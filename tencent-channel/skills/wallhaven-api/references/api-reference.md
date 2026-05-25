# Wallhaven API v1 — Full Reference

## Table of Contents

1. [Authentication](#authentication)
2. [Rate Limiting](#rate-limiting)
3. [Search Endpoint](#search-endpoint)
4. [Wallpaper Info Endpoint](#wallpaper-info-endpoint)
5. [Tag Info Endpoint](#tag-info-endpoint)
6. [User Settings Endpoint](#user-settings-endpoint)
7. [Collections Endpoints](#collections-endpoints)
8. [Response Schemas](#response-schemas)
9. [Search Query Syntax](#search-query-syntax)
10. [Error Codes](#error-codes)

---

## Authentication

API key is optional but unlocks NSFW content and user-specific settings.

- Obtain from: Account Settings on wallhaven.cc
- Pass via query parameter: `?apikey=<YOUR_API_KEY>`
- Can be regenerated at any time
- When provided with search, the user's browsing settings (purity, categories, per_page, etc.) are merged with explicit params. Explicit params take priority.

## Rate Limiting

- **Limit**: 45 requests per minute (applies to all users equally, no paid tiers)
- **Exceeded**: Returns HTTP `429 Too Many Requests`
- **Strategy**: Add ~1.5s delay between batch requests; use exponential backoff on 429

## Search Endpoint

```
GET https://wallhaven.cc/api/v1/search
```

### Parameters

| Parameter    | Type   | Default     | Description |
|-------------|--------|-------------|-------------|
| `q`          | string | (empty)     | Search query. Supports special syntax (see below). |
| `categories` | string | `"111"`     | 3-digit binary: General / Anime / People. `"100"` = General only. |
| `purity`     | string | `"100"`     | 3-digit binary: SFW / Sketchy / NSFW. `"110"` = SFW + Sketchy. NSFW requires API key. |
| `sorting`    | string | `"date_added"` | One of: `date_added`, `relevance`, `random`, `views`, `favorites`, `toplist`. |
| `order`      | string | `"desc"`    | `desc` or `asc`. |
| `topRange`   | string | `"1M"`      | Only with `sorting=toplist`. Values: `1d`, `3d`, `1w`, `1M`, `3M`, `6M`, `1y`. |
| `atleast`    | string | —           | Minimum resolution, e.g. `"1920x1080"`. |
| `resolutions` | string | —          | Exact resolutions, comma-separated: `"1920x1080,2560x1440"`. |
| `ratios`     | string | —           | Aspect ratios, comma-separated: `"16x9,16x10"`. |
| `colors`     | string | —           | Hex color (no `#`), e.g. `"660000"`. One color only. |
| `page`       | int    | `1`         | Page number for pagination. |
| `seed`       | string | —           | Seed for `sorting=random` to ensure consistent pages. Returned in meta on first random request. |
| `apikey`     | string | —           | API key for auth. |

### Search Response Structure

```json
{
  "data": [
    {
      "id": "xxyyxx",
      "url": "https://wallhaven.cc/w/xxyyxx",
      "short_url": "https://whvn.cc/xxyyxx",
      "views": 1234,
      "favorites": 56,
      "source": "https://...",
      "purity": "sfw",
      "category": "general",
      "dimension_x": 1920,
      "dimension_y": 1080,
      "resolution": "1920x1080",
      "ratio": "1.78",
      "file_size": 456789,
      "file_type": "image/jpeg",
      "created_at": "2024-01-15 10:30:00",
      "colors": ["#000000", "#ffffff", ...],
      "path": "https://w.wallhaven.cc/full/xx/wallhaven-xxyyxx.jpg",
      "thumbs": {
        "large": "https://th.wallhaven.cc/lg/xx/xxyyxx.jpg",
        "original": "https://th.wallhaven.cc/orig/xx/xxyyxx.jpg",
        "small": "https://th.wallhaven.cc/small/xx/xxyyxx.jpg"
      }
    }
  ],
  "meta": {
    "current_page": 1,
    "last_page": 100,
    "per_page": 24,
    "total": 2400,
    "query": "landscape",
    "seed": null
  }
}
```

Note: Search results do NOT include tag details. To get tags, fetch individual wallpaper info.

## Wallpaper Info Endpoint

```
GET https://wallhaven.cc/api/v1/w/<ID>
GET https://wallhaven.cc/api/v1/w/<ID>?apikey=<KEY>   # for NSFW
```

### Response

Includes everything from search results, PLUS:

```json
{
  "data": {
    "id": "94x38z",
    "url": "https://wallhaven.cc/w/94x38z",
    "short_url": "http://whvn.cc/94x38z",
    "uploader": {
      "username": "test-user",
      "group": "User",
      "avatar": {
        "200px": "https://wallhaven.cc/images/user/avatar/200/...",
        "128px": "...",
        "32px": "...",
        "20px": "..."
      }
    },
    "views": 12,
    "favorites": 0,
    "source": "",
    "purity": "sfw",
    "category": "anime",
    "dimension_x": 6742,
    "dimension_y": 3534,
    "resolution": "6742x3534",
    "ratio": "1.91",
    "file_size": 5070446,
    "file_type": "image/jpeg",
    "created_at": "2018-10-31 01:23:10",
    "colors": ["#000000", "#abbcda", "#424153", "#66cccc", "#333399"],
    "path": "https://w.wallhaven.cc/full/94/wallhaven-94x38z.jpg",
    "thumbs": {
      "large": "...",
      "original": "...",
      "small": "..."
    },
    "tags": [
      {
        "id": 1,
        "name": "anime",
        "alias": "Chinese cartoons",
        "category_id": 1,
        "category": "Anime & Manga",
        "purity": "sfw",
        "created_at": "2015-01-16 02:06:45"
      }
    ]
  }
}
```

## Tag Info Endpoint

```
GET https://wallhaven.cc/api/v1/tag/<TAG_ID>
```

### Response

```json
{
  "data": {
    "id": 1,
    "name": "anime",
    "alias": "Chinese cartoons",
    "category_id": 1,
    "category": "Anime & Manga",
    "purity": "sfw",
    "created_at": "2015-01-16 02:06:45"
  }
}
```

## User Settings Endpoint

Requires API key.

```
GET https://wallhaven.cc/api/v1/settings?apikey=<KEY>
```

### Response

```json
{
  "data": {
    "thumb_size": "orig",
    "per_page": "24",
    "purity": ["sfw", "sketchy", "nsfw"],
    "categories": ["general", "anime", "people"],
    "resolutions": ["1920x1080", "2560x1440"],
    "aspect_ratios": ["16x9"],
    "toplist_range": "6M",
    "tag_blacklist": ["blacklist tag"],
    "user_blacklist": [""]
  }
}
```

## Collections Endpoints

### List own collections (requires API key)

```
GET https://wallhaven.cc/api/v1/collections?apikey=<KEY>
```

### List another user's public collections

```
GET https://wallhaven.cc/api/v1/collections/<USERNAME>
```

### Get wallpapers in a collection

```
GET https://wallhaven.cc/api/v1/collections/<USERNAME>/<COLLECTION_ID>
GET https://wallhaven.cc/api/v1/collections/<USERNAME>/<COLLECTION_ID>?apikey=<KEY>  # private collections
```

Collection listing response is the same as search results (with `data` array and `meta` for pagination). Only the `purity` filter is available when browsing collections.

### Collection List Response

```json
{
  "data": [
    {
      "id": 15,
      "label": "Default",
      "views": 38,
      "public": 1,
      "count": 10
    },
    {
      "id": 17,
      "label": "Another collection",
      "views": 6,
      "public": 1,
      "count": 7
    }
  ]
}
```

## Search Query Syntax

The `q` parameter supports these special operators:

| Syntax | Description | Example |
|--------|-------------|---------|
| `id:<tag_id>` | Exact tag by ID | `id:1` (anime tag) |
| `+tag` | Include tag (AND) | `+landscape +mountain` |
| `-tag` | Exclude tag | `nature -anime` |
| `@username` | Wallpapers uploaded by user | `@test-user` |
| `like:<wallpaper_id>` | Similar wallpapers | `like:94x38z` |
| `type:png` | File type filter | `type:png`, `type:jpg` |

Multiple tag operators can be combined: `+landscape +4k -anime` finds wallpapers tagged both "landscape" AND "4k" but NOT "anime".

When searching for an exact tag (`id:##`), the resolved tag name is provided in `meta.query` if the tag exists.

## Error Codes

| Code | Meaning | Cause |
|------|---------|-------|
| 200  | OK | Successful request |
| 401  | Unauthorized | Invalid API key, or accessing NSFW without key |
| 404  | Not Found | Invalid wallpaper/tag/collection ID |
| 429  | Too Many Requests | Exceeded 45 req/min rate limit |

## Thumbnail & Image URL Patterns

Understanding the URL structure helps when constructing direct links:

- **Full image**: `https://w.wallhaven.cc/full/<first2chars>/<filename>`
- **Large thumb**: `https://th.wallhaven.cc/lg/<first2chars>/<id>.jpg`
- **Original thumb**: `https://th.wallhaven.cc/orig/<first2chars>/<id>.jpg`
- **Small thumb**: `https://th.wallhaven.cc/small/<first2chars>/<id>.jpg`

Where `<first2chars>` is the first two characters of the wallpaper ID.
