#!/usr/bin/env python3
"""
anime-pictures.net 手动触发发帖脚本
用户说"anime_pictures发帖"时调用。

逻辑：
  1. 用 browser_tool 访问首页，提取"今日最佳"+"本周最佳"的所有 post_id
  2. 读取 state 文件排除已发帖
  3. 随机选 1 张
  4. 下载图片 → 构造 caption → tencent-channel-cli 发帖 → 记录 state
"""

import argparse
import base64
import json
import logging
import os
import random
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── 复用 anime_pictures_cold_detect 的组件 ──────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from anime_pictures_cold_detect import (
    Config, HttpClient, AnimePicturesParser,
    ImageDownloader, Translator, build_caption,
    publish_post, load_state, save_state,
    LOG,
)

CST = timezone(timedelta(hours=8))

def cst_now() -> datetime:
    return datetime.now(CST)


def fetch_best_ids_via_browser(browser_console_fn) -> dict[str, list[int]]:
    """
    通过 browser_tool 在首页执行 JS，提取两个最佳板块的 post_id。
    browser_console_fn: 传入 browser_console tool 的调用函数
    返回 {"day": [...], "week": [...]}
    """
    js = """
    const snapshot = {};
    document.querySelectorAll('.index_page').forEach((section) => {
        const title = section.querySelector('.title');
        if (!title) return;
        const text = title.textContent.trim();
        if (text.includes('Highest rated')) {
            const links = Array.from(section.querySelectorAll('a[href*="/posts/"]'))
                .map(a => a.getAttribute('href').match(/\/posts\/(\d+)/)?.[1])
                .filter(Boolean);
            const key = text.includes('day') ? 'day' : 'week';
            snapshot[key] = snapshot[key] || [];
            snapshot[key].push(...links);
        }
    });
    // 去重
    for (const k in snapshot) snapshot[k] = [...new Set(snapshot[k])];
    JSON.stringify(snapshot);
    """
    result = browser_console_fn(expression=js)
    data = json.loads(result)
    return {k: [int(x) for x in v] for k, v in data.items()}


def run(args: argparse.Namespace) -> int:
    # ── 读取配置 ─────────────────────────────────────────────────────────────
    hermes_home = Path(os.environ.get(
        "HERMES_HOME", "/root/.hermes/profiles/tencent-channel"))
    state_file = hermes_home / "anime_pictures_state.json"
    wallpaper_dir = hermes_home / "media" / "anime-pictures"

    # 构造最小 Config（直接实例化，不用 load，避免 search_tag 验证）
    cfg = Config(
        guild_id="652812504031889164",
        channel_id="669891684",
        hermes_home=hermes_home,
        wallpaper_dir=wallpaper_dir,
        state_file=state_file,
        check_window=(7, 23),
        cold_threshold=timedelta(minutes=65),
        min_self_interval=timedelta(minutes=120),
        download_count=3,
        max_images_per_post=9,
        http_timeout=15,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        minimax_base_url="https://api.minimaxi.com/v1",
        minimax_model="MiniMax-M2.7",
        minimax_api_key=os.environ.get("MINIMAX_API_KEY", ""),
        search_tag="",
    )
    http = HttpClient(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        timeout=15,
    )
    parser = AnimePicturesParser(http)
    translator = Translator(http, cfg)
    img_downloader = ImageDownloader(http, cfg)

    # ── 读取 state（已发帖记录）───────────────────────────────────────────────
    state = load_state(cfg.state_file)
    posted_ids: set[int] = set(int(pid) for pid in state.get("posted_ids", []))
    LOG.info("已发帖记录: %d 张", len(posted_ids))

    # ── 从首页提取今日/本周最佳 post_id ──────────────────────────────────────
    # browser_console_fn 由调用者注入（agent 模式才有 browser_tool）
    if args.browser_console:
        best_ids = fetch_best_ids_via_browser(args.browser_console)
    else:
        # fallback: 用 requests（首页 HTML 可访问，部分内容有 SSR）
        import re
        ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        req = urllib.request.Request(
            "https://anime-pictures.net/",
            headers={"User-Agent": ua, "Referer": "https://anime-pictures.net/"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="replace")

        best_ids = {"day": [], "week": []}
        day_idx = text.find("Highest rated anime pictures of the day")
        week_idx = text.find("Highest rated anime pictures of the week")
        for label, idx in [("day", day_idx), ("week", week_idx)]:
            if idx < 0:
                continue
            block_start = text.find('<div class="images-block', idx)
            block_end = text.find("</div>", block_start + 100) if block_start >= 0 else -1
            if block_start < 0:
                continue
            block = text[block_start:block_end + 6]
            best_ids[label] = [int(x) for x in re.findall(r'href="\.\/posts\/(\d+)\?lang=en"', block)]

    day_ids = best_ids.get("day", [])
    week_ids = best_ids.get("week", [])
    all_best = day_ids + week_ids
    unique_best = list(dict.fromkeys(all_best))  # 保留顺序去重

    LOG.info("今日最佳: %d 张, 本周最佳: %d 张, 合并去重: %d 张",
             len(day_ids), len(week_ids), len(unique_best))

    # ── 排除已发帖 + 随机选 1 ─────────────────────────────────────────────────
    candidates = [pid for pid in unique_best if pid not in posted_ids]
    if not candidates:
        LOG.warning("今日+本周最佳已全部发过，无可用壁纸")
        return 0

    selected_id = random.choice(candidates)
    LOG.info("选中 post_id=%d", selected_id)

    # ── 获取标签和元数据 ──────────────────────────────────────────────────────
    try:
        tags = parser.get_post_tags(selected_id)
    except Exception as exc:
        LOG.warning("获取 post_id=%d 标签失败: %s", selected_id, exc)
        tags = {"all": [], "copyright": [], "character": [], "artist": [],
                "reference": [], "meta": [], "file_url": "", "md5": "", "ext": ".png"}

    LOG.info("标签: copyright=%s, character=%s, artist=%s",
             tags.get("copyright", [])[:3],
             tags.get("character", [])[:3],
             tags.get("artist", [])[:3])

    # ── 下载图片（优先 browser，fallback 到 ImageDownloader）────────────────
    img_bytes = None
    img_source = ""

    if args.browser_navigate and args.browser_console:
        # agent 模式：用 browser_tool 下载完整图片
        try:
            nav_result = args.browser_navigate(
                url=f"https://anime-pictures.net/posts/{selected_id}?lang=en"
            )
            download_js = """
            async function() {
                // 找到下载链接
                const link = document.querySelector('a[href*="api.anime-pictures.net/pictures/download_image"]');
                if (!link) return JSON.stringify({error: 'no_download_link'});
                const url = link.href;
                // fetch 图片
                const resp = await fetch(url, {credentials: 'include'});
                if (!resp.ok) return JSON.stringify({error: 'fetch_failed', status: resp.status});
                const buf = await resp.arrayBuffer();
                const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
                const ct = resp.headers.get('content-type') || 'image/png';
                return JSON.stringify({data: b64, contentType: ct});
            }
            """
            dl_result = json.loads(args.browser_console(expression=download_js))
            if "data" in dl_result:
                img_bytes = base64.b64decode(dl_result["data"])
                img_source = "browser_full"
                LOG.info("browser_tool 下载成功: %.1fKB", len(img_bytes) / 1024)
        except Exception as exc:
            LOG.warning("browser 下载失败: %s", exc)

    if not img_bytes:
        # fallback: ImageDownloader（直接 HTTP 或预览图）
        img_bytes, src_desc, fmt = img_downloader.download(
            selected_id,
            md5=tags.get("md5", ""),
            ext=tags.get("ext", ".png"),
            file_url=tags.get("file_url", ""),
        )
        img_source = src_desc or "fallback_failed"
        if img_bytes:
            LOG.info("ImageDownloader 下载成功: %s (%.1fKB)", img_source, len(img_bytes) / 1024)

    if not img_bytes:
        LOG.error("所有下载策略均失败，post_id=%d", selected_id)
        return 1

    # ── 保存图片 ─────────────────────────────────────────────────────────────
    ext = tags.get("ext", ".png")
    img_path = cfg.wallpaper_dir / f"anime_pictures_{selected_id}{ext}"
    img_path.parent.mkdir(parents=True, exist_ok=True)
    img_path.write_bytes(img_bytes)

    # ── 构造 caption ─────────────────────────────────────────────────────────
    caption = build_caption(tags, translator, f"{tags.get('width',0)}x{tags.get('height',0)}")
    LOG.info("caption: %s", caption[:120])

    # ── 审核模式：暂停等待用户确认 ─────────────────────────────────────────
    if args.review and not args.dry_run:
        # 图片 + 标签 + 文案全部输出到 stdout，返回特殊 exit code 66
        # 由调用者（agent）用 clarify 展示给用户
        review_info = {
            "action": "review",
            "post_id": selected_id,
            "image_path": str(img_path),
            "caption": caption,
            "tags": {
                "copyright": tags.get("copyright", [])[:5],
                "character": tags.get("character", [])[:5],
                "artist": tags.get("artist", [])[:3],
            },
            "resolution": f"{tags.get('width', 0)}x{tags.get('height', 0)}",
            "img_source": img_source,
            "img_size_kb": len(img_bytes) // 1024,
        }
        print(json.dumps(review_info, ensure_ascii=False, indent=2))
        LOG.info("[REVIEW] 等待用户确认，post_id=%d", selected_id)
        # 返回 66 作为审核信号（agent 收到后执行 clarify）
        return 66

    # ── 发帖 ─────────────────────────────────────────────────────────────────
    if args.dry_run:
        LOG.info("[DRY-RUN] 跳过发帖，图片已保存: %s", img_path)
        return 0

    feed_id = publish_post(cfg, [img_path], caption)
    if not feed_id:
        LOG.error("发帖失败")
        return 1

    # ── 更新 state ───────────────────────────────────────────────────────────
    new_state = load_state(cfg.state_file)
    new_state.setdefault("posted_ids", []).append(str(selected_id))
    new_state.setdefault("self_feed_ids", []).append(feed_id)
    new_state["last_self_post_ts"] = int(cst_now().timestamp())
    save_state(cfg.state_file, new_state)

    LOG.info("发帖成功: post_id=%d, feed_id=%s", selected_id, feed_id)

    # ── 清理图片 ─────────────────────────────────────────────────────────────
    try:
        img_path.unlink()
    except OSError:
        pass

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="anime-pictures.net 手动发帖")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--review", action="store_true",
                        help="审核模式：下载完成后暂停，展示给用户确认再发帖")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    # 无 browser_tool 时禁用 browser 下载
    args.browser_navigate = None
    args.browser_console = None

    sys.exit(run(args))
