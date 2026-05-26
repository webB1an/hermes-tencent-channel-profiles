#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Download Kuaishou shared videos or images from public page data.

Usage:
    python kuaishou.py "整段快手分享文案"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import requests
import urllib3


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Referer": "https://v.kuaishou.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
        "Mobile/15E148 Safari/604.1 Edg/122.0.0.0"
    ),
}


class KuaishouDownloadError(RuntimeError):
    """Raised when a Kuaishou share cannot be parsed or downloaded."""


def extract_url(text: str) -> str:
    """Extract the first Kuaishou URL from a share sentence."""
    urls = re.findall(r"https?://[^\s，。！？!]+", text)
    for url in urls:
        if "kuaishou.com" in url or "chenzhongtech.com" in url:
            return url.rstrip(".,;:!?，。；：！？)")
    raise KuaishouDownloadError("没有在输入内容里找到快手链接。")


def resolve_url(session: requests.Session, url: str) -> str:
    """Resolve Kuaishou short links."""
    response = session.get(url, headers=HEADERS, allow_redirects=True, timeout=20, verify=False)
    response.raise_for_status()
    return response.url


def fetch_html(session: requests.Session, url: str) -> str:
    """Fetch Kuaishou page HTML."""
    response = session.get(url, headers=HEADERS, timeout=20, verify=False)
    response.raise_for_status()
    return response.text


def extract_content_id_and_type(url: str) -> tuple[str, str]:
    """Extract content type and id from final Kuaishou URL."""
    patterns = {
        "short-video": r"/short-video/([^?/#]+)",
        "long-video": r"/long-video/([^?/#]+)",
        "photo": r"/photo/([^?/#]+)",
    }
    for content_type, pattern in patterns.items():
        match = re.search(pattern, url)
        if match:
            return content_type, match.group(1)
    return "", ""


def parse_init_state(html: str) -> dict[str, Any] | None:
    """Parse window.INIT_STATE and return the first media result."""
    match = re.search(r"window\.INIT_STATE\s*=\s*(.*?)</script>", html, re.S)
    if not match:
        return None

    json_text = match.group(1).strip().rstrip(";")
    try:
        state = json.loads(json_text)
    except json.JSONDecodeError:
        cleaned = json_text.replace(
            '"{"err_msg":"launchApplication:fail"}"',
            '"err_msg","launchApplication:fail"',
        ).replace(
            '"{"err_msg":"system:access_denied"}"',
            '"err_msg","system:access_denied"',
        )
        state = json.loads(cleaned)

    media_items = [
        value
        for key, value in state.items()
        if key.startswith("tusjoh")
        and isinstance(value, dict)
        and ("fid" in value or "photo" in value)
    ]
    if not media_items:
        return None

    photo = media_items[0].get("photo") or {}
    if not isinstance(photo, dict):
        return None
    return format_photo(photo)


def parse_apollo_state(html: str, content_id: str, content_type: str) -> dict[str, Any] | None:
    """Parse window.__APOLLO_STATE__ as a fallback."""
    match = re.search(r"window\.__APOLLO_STATE__\s*=\s*(.*?)</script>", html, re.S)
    if not match:
        return None

    json_text = re.sub(r"function\s*\([^)]*\)\s*{[^}]*}", ":", match.group(1))
    json_text = re.sub(r",\s*(?=}|])", "", json_text).replace(";(:());", "")
    try:
        apollo = json.loads(json_text)
    except json.JSONDecodeError:
        return None

    client = apollo.get("defaultClient") or {}
    video_data = client.get(f"VisionVideoDetailPhoto:{content_id}") or {}
    if not video_data:
        return None

    author = next(
        (
            value
            for key, value in client.items()
            if key.startswith("VisionVideoDetailAuthor:") and isinstance(value, dict)
        ),
        {},
    )
    if content_type == "long-video":
        url = (
            video_data.get("manifestH265", {})
            .get("json", {})
            .get("adaptationSet", [{}])[0]
            .get("representation", [{}])[0]
            .get("backupUrl", [""])[0]
        )
    else:
        url = video_data.get("photoUrl") or ""
    if not url:
        return None

    return {
        "type": "image" if content_type == "photo" else "video",
        "title": video_data.get("caption") or "",
        "author": author.get("name") or "",
        "cover": video_data.get("coverUrl") or "",
        "url": url,
        "images": [url] if content_type == "photo" else [],
    }


def format_photo(photo: dict[str, Any]) -> dict[str, Any] | None:
    """Format photo data from INIT_STATE."""
    atlas = ((photo.get("ext_params") or {}).get("atlas") or {})
    image_list = atlas.get("list") or []
    if image_list:
        return {
            "type": "image",
            "title": photo.get("caption") or "",
            "author": photo.get("userName") or "",
            "cover": "",
            "url": "",
            "images": [f"http://tx2.a.yximgs.com/{path}" for path in image_list],
        }

    cover_urls = photo.get("coverUrls") or []
    is_single_picture = photo.get("photoType") == "SINGLE_PICTURE" or photo.get("singlePicture")
    if is_single_picture and cover_urls:
        image_url = cover_urls[0].get("url") or ""
        if image_url:
            return {
                "type": "image",
                "title": photo.get("caption") or "",
                "author": photo.get("userName") or "",
                "cover": image_url,
                "url": image_url,
                "images": [image_url],
            }

    video_url = ""
    main_mv_urls = photo.get("mainMvUrls") or []
    if main_mv_urls:
        video_url = main_mv_urls[0].get("url") or ""
    if not video_url:
        video_url = (
            (photo.get("manifest") or {})
            .get("adaptationSet", [{}])[0]
            .get("representation", [{}])[0]
            .get("url")
            or ""
        )
    if video_url:
        return {
            "type": "video",
            "title": photo.get("caption") or "",
            "author": photo.get("userName") or "",
            "cover": (cover_urls[0].get("url") if cover_urls else "") or "",
            "url": video_url,
            "images": [],
        }
    return None


def sanitize_filename(name: str, fallback: str) -> str:
    """Make a Windows-safe filename."""
    name = (name or "").strip() or fallback
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name or fallback)[:90]


def download_file(session: requests.Session, url: str, output: Path) -> Path:
    """Download one file to disk."""
    with session.get(url, headers=HEADERS, timeout=60, stream=True, verify=False) as response:
        response.raise_for_status()
        with output.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 512):
                if chunk:
                    file.write(chunk)
    return output


def parse_media(session: requests.Session, share_text: str) -> tuple[dict[str, Any], str]:
    """Resolve, fetch, and parse Kuaishou media."""
    final_url = resolve_url(session, extract_url(share_text))
    html = fetch_html(session, final_url)
    content_type, content_id = extract_content_id_and_type(final_url)
    media = parse_init_state(html)
    if not media and content_id:
        media = parse_apollo_state(html, content_id, content_type)
    if not media:
        raise KuaishouDownloadError("没有在页面状态中找到有效媒体信息。")
    return media, content_id or "kuaishou"


def run(share_text: str, output_dir: Path) -> list[Path]:
    """Download Kuaishou media."""
    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    media, content_id = parse_media(session, share_text)

    base_name = sanitize_filename(str(media.get("title") or ""), f"kuaishou_{content_id}")
    if media.get("type") == "video" and media.get("url"):
        return [download_file(session, str(media["url"]), output_dir / f"{base_name}.mp4")]

    images = media.get("images") or ([media["url"]] if media.get("url") else [])
    if not images:
        raise KuaishouDownloadError("解析到了内容，但没有视频或图片下载地址。")

    outputs: list[Path] = []
    for index, image_url in enumerate(images, start=1):
        outputs.append(download_file(session, str(image_url), output_dir / f"{base_name}_{index:02d}.jpg"))
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载快手分享链接里的无水印视频或图片。")
    parser.add_argument("share_text", nargs="*", help="整段快手分享文案，或单独的分享链接。")
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
        share_text = input("请粘贴快手分享文案或链接：").strip()

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
