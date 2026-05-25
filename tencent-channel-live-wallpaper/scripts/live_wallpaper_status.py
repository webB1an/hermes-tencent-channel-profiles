#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


ROOT = Path("/root/.hermes/profiles/tencent-channel-live-wallpaper")
DOWNLOADS = ROOT / "scripts" / "live-wallpaper-download" / "downloads"
STATE = ROOT / "live_wallpaper_state.json"
LOG = ROOT / "logs" / "live_wallpaper_post.log"
LOCK = Path("/tmp/live_wallpaper_post.lock")


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}B"
        size /= 1024
    return f"{size:.1f}GB"


def is_poster_running() -> bool:
    current = os.getpid()
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == current:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore")
        except OSError:
            continue
        if "live_wallpaper_post.py" in cmdline:
            return True
    return False


def posted_count() -> tuple[int, str]:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, ""
    urls = data.get("posted_detail_urls", [])
    return len(urls), (urls[-1] if urls else "")


def pending_files() -> list[Path]:
    if not DOWNLOADS.is_dir():
        return []
    return sorted(
        [p for p in DOWNLOADS.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"],
        key=lambda p: p.stat().st_mtime,
    )


def recent_log(lines: int = 12) -> list[str]:
    if not LOG.exists():
        return []
    try:
        all_lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    useful = [
        line for line in all_lines
        if any(key in line for key in ("动态壁纸发帖开始", "选中壁纸", "标题:", "文件:", "发帖成功", "发帖失败", "没有新壁纸", "另一个实例"))
    ]
    return useful[-lines:]


def main() -> int:
    running = is_poster_running()
    count, last_url = posted_count()
    files = pending_files()
    print("动态壁纸后台状态")
    print(f"- 后台进程: {'运行中' if running else '未运行'}")
    print(f"- 锁文件: {'存在' if LOCK.exists() else '不存在'}")
    print(f"- 已发记录: {count} 条")
    if last_url:
        print(f"- 最近已发 URL: {last_url}")
    print(f"- 待处理视频: {len(files)} 个")
    for file in files[:8]:
        print(f"  - {file.name} ({human_size(file.stat().st_size)})")
    if len(files) > 8:
        print(f"  - ... 还有 {len(files) - 8} 个")
    log_lines = recent_log()
    if log_lines:
        print("- 最近日志:")
        for line in log_lines:
            print(f"  {line}")
    else:
        print("- 最近日志: 暂无")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
