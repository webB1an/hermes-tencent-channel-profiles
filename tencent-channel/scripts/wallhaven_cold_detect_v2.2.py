#!/usr/bin/env python3
"""
Wallhaven 壁纸库 · 频道冷度检测自动发帖脚本 (v2.2)

支持两种模式：
  1. 分类分发模式（默认）：每张壁纸按 tag 分类发到对应板块
  2. 单频道合并发帖模式：多张壁纸合并一条帖子发到指定板块（--merge 模式）

v2.2 相比 v2.1 的主要变化：
  1. 新频道 GUILD_ID = 640973304078348133（Wallhaven 频道）
  2. 支持按 tag 分类自动分发到不同板块
  3. 分类规则参照 GitHub 脚本 post_wallpaper_to_channel.py 的 TAG_CLASSIFY
  4. 每张壁纸独立发帖（分类模式），附带 hitokoto 一言文案
  5. 翻译链路：Google Translate 优先 + MiniMax 候补 + 退回英文原文
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
from typing import Any, Callable, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# =========================================================================
# 默认配置
# =========================================================================

CST = timezone(timedelta(hours=8))

DEFAULTS: dict[str, str] = {
    "GUILD_ID":              "640973304078348133",   # Wallhaven 频道（新）
    "CHANNEL_ID":            "728523265",             # 默认板块（动漫插画）
    "HERMES_HOME":           "/root/.hermes/profiles/tencent-channel",
    "WALLPAPER_DIR":         "/root/.hermes/profiles/tencent-channel/media/wallhaven",
    "STATE_FILE":            "/root/.hermes/profiles/tencent-channel/wallhaven_state.json",
    "CHECK_WINDOW_START":    "7",
    "CHECK_WINDOW_END":      "23",
    "COLD_THRESHOLD_MIN":    "65",
    "MIN_SELF_INTERVAL_MIN": "120",
    "DOWNLOAD_COUNT":        "3",
    "MAX_IMAGES_PER_POST":   "9",
    "WALLHAVEN_API":         "https://wallhaven.cc/api/v1",
    "WALLHAVEN_PURITY":     "100",
    "MINIMAX_BASE_URL":      "https://api.minimaxi.com/v1",
    "MINIMAX_MODEL":         "MiniMax-M2.7",
    "HTTP_TIMEOUT":          "15",
    "USER_AGENT":            "Hermes-WallhavenBot/2.2",
    "POST_MODE":             "classify",   # "classify" | "merge"
    "HITOKOTO_API":          "https://v1.hitokoto.cn/?encode=text",
}

LOG = logging.getLogger("wallhaven-cold")

# =========================================================================
# 板块映射表（来自 post_wallpaper_to_channel.py）
# =========================================================================

CHANNEL_MAP: dict[str, dict[str, str]] = {
    "anime":     {"name": "动漫插画",   "id": "728523265"},
    "game":      {"name": "游戏电竞",   "id": "728523307"},
    "landscape": {"name": "风景自然",   "id": "728523284"},
    "car":       {"name": "汽车机械",   "id": "728523386"},
    "city":      {"name": "城市建筑",   "id": "728523356"},
    "scifi":     {"name": "奇幻科幻",   "id": "728523341"},
    "animal":    {"name": "动物萌宠",   "id": "728523374"},
    "people":    {"name": "人物摄影",   "id": "728523320"},
    "art":       {"name": "抽象艺术",   "id": "728523340"},
    "movie":     {"name": "影视音乐",   "id": "728523419"},
    "solid":     {"name": "简约纯色",   "id": "728523356"},
    "default":   {"name": "全部",       "id": "728513789"},
}

# Tag 关键词 → 分类（来自 post_wallpaper_to_channel.py）
TAG_CLASSIFY: dict[str, list[str]] = {
    "anime": [
        "anime", "manga", "二次元", "动漫", "saber", "fate", "vocaloid",
        "lovelive", "touhou", "azurlane", "公主连结", "碧蓝航线",
        "明日方舟", "genshin", "崩坏", "赛马娘", "咒术回战", "鬼灭之刃",
        "刀剑神域", "进击的巨人", "hololive", "vtuber", "虚拟主播",
        "亚托莉", "老婆", "纸片人",
    ],
    "game": [
        "game", "games", "游戏", "塞尔达", "zelda", "pokemon", "宝可梦",
        "最终幻想", "ff14", "minecraft", "我的世界", "pubg", "apex",
        "valorant", "csgo", "lol", "dota", "steam", "switch", "ps5",
        "艾尔登法环", "elden ring", "黑魂", "dark souls", "王国之泪",
        "鸣潮", "无限暖暖", "绝区零",
    ],
    "landscape": [
        "landscape", "nature", "风景", "自然", "mountain", "海", "森林",
        "天空", "日落", "日出", "云", "湖泊", "河流", "海洋", "花",
        "星空", "moon", "月亮", "夕阳", "黄昏", "朝霞", "晚霞",
        "瀑布", "岛屿", "沙滩", "沙漠", "草原", "outdoor",
    ],
    "car": [
        "car", "cars", "汽车", "toyota", "bmw", "benz", "mercedes", "audi",
        "honda", "nissan", "supra", "evo", "porsche", "ferrari",
        "lamborghini", "赛车", "jdm", "摩托车", "motorcycle",
    ],
    "city": [
        "city", "urban", "城市", "建筑", "东京", "街道", "tower",
        "skyscraper", "夜景", "night", "涉谷", "新宿", "秋叶原",
        "paris", "london", "new york", "上海", "architecture", "building",
    ],
    "scifi": [
        "sci-fi", "sci fi", "科幻", "星球", "planet", "宇宙", "space",
        "cyberpunk", "赛博", "robot", "机器人", "机械", "高达", "gundam",
        "eve", "星际", "飞船", "alien", "外星人", "star wars", "三体",
        "赛博朋克", "蒸汽朋克",
    ],
    "animal": [
        "animal", "动物", "cat", "dog", "宠物", "puppy", "kitten", " bunny",
        "仓鼠", "鸟", "fish", "马", "horse", "狐狸", "fox", "wolf", "熊",
        "panda", "猫耳",
    ],
    "people": [
        "people", "人物", "肖像", "portrait", "写真", "model", "摄影",
        "photography", "girl", "boy", "女性", "男性", "人像", "街拍",
    ],
    "art": [
        "art", "艺术", "painting", "插画", "illustration", "drawing",
        "digital art", "数字艺术", "concept art", "概念艺术", "画师",
        "artist", "design", "设计",
    ],
    "movie": [
        "movie", "film", "影视", "music", "音乐", "电影", "cinema",
        "hollywood", "netflix", "disney", "主题曲", "ost", "soundtrack",
    ],
    "solid": [
        "solid", "纯色", "minimal", "简约", "background", "plain",
        "solid color", "gradient", "渐变", "blank", "空白",
    ],
}

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
    post_mode: str
    hitokoto_api: str

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
            post_mode=_get("POST_MODE"),
            hitokoto_api=_get("HITOKOTO_API"),
        )


# =========================================================================
# 通用工具：时间、重试、HTTP
# =========================================================================

def cst_now() -> datetime:
    return datetime.now(CST)


T = Optional


def retry(
    fn: Callable[[], T],
    *,
    tries: int = 3,
    base_delay: float = 1.5,
    on: tuple[type[BaseException], ...] = (URLError, HTTPError, TimeoutError, OSError),
    label: str = "",
) -> T:
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

    def get_text(self, url: str) -> str:
        def _do() -> str:
            with urlopen(self._request(url), timeout=self.timeout) as resp:
                return resp.read().decode("utf-8").strip()
        return retry(_do, label=f"GET {url}")

    def download(self, url: str, dest: Path, max_bytes: int = 40 * 1024 * 1024) -> None:
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
# 翻译器（与 v2.1 相同链路）
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
            except Exception as exc:
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
        headers = {
            "Authorization": f"Bearer {self.cfg.minimax_api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.cfg.minimax_base_url}/chat/completions"
        result = self.http.post_json(url, payload, headers)
        content = result["choices"][0]["message"]["content"]
        cleaned = re.sub(r"<[^>]+>", "", content, flags=re.DOTALL).strip()
        cleaned = re.sub(r"^\s*(tags?|翻译|result)\s*[:：]\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip(" 　\"'`")
        return cleaned or None

    def post_json(self, url: str, payload: dict, headers: dict) -> Any:
        data = json.dumps(payload).encode("utf-8")
        req = Request(url, data=data, headers=headers, method="POST")
        with urlopen(req, timeout=self.cfg.http_timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


# =========================================================================
# 已发送 ID 持久化
# =========================================================================

class PostedStore:
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
# Wallhaven 客户端
# =========================================================================

@dataclass
class Wallpaper:
    id: str
    url: str
    tags: list[str] = field(default_factory=list)
    resolution: str = ""
    local_path: Optional[Path] = None
    category_key: str = "default"
    channel_id: str = ""
    channel_name: str = ""


def classify_tags(tags: list[str]) -> tuple[str, str, str]:
    """根据 tag list 返回 (cat_key, 板块名, channel_id)"""
    tag_str = " ".join(t.lower() for t in tags)
    for cat, keywords in TAG_CLASSIFY.items():
        for kw in keywords:
            if kw.lower() in tag_str:
                info = CHANNEL_MAP[cat]
                return cat, info["name"], info["id"]
    return "default", CHANNEL_MAP["default"]["name"], CHANNEL_MAP["default"]["id"]


class WallhavenClient:
    def __init__(self, http: HttpClient, cfg: Config) -> None:
        self.http = http
        self.cfg = cfg

    def pick_random(self, want: int, exclude_ids: set[str]) -> list[Wallpaper]:
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
            except Exception as exc:
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
        # 对最终候选拉详情拿 tag 名 + 分类
        for wp in result:
            try:
                detail = self.http.get_json(f"{self.cfg.wallhaven_api}/w/{wp.id}")
                tag_names = [
                    t.get("name", "").strip()
                    for t in (detail.get("data", {}).get("tags") or [])
                    if t.get("name")
                ]
                wp.tags = [t for t in tag_names if t]
                cat_key, cat_name, channel_id = classify_tags(wp.tags)
                wp.category_key = cat_key
                wp.channel_id = channel_id
                wp.channel_name = cat_name
            except Exception as exc:
                LOG.warning("获取 %s 详情失败（不影响发帖）: %s", wp.id, exc)
        return result

    def download_all(self, wps: list[Wallpaper], out_dir: Path) -> list[Wallpaper]:
        out_dir.mkdir(parents=True, exist_ok=True)
        ok: list[Wallpaper] = []
        for wp in wps:
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
                time.sleep(1.0)
            except Exception as exc:
                LOG.warning("下载失败 %s: %s", wp.id, exc)
        return ok


# =========================================================================
# 腾讯频道 CLI 封装
# =========================================================================

class ChannelCLI:
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
        cmd = [self.binary, domain, action, "--json"]
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
            LOG.error("找不到 %s，请确认 CLI 已安装", self.binary)
            return 127, "", "binary not found"
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()

    def get_guild_feeds(self, get_type: int = 2, page: int = 1) -> list[dict]:
        code, out, err = self.call(
            "feed", "get-guild-feeds",
            {"guild_id": self.cfg.guild_id, "get_type": get_type, "page": page},
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
        payload = {
            "guild_id": self.cfg.guild_id,
            "channel_id": channel_id,
            "content": content,
            "file_paths": [{"file_path": str(p)} for p in image_paths],
        }
        code, out, err = self.call(
            "feed", "publish-feed",
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
        feed_id = str(data.get("feed_id") or data.get("id") or data.get("feedId") or "")
        share_url = data.get("share_url", "") or ""
        return True, feed_id, share_url, ""


# =========================================================================
# 冷度检测与发帖编排
# =========================================================================

class ColdPublisher:
    SELF_POST_TS_TOLERANCE = 90

    def __init__(self, cfg: Config, *, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.http = HttpClient(cfg.user_agent, cfg.http_timeout)
        self.translator = Translator(self.http, cfg)
        self.wallhaven = WallhavenClient(self.http, cfg)
        self.cli = ChannelCLI(cfg)
        self.store = PostedStore(cfg.state_file)

    def is_in_check_window(self, now: datetime) -> bool:
        start, end = self.cfg.check_window
        return start <= now.hour < end

    def _is_self_feed(self, feed: dict) -> bool:
        fid = str(feed.get("feed_id") or feed.get("id") or feed.get("feedId") or "")
        if fid and fid in self.store.self_feed_ids:
            return True
        create_ts = int(feed.get("create_time_raw") or 0)
        if create_ts <= 0:
            return False
        for self_ts in self.store.self_post_timestamps:
            if abs(create_ts - self_ts) <= self.SELF_POST_TS_TOLERANCE:
                return True
        return False

    def is_guild_cold(self, now: datetime) -> bool:
        threshold_ts = int((now - self.cfg.cold_threshold).timestamp())
        feeds = self.cli.get_guild_feeds(get_type=2, page=1)
        if not feeds:
            LOG.info("未获取到频道帖子，保守跳过发帖")
            return False
        for feed in feeds:
            create_ts = int(feed.get("create_time_raw") or 0)
            if create_ts < threshold_ts:
                continue
            if self._is_self_feed(feed):
                continue
            title = (feed.get("title") or "")[:30]
            LOG.info("频道 %s 近 %d 分钟内有真人新帖 (%s)",
                     self.cfg.guild_id,
                     int(self.cfg.cold_threshold.total_seconds() // 60),
                     title)
            return False
        return True

    def cooldown_remaining(self, now: datetime) -> timedelta:
        last = self.store.last_self_post_ts
        if last <= 0:
            return timedelta(0)
        elapsed = now - datetime.fromtimestamp(last, CST)
        return self.cfg.min_self_interval - elapsed

    def get_hitokoto(self) -> str:
        """获取一言文案"""
        try:
            return self.http.get_text(self.cfg.hitokoto_api)
        except Exception:
            return "壁纸分享~"

    def build_content(self, wp: Wallpaper) -> str:
        """为单张壁纸构建文案（分类模式：hitokoto + 翻译 tags）"""
        hitokoto = self.get_hitokoto()
        tags = wp.tags[:2]
        tag_line = ""
        if tags:
            tag_line = self.translator.translate(tags)
        if not tag_line and wp.resolution:
            tag_line = wp.resolution
        if tag_line:
            return f"{hitokoto}\n{tag_line}"
        return hitokoto

    def run_once(self, *, force: bool = False, merge: bool = False) -> int:
        now = cst_now()
        if not force and not self.is_in_check_window(now):
            LOG.info("当前时间 %s 不在检测窗口 %s，跳过",
                     now.strftime("%H:%M"), self.cfg.check_window)
            return 0

        LOG.info("===== Wallhaven 冷度检测开始 @ %s =====", now.strftime("%Y-%m-%d %H:%M:%S"))

        if not force:
            remaining = self.cooldown_remaining(now)
            if remaining.total_seconds() > 0:
                mins = int(remaining.total_seconds() // 60) + 1
                LOG.info("距上次自发帖未达最小间隔，还需等待约 %d 分钟", mins)
                return 0

            if not self.is_guild_cold(now):
                LOG.info("频道活跃，无需发帖")
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

        LOG.info("准备发帖: 模式=%s, 图片=%d", self.cfg.post_mode, len(downloaded))

        if self.dry_run:
            for wp in downloaded:
                LOG.info("[DRY-RUN] 分类=%s 板块=%s(%s) tags=%s",
                         wp.category_key, wp.channel_name, wp.channel_id, wp.tags[:3])
            return 0

        if merge or self.cfg.post_mode == "merge":
            # 合并模式：所有图片发到同一个板块
            return self._post_merge(downloaded)
        else:
            # 分类模式：每张壁纸发到各自的分类板块
            return self._post_classify(downloaded)

    def _post_merge(self, wps: list[Wallpaper]) -> int:
        """合并模式：多图合并一条帖子发到默认板块"""
        downloaded = wps[:self.cfg.max_images_per_post]
        paths = [wp.local_path for wp in downloaded if wp.local_path]

        tags_all: list[str] = []
        for wp in downloaded:
            tags_all.extend(wp.tags[:2])
        tags_all = list(dict.fromkeys(tags_all))[:4]
        tag_line = self.translator.translate(tags_all) if tags_all else "壁纸分享"
        content = f"壁纸分享~\n{tag_line}"

        ok, feed_id, share_url, err = self.cli.publish_feed(
            self.cfg.channel_id, content, paths
        )
        if not ok:
            LOG.error("发帖失败: %s", err)
            return 4

        self.store.add_wallpapers(wp.id for wp in downloaded)
        self.store.record_self_post(feed_id, int(cst_now().timestamp()))
        LOG.info("发帖成功 ✅ merge 模式 feed_id=%s", feed_id or "(未返回)")
        return 0

    def _post_classify(self, wps: list[Wallpaper]) -> int:
        """分类模式：每张壁纸独立发到各自的分类板块"""
        posted_count = 0
        errors: list[str] = []

        for wp in wps:
            if not wp.local_path:
                errors.append(f"{wp.id} 无本地文件")
                continue

            content = self.build_content(wp)
            target_channel = wp.channel_id or self.cfg.channel_id

            ok, feed_id, share_url, err = self.cli.publish_feed(
                target_channel, content, [wp.local_path]
            )
            if not ok:
                errors.append(f"{wp.id}→{wp.category_key} 失败: {err[:80]}")
                LOG.error("发帖失败 %s: %s", wp.id, err)
                continue

            self.store.record_self_post(feed_id, int(cst_now().timestamp()))
            posted_count += 1
            LOG.info("✅ 发帖成功 [%s] %s → %s(%s) feed_id=%s",
                     wp.id, wp.resolution, wp.category_key, target_channel,
                     feed_id or "(未返回)")
            time.sleep(2.0)  # 分类模式每张之间稍作间隔

        self.store.add_wallpapers(wp.id for wp in wps)

        if posted_count > 0:
            LOG.info("分类发帖完成: 成功 %d 条，失败 %d 条", posted_count, len(errors))
            return 0
        else:
            LOG.error("所有壁纸发帖均失败")
            return 4


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
    p = argparse.ArgumentParser(description="Wallhaven 壁纸库冷度检测 & 分类自动发帖 (v2.2)")
    p.add_argument("--dry-run", action="store_true", help="下载并准备内容，但不实际发帖")
    p.add_argument("--force", action="store_true", help="跳过时间窗口和冷度判定，强制发一次")
    p.add_argument("--merge", action="store_true", help="合并模式：所有图片发到同一板块（不走分类）")
    p.add_argument("-v", "--verbose", action="store_true", help="打印 DEBUG 级日志")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    try:
        cfg = Config.load()
    except Exception as exc:
        LOG.error("配置加载失败: %s", exc)
        return 10

    publisher = ColdPublisher(cfg, dry_run=args.dry_run)
    try:
        return publisher.run_once(force=args.force, merge=args.merge)
    except KeyboardInterrupt:
        LOG.warning("用户中断")
        return 130
    except Exception as exc:
        LOG.exception("未处理异常: %s", exc)
        return 99


if __name__ == "__main__":
    sys.exit(main())
