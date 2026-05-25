#!/usr/bin/env python3
import json, os, subprocess, time

posted = set(json.load(open('live_wallpaper_state.json'))['posted_detail_urls'])
fp = 'scripts/live-wallpaper-download/downloads/'
manifest = json.load(open('scripts/live-wallpaper-download/config/manifest.json'))

name_to_url = {}
for x in manifest:
    name = x.get('name', '')
    url = x.get('detailUrl', '')
    if name and url:
        name_to_url[name] = url

files = sorted(os.listdir(fp))
print(f'待发帖: {len(files)} 个')

state_file = 'live_wallpaper_state.json'

for f in files:
    name = f.replace('.mp4', '')
    url = name_to_url.get(name, '')
    video_path = os.path.abspath(fp + f)

    if url and url in posted:
        print(f'⏭ 已发帖: {name}')
        os.remove(fp + f)
        continue

    size_mb = os.path.getsize(video_path) // 1024 // 1024
    print(f'📤 [{size_mb}MB] {name}...', end='', flush=True)

    r = subprocess.run([
        'tencent-channel-cli', 'feed', 'publish-feed',
        '--guild-id', '652812504031889164',
        '--channel-id', '667049126',
        '--video', video_path,
        '--title', name
    ], capture_output=True, text=True, timeout=300)

    if r.returncode == 0 and '"success":true' in r.stdout:
        print(f' ✅')
        if url:
            s = json.load(open(state_file))
            if url not in s['posted_detail_urls']:
                s['posted_detail_urls'].append(url)
            with open(state_file, 'w') as f2:
                json.dump(s, f2, indent=2, ensure_ascii=False)
        os.remove(fp + f)
    else:
        err = 'rate limit'
        try:
            err = json.loads(r.stdout).get('error', {}).get('message', err)
        except Exception:
            pass
        print(f' ❌ {err}')
        if '20063' in r.stdout or '过于频繁' in r.stdout:
            print('    等待65秒冷却...')
            time.sleep(65)

    time.sleep(3)

print('完成！')
