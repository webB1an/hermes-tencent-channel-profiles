#!/usr/bin/env python3
"""
百度网盘动态壁纸发帖脚本

触发词：发动态壁纸

流程：
  1. 使用 bdpan search 分页查找网盘中的 .mp4 文件
  2. 读取 live_wallpaper_state.json 中的已发帖记录
  3. 找到第一个未发帖的 mp4，下载到本地
  4. 发帖到腾讯频道（标题：文件名 + 翻译）
  5. 删除本地文件，标记已发帖

去重：以网盘文件路径（/apps/bdpan/动态壁纸/.../xxx.mp4）为唯一标识
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
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# =========================================================================
# 配置
# =========================================================================

CST = timezone(timedelta(hours=8))

DEFAULTS: dict[str, str] = {
    "GUILD_ID":         "652812504031889164",
    "CHANNEL_ID":       "667049126",
    "HERMES_HOME":      "/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper",
    "BDPAN_BIN":        "/root/.local/bin/bdpan",
    "BDPAN_HOME":       "/root",
    "MINIMAX_BASE_URL": "https://api.minimaxi.com/v1",
    "MINIMAX_MODEL":    "MiniMax-M2.7",
    "HTTP_TIMEOUT":     "20",
    "USER_AGENT":       "Mozilla/5.0 (compatible; Hermes-Bot/1.0)",
}

# 下载速度保守估算：40 KB/s（实测约 40-80KB/s）
# timeout = max(DOWNLOAD_MIN_TIMEOUT, size_bytes / 40_000 * 1.5)
DOWNLOAD_SPEED_BPS = 40_000
DOWNLOAD_MIN_TIMEOUT = 3600

LOG = logging.getLogger("bdpan-wallpaper-post")

LOCK_PATH = Path("/tmp/bdpan_wallpaper_post.lock")


# =========================================================================
# 进程锁
# =========================================================================

def acquire_lock() -> Optional[int]:
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("⚠️  另一个实例正在运行，直接退出。")
        sys.exit(0)

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
    bdpan_bin: Path
    bdpan_home: str
    minimax_base_url: str
    minimax_model: str
    minimax_api_key: str
    http_timeout: int
    user_agent: str
    state_file: Path
    download_base: Path
    feishu_app_id: str
    feishu_app_secret: str
    feishu_user_id: str

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
            bdpan_bin=Path(_get("BDPAN_BIN")),
            bdpan_home=_get("BDPAN_HOME"),
            minimax_base_url=_get("MINIMAX_BASE_URL"),
            minimax_model=_get("MINIMAX_MODEL"),
            minimax_api_key=env.get("MINIMAX_CN_API_KEY", ""),
            http_timeout=_int("HTTP_TIMEOUT"),
            user_agent=_get("USER_AGENT"),
            state_file=hermes_home / "live_wallpaper_state.json",
            download_base=hermes_home / "bdpan-downloads",
            feishu_app_id=_get("FEISHU_APP_ID"),
            feishu_app_secret=_get("FEISHU_APP_SECRET"),
            feishu_user_id=env.get("FEISHU_USER_ID", ""),
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


# =========================================================================
# Bdpan 封装
# =========================================================================

class BdpanRunner:
    """封装 bdpan CLI 调用"""

    REMOTE_BASE = "/apps/bdpan/动态壁纸"
    MAX_SIZE_BYTES = 300 * 1024 * 1024  # 300MB

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def _run(self, args: list[str], timeout: int = 60) -> tuple[int, str, str]:
        env = os.environ.copy()
        env["HOME"] = self.cfg.bdpan_home
        cmd = [str(self.cfg.bdpan_bin)] + args
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
                check=False,
            )
            return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"

    def _search_page(self, page: int) -> tuple[list[dict], bool]:
        """
        调用 bdpan search "mp4" --page N，返回 (items, has_more)
        """
        code, out, _ = self._run(
            ["search", "mp4", "--category", "1",
             "--page-size", "50", "--page", str(page), "--json"],
            timeout=30,
        )
        if code != 0 or not out:
            return [], False
        try:
            data = json.loads(out)
            items = data.get("items", [])
            has_more = data.get("has_more", False)
            return items, has_more
        except (json.JSONDecodeError, KeyError):
            return [], False

    def iter_mp4_newest_first(self, posted_store) -> dict:
        """
        流式遍历网盘 mp4 文件（按 server_mtime 倒序），
        遇到第一个未发帖且大小 <= 300MB 的文件就返回。
        返回空 dict 表示没有更多文件了。

        已发帖和已在 pending 的文件都会被跳过。
        """
        page = 1
        while True:
            items, has_more = self._search_page(page)
            if not items:
                # 该页为空，结束遍历
                return {}

            for item in items:
                path = item.get("path", "")
                if not path:
                    continue
                if not path.startswith(self.REMOTE_BASE + "/"):
                    continue
                size = int(item.get("size", 0))
                if size > self.MAX_SIZE_BYTES:
                    continue
                if not posted_store.is_posted(path):
                    # 检查是否已在 pending（避免 download 进程被 kill 后重复选同一个文件）
                    if posted_store._pending and any(p.get("path") == path for p in posted_store._pending):
                        continue
                    # 第一个未发帖且不在 pending 的文件
                    return item

            if not has_more:
                return {}

            page += 1

    def download(
        self,
        remote_path: str,
        local_dir: Path,
        local_filename: str,
        size_bytes: int = 0,
    ) -> tuple[bool, Path, bool]:
        """
        下载单个远端文件到 local_dir，返回 (success, local_path, should_skip)

        remote_path 示例: /apps/bdpan/动态壁纸/其他/武器/Katana.mp4
        转换为相对路径: 动态壁纸/其他/武器/Katana.mp4

        特性：
        - 实时打印 bdpan 下载进度（stderr 流式解析）
        - 按文件大小动态计算 timeout（保守按 40KB/s，1.5 倍冗余，最低 3600s）
        - bdpan 不支持断点续传，下载目录内的 *.bdpan 残留文件会被覆盖重下
        """
        local_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = local_dir / f"{local_filename}.bdpan"

        # 去掉 /apps/bdpan/ 前缀，转为相对路径（bdpan 不接受绝对路径）
        relative_path = remote_path
        if relative_path.startswith("/apps/bdpan/"):
            relative_path = relative_path[len("/apps/bdpan/"):]

        # bdpan 把连续 4 个点 "...." 当成路径穿越攻击，
        # 这类文件无法下载（bdpan 本身限制），跳过
        if "...." in relative_path:
            LOG.warning("文件名含 .... 无法下载（bdpan 路径检查限制），跳过: %s", relative_path)
            return False, Path(), True

        # 动态计算 timeout：保守 40KB/s + 1.5 倍冗余，最少 DOWNLOAD_MIN_TIMEOUT
        if size_bytes > 0:
            estimated_seconds = size_bytes / DOWNLOAD_SPEED_BPS * 1.5
            timeout = max(int(estimated_seconds), DOWNLOAD_MIN_TIMEOUT)
        else:
            timeout = 1800  # 旧行为：无 size 时用 30min
        LOG.debug("下载 timeout=%.0fs（size=%.1fMB，speed=40KB/s）", timeout, size_bytes / 1024 / 1024)

        # 用 Popen 流式读取 stderr，实时打印进度
        env = os.environ.copy()
        env["HOME"] = self.cfg.bdpan_home
        cmd = [str(self.cfg.bdpan_bin), "download", relative_path, str(tmp_path)]

        try:
            proc = subprocess.Popen(
                cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
            )
        except FileNotFoundError:
            LOG.error("bdpan binary not found: %s", self.cfg.bdpan_bin)
            return False, Path(), False

        # 实时解析 stderr 中的进度行
        import select
        deadline = time.time() + timeout
        last_progress = time.time()

        while True:
            remaining = max(1, int(deadline - time.time()))
            # 用 select 监听 stderr
            ready, _, _ = select.select([proc.stderr], [], [], min(remaining, 5))
            if proc.stderr in ready:
                chunk = os.read(proc.stderr.fileno(), 4096).decode("utf-8", errors="replace")
                if not chunk:
                    break
                for line in chunk.splitlines():
                    line = line.rstrip()
                    if "下载中" in line or "下载完成" in line or "%" in line:
                        LOG.info("[下载] %s", line)
                        last_progress = time.time()
                    elif line.startswith("Error:"):
                        LOG.error("[下载错误] %s", line)
            # 检查是否结束
            if proc.poll() is not None:
                break
            # 超时检测
            if time.time() >= deadline:
                proc.kill()
                LOG.error("下载超时（%.0fs）", timeout)
                return False, Path(), False
            # 超过 60s 无进度更新但进程还活着，只打警告不杀掉（网速波动）
            if time.time() - last_progress > 60 and proc.poll() is None:
                LOG.warning("下载进度停滞已 %.0fs，继续等待...", time.time() - last_progress)
                last_progress = time.time()  # 重置避免重复报警

        proc.wait()
        code = proc.returncode

        if code != 0:
            # 读取剩余 stderr
            remaining_err = proc.stderr.read()
            LOG.error("bdpan download 失败 (exit=%d): %s", code, remaining_err or "(无详情)")
            return False, Path(), False

        if not tmp_path.exists():
            LOG.error("下载后临时文件不存在: %s", tmp_path)
            return False, Path(), False

        # 移动到目标目录
        final_path = local_dir / local_filename
        try:
            import shutil
            shutil.move(str(tmp_path), str(final_path))
        except OSError as exc:
            LOG.warning("移动文件失败（保留在 tmp）: %s", exc)
            return False, Path(), False

        LOG.info("下载成功: %s (%.1f MB)", final_path.name, final_path.stat().st_size / 1024 / 1024)
        return True, final_path, False


# =========================================================================
# 翻译
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
        import urllib.parse
        from urllib.request import Request, urlopen
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
        import urllib.request
        from urllib.request import Request, urlopen
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
# 已发帖状态管理
# =========================================================================

class PostedStore:
    """维护 posted_detail_urls（网盘文件路径）"""

    # 两次发帖最小间隔（秒），防止频繁刷屏
    POST_COOLDOWN_SECONDS = 30 * 60  # 30 分钟

    def __init__(self, path: Path) -> None:
        self.path = path
        self._posted: set[str] = set()
        self._pending: list[dict] = []
        self._last_posted_at: Optional[float] = None  # Unix timestamp
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._posted = set()
            self._pending = []
            self._last_posted_at = None
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._posted = set(data.get("posted_detail_urls", []))
            self._pending = data.get("pending_downloads", [])
            self._last_posted_at = data.get("last_posted_at")
        except (OSError, json.JSONDecodeError):
            self._posted = set()
            self._pending = []
            self._last_posted_at = None

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({
                "posted_detail_urls": sorted(self._posted),
                "pending_downloads": self._pending,
                "last_posted_at": self._last_posted_at,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def can_post_now(self) -> bool:
        """检查距离上次发帖是否已过冷却期"""
        if self._last_posted_at is None:
            return True
        elapsed = time.time() - self._last_posted_at
        if elapsed < self.POST_COOLDOWN_SECONDS:
            LOG.info("发帖冷却期：还剩 %.0f 分钟（距上次 %.0f 分钟）",
                     (self.POST_COOLDOWN_SECONDS - elapsed) / 60,
                     elapsed / 60)
            return False
        return True

    def record_post(self) -> None:
        """记录一次发帖时间"""
        self._last_posted_at = time.time()
        self._save()

    def is_posted(self, remote_path: str) -> bool:
        return remote_path in self._posted

    def mark_posted(self, remote_path: str) -> None:
        self._posted.add(remote_path)
        self._save()

    def add_pending(self, item: dict) -> None:
        """标记一个文件为"下载中"，等下载完成后再 mark_posted。自动去重。"""
        if any(p.get("path") == item.get("path") for p in self._pending):
            return
        # 记录加入 pending 的时间戳，用于超时清理
        item = dict(item)
        item["pending_at"] = time.time()
        self._pending.append(item)
        self._save()

    def remove_pending(self, remote_path: str) -> None:
        """下载失败或确认放弃时从 pending 移除"""
        self._pending = [p for p in self._pending if p.get("path") != remote_path]
        self._save()

    def get_pending(self) -> list[dict]:
        return list(self._pending)


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

    def publish_feed(self, channel_id: str, content: str, video_path: Path) -> tuple[bool, str, str]:
        """发帖（视频），返回 (success, feed_id, share_url)"""
        payload = {
            "guild_id": self.cfg.guild_id,
            "channel_id": channel_id,
            "content": content,
            "video_paths": [{"file_path": str(video_path)}],
        }
        code, out, err = self.call("feed", "publish-feed", payload, extra_flags=["--yes"])
        if code != 0:
            return False, "", f"exit={code} stderr={err[:200]}"
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
# 主流程
# =========================================================================

def extract_name(filename: str) -> str:
    """从文件名提取可展示的名称（去掉扩展名和多余空格）"""
    name = filename
    # 去掉扩展名
    for ext in (".mp4", ".MP4", ".avi", ".mkv", ".mov"):
        if name.lower().endswith(ext):
            name = name[:-len(ext)]
    # 去掉多余空白
    name = re.sub(r"\s+", " ", name).strip()
    return name


def local_filename_for(item: dict) -> str:
    """生成本地唯一文件名，避免不同目录下同名 mp4 串单。"""
    filename = Path(item.get("server_filename") or item.get("path", "")).name
    stem = Path(filename).stem or "wallpaper"
    suffix = Path(filename).suffix or ".mp4"
    fs_id = str(item.get("fs_id") or "unknown")
    return f"{fs_id}_{stem}{suffix}"


# =========================================================================
# 飞书通知
# =========================================================================

class FeishuNotifier:
    """通过飞书开放平台 API 发送消息给用户"""

    def __init__(self, app_id: str, app_secret: str, user_id: str) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.user_id = user_id
        self._token: Optional[str] = None

    def _get_token(self) -> Optional[str]:
        if self._token:
            return self._token
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        data = json.dumps({"app_id": self.app_id, "app_secret": self.app_secret}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                if result.get("code") == 0:
                    self._token = result.get("tenant_access_token")
                    return self._token
        except Exception:
            pass
        return None

    def send(self, filename: str, feed_id: str, share_url: str) -> bool:
        token = self._get_token()
        if not token:
            LOG.warning("飞书通知：无法获取 token，跳过通知")
            return False
        url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
        content = json.dumps({
            "receive_id": self.user_id,
            "msg_type": "text",
            "content": json.dumps({
                "text": f"✅ 动态壁纸发帖成功\n📹 {filename}\n🔗 {share_url}"
            })
        }).encode()
        req = urllib.request.Request(url, data=content, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                ok = result.get("code") == 0
                if not ok:
                    LOG.warning("飞书通知失败: %s", result)
                return ok
        except Exception as exc:
            # 区分是否是 token 失效（401/403），若是则清缓存下次重试
            if hasattr(exc, 'code') and exc.code in (401, 403):
                LOG.warning("飞书 token 失效，清除缓存: %s", exc)
                self._token = None
            else:
                LOG.warning("飞书通知异常: %s", exc)
            return False

    def alert(self, title: str, message: str) -> bool:
        """发送告警通知（不受发帖成功/失败状态影响）"""
        token = self._get_token()
        if not token:
            LOG.warning("飞书告警：无法获取 token，跳过通知")
            return False
        url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
        content = json.dumps({
            "receive_id": self.user_id,
            "msg_type": "text",
            "content": json.dumps({
                "text": f"⚠️ {title}\n{message}"
            })
        }).encode()
        req = urllib.request.Request(url, data=content, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                return result.get("code") == 0
        except Exception as exc:
            if hasattr(exc, 'code') and exc.code in (401, 403):
                self._token = None
            return False


class BdpanWallpaperPoster:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.translator = Translator(cfg)
        self.bdpan = BdpanRunner(cfg)
        self.store = PostedStore(cfg.state_file)
        self.cli = ChannelCLI(cfg)
        self.notifier = FeishuNotifier(
            cfg.feishu_app_id, cfg.feishu_app_secret, cfg.feishu_user_id
        ) if cfg.feishu_app_id and cfg.feishu_user_id else None

    def _scan_download_dir(self) -> list[Path]:
        """扫描下载目录，返回所有已完成的 mp4 文件（排除 .bdpan 残留）"""
        if not self.cfg.download_base.exists():
            return []
        return sorted(
            f for f in self.cfg.download_base.iterdir()
            if f.suffix.lower() == ".mp4" and f.stat().st_size > 0
        )

    def _cleanup_stale_pending(self, max_age_seconds: int = 3600) -> None:
        """清理超时的 pending 项（超过 max_age_seconds 未完成的视为失效）"""
        now = time.time()
        original_count = len(self.store._pending)
        self.store._pending = [
            p for p in self.store._pending
            if now - p.get("pending_at", 0) < max_age_seconds
        ]
        removed = original_count - len(self.store._pending)
        if removed:
            self.store._save()
            LOG.warning("清理了 %d 个超时 pending 项（>%ds）", removed, max_age_seconds)

    def run_download(self) -> int:
        """
        异步下载模式：找到一个未发帖的 mp4，下载到目录后写入 pending，然后退出。
        只有确定不可下载的文件才标记跳过；普通下载失败会移除 pending，等待下次重试。
        """
        now = cst_now()
        LOG.info("===== 下载器 @ %s =====", now.strftime("%Y-%m-%d %H:%M:%S"))

        # 清理超时的 pending 项（超过1小时未完成的视为失效）
        self._cleanup_stale_pending(max_age_seconds=3600)

        while True:
            LOG.info("扫描网盘 动态壁纸/ 目录...")
            wallpaper = self.bdpan.iter_mp4_newest_first(self.store)
            if not wallpaper:
                LOG.warning("所有壁纸均已发帖过（或无 mp4 文件）")
                # 登录失效检测：尝试一次 /ls 探测
                code, out, _ = self.bdpan._run(
                    ["ls", "--json", BdpanRunner.REMOTE_BASE], timeout=10
                )
                if code != 0 or not out:
                    err_msg = "百度网盘登录可能已失效（ls 返回空），请重新登录"
                    LOG.error(err_msg)
                    if self.notifier:
                        self.notifier.alert("动态壁纸机器人异常", err_msg)
                return 2
            remote_path = wallpaper["path"]
            filename = wallpaper["server_filename"]
            local_filename = local_filename_for(wallpaper)
            wallpaper["local_filename"] = local_filename
            size_mb = wallpaper.get("size", 0) / (1024 * 1024)
            LOG.info("选中壁纸: %s (%.1f MB)", filename, size_mb)
            LOG.info("网盘路径: %s", remote_path)

            # 先加入 pending（下载前就标记），这样 crash 后下次不会重复选同一个文件
            self.store.add_pending(wallpaper)

            size_bytes = int(wallpaper.get("size", 0))
            LOG.info("开始下载（异步）...")
            ok, local_path, should_skip = self.bdpan.download(
                remote_path, self.cfg.download_base, local_filename, size_bytes
            )

            if should_skip:
                LOG.info("该文件不可下载，标记已处理后继续下一个...")
                self.store.mark_posted(remote_path)
                self.store.remove_pending(remote_path)
                continue

            if not ok:
                LOG.error("下载失败，移除 pending（下次重新尝试同一个未发帖文件）")
                self.store.remove_pending(remote_path)
                return 3

            LOG.info("下载完成，进入等待队列: %s", local_path.name)
            return 0

    def run_watch(self) -> int:
        """
        定时扫描下载目录，有 mp4 就发帖。
        流程：扫描 → 有文件 → 发帖 → 删文件 → mark_posted → 再扫描
        永远不主动退出（定时任务每次调用只扫一次，cron 控制频率）
        """
        now = cst_now()
        LOG.info("===== Watcher @ %s =====", now.strftime("%Y-%m-%d %H:%M:%S"))

        # 清理超时的 pending 项
        self._cleanup_stale_pending(max_age_seconds=3600)

        files = self._scan_download_dir()
        if not files:
            LOG.info("下载目录暂无 mp4，等待下载完成...")
            return 0

        # 检查 pending 队列，按顺序处理
        pending = self.store.get_pending()
        LOG.info("待处理 %d 个下载任务，%d 个本地 mp4 文件", len(pending), len(files))

        # 发帖冷却期检查
        if not self.store.can_post_now():
            LOG.info("发帖冷却期中，跳过本次扫描")
            return 0

        for local_path in files:
            # 优先用唯一 local_filename 匹配；兼容旧 pending 再回退到 server_filename。
            pending_item = next(
                (
                    p for p in pending
                    if p.get("local_filename") == local_path.name
                    or (
                        not p.get("local_filename")
                        and Path(p.get("server_filename", "")).name == local_path.name
                    )
                ),
                None
            )
            if pending_item:
                remote_path = pending_item["path"]
                display_filename = Path(pending_item.get("server_filename", local_path.name)).name
            else:
                # 无 pending 记录：文件可能是上一次 watch post 成功后残留（极少见）
                # 用 posted_store 反查——如果已 posted 就删掉，否则跳过等 downloader 补上 pending
                remote_path = None
                for p in self.store._posted:
                    if Path(p).name == local_path.name:
                        remote_path = p
                        break
                if not remote_path:
                    LOG.warning("本地文件 %s 无对应 pending 且无法匹配已发帖记录，跳过",
                                local_path.name)
                    continue
                display_filename = local_path.name
            LOG.info("发现已完成壁纸: %s", display_filename)

            # 翻译
            name_en = extract_name(display_filename)
            if is_likely_english(name_en):
                name_zh = self.translator.translate(name_en)
                title = f"{name_en} · {name_zh}"
            else:
                title = name_en
            LOG.info("标题: %s", title)

            # 发帖
            ok, feed_id, share_url = self.cli.publish_feed(
                self.cfg.channel_id, title, local_path
            )
            if not ok:
                LOG.error("发帖失败: %s，跳过删除，稍后重试", share_url)
                continue

            LOG.info("发帖成功 ✅ feed_id=%s share_url=%s", feed_id or "(无)", share_url or "(无)")

            # 标记 + 清理
            self.store.mark_posted(remote_path)
            self.store.remove_pending(remote_path)
            self.store.record_post()  # 记录发帖时间，启动冷却期

            try:
                local_path.unlink()
                LOG.info("已删除本地文件: %s", local_path.name)
            except OSError as exc:
                LOG.warning("删除本地文件失败: %s", exc)

            # 飞书通知
            if self.notifier:
                self.notifier.send(display_filename, feed_id or "", share_url or "")

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
    p = argparse.ArgumentParser(description="百度网盘动态壁纸发帖机器人")
    sub = p.add_subparsers(dest="cmd", help="子命令")
    dl = sub.add_parser("download", help="异步下载一个 mp4 到下载目录，然后退出")
    dl.add_argument("-v", "--verbose", action="store_true", help="打印 DEBUG 级日志")
    watch = sub.add_parser("watch", help="扫描下载目录，有 mp4 就发帖（供定时任务调用）")
    watch.add_argument("-v", "--verbose", action="store_true", help="打印 DEBUG 级日志")
    # 兼容旧行为：无子命令时默认 download
    p.set_defaults(cmd="download")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    try:
        cfg = Config.load()
    except Exception as exc:
        LOG.error("配置加载失败: %s", exc)
        return 10

    poster = BdpanWallpaperPoster(cfg)
    try:
        if args.cmd == "watch":
            return poster.run_watch()
        else:
            return poster.run_download()
    except KeyboardInterrupt:
        LOG.warning("用户中断")
        return 130
    except Exception as exc:
        LOG.exception("未处理异常: %s", exc)
        return 99


if __name__ == "__main__":
    sys.exit(main())
