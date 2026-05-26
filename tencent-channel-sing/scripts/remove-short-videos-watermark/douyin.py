#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Download a Douyin shared video without the visible watermark when available.

Usage:
    python douyin.py "复制来的整段抖音分享文案"
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Referer": "https://www.douyin.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

MOBILE_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Referer": "https://www.iesdouyin.com/",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
        "Mobile/15E148 Safari/604.1 Edg/122.0.0.0"
    ),
}


class DouyinDownloadError(RuntimeError):
    """Raised when a Douyin share cannot be resolved or downloaded."""


def extract_url(text: str) -> str:
    """Extract the first Douyin URL from a share sentence."""
    urls = re.findall(r"https?://[^\s，。！？!]+", text)
    for url in urls:
        if "douyin.com" in url:
            return url.rstrip(".,;:!?，。；：！？)")
    raise DouyinDownloadError("没有在输入内容里找到 douyin.com 链接。")


def resolve_share_url(session: requests.Session, share_url: str) -> str:
    """Follow a short Douyin share URL and return the final page URL."""
    response = session.get(
        share_url,
        headers=HEADERS,
        allow_redirects=True,
        timeout=20,
        verify=False,
    )
    response.raise_for_status()
    return response.url


def extract_aweme_id(final_url: str, html: str = "") -> str:
    """Extract aweme/video id from the redirected URL or page HTML."""
    patterns = [
        r"/video/(\d+)",
        r"modal_id=(\d+)",
        r"aweme_id=(\d+)",
        r'"aweme_id"\s*:\s*"(\d+)"',
        r'"itemId"\s*:\s*"(\d+)"',
    ]
    search_space = f"{final_url}\n{html}"
    for pattern in patterns:
        match = re.search(pattern, search_space)
        if match:
            return match.group(1)
    raise DouyinDownloadError(f"没有从跳转地址中解析到视频 ID：{final_url}")


def fetch_aweme_detail(session: requests.Session, aweme_id: str) -> dict[str, Any]:
    """Fetch video metadata through current and legacy Douyin endpoints."""
    endpoints = [
        (
            "https://www.douyin.com/aweme/v1/web/aweme/detail/"
            f"?aweme_id={aweme_id}&aid=1128&device_platform=webapp"
        ),
        (
            "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/"
            f"?item_ids={aweme_id}"
        ),
    ]

    errors: list[str] = []
    for endpoint in endpoints:
        try:
            response = session.get(endpoint, headers=HEADERS, timeout=20, verify=False)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            errors.append(f"{endpoint}: {exc}")
            continue

        if isinstance(data.get("aweme_detail"), dict):
            return data["aweme_detail"]
        item_list = data.get("item_list")
        if isinstance(item_list, list) and item_list:
            return item_list[0]
        errors.append(f"{endpoint}: 响应里没有视频信息")

    raise DouyinDownloadError("视频信息接口解析失败。\n" + "\n".join(errors))


def fetch_aweme_detail_from_share_page(
    session: requests.Session,
    aweme_id: str,
) -> dict[str, Any]:
    """Fetch metadata from the public iesdouyin share page without cookies."""
    url = f"https://www.iesdouyin.com/share/video/{aweme_id}"
    response = session.get(
        url,
        headers=MOBILE_HEADERS,
        allow_redirects=True,
        timeout=20,
        verify=False,
    )
    response.raise_for_status()

    match = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", response.text, re.S)
    if not match:
        raise DouyinDownloadError("分享页里没有找到 window._ROUTER_DATA 数据。")

    try:
        router_data = json.loads(match.group(1).strip())
        item = router_data["loaderData"]["video_(id)/page"]["videoInfoRes"]["item_list"][0]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise DouyinDownloadError(f"分享页数据结构解析失败：{exc}") from exc

    if not isinstance(item, dict):
        raise DouyinDownloadError("分享页视频信息不是预期的 JSON 对象。")
    return item


def pick_play_url(aweme: dict[str, Any]) -> str:
    """Return the best no-watermark-ish play URL exposed by Douyin metadata."""
    video = aweme.get("video") or {}
    candidates: list[str] = []

    for key in ("play_addr", "download_addr"):
        addr = video.get(key) or {}
        candidates.extend(addr.get("url_list") or [])

    for bit_rate in video.get("bit_rate") or []:
        addr = bit_rate.get("play_addr") or {}
        candidates.extend(addr.get("url_list") or [])

    cleaned: list[str] = []
    for url in candidates:
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        cleaned.append(url.replace("playwm", "play"))

    if not cleaned:
        raise DouyinDownloadError("没有在视频信息里找到可下载播放地址。")
    return cleaned[0]


def sanitize_filename(name: str, fallback: str) -> str:
    """Make a Windows-safe filename from the video title."""
    name = name.strip() or fallback
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = fallback
    return name[:60]


def download_video(
    session: requests.Session,
    url: str,
    output: Path,
    headers: dict[str, str] | None = None,
) -> Path:
    """Download video bytes to output path."""
    request_headers = headers or HEADERS
    with session.get(
        url,
        headers=request_headers,
        timeout=60,
        stream=True,
        verify=False,
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "text/html" in content_type or "application/json" in content_type:
            raise DouyinDownloadError(f"下载地址没有返回视频内容，Content-Type={content_type}")

        with output.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 512):
                if chunk:
                    file.write(chunk)
    return output


def run_native(share_text: str, output_dir: Path) -> Path:
    """Resolve a Douyin share sentence and download the video."""
    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    share_url = extract_url(share_text)
    final_url = resolve_share_url(session, share_url)

    page = session.get(final_url, headers=HEADERS, timeout=20, verify=False)
    html = page.text if page.ok else ""
    aweme_id = extract_aweme_id(final_url, html)
    try:
        aweme = fetch_aweme_detail_from_share_page(session, aweme_id)
    except Exception:
        aweme = fetch_aweme_detail(session, aweme_id)

    desc = str(aweme.get("desc") or "")
    filename = sanitize_filename(desc, f"douyin_{aweme_id}") + ".mp4"
    output = output_dir / filename
    play_url = pick_play_url(aweme)
    return download_video(session, play_url, output, MOBILE_HEADERS)


def run_ytdlp(
    share_text: str,
    output_dir: Path,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> Path:
    """Download through yt-dlp, which handles more Douyin signing changes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    share_url = extract_url(share_text)
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        share_url,
        "--no-playlist",
        "--merge-output-format",
        "mp4",
        "-o",
        str(output_dir / "%(title).90s.%(ext)s"),
    ]
    if cookies:
        command.extend(["--cookies", cookies])
    if cookies_from_browser:
        command.extend(["--cookies-from-browser", cookies_from_browser])

    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise DouyinDownloadError("yt-dlp 下载失败。\n" + detail)

    downloaded = sorted(output_dir.glob("*.mp4"), key=lambda path: path.stat().st_mtime)
    if not downloaded:
        raise DouyinDownloadError("yt-dlp 执行成功，但没有在保存目录找到 mp4 文件。")
    return downloaded[-1]


def run(
    share_text: str,
    output_dir: Path,
    backend: str,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> Path:
    """Download with native parsing first, then yt-dlp fallback when requested."""
    if backend == "native":
        return run_native(share_text, output_dir)
    if backend == "yt-dlp":
        return run_ytdlp(share_text, output_dir, cookies, cookies_from_browser)

    native_error: Exception | None = None
    try:
        return run_native(share_text, output_dir)
    except Exception as exc:
        native_error = exc

    try:
        return run_ytdlp(share_text, output_dir, cookies, cookies_from_browser)
    except Exception as exc:
        raise DouyinDownloadError(
            "内置解析和 yt-dlp 都失败了。\n"
            f"\n[内置解析]\n{native_error}"
            f"\n\n[yt-dlp]\n{exc}"
        ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载抖音分享链接里的无水印视频。")
    parser.add_argument("share_text", nargs="*", help="整段抖音分享文案，或单独的分享链接。")
    parser.add_argument(
        "-o",
        "--output-dir",
        default="downloads",
        help="保存目录，默认：downloads",
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "native", "yt-dlp"),
        default="auto",
        help="下载后端，默认 auto：先内置解析，失败后尝试 yt-dlp。",
    )
    parser.add_argument(
        "--cookies",
        help="Netscape cookies.txt 路径。Douyin 提示需要 fresh cookies 时使用。",
    )
    parser.add_argument(
        "--cookies-from-browser",
        help="让 yt-dlp 从浏览器读取 cookie，例如 chrome、edge、firefox。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    share_text = " ".join(args.share_text).strip()
    if not share_text:
        share_text = input("请粘贴抖音分享文案或链接：").strip()

    try:
        output = run(
            share_text,
            Path(args.output_dir),
            backend=args.backend,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
        )
    except Exception as exc:
        print(f"下载失败：{exc}", file=sys.stderr)
        return 1

    print(f"下载完成：{output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
