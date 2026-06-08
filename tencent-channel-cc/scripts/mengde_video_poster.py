#!/usr/bin/env python3
"""
视频搬运脚本 v3.0
功能：下载抖音/小红书/快手无水印视频 → 轮询发到所有频道主的频道 → 删除本地文件

频道主频道池（随机轮询）:
  - 自拍摄影圈
  - 孟德严选
  - 女友控
  - 忏悔一切
  - 肉腿控

用法（CLI 模式）:
    python mengde_video_poster.py "分享文案"

用法（stdin JSON 模式）:
    echo '{"share_text": "..."}' | python mengde_video_poster.py --stdin
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

# ============================================================================
# 配置
# ============================================================================

SCRIPTS_DIR = Path(__file__).parent
BASE_DIR = SCRIPTS_DIR.parent
DOUYIN_SCRIPT = SCRIPTS_DIR / "remove-short-videos-watermark" / "douyin.py"
XHS_SCRIPT = SCRIPTS_DIR / "remove-short-videos-watermark" / "xiaohongshu.py"
KUAISHOU_SCRIPT = SCRIPTS_DIR / "remove-short-videos-watermark" / "kuaishou.py"
QQCLI_ENV_FILE = BASE_DIR / "home" / ".qqcli" / ".env"
ACCOUNT_TOKENS_FILE = BASE_DIR / "home" / ".qqcli" / "account_tokens.json"
PROFILE_ENV_FILE = BASE_DIR / ".env"
ACCOUNT_HOMES_DIR = BASE_DIR / "home" / ".qqcli" / "account_homes"

# 频道主频道池（id, 名称）
OWNER_GUILDS = [
    ("664279424082167719", "自拍摄影圈"),
    ("670516334082074035", "孟德严选"),
    ("584303044082165170", "女友控"),
    ("661081054082166997", "忏悔一切"),
    ("46486561778743039", "肉腿控"),
]

STATE_FILE = Path(tempfile.gettempdir()) / "mengde_round_robin.json"
ACCOUNT_STATE_FILE = Path(tempfile.gettempdir()) / "mengde_account_round_robin.json"


# ============================================================================
# Profile 环境变量
# ============================================================================

def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("'\"")
    return env


def profile_env() -> dict[str, str]:
    return {**load_env_file(PROFILE_ENV_FILE), **os.environ}


# ============================================================================
# 账号轮询状态管理
# ============================================================================

def read_default_token() -> str:
    """读取 tencent-channel-cc 当前默认账号 token"""
    if not QQCLI_ENV_FILE.exists():
        return ""
    for raw in QQCLI_ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "QQ_AI_CONNECT_TOKEN":
            return value.strip().strip("'\"")
    return ""


def unique_account_name(name: str, used: set[str]) -> str:
    base = name.strip() or "account"
    candidate = base
    i = 2
    while candidate in used:
        candidate = f"{base}-{i}"
        i += 1
    used.add(candidate)
    return candidate


def normalize_extra_accounts(raw: object) -> list[dict[str, str]]:
    """支持 {"accounts": [...]} 或直接 [...] 两种 token 池格式"""
    if isinstance(raw, dict):
        raw_accounts = raw.get("accounts", [])
    else:
        raw_accounts = raw
    if not isinstance(raw_accounts, list):
        return []

    accounts: list[dict[str, str]] = []
    for idx, item in enumerate(raw_accounts, start=1):
        if isinstance(item, str):
            token = item.strip()
            name = f"extra-{idx}"
        elif isinstance(item, dict):
            token = str(item.get("token", "")).strip()
            name = str(item.get("name") or f"extra-{idx}").strip()
        else:
            continue
        if token:
            accounts.append({"name": name, "token": token})
    return accounts


def load_accounts() -> list[dict[str, str]]:
    """加载默认账号 + 可选额外 token 池"""
    accounts: list[dict[str, str]] = []
    used_names: set[str] = set()
    seen_tokens: set[str] = set()

    default_token = read_default_token()
    if default_token:
        accounts.append({
            "name": unique_account_name("怪异星人", used_names),
            "token": default_token,
        })
        seen_tokens.add(default_token)

    if ACCOUNT_TOKENS_FILE.exists():
        try:
            raw = json.loads(ACCOUNT_TOKENS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"账号 token 池 JSON 格式错误：{ACCOUNT_TOKENS_FILE} ({exc})") from exc
        for account in normalize_extra_accounts(raw):
            token = account["token"]
            if token in seen_tokens:
                continue
            accounts.append({
                "name": unique_account_name(account["name"], used_names),
                "token": token,
            })
            seen_tokens.add(token)

    if not accounts:
        raise RuntimeError(f"未找到腾讯频道账号 token，请检查 {QQCLI_ENV_FILE}")
    return accounts


def save_account_pool(pool: list[str], last: str = "") -> None:
    data = {"pool": pool, "last": last}
    ACCOUNT_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def load_account_state(accounts: list[dict[str, str]]) -> tuple[list[str], str]:
    valid_names = {account["name"] for account in accounts}
    last = ""
    if ACCOUNT_STATE_FILE.exists():
        try:
            data = json.loads(ACCOUNT_STATE_FILE.read_text(encoding="utf-8"))
            last = data.get("last", "") if data.get("last") in valid_names else ""
            pool = [name for name in data.get("pool", []) if name in valid_names]
            if pool:
                return pool, last
        except Exception:
            pass

    pool = [account["name"] for account in accounts]
    random.shuffle(pool)
    if last and len(pool) > 1 and pool[0] == last:
        pool.append(pool.pop(0))
    save_account_pool(pool, last)
    return pool, last


def select_account() -> dict[str, str]:
    accounts = load_accounts()
    by_name = {account["name"]: account for account in accounts}
    pool, _ = load_account_state(accounts)
    name = pool[0]
    return by_name[name]


def mark_account_used(account_name: str) -> None:
    accounts = load_accounts()
    pool, _ = load_account_state(accounts)
    pool = [name for name in pool if name != account_name]
    save_account_pool(pool, account_name)


def safe_account_dir_name(name: str) -> str:
    value = re.sub(r"[^0-9A-Za-z_.-]+", "_", name.strip())
    value = value.strip("._") or "account"
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"{value}-{digest}"


def account_home(account: dict[str, str]) -> Path:
    home = ACCOUNT_HOMES_DIR / safe_account_dir_name(account["name"])
    qqcli_dir = home / ".qqcli"
    qqcli_dir.mkdir(parents=True, exist_ok=True)
    env_file = qqcli_dir / ".env"
    env_file.write_text(f"QQ_AI_CONNECT_TOKEN={account['token']}\n", encoding="utf-8")
    env_file.chmod(0o600)
    return home


def cli_env(account: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(BASE_DIR)
    env["HOME"] = str(account_home(account))
    env["QQ_AI_CONNECT_TOKEN"] = account["token"]
    return env


# ============================================================================
# 轮询状态管理
# ============================================================================

def load_pool() -> list[tuple[str, str]]:
    """加载轮询池，为空则初始化并打乱"""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            pool = [(g["id"], g["name"]) for g in data.get("pool", [])]
            if pool:
                return pool
        except Exception:
            pass
    # 首次或池空：打乱所有频道
    pool = list(OWNER_GUILDS)
    random.shuffle(pool)
    save_pool(pool)
    return pool


def save_pool(pool: list[tuple[str, str]]) -> None:
    """持久化轮询池"""
    data = {"pool": [{"id": g[0], "name": g[1]} for g in pool]}
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def remove_from_pool(guild_id: str) -> None:
    """从池中移除指定频道并保存"""
    pool = load_pool()
    pool = [g for g in pool if g[0] != guild_id]
    save_pool(pool)


# ============================================================================
# 平台识别
# ============================================================================

class Platform:
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    KUAISHOU = "kuaishou"
    UNKNOWN = "unknown"


def detect_platform(text: str) -> tuple[Platform, str]:
    """从分享文案中识别平台并提取 URL"""
    urls = re.findall(r"https?://[^\s，。！？!！]+", text)
    for url in urls:
        if "douyin.com" in url:
            return Platform.DOUYIN, url.rstrip(".,;:!?，。；：！？)")
        if any(domain in url for domain in ("xiaohongshu.com", "xhslink.com", "xhs.com")):
            return Platform.XIAOHONGSHU, url.rstrip(".,;:!?，。；：！？)")
        if any(domain in url for domain in ("kuaishou.com", "chenzhongtech.com")):
            return Platform.KUAISHOU, url.rstrip(".,;:!?，。；：！？)")
    raise ValueError("无法识别平台，请提供抖音/小红书/快手的分享链接")


# ============================================================================
# 下载视频
# ============================================================================

def download_video(platform: Platform, share_text: str, output_dir: Path) -> Path:
    """调用对应平台脚本下载视频，返回下载文件路径"""
    output_dir.mkdir(parents=True, exist_ok=True)

    if platform == Platform.DOUYIN:
        script = DOUYIN_SCRIPT
        args = [sys.executable, str(script), share_text, "-o", str(output_dir), "--backend", "native"]
    elif platform == Platform.XIAOHONGSHU:
        script = XHS_SCRIPT
        args = [sys.executable, str(script), share_text, "-o", str(output_dir)]
    elif platform == Platform.KUAISHOU:
        script = KUAISHOU_SCRIPT
        args = [sys.executable, str(script), share_text, "-o", str(output_dir)]
    else:
        raise ValueError(f"不支持的平台: {platform}")

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip() or "未知错误"
        raise RuntimeError(f"下载失败 [{platform}]：{error_msg}")

    # 解析输出最后一行的下载文件路径
    output_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    for line in reversed(output_lines):
        if line.startswith("下载完成："):
            return Path(line.replace("下载完成：", "").strip())
        if line.endswith(".mp4") or line.endswith(".jpg") or line.endswith(".png"):
            return Path(line.strip())

    # 兜底：在 output_dir 中找最新文件
    files = sorted(output_dir.glob("*"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise RuntimeError("下载脚本执行成功，但未找到输出文件")
    return files[-1]


# ============================================================================
# 腾讯频道发帖
# ============================================================================

def post_video_to_channel(
    guild_id: str,
    channel_id: str,
    video_path: Path,
    content: str = "",
    *,
    account: dict[str, str],
) -> str:
    """将本地视频发布到指定频道"""
    cmd = [
        "tencent-channel-cli",
        "feed", "publish-feed",
        "--guild-id", guild_id,
        "--channel-id", channel_id,
        "--content", content,
        "--video", str(video_path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=cli_env(account),
        check=False,
    )
    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip() or "未知错误"
        raise RuntimeError(f"发帖失败：{error_msg}")

    data = json.loads(result.stdout)
    if data.get("success"):
        share_url = data.get("data", {}).get("share_url", "")
        if not share_url:
            raise RuntimeError(f"发帖成功但未返回帖子链接：{data}")
        return share_url
    else:
        raise RuntimeError(f"发帖失败：retCode={data.get('retCode')}, msg={data.get('msg', '未知错误')}")


def get_channel_id_for_guild(guild_id: str, *, account: dict[str, str]) -> str:
    """获取频道的"全部"版块 channel_id"""
    cmd = [
        "tencent-channel-cli",
        "manage", "get-guild-channel-list",
        "--guild-id", guild_id,
        "-j",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=cli_env(account), check=False)
    if result.returncode != 0:
        # fallback
        return "1"
    try:
        data = json.loads(result.stdout)
        channels = data.get("data", {}).get("channels", [])
        # 找"全部"或第一个
        for ch in channels:
            if ch.get("channel_name") in ("全部", "综合", "默认") or ch.get("is_default"):
                return str(ch["channel_id"])
        if channels:
            return str(channels[0]["channel_id"])
    except Exception:
        pass
    return "1"


# ============================================================================
# 飞书通知
# ============================================================================

class FeishuNotifier:
    """通过飞书开放平台 API 发送发帖完成通知"""

    def __init__(self, env: dict[str, str], *, enabled: bool = True) -> None:
        self.app_id = env.get("FEISHU_APP_ID", "")
        self.app_secret = env.get("FEISHU_APP_SECRET", "")
        self.domain = env.get("FEISHU_DOMAIN", "feishu")
        self.user_id = env.get("FEISHU_USER_ID", "")
        self.chat_id = env.get("FEISHU_NOTIFY_CHAT_ID", "")
        self.enabled = enabled

    def _base_url(self) -> str:
        if self.domain.lower() == "lark":
            return "https://open.larksuite.com"
        return "https://open.feishu.cn"

    def is_enabled(self) -> bool:
        return bool(self.enabled and self.app_id and self.app_secret and (self.user_id or self.chat_id))

    def _post_json(self, url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
        req = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                **(headers or {}),
            },
            method="POST",
        )
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _tenant_access_token(self) -> str:
        data = self._post_json(
            f"{self._base_url()}/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
        )
        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise RuntimeError(f"tenant token failed: {data}")
        return str(data["tenant_access_token"])

    def send(self, text: str) -> None:
        if not self.is_enabled():
            print("飞书通知未启用，跳过")
            return
        token = self._tenant_access_token()
        receive_id_type = "open_id" if self.user_id else "chat_id"
        receive_id = self.user_id or self.chat_id
        data = self._post_json(
            f"{self._base_url()}/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            {
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": text[:3800]}, ensure_ascii=False),
                "uuid": str(uuid.uuid4()),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"message send failed: {data}")
        print("飞书通知已发送")


# ============================================================================
# 主流程
# ============================================================================

def process(share_text: str) -> None:
    print(f"收到分享文案：{share_text[:80]}...")

    # 1. 识别平台
    platform, url = detect_platform(share_text)
    platform_name = {"douyin": "抖音", "xiaohongshu": "小红书", "kuaishou": "快手"}.get(platform, platform)
    print(f"识别平台：{platform_name}")
    print(f"提取链接：{url}")

    # 2. 下载视频到临时目录
    content = ""
    persistent_path = None
    try:
        with tempfile.TemporaryDirectory(prefix="mengde_video_") as tmp_dir:
            downloaded_path = download_video(platform, share_text, Path(tmp_dir))
            file_size = downloaded_path.stat().st_size
            print(f"下载完成：{downloaded_path.name} ({file_size / 1024 / 1024:.1f} MB)")

            # 复制到独立持久路径，避免 with 块退出后文件被删
            import shutil
            persistent_path = Path(tempfile.gettempdir()) / f"mengde_post_{downloaded_path.name}"
            shutil.copy2(downloaded_path, persistent_path)
            print(f"复制到临时持有路径：{persistent_path.name}")

            # 文件名处理：去 # 后缀 + 平台关键词检测
            stem = downloaded_path.stem
            content = stem.split("#")[0].strip()
            name_lower = stem.lower()
            if any(k in name_lower for k in ("douyin", "dy", "xiaohongshu", "xhs", "kuaishou", "ks")):
                content = ""

        # 3. 自动从账号池和频道池各选一个（随机轮询）
        account = select_account()
        print(f"随机选择账号：{account['name']}")

        pool = load_pool()
        if not pool:
            pool = list(OWNER_GUILDS)
            random.shuffle(pool)
            save_pool(pool)
        guild_id, guild_name_selected = pool[0]
        print(f"随机选择频道：{guild_name_selected}（池内共{len(pool)}个）")

        # 4. 发帖
        channel_id = get_channel_id_for_guild(guild_id, account=account)
        print(f"目标频道：{guild_name_selected}（guild_id={guild_id}，channel_id={channel_id}）")
        print(f"发帖文案：'{content}'" if content else "发帖文案：（纯视频）")
        share_url = post_video_to_channel(guild_id, channel_id, persistent_path, content, account=account)
        mark_account_used(account["name"])
        remove_from_pool(guild_id)
        print("发帖完成")
        print(f"账号：{account['name']}")
        print(f"频道：{guild_name_selected}")
        print(f"帖子链接：{share_url}")
        notice = (
            "发帖完成\n"
            f"账号：{account['name']}\n"
            f"频道：{guild_name_selected}\n"
            f"帖子链接：{share_url}"
        )
        try:
            FeishuNotifier(profile_env()).send(notice)
        except Exception as exc:
            print(f"飞书通知失败（不影响发帖结果）：{exc}", file=sys.stderr)

    finally:
        if persistent_path:
            persistent_path.unlink(missing_ok=True)
            print(f"删除本地文件：{persistent_path.name}")


# ============================================================================
# 入口
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="视频搬运脚本 v3.0（随机轮询频道主频道）")
    parser.add_argument("share_text", nargs="*", help="平台分享文案或链接")
    parser.add_argument("--stdin", action="store_true", help="从 stdin JSON 读取 share_text")
    args = parser.parse_args()

    try:
        if args.stdin:
            payload = json.loads(sys.stdin.read())
            share_text = payload.get("share_text", "")
            if not share_text:
                print("错误：stdin JSON 缺少 share_text 字段", file=sys.stderr)
                return 1
        else:
            share_text = " ".join(args.share_text).strip()
            if not share_text:
                print("错误：请提供分享文案", file=sys.stderr)
                return 1

        process(share_text)
        return 0

    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
