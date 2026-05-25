#!/usr/bin/env python3
"""
anime-pictures.net 壁纸机器人 · 频道冷度检测自动发帖脚本

基于 anime-pictures.net 关键词搜索，下载壁纸并发到腾讯频道。

架构：
  1. 用 requests 解析搜索结果页（anime-pictures.net 可直接访问，无 Cloudflare 拦截）
  2. 用 browser_tool 下载完整图片（images.anime-pictures.net DNS 在本服务器不可解析）
  3. 翻译标签（Google Translate → MiniMax API → 退回英文）
  4. tencent-channel-cli 发帖

发帖条件（全部满足）：
  1. 距上次"自发帖"已超过 MIN_SELF_INTERVAL_MIN（默认 120 分钟）
  2. 整个频道近 COLD_THRESHOLD_MIN 分钟无"真人"新帖
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# =========================================================================
# 默认配置（可被环境变量或 .env 覆盖）
# =========================================================================

CST = timezone(timedelta(hours=8))

DEFAULTS: dict[str, str] = {
    "GUILD_ID":              "652812504031889164",
    "CHANNEL_ID":            "669891684",                       # Wallpaper壁纸库静态壁纸板块
    "HERMES_HOME":           "/root/.hermes/profiles/tencent-channel",
    "WALLPAPER_DIR":         "/root/.hermes/profiles/tencent-channel/media/anime-pictures",
    "STATE_FILE":            "/root/.hermes/profiles/tencent-channel/anime_pictures_state.json",
    "CHECK_WINDOW_START":    "7",
    "CHECK_WINDOW_END":      "23",
    "COLD_THRESHOLD_MIN":    "65",
    "MIN_SELF_INTERVAL_MIN": "120",
    "DOWNLOAD_COUNT":        "3",
    "MAX_IMAGES_PER_POST":   "9",
    "HTTP_TIMEOUT":          "15",
    "USER_AGENT":            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "MINIMAX_BASE_URL":      "https://api.minimaxi.com/v1",
    "MINIMAX_MODEL":         "MiniMax-M2.7",
    "SEARCH_TAG":            "",         # 搜索关键词（如 "senko"），可填多个逗号分隔
}

LOG = logging.getLogger("anime-pictures")


# =========================================================================
# 配置加载
# =========================================================================

def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
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
    http_timeout: int
    user_agent: str
    minimax_base_url: str
    minimax_model: str
    minimax_api_key: str
    search_tag: str

    @classmethod
    def load(cls, args: argparse.Namespace) -> "Config":
        hermes_home = Path(os.environ.get("HERMES_HOME", DEFAULTS["HERMES_HOME"]))
        env = load_env_file(hermes_home / ".env")

        def _get(key: str) -> str:
            return os.environ.get(key, env.get(key, DEFAULTS.get(key, "")))

        def _int(key: str) -> int:
            return int(_get(key) or "0")

        # search_tag 从 CLI args 或环境变量
        search_tag = (args.search_tag or _get("SEARCH_TAG") or "").strip()
        if not search_tag:
            raise ValueError("必须指定搜索关键词（--search-tag 或 SEARCH_TAG 环境变量）")

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
            http_timeout=_int("HTTP_TIMEOUT"),
            user_agent=_get("USER_AGENT"),
            minimax_base_url=_get("MINIMAX_BASE_URL"),
            minimax_model=_get("MINIMAX_MODEL"),
            minimax_api_key=env.get("MINIMAX_CN_API_KEY", ""),
            search_tag=search_tag,
        )


# =========================================================================
# 通用工具：时间、重试、HTTP
# =========================================================================

def cst_now() -> datetime:
    return datetime.now(CST)


def in_check_window(now: datetime, window: tuple[int, int]) -> bool:
    hour = (now.astimezone(CST).hour)
    return window[0] <= hour <= window[1]


import urllib.request
from urllib.error import HTTPError, URLError


def retry(
    fn,
    *,
    tries: int = 3,
    base_delay: float = 1.5,
    on: tuple = (URLError, HTTPError, TimeoutError, OSError),
    label: str = "",
):
    last_exc: Optional[BaseException] = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except on as exc:
            last_exc = exc
            if attempt == tries:
                break
            sleep_time = base_delay * (2 ** (attempt - 1))
            LOG.warning("%s 第 %d/%d 次失败: %s，%.1fs 后重试",
                        label or str(fn), attempt, tries, exc, sleep_time)
            time.sleep(sleep_time)
    raise last_exc


class HttpClient:
    def __init__(self, user_agent: str, timeout: int) -> None:
        self.user_agent = user_agent
        self.timeout = timeout

    def _request(self, url: str, extra_headers: Optional[dict] = None):
        headers = {"User-Agent": self.user_agent}
        if extra_headers:
            headers.update(extra_headers)
        return urllib.request.Request(url, headers=headers)

    def get_text(self, url: str) -> str:
        def _do() -> str:
            with urllib.request.urlopen(self._request(url), timeout=self.timeout) as resp:
                return resp.read().decode("utf-8")
        return retry(_do, label=f"GET {url}")

    def get_bytes(self, url: str) -> bytes:
        def _do() -> bytes:
            with urllib.request.urlopen(self._request(url), timeout=self.timeout) as resp:
                return resp.read()
        return retry(_do, label=f"GET {url}")


# =========================================================================
# 翻译器
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
        import json
        data = json.loads(self.http.get_text(url))
        parts = [seg[0] for seg in data[0] if seg and seg[0]]
        joined = " · ".join(p.strip(" ,，") for p in parts if p.strip())
        return joined or None

    def _minimax(self, tags: list[str]) -> Optional[str]:
        if not self.cfg.minimax_api_key:
            return None
        import json
        prompt = (
            "Translate the following English tags to Simplified Chinese. "
            "Return only the translated tags separated by ' · ', keep the order. "
            "Do not add any explanation or extra text.\n\n"
            f"Tags: {','.join(tags)}"
        )
        payload = json.dumps({
            "model": self.cfg.minimax_model,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            f"{self.cfg.minimax_base_url}/text/chatcompletion_v2",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.cfg.minimax_api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.cfg.http_timeout) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()

    def translate_tag_string(self, tag_str: str) -> str:
        """翻译单个标签字符串（可能包含空格）"""
        tags = [t.strip() for t in tag_str.replace("/", " ").split() if t.strip()]
        return self.translate(tags)


# =========================================================================
# anime-pictures.net 解析
# =========================================================================

class AnimePicturesParser:
    """解析 anime-pictures.net 搜索结果页"""

    BASE_URL = "https://anime-pictures.net/posts"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def search(self, keyword: str, page: int = 0, lang: str = "en") -> list[dict]:
        """
        搜索关键词，返回帖子列表。

        返回 dict 列表，每个 dict 包含：
          post_id: int
          resolution: str  (如 "2892x3912")
          preview_url: str  (avif 缩略图)
          preview_bp_url: str  (1.5x 更大缩略图)
          bg_color: str  (如 "rgb(213, 207, 206)")
          slug: str  (URL slug)
        """
        encoded = urllib.parse.quote(keyword)
        url = f"{self.BASE_URL}?page={page}&search_tag={encoded}&lang={lang}"
        html = self.http.get_text(url)
        return self._parse_search_page(html)

    def _parse_search_page(self, html: str) -> list[dict]:
        """从搜索结果页 HTML 中提取帖子数据

        页面结构（相对 URL）：
          <a href="./posts/{id}?lang=en" title="Anime picture {WIDTHxHEIGHT}" ...>
            <img alt="Anime picture {WIDTHxHEIGHT}" src="https://opreviews.anime-pictures.net/..." />
          </a>
        """
        results: list[dict] = []

        # 方式1：提取带预览图的完整区块（最可靠）
        img_block_pattern = re.findall(
            r'<a\s+href="\./posts/(\d+)\?[^"\']*"[^>]*title="Anime picture\s+(\d+x\d+)"[^>]*>.*?'
            r'<img\s+alt="Anime picture\s+\d+x\d+"[^>]+src="(https://opreviews\.anime-pictures\.net/[^"]+)"',
            html, re.DOTALL
        )
        for post_id, res, preview_url in img_block_pattern:
            bp_url = preview_url.replace("_cp.", "_bp.")
            results.append({
                "post_id": int(post_id),
                "resolution": res,
                "preview_url": preview_url,
                "preview_bp_url": bp_url,
                "slug": "",
            })

        # 方式2：若方式1太少，用 title 属性兜底（可能有重复，需要去重）
        title_pattern = re.findall(
            r'<a\s+href="\./posts/(\d+)\?[^"\']*"[^>]*title="Anime picture\s+(\d+x\d+)"',
            html
        )
        seen_ids = {r["post_id"] for r in results}
        for post_id, res in title_pattern:
            if int(post_id) not in seen_ids:
                seen_ids.add(int(post_id))
                results.append({
                    "post_id": int(post_id),
                    "resolution": res,
                    "preview_url": "",
                    "preview_bp_url": "",
                    "slug": "",
                })

        return results

    def get_post_tags(self, post_id: int, lang: str = "en") -> dict[str, Any]:
        """
        获取帖子标签和元数据。

        返回 dict：
          copyright: [str]
          character: [str]
          artist: [str]
          reference: [str]
          all: [str]
          file_url: str (用于构造下载 URL)
          md5: str (图片 MD5)
          width: int
          height: int
          ext: str
        """
        url = f"https://anime-pictures.net/posts/{post_id}?lang={lang}"
        html = self.http.get_text(url)
        return self._parse_post_page(html)

    def _parse_post_page(self, html: str) -> dict[str, Any]:
        """
        从帖子页 HTML 的 JSON script 标签中提取标签和元数据。
        返回：
          copyright, character, artist, reference, meta, all: list[str]
          file_url, md5, ext, width, height
        """
        result: dict[str, Any] = {
            "copyright": [], "character": [], "artist": [],
            "reference": [], "meta": [], "all": [],
            "file_url": "", "md5": "", "ext": ".png",
            "width": 0, "height": 0,
        }

        json_scripts = re.findall(
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
            html, re.DOTALL
        )
        for s in json_scripts:
            try:
                data = json.loads(s)
                if "body" not in data:
                    continue
                body = json.loads(data["body"])
                if "tags" not in body:
                    continue

                TAG_TYPE_MAP = {
                    1: "character",   # 角色
                    2: "reference",    # 参考
                    3: "copyright",    # 版权方
                    4: "artist",       # 画师
                    6: "copyright",    # 其他版权
                    7: "meta",         # 元标签
                }

                for item in body["tags"]:
                    t = item.get("tag", {})
                    tag_name = t.get("tag", "")
                    if not tag_name:
                        continue
                    cat = TAG_TYPE_MAP.get(t.get("type", 0), "meta")
                    if tag_name not in result[cat]:
                        result[cat].append(tag_name)
                    if tag_name not in result["all"]:
                        result["all"].append(tag_name)

                # 提取帖子元数据
                if "post" in body:
                    post = body["post"]
                    result["md5"] = post.get("md5", "")
                    result["ext"] = post.get("ext", ".png")
                    result["width"] = post.get("width", 0)
                    result["height"] = post.get("height", 0)

                if "file_url" in body:
                    result["file_url"] = body["file_url"]

                break  # 找到 tags 就停止
            except (json.JSONDecodeError, KeyError):
                continue

        # 兜底：用正则从 HTML 中提取所有 search_tag
        if not result["all"]:
            tags = re.findall(r'href="[^"]*search_tag=([^&"]+)', html)
            for t in tags:
                tag = urllib.parse.unquote(t.replace("+", " "))
                if tag not in result["all"]:
                    result["all"].append(tag)

        return result

    # 别名（向后兼容）
    _parse_tags = _parse_post_page


# =========================================================================
# 状态管理
# =========================================================================

def load_state(state_file: Path) -> dict:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "posted_ids": [],
        "self_feed_ids": [],
        "self_post_timestamps": [],
        "last_self_post_ts": 0,
    }


def save_state(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    # 保留最近 1000 条 posted_ids
    if len(state.get("posted_ids", [])) > 1000:
        state["posted_ids"] = state["posted_ids"][-1000:]
    # 保留最近 200 条 self_feed_ids
    if len(state.get("self_feed_ids", [])) > 200:
        state["self_feed_ids"] = state["self_feed_ids"][-200:]
    state_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# =========================================================================
# 腾讯频道 CLI 调用
# =========================================================================

def get_cli_path(hermes_home: Path) -> str:
    return str(hermes_home / "bin" / "tencent-channel-cli")


def check_cold_and_get_last_ts(cfg: Config) -> tuple[bool, datetime]:
    """
    检查频道冷度（近 COLD_THRESHOLD_MIN 分钟是否无真人新帖）。
    返回 (是否冷, None) 或 (是否冷, 最后真人帖时间)
    """
    cli = get_cli_path(cfg.hermes_home)

    # 获取近 N 分钟的所有帖子
    cutoff_ts = int((cst_now() - cfg.cold_threshold).timestamp())
    now_ts = int(cst_now().timestamp())

    try:
        result = subprocess.run(
            [cli, "feed", "get-guild-feeds",
             "--guild-id", cfg.guild_id,
             "--limit", "30"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HERMES_HOME": str(cfg.hermes_home)}
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        LOG.warning("获取频道feeds失败: %s", exc)
        return True, None

    if result.returncode != 0:
        LOG.warning("get-guild-feeds 返回非零: %s\nstdout: %s\nstderr: %s",
                    result.returncode, result.stdout[:500], result.stderr[:500])
        return True, None

    try:
        feeds = json.loads(result.stdout)
    except json.JSONDecodeError:
        LOG.warning("get-guild-feeds 解析 JSON 失败: %s", result.stdout[:300])
        return True, None

    # 过滤出 COLD_THRESHOLD 分钟内且非脚本自发帖的真人帖
    state = load_state(cfg.state_file)
    self_feed_ids = set(state.get("self_feed_ids", []))

    human_posts: list[dict] = []
    for feed in feeds if isinstance(feeds, list) else []:
        fid = feed.get("feed_id", "")
        ts = feed.get("create_time", 0)
        if ts < cutoff_ts:
            continue
        if fid in self_feed_ids:
            continue
        human_posts.append(feed)

    is_cold = len(human_posts) == 0
    last_ts = human_posts[0]["create_time"] if human_posts else None
    return is_cold, last_ts


def can_self_post(cfg: Config) -> bool:
    """检查距离上次自发帖是否已超过最小间隔"""
    state = load_state(cfg.state_file)
    last_ts = state.get("last_self_post_ts", 0)
    if not last_ts:
        return True
    elapsed = cst_now().timestamp() - last_ts
    return elapsed >= cfg.min_self_interval.total_seconds()


def build_caption(tags: dict[str, list[str]], translator: Translator, resolution: str) -> str:
    """构建帖子的文字 caption"""
    # 优先用角色标签 + 参考标签
    display_tags = tags.get("character", []) + tags.get("reference", [])
    if not display_tags:
        display_tags = tags.get("all", [])

    if not display_tags:
        tag_str = ""
    else:
        tag_str = translator.translate(display_tags[:8])

    lines = []
    if tag_str:
        lines.append(f"🏷️ {tag_str}")
    if resolution:
        lines.append(f"📐 分辨率: {resolution}")
    lines.append("\n#anime-pictures")
    return "\n".join(lines)


def publish_post(cfg: Config, image_paths: list[Path], caption: str) -> Optional[str]:
    """
    调用 tencent-channel-cli 发帖。
    返回 feed_id 或 None（失败）。
    """
    cli = get_cli_path(cfg.hermes_home)

    # 构造 stdin JSON（file_paths 格式）
    payload = {
        "guild_id": cfg.guild_id,
        "channel_id": cfg.channel_id,
        "content": caption,
        "file_paths": [{"file_path": str(p)} for p in image_paths],
    }

    try:
        result = subprocess.run(
            [cli, "feed", "publish-feed"],
            input=json.dumps(payload),
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "HERMES_HOME": str(cfg.hermes_home)}
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        LOG.error("publish-feed 执行失败: %s", exc)
        return None

    # 某些版本的 CLI 成功时也返回 exit=1，所以检查 stdout
    if result.returncode not in (0, 1):
        LOG.error("publish-feed 返回 %d: stdout=%s stderr=%s",
                  result.returncode, result.stdout[:300], result.stderr[:300])
        return None

    try:
        resp = json.loads(result.stdout)
        feed_id = resp.get("feed_id") or (resp.get("data", {}) or {}).get("feed_id")
        if feed_id:
            return str(feed_id)
    except json.JSONDecodeError:
        pass

    # 尝试从 stderr 解析
    if result.stderr:
        try:
            resp = json.loads(result.stderr)
            feed_id = resp.get("feed_id") or (resp.get("data", {}) or {}).get("feed_id")
            if feed_id:
                return str(feed_id)
        except json.JSONDecodeError:
            pass

    LOG.warning("publish-feed 未返回 feed_id，stdout=%s stderr=%s",
                result.stdout[:300], result.stderr[:300])
    return None


# =========================================================================
# 图片下载
# =========================================================================

class ImageDownloader:
    """
    下载 anime-pictures.net 图片。

    下载策略（按优先级）：
      1. 直接 HTTP 下载完整图片（api.anime-pictures.net）
         —— 若服务器 DNS/防火墙不通，回退到方案 2
      2. 用 browser_tool（agent 上下文中）通过浏览器下载完整图片
         —— 需要 browser_navigate + browser_console JavaScript 提取 base64
      3. 降级：用 preview_avif（avif 格式，可直接下载，质量尚可）

    返回 (图片 bytes, 来源描述, 原始格式)
    """

    def __init__(self, http: HttpClient, cfg: Config) -> None:
        self.http = http
        self.cfg = cfg

    def download(self, post_id: int, md5: str = "", ext: str = ".png",
                 file_url: str = "") -> tuple[Optional[bytes], str, str]:
        """
        尝试下载图片。

        1. 先尝试直接 HTTP 下载（api.anime-pictures.net）
        2. 回退到 preview_avif

        返回 (bytes, 来源描述, 格式扩展名)
        """
        # 策略1：直接 HTTP 下载完整图片
        # 格式: https://api.anime-pictures.net/pictures/download_image/{id}-{w}x{h}-{slug}.{ext}
        #        = https://api.anime-pictures.net/pictures/get_image/{id}-{w}x{h}-{slug}.{ext}
        if file_url:
            for endpoint in [
                f"https://api.anime-pictures.net/pictures/download_image/{file_url}",
                f"https://api.anime-pictures.net/pictures/get_image/{file_url}",
            ]:
                try:
                    req = urllib.request.Request(endpoint, headers={
                        "User-Agent": self.http.user_agent,
                        "Referer": "https://anime-pictures.net/",
                        "Origin": "https://anime-pictures.net",
                    })
                    with urllib.request.urlopen(req, timeout=self.cfg.http_timeout) as resp:
                        data = resp.read()
                    if len(data) > 50 * 1024:  # 大于 50KB 才是完整图片
                        return data, f"api:{endpoint.split('/')[-2]}", ext
                except Exception as exc:
                    LOG.debug("直接下载失败 %s: %s", endpoint[:60], exc)

        # 策略2：降级到 preview avif（opreviews CDN 可直接访问）
        if md5 and len(md5) == 32:
            # avif 预览图路径: {2chars}/{full_md5}_bp.avif
            prefix = md5[:3]  # 前3个字符
            for variant in ["_bp", "_cp"]:
                avif_url = f"https://opreviews.anime-pictures.net/{prefix}/{md5}{variant}.avif"
                try:
                    req = urllib.request.Request(avif_url, headers={
                        "User-Agent": self.http.user_agent,
                        "Referer": "https://anime-pictures.net/",
                    })
                    with urllib.request.urlopen(req, timeout=self.cfg.http_timeout) as resp:
                        data = resp.read()
                    if len(data) > 5 * 1024:  # 大于 5KB
                        return data, f"preview:{variant}", ".avif"
                except Exception as exc:
                    LOG.debug("预览图下载失败 %s: %s", avif_url[:60], exc)

        return None, "", ""


# =========================================================================
# 浏览器下载（agent 上下文专用）
# =========================================================================

BROWSER_DOWNLOAD_JS = """
// 在 anime-pictures 帖子页执行，通过 fetch + cors 下载完整图片并转为 base64
async function() {
    const postId = {post_id};
    // 找到下载链接
    const downloadLink = document.querySelector('a[href*="api.anime-pictures.net/pictures/download_image"]');
    if (!downloadLink) {{
        return JSON.stringify({{ error: "no_download_link_found" }});
    }}
    const url = downloadLink.href;
    try {{
        const resp = await fetch(url, {{ mode: 'cors', credentials: 'include' }});
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const blob = await resp.blob();
        return new Promise((resolve, reject) => {{
            const reader = new FileReader();
            reader.onloadend = () => resolve({{ base64: reader.result, size: blob.size }});
            reader.onerror = reject;
            reader.readAsDataURL(blob);
        }});
    }} catch(e) {{
        return JSON.stringify({{ error: e.message }});
    }}
}
"""


def download_image_via_browser(post_id: int, browser_execute_code_fn) -> Optional[bytes]:
    """
    通过 browser_tool 的 execute_code 下载完整图片。
    仅在 agent 上下文中可用（需要传入 browser_execute_code 函数）。
    """
    script = BROWSER_DOWNLOAD_JS.format(post_id=post_id)
    try:
        result = browser_execute_code_fn(code=script)
        if not result:
            return None
        import json as _json
        try:
            parsed = _json.loads(result)
            if "error" in parsed:
                LOG.warning("browser 下载失败 post_id=%d: %s", post_id, parsed["error"])
                return None
            # result is {"base64": "data:image/...;base64,..."}
            base64_data = parsed.get("base64", "")
        except (json.JSONDecodeError, TypeError):
            # result is already the raw base64 string
            base64_data = result

        if "," not in str(base64_data):
            LOG.warning("browser 返回格式异常 post_id=%d: %s", post_id, str(base64_data)[:100])
            return None

        import base64 as _b64
        b64_str = str(base64_data).split(",", 1)[1]
        return _b64.b64decode(b64_str)
    except Exception as exc:
        LOG.warning("browser 下载异常 post_id=%d: %s", post_id, exc)
        return None


# =========================================================================
# 主流程
# =========================================================================

def run(args: argparse.Namespace) -> int:
    # 设置日志
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    cfg = Config.load(args)
    LOG.info("anime-pictures.net 壁纸机器人启动")
    LOG.info("  搜索关键词: %s", cfg.search_tag)
    LOG.info("  目标频道: guild=%s channel=%s", cfg.guild_id, cfg.channel_id)
    LOG.info("  下载目录: %s", cfg.wallpaper_dir)

    http = HttpClient(cfg.user_agent, cfg.http_timeout)
    translator = Translator(http, cfg)
    parser = AnimePicturesParser(http)
    state = load_state(cfg.state_file)

    # ── 冷度检测 ──────────────────────────────────────────────────────────
    if not args.force:
        is_cold, _ = check_cold_and_get_last_ts(cfg)
        if not is_cold:
            LOG.info("频道不冷，跳过发帖")
            return 0
        LOG.info("频道寒冷，继续检查发帖条件")

        if not can_self_post(cfg):
            LOG.info("距上次发帖未超过 %.0f 分钟，跳过", cfg.min_self_interval.total_seconds() / 60)
            return 0

    # ── 搜索壁纸 ──────────────────────────────────────────────────────────
    keywords = [k.strip() for k in cfg.search_tag.split(",") if k.strip()]
    all_posts: list[dict] = []
    seen_ids: set[int] = set()

    for kw in keywords:
        for page in range(2):  # 最多 2 页
            try:
                posts = parser.search(kw, page=page)
            except Exception as exc:
                LOG.warning("搜索 %s 第 %d 页失败: %s", kw, page, exc)
                continue
            for p in posts:
                if p["post_id"] not in seen_ids and p["post_id"] not in state.get("posted_ids", []):
                    seen_ids.add(p["post_id"])
                    all_posts.append(p)
            if len(all_posts) >= cfg.download_count * 3:
                break
        if len(all_posts) >= cfg.download_count * 3:
            break

    if len(all_posts) < cfg.download_count:
        LOG.warning("搜索结果不足（需要 %d，仅获得 %d），跳过", cfg.download_count, len(all_posts))
        return 0

    # 随机选择候选
    selected = random.sample(all_posts, min(cfg.download_count, len(all_posts)))
    LOG.info("候选壁纸数: %d", len(selected))

    # ── 获取标签并下载 ────────────────────────────────────────────────────
    img_downloader = ImageDownloader(http, cfg)
    downloaded: list[tuple[Path, dict]] = []  # (图片路径, 标签字典)

    for post in selected:
        post_id = post["post_id"]
        resolution = post["resolution"]

        # 获取标签（从缓存或帖子页）
        try:
            tags = parser.get_post_tags(post_id)
        except Exception as exc:
            LOG.warning("获取 post_id=%d 标签失败: %s", post_id, exc)
            tags = {"all": [], "character": [], "reference": [], "copyright": [],
                    "artist": [], "meta": [], "file_url": "", "md5": "", "ext": ".jpg"}

        # 用 ImageDownloader 尝试下载（直接 HTTP → 降级预览图）
        img_bytes, src_desc, fmt = img_downloader.download(
            post_id,
            md5=tags.get("md5", ""),
            ext=tags.get("ext", ".jpg"),
            file_url=tags.get("file_url", ""),
        )

        if not img_bytes:
            LOG.warning("post_id=%d 所有下载策略均失败，跳过", post_id)
            continue

        LOG.info("post_id=%d 下载成功: %s (%.1fKB)",
                 post_id, src_desc, len(img_bytes) / 1024)

        # 保存图片
        img_path = cfg.wallpaper_dir / f"anime_pictures_{post_id}.jpg"
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(img_bytes)
        downloaded.append((img_path, tags))
        LOG.info("post_id=%d 已保存: %s", post_id, img_path.name)

    if len(downloaded) < cfg.download_count:
        LOG.warning("实际下载成功 %d 张，未达目标 %d，跳过",
                    len(downloaded), cfg.download_count)
        return 0

    # ── 构造 caption 并发帖 ───────────────────────────────────────────────
    # 取第一张的标签作为帖子的主要标签
    main_tags = downloaded[0][1]
    first_res = selected[0].get("resolution", "")
    caption = build_caption(main_tags, translator, first_res)

    image_paths = [p for p, _ in downloaded]

    LOG.info("准备发帖，图片数=%d", len(image_paths))
    if args.dry_run:
        LOG.info("[DRY-RUN] 跳过实际发帖")
        for p in image_paths:
            LOG.info("[DRY-RUN] 图片: %s", p)
        LOG.info("[DRY-RUN] caption: %s", caption[:100])
        return 0

    feed_id = publish_post(cfg, image_paths, caption)

    if not feed_id:
        LOG.error("发帖失败")
        return 1

    # ── 更新状态 ─────────────────────────────────────────────────────────
    new_state = load_state(cfg.state_file)
    for post in selected[:cfg.download_count]:
        pid = str(post["post_id"])
        if pid not in new_state.get("posted_ids", []):
            new_state.setdefault("posted_ids", []).append(pid)
    new_state.setdefault("self_feed_ids", []).append(feed_id)
    new_state["last_self_post_ts"] = int(cst_now().timestamp())
    save_state(cfg.state_file, new_state)

    LOG.info("发帖成功: feed_id=%s", feed_id)

    # ── 清理图片 ──────────────────────────────────────────────────────────
    for p in image_paths:
        try:
            p.unlink()
        except OSError:
            pass

    return 0


# =========================================================================
# CLI 入口
# =========================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="anime-pictures.net 壁纸机器人")
    parser.add_argument("--search-tag", default=os.environ.get("SEARCH_TAG", ""),
                        help="搜索关键词（支持逗号分隔多关键词）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="跳过冷度检测，强制发帖")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("HERMES_HOME", DEFAULTS["HERMES_HOME"])
    sys.exit(run(args))
