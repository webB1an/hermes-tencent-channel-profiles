---
name: debug-tencent-channel-cli
description: 调试 tencent-channel-cli 发帖失败（exit=2 stderr=空）的诊断方法。当 ChannelCLI.call() 返回 exit=2 但 stderr 为空时，实际错误 JSON 在 stdout 中，而非 stderr。
homepage: 
version: 1.0.0
metadata: {}
---

# 调试 tencent-channel-cli 发帖失败

## 典型症状

Python 脚本调用 `tencent-channel-cli` 时报告：
```
ERROR 发帖失败 xxx: exit=2 stderr=
```

`stderr` 为空，但发帖确实失败了。

## 根本原因

`tencent-channel-cli` 的行为特征：
- **无论成功还是失败，结果 JSON 都输出到 stdout**
- 非零 exit code 表示 CLI 层失败（如网络、超时、参数格式）
- stderr 在大多数情况下为空
- 当 exit=2 时，**实际错误信息在 stdout 的 JSON 中**，字段为 `error.message`

## 调试步骤

```bash
# 1. 提取 stdout 中的 error.message
echo '{"guild_id": "...", "channel_id": "...", "content": "test", "file_paths": []}' | \
  HERMES_HOME=/root/.hermes/profiles/tencent-channel \
  tencent-channel-cli feed publish-feed --json --yes 2>&1 | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('message',''))"

# 2. 确认认证有效
HERMES_HOME=/root/.hermes/profiles/tencent-channel \
  HOME=/root/.hermes/profiles/tencent-channel/home \
  tencent-channel-cli token verify --json

# 3. 检查 HERMES_HOME 和 HOME 环境变量
# ChannelCLI._env() 设置 HERMES_HOME 和 HOME（指向 profile 的 home/ 子目录）
# 凭证在 profile/home/.qqcli/.env 中
```

## 常见错误码

| 错误码 | 含义 | 解决方案 |
|--------|------|---------|
| 153 | API 频率超限 | 等待 1-5 分钟后再试 |

**exit=124 超时叠加效应**：当 retry 队列有 47+ 条卡死条目时，每次重试都要遍历全部卡死条目并逐条调用 CLI，触发 rate limit 后等待 5~15s 再重试。3 次重试 × N 条卡死条目 × 等待时间 = 总耗时极易超过 120s 脚本超时。表现为 `exit=2 stderr=` 反复出现，最终整体超时 `exit=124`。清理 retry 队列（`0 < v < 3` → `-1`）是解决此类超时的最优先步骤，先于任何网络排查。
| 8010 | guild_id 精度丢失（float→int） | 确认 guild_id 为纯数字字符串 |
| 8011 | 认证失败 | 重新运行 `tencent-channel-cli token setup` |

## 关键代码位置

`ChannelCLI.call()` in `wallhaven_cold_detect_v2.2.py` 第 587-616 行：
```python
code, out, err = self.call(...)
if code != 0:
    return False, "", "", f"exit={code} stderr={err[:200]}"
# ↑ 失败时 err 通常为空，错误在 out 中
```

## 直接运行脚本时的环境变量

当手动运行 `sync_jandan_treehole.py`（而非通过 cron）时，**必须同时设置 `HERMES_HOME` 和 `HOME`**：

```bash
cd /root/.hermes/profiles/tencent-channel
HOME=/root/.hermes/profiles/tencent-channel/home \
HERMES_HOME=/root/.hermes/profiles/tencent-channel \
python3 scripts/sync_jandan_treehole.py
```

若只设置 `HERMES_HOME` 而不设 `HOME`，`tencent-channel-cli` 找不到 QQ 凭证（`~/.qqcli/.env`），表现为 exit=2 + stderr 空。cron 任务通常已通过系统服务配置正确继承 `HOME`，故此问题仅在直接执行时出现。

## 重试队列卡死问题（煎蛋树洞同步脚本特有问题）

**症状**：有 43 条帖子 `retry>0`（重试次数 1-2），但永远不会被重试。

**根本原因**：`sync_jandan_treehole.py` 中的过滤逻辑：

```python
if c_dt > cutoff_dt and cid not in synced:
    all_new_comments.append(c)
```

- `cid not in synced` 只检查 ID 是否存在，**不区分 retry>0（失败待重试）还是 -1（成功/跳过）**
- retry>0 的条目已存在于 `synced`，所以被当作旧评论跳过，永远得不到重试机会
- 除非下一周期煎蛋 API 重新返回这些帖子（按时间窗口它们已是旧帖，不会再出现）

**状态文件中的 retry 计数含义**：
| 值 | 含义 |
|---|---|
| `-1` | 成功发布，或过滤跳过（已终态） |
| `0` | 未处理（新帖子） |
| `1-2` | 发布失败，正在重试（**卡死风险**） |
| `≥3` | 3次重试全部失败，标记为跳过（已终态） |

**解决方案**：运行脚本前，手动清理 retry 队列：
```bash
HERMES_HOME=/root/.hermes/profiles/tencent-channel \
HOME=/root/.hermes/profiles/tencent-channel/home \
python3 -c "
import json
from pathlib import Path
f = Path('/root/.hermes/profiles/tencent-channel/state/.jandan_synced_ids.json')
d = json.loads(f.read_text())
stuck = {k: v for k,v in d.items() if isinstance(v, int) and 0 < v < 3}
print(f'清理 {len(stuck)} 条卡死重试条目')
for k in stuck:
    d[k] = -1  # 降级为跳过，不重复发帖
f.write_text(json.dumps(d, ensure_ascii=False, indent=2))
"
```

在日志中同时记录 `out` 和 `err`：
```python
if code != 0:
    err_msg = err[:200] if err else ""
    out_msg = json.loads(out).get("error", {}).get("message", "") if out else ""
    return False, "", "", f"exit={code} err={err_msg} out_err={out_msg}"
```
