#!/usr/bin/env python3
"""
语音翻唱社区视频搬运脚本
功能：下载抖音/快手/小红书无水印视频 → 发到语音翻唱聊天聊天社区·唱歌/翻唱板块

目标频道：
  - 频道：语音翻唱聊天聊天社区（guild_id=585169334083036916）
  - 板块：唱歌/翻唱（channel_id=732731676）

用法：
    python sing_video_poster.py "分享文案"
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# ============================================================================
# 配置
# ============================================================================

SCRIPTS_DIR = Path(__file__).parent
REMOVE_WATERMARK_DIR = SCRIPTS_DIR / "remove-short-videos-watermark"
DOUYIN_SCRIPT = REMOVE_WATERMARK_DIR / "douyin.py"
XHS_SCRIPT = REMOVE_WATERMARK_DIR / "xiaohongshu.py"
KUAISHOU_SCRIPT = REMOVE_WATERMARK_DIR / "kuaishou.py"

# 固定目标频道
TARGET_GUILD_ID = "585169334083036916"
TARGET_CHANNEL_ID = "732731676"
TARGET_GUILD_NAME = "语音翻唱聊天聊天社区"
TARGET_CHANNEL_NAME = "唱歌/翻唱"

# ============================================================================
# 平台识别
# ============================================================================

class Platform:
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    KUAISHOU = "kuaishou"
    UNKNOWN = "unknown"


def detect_platform(text: str) -> tuple[Platform, str]:
    """从分享文案中识别平台并提取 URL"""
    urls = re.findall(r"https?://[^\s，。！？!！]+", text)
    for url in urls:
        url = url.rstrip(".,;:!?，。；：！？)")
        if "douyin.com" in url:
            return Platform.DOUYIN, url
        if any(domain in url for domain in ("xiaohongshu.com", "xhslink.com", "xhs.com")):
            return Platform.XIAOHONGSHU, url
        if any(domain in url for domain in ("kuaishou.com", "chenzhongtech.com")):
            return Platform.KUAISHOU, url
    raise ValueError("无法识别平台，请提供抖音/小红书/快手的分享链接")


# ============================================================================
# 下载视频
# ============================================================================

def download_video(platform: Platform, share_text: str, output_dir: Path) -> Path:
    """调用对应平台脚本下载视频，返回下载文件路径"""
    output_dir.mkdir(parents=True, exist_ok=True)

    if platform == Platform.DOUYIN:
        script = DOUYIN_SCRIPT
        args = [sys.executable, str(script), share_text, "-o", str(output_dir), "--backend", "native"]
    elif platform == Platform.XIAOHONGSHU:
        script = XHS_SCRIPT
        args = [sys.executable, str(script), share_text, "-o", str(output_dir)]
    elif platform == Platform.KUAISHOU:
        script = KUAISHOU_SCRIPT
        args = [sys.executable, str(script), share_text, "-o", str(output_dir)]
    else:
        raise ValueError(f"不支持的平台: {platform}")

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip() or "未知错误"
        raise RuntimeError(f"下载失败 [{platform}]：{error_msg}")

    # 解析输出最后一行的下载文件路径
    output_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    for line in reversed(output_lines):
        if line.startswith("下载完成："):
            return Path(line.replace("下载完成：", "").strip())
        if line.endswith(".mp4") or line.endswith(".jpg") or line.endswith(".png"):
            return Path(line.strip())

    # 兜底：在 output_dir 中找最新文件
    files = sorted(output_dir.glob("*"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise RuntimeError("下载脚本执行成功，但未找到输出文件")
    return files[-1]


# ============================================================================
# 腾讯频道发帖
# ============================================================================

def post_video_to_channel(video_path: Path, content: str = "") -> None:
    """将本地视频发布到指定频道"""
    cmd = [
        "tencent-channel-cli",
        "feed", "publish-feed",
        "--guild-id", TARGET_GUILD_ID,
        "--channel-id", TARGET_CHANNEL_ID,
        "--content", content,
        "--video", str(video_path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip() or "未知错误"
        raise RuntimeError(f"发帖失败：{error_msg}")

    data = json.loads(result.stdout)
    if data.get("success"):
        share_url = data.get("data", {}).get("share_url", "")
        print(f"发帖成功：{share_url}")
    else:
        raise RuntimeError(f"发帖失败：retCode={data.get('retCode')}, msg={data.get('msg', '未知错误')}")


# ============================================================================
# 主流程
# ============================================================================

def process(share_text: str) -> None:
    print(f"收到分享文案：{share_text[:80]}...")

    # 1. 识别平台
    platform, url = detect_platform(share_text)
    platform_name = {"douyin": "抖音", "xiaohongshu": "小红书", "kuaishou": "快手"}.get(platform, platform)
    print(f"识别平台：{platform_name}")
    print(f"提取链接：{url}")

    # 2. 下载视频
    content = ""
    persistent_path = None
    try:
        with tempfile.TemporaryDirectory(prefix="sing_video_") as tmp_dir:
            downloaded_path = download_video(platform, share_text, Path(tmp_dir))
            file_size = downloaded_path.stat().st_size
            print(f"下载完成：{downloaded_path.name} ({file_size / 1024 / 1024:.1f} MB)")

            # 复制到独立持久路径，避免 with 块退出后文件被删
            import shutil
            persistent_path = Path(tempfile.gettempdir()) / f"sing_post_{downloaded_path.name}"
            shutil.copy2(downloaded_path, persistent_path)
            print(f"复制到临时持有路径：{persistent_path.name}")

            # 文件名处理：去 # 后缀 + 平台关键词检测
            stem = downloaded_path.stem
            content = stem.split("#")[0].strip()
            name_lower = stem.lower()
            if any(k in name_lower for k in ("douyin", "dy", "xiaohongshu", "xhs", "kuaishou", "ks")):
                content = ""

        # 3. 发帖
        print(f"目标频道：{TARGET_GUILD_NAME} > {TARGET_CHANNEL_NAME}")
        print(f"发帖文案：'{content}'" if content else "发帖文案：（纯视频）")
        post_video_to_channel(persistent_path, content)

    finally:
        if persistent_path:
            persistent_path.unlink(missing_ok=True)
            print(f"删除本地文件：{persistent_path.name}")


# ============================================================================
# 入口
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="语音翻唱社区视频搬运脚本")
    parser.add_argument("share_text", nargs="*", help="平台分享文案或链接")
    parser.add_argument("--stdin", action="store_true", help="从 stdin JSON 读取 share_text")
    args = parser.parse_args()

    try:
        if args.stdin:
            payload = json.loads(sys.stdin.read())
            share_text = payload.get("share_text", "")
            if not share_text:
                print("错误：stdin JSON 缺少 share_text 字段", file=sys.stderr)
                return 1
        else:
            share_text = " ".join(args.share_text).strip()
            if not share_text:
                print("错误：请提供分享文案", file=sys.stderr)
                return 1

        process(share_text)
        return 0

    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())