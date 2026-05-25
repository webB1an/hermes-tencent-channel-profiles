#!/usr/bin/env python3
"""
动态壁纸频道发帖脚本 — Wallpaper动态壁纸发帖

轮询三个动态壁纸源（WallpaperWaifu → MoeWalls → DesktopHut），
找到最新未发帖的壁纸，下载并发布到 动态壁纸 频道（channel_id=667049126），
发帖后删除本地视频文件。

去重机制：
  - download 脚本的 config/downloaded-*-detail-urls.json 防止同一脚本重复下载
  - 本脚本的 live_wallpaper_state.json 防止在不同源之间重复发帖

触发词：Wallpaper动态壁纸发帖
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# =========================================================================
# 默认配置
# =========================================================================

CST = timezone(timedelta(hours=8))

DEFAULTS: dict[str, str] = {
    "GUILD_ID":         "652812504031889164",
    "CHANNEL_ID":       "667049126",                     # 动态壁纸板块
    "HERMES_HOME":      "/root/.hermes/profiles/tencent-channel-live-wallpaper",
    "MINIMAX_BASE_URL": "https://api.minimaxi.com/v1",
    "MINIMAX_MODEL":    "MiniMax-M2.7",
    "HTTP_TIMEOUT":     "20",
    "USER_AGENT":       "Mozilla/5.0 (compatible; Hermes-Bot/1.0)",
    "FEISHU_DOMAIN":    "feishu",
    "FEISHU_NOTIFY_CHAT_ID": "",
}

LOG = logging.getLogger("live-wallpaper-post")

# =========================================================================
# 进程锁：防止多实例并发运行
# =========================================================================

LOCK_PATH = Path("/tmp/live_wallpaper_post.lock")


def acquire_lock() -> Optional[int]:
    """获取独占文件锁，获取失败则直接退出。返回 lock_fd 文件描述符。"""
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("⚠️  另一个实例正在运行，直接退出。")
        sys.exit(0)

    # 崩溃时自动释放锁
    def _release(signum, _frame):
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_file.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _release)
    signal.signal(signal.SIGINT, _release)
    return lock_file.fileno()


_lock_fd = acquire_lock()


# =========================================================================
# 配置加载
# =========================================================================

def load_env_file(path: Path) -> dict[str, str]:
    """读取 KEY=VALUE 格式的 .env 文件"""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'\"")
    return out


@dataclass
class Config:
    guild_id: str
    channel_id: str
    hermes_home: Path
    minimax_base_url: str
    minimax_model: str
    minimax_api_key: str
    http_timeout: int
    user_agent: str
    feishu_app_id: str
    feishu_app_secret: str
    feishu_domain: str
    feishu_user_id: str
    feishu_notify_chat_id: str
    state_file: Path
    download_base: Path          # live-wallpaper-download 仓库根目录

    @classmethod
    def load(cls) -> "Config":
        hermes_home = Path(os.environ.get("HERMES_HOME", DEFAULTS["HERMES_HOME"]))
        env = {**DEFAULTS, **load_env_file(hermes_home / ".env"), **os.environ}

        def _get(key: str) -> str:
            return env.get(key, DEFAULTS.get(key, ""))

        def _int(key: str) -> int:
            try:
                return int(_get(key))
            except ValueError:
                return int(DEFAULTS[key])

        return cls(
            guild_id=_get("GUILD_ID"),
            channel_id=_get("CHANNEL_ID"),
            hermes_home=hermes_home,
            minimax_base_url=_get("MINIMAX_BASE_URL"),
            minimax_model=_get("MINIMAX_MODEL"),
            minimax_api_key=env.get("MINIMAX_CN_API_KEY", ""),
            http_timeout=_int("HTTP_TIMEOUT"),
            user_agent=_get("USER_AGENT"),
            feishu_app_id=env.get("FEISHU_APP_ID", ""),
            feishu_app_secret=env.get("FEISHU_APP_SECRET", ""),
            feishu_domain=_get("FEISHU_DOMAIN"),
            feishu_user_id=env.get("FEISHU_USER_ID", ""),
            feishu_notify_chat_id=env.get("FEISHU_NOTIFY_CHAT_ID", ""),
            state_file=hermes_home / "live_wallpaper_state.json",
            download_base=hermes_home / "scripts" / "live-wallpaper-download",
        )


# =========================================================================
# 工具函数
# =========================================================================

def is_likely_english(text: str) -> bool:
    """判断文本是否像是英文（需要翻译的语言）——非英文/非拉丁字母文本跳过翻译"""
    if not text:
        return False
    latin = sum(1 for c in text if c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return latin / len(text) > 0.5


def cst_now() -> datetime:
    return datetime.now(CST)


def retry(fn, *, tries: int = 3, base_delay: float = 1.5,
          on: tuple = (URLError, HTTPError, TimeoutError, OSError),
          label: str = ""):
    last_exc: Optional[BaseException] = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except on as exc:
            last_exc = exc
            if attempt == tries:
                break
            sleep = base_delay * (2 ** (attempt - 1))
            LOG.warning("%s 第 %d/%d 次失败: %s，%.1fs 后重试",
                        label or fn.__name__, attempt, tries, exc, sleep)
            time.sleep(sleep)
    raise last_exc


# =========================================================================
# 翻译：Google Translate 优先，MiniMax 候补，失败回退原文
# =========================================================================

class Translator:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def translate(self, text: str) -> str:
        """翻译英文文本到中文，三级 fallback"""
        if not text:
            return ""
        for fn, name in [
            (self._google, "google"),
            (self._minimax, "minimax"),
        ]:
            try:
                result = fn(text)
                if result and result.strip():
                    return result.strip()
            except Exception as exc:
                LOG.warning("翻译渠道 %s 失败: %s", name, exc)
        LOG.info("所有翻译渠道失败，回退到英文原文")
        return text

    def _google(self, text: str) -> Optional[str]:
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=en&tl=zh-CN&dt=t&q={urllib.parse.quote(text)}"
        )
        req = Request(url, headers={"User-Agent": self.cfg.user_agent})
        with urlopen(req, timeout=self.cfg.http_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        parts = [seg[0] for seg in data[0] if seg and seg[0]]
        joined = "".join(p.strip(" ,，") for p in parts if p.strip())
        return joined or None

    def _minimax(self, text: str) -> Optional[str]:
        if not self.cfg.minimax_api_key:
            return None
        prompt = (
            "Translate the following English text to Simplified Chinese. "
            "Return only the translated text, no explanation.\n\n"
            f"Text: {text}"
        )
        payload = {
            "model": self.cfg.minimax_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        req = Request(
            f"{self.cfg.minimax_base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.cfg.minimax_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(req, timeout=self.cfg.http_timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"]
        cleaned = re.sub(r"<[^>]+>", "", content, flags=re.DOTALL).strip()
        return cleaned or None


# =========================================================================
# 下载脚本调用
# =========================================================================

@dataclass
class DownloadedWallpaper:
    name: str           # 英文名（用于翻译）
    detail_url: str     # 详情页 URL（用于去重）
    file_path: Path     # 本地文件路径（发帖后删除）
    source: str         # "wallpaperwaifu" | "moewalls" | "desktophut"


class DownloadRunner:
    """调用 Node.js 下载脚本，解析 manifest 返回下载结果"""

    SOURCES = [
        ("wallpaperwaifu", "WallpaperWaifu", "download-wallpaperwaifu-first-page.mjs"),
        ("moewalls",       "MoeWalls",       "download-moewalls-first-page.mjs"),
        ("desktophut",     "DesktopHut",     "download-desktophut-first-page.mjs"),
    ]

    def __init__(self, base: Path, dry_run: bool = False) -> None:
        self.base = base
        self.scripts_dir = base / "scripts"
        self.dry_run = dry_run

    def _cleanup_resume_artifacts(self) -> None:
        """清除残留的断点续传中间态文件（.part + 不完整的 .mp4）"""
        downloads_dir = self.base / "downloads"
        if not downloads_dir.is_dir():
            return
        for f in downloads_dir.iterdir():
            if f.suffix == ".part":
                try:
                    f.unlink()
                    LOG.info("已清除残留文件: %s", f.name)
                except OSError as exc:
                    LOG.warning("无法清除残留文件 %s: %s", f.name, exc)
            elif f.suffix == ".mp4":
                # 检查是否是 Node 脚本残留的不完整下载（curl 被 kill 后留下）
                try:
                    size = f.stat().st_size
                    if size < 1024 * 1024:  # 小于 1MB 视为不完整
                        f.unlink()
                        LOG.info("已清除不完整的视频文件: %s (%d bytes)", f.name, size)
                except OSError as exc:
                    LOG.warning("无法检查文件 %s: %s", f.name, exc)

    DRY_RUN_SUPPORTED = {"wallpaperwaifu"}  # desktophut also claims support but hangs in Python subprocess

    def _run_script(self, source_key: str, script_name: str) -> bool:
        script_path = self.scripts_dir / script_name
        if not script_path.exists():
            LOG.warning("下载脚本不存在，跳过: %s", script_path)
            return False

        # 如果是 dry-run 模式但该源不支持 dry-run，直接跳过（不运行）
        if self.dry_run and source_key not in self.DRY_RUN_SUPPORTED:
            LOG.info("跳过 %s（dry-run 模式不支持该源）", source_key)
            return False

        env = os.environ.copy()
        env["NODE_PATH"] = str(self.scripts_dir.parent / "node_modules")

        args = ["node", str(script_path)]
        # --dry-run only supported by wallpaperwaifu and desktophut
        if self.dry_run and source_key in self.DRY_RUN_SUPPORTED:
            args.append("--dry-run")
        # Limit downloads to 1 (one-at-a-time posting: download one, post, delete, repeat)
        if not self.dry_run:
            args.extend(["--limit", "1"])

        LOG.info("运行下载脚本: %s", " ".join(args))
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                env=env,
                timeout=300,
            )
            if proc.returncode != 0:
                LOG.warning("%s 运行失败 (code=%d): %s",
                            script_name, proc.returncode, proc.stderr[:300])
                return False
            for line in (proc.stdout + proc.stderr).splitlines():
                if line.strip():
                    LOG.debug("  [node] %s", line)
            return True
        except subprocess.TimeoutExpired:
            LOG.warning("%s 运行超时（5分钟）", script_name)
            return False
        except FileNotFoundError:
            LOG.error("node 未找到，请确认 Node.js 已安装")
            return False

    MANIFEST_NAMES = {
        "wallpaperwaifu": "manifest-wallpaperwaifu.json",
        "moewalls":       "manifest.json",
        "desktophut":     "manifest-desktophut.json",
    }

    def _parse_manifest(self, source_key: str) -> list[DownloadedWallpaper]:
        """从 manifest JSON 中提取所有下载成功的条目"""
        manifest_name = self.MANIFEST_NAMES.get(source_key, f"manifest-{source_key}.json")
        manifest_path = self.base / "config" / manifest_name
        if not manifest_path.exists():
            return []

        try:
            items = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            LOG.warning("解析 manifest 失败 %s: %s", manifest_path, exc)
            return []

        results = []
        for item in items:
            # 接受两类状态：
            #   - downloaded / skipped-existing：Node 正常下载完成
            #   - skipped-detail-url：URL 已在 dedup 中记录过（Node 跳过下载）
            #     但如果 record.filePath 指向的文件存在，说明之前成功下载过，可以复用
            top_status = item.get("status", "")
            record_status = item.get("record", {}).get("status", "")
            if top_status not in ("downloaded", "skipped-existing") and not (
                top_status == "skipped-detail-url" and record_status == "downloaded"
            ):
                if not (self.dry_run and top_status == "dry-run"):
                    continue

            detail_url = item.get("detailUrl") or ""

            # 优先用 record 里的信息（skipped-detail-url 场景）
            record = item.get("record", {})
            file_path_str = record.get("filePath") or item.get("filePath") or ""
            name = record.get("name") or item.get("name") or item.get("pageTitle") or "wallpaper"
            # In dry-run mode, filePath may be empty since no file is actually downloaded
            if not detail_url:
                continue
            if not file_path_str and not self.dry_run:
                continue
            file_path = Path(file_path_str)
            # 文件不存在？可能是之前超时被 kill 后文件被清理了
            # 注意：不在这里清理 dedup 记录（因为还不知道该 URL 是否已发帖）。
            # 清理逻辑由调用方在 run_once() 中检查 is_posted 后执行。
            if not self.dry_run and not file_path.exists():
                LOG.warning("文件不存在，跳过: %s", file_path.name)
                continue
            results.append(DownloadedWallpaper(
                name=name,
                detail_url=detail_url,
                file_path=Path(file_path_str),
                source=source_key,
            ))
        return results

    def _remove_url_record(self, source_key: str, detail_url: str) -> None:
        """从 Node dedup 文件中移除指定 URL（允许重新下载该壁纸）"""
        url_record_map = {
            "wallpaperwaifu": "downloaded-wallpaperwaifu-detail-urls.json",
            "moewalls": "downloaded-detail-urls.json",
            "desktophut": "downloaded-desktophut-detail-urls.json",
        }
        filename = url_record_map.get(source_key)
        if not filename:
            return
        record_path = self.base / "config" / filename
        if not record_path.exists():
            return
        try:
            records = json.loads(record_path.read_text(encoding="utf-8"))
            # 找出所有不匹配该 detail_url 的记录
            filtered = [r for r in records if r.get("detailUrl") != detail_url]
            if len(filtered) < len(records):
                tmp = record_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(filtered, indent=2, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, record_path)
                LOG.info("已从 dedup 记录中移除: %s", detail_url)
        except (json.JSONDecodeError, OSError) as exc:
            LOG.warning("无法更新 dedup 文件 %s: %s", record_path, exc)

    def try_download_all(self) -> list[DownloadedWallpaper]:
        """
        轮询所有源，返回第一个找到的新壁纸列表。
        每个源只返回该源本次新下载的条目（通过 manifest 状态过滤）。
        """
        self._cleanup_resume_artifacts()

        for source_key, source_label, script_name in self.SOURCES:
            LOG.info("尝试下载源: %s", source_label)
            ok = self._run_script(source_key, script_name)
            if not ok:
                continue

            downloaded = self._parse_manifest(source_key)
            if downloaded:
                LOG.info("%s 下载成功，获取 %d 个条目", source_label, len(downloaded))
                return downloaded

        LOG.warning("所有源均无新下载")
        return []


# =========================================================================
# 已发帖去重
# =========================================================================

class PostedStore:
    """维护 live_wallpaper_state.json，记录已发帖的 detailURL"""

    DEDUP_FILES = [
        "downloaded-detail-urls.json",
        "downloaded-wallpaperwaifu-detail-urls.json",
        "downloaded-desktophut-detail-urls.json",
    ]

    def __init__(self, path: Path, download_base: Path) -> None:
        self.path = path
        self.download_base = download_base
        self._posted: set[str] = set()
        self._load()
        self._report_missing_dedup_files()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._posted = set(data.get("posted_detail_urls", []))
        except (OSError, json.JSONDecodeError):
            self._posted = set()

    def _save(self) -> None:
        """持久化到文件（原子写入）"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"posted_detail_urls": sorted(self._posted)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def _report_missing_dedup_files(self) -> None:
        """
        扫描 dedup 文件中已下载但本地文件不存在的条目。

        这些条目通常是发帖成功后删除了本地视频，但也可能是手动删除、
        超时中断后的残缺状态。这里只记录，不自动标记已发，避免误跳过。
        """
        missing = 0
        for dedup_name in self.DEDUP_FILES:
            dedup_path = self.download_base / "config" / dedup_name
            if not dedup_path.exists():
                continue
            try:
                records = json.loads(dedup_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for item in records:
                if item.get("status") != "downloaded":
                    continue
                fp = item.get("filePath", "")
                detail_url = item.get("detailUrl")
                if fp and detail_url and not Path(fp).exists() and detail_url not in self._posted:
                    missing += 1
                    LOG.info("dedup 记录文件不存在但未标记已发，保留待人工判断: %s", detail_url)
        if missing:
            LOG.info("发现 %d 条 dedup 缺文件记录，未自动写入已发 state", missing)

    def is_posted(self, detail_url: str) -> bool:
        return detail_url in self._posted

    def mark_posted(self, detail_url: str) -> None:
        self._posted.add(detail_url)
        self._save()


# =========================================================================
# 腾讯频道 CLI 封装
# =========================================================================

class ChannelCLI:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def _env(self) -> dict:
        env = os.environ.copy()
        env["HERMES_HOME"] = str(self.cfg.hermes_home)
        env["HOME"] = str(self.cfg.hermes_home / "home")
        return env

    def call(self, domain: str, action: str, payload: dict, *,
             extra_flags: Optional[list] = None, timeout: int = 180) -> tuple[int, str, str]:
        cmd = ["tencent-channel-cli", domain, action, "--json"]
        if extra_flags:
            cmd.extend(extra_flags)
        stdin_data = json.dumps(payload, ensure_ascii=False)
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_data,
                capture_output=True,
                text=True,
                env=self._env(),
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return 127, "", "binary not found"
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()

    def call_flags(self, domain: str, action: str, flags: list[str], *,
                   timeout: int = 180) -> tuple[int, str, str]:
        cmd = ["tencent-channel-cli", domain, action, "--json", "--yes", *flags]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=self._env(),
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return 127, "", "binary not found"
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()

    def publish_feed(self, channel_id: str, content: str, video_path: Path) -> tuple[bool, str, str]:
        """发帖（视频），返回 (success, feed_id, err_msg)"""
        flags = [
            "--guild-id", self.cfg.guild_id,
            "--channel-id", channel_id,
            "--content", content,
            "--video", str(video_path),
        ]
        code, out, err = self.call_flags("feed", "publish-feed", flags)
        if code != 0:
            return False, "", f"exit={code} stdout={out[:300]} stderr={err[:300]}"
        try:
            result = json.loads(out)
        except json.JSONDecodeError:
            return False, "", f"invalid json: {out[:200]}"
        if not result.get("success"):
            return False, "", f"api failed: {result}"
        data = result.get("data") or {}
        feed_id = str(data.get("feed_id") or data.get("id") or "")
        share_url = data.get("share_url", "") or ""
        return True, feed_id, share_url or ""


# =========================================================================
# 飞书完成通知
# =========================================================================

class FeishuNotifier:
    """Send background completion notices through Feishu Open API."""

    def __init__(self, cfg: Config, *, chat_id: str = "", enabled: bool = True) -> None:
        self.cfg = cfg
        self.chat_id = chat_id or cfg.feishu_notify_chat_id
        self.user_id = cfg.feishu_user_id
        self.enabled = enabled

    def _base_url(self) -> str:
        if self.cfg.feishu_domain.lower() == "lark":
            return "https://open.larksuite.com"
        return "https://open.feishu.cn"

    def is_enabled(self) -> bool:
        return bool(
            self.enabled
            and self.cfg.feishu_app_id
            and self.cfg.feishu_app_secret
            and (self.user_id or self.chat_id)
        )

    def _post_json(self, url: str, payload: dict, headers: Optional[dict] = None) -> dict:
        req = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": self.cfg.user_agent,
                **(headers or {}),
            },
            method="POST",
        )
        with urlopen(req, timeout=self.cfg.http_timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _tenant_access_token(self) -> str:
        data = self._post_json(
            f"{self._base_url()}/open-apis/auth/v3/tenant_access_token/internal",
            {
                "app_id": self.cfg.feishu_app_id,
                "app_secret": self.cfg.feishu_app_secret,
            },
        )
        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise RuntimeError(f"tenant token failed: {data}")
        return str(data["tenant_access_token"])

    def send(self, text: str) -> None:
        if not self.is_enabled():
            LOG.info("飞书通知未启用，跳过")
            return
        token = self._tenant_access_token()
        receive_id_type = "open_id" if self.user_id else "chat_id"
        receive_id = self.user_id or self.chat_id
        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": text[:3800]}, ensure_ascii=False),
            "uuid": str(uuid.uuid4()),
        }
        data = self._post_json(
            f"{self._base_url()}/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"message send failed: {data}")
        LOG.info("飞书通知已发送到 %s:%s", receive_id_type, receive_id)


# =========================================================================
# 主流程
# =========================================================================

def build_title(name_en: str, name_zh: str) -> str:
    """组装标题：英文名 · 中文翻译"""
    return f"{name_en} · {name_zh}"


class LiveWallpaperPoster:
    def __init__(self, cfg: Config, *, dry_run: bool = False,
                 notifier: Optional[FeishuNotifier] = None) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.translator = Translator(cfg)
        self.downloader = DownloadRunner(cfg.download_base, dry_run=dry_run)
        self.store = PostedStore(cfg.state_file, cfg.download_base)
        self.cli = ChannelCLI(cfg)
        self.notifier = notifier

    def _notify(self, text: str) -> None:
        if self.dry_run or not self.notifier:
            return
        try:
            self.notifier.send(text)
        except Exception as exc:
            LOG.warning("飞书通知失败（不影响发帖结果）: %s", exc)

    def run_once(self) -> int:
        now = cst_now()
        LOG.info("===== 动态壁纸发帖开始 @ %s =====", now.strftime("%Y-%m-%d %H:%M:%S"))

        # 1) 轮询下载
        downloaded = self.downloader.try_download_all()
        if not downloaded:
            LOG.warning("没有新壁纸，退出")
            self._notify("动态壁纸发帖未完成：没有下载到新壁纸。")
            return 1

        # 2) 过滤已发帖的（跨源去重）
        candidates = [w for w in downloaded if not self.store.is_posted(w.detail_url)]
        if not candidates:
            LOG.warning("所有下载的壁纸均已发帖过，退出")
            self._notify("动态壁纸发帖未完成：本次下载的壁纸都已经发过。")
            return 2

        # 3) 取第一个候选发帖
        wallpaper = candidates[0]
        LOG.info("选中壁纸: %s (来源=%s, URL=%s)",
                 wallpaper.name, wallpaper.source, wallpaper.detail_url)

        # 4) 翻译标题
        # 去掉末尾的 "Live Wallpaper" 后缀再翻译，避免中英文都出现重复的"动态壁纸"
        name_en_base = re.sub(r'\s+Live\s+Wallpaper\s*$', '', wallpaper.name, flags=re.IGNORECASE)
        if is_likely_english(name_en_base):
            name_zh = self.translator.translate(name_en_base)
            title = build_title(name_en_base, name_zh)
        else:
            # 本身已经是中文/非英文文本，跳过翻译直接使用
            title = name_en_base
        LOG.info("标题: %s", title)

        # 5) 检查文件存在（dry-run 模式跳过，因为文件未实际下载）
        if not self.dry_run and not wallpaper.file_path.exists():
            LOG.error("壁纸文件不存在: %s", wallpaper.file_path)
            # 文件不存在且未发帖（才从 dedup 移除，让下次重新下载）
            if not self.store.is_posted(wallpaper.detail_url):
                LOG.info("该壁纸未发帖，从 dedup 移除以便重新下载")
                self.downloader._remove_url_record(wallpaper.source, wallpaper.detail_url)
            self._notify(
                "动态壁纸发帖失败：视频文件不存在。\n"
                f"标题：{title}\n"
                f"来源：{wallpaper.detail_url}\n"
                f"文件：{wallpaper.file_path}"
            )
            return 3

        if not self.dry_run:
            file_size_mb = wallpaper.file_path.stat().st_size / (1024 * 1024)
            LOG.info("文件: %s (%.1f MB)", wallpaper.file_path.name, file_size_mb)

        if self.dry_run:
            LOG.info("[DRY-RUN] 跳过实际发帖和删除")
            return 0

        # 6) 发帖
        ok, feed_id, publish_detail = self.cli.publish_feed(
            self.cfg.channel_id, title, wallpaper.file_path
        )
        if not ok:
            LOG.error("发帖失败: %s", publish_detail)
            self._notify(
                "动态壁纸发帖失败：腾讯频道发布失败。\n"
                f"标题：{title}\n"
                f"来源：{wallpaper.detail_url}\n"
                f"错误：{publish_detail[:500]}"
            )
            return 4

        LOG.info("发帖成功 ✅ feed_id=%s share_url=%s", feed_id or "(无)", publish_detail or "(无)")
        self._notify(
            "动态壁纸发帖成功\n"
            f"标题：{title}\n"
            f"来源：{wallpaper.detail_url}\n"
            f"帖子：{publish_detail or feed_id or '未返回链接'}"
        )

        # 7) 标记已发帖
        self.store.mark_posted(wallpaper.detail_url)

        # 8) 删除本地视频文件
        try:
            wallpaper.file_path.unlink()
            LOG.info("已删除视频文件: %s", wallpaper.file_path.name)
        except OSError as exc:
            LOG.warning("删除视频文件失败（不影响发帖结果）: %s", exc)

        return 0


# =========================================================================
# CLI 入口
# =========================================================================

def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="动态壁纸频道发帖脚本")
    p.add_argument("--dry-run", action="store_true", help="下载并准备内容，但不实际发帖")
    p.add_argument("--notify-chat-id", default="", help="飞书完成通知 chat_id（默认读取 FEISHU_NOTIFY_CHAT_ID）")
    p.add_argument("--no-notify", action="store_true", help="禁用飞书完成通知")
    p.add_argument("-v", "--verbose", action="store_true", help="打印 DEBUG 级日志")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    try:
        cfg = Config.load()
    except Exception as exc:
        LOG.error("配置加载失败: %s", exc)
        return 10

    notifier = FeishuNotifier(cfg, chat_id=args.notify_chat_id, enabled=not args.no_notify)
    poster = LiveWallpaperPoster(cfg, dry_run=args.dry_run, notifier=notifier)
    try:
        return poster.run_once()
    except KeyboardInterrupt:
        LOG.warning("用户中断")
        return 130
    except Exception as exc:
        LOG.exception("未处理异常: %s", exc)
        try:
            notifier.send(f"动态壁纸发帖异常：{exc}")
        except Exception as notify_exc:
            LOG.warning("飞书异常通知失败: %s", notify_exc)
        return 99


if __name__ == "__main__":
    sys.exit(main())
