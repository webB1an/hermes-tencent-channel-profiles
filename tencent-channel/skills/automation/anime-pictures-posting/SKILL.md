---
name: anime-pictures-posting
description: anime-pictures.net 壁纸手动发帖。从首页"今日最佳"+"本周最佳"中随机选一张壁纸，下载并发到腾讯频道 Wallpaper壁纸库 板块。支持浏览器下载完整图片（agent 模式）或 HTTP 降级下载（cron 模式）。已发帖的 post_id 记录在 state 文件中防重。
category: automation
---

# anime-pictures.net 手动发帖

## 触发词
用户说"anime_pictures 发帖"、"anime-pictures 发帖"、"去 anime-pictures 找张壁纸发"等类似表达时调用。

## 核心逻辑

1. **选图池**：从 anime-pictures.net 首页提取"今日最佳"（12张）和"本周最佳"（12张），合并去重
2. **去重**：读取 `posted_ids` 列表，排除已发帖；另有 `anime_pictures_skip.json` 记录审核时跳过的
3. **抽选**：从剩余候选中 `random.choice()` 抽 1 张
4. **下载**：
   - **agent 模式**（有 browser_tool）：用 `browser_navigate` + `browser_console` JS 提取完整图片 base64
   - **fallback**：`ImageDownloader` 走直接 HTTP 或预览 avif
5. **caption**：用 Google Translate 翻译版权方+角色标签，附分辨率
6. **发帖**：调用 `tencent-channel-cli post`
7. **记录**：发帖后写入 state 文件

## ★ 审核模式（review 模式）

默认触发审核，让用户先看图+文案再决定是否发帖。

### 流程

```
抽图 → 下载图片 → 构造文案 → [暂停，展示给用户]
    ↓
clarify 选择：
  [发帖]  → 执行 publish_post + 更新 state
  [跳过]  → 写入 anime_pictures_skip.json，换抽下一张
  [取消]  → 清理图片，退出
```

### 审核阶段的 JSON 输出

脚本返回 exit code 66，同时 stdout 打印：

```json
{
  "action": "review",
  "post_id": 918541,
  "image_path": "/root/.hermes/profiles/tencent-channel/media/anime-pictures/918541.avif",
  "caption": "🏷️ 蓝色档案 · Asuna (校服)\n📐 分辨率: 2306×3820\n\n#anime-pictures",
  "tags": {
    "copyright": ["blue archive"],
    "character": ["asuna (blue archive)"],
    "artist": ["danha"]
  },
  "resolution": "2306×3820",
  "img_source": "opreviews_cdn",
  "img_size_kb": 20
}
```

### Agent 侧审核实现

> ⚠️ `clarify` 在某些执行上下文（如某些 subagent 上下文）中不可用，报 "not available in this execution context"。此时 fallback 到**纯文本回复**，让用户直接说"发帖"/"跳过"/"取消"。

```python
code = run(args)  # code == 66 表示进入审核

if code == 66:
    import json, re
    # ... 从上下文拿到 review_info ...

    # 展示图片（如需分析，先转 JPEG）
    if review_info["image_path"].endswith('.avif'):
        # 用 hermes-agent venv 的 python3 转换
        subprocess.run([
            "/root/.hermes/hermes-agent/venv/bin/python3", "-c",
            f"from PIL import Image; img=Image.open('{review_info['image_path']}'); img=img.convert('RGB'); img.save('{review_info['image_path']}.jpg','JPEG',quality=90)"
        ], check=True)
        img_for_vision = review_info["image_path"] + ".jpg"
    else:
        img_for_vision = review_info["image_path"]

    vision_analyze(image_url=img_for_vision,
                   question="描述这张图，适合做壁纸吗？有什么需要注意的？")

    # 纯文本审核（clarify 不可用时 fallback）
    print(f"📋 文案预览：\n{review_info['caption']}\n\n🏷️ 版权方={review_info['tags']['copyright']} 角色={review_info['tags']['character']}\n📐 分辨率={review_info['resolution']}\n\n确认发帖还是跳过？回复'发帖'/'跳过'/'取消'")

    # 等待用户输入后：
    # 用户说"发帖" → 调用 do_confirm()
    # 用户说"跳过" → 调用 do_skip(post_id) + 重新 run()
    # 用户说"取消" → 清理图片，return
```

### 辅助函数

```python
def do_confirm(img_path, post_id, feed_id):
    """用户确认后：更新 state，清理图片"""
    from anime_pictures_cold_detect import load_state, save_state
    from datetime import datetime, timezone, timedelta
    state = load_state(cfg.state_file)
    state.setdefault("posted_ids", []).append(str(post_id))
    state.setdefault("self_feed_ids", []).append(feed_id)
    state["last_self_post_ts"] = int(datetime.now(timezone(timedelta(hours=8))).timestamp())
    save_state(cfg.state_file, state)
    try:
        img_path.unlink()
    except OSError:
        pass

def do_skip(post_id):
    """用户跳过：写入 skip 文件，不记 posted_ids"""
    skip_file = Path(cfg.hermes_home) / "anime_pictures_skip.json"
    skips = json.loads(skip_file.read_text()) if skip_file.exists() else []
    skips.append(str(post_id))
    skip_file.write_text(json.dumps(skips))
```

## 关键坑 1：完整图片无法下载（★ 重要 ★）

anime-pictures.net 的图片下载有**三层障碍**：

| 端点 | 访问方式 | 结果 |
|------|----------|------|
| `api.anime-pictures.net/pictures/download_image/` | 需要登录 session | `{error: "need_login"}` |
| `api.anime-pictures.net/pictures/get_image/` | 直接请求 | HTTP 403 |
| `oimages.anime-pictures.net/{md5}.jpg` | 直接请求 | Cloudflare HTML 挑战页 |
| `opreviews.anime-pictures.net/..._bp.avif` | 直接请求 | Cloudflare HTML 挑战页 |

**结论：完整分辨率（2306×3820 等）在本服务器环境无法下载**，需要用户账号登录态。

**能拿到的图片：**
- 帖子页内嵌预览图（浏览器打开帖子页时加载的）：362×600 avif
- URL 规律：`https://opreviews.anime-pictures.net/{md5[:3]}/{md5}_bp.avif`
- 大小：约 20KB，只能看轮廓

**Pillow 转换 avif → JPEG 不可行：**
```python
# PIL 在 hermes-agent venv 中不可用（import PIL 失败）
from PIL import Image  # ❌ ModuleNotFoundError
```
本服务器环境任何 Python venv 的 PIL 都不可用。

**browser_vision 截图 vision_analyze 读不到：**
browser_vision 返回的截图路径（`/root/.hermes/.../screenshot_xxx.png`）对 vision_analyze 不可见（只接受"real image files"）。vision_analyze 只能分析通过 `image_url` 参数传入的图片 URL 或本地绝对路径。

**审核图解决方案（已验证可行）：**
1. 下载 avif 预览到本地路径：`/root/.hermes/profiles/tencent-channel/media/anime-pictures/{post_id}.avif`
2. 将 avif 文件路径直接发给用户（QQ/Telegram 支持 avif 预览）
3. 让用户通过 IM 预览 avif 原文件审核内容（而非通过 vision_analyze）

## 关键坑 2：`clarify` 工具在某些执行上下文不可用

当前 session 中 `clarify` 报 "not available in this execution context"。**必须 fallback 到纯文本审核**：

```
📋 post_id=918331 审核信息
图片内容：[文字描述]
文案：
🏷️ Blue Archive · Asuna (校服)
📐 分辨率: 2306×3820
#anime-pictures

确认发帖还是跳过？回复"发帖"/"跳过"/"取消"
```

## 关键坑 3：首页 JS 渲染

anime-pictures.net 首页的"今日最佳"和"本周最佳"板块**不在原始 HTML 里**，requests 直接拿到的只有 spinner 占位符。必须用 `browser_console` 执行 JS 才能提取到 post_id

```python
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
for (const k in snapshot) snapshot[k] = [...new Set(snapshot[k])];
JSON.stringify(snapshot);
"""
result = json.loads(browser_console(expression=js))
# result = {"day": ["918541", "918498", ...], "week": ["918265", ...]}
```

## 文件位置

- 脚本：`/root/.hermes/profiles/tencent-channel/scripts/anime_pictures_post.py`
- 状态文件：`/root/.hermes/profiles/tencent-channel/anime_pictures_state.json`
- 跳过记录：`/root/.hermes/profiles/tencent-channel/anime_pictures_skip.json`
- 图片缓存：`/root/.hermes/profiles/tencent-channel/media/anime-pictures/`

## 防重机制

- `posted_ids`：已成功发帖的 post_id（永不删除）
- `anime_pictures_skip.json`：审核时用户跳过但未发帖的 post_id（下次抽图跳过这些）

## 关键函数

- `fetch_best_ids_via_browser(browser_console_fn)` — 用 JS 从首页提取 post_id
- `ImageDownloader.download(post_id, md5, ext, file_url)` — 三级下载策略
- `build_caption(tags, translator, resolution)` — 构造 caption
- `publish_post(cfg, image_paths, caption)` — 调用 tencent-channel-cli 发帖
