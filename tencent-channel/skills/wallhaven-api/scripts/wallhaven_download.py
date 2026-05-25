#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
ONTOLOGY_SCRIPT = WORKSPACE_ROOT / "skills" / "ontology" / "scripts" / "ontology.py"
GRAPH_PATH = WORKSPACE_ROOT / "memory" / "ontology" / "graph.jsonl"
DEFAULT_OUTPUT_DIR = WORKSPACE_ROOT / "media" / "wallpapers"
API_BASE = "https://wallhaven.cc/api/v1"


MODE_DEFAULTS = {
    "latest": {
        "sorting": "date_added",
        "order": "desc",
        "categories": "100",
        "purity": "100",
    },
    "hot": {
        "sorting": "favorites",
        "order": "desc",
        "categories": "100",
        "purity": "100",
    },
    "toplist": {
        "sorting": "toplist",
        "order": "desc",
        "topRange": "1M",
        "categories": "100",
        "purity": "100",
    },
    "random": {
        "sorting": "random",
        "order": "desc",
        "categories": "100",
        "purity": "100",
    },
    "mobile": {
        "sorting": "toplist",
        "order": "desc",
        "topRange": "1M",
        "categories": "100",
        "purity": "100",
        "ratios": "9x16,10x16,9x18,9x19,9x20",
        "atleast": "1080x1920",
    },
    "anime": {
        "sorting": "toplist",
        "order": "desc",
        "topRange": "1M",
        "categories": "010",
        "purity": "100",
    },
    "search": {
        "sorting": "relevance",
        "order": "desc",
        "categories": "100",
        "purity": "100",
    },
}

MODE_ALIASES = {
    "最新": "latest",
    "最新壁纸": "latest",
    "热门": "hot",
    "热门壁纸": "hot",
    "排行": "toplist",
    "排行榜": "toplist",
    "排行壁纸": "toplist",
    "随机": "random",
    "随机壁纸": "random",
    "手机": "mobile",
    "手机壁纸": "mobile",
    "动漫": "anime",
    "动漫壁纸": "anime",
    "搜索": "search",
    "搜索壁纸": "search",
}

MODE_DESCRIPTIONS = {
    "latest": "最新壁纸",
    "hot": "热门壁纸",
    "toplist": "排行壁纸",
    "random": "随机壁纸",
    "mobile": "手机壁纸",
    "anime": "动漫壁纸",
    "search": "搜索壁纸",
}


def http_get_json(url: str) -> dict:
    req = Request(url, headers={"User-Agent": "OpenClaw-WallhavenAPI/1.0"})
    with urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_wallpapers(query: str, **params) -> tuple[list[dict], dict]:
    qs = {"q": query, **{k: v for k, v in params.items() if v is not None and v != ""}}
    url = f"{API_BASE}/search?{urlencode(qs)}"
    payload = http_get_json(url)
    return payload.get("data", []), payload.get("meta", {})


def wallpaper_info(wallpaper_id: str) -> dict:
    url = f"{API_BASE}/w/{wallpaper_id}"
    payload = http_get_json(url)
    return payload["data"]


def ontology_query_by_wallhaven_id(wallhaven_id: str) -> list[dict]:
    import subprocess

    cmd = [
        sys.executable,
        str(ONTOLOGY_SCRIPT),
        "query",
        "--type",
        "Document",
        "--where",
        json.dumps({"source": "wallhaven", "wallhaven_id": wallhaven_id}),
        "--graph",
        str(GRAPH_PATH),
    ]
    result = subprocess.run(cmd, cwd=WORKSPACE_ROOT, check=True, capture_output=True, text=True)
    return json.loads(result.stdout or "[]")


def ontology_record_wallpaper(info: dict, saved_path: Path) -> dict:
    import subprocess

    tag_names = [tag.get("name") for tag in info.get("tags", []) if tag.get("name")]
    properties = {
        "title": f"Wallhaven {info['id']}",
        "path": str(saved_path),
        "url": info.get("url"),
        "summary": info.get("short_url") or info.get("path"),
        "source": "wallhaven",
        "wallhaven_id": info["id"],
        "image_url": info.get("path"),
        "category": info.get("category"),
        "purity": info.get("purity"),
        "resolution": info.get("resolution"),
        "file_type": info.get("file_type"),
        "file_size": info.get("file_size"),
        "favorites": info.get("favorites"),
        "views": info.get("views"),
        "tags": tag_names,
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    cmd = [
        sys.executable,
        str(ONTOLOGY_SCRIPT),
        "create",
        "--type",
        "Document",
        "--id",
        f"wall_{info['id']}",
        "--props",
        json.dumps(properties, ensure_ascii=False),
        "--graph",
        str(GRAPH_PATH),
    ]
    result = subprocess.run(cmd, cwd=WORKSPACE_ROOT, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def download_file(url: str, dest: Path) -> None:
    ensure_parent(dest)
    req = Request(url, headers={"User-Agent": "OpenClaw-WallhavenAPI/1.0"})
    with urlopen(req) as resp, open(dest, "wb") as fh:
        while True:
            chunk = resp.read(1024 * 64)
            if not chunk:
                break
            fh.write(chunk)


def output_filename(info: dict) -> str:
    ext = Path(info.get("path", "")).suffix or ".jpg"
    return f"wallhaven-{info['id']}{ext}"


def normalize_mode(mode: str) -> str:
    return MODE_ALIASES.get(mode, mode)


def build_params(args: argparse.Namespace, mode: str) -> dict:
    params = dict(MODE_DEFAULTS[mode])
    if args.query:
        params["q"] = args.query

    overrides = {
        "categories": args.categories,
        "purity": args.purity,
        "sorting": args.sorting,
        "order": args.order,
        "topRange": args.topRange,
        "atleast": args.atleast,
        "resolutions": args.resolutions,
        "ratios": args.ratios,
        "colors": args.colors,
    }
    for key, value in overrides.items():
        if value is not None:
            params[key] = value

    if mode == "random" and "seed" not in params:
        params["seed"] = f"oc{random.randint(100000, 999999)}"

    return params


def fetch_candidate_batch(params: dict, page: int) -> tuple[list[dict], dict]:
    search_params = dict(params)
    search_params["page"] = page
    query = search_params.pop("q", "")
    return search_wallpapers(query, **search_params)


def download_and_record(info: dict, output_dir: Path) -> dict:
    saved_path = output_dir / output_filename(info)
    if saved_path.exists():
        raise FileExistsError(str(saved_path))
    download_file(info["path"], saved_path)
    entity = ontology_record_wallpaper(info, saved_path)
    return {
        "wallhaven_id": info["id"],
        "saved_path": str(saved_path),
        "entity_id": entity["id"],
        "url": info.get("url"),
        "image_url": info.get("path"),
        "resolution": info.get("resolution"),
        "tags": [tag.get("name") for tag in info.get("tags", []) if tag.get("name")],
    }


def collect_new_wallpapers(params: dict, start_page: int, max_pages: int, count: int) -> tuple[list[dict], dict, int]:
    meta: dict = {}
    selected: list[dict] = []
    seen_ids: set[str] = set()
    page = start_page
    pages_checked = 0

    while pages_checked < max_pages and len(selected) < count:
        results, meta = fetch_candidate_batch(params, page)
        pages_checked += 1
        if not results:
            break

        for item in results:
            wallhaven_id = item.get("id")
            if not wallhaven_id or wallhaven_id in seen_ids:
                continue
            seen_ids.add(wallhaven_id)
            existing = ontology_query_by_wallhaven_id(wallhaven_id)
            if existing:
                continue
            selected.append(item)
            if len(selected) >= count:
                break

        last_page = int(meta.get("last_page") or page)
        if page >= last_page:
            break
        page += 1

    return selected, meta, pages_checked


def main() -> int:
    parser = argparse.ArgumentParser(description="Download non-duplicate Wallhaven wallpapers and record them in ontology")
    parser.add_argument("--mode", default="toplist", help="Mode name or Chinese alias")
    parser.add_argument("--query", default="", help="Search keyword, mainly for mode=search but also usable as an extra filter")
    parser.add_argument("--count", type=int, default=1, help="How many new wallpapers to download")
    parser.add_argument("--categories")
    parser.add_argument("--purity")
    parser.add_argument("--sorting")
    parser.add_argument("--order")
    parser.add_argument("--topRange")
    parser.add_argument("--atleast")
    parser.add_argument("--resolutions")
    parser.add_argument("--ratios")
    parser.add_argument("--colors")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    mode = normalize_mode(args.mode)
    if mode not in MODE_DEFAULTS:
        print(json.dumps({
            "ok": False,
            "reason": "invalid_mode",
            "mode": args.mode,
            "supported_modes": sorted(MODE_DEFAULTS.keys()),
            "aliases": MODE_ALIASES,
        }, ensure_ascii=False, indent=2))
        return 5

    if args.count < 1:
        print(json.dumps({"ok": False, "reason": "count_must_be_positive"}, ensure_ascii=False))
        return 6

    if mode == "search" and not args.query.strip():
        print(json.dumps({"ok": False, "reason": "search_mode_requires_query"}, ensure_ascii=False))
        return 4

    params = build_params(args, mode)
    picked_items, meta, pages_checked = collect_new_wallpapers(params, args.page, args.max_pages, args.count)

    if not picked_items:
        print(json.dumps({
            "ok": False,
            "reason": "all_results_already_downloaded_or_no_results",
            "meta": meta,
            "pages_checked": pages_checked,
            "mode": mode,
            "mode_label": MODE_DESCRIPTIONS.get(mode, mode),
            "query": args.query,
            "requested_count": args.count,
        }, ensure_ascii=False, indent=2))
        return 2

    output_dir = Path(args.output_dir).expanduser()
    downloads = []
    skipped_existing_files = []

    for item in picked_items:
        info = wallpaper_info(item["id"])
        try:
            downloads.append(download_and_record(info, output_dir))
        except FileExistsError as exc:
            skipped_existing_files.append({
                "wallhaven_id": info["id"],
                "path": str(exc),
                "reason": "file_already_exists_without_ontology_match",
            })

    if not downloads:
        print(json.dumps({
            "ok": False,
            "reason": "all_selected_files_already_exist_without_ontology_match",
            "mode": mode,
            "mode_label": MODE_DESCRIPTIONS.get(mode, mode),
            "query": args.query,
            "pages_checked": pages_checked,
            "skipped": skipped_existing_files,
        }, ensure_ascii=False, indent=2))
        return 3

    print(json.dumps({
        "ok": True,
        "mode": mode,
        "mode_label": MODE_DESCRIPTIONS.get(mode, mode),
        "query": args.query,
        "requested_count": args.count,
        "downloaded_count": len(downloads),
        "downloads": downloads,
        "pages_checked": pages_checked,
        "skipped": skipped_existing_files,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
