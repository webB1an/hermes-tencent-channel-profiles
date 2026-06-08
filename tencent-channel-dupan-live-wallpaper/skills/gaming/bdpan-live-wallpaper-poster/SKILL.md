---
name: bdpan-live-wallpaper-poster
description: 百度网盘动态壁纸发帖机器人 - bdpan search 下载 mp4 → 腾讯频道发帖 → 飞书通知
---

# bdpan 动态壁纸发帖机器人

## 快速开始
```bash
cd /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper
python3 scripts/bdpan_wallpaper_post.py
```

## 触发词
`发动态壁纸` → 执行上述脚本（完整流程：下载→发帖→删除→飞书通知）

## Profile 信息
- Profile 名：`tencent-channel-dupan-live-wallpaper`
- HERMES_HOME：`/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper`

## 核心组件路径
| 组件 | 路径 |
|------|------|
| 发帖脚本 | `scripts/bdpan_wallpaper_post.py` |
| bdpan CLI | `home/.local/bin/bdpan` |
| Channel token | `home/.qqcli/.env` |
| 状态文件 | `live_wallpaper_state.json` |
| 下载目录 | `bdpan-downloads/` |

## 关键技术细节

### bdpan 搜索（流式）
```bash
HOME=/root /path/to/bdpan search "mp4" --category 1 --page-size 50 --page N --json
```
- `category 1` = 文件，按更新时间排序
- 流式找第一个未发帖的 mp4，找到即返回，不扫描全量
- ~50条/页，总共约150个 mp4

### bdpan 下载
```bash
HOME=/root /path/to/bdpan download "动态壁纸/其他/武器/Katana.mp4" "/tmp/Katana.mp4"
```
- **路径格式**：远端搜索结果返回 `/apps/bdpan/动态壁纸/...`，但 download 命令只接受**相对路径**（去掉 `/apps/bdpan/` 前缀）
  - ❌ 错误：`bdpan download "/apps/bdpan/动态壁纸/xxx.mp4" /tmp/xxx.mp4` → 路径穿越攻击被阻止
  - ✅ 正确：`bdpan download "动态壁纸/xxx.mp4" /tmp/xxx.mp4`
- **不要传 `--transfer-dir ""`**：该 flag 在某些版本会导致路径校验失败
- 先下载到 `/tmp` 再 `shutil.move` 到目标（避免文件系统同步延迟）
- 下载速度约 80KB/s，95MB 文件约需 20 分钟，terminal 180s SIGKILL 硬限制，必须 background 模式
- bdpan **不支持断点续传**：.bdpan 残留文件无用，下次会从头下载，无需清理（删了也没用）

### 锁文件与僵尸进程
- 锁路径：`/tmp/bdpan_wallpaper_post.lock`
- 若脚本异常退出（被 kill/timeout），锁文件可能残留导致后续启动直接退出
- 症状：日志停在"开始下载..."后无任何输出，进程存在但什么都不做
- 处理：手动 `rm -f /tmp/bdpan_wallpaper_post.lock` 后重跑
- 多个 bdpan 下载实例同时跑会争抢资源，下载速度叠满也不更快，优先杀掉多余进程

### ChannelCLI._env()
```python
def _env(self) -> dict:
    env = os.environ.copy()  # 保留 PATH!
    env["HERMES_HOME"] = str(self.cfg.hermes_home)
    env["HOME"] = str(self.cfg.hermes_home / "home")
    return env
```
- 关键：`os.environ.copy()` 已保留 PATH，不要再手动覆盖 PATH
- `HOME` 必须指向 `hermes_home/home`（含 `.qqcli/.env` bot token）
- `HERMES_HOME` 显式设

### 发帖后飞书通知
- Feishu API：`POST https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id`
- `receive_id_type` 必须放 URL，不能放 body
- `content` 是字符串（不是 JSON 字符串）

## 文案处理逻辑

`bdpan_wallpaper_post.py` 第 588-603 行：

```python
name_en = extract_name(filename)   # 去掉扩展名
if is_likely_english(name_en):      # 拉丁字母 > 50% 才翻译
    name_zh = self.translator.translate(name_en)
    title = f"{name_en} · {name_zh}"
else:
    title = name_en                  # 非英文直接用原名
```

- `is_likely_english()`: 统计字符串中拉丁字母占比，超过 50% 判定为英文，需要翻译
- 非英文/非拉丁字母文本（如中文文件名）跳过翻译，避免 Google Translate 把中文当英文翻译产生不可控结果

## 下载返回值约定（关键）

`download()` 返回三元组 `(ok, local_path)`，caller 必须按顺序判断：

```python
ok, local_path = self.bdpan.download(remote_path, ...)
if local_path == Path():
    # 跳过（如文件名含 .... 无法下载）
    self.store.mark_posted(remote_path)
    return self.run_download()
if not ok:
    # 真正失败
    self.store.remove_pending(remote_path)
    return 3
```

## 正确做法：download 必须 background=true（必须！）

bdpan 下载必须用 `background=true` 模式，这是**强制要求**，不是可选优化：

- terminal tool 有 **180s 内部 hard limit**（`wait()` 超时会被系统 SIGKILL），`process(wait(timeout=N))` 实际等待不超过 180s
- 典型下载：95MB @ 80KB/s ≈ 20 分钟，必然超时
- **不要调 `process(wait())`** —— 调了反而被 kill。设 `notify_on_complete=True` 等系统通知即可
- timeout kill 后 `.bdpan` 残留 + pending 里该文件已标记，下次 cron 重跑同一个（因为 `iter_mp4_newest_first` 会跳过 pending）
- `background=true` 完全绕过 180s 限制，下载在独立进程运行，不受 terminal session 约束

**操作步骤**：

1. 用 `background=true` 触发下载（不是直接跑脚本！）

```bash
terminal(
    command="cd /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper && "
            "HERMES_HOME=... BDPAN_BIN=... python3 scripts/bdpan_wallpaper_post.py download -v",
    background=True,
    notify_on_complete=True,
    watch_patterns=["完成", "ERROR", "下载失败", "posted_detail_urls", "异常"]
)
```

2. 等 `notify_on_complete` 通知（下载完成后 watcher 会自动发帖，也可以手动跑 `watch` 子命令验证）
3. 若下载失败，先 `rm -f /tmp/*.bdpan` 清理残留，再重新 background 下载

---

## 架构：下载与发帖彻底分离（Plan B）

**download 子命令**（手动触发，cron 也跑）：
- 扫描网盘 → 找第一个未发帖且不在 pending 的 mp4
- **先写入 `pending_downloads`**（下载前就标记）→ 下载到 `bdpan-downloads/` → 退出
- **不等发帖**，不怕超时
- 若进程被 kill：pending 保留，下次 `iter_mp4_newest_first` 会跳过该 pending，下次 cron 重跑同一个文件

**watch 子命令**（cron 每 5 分钟自动跑）：
- 扫描 `bdpan-downloads/` → 有 mp4 则发帖 → 删文件 → `mark_posted` → `remove_pending`
- **不依赖 pending 匹配文件**：任何 mp4 都能处理，只靠 `is_posted()` 防重发帖
- 完全独立，不受下载速度影响

### 状态文件

```json
{
  "posted_detail_urls": ["/apps/bdpan/动态壁纸/其他/武器/Katana.mp4", ...],
  "pending_downloads": [
    {"path": "/apps/bdpan/动态壁纸/游戏/战双/露西娅/Lucia.mp4", "server_filename": "Lucia.mp4", "size": 27842560}
  ]
}
```

- `posted_detail_urls`：已发帖完成的网盘路径（**不是 share_url**）
- `pending_downloads`：正在下载的队列（download 前写入，watch 消费后删除）
- `add_pending` 有去重逻辑，重复添加同一个 path 不会生效
- `iter_mp4_newest_first` 会跳过已在 `pending_downloads` 中的文件（避免进程被 kill 后重复选同一个）
- `mark_posted` 只在 watcher 确认发帖成功后调用，彻底避免重复发帖

## 验证发帖结果（排查问题用）

```bash
# 查看频道最近帖子（最可靠的发帖验证）
HERMES_HOME=/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper \
HOME=/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/home \
tencent-channel-cli feed get-channel-timeline-feeds \
  --json --channel-id 667049126 --guild-id 652812504031889164 | \
python3 -c "
import sys, json
d = json.load(sys.stdin)
feeds = d.get('data', {}).get('feeds', [])
print(f'Total feeds: {len(feeds)}')
for f in feeds:
    t = f.get('title', '') or f.get('content_snippet', '')[:60]
    print(f'  [{f[\"create_time\"]}] {t}')
"
```

- `get-channel-timeline-feeds` 返回真实发帖记录（比 state 文件更权威）
- state 文件只记录网盘路径，不记录 share_url
- 若 state 有记录但 channel timeline 无对应帖子 → 可能是发帖 API 成功但实际未显示

## 已知坑

1. **terminal tool 180s hard limit — 必须用 background=true（强制！）**：bdpan 下载前台运行必然超时，`.bdpan` 残留导致文件被跳过。必须用 `background=true` + `notify_on_complete=True` + `watch_patterns`。见上方「正确做法」章节。
   **正确做法**：用 `background=true` 把整个下载任务丢到后台，不受 terminal timeout 限制：

   ```python
   terminal(
       command="cd /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper && "
               "HERMES_HOME=... BDPAN_BIN=... python3 scripts/bdpan_wallpaper_post.py download -v",
       background=True,
       notify_on_complete=True,    # 完成后自动通知
       watch_patterns=[            # 监控关键字，匹配到任一关键字时立即通知
           "完成", "ERROR", "下载失败", "posted_detail_urls", "异常"
       ]
   )
   ```
   - `notify_on_complete=True` + `watch_patterns`：后台进程输出匹配到任一关键字时立即通知，不需要等进程结束
   - 进程结束后系统自动通知（不论是否匹配到关键字）
   - 被 timeout kill 的进程：`.bdpan` 临时文件残留在 `/tmp/`，需手动清理后重跑
   - 查后台进程状态：`process(action="poll", session_id="proc_xxx")`
   - 完整查日志：`process(action="log", session_id="proc_xxx")`

2. **进程被 kill 后 pending 保留，下次 cron 重跑同一个文件**：download 进程被 kill 后，`pending_downloads` 里仍有该文件记录，`iter_mp4_newest_first` 会跳过它，下一次 cron download 会重选同一个。这是**预期行为**，不是 bug。bdpan 不支持断点续传，所以会重新从头下。

2b. **Stale lock 导致脚本直接退出（无声静默失败）**：若进程被 SIGKILL，`/tmp/bdpan_wallpaper_post.lock` 残留且指向已死亡的 PID。下次调用 `bdpan_wallpaper_post.py` 时 `fcntl.flock` 成功获取锁（因为 PID 已死，内核已释放），但 fcntl 在 flock 时发现文件存在就报错退出。症状：cron 每5分钟触发，但日志完全无输出——脚本在进程锁检查时直接退出，返回码 0。诊断：`ls -la /tmp/bdpan_wallpaper_post.lock` + `fuser /tmp/bdpan_wallpaper_post.lock` 查是否有过期 PID。处理：`rm -f /tmp/bdpan_wallpaper_post.lock`。

2c. **duplicate 脚本在另一个 profile**：`bdpan_wallpaper_post.py` 存在于 `tencent-channel-live-wallpaper/scripts/`（旧副本，无 cron）和 `tencent-channel-dupan-live-wallpaper/scripts/`（当前使用的）。不要混淆。

3. **done 写在下载后导致文件遗漏（旧版 bug，已修复）**：旧版 `add_pending` 在下载**成功后才写入**，若进程在下载完成后、写入 pending 前被 kill，该文件在网盘上未标记，下次 cron 又会选中同一个，造成重复下载。Plan B 把 `add_pending` 改到下载**前**，彻底解决这个问题。

4. **bdpan download 不能传绝对路径**：必须传相对路径（去掉 `/apps/bdpan/` 前缀）。直接传 `/apps/bdpan/动态壁纸/xxx.mp4` 会触发"路径穿越攻击被阻止"错误。正确用法：`bdpan download "动态壁纸/xxx.mp4" /tmp/xxx.mp4`

5. **--transfer-dir "" 禁用转存子目录**：否则默认会建 `./日期/文件名` 结构

6. **HOME 必须加**：终端里 HOME 不是 /root，bdpan 读不到 token

7. **100MB→300MB 过滤**：搜索时过滤 >300MB 文件（代码 `MAX_SIZE_BYTES`），大文件下载耗时长（80KB/s 下 95MB 约需 20 分钟）

8. **ChannelCLI._env() 不能清 PATH**：用 `os.environ.copy()` 而不是手动拼 env

9. **文件名中文 bug**：非英文文件名会被送去翻译 → ✅ 已修复（`is_likely_english` 判断）

10. **别混淆 profile**：有两个wallpaper profile：
    - `tencent-channel-live-wallpaper` — wallpaperWaifu/MoeWalls/desktopHut 源（触发词是"Wallpaper动态壁纸发帖"）
    - `tencent-channel-dupan-live-wallpaper` — bdpan 网盘源（**触发词是"发动态壁纸"**）

11. **登录失效后 cron 空转发假象**：bdpan login 失效后，`iter_mp4_newest_first` 返回空（所有页都无结果），但 pending 队列中的文件仍会被 watch 处理，导致"一直在发动态壁纸"——实际是 cron 在反复处理相同的 pending 文件，并非新文件。

12. **pending 文件无存活上限导致堆积**：若 download 因 login 失效而失败，pending 中的文件永远不会被清理，`iter_mp4_newest_first` 每次都跳过它。**已修复**：pending 加 `pending_at` 字段，1小时超时自动清理（`_cleanup_stale_pending(max_age_seconds=3600)` 在 `run_download`/`run_watch` 开头调用）。

14. **锁文件残留诊断流程**：
```bash
# 1. 检查锁文件是否残留
ls -la /tmp/bdpan_wallpaper_post.lock

# 2. 查看锁文件指向的PID（fuser 即使进程已死也会显示）
fuser /tmp/bdpan_wallpaper_post.lock

# 3. 检查该PID是否存活
ps aux | grep PID

# 4. 查看哪些进程持有文件锁（/proc/locks 更权威）
cat /proc/locks | grep bdpan_wallpaper

# 5. 找到持有锁的进程的文件描述符
ls -la /proc/<PID>/fd/ | grep lock

# 6. 清理并重跑
rm -f /tmp/bdpan_wallpaper_post.lock
python3 scripts/bdpan_wallpaper_post.py watch
```

15. **bdpan login 过期时的精确诊断**：执行 `bdpan whoami` 返回"⚠ 未登录"表示 cookie/token 已过期，需要重新 `bdpan login`。此时 `bdpan search` 和 `bdpan ls` 都会返回 `{"code": 1, "error": "请先执行 bdpan login 命令"}`。登录失效不会触发脚本报错，只会导致 search 返回空，cron 空转。

### Hermes cron 是 LLM 驱动的（关键发现 2026-05-27）
cron scheduler 不是直接跑 subprocess，而是把 prompt 发给 `AIAgent`，LLM 有完全的自主决策权。这意味着：
- 同一个 cron prompt，每次运行结果可能不同（取决于 LLM 当次决策）
- LLM 可能在 watch 模式下**自主决定**切换到 `download` 子命令（当下载目录为空时）
- 安全审批（`tirith:pipe_to_interpreter`）可能随机拦截某些 LLM 行动，导致同一 cron session 有的能下载有的不能

### 两次发帖的触发机制
- **白天（19:50）**：独立后台进程（`python3 ... download`，PID 1710201）在 19:43 启动，cron watch 在 19:50 检测到下载完成并发帖。独立进程的来源可能是：手动触发、systemd service、或其他 cron job。
- **个性380（21:32）**：cron session (21:30:30) 的 LLM 自主从 watch 切换到 download → bdpan search → 下载 → 发帖。20:30 同类型的 session 曾被安全审批拦截，没有触发 download。

### 防止重复发帖的方法
当前系统的 watch-only cron **不能保证只发一张**。如需严格控制：
1. 方案A：在脚本 `run_watch` 里加 guard，当 `pending_downloads` 为空时直接退出，禁止触发 download
2. 方案B：cron prompt 明确写 "只执行 watch，不调用 download 子命令，不要主动搜索网盘"
3. 方案C：分离 watch 和 download 为两个独立 cron job，download cron 触发后自动 disable 下一轮

## ⚠️ 关键警告：Hermes Cron 是 LLM 自主执行，不是直接 subprocess 调用

**发现于 2026-05-27**：当你配置 cron job prompt 为 `python3 .../bdpan_wallpaper_post.py watch`，Hermes 的调度器**不是直接调用 subprocess** 执行这条命令，而是：
1. 启动一个新的 LLM Agent 会话（MiniMax-M2.7）
2. 把这条 prompt 作为 user message 发给 LLM
3. **由 LLM 自主决定怎么执行**（包括它可以自行判断并切换子命令）

**实际影响**：当 LLM 收到 `python3 ... watch` 指令时，如果它发现：
- 下载目录为空
- `posted_detail_urls` 里显示有个性380未发帖
- 它会去读 `bdpan_wallpaper_post.py` 的代码逻辑

LLM 看到空目录+有 pending 的未发帖文件 → 自行决定从 `watch` 切换到 `download` 子命令 → 执行下载 → 发帖。

**这意味着**：
- watch cron 严格来说**不保证只 watch 不下载** — LLM 有自主决策权
- 两个 cron session 的 LLM 可能行为不同（有的更激进，有的更保守）
- 21:30 的 cron session 执行了完整 download→watch 流程，但 21:20、21:26 没有（不同 LLM 实例的决策不同）

**正确的架构理解**：
- `watch` cron job 的 prompt 传入后，LLM 看到空目录会自己决定切 download
- 这其实是合理行为，但可能不符合"只监控已下载文件"的预期

**如需严格区分 watch/download 为两个独立 cron job**，需要：
- 两个独立 job，prompt 明确说"只执行 watch，不要执行 download"
- 或者修改代码逻辑，让 watch 子命令不包含 download 的代码路径

## 调试技巧：查 cron 执行历史（SQLite）

cron 每次运行都会在 `state.db` 的 `sessions` 和 `messages` 表留下完整记录：

```python
import sqlite3
db = '/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/state.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

# 找到某天的 watch cron sessions
cur.execute("""
    SELECT id, started_at, ended_at, message_count 
    FROM sessions 
    WHERE source='cron' AND id LIKE '%31b23f845072%20260527%'
    ORDER BY started_at
""")
for row in cur.fetchall():
    print(row)

# 查看某个 session 的完整消息链
cur.execute("""
    SELECT id, role, substr(content, 1, 300), tool_name 
    FROM messages 
    WHERE session_id='cron_31b23f845072_20260527_213030'
    ORDER BY id
""")
for row in cur.fetchall():
    print(f'ID={row[0]} role={row[1]} tool={row[3]}')
    print(f'  {repr(row[2][:300])}')

conn.close()
```

**调查过程**：
1. 发现某次 cron（21:32）发了个性380 → 查 `cron_31b23f845072_20260527_213030` session
2. 工具调用链显示：LLM 先跑 `watch`（空目录）→ 自行决定切 `download` → 下载 → 再跑 `watch` → 发帖
3. 全程由 LLM 自主决策，不是 cron 直接调 subprocess

## 当前问题：bdpan login 已过期

```bash
HOME=/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/home \
  /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/home/.local/bin/bdpan whoami
# 返回：⚠ 未登录
```

需要重新 `bdpan login` 才能恢复下载功能。

## ⚠️ 关键警告：下载与发帖调度必须同时配置（发现于 2026-05-25）

```bash
# jobs.json 中的 watch job（id: 31b23f845072）
# schedule: */5 * * * *
# prompt: python3 .../bdpan_wallpaper_post.py watch
```

⚠️ 注意：`hermes cron list` 的输出**不包含** id `31b23f845072`（显示过滤问题），但 jobs.json 中确实存在。

## ⚠️ 关键警告：下载与发帖调度必须同时配置（发现于 2026-05-25）

**当前问题（此 profile 独有）**：只有 `watch` cron job，没有 `download` cron job。

- `watch` 每5分钟扫描 `bdpan-downloads/` 找 `.mp4` 文件来发帖
- `download` **必须手动触发**（或由其他机制触发），否则新壁纸永远不会从网盘拉下来
- 结果：watch 每次都报"下载目录暂无 mp4"，一直在空转

**症状**：cron output 显示每次都是"下载目录暂无 mp4，等待下载完成..."，pending 始终为空，没有任何发帖动作。

**诊断**：
```bash
# 确认 download 有没有被 cron 调度
cat /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/cron/jobs.json

# 确认下载目录只有 .bdpan 残留（无 .mp4）
ls -la /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/bdpan-downloads/
```

**必须修复**：给 `download` 命令也加一个 cron job，建议调度 `*/10 * * * *`（每10分钟），比 watch 稀疏一些，避免下载和发帖速度跟不上。

**补充：.bdpan 文件不是"待发帖"文件**：
- `watch` 只扫描 `*.mp4`，`*.bdpan` 对它完全不可见
- 中断的下载留下 `.bdpan` 残留，这些是**孤儿文件**，不会自动重试，不会被 watch 处理
- 下次 `download` 重跑时会选同一个文件（pending 已标记跳过），但如果 pending 也超时被清理了，会重新选同一个文件从头下（因为 bdpan 不支持断点续传）
- `.bdpan` 文件的存在**不是 bug，是正常状态**（下载进行中）或**之前被 kill 的遗迹**

**正确配置（两个 cron job）**：
| Job | Schedule | Command |
|-----|----------|---------|
| `bdpan动态壁纸-watcher` | `*/5 * * * *` | `watch` 子命令 |
| `bdpan动态壁纸-downloader` | `*/10 * * * *` | `download` 子命令 |

**区分 .bdpan 残留的三种状态**：
```bash
# 1. 活跃下载中（最近有更新）
stat 465223581608255_RO姬动态壁纸.mp4.bdpan
# Access/Modify 应该在最近几分钟内

# 2. 卡住的下载（很久没更新，可能被 kill 了）
stat 1087917026367720_流萤.mp4.bdpan
# Modify 显示 2 小时前 → 进程已死，需要手动清理

# 3. 下载完成（已转为 .mp4）
ls -la bdpan-downloads/*.mp4
# 有输出 → watch 可以处理
```

清理卡住下载的方法（先删残留，再重跑 download）：
```bash
rm -f /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/bdpan-downloads/*.bdpan
# 然后用 background=true 触发 download（避免 180s timeout）
```

16. **文件名含 `....` 无法下载**：bdpan 对远程路径中含 `....`（连续4个点）的文件直接 server-side 拒绝，错误信息"路径穿越攻击被阻止"，无任何变通方法。代码中在 download() 调用前检测并跳过。

11. **文件名含 `....` 无法下载**：bdpan 对远程路径中含 `....`（连续4个点）的文件直接 server-side 拒绝，错误信息"路径穿越攻击被阻止"，无任何变通方法。代码中在 download() 调用前检测并跳过。

12. **飞书通知 token 失效**：飞书 token 过期后若不清理缓存，会永远失败。代码中 `send()` 捕获 401/403 会自动清 `self._token = None`，下次重试重新认证。

13. **pending 文件无存活上限**：若下载因 login 失效而失败（如本次），pending 队列中的文件永远不会被清理，`iter_mp4_newest_first` 每次都跳过它，但 cron 每5分钟仍会选下一个（直到用完所有未发帖的 mp4）。这会导致 cron 空跑（找不到新文件可下载）但 watch 仍不断处理 pending 文件。建议给 `pending_downloads` 中的文件加 timestamp 字段，超时（如30分钟）则移除或重新入队。

## ⚠️ 关键警告：两个 profile 使用相同的触发词"发动态壁纸"

| Profile | 处理方式 | 风险 |
|---|---|---|
| `tencent-channel-live-wallpaper` | 接收用户消息，处理"发动态壁纸" | 手动发帖入口 |
| `tencent-channel-dupan-live-wallpaper` | cron job `31b23f845072` 每5分钟跑一次 `watch` | **自动发帖，不受用户控制** |

**问题**：两个 profile 都注册了"发动态壁纸"触发词。如果用户发消息"发动态壁纸"，消息可能被路由到 `tencent-channel-live-wallpaper` profile（WallpaperWaifu 源），**而不是** `tencent-channel-dupan-live-wallpaper` 的 bdpan 发帖脚本。

**但 cron job 独立于消息路由运行**：`tencent-channel-dupan-live-wallpaper` 的 cron job 每5分钟自动执行，不依赖用户消息。如果 cron 跑起来了（比如凌晨有人手动触发了一次），它会持续每5分钟尝试发帖，即使没有人再发消息。

**bdpan login 失效时的症状**：
- `bdpan search "mp4"` 返回 `{"code": 1, "error": "请先执行 bdpan login 命令"}`
- 但 cron 仍每5分钟运行，pending 队列中的文件不断被处理
- 造成"一直在发动态壁纸"的假象（实际是 cron 在反复处理相同的 pending 文件）

**触发词冲突**：⚠️ `bdpan_wallpaper_post.py`（bdpan 源）同时存在于两个 profile，都注册了"发动态壁纸"：
- `tencent-channel-dupan-live-wallpaper/scripts/bdpan_wallpaper_post.py` ← cron job 挂在此（每5分钟跑 `watch`）
- `tencent-channel-live-wallpaper/scripts/bdpan_wallpaper_post.py` ← 重复副本，无 cron
- `tencent-channel-live-wallpaper/scripts/live_wallpaper_post.py` ← WallpaperWaifu 源，触发词是"Wallpaper动态壁纸发帖"

**修复方法**：重新登录 bdpan 或暂停 cron job：
```bash
# 重新登录
HOME=/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/home \
  /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/home/.local/bin/bdpan login

# 暂停 cron job
hermes cron pause 31b23f845072
```

## 已知问题：下载后文件未移到 bdpan-downloads/

**现象**：下载完成（10.7MB Sirin - Summer.mp4），但 `bdpan-downloads/` 为空，watch 找不到文件。

**原因**：下载命令的输出路径问题——可能写死了 `/tmp` 而非 `bdpan-downloads/`。`--transfer-dir ""` 在某些版本无效，文件残留在 `/tmp`。

**但发帖仍成功**：旧版 download 可能直接传 `/tmp/xxx.mp4` 给 bdpan，文件留在 /tmp。手动跑 watch 时文件已在 /tmp 待处理，所以 watch 仍能处理并成功发帖。**新版已改为输出到 bdpan-downloads/**。

**排查关键**：`download()` 方法中 bdpan CLI 的**第二个参数**（local path）是否是 `self.output_dir / filename`？如果写死了 `/tmp/xxx.mp4` 就会导致此问题。

**验证方法**：
```bash
# 下载完成后检查 /tmp 有无残留
ls -la /tmp/*.mp4 2>/dev/null

# 同时检查 bdpan-downloads/ 有无文件
ls -la /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/bdpan-downloads/
```

## "一直在发动态壁纸" 的诊断流程

1. **检查 bdpan 登录状态**：
```bash
HOME=/root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/home \
  /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/home/.local/bin/bdpan search "mp4" --json 2>&1 | head -5
```
→ 返回 `{"code": 1, "error": "请先执行 bdpan login 命令"}` = 登录已失效

2. **检查 pending 队列**（若 pending 积压且 download 因 login 失效而失败，cron watch 会反复尝试发帖）：
```bash
cat /root/.hermes/profiles/tencent-channel-dupan-live-wallpaper/live_wallpaper_state.json | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('pending:', len(d.get('pending_downloads', [])))
print('posted:', len(d.get('posted_detail_urls', [])))
"
```

3. **检查 cron job 状态**：
```bash
hermes cron list  # 查看所有 cron job，找到 31b23f845072
```

4. **确认发帖的是 cron 还是消息路由**：若 cron 在跑，即使没有人发"发动态壁纸"消息，watch 也会每5分钟跑一次发帖逻辑。

## 目录结构（已清理）

当前 `scripts/` 下只有 `bdpan_wallpaper_post.py`，其余均为历史遗留已删除。
