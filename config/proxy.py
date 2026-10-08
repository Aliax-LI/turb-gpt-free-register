# -*- coding: utf-8 -*-
"""
代理池配置

每次注册随机抽取一个代理，保证不同 sid 之间彼此独立，避免风控关联。

协议说明：
    - http:// / https://   HTTP(S) 代理
    - socks5://            SOCKS5（DNS 本地解析，可能泄漏）
    - socks5h://           SOCKS5（DNS 在代理端解析，推荐，避免 DNS-IP 错配）
"""
import random
import re
import secrets
from urllib.parse import quote, unquote, urlparse, urlsplit, urlunsplit

from config.env_loader import apply_env_overrides


# 本地代理入口；实际出口地区以代理/分流规则为准。
# 推荐使用 socks5h://（DNS 在代理端解析），避免本地 DNS 与出口 IP 地区错配。
PROXY_POOL = [
    "socks5://127.0.0.1:7897",
]

# IPRocket 等代理支持通过用户名中的 Lsid/TTL 参数保持会话。
# 开启后，使用代理池创建注册环境时每个任务生成独立 Lsid，避免多个任务共享出口 IP。
PROXY_STICKY_SESSION_PER_TASK: bool = True
PROXY_STICKY_SESSION_TTL: int = 300

# 代理池使用的本地上游代理。填写后形成：本地上游 -> 代理池目标代理 -> ChatGPT；
# 留空则直接使用代理池中的目标代理，不启动链式中继。
PROXY_POOL_UPSTREAM_PROXY = ""

# 套餐/Plus 试用资格查询与 Codex Agent Token 生成共用这组独立网络策略，
# 避免批量请求被注册代理池中的临时本地代理拖垮，也避免无条件直连造成出口策略失控。
#   auto   = 优先使用 PLAN_CHECK_PROXY 或代理池；没有代理时才按 direct 运行
#   proxy  = 强制使用 PLAN_CHECK_PROXY 或代理池，失败直接报错
#   direct = 始终直连
PLAN_CHECK_PROXY_MODE = "auto"

# 套餐查询 / Codex Agent Token 生成专用代理。留空时 auto/proxy 模式从 PROXY_POOL 选择。
# 代理可能包含账号密码，因此 WebUI 会把它保存到 .env。
PLAN_CHECK_PROXY = []

# 套餐查询 / Codex Agent Token 生成的上游代理。填写后形成：本地代理 -> 动态代理 -> ChatGPT。
# 例如 http://127.0.0.1:7897；留空则直接连接 PLAN_CHECK_PROXY。
PLAN_CHECK_UPSTREAM_PROXY = ""

# 查套餐 / 生成 Codex Agent Token 使用独立的短超时和有限重试，避免后台任务长时间卡住。
PLAN_CHECK_TIMEOUT = 15.0
PLAN_CHECK_MAX_ATTEMPTS = 3
PLAN_CHECK_RETRY_DELAY = 2.0

# 新注册账号的权益可能存在短暂同步延迟。首次查询失败，或返回 free 且暂未发现
# Plus 试用资格时，等待该秒数后再复查一次；设为 0 可关闭复查。
PLAN_CHECK_REGISTRATION_RECHECK_DELAY = 2.0

# 自动、手动和批量套餐查询共用同一个后台队列；Codex Agent Token 使用独立队列，
# 但复用这里的网络模式、请求启动间隔与随机抖动，避免批量后台请求过于集中。
PLAN_CHECK_WORKERS = 3
PLAN_CHECK_QUEUE_LIMIT = 500
PLAN_CHECK_MIN_INTERVAL = 1.0
PLAN_CHECK_JITTER = 0.8


def _valid_port(value: str) -> bool:
    return value.isdigit() and 1 <= int(value) <= 65535


def normalize_proxy_url(value: str, default_scheme: str = "http") -> str:
    """Normalize common proxy shorthand while preserving explicit URLs."""
    text = str(value or "").strip()
    if not text or "://" in text:
        return text
    parts = text.split(":", 3)
    if len(parts) == 4 and parts[0] and _valid_port(parts[1]) and parts[2] and parts[3]:
        host, port, username, password = parts
        return f"{default_scheme}://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
    if len(parts) == 4 and parts[0] and parts[1] and parts[2] and _valid_port(parts[3]):
        username, password, host, port = parts
        return f"{default_scheme}://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
    parsed = urlparse(f"//{text}")
    try:
        port = parsed.port
    except ValueError:
        port = None
    if parsed.hostname and port:
        return f"{default_scheme}://{text}"
    return text


def normalize_proxy_list(values, default_scheme: str = "http") -> list[str]:
    """Normalize a multiline proxy list and omit empty entries."""
    if values is None:
        return []
    if isinstance(values, str):
        values = values.splitlines()
    return [
        normalized
        for value in values
        if (normalized := normalize_proxy_url(value, default_scheme=default_scheme))
    ]


def pick_proxy() -> str:
    """从代理池中随机抽取一个代理 URL；池为空时返回空串（即不使用代理）。"""
    return random.choice(PROXY_POOL) if PROXY_POOL else ""


def _new_sticky_session_id() -> str:
    """生成与常见 IPRocket Lsid 格式兼容的 9 位会话 ID。"""
    return str(secrets.randbelow(900_000_000) + 100_000_000)


def _proxy_with_task_sticky_session(proxy_url: str) -> str:
    """为代理认证用户名写入本任务独立的 Lsid 和 TTL。"""
    text = str(proxy_url or "").strip()
    if not text or not bool(PROXY_STICKY_SESSION_PER_TASK):
        return text
    try:
        parsed = urlsplit(text)
        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        if not username:
            return text
        ttl = max(1, int(PROXY_STICKY_SESSION_TTL or 300))
        session_id = _new_sticky_session_id()
        if re.search(r"-Lsid-[^-]+", username, re.IGNORECASE):
            username = re.sub(r"(-Lsid-)[^-]+", rf"\g<1>{session_id}", username, count=1, flags=re.IGNORECASE)
        else:
            username += f"-Lsid-{session_id}"
        if re.search(r"-TTL-\d+", username, re.IGNORECASE):
            username = re.sub(r"(-TTL-)\d+", rf"\g<1>{ttl}", username, count=1, flags=re.IGNORECASE)
        else:
            username += f"-TTL-{ttl}"
        auth = quote(username, safe="-._~")
        if parsed.password is not None:
            auth += ":" + quote(password, safe="-._~!$&'()*+,;=")
        auth += "@"
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = auth + host + (f":{parsed.port}" if parsed.port else "")
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    except (TypeError, ValueError, UnicodeError):
        return text


def pick_proxy_for_task() -> str:
    """为一个任务抽取代理；可选地为其生成独立粘性会话。"""
    return _proxy_with_task_sticky_session(pick_proxy())


# 兼容入口：默认每次进程启动随机选一个，作为本次注册全程的固定代理
PROXY = pick_proxy()

# ---- .env overrides for WebUI editable fields ----
apply_env_overrides(globals(), {
    'PROXY_POOL': 'list_str_multiline',
    'PROXY_STICKY_SESSION_PER_TASK': 'bool',
    'PROXY_STICKY_SESSION_TTL': 'int',
    'PROXY_POOL_UPSTREAM_PROXY': 'str',
    'PLAN_CHECK_PROXY_MODE': 'str',
    'PLAN_CHECK_PROXY': 'list_str_multiline',
    'PLAN_CHECK_UPSTREAM_PROXY': 'str',
    'PLAN_CHECK_TIMEOUT': 'float',
    'PLAN_CHECK_MAX_ATTEMPTS': 'int',
    'PLAN_CHECK_RETRY_DELAY': 'float',
    'PLAN_CHECK_REGISTRATION_RECHECK_DELAY': 'float',
    'PLAN_CHECK_WORKERS': 'int',
    'PLAN_CHECK_QUEUE_LIMIT': 'int',
    'PLAN_CHECK_MIN_INTERVAL': 'float',
    'PLAN_CHECK_JITTER': 'float',
})
PROXY_POOL = normalize_proxy_list(PROXY_POOL)
PLAN_CHECK_PROXY = normalize_proxy_list(PLAN_CHECK_PROXY)
PROXY = pick_proxy()
