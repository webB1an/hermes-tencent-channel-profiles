---
name: tencent-channel-search-enrich
description: 腾讯频道帖子数据归因与富化——搜索结果中 prefer_count 为0，需逐帖调用 get-feed-detail 获取真实点赞/评论数。搜索 → 去重 → 批量富化 → 合并呈现的完整工作流。
version: 1.0.0
tags: ["tencent-channel", "data-gathering", "api-quirk"]
category: social-media
---

# 腾讯频道帖子数据富化工作流

## 核心问题

`feed.search-guild-feeds` 返回的帖子列表中，`prefer_count`（点赞数）**始终为 0**，`comment_count` 也可能不准确。这是搜索接口的简化摘要数据。

**解决方案：** 对每个 `feed_id` 调用 `feed.get-feed-detail` 获取完整数据。

## 完整工作流

### 1. 搜索帖子
```bash
tencent-channel-cli feed search-guild-feeds \
  --guild-id <频道ID> --query "<关键词>" --json
```

返回字段（含局限性）：
- `feed_id` ✅ 可用
- `title` ✅ 可用
- `author` ✅ 可用
- `create_time` ✅ 可用
- `create_time_raw` ✅ 可用（时间戳，过滤用）
- `prefer_count` ❌ 始终为 0
- `comment_count` ❌ 可能为 0

### 2. 时间范围过滤
```python
import datetime
tz = datetime.timezone(datetime.timedelta(hours=8))
start_ts = datetime.datetime(2026, 5, 18, 0, 0, 0, tzinfo=tz).timestamp()
end_ts   = datetime.datetime(2026, 5, 21, 23, 59, 59, tzinfo=tz).timestamp()

if start_ts <= feed["create_time_raw"] <= end_ts:
    # 在时间范围内
    pass
```

### 3. 去重
同一帖子可能多次出现在搜索结果中（跨页或重复索引），按 `feed_id` 去重：
```python
seen = set()
unique_feeds = [f for f in feeds if f["feed_id"] not in seen and not seen.add(f["feed_id"])]
```

### 4. 批量获取详情（富化）
```python
import subprocess, json, time

details = {}
for f in unique_feeds:
    result = subprocess.run(
        ["tencent-channel-cli", "feed", "get-feed-detail",
         "--feed-id", f["feed_id"], "--json"],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    if data.get("success"):
        feed = data["data"]["feed"]
        details[f["feed_id"]] = {
            "prefer_count": feed.get("prefer_count", 0),
            "comment_count": feed.get("comment_count", 0),
        }
    time.sleep(0.3)  # 避免频率限制
```

### 5. 合并数据并呈现
```python
for f in unique_feeds:
    detail = details.get(f["feed_id"], {})
    f["prefer_count"] = detail.get("prefer_count", 0)
    f["comment_count"] = detail.get("comment_count", 0)
```

### 已知坑

1. **prefer_count 为 0**：搜索结果不可用，必须富化
2. **帖子已删除**：返回 `retCode=10014`，跳过后不影响整体统计
3. **频率限制**：批量查询时加 `sleep(0.3)` 间隔
4. **翻页**：搜索结果 `has_more=true` 时用 `next_page_cookie` 翻页，最多约 15 页
5. **feed_id 格式**：通常为 `B_` 开头长字符串

## 验证方法
```bash
# 检查单帖详情（确认 prefer_count 非 0）
tencent-channel-cli feed get-feed-detail --feed-id <feed_id> --json
```
