#!/usr/bin/env python3
"""
Wallpaper 壁纸库 · 频道冷度检测自动发帖脚本 (v2.1)

检测窗口 (默认 7:00-23:00, 北京时间) 内，每小时执行一次；
发帖条件 (全部满足):
  1. 距上次"自发帖"已超过 MIN_SELF_INTERVAL_MIN (默认 120 分钟)
  2. 整个频道 (GUILD_ID) 近 COLD_THRESHOLD_MIN 分钟无"真人"新帖
     —— 脚本自己发过的帖会从活跃度判定中剔除
满足条件后，从 Wallhaven 抓取 DOWNLOAD_COUNT 张随机壁纸，
翻译 tags 并发到指定板块 (CHANNEL_ID)。

相比 v1 的主要改进：
  1. 安全：CLI 调用改为 list + shell=False + stdin JSON，彻底规避 shell 注入
  2. 冷度语义：活跃度只看真人帖，避免"自己发的帖把自己判为活跃"
  3. 限速：死群场景下脚本自己也有最小发帖间隔，不会疯狂刷屏
  4. 去重：本地维护 posted_ids.json，避免重复推送同一张壁纸
  5. 健壮：网络请求统一指数退避重试；裸 except 全部收口
  6. NSFW：Wallhaven 搜索显式传 purity=100（SFW only），categories 不限制
  7. 性能：只对最终候选拉 /w/{id} 详情，减少 wallhaven 请求
  8. 可观测：logging + 结构化日志；支持 --dry-run / --force / -v
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# =========================================================================
# 默认配置（可被环境变量或 .env 覆盖）
# =========================================================================

CST = timezone(timedelta(hours=8))

DEFAULTS: dict[str, str] = {
    "GUILD_ID":           "652812504031889164",
    "CHANNEL_ID":         "669891684",                          # 静态壁纸板块
    "HERMES_HOME":        "/root/.hermes/profiles/tencent-channel",
    "WALLPAPER_DIR":      "/root/.hermes/profiles/tencent-channel/media",
    "STATE_FILE":         "/root/.hermes/profiles/tencent-channel/wallpaper_state.json",
    "CHECK_WINDOW_START": "7",
    "CHECK_WINDOW_END":   "23",
    "COLD_THRESHOLD_MIN":  "65",    # 近 65 分钟无"真人"新帖即视为"冷"
    "MIN_SELF_INTERVAL_MIN": "120", # 脚本自己两次发帖之间的最小间隔（冷群限速）
    "DOWNLOAD_COUNT":     "3",
    "MAX_IMAGES_PER_POST": "9",     # 一贴最多图片数 (腾讯频道短贴上限 18)
    "WALLHAVEN_API":      "https://wallhaven.cc/api/v1",
    "WALLHAVEN_PURITY":   "100",    # 100=SFW only（分类 categories 不限制，默认全开）
    "MINIMAX_BASE_URL":   "https://api.minimaxi.com/v1",
    "MINIMAX_MODEL":      "MiniMax-M2.7",
    "HTTP_TIMEOUT":       "15",
    "USER_AGENT":         "Hermes-WallpaperBot/2.0",
}

LOG = logging.getLogger("wallpaper-cold")


# =========================================================================
# 配置加载
# =========================================================================

def load_env_file(path: Path) -> dict[str, str]:
    """读取简单 KEY=VALUE 格式的 .env 文件，不处理 export / 变量展开"""
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
    wallpaper_dir: Path
    state_file: Path
    check_window: tuple[int, int]
    cold_threshold: timedelta
    min_self_interval: timedelta
    download_count: int
    max_images_per_post: int
    wallhaven_api: str
    wallhaven_purity: str
    minimax_base_url: str
    minimax_model: str
    minimax_api_key: str
    http_timeout: int
    user_agent: str

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
            wallpaper_dir=Path(_get("WALLPAPER_DIR")),
            state_file=Path(_get("STATE_FILE")),
            check_window=(_int("CHECK_WINDOW_START"), _int("CHECK_WINDOW_END")),
            cold_threshold=timedelta(minutes=_int("COLD_THRESHOLD_MIN")),
            min_self_interval=timedelta(minutes=_int("MIN_SELF_INTERVAL_MIN")),
            download_count=_int("DOWNLOAD_COUNT"),
            max_images_per_post=_int("MAX_IMAGES_PER_POST"),
            wallhaven_api=_get("WALLHAVEN_API"),
            wallhaven_purity=_get("WALLHAVEN_PURITY"),
            minimax_base_url=_get("MINIMAX_BASE_URL"),
            minimax_model=_get("MINIMAX_MODEL"),
            minimax_api_key=env.get("MINIMAX_CN_API_KEY", ""),
            http_timeout=_int("HTTP_TIMEOUT"),
            user_agent=_get("USER_AGENT"),
        )


# =========================================================================
# 通用工具：时间、重试、HTTP
# =========================================================================

def cst_now() -> datetime:
    return datetime.now(CST)


T = TypeVar("T")


def retry(
    fn: Callable[[], T],
    *,
    tries: int = 3,
    base_delay: float = 1.5,
    on: tuple[type[BaseException], ...] = (URLError, HTTPError, TimeoutError, OSError),
    label: str = "",
) -> T:
    """简单的指数退避重试，只捕获指定异常类型"""
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
    assert last_exc is not None
    raise last_exc


class HttpClient:
    def __init__(self, user_agent: str, timeout: int) -> None:
        self.user_agent = user_agent
        self.timeout = timeout

    def _request(self, url: str, extra_headers: Optional[dict] = None) -> Request:
        headers = {"User-Agent": self.user_agent}
        if extra_headers:
            headers.update(extra_headers)
        return Request(url, headers=headers)

    def get_json(self, url: str) -> Any:
        def _do() -> Any:
            with urlopen(self._request(url), timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        return retry(_do, label=f"GET {url}")

    def download(self, url: str, dest: Path, max_bytes: int = 40 * 1024 * 1024) -> None:
        """原子化下载：写临时文件再 rename；限制最大大小，防止恶意大文件"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")

        def _do() -> None:
            written = 0
            try:
                with urlopen(self._request(url), timeout=self.timeout) as resp, open(tmp, "wb") as fh:
                    while True:
                        chunk = resp.read(1024 * 64)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > max_bytes:
                            raise IOError(f"文件超过 {max_bytes} 字节上限")
                        fh.write(chunk)
                os.replace(tmp, dest)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
        retry(_do, label=f"download {url}")


# =========================================================================
# 已发送 ID 持久化
# =========================================================================

class PostedStore:
    """
    持久化脚本状态：
      - posted_ids:          已发送过的 wallhaven 壁纸 ID（防止随机抽重）
      - self_feed_ids:       自己发过的频道 feed_id（用于判定"这条帖是不是我发的"）
      - self_post_timestamps: 自己发帖时的 unix 秒时间戳（feed_id 兜底方案）
      - last_self_post_ts:   最近一次自发帖时间（用于最小间隔限速）
    """

    KEEP_LAST_WALLS = 1000
    KEEP_LAST_SELF  = 200

    def __init__(self, path: Path) -> None:
        self.path = path
        self._ids: list[str] = []
        self._self_feed_ids: list[str] = []
        self._self_post_timestamps: list[int] = []
        self._last_self_post_ts: int = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._ids = list(data.get("posted_ids", []))
            self._self_feed_ids = list(data.get("self_feed_ids", []))
            self._self_post_timestamps = [int(x) for x in data.get("self_post_timestamps", [])]
            self._last_self_post_ts = int(data.get("last_self_post_ts", 0))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            LOG.warning("状态文件读取失败，按空处理: %s", exc)
            self._ids = []
            self._self_feed_ids = []
            self._self_post_timestamps = []
            self._last_self_post_ts = 0

    # -------- 壁纸去重 --------
    @property
    def wallpaper_ids(self) -> set[str]:
        return set(self._ids)

    def contains(self, wid: str) -> bool:
        return wid in self._ids

    def add_wallpapers(self, ids: Iterable[str]) -> None:
        new_ids = [i for i in ids if i and i not in self._ids]
        if not new_ids:
            return
        self._ids.extend(new_ids)
        if len(self._ids) > self.KEEP_LAST_WALLS:
            self._ids = self._ids[-self.KEEP_LAST_WALLS:]
        self._save()

    # -------- 自发帖记录 --------
    @property
    def last_self_post_ts(self) -> int:
        return self._last_self_post_ts

    @property
    def self_feed_ids(self) -> set[str]:
        return set(self._self_feed_ids)

    @property
    def self_post_timestamps(self) -> list[int]:
        return list(self._self_post_timestamps)

    def record_self_post(self, feed_id: str, ts: int) -> None:
        if feed_id:
            self._self_feed_ids.append(feed_id)
            if len(self._self_feed_ids) > self.KEEP_LAST_SELF:
                self._self_feed_ids = self._self_feed_ids[-self.KEEP_LAST_SELF:]
        self._self_post_timestamps.append(int(ts))
        if len(self._self_post_timestamps) > self.KEEP_LAST_SELF:
            self._self_post_timestamps = self._self_post_timestamps[-self.KEEP_LAST_SELF:]
        self._last_self_post_ts = int(ts)
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({
                "posted_ids": self._ids,
                "self_feed_ids": self._self_feed_ids,
                "self_post_timestamps": self._self_post_timestamps,
                "last_self_post_ts": self._last_self_post_ts,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)


# =========================================================================
# 翻译：Google Translate 优先，MiniMax 候补，失败回退原文
# =========================================================================

class Translator:
    def __init__(self, http: HttpClient, cfg: Config) -> None:
        self.http = http
        self.cfg = cfg

    def translate(self, tags: list[str]) -> str:
        if not tags:
            return ""
        for fn, name in [
            (self._google, "google"),
            (self._minimax, "minimax"),
        ]:
            try:
                result = fn(tags)
                if result and result.strip():
                    return result.strip()
            except Exception as exc:  # noqa: BLE001 - 翻译失败不影响主流程
                LOG.warning("翻译渠道 %s 失败: %s", name, exc)
        LOG.info("所有翻译渠道失败，回退到英文原文")
        return " · ".join(tags)

    def _google(self, tags: list[str]) -> Optional[str]:
        text = ",".join(tags)
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=en&tl=zh-CN&dt=t&q={urllib.parse.quote(text)}"
        )
        data = self.http.get_json(url)
        parts = [seg[0] for seg in data[0] if seg and seg[0]]
        joined = " · ".join(p.strip(" ,，") for p in parts if p.strip())
        return joined or None

    def _minimax(self, tags: list[str]) -> Optional[str]:
        if not self.cfg.minimax_api_key:
            return None
        prompt = (
            "Translate the following English tags to Simplified Chinese. "
            "Return only the translated tags separated by ' · ', keep the order. "
            "Do not add any explanation or extra text.\n\n"
            f"Tags: {','.join(tags)}"
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
        # 去掉所有 <...> 标签（think/reasoning 等）
        cleaned = re.sub(r"<[^>]+>", "", content, flags=re.DOTALL).strip()
        # 防止模型返回带引号、"Tags:" 前缀等噪音
        cleaned = re.sub(r"^\s*(tags?|翻译|result)\s*[:：]\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip(" 　\"'`")
        return cleaned or None


# =========================================================================
# Wallhaven 客户端
# =========================================================================

@dataclass
class Wallpaper:
    id: str
    url: str            # 原图 URL
    tags: list[str] = field(default_factory=list)
    resolution: str = ""
    local_path: Optional[Path] = None


class WallhavenClient:
    def __init__(self, http: HttpClient, cfg: Config) -> None:
        self.http = http
        self.cfg = cfg

    def pick_random(self, want: int, exclude_ids: set[str]) -> list[Wallpaper]:
        """从 random 搜索接口抽取 want 张未发过的壁纸（不拉 /w/{id} 详情）"""
        picked: dict[str, Wallpaper] = {}
        page = 1
        max_pages = 6
        while len(picked) < want and page <= max_pages:
            params = {
                "sorting": "random",
                "purity": self.cfg.wallhaven_purity,
                "page": str(page),
            }
            url = f"{self.cfg.wallhaven_api}/search?{urllib.parse.urlencode(params)}"
            try:
                data = self.http.get_json(url)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Wallhaven search 第 %d 页失败: %s", page, exc)
                page += 1
                continue

            items = data.get("data", []) or []
            if not items:
                break

            for item in items:
                wid = str(item.get("id") or "")
                if not wid or wid in picked or wid in exclude_ids:
                    continue
                path = item.get("path")
                if not path:
                    continue
                picked[wid] = Wallpaper(
                    id=wid,
                    url=path,
                    resolution=item.get("resolution", "") or "",
                )
                if len(picked) >= want:
                    break
            page += 1

        result = list(picked.values())[:want]
        # 对最终候选（仅这几张）拉详情拿 tag 名
        for wp in result:
            try:
                detail = self.http.get_json(f"{self.cfg.wallhaven_api}/w/{wp.id}")
                tag_names = [
                    t.get("name", "").strip()
                    for t in (detail.get("data", {}).get("tags") or [])
                    if t.get("name")
                ]
                wp.tags = [t for t in tag_names if t]
            except Exception as exc:  # noqa: BLE001
                LOG.warning("获取 %s 详情失败（不影响发帖）: %s", wp.id, exc)
        return result

    def download_all(self, wps: list[Wallpaper], out_dir: Path) -> list[Wallpaper]:
        out_dir.mkdir(parents=True, exist_ok=True)
        ok: list[Wallpaper] = []
        for wp in wps:
            # 正确地从 URL path 取扩展名，避免 query string 污染
            ext = Path(urllib.parse.urlparse(wp.url).path).suffix or ".jpg"
            fpath = out_dir / f"wallhaven-{wp.id}{ext}"
            if fpath.exists() and fpath.stat().st_size > 0:
                wp.local_path = fpath
                ok.append(wp)
                LOG.info("已有缓存: %s", fpath.name)
                continue
            try:
                self.http.download(wp.url, fpath)
                wp.local_path = fpath
                ok.append(wp)
                LOG.info("已下载: %s (tags=%s)", fpath.name, wp.tags[:3])
                time.sleep(1.0)   # 轻度限速
            except Exception as exc:  # noqa: BLE001
                LOG.warning("下载失败 %s: %s", wp.id, exc)
        return ok


# =========================================================================
# 腾讯频道 CLI 封装
# =========================================================================

class ChannelCLI:
    """
    统一通过 list + stdin JSON 调用 tencent-channel-cli，避免 shell 转义。

    CLI 支持两种传参模式（见官方 README）：
      - stdin JSON:   echo '{...}' | tencent-channel-cli <domain> <action>
      - CLI flag:     tencent-channel-cli <domain> <action> --key value
    这里统一用 stdin JSON 模式，所有字符串字段原样入 JSON，安全。
    """

    def __init__(self, cfg: Config, binary: str = "tencent-channel-cli") -> None:
        self.cfg = cfg
        self.binary = binary

    def _env(self) -> dict:
        env = os.environ.copy()
        env["HERMES_HOME"] = str(self.cfg.hermes_home)
        env.setdefault("HOME", str(self.cfg.hermes_home / "home"))
        return env

    def call(
        self,
        domain: str,
        action: str,
        payload: dict,
        *,
        extra_flags: Optional[list[str]] = None,
        timeout: int = 120,
    ) -> tuple[int, str, str]:
        cmd = [self.binary, domain, action]
        if extra_flags:
            cmd.extend(extra_flags)
        stdin_data = json.dumps(payload, ensure_ascii=False)
        LOG.debug("CLI call: %s | stdin=%s", " ".join(cmd), stdin_data)
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
            LOG.error("找不到 %s，请确认 CLI 已安装并在 PATH 中", self.binary)
            return 127, "", "binary not found"
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()

    def get_guild_feeds(self, get_type: int = 2, page: int = 1) -> list[dict]:
        """获取整个频道的帖子列表（跨所有板块）。get_type: 1=热门, 2=最新"""
        code, out, err = self.call(
            "feed",
            "get-guild-feeds",
            {
                "guild_id": self.cfg.guild_id,
                "get_type": get_type,
                "page": page,
            },
        )
        if code != 0 or not out:
            LOG.warning("get-guild-feeds 失败 code=%s stderr=%s", code, err[:200])
            return []
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            LOG.warning("解析 feeds 响应失败: %s", exc)
            return []
        return data.get("data", {}).get("feeds", []) or []

    def publish_feed(
        self,
        channel_id: str,
        content: str,
        image_paths: list[Path],
    ) -> tuple[bool, str, str, str]:
        """
        返回 (success, feed_id, share_url, err_msg)。
        feed_id 在不同 CLI 版本下字段名可能叫 id / feed_id / feedId，都兜底一下。
        """
        payload = {
            "guild_id": self.cfg.guild_id,
            "channel_id": channel_id,
            "content": content,
            "file_paths": [{"file_path": str(p)} for p in image_paths],
        }
        code, out, err = self.call(
            "feed",
            "publish-feed",
            payload,
            extra_flags=["--yes"],
            timeout=180,
        )
        if code != 0:
            err_msg = err[:200] if err else ""
            # exit=2 时错误 JSON 在 stdout 而非 stderr
            try:
                out_err = json.loads(out).get("error", {}).get("message", "") if out else ""
            except Exception:
                out_err = ""
            return False, "", "", f"exit={code} stderr={err_msg} out_err={out_err}"
        try:
            result = json.loads(out)
        except json.JSONDecodeError:
            return False, "", "", f"invalid json: {out[:200]}"
        if not result.get("success"):
            return False, "", "", f"api failed: {result}"
        data = result.get("data") or {}
        feed_id = str(
            data.get("feed_id")
            or data.get("id")
            or data.get("feedId")
            or ""
        )
        share_url = data.get("share_url", "") or ""
        return True, feed_id, share_url, ""


# =========================================================================
# 冷度检测与发帖编排
# =========================================================================

class ColdPublisher:
    def __init__(self, cfg: Config, *, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.http = HttpClient(cfg.user_agent, cfg.http_timeout)
        self.translator = Translator(self.http, cfg)
        self.wallhaven = WallhavenClient(self.http, cfg)
        self.cli = ChannelCLI(cfg)
        self.store = PostedStore(cfg.state_file)

    # ------------------------------------------------------------ 冷度检测

    SELF_POST_TS_TOLERANCE = 90   # 时间戳兜底匹配容差（秒）

    def is_in_check_window(self, now: datetime) -> bool:
        start, end = self.cfg.check_window
        return start <= now.hour < end

    def _is_self_feed(self, feed: dict) -> bool:
        """判断某条 feed 是不是脚本自己发的：feed_id 优先，时间戳兜底"""
        fid = str(
            feed.get("feed_id")
            or feed.get("id")
            or feed.get("feedId")
            or ""
        )
        if fid and fid in self.store.self_feed_ids:
            return True
        # 兜底：没拿到过 feed_id 的旧记录，用时间戳 ±tolerance 秒匹配
        create_ts = int(feed.get("create_time_raw") or 0)
        if create_ts <= 0:
            return False
        for self_ts in self.store.self_post_timestamps:
            if abs(create_ts - self_ts) <= self.SELF_POST_TS_TOLERANCE:
                return True
        return False

    def is_guild_cold(self, now: datetime) -> bool:
        """
        检测整个频道（跨所有板块）是否 '冷' —— 近 N 分钟无"真人"新帖。
        脚本自己发过的帖不算活跃。
        """
        threshold_ts = int((now - self.cfg.cold_threshold).timestamp())
        feeds = self.cli.get_guild_feeds(get_type=2, page=1)
        if not feeds:
            # 查询失败/频道为空：保守处理，不发帖
            LOG.info("未获取到频道帖子（可能频道为空或查询失败），保守跳过发帖")
            return False
        for feed in feeds:
            create_ts = int(feed.get("create_time_raw") or 0)
            if create_ts < threshold_ts:
                continue
            if self._is_self_feed(feed):
                LOG.debug("跳过自发帖 feed_id=%s ts=%s",
                          feed.get("feed_id") or feed.get("id"), create_ts)
                continue
            title = (feed.get("title") or "")[:30]
            LOG.info("频道 %s 近 %d 分钟内有真人新帖 (%s)",
                     self.cfg.guild_id,
                     int(self.cfg.cold_threshold.total_seconds() // 60),
                     title)
            return False
        return True

    def cooldown_remaining(self, now: datetime) -> timedelta:
        """距离下次可自发帖还差多久；≤0 表示 cooldown 已过"""
        last = self.store.last_self_post_ts
        if last <= 0:
            return timedelta(0)
        elapsed = now - datetime.fromtimestamp(last, CST)
        return self.cfg.min_self_interval - elapsed

    # ------------------------------------------------------------ 内容组装

    def build_content(self, wps: list[Wallpaper]) -> str:
        lines: list[str] = []
        for wp in wps:
            tags = wp.tags[:2]
            line = ""
            if tags:
                line = self.translator.translate(tags)
            if not line and wp.resolution:
                line = wp.resolution
            if line:
                lines.append(line)
        return "\n".join(lines) if lines else "壁纸分享"

    # ------------------------------------------------------------ 主流程

    def run_once(self, *, force: bool = False) -> int:
        now = cst_now()
        if not force and not self.is_in_check_window(now):
            LOG.info("当前时间 %s 不在检测窗口 %s，跳过",
                     now.strftime("%H:%M"), self.cfg.check_window)
            return 0

        LOG.info("===== 冷度检测开始 @ %s =====", now.strftime("%Y-%m-%d %H:%M:%S"))

        if not force:
            # 1) 最小自发帖间隔保护：防止在死群里连刷
            remaining = self.cooldown_remaining(now)
            if remaining.total_seconds() > 0:
                mins = int(remaining.total_seconds() // 60) + 1
                LOG.info("距上次自发帖未达最小间隔 %d 分钟，还需等待约 %d 分钟",
                         int(self.cfg.min_self_interval.total_seconds() // 60),
                         mins)
                return 0

            # 2) 频道冷度判定（真人帖）
            if not self.is_guild_cold(now):
                LOG.info("频道活跃（真人有新帖），无需发帖")
                return 0

        LOG.info("频道冷，开始抓取壁纸...")
        candidates = self.wallhaven.pick_random(
            self.cfg.download_count,
            exclude_ids=self.store.wallpaper_ids,
        )
        if not candidates:
            LOG.error("Wallhaven 未返回任何候选壁纸")
            return 2

        downloaded = self.wallhaven.download_all(candidates, self.cfg.wallpaper_dir)
        if not downloaded:
            LOG.error("所有候选壁纸下载均失败")
            return 3

        # 控制一帖图片数上限
        downloaded = downloaded[: self.cfg.max_images_per_post]
        content = self.build_content(downloaded)
        paths = [wp.local_path for wp in downloaded if wp.local_path]

        LOG.info("准备发帖: content=%r, images=%d", content, len(paths))
        if self.dry_run:
            LOG.info("[DRY-RUN] 跳过实际发帖")
            return 0

        ok, feed_id, share_url, err = self.cli.publish_feed(
            self.cfg.channel_id, content, paths
        )
        if not ok:
            LOG.error("发帖失败: %s", err)
            return 4

        self.store.add_wallpapers(wp.id for wp in downloaded)
        self.store.record_self_post(feed_id, int(cst_now().timestamp()))
        LOG.info("发帖成功 ✅ feed_id=%s share_url=%s", feed_id or "(未返回)", share_url or "(无)")
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


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="壁纸频道冷度检测 & 自动发帖")
    p.add_argument("--dry-run", action="store_true", help="下载并准备内容，但不实际发帖")
    p.add_argument("--force", action="store_true", help="跳过时间窗口和冷度判定，强制发一次")
    p.add_argument("-v", "--verbose", action="store_true", help="打印 DEBUG 级日志")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    try:
        cfg = Config.load()
    except Exception as exc:  # noqa: BLE001
        LOG.error("配置加载失败: %s", exc)
        return 10

    publisher = ColdPublisher(cfg, dry_run=args.dry_run)
    try:
        return publisher.run_once(force=args.force)
    except KeyboardInterrupt:
        LOG.warning("用户中断")
        return 130
    except Exception as exc:  # noqa: BLE001
        LOG.exception("未处理异常: %s", exc)
        return 99


if __name__ == "__main__":
    sys.exit(main())
