---
name: wallpaper-posting
description: Execute Wallpaper壁纸库发帖 — downloads 3 random Wallhaven wallpapers, translates tags, and posts to Tencent Channel. Triggered by the phrase "Wallpaper壁纸库发帖".
category: automation
---

# Wallpaper Posting Workflow

Manual trigger for the Tencent Channel wallpaper bot. Posts 3 random SFW wallpapers with translated Chinese tags to the 静态壁纸板块 (channel_id=669891684).

## Scripts

| Action | Script |
|--------|--------|
| Manual post (trigger word) | `wallpaper_post_v2.py` — calls `wallpaper_cold_detect_v2.1.py --force` |
| Auto cold-detect cron | `wallpaper_cold_detect_v2.1.py` (Cron: `0 7-23 * * *`) |

## Execution Steps

1. **Verify syntax** before running:
   ```bash
   python3 -m py_compile /root/.hermes/profiles/tencent-channel/scripts/wallpaper_post_v2.py
   ```

2. **Dry-run first** (optional, to check logic without posting):
   ```bash
   HERMES_HOME=/root/.hermes/profiles/tencent-channel \
     python3 /root/.hermes/profiles/tencent-channel/scripts/wallpaper_cold_detect_v2.1.py --dry-run -v
   ```

3. **Force-run** (skip cold detection, post immediately):
   ```bash
   HERMES_HOME=/root/.hermes/profiles/tencent-channel \
     python3 /root/.hermes/profiles/tencent-channel/scripts/wallpaper_post_v2.py
   ```

## Key Implementation Notes

- **CLI mode matters**: Manual `wallpaper_post_v2.py` uses CLI args mode (`--image flag`). The cron script `wallpaper_cold_detect_v2.1.py` uses stdin JSON mode — must use `file_paths` (not `images`) for image attachment.
- **Translator**: Google Translate first, MiniMax API fallback, English fallback.
- **Tags cache**: `wallpapers/.tags_cache.json` stores wallhaven_id → {tags, resolution} to avoid repeated API calls.
- **Posted IDs**: `wallpaper_state.json` prevents duplicate wallpaper posting across runs.
- **Cold detection logic**: Only cron uses it. Script's own posts are excluded from cold判定 via feed_id match + timestamp tolerance (±90s).

## Pitfalls

- **`--json` flag must NOT be passed** in `call()`: `cmd = [binary, domain, action]` (no `"--json"`). The `--json` flag tells the CLI to read JSON from stdin, BUT when present, the CLI does NOT accept any stdin data at all — the payload is silently discarded. All caller scripts must omit `--json` and rely on the CLI's default stdin-reading behavior. Both `wallpaper_cold_detect_v2.1.py` and `sync_jandan_treehole.py` had this bug; fixed by removing `"--json"` from the cmd list.
- `images` field in stdin JSON does NOT work — must be `file_paths: [{"file_path": "/path"}]`
- When editing `wallpaper_cold_detect_v2.1.py`, always verify syntax with `py_compile` before suggesting a run
- If user says "看不到内容" after a message, the content was likely just trimmed by their UI — re-sending the same info usually works
