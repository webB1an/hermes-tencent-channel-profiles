#!/usr/bin/env python3
"""
视频搬运脚本 v3.0
功能：下载抖音/小红书/快手无水印视频 → 轮询发到所有频道主的频道 → 删除本地文件

频道主频道池（随机轮询）:
  - 自拍摄影圈
  - 孟德严选
  - 女友控
  - 忏悔一切
  - 肉腿控

用法（CLI 模式）:
    python mengde_video_poster.py "分享文案"

用法（stdin JSON 模式）:
    echo '{"share_text": "..."}' | python mengde_video_poster.py --stdin
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# ============================================================================
# 配置
# ============================================================================

SCRIPTS_DIR = Path(__file__).parent
DOUYIN_SCRIPT = SCRIPTS_DIR / "remove-short-videos-watermark" / "douyin.py"
XHS_SCRIPT = SCRIPTS_DIR / "remove-short-videos-watermark" / "xiaohongshu.py"
KUAISHOU_SCRIPT = SCRIPTS_DIR / "remove-short-videos-watermark" / "kuaishou.py"

# 频道主频道池（id, 名称）
OWNER_GUILDS = [
    ("664279424082167719", "自拍摄影圈"),
    ("670516334082074035", "孟德严选"),
    ("584303044082165170", "女友控"),
    ("661081054082166997", "忏悔一切"),
    ("46486561778743039", "肉腿控"),
]

STATE_FILE = Path(tempfile.gettempdir()) / "mengde_round_robin.json"


# ============================================================================
# 轮询状态管理
# ============================================================================

def load_pool() -> list[tuple[str, str]]:
    """加载轮询池，为空则初始化并打乱"""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            pool = [(g["id"], g["name"]) for g in data.get("pool", [])]
            if pool:
                return pool
        except Exception:
            pass
    # 首次或池空：打乱所有频道
    pool = list(OWNER_GUILDS)
    random.shuffle(pool)
    save_pool(pool)
    return pool


def save_pool(pool: list[tuple[str, str]]) -> None:
    """持久化轮询池"""
    data = {"pool": [{"id": g[0], "name": g[1]} for g in pool]}
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def remove_from_pool(guild_id: str) -> None:
    """从池中移除指定频道并保存"""
    pool = load_pool()
    pool = [g for g in pool if g[0] != guild_id]
    save_pool(pool)


def remove_from_pool(guild_id: str) -> None:
    """从池中移除指定频道并保存"""
    pool = load_pool()
    pool = [g for g in pool if g[0] != guild_id]
    save_pool(pool)


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
        if "douyin.com" in url:
            return Platform.DOUYIN, url.rstrip(".,;:!?，。；：！？)")
        if any(domain in url for domain in ("xiaohongshu.com", "xhslink.com", "xhs.com")):
            return Platform.XIAOHONGSHU, url.rstrip(".,;:!?，。；：！？)")
        if any(domain in url for domain in ("kuaishou.com", "chenzhongtech.com")):
            return Platform.KUAISHOU, url.rstrip(".,;:!?，。；：！？)")
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

def post_video_to_channel(guild_id: str, channel_id: str, video_path: Path, content: str = "") -> None:
    """将本地视频发布到指定频道"""
    cmd = [
        "tencent-channel-cli",
        "feed", "publish-feed",
        "--guild-id", guild_id,
        "--channel-id", channel_id,
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


def get_channel_id_for_guild(guild_id: str) -> str:
    """获取频道的"全部"版块 channel_id"""
    cmd = [
        "tencent-channel-cli",
        "manage", "get-guild-channel-list",
        "--guild-id", guild_id,
        "-j",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # fallback
        return "1"
    try:
        data = json.loads(result.stdout)
        channels = data.get("data", {}).get("channels", [])
        # 找"全部"或第一个
        for ch in channels:
            if ch.get("channel_name") in ("全部", "综合", "默认") or ch.get("is_default"):
                return str(ch["channel_id"])
        if channels:
            return str(channels[0]["channel_id"])
    except Exception:
        pass
    return "1"


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

    # 2. 下载视频到临时目录
    content = ""
    persistent_path = None
    try:
        with tempfile.TemporaryDirectory(prefix="mengde_video_") as tmp_dir:
            downloaded_path = download_video(platform, share_text, Path(tmp_dir))
            file_size = downloaded_path.stat().st_size
            print(f"下载完成：{downloaded_path.name} ({file_size / 1024 / 1024:.1f} MB)")

            # 复制到独立持久路径，避免 with 块退出后文件被删
            import shutil
            persistent_path = Path(tempfile.gettempdir()) / f"mengde_post_{downloaded_path.name}"
            shutil.copy2(downloaded_path, persistent_path)
            print(f"复制到临时持有路径：{persistent_path.name}")

            # 文件名处理：去 # 后缀 + 平台关键词检测
            stem = downloaded_path.stem
            content = stem.split("#")[0].strip()
            name_lower = stem.lower()
            if any(k in name_lower for k in ("douyin", "dy", "xiaohongshu", "xhs", "kuaishou", "ks")):
                content = ""

        # 3. 自动从池中选一个频道（随机轮询）
        pool = load_pool()
        if not pool:
            pool = list(OWNER_GUILDS)
            random.shuffle(pool)
            save_pool(pool)
        guild_id, guild_name_selected = pool[0]
        print(f"随机选择频道：{guild_name_selected}（池内共{len(pool)}个）")
        remove_from_pool(guild_id)

        # 4. 发帖
        channel_id = get_channel_id_for_guild(guild_id)
        print(f"目标频道：{guild_name_selected}（guild_id={guild_id}，channel_id={channel_id}）")
        print(f"发帖文案：'{content}'" if content else "发帖文案：（纯视频）")
        post_video_to_channel(guild_id, channel_id, persistent_path, content)

    finally:
        if persistent_path:
            persistent_path.unlink(missing_ok=True)
            print(f"删除本地文件：{persistent_path.name}")


# ============================================================================
# 入口
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="视频搬运脚本 v3.0（随机轮询频道主频道）")
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
