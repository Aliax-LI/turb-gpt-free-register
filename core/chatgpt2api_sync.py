# -*- coding: utf-8 -*-
"""ChatGPT2API 账号同步：导入 access token、删除旧账号、扫描远端异常账号。"""
import logging
import threading
import time

import requests

from config import chatgpt2api as _cfg
from config.browser import USER_AGENT
from core import db

logger = logging.getLogger(__name__)

# 远端账号列表里表示「异常」的状态文案。新接口用 status_category=abnormal。
ABNORMAL_STATUS = "异常"
_PAGE_SIZE = 500
# 导入后要等远端拉取邮箱和额度，比普通列表请求长。
_IMPORT_TIMEOUT_FLOOR = 120

_PENDING_LOCK = threading.Lock()
# account_id -> 远端待处理的旧 access token / 管理端账号 id。
# ponytail: 只保存在进程内存，WebUI 重启后需要重新点一次「C2A异常」筛选；
# 升级路径是把待处理 token 落到 accounts.payload。
_PENDING_OLD_TOKENS: dict[int, list[str]] = {}
_PENDING_REMOTE_IDS: dict[int, list[str]] = {}


def _headers() -> dict:
    return {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {_cfg.CHATGPT2API_BEARER}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


def _remember(bucket: dict[int, list[str]], account_id: int, values: list[str]) -> None:
    with _PENDING_LOCK:
        current = bucket.setdefault(int(account_id), [])
        for value in values:
            if value and value not in current:
                current.append(value)


def _remember_old_tokens(account_id: int, tokens: list[str]) -> None:
    _remember(_PENDING_OLD_TOKENS, account_id, tokens)


def _remember_remote_ids(account_id: int, remote_ids: list[str]) -> None:
    _remember(_PENDING_REMOTE_IDS, account_id, remote_ids)


def _old_tokens(account_id: int) -> list[str]:
    with _PENDING_LOCK:
        return list(_PENDING_OLD_TOKENS.get(int(account_id)) or [])


def _remote_ids(account_id: int) -> list[str]:
    with _PENDING_LOCK:
        return list(_PENDING_REMOTE_IDS.get(int(account_id)) or [])


def forget_old_tokens(account_id: int) -> None:
    with _PENDING_LOCK:
        _PENDING_OLD_TOKENS.pop(int(account_id), None)
        _PENDING_REMOTE_IDS.pop(int(account_id), None)


def _count(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _error_text(errors: object) -> str:
    if not isinstance(errors, list) or not errors:
        return "未知错误"
    first = errors[0]
    if isinstance(first, dict):
        return str(first.get("message") or first.get("error") or first)[:160]
    return str(first)[:160]


def _response_data(response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_abnormal(item: dict) -> bool:
    if str(item.get("status") or "").strip() == ABNORMAL_STATUS:
        return True
    if str(item.get("status_category") or "").strip().lower() == "abnormal":
        return True
    for key in ("status_label", "backend_status"):
        if str(item.get(key) or "").strip() == ABNORMAL_STATUS:
            return True
    return False


def _session_refresh_token(result: dict) -> str:
    """从查活 session 里取出 ChatGPT refresh token，不使用邮箱池的 refresh_token。"""
    sources: list[dict] = []
    session = result.get("session")
    if isinstance(session, dict):
        sources.append(session)
        for key in ("credentials", "credential", "tokens", "auth"):
            nested = session.get(key)
            if isinstance(nested, dict):
                sources.append(nested)
    sources.append(result)
    for source in sources:
        for key in ("refreshToken", "refresh_token"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def sync_account(access_token: str, *, refresh_token: str = "", force: bool = False) -> dict:
    """导入一个 access token；失败不抛出，不能影响调用方主流程。

    force=True 用于手动触发的流程（如查活后重新导入），不受 ENABLE_CHATGPT2API_SYNC 限制。
    refresh_token 有值时一并写入，对应网站添加账号里的 RT。
    """
    if not force and not _cfg.ENABLE_CHATGPT2API_SYNC:
        return {"status": "skipped", "ok": False, "message": "ENABLE_CHATGPT2API_SYNC=False"}
    if not access_token:
        return {"status": "skipped", "ok": False, "message": "access_token 为空"}
    if not _cfg.CHATGPT2API_BEARER:
        return {"status": "skipped", "ok": False, "message": "CHATGPT2API_BEARER 为空"}

    refresh_token = str(refresh_token or "").strip()
    account = {
        "access_token": access_token,
        "source_type": "web",
        "status": "正常",
    }
    if refresh_token:
        account["refresh_token"] = refresh_token
    try:
        response = requests.post(
            _cfg.CHATGPT2API_ACCOUNTS_URL,
            headers=_headers(),
            # 与网站手动导入一致：写入 AT/RT，并立刻同步邮箱、套餐和额度。
            json={
                "tokens": [access_token],
                "accounts": [account],
                "sync_after_import": True,
                "restore": False,
                "return_items": False,
            },
            timeout=max(int(_cfg.CHATGPT2API_TIMEOUT or 0), _IMPORT_TIMEOUT_FLOOR),
        )
    except requests.RequestException as exc:
        return {"status": "failed", "ok": False, "message": f"{type(exc).__name__}: {exc}"}

    message = (response.text or "")[:200]
    if not response.ok:
        return {"status": "failed", "ok": False, "http_status": response.status_code, "message": message}
    data = _response_data(response)
    added = _count(data.get("added")) if "added" in data else None
    skipped = _count(data.get("skipped")) if "skipped" in data else None
    synced = _count(data.get("synced")) if "synced" in data else None
    errors = data.get("errors") or []
    if added is not None and skipped is not None and added + skipped <= 0:
        return {
            "status": "failed",
            "ok": False,
            "http_status": response.status_code,
            "added": added,
            "skipped": skipped,
            "synced": synced,
            "has_refresh_token": bool(refresh_token),
            "message": "账号未写入",
        }
    if synced == 0 and errors:
        return {
            "status": "failed",
            "ok": False,
            "http_status": response.status_code,
            "added": added,
            "skipped": skipped,
            "synced": synced,
            "message": f"已入库但同步失败：{_error_text(errors)}"[:200],
            "has_refresh_token": bool(refresh_token),
        }
    return {
        "status": "success",
        "ok": True,
        "http_status": response.status_code,
        "added": added,
        "skipped": skipped,
        "synced": synced,
        "has_refresh_token": bool(refresh_token),
        "message": message,
    }


def delete_accounts(tokens: list[str], account_ids: list[str] | None = None) -> dict:
    """按 access token 或管理端账号 id 删除 ChatGPT2API 上的账号。"""
    cleaned = [str(t).strip() for t in tokens if str(t or "").strip()]
    ids: list[str] = []
    for raw in account_ids or []:
        remote_id = str(raw or "").strip()
        if remote_id and remote_id not in ids:
            ids.append(remote_id)
    if not cleaned and not ids:
        return {"status": "skipped", "ok": True, "message": "没有需要删除的 token"}
    if not _cfg.CHATGPT2API_BEARER:
        return {"status": "skipped", "ok": False, "message": "CHATGPT2API_BEARER 为空"}

    body: dict = {}
    if cleaned:
        body["tokens"] = cleaned
    if ids:
        body["account_ids"] = ids
    try:
        response = requests.delete(
            _cfg.CHATGPT2API_ACCOUNTS_URL,
            headers=_headers(),
            json=body,
            timeout=_cfg.CHATGPT2API_TIMEOUT,
        )
    except requests.RequestException as exc:
        return {"status": "failed", "ok": False, "message": f"{type(exc).__name__}: {exc}"}

    message = (response.text or "")[:200]
    if not response.ok:
        return {"status": "failed", "ok": False, "http_status": response.status_code, "message": message}

    progress_id = str(_response_data(response).get("progress_id") or "").strip()
    if progress_id:
        return _wait_delete(progress_id, fallback_count=len(cleaned) + len(ids))
    return {
        "status": "success",
        "ok": True,
        "http_status": response.status_code,
        "deleted_count": len(cleaned) + len(ids),
        "message": message,
    }


def _wait_delete(progress_id: str, fallback_count: int) -> dict:
    """新接口的删除是异步的，等到操作结束再认为旧号已删掉。"""
    url = _cfg.CHATGPT2API_ACCOUNTS_URL.rstrip("/") + f"/operations/{progress_id}"
    deadline = time.monotonic() + max(1, int(_cfg.CHATGPT2API_TIMEOUT))
    while True:
        try:
            response = requests.get(url, headers=_headers(), timeout=_cfg.CHATGPT2API_TIMEOUT)
        except requests.RequestException as exc:
            return {"status": "failed", "ok": False, "message": f"{type(exc).__name__}: {exc}"}
        if not response.ok:
            return {
                "status": "failed",
                "ok": False,
                "http_status": response.status_code,
                "message": (response.text or "")[:200],
            }
        data = _response_data(response)
        if data.get("done"):
            if data.get("error"):
                return {"status": "failed", "ok": False, "message": str(data.get("error"))[:200]}
            result = data.get("result") if isinstance(data.get("result"), dict) else {}
            errors = result.get("errors") or []
            removed = int(result.get("removed") or 0)
            if errors and removed <= 0:
                return {"status": "failed", "ok": False, "message": str(errors)[:200]}
            return {
                "status": "success",
                "ok": True,
                "deleted_count": removed or fallback_count,
                "message": "deleted",
            }
        if time.monotonic() >= deadline:
            return {"status": "failed", "ok": False, "message": "删除未在超时内完成"}
        time.sleep(0.4)


def list_remote_accounts() -> dict:
    """拉取 ChatGPT2API 账号列表。新接口按页返回，最多每页 500 条。"""
    if not _cfg.CHATGPT2API_BEARER:
        return {"ok": False, "items": [], "error": "CHATGPT2API_BEARER 为空"}

    items: list = []
    seen_pages: set[tuple] = set()
    page = 1
    while page <= 40:
        try:
            response = requests.get(
                _cfg.CHATGPT2API_ACCOUNTS_URL,
                headers=_headers(),
                params={"page": page, "page_size": _PAGE_SIZE},
                timeout=_cfg.CHATGPT2API_TIMEOUT,
            )
        except requests.RequestException as exc:
            return {"ok": False, "items": [], "error": f"{type(exc).__name__}: {exc}"}
        if not response.ok:
            return {"ok": False, "items": [], "error": f"HTTP {response.status_code}: {(response.text or '')[:200]}"}

        try:
            data = response.json()
        except ValueError:
            return {"ok": False, "items": [], "error": "ChatGPT2API 响应不是 JSON"}

        batch = data if isinstance(data, list) else (data or {}).get("items")
        if not isinstance(batch, list):
            return {"ok": False, "items": [], "error": "ChatGPT2API 响应缺少 items 数组"}
        if not batch:
            break

        fingerprint = tuple(
            (
                str(item.get("id") or item.get("management_id") or ""),
                str(item.get("email") or ""),
                str(item.get("access_token") or "")[:24],
            )
            for item in batch
            if isinstance(item, dict)
        )
        if fingerprint in seen_pages:
            break
        seen_pages.add(fingerprint)
        items.extend(item for item in batch if isinstance(item, dict))

        if isinstance(data, list):
            break
        total = data.get("total")
        try:
            page_size = int(data.get("page_size") or _PAGE_SIZE)
        except (TypeError, ValueError):
            page_size = _PAGE_SIZE
        if isinstance(total, int) and len(items) >= total:
            break
        if len(batch) < max(1, page_size):
            break
        page += 1
    return {"ok": True, "items": items}


def scan_abnormal_accounts() -> dict:
    """拉取远端异常账号并匹配本地账号，记住待删除的旧 token 供查活后使用。"""
    listed = list_remote_accounts()
    if not listed.get("ok"):
        return {"ok": False, "error": listed.get("error") or "拉取 ChatGPT2API 账号失败"}

    grouped: dict[str, dict[str, list[str]]] = {}
    unmatched_remote = 0
    abnormal_count = 0
    for item in listed["items"]:
        if not isinstance(item, dict) or not _is_abnormal(item):
            continue
        abnormal_count += 1
        email = str(item.get("email") or "").strip().lower()
        token = str(item.get("access_token") or "").strip()
        remote_id = str(item.get("id") or item.get("management_id") or "").strip()
        if not email or not (token or remote_id):
            unmatched_remote += 1
            continue
        bucket = grouped.setdefault(email, {"tokens": [], "ids": []})
        if token and token not in bucket["tokens"]:
            bucket["tokens"].append(token)
        if remote_id and remote_id not in bucket["ids"]:
            bucket["ids"].append(remote_id)

    account_ids: list[int] = []
    matched: list[dict] = []
    archived_count = 0
    for email, bucket in grouped.items():
        acc = db.get_account_by_email(email)
        acc_id = int((acc or {}).get("id") or 0)
        if not acc_id:
            unmatched_remote += 1
            continue
        _remember_old_tokens(acc_id, bucket["tokens"])
        _remember_remote_ids(acc_id, bucket["ids"])
        account_ids.append(acc_id)
        archived = bool(acc.get("archived"))
        if archived:
            archived_count += 1
        matched.append({
            "id": acc_id,
            "email": acc.get("email") or email,
            "token_count": max(len(bucket["tokens"]), len(bucket["ids"])),
            "archived": archived,
        })

    account_ids.sort(reverse=True)
    return {
        "ok": True,
        "abnormal_count": abnormal_count,
        "remote_total": len(listed["items"]),
        "account_ids": account_ids,
        "matched": matched,
        "matched_count": len(account_ids),
        # 归档账号默认不在账号列表里显示，需要开「查看归档」才能看到。
        "archived_count": archived_count,
        "unmatched_remote": unmatched_remote,
    }


def handle_live_check_finished(
    account_id: int,
    result: dict | None = None,
    previous_token: str | None = None,
) -> dict | None:
    """查活结束后同步 ChatGPT2API：成功=导入新 token 并删旧号，已废=只删旧号。

    其他失败（网络/CF 拦截等）不动远端账号，旧 token 继续保留以便重新查活。
    开启 ENABLE_CHATGPT2API_SYNC 或账号记录过旧 token 时均执行同步。
    """
    old_tokens = _old_tokens(account_id)
    remote_ids = _remote_ids(account_id)
    if not old_tokens and previous_token:
        old_tokens = [previous_token]

    sync_enabled = bool(getattr(_cfg, "ENABLE_CHATGPT2API_SYNC", False))
    has_tracked_tokens = bool(_old_tokens(account_id) or remote_ids)

    if not sync_enabled and not has_tracked_tokens:
        return None

    if not getattr(_cfg, "CHATGPT2API_BEARER", ""):
        return {"handled": False, "reason": "CHATGPT2API_BEARER 为空"}

    result = result or {}
    status = str(result.get("status") or "").strip()
    if not result.get("ok") and status != "deactivated":
        return {"handled": False, "reason": f"查活状态 {status or 'failed'}，保留 ChatGPT2API 账号"}

    new_token = str(result.get("access_token") or "").strip() if result.get("ok") else ""
    refresh_token = _session_refresh_token(result) if new_token else ""
    if new_token and not refresh_token:
        session = result.get("session") if isinstance(result.get("session"), dict) else {}
        logger.info(
            "[ChatGPT2API] 查活 session 没有 refreshToken，本次只同步 AT，session keys=%s",
            sorted(str(key) for key in session),
        )
    imported = sync_account(new_token, refresh_token=refresh_token, force=True) if new_token else {
        "status": "skipped",
        "ok": False,
        "message": "账号已废，跳过导入",
    }
    # 导入失败时不能删旧号，否则远端会一个可用账号都不剩。
    if new_token and not imported.get("ok"):
        return {"handled": False, "imported": imported, "reason": f"导入新 token 失败（{imported.get('message') or '-'}），保留旧账号"}

    tokens_to_delete = [t for t in old_tokens if t and t != new_token]
    if remote_ids:
        deleted = delete_accounts(tokens_to_delete, account_ids=remote_ids)
    elif tokens_to_delete:
        deleted = delete_accounts(tokens_to_delete)
    else:
        deleted = {
            "status": "skipped",
            "ok": True,
            "message": "无旧账号需要删除",
        }
    if deleted.get("ok"):
        forget_old_tokens(account_id)
    logger.info(
        "[ChatGPT2API] 查活后同步 account_id=%s live_status=%s import=%s delete=%s",
        account_id, status or ("live" if result.get("ok") else "failed"),
        imported.get("status"), deleted.get("status"),
    )
    return {"handled": True, "imported": imported, "deleted": deleted}
