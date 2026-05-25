---
name: wallpaper-translate
description: Translate Wallhaven wallpaper tags to Chinese for automated posting workflows. Covers Google Translate (primary) + MiniMax (fallback) translation chain with retry logic.
category: wallpaper
---

# Wallpaper Tag Translation

Translates Wallhaven wallpaper English tags to Chinese using a two-tier fallback chain.

## Translation Chain

```
translate_tags(tags)
  ├─ google_translate_tags()   ← ~80ms, no key, stable
  │    └─ returns None on failure
  └─ minimax_translate_tags()  ← backup, needs MINIMAX_API_KEY
       └─ returns None on failure
  └─ fallback: return raw English tags
```

## Google Translate (Primary)

**URL**: `https://translate.googleapis.com/translate_a/single`

**Advantages**:
- ~80ms latency (5x faster than MyMemory)
- No API key required
- Stable in China (tested 200 OK consistently)
- Simple JSON response format

**Python implementation**:
```python
import urllib.parse, urllib.request, json

def google_translate_tags(tags, target="zh-CN"):
    if not tags:
        return ""
    text = ",".join(tags)
    url = (f"https://translate.googleapis.com/translate_a/single"
           f"?client=gtx&sl=en&tl={target}&dt=t&q={urllib.parse.quote(text)}")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        parts = [t[0] for t in data[0] if t[0]]
        return " · ".join(parts)
    except Exception:
        return None
```

**Separators**: Google uses `、` (ideographic comma) or `,` — join with ` · ` for consistency.

## MiniMax API (Fallback)

Used when Google Translate fails. Requires `MINIMAX_API_KEY` from profile `.env` (`MINIMAX_CN_API_KEY`).

**Endpoint**: `https://api.minimaxi.com/v1/chat/completions`
**Model**: `MiniMax-M2.7`

```python
def minimax_translate_tags(tags):
    tags_str = ",".join(tags)
    prompt = (
        "Translate the following English tags to Chinese (Simplified). "
        "Return only the translated tags separated by ' · ', keep the order. "
        f"Do not add any explanation.\n\nTags: {tags_str}"
    )
    try:
        req = Request(
            f"{MINIMAX_BASE_URL}/chat/completions",
            data=json.dumps({
                "model": "MiniMax-M2.7",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 1.0,
            }).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {MINIMAX_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
        # Strip reasoning tags
        content = re.sub(r"^<think>.*?</t\w*>", "\n", content, flags=re.DOTALL)
        content = re.sub(r"^<reasoning>.*?</reasoning>", "\n", content, flags=re.DOTALL)
        content = re.sub(r"<[^>]+>", "", content).strip()
        return content if content else tags_str
    except Exception as e:
        print(f"[WARN] MiniMax 翻译失败: {e}")
        return None
```

## Translation Failure Detection

After translation, detect when both Google and MiniMax failed by comparing the result to the original English:

```python
if translated == ",".join(tags):
    print(f"[WARN] 标签翻译失败（退英文）: {tags}")
```

This works because the fallback returns `",".join(tags)` unchanged.

## Tags Caching

Wallhaven `search` endpoint does **NOT** return tags — `tags` field is always `null`. Each `wallpaper_info` call is mandatory per image. To avoid redundant API calls on re-runs, cache tags by wallpaper ID:

```python
CACHE_PATH = Path("~/.hermes/profiles/tencent-channel/wallpapers/.tags_cache.json").expanduser()

def _load_cache():
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}

def _save_cache(cache):
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False))

def _get_wallpaper_tags(wid):
    cache = _load_cache()
    if str(wid) in cache:
        return cache[str(wid)]["tags"], cache[str(wid)]["resolution"]
    info = wallhaven.wallpaper_info(wid)
    tags = [t["name"] for t in info.get("tags", []) if t.get("name")]
    resolution = f"{info.get('width','')}x{info.get('height','')}"
    cache[str(wid)] = {"tags": tags, "resolution": resolution}
    _save_cache(cache)
    return tags, resolution
```

Cache file: `~/.hermes/profiles/tencent-channel/wallpapers/.tags_cache.json` (profile-anchored).

## Unified translate_tags()

```python
def translate_tags(tags):
    if not tags:
        return ""
    result = google_translate_tags(tags)
    if result:
        return result
    result = minimax_translate_tags(tags)
    if result:
        return result
    return ",".join(tags)
```

## Scripts Using This Pattern

- `/root/.hermes/profiles/tencent-channel/scripts/wallpaper_post.py` — manual wallpaper post
- `/root/.hermes/profiles/tencent-channel/scripts/wallpaper_cold_detect.py` — hourly cold-detection auto-post

Both scripts share identical translation logic.
