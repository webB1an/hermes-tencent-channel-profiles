#!/usr/bin/env python3
"""
每日清理脚本 — 清理运行时产物，防止磁盘膨胀

清理范围（保留 7 天）：
  - media/               图片（发帖后保留用于本地缓存）
  - sessions/            Session 文件、jsonl 日志、request_dump
  - cron/output/         Cron 任务输出
  - wallhaven_state.json, wallpaper_state.json   状态文件（超7天清理，避免孤立状态堆积）
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BASE_DIR = Path("/root/.hermes/profiles/tencent-channel")
RETENTION_SECONDS = 7 * 24 * 3600  # 7 天

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def age_seconds(path: Path) -> float:
    """文件修改时间距离现在的秒数"""
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return 0


def cleanup_dir(directory: Path, *, recursive: bool = False,
                older_than: float = RETENTION_SECONDS,
                filter_ext: set[str] | None = None) -> int:
    """
    清理目录中超过 old_than 秒的文件。
    返回删除的文件数量。
    """
    deleted = 0
    if not directory.exists():
        return 0

    for item in directory.rglob("*") if recursive else directory.iterdir():
        if not item.is_file():
            continue
        if filter_ext and item.suffix.lower() not in filter_ext:
            continue
        if age_seconds(item) > older_than:
            try:
                item.unlink()
                deleted += 1
            except OSError:
                pass
    return deleted


def cleanup_media() -> int:
    """清理 media 目录下的所有图片（无差别清理，不看时间）"""
    media = BASE_DIR / "media"
    deleted = 0
    for item in media.rglob("*"):
        if item.is_file() and item.suffix.lower() in IMAGE_EXTS:
            try:
                item.unlink()
                deleted += 1
            except OSError:
                pass
    return deleted


def cleanup_state_files() -> int:
    """清理 wallhaven_state.json 和 wallpaper_state.json（超7天）"""
    deleted = 0
    for name in ("wallhaven_state.json", "wallpaper_state.json"):
        path = BASE_DIR / name
        if path.exists() and age_seconds(path) > RETENTION_SECONDS:
            try:
                path.unlink()
                deleted += 1
            except OSError:
                pass
    return deleted


def main() -> int:
    total = 0

    # 1. media/ — 全量清理（图片已发帖，没必要一直留）
    m = cleanup_media()
    if m:
        print(f"media/: 删除了 {m} 张图片")
        total += m

    # 2. sessions/ — 保留 7 天
    sessions = BASE_DIR / "sessions"
    s = cleanup_dir(sessions, recursive=True, filter_ext={".json", ".jsonl"})
    if s:
        print(f"sessions/: 删除了 {s} 个旧文件（保留 7 天）")
        total += s

    # 3. cron/output/ — 保留 7 天
    cron_out = BASE_DIR / "cron" / "output"
    c = cleanup_dir(cron_out, recursive=True, filter_ext={".md"})
    if c:
        print(f"cron/output/: 删除了 {c} 个旧输出（保留 7 天）")
        total += c

    # 4. wallhaven_state.json, wallpaper_state.json — 保留 7 天
    w = cleanup_state_files()
    if w:
        print(f"状态文件: 删除了 {w} 个（保留 7 天）")
        total += w

    if total:
        print(f"总计清理: {total} 个文件")
    else:
        print("无需清理")

    return 0


if __name__ == "__main__":
    sys.exit(main())
