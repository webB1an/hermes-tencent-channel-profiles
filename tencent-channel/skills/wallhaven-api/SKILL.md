---
name: wallhaven-api
description: Use Wallhaven's public API to search, browse, and download high-quality wallpapers. Trigger this skill whenever the user mentions wallhaven, wallpapers, desktop backgrounds, or wants to programmatically fetch/search/download wallpapers from wallhaven.cc. Also trigger when the user wants to build a wallpaper app, wallpaper browser, wallpaper downloader, or any tool that integrates with wallhaven. Covers search with filters, wallpaper info, tag lookup, user settings, and collection browsing.
---

# Wallhaven API Skill

This skill enables Claude to write code that interacts with the Wallhaven API (v1) — a free, public REST API for searching and retrieving high-quality wallpapers from wallhaven.cc.

## Quick Overview

- **Base URL**: `https://wallhaven.cc/api/v1`
- **Auth**: Optional API key via `?apikey=<KEY>` query parameter. Required only for NSFW content and user-specific settings.
- **Rate Limit**: 45 requests/minute. Exceeding returns `429 Too Many Requests`.
- **Method**: GET only (read-only API).
- **Response Format**: JSON, all responses wrapped in a `"data"` key.

## When to Use This Skill

- User wants to search wallhaven for wallpapers by keyword, tag, resolution, color, etc.
- User wants to build a wallpaper browser, downloader, or gallery app.
- User wants to fetch metadata (resolution, colors, tags, file size) for a specific wallpaper.
- User wants to browse collections or get tag info from wallhaven.
- User wants to create a script to batch-download wallpapers.

## API Endpoints

For the full endpoint reference with all parameters and response schemas, read `references/api-reference.md`.

### 1. Search Wallpapers

```
GET /api/v1/search?q=<query>&apikey=<optional>
```

Key parameters: `q` (query), `categories`, `purity`, `sorting`, `order`, `topRange`, `atleast`, `resolutions`, `colors`, `ratios`, `page`, `seed`.

Default returns latest SFW wallpapers, 24 per page (up to 64 if configured in account settings).

### 2. Random Wallpapers

```
GET /api/v1/random
```

Returns a list of random wallpaper objects. No parameters. Good for fetching a batch of random wallpapers without query/search overhead. Each item contains `id`, `url`, `short_url`, `views`, `favorites`, `source`, `purity`, `type`, `resolution`, `file_size`, `created_at`, `colors`, `path` (direct image URL), `thumbs`, `tags`.

### 3. Get Wallpaper Info

```
GET /api/v1/w/<ID>
```

Returns full metadata: resolution, file size, file type, colors, tags, uploader, views, favorites, source URL, and the direct image path.

### 3. Get Tag Info

```
GET /api/v1/tag/<ID>
```

Returns tag name, alias, category, purity.

### 4. User Settings (requires API key)

```
GET /api/v1/settings?apikey=<KEY>
```

Returns the user's browsing preferences (purity, categories, resolutions, blacklists, etc.).

### 5. Collections

```
GET /api/v1/collections?apikey=<KEY>          # own collections
GET /api/v1/collections/<USERNAME>             # another user's public collections
GET /api/v1/collections/<USERNAME>/<ID>        # wallpapers in a collection
```

## Implementation Patterns

### Python (requests)

```python
import requests
import time

class WallhavenClient:
    BASE_URL = "https://wallhaven.cc/api/v1"

    def __init__(self, api_key=None):
        self.api_key = api_key
        self.session = requests.Session()
        if api_key:
            self.session.params = {"apikey": api_key}

    def _get(self, endpoint, params=None):
        """Make a GET request with basic rate-limit handling."""
        url = f"{self.BASE_URL}/{endpoint}"
        resp = self.session.get(url, params=params)
        if resp.status_code == 429:
            print("Rate limited. Waiting 60s...")
            time.sleep(60)
            resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()["data"]

    def search(self, query="", **kwargs):
        """Search wallpapers. kwargs map to API params: categories, purity, sorting, etc."""
        params = {"q": query, **kwargs}
        return self._get("search", params=params)

    def wallpaper(self, wallpaper_id):
        """Get full info for a single wallpaper."""
        return self._get(f"w/{wallpaper_id}")

    def tag(self, tag_id):
        """Get tag info by ID."""
        return self._get(f"tag/{tag_id}")

    def collections(self, username=None, collection_id=None):
        """List collections or wallpapers in a collection."""
        if username and collection_id:
            return self._get(f"collections/{username}/{collection_id}")
        elif username:
            return self._get(f"collections/{username}")
        else:
            return self._get("collections")

    def download_wallpaper(self, wallpaper_id, output_dir="."):
        """Download the full-resolution wallpaper image."""
        import os
        info = self.wallpaper(wallpaper_id)
        image_url = info["path"]
        filename = os.path.basename(image_url)
        filepath = os.path.join(output_dir, filename)
        resp = self.session.get(image_url, stream=True)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return filepath
```

### JavaScript / Node.js (fetch)

```javascript
class WallhavenClient {
  constructor(apiKey = null) {
    this.baseUrl = "https://wallhaven.cc/api/v1";
    this.apiKey = apiKey;
  }

  async _get(endpoint, params = {}) {
    if (this.apiKey) params.apikey = this.apiKey;
    const qs = new URLSearchParams(params).toString();
    const url = `${this.baseUrl}/${endpoint}${qs ? "?" + qs : ""}`;
    const resp = await fetch(url);
    if (resp.status === 429) throw new Error("Rate limited (45 req/min)");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
    const json = await resp.json();
    return json.data;
  }

  search(query = "", params = {}) {
    return this._get("search", { q: query, ...params });
  }

  wallpaper(id) {
    return this._get(`w/${id}`);
  }

  tag(id) {
    return this._get(`tag/${id}`);
  }

  collections(username = null, collectionId = null) {
    if (username && collectionId) return this._get(`collections/${username}/${collectionId}`);
    if (username) return this._get(`collections/${username}`);
    return this._get("collections");
  }
}
```

### Browser / React Artifact

When building a wallpaper browser as a React artifact, the Wallhaven API supports CORS so you can call it directly from the browser. Use the JS client pattern above inside React components.

## Non-Duplicate Download + Ontology Tracking

This skill now supports a workspace-local download flow that prevents repeated downloads by checking `wallhaven_id` in ontology before saving.

Script:

```bash
python skills/wallhaven-api/scripts/wallhaven_download.py --mode toplist
```

Built-in modes:

- `latest` / `最新` / `最新壁纸` — 最新壁纸（`sorting=date_added`）
- `hot` / `热门` / `热门壁纸` — 热门壁纸（`sorting=favorites`）
- `toplist` / `排行` / `排行榜` / `排行壁纸` — 排行壁纸（`sorting=toplist`）
- `random` / `随机` / `随机壁纸` — 随机壁纸（`sorting=random`）
- `mobile` / `手机` / `手机壁纸` — 手机壁纸（竖屏比例 + 高分辨率）
- `anime` / `动漫` / `动漫壁纸` — 动漫壁纸（`categories=010`）
- `search` / `搜索` / `搜索壁纸` — 搜索壁纸（需要 `--query`）

Examples:

```bash
python skills/wallhaven-api/scripts/wallhaven_download.py --mode latest
python skills/wallhaven-api/scripts/wallhaven_download.py --mode 热门
python skills/wallhaven-api/scripts/wallhaven_download.py --mode 排行 --topRange 1w
python skills/wallhaven-api/scripts/wallhaven_download.py --mode 随机 --count 3
python skills/wallhaven-api/scripts/wallhaven_download.py --mode 手机壁纸 --count 2
python skills/wallhaven-api/scripts/wallhaven_download.py --mode 动漫
python skills/wallhaven-api/scripts/wallhaven_download.py --mode 搜索 --query "cyberpunk" --count 5
```

Behavior:

- Searches Wallhaven using the selected mode defaults
- Supports `--count N` to download multiple new wallpapers in one run
- Skips any wallpaper whose `wallhaven_id` already exists in `memory/ontology/graph.jsonl`
- If the current page has only duplicates or not enough new wallpapers, automatically checks later pages (up to `--max-pages`)
- Downloads the selected new wallpapers into `media/wallpapers/`
- Records each successful download into ontology as a `Document` entity with `source=wallhaven` and `wallhaven_id=<ID>`

Recorded fields include:

- `wallhaven_id`
- `path`
- `url`
- `image_url`
- `category`
- `purity`
- `resolution`
- `file_type`
- `file_size`
- `tags`
- `downloaded_at`

This means future downloads can use ontology as the dedupe source of truth.

## Important Notes

- **Rate limiting**: 45 requests/minute. For batch operations, add a ~1.5s delay between requests to stay safe. Handle 429 responses with exponential backoff.
- **NSFW content**: Requires a valid API key. Without it, NSFW wallpapers return 401.
- **Random endpoint does NOT return tags**: `GET /search?sorting=random` returns `null` for the `tags` field in every item. The `tags` array is only populated by `GET /w/{id}` (wallpaper detail). Optimization strategy: pick candidates from search results first (no tag detail yet), then only call `/w/{id}` for the final N candidates — do NOT call `/w/{id}` for every result in the search page, as that defeats the purpose of the random search.
- **Image paths**: The `path` field in wallpaper data is the direct URL to the full-resolution image (e.g., `https://w.wallhaven.cc/full/xx/wallhaven-xxxx.jpg`). Thumbnail paths are in `thumbs.large`, `thumbs.original`, `thumbs.small`.
- **Pagination**: Responses include `meta.current_page`, `meta.last_page`, `meta.total` for pagination. Default 24 results/page, max 64 (configurable via account settings).
- **Search query syntax**: Supports special syntax like `id:123` for exact tag search, `@username` for user uploads, `like:wallpaper_id` for similar wallpapers, `type:png/jpg` for file type filtering.
- **Categories**: Use 3-digit binary string — `1` = on, `0` = off, in order: General / Anime / People. E.g., `"110"` = General + Anime.
- **Purity**: Same pattern — SFW / Sketchy / NSFW. E.g., `"100"` = SFW only, `"110"` = SFW + Sketchy.
- **Colors**: Filter by hex color like `#660000`. Only one color at a time.
