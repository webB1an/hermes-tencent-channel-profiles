#!/usr/bin/env python3
"""
煎蛋网树洞（post_id=102312）→ 废话回收站 同步脚本

每 30 分钟运行一次，抓取自上次同步以来的新评论（以 date_gmt 为准），
分发到对应板块。已同步的评论 ID 记录在 STATE_FILE 防重。

代码风格参照 wallpaper_cold_detect_v2.1.py，
发帖调用 tencent-channel-cli（stdin JSON 模式）。
"""
from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import sys
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ============================================================================
# 默认配置（可被环境变量或 .env 覆盖）
# ============================================================================

CST = timezone(timedelta(hours=8))

DEFAULTS: dict[str, str] = {
    "GUILD_ID":           "622213584078628209",   # 废话回收站
    "POST_ID":            "102312",               # 煎蛋树洞 post_id
    "STATE_DIR":          "/root/.hermes/profiles/tencent-channel/state",
    "HTTP_TIMEOUT":       "15",
    "USER_AGENT":         "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
}

# 废话回收站板块映射
CHANNEL_MAP: dict[str, str] = {
    "选择困难": "728730933",
    "可回收物": "728730909",
    "今日垃圾": "728730788",
    "夜间投放": "728730802",
    "有毒废物": "728730812",
    "摸鱼回收": "728730935",
    "垃圾影展": "728730956",
    "分拣中心": "728730920",
    "匿名丢弃": "728730833",
}

# 过滤关键词（含以下内容的评论直接跳过不发布）
SKIP_KEYWORDS = ["性侵", "http", "煎蛋", "方丈", "do", "蛋友"]

LOG = logging.getLogger("jandan-sync")


# ============================================================================
# 配置加载
# ============================================================================

@dataclass
class Config:
    guild_id: str
    post_id: str
    state_dir: Path
    state_file: Path
    last_sync_file: Path
    http_timeout: int
    user_agent: str
    api_base: str

    @classmethod
    def load(cls) -> "Config":
        hermes_home = Path(os.environ.get(
            "HERMES_HOME",
            "/root/.hermes/profiles/tencent-channel"
        ))
        env = {**DEFAULTS, **os.environ}

        def _get(key: str) -> str:
            return env.get(key, DEFAULTS.get(key, ""))

        state_dir = Path(_get("STATE_DIR"))
        return cls(
            guild_id=_get("GUILD_ID"),
            post_id=_get("POST_ID"),
            state_dir=state_dir,
            state_file=state_dir / ".jandan_synced_ids.json",
            last_sync_file=state_dir / ".jandan_last_sync.json",
            http_timeout=int(_get("HTTP_TIMEOUT")),
            user_agent=_get("USER_AGENT"),
            api_base=f"https://jandan.net/api/comment/post/{_get('POST_ID')}",
        )


# ============================================================================
# 通用工具：时间、重试、HTTP
# ============================================================================

def cst_now() -> datetime:
    return datetime.now(CST)


T = type(None)


def retry(
    fn,
    *,
    tries: int = 3,
    base_delay: float = 1.5,
    on: tuple = (URLError, HTTPError, TimeoutError, OSError),
    label: str = "",
) -> Any:
    """指数退避重试"""
    last_exc: Optional[BaseException] = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except on as exc:
            last_exc = exc
            if attempt == tries:
                break
            sleep = base_delay * (2 ** (attempt - 1))
            LOG.warning("%s 第 %d/%d 次失败: %s，%.1fs 后重试",
                        label or fn.__name__, attempt, tries, exc, sleep)
            _time.sleep(sleep)
    raise last_exc


class HttpClient:
    def __init__(self, user_agent: str, timeout: int) -> None:
        self.user_agent = user_agent
        self.timeout = timeout

    def _request(self, url: str, extra_headers: Optional[dict] = None) -> Request:
        headers = {
            "User-Agent": self.user_agent,
            "Referer": "https://jandan.net/",
            "Accept": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        return Request(url, headers=headers)

    def get_json(self, url: str) -> Any:
        def _do() -> Any:
            with urlopen(self._request(url), timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        return retry(_do, label=f"GET {url}")


# ============================================================================
# 状态持久化
# ============================================================================

def load_synced_ids(path: Path) -> dict:
    """返回 {id: retry_count}"""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return {str(i): 0 for i in raw}
        return raw
    except (OSError, json.JSONDecodeError):
        return {}


def save_synced_ids(ids: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ids, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_last_sync(path: Path, cfg: Config) -> datetime:
    """返回上次同步的时间窗口起点。首次运行取 30 分钟前。"""
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            cutoff = raw.get("cutoff", "")
            if cutoff:
                return parse_dt(cutoff)
        except (OSError, json.JSONDecodeError):
            pass
    return cst_now() - timedelta(minutes=30)


def save_last_sync(cutoff: datetime, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"cutoff": cutoff.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ============================================================================
# 煎蛋 API
# ============================================================================

def parse_dt(date_gmt: str) -> datetime:
    """解析 API 返回的时间字符串为北京时间 datetime"""
    if not date_gmt:
        return datetime.min.replace(tzinfo=CST)
    try:
        dt = datetime.fromisoformat(date_gmt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        return dt
    except Exception:
        try:
            return datetime.strptime(date_gmt[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=CST)
        except Exception:
            return datetime.min.replace(tzinfo=CST)


def classify(comment: dict) -> str:
    """根据内容关键词+时间决定板块"""
    content = comment.get("content", "")
    sub_count = comment.get("sub_comment_count", 0)
    dt_str = comment.get("date_gmt", "")
    try:
        hour = int(dt_str[11:13])
        is_night = (hour >= 22) or (hour < 3)
    except (ValueError, IndexError):
        is_night = False

    neg = ["傻", "笨", "丑", "烦", "累", "困", "丧", "emo", "崩溃", "想死",
           "焦虑", "抑郁", "吐了", "卧槽", "无语", "服了", "绝了", "气死了"]
    if any(k in content for k in neg):
        return "有毒废物"

    diff = ["要不要", "怎么办", "纠结", "好难", "求助", "该怎", "选哪个", "选 A", "选B", "救命"]
    if any(k in content for k in diff):
        return "选择困难"

    work = ["上班", "下班", "加班", "工资", "老板", "同事", "工作", "打工", "辞职", "offer", "面试"]
    if any(k in content for k in work):
        return "摸鱼回收"

    if comment.get("images"):
        return "垃圾影展"

    if sub_count > 0:
        return "分拣中心"

    pos = ["谢谢", "哈哈", "开心", "幸福", "加油", "好运", "祝福", "棒", "赞", "好可爱"]
    if any(k in content for k in pos):
        return "可回收物"

    if is_night:
        return "夜间投放"

    return "匿名丢弃"


def format_content(comment: dict) -> str:
    content = comment.get("content", "").replace("\r", "").strip()
    if len(content) > 300:
        content = content[:297] + "..."
    return content


# ============================================================================
# 发帖（tencent-channel-cli stdin JSON 模式）
# ============================================================================

class ChannelCLI:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def _env(self) -> dict:
        env = os.environ.copy()
        hermes_home = str(self.cfg.state_dir.parent)
        env["HERMES_HOME"] = hermes_home
        env.setdefault("HOME", hermes_home)
        # /usr/bin 不在默认 PATH，确保 subprocess 能找到 tencent-channel-cli
        env["PATH"] = "/usr/bin:" + env.get("PATH", "")
        return env

    def call(
        self,
        domain: str,
        action: str,
        payload: dict,
        *,
        timeout: int = 120,
    ) -> tuple[int, str, str]:
        binary = getattr(self.cfg, "binary", "tencent-channel-cli")
        cmd = [binary, domain, action]
        stdin_data = json.dumps(payload, ensure_ascii=False)
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_data,
                capture_output=True,
                text=True,
                env=self._env(),
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return 127, "", "binary not found"
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()

    def publish_feed(
        self,
        channel_id: str,
        content: str,
    ) -> tuple[bool, str, str]:
        """发帖到指定板块。返回 (success, feed_id, err_msg)。"""
        payload = {
            "guild_id": self.cfg.guild_id,
            "channel_id": channel_id,
            "content": content,
            "file_paths": [],
        }
        code, out, err = self.call(
            "feed",
            "publish-feed",
            payload,
            timeout=180,
        )
        if code != 0:
            # 错误信息可能在 stdout JSON 中而非 stderr
            err_msg = err
            if out:
                try:
                    j = json.loads(out)
                    err_msg = j.get("error", {}).get("message", "") or j.get("message", "") or out
                except (json.JSONDecodeError, Exception):
                    err_msg = out
            return False, "", f"exit={code} {err_msg[:200]}"
        try:
            result = json.loads(out)
        except json.JSONDecodeError:
            return False, "", f"invalid json: {out[:200]}"
        if not result.get("success"):
            return False, "", f"api failed: {result}"
        data = result.get("data") or {}
        feed_id = str(data.get("feed_id") or data.get("id") or "")
        share_url = data.get("share_url", "") or ""
        LOG.debug("发帖成功 feed_id=%s share_url=%s", feed_id, share_url)
        return True, feed_id, ""


# ============================================================================
# 主逻辑
# ============================================================================

def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> int:
    cfg = Config.load()
    http = HttpClient(cfg.user_agent, cfg.http_timeout)
    cli = ChannelCLI(cfg)

    LOG.info("========== 煎蛋树洞同步开始 ==========")

    # 加载已同步 ID 和上次窗口
    synced = load_synced_ids(cfg.state_file)
    cutoff_dt = load_last_sync(cfg.last_sync_file, cfg)
    LOG.info("同步窗口：%s 之后的新评论", cutoff_dt.strftime("%Y-%m-%d %H:%M:%S"))

    # 首次请求 page=0，获取 total_pages
    url = f"{cfg.api_base}?order=desc&page=0"
    try:
        result = http.get_json(url)
    except Exception as exc:
        LOG.error("API 请求失败: %s", exc)
        return 1

    if result.get("code") != 0:
        LOG.error("API 返回错误: %s", result)
        return 1

    data = result.get("data", {})
    total_pages = data.get("total_pages", 0)
    list_data = data.get("list", [])

    if not list_data:
        LOG.info("page=0 无数据，退出")
        return 0

    LOG.info("总页数：%s，page=0 list 共 %s 条", total_pages, len(list_data))

    current_page = data.get("current_page", 0)
    all_new_comments: list[dict] = []
    newest_dt = cutoff_dt

    # 翻页遍历，直到触及时间窗口
    page_param = 0
    while True:
        LOG.info("page=%s（current_page=%s）：list %s 条，窗口 [%s]",
                 page_param, current_page, len(list_data),
                 cutoff_dt.strftime("%Y-%m-%d %H:%M"))

        for c in list_data:
            cid = str(c["id"])
            c_dt = parse_dt(c.get("date_gmt", ""))
            if c_dt > cutoff_dt and cid not in synced:
                all_new_comments.append(c)
                if c_dt > newest_dt:
                    newest_dt = c_dt
                LOG.info("  + 新评论 id=%s [%s]", cid, c_dt.strftime("%Y-%m-%d %H:%M"))

        oldest_in_page = min((parse_dt(c.get("date_gmt", "")) for c in list_data), default=None)

        # 最老一条已触及窗口，停止
        if oldest_in_page and oldest_in_page <= cutoff_dt:
            LOG.info("最老 %s ≤ 窗口 %s，停止翻页",
                     oldest_in_page.strftime("%H:%M"), cutoff_dt.strftime("%H:%M"))
            break

        next_page = current_page - 1
        if next_page < 1:
            LOG.info("已到最老页，停止")
            break
        if next_page == page_param:
            LOG.info("下一页 page 参数相同，停止防死循环")
            break

        page_param = next_page
        _time.sleep(3)

        try:
            result = http.get_json(f"{cfg.api_base}?order=desc&page={page_param}")
        except Exception as exc:
            LOG.warning("page=%s 请求失败: %s，停止翻页", page_param, exc)
            break

        if result.get("code") != 0:
            LOG.warning("page=%s API 错误（%s），停止翻页", page_param, result.get("msg"))
            break

        data = result.get("data", {})
        list_data = data.get("list", [])
        current_page = data.get("current_page", 0)
        if not list_data:
            LOG.info("下一页无数据，停止翻页")
            break

    if not all_new_comments:
        LOG.info("本周期无新评论，退出")
        save_last_sync(cutoff_dt, cfg.last_sync_file)
        return 0

    # 按时间从新到旧发布
    all_new_comments.sort(key=lambda c: c.get("date_gmt", ""), reverse=True)
    posted = failed = skipped_filter = 0

    for comment in all_new_comments:
        text = format_content(comment)
        cid = str(comment["id"])

        # 关键词过滤
        if any(kw in text for kw in SKIP_KEYWORDS):
            synced[cid] = -1
            LOG.info("⊘ [跳过] 含敏感词: %s...", text[:30])
            skipped_filter += 1
            continue

        board = classify(comment)
        channel_id = CHANNEL_MAP.get(board)
        if not channel_id:
            LOG.warning("未知板块: %s，跳过", board)
            skipped_filter += 1
            continue

        retries = synced.get(cid, 0)
        ok = False
        for attempt in range(retries, 3):
            ok, _, err = cli.publish_feed(channel_id, text)
            if ok:
                break
            wait = random.randint(5, 15)
            LOG.warning("  重试 %d/3 失败，等待 %ds: %s", attempt + 1, wait, err[:100])
            _time.sleep(wait)

        if ok:
            synced[cid] = -1
            posted += 1
            LOG.info("✓ [%s] %s...", board, text[:40])
        else:
            synced[cid] = synced.get(cid, 0) + 1
            if synced[cid] >= 3:
                synced[cid] = -1
                LOG.info("⊘ [跳过] 3次失败: %s...", text[:30])
                skipped_filter += 1
            else:
                LOG.info("  失败 %d/3 次，标记待重试", synced[cid])
            failed += 1

        # 发完立即保存进度，防止被 SIGTERM 杀掉后重复发帖
        save_last_sync(newest_dt, cfg.last_sync_file)
        save_synced_ids(synced, cfg.state_file)

        # 随机等待再发下一条（30分钟cron周期内足够，发完就checkpoint不会丢进度）
        interval = random.randint(10, 30)
        LOG.debug("  等待 %ds ...", interval)
        _time.sleep(interval)

    # 确保窗口前进
    newest_in_page = min((parse_dt(c.get("date_gmt", "")) for c in list_data), default=None)
    if newest_in_page and newest_in_page > cutoff_dt:
        newest_dt = newest_in_page

    save_last_sync(newest_dt, cfg.last_sync_file)
    save_synced_ids(synced, cfg.state_file)
    LOG.info("========== 同步完成: 成功 %d 条，失败 %d 条，跳过 %d 条 ==========",
              posted, failed, skipped_filter)
    return 0


if __name__ == "__main__":
    sys.exit(main())
