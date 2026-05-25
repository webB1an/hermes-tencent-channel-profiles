#!/usr/bin/env python3
"""
每日清理脚本 — 清理运行时产物，防止磁盘膨胀

清理范围（保留 7 天）：
  - media/               图片（发帖后保留用于本地缓存）
  - sessions/            Session 文件、jsonl 日志、request_dump
  - cron/output/         Cron 任务输出

注意：state/ 目录（wallpaper_state.json、wallhaven_state.json、jandan_synced_ids.json 等）
不在清理范围——它们是持久化状态，包含去重和进度信息，删除会导致重复发帖。
"""
from __future__ import annotations

import os
import sys
import time
import json
from pathlib import Path

BASE_DIR = Path("/root/.hermes/profiles/tencent-channel-june")
RETENTION_SECONDS = 7 * 24 * 3600  # 7 天
KEEP_LAST_POSTED = 500  # posted_ids 只保留最新 500 条

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
    """
    清理 wallpaper_state.json（每7天）：
      - posted_ids: 截断保留最新 KEEP_LAST_POSTED 条
      - self_feed_ids / self_post_timestamps: 过滤掉 7 天前的记录
      - last_self_post_ts: 如果超过 7 天无发帖则重置为 0
    wallhaven_state.json: 同理（如果存在）
    """
    deleted = 0
    now = time.time()
    cutoff = now - RETENTION_SECONDS

    for fname in ("wallpaper_state.json", "wallhaven_state.json"):
        fpath = BASE_DIR / fname
        if not fpath.exists():
            continue
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        changed = False

        # posted_ids: 只保留最新 500 条
        if "posted_ids" in data and len(data["posted_ids"]) > KEEP_LAST_POSTED:
            data["posted_ids"] = data["posted_ids"][-KEEP_LAST_POSTED:]
            changed = True

        # self_post_timestamps: 过滤 7 天前的
        if "self_post_timestamps" in data:
            before = len(data["self_post_timestamps"])
            data["self_post_timestamps"] = [ts for ts in data["self_post_timestamps"] if ts >= cutoff]
            if len(data["self_post_timestamps"]) < before:
                changed = True

        # self_feed_ids: 与 self_post_timestamps 同步裁剪（按最小长度）
        if "self_feed_ids" in data and "self_post_timestamps" in data:
            min_len = min(len(data["self_feed_ids"]), len(data["self_post_timestamps"]))
            data["self_feed_ids"] = data["self_feed_ids"][:min_len]

        # last_self_post_ts: 超过 7 天无发帖则重置
        if "last_self_post_ts" in data and data["last_self_post_ts"] < cutoff:
            data["last_self_post_ts"] = 0
            changed = True

        if changed:
            bak = fpath.with_suffix(".json.bak")
            fpath.rename(bak)
            fpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            bak.unlink(missing_ok=True)
            deleted += 1
            print(f"  {fname}: 已清理（备份并重写）")
        else:
            print(f"  {fname}: 无需清理")

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

    # 4. state 文件 — 保留 7 天滚动清理
    st = cleanup_state_files()
    total += st

    if total:
        print(f"总计清理: {total} 个文件")
    else:
        print("无需清理")

    return 0


if __name__ == "__main__":
    sys.exit(main())
