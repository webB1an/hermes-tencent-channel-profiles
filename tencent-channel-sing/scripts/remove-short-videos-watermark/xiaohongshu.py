#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Download Xiaohongshu shared videos or images from public page data.

Usage:
    python xiaohongshu.py "整段小红书分享文案"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,"
        "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "Connection": "keep-alive",
    "Referer": "https://www.xiaohongshu.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
    ),
}

BACKUP_HEADERS = {
    **HEADERS,
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36 EdgA/143.0.0.0"
    ),
}


class XiaohongshuDownloadError(RuntimeError):
    """Raised when a Xiaohongshu share cannot be parsed or downloaded."""


def extract_url(text: str) -> str:
    """Extract the first Xiaohongshu URL from a share sentence."""
    urls = re.findall(r"https?://[^\s，。！？!]+", text)
    for url in urls:
        if "xiaohongshu.com" in url or "xhslink.com" in url or "xhs.com" in url:
            return url.rstrip(".,;:!?，。；：！？)")
    raise XiaohongshuDownloadError("没有在输入内容里找到小红书链接。")


def resolve_url(session: requests.Session, url: str) -> str:
    """Resolve short links and normalize xhs.com to xhslink.com."""
    url = url.replace("xhs.com", "xhslink.com")
    parsed = urlparse(url)
    if parsed.netloc == "www.xiaohongshu.com":
        return url

    response = session.get(url, headers=HEADERS, allow_redirects=True, timeout=20, verify=False)
    response.raise_for_status()
    return response.url


def extract_note_id(url: str) -> str:
    """Extract note id from a Xiaohongshu URL."""
    patterns = [
        r"/discovery/item/([a-zA-Z0-9]+)",
        r"/explore/([a-zA-Z0-9]+)",
        r"/item/([a-zA-Z0-9]+)",
        r"/note/([a-zA-Z0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise XiaohongshuDownloadError(f"无法从链接中提取笔记 ID：{url}")


def fetch_html(session: requests.Session, url: str) -> str:
    """Fetch note HTML, retrying once with a mobile user agent."""
    for headers in (HEADERS, BACKUP_HEADERS):
        response = session.get(url, headers=headers, timeout=20, verify=False)
        response.raise_for_status()
        if "window.__INITIAL_STATE__" in response.text:
            return response.text
    raise XiaohongshuDownloadError("页面里没有找到 window.__INITIAL_STATE__ 数据。")


def extract_note(html: str, note_id: str) -> dict[str, Any]:
    """Parse the note JSON from window.__INITIAL_STATE__."""
    match = re.search(
        r"<script>\s*window\.__INITIAL_STATE__\s*=\s*({[\s\S]*?})</script>",
        html,
        re.I,
    )
    if not match:
        raise XiaohongshuDownloadError("没有匹配到 window.__INITIAL_STATE__ 脚本。")

    json_text = match.group(1).replace("undefined", "null")
    try:
        state = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise XiaohongshuDownloadError(f"页面 JSON 解析失败：{exc}") from exc

    note = (
        state.get("note", {})
        .get("noteDetailMap", {})
        .get(note_id, {})
        .get("note")
    )
    if not note:
        note = state.get("noteData", {}).get("data", {}).get("noteData")
    if not isinstance(note, dict):
        raise XiaohongshuDownloadError("页面状态里没有找到笔记详情。")
    return note


def sanitize_filename(name: str, fallback: str) -> str:
    """Make a Windows-safe filename."""
    name = (name or "").strip() or fallback
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name or fallback)[:90]


def process_image_url(url: str) -> str:
    """Convert common Xiaohongshu image URLs to cleaner original-like URLs."""
    if not url:
        return ""

    match = re.search(r"/oss-sg/([a-zA-Z0-9_]+)/([a-zA-Z0-9]+)!", url)
    if match and not re.fullmatch(r"[a-f0-9]{32}|\d+", match.group(1)):
        return (
            f"https://sns-img-hw.xhscdn.com/oss-sg/{match.group(1)}/{match.group(2)}"
            "?imageView2/2/w/0/format/jpg"
        )

    match = re.search(r"/([a-zA-Z0-9_]+)/([a-zA-Z0-9]+)!", url)
    if match and not re.fullmatch(r"[a-f0-9]{32}|\d+", match.group(1)):
        return (
            f"https://sns-img-hw.xhscdn.com/{match.group(1)}/{match.group(2)}"
            "?imageView2/2/w/0/format/jpg"
        )

    match = re.search(r"(notes_pre_post|spectrum|notes_uhdr)/([a-zA-Z0-9]+)", url)
    if match:
        return (
            f"https://sns-img-hw.xhscdn.com/{match.group(1)}/{match.group(2)}"
            "?imageView2/2/w/0/format/jpg"
        )
    return url


def pick_video_url(note: dict[str, Any]) -> str | None:
    """Pick the best available no-watermark video stream."""
    streams: list[dict[str, Any]] = []
    stream_data = ((note.get("video") or {}).get("media") or {}).get("stream") or {}

    for codec in ("h265", "h264", "h266", "av1"):
        for stream in stream_data.get(codec) or []:
            if isinstance(stream, dict) and stream.get("masterUrl"):
                item = dict(stream)
                item["_codec"] = codec
                streams.append(item)

    codec_rank = {"h265": 0, "h264": 1, "h266": 2, "av1": 3}
    streams.sort(
        key=lambda item: (
            codec_rank.get(str(item.get("_codec")), 9),
            -int(item.get("avgBitrate") or item.get("videoBitrate") or 0),
        )
    )
    if streams:
        return str(streams[0]["masterUrl"])

    origin_key = ((note.get("video") or {}).get("consumer") or {}).get("originVideoKey")
    if origin_key:
        return f"http://sns-video-bd.xhscdn.com/{origin_key}"
    return None


def collect_images(note: dict[str, Any]) -> list[str]:
    """Collect image URLs from imageList."""
    images: list[str] = []
    for image in note.get("imageList") or []:
        if not isinstance(image, dict):
            continue
        url = image.get("url") or image.get("urlDefault") or image.get("urlPre") or ""
        processed = process_image_url(str(url))
        if processed:
            images.append(processed)
    return images


def download_file(session: requests.Session, url: str, output: Path) -> Path:
    """Download one file to disk."""
    with session.get(url, headers=HEADERS, timeout=60, stream=True, verify=False) as response:
        response.raise_for_status()
        with output.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 512):
                if chunk:
                    file.write(chunk)
    return output


def run(share_text: str, output_dir: Path) -> list[Path]:
    """Parse and download Xiaohongshu media."""
    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    url = resolve_url(session, extract_url(share_text))
    note_id = extract_note_id(url)
    note = extract_note(fetch_html(session, url), note_id)

    title = str(note.get("title") or note.get("desc") or "")
    base_name = sanitize_filename(title, f"xiaohongshu_{note_id}")

    video_url = pick_video_url(note)
    if video_url:
        return [download_file(session, video_url, output_dir / f"{base_name}.mp4")]

    image_urls = collect_images(note)
    if not image_urls:
        raise XiaohongshuDownloadError("没有在笔记数据中找到视频或图片地址。")

    outputs: list[Path] = []
    for index, image_url in enumerate(image_urls, start=1):
        outputs.append(download_file(session, image_url, output_dir / f"{base_name}_{index:02d}.jpg"))
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载小红书分享链接里的无水印视频或图片。")
    parser.add_argument("share_text", nargs="*", help="整段小红书分享文案，或单独的分享链接。")
    parser.add_argument(
        "-o",
        "--output-dir",
        default="downloads",
        help="保存目录，默认：downloads",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    share_text = " ".join(args.share_text).strip()
    if not share_text:
        share_text = input("请粘贴小红书分享文案或链接：").strip()

    try:
        outputs = run(share_text, Path(args.output_dir))
    except Exception as exc:
        print(f"下载失败：{exc}", file=sys.stderr)
        return 1

    print("下载完成：")
    for output in outputs:
        print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
