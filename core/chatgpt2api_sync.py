# -*- coding: utf-8 -*-
"""ChatGPT2API 账号同步：导入 access token、删除旧账号、扫描远端异常账号。"""
import logging
import threading

import requests

from config import chatgpt2api as _cfg
from config.browser import USER_AGENT
from core import db

logger = logging.getLogger(__name__)

# 远端账号列表里表示「异常」的状态文案。
ABNORMAL_STATUS = "异常"

_PENDING_LOCK = threading.Lock()
# account_id -> 远端待处理的旧 access token。
# ponytail: 只保存在进程内存，WebUI 重启后需要重新点一次「C2A异常」筛选；
# 升级路径是把待处理 token 落到 accounts.payload。
_PENDING_OLD_TOKENS: dict[int, list[str]] = {}


def _headers() -> dict:
    return {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {_cfg.CHATGPT2API_BEARER}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


def _remember_old_tokens(account_id: int, tokens: list[str]) -> None:
    with _PENDING_LOCK:
        current = _PENDING_OLD_TOKENS.setdefault(int(account_id), [])
        for token in tokens:
            if token and token not in current:
                current.append(token)


def _old_tokens(account_id: int) -> list[str]:
    with _PENDING_LOCK:
        return list(_PENDING_OLD_TOKENS.get(int(account_id)) or [])


def forget_old_tokens(account_id: int) -> None:
    with _PENDING_LOCK:
        _PENDING_OLD_TOKENS.pop(int(account_id), None)


def sync_account(access_token: str, *, force: bool = False) -> dict:
    """导入一个 access token；失败不抛出，不能影响调用方主流程。

    force=True 用于手动触发的流程（如查活后重新导入），不受 ENABLE_CHATGPT2API_SYNC 限制。
    """
    if not force and not _cfg.ENABLE_CHATGPT2API_SYNC:
        return {"status": "skipped", "ok": False, "message": "ENABLE_CHATGPT2API_SYNC=False"}
    if not access_token:
        return {"status": "skipped", "ok": False, "message": "access_token 为空"}
    if not _cfg.CHATGPT2API_BEARER:
        return {"status": "skipped", "ok": False, "message": "CHATGPT2API_BEARER 为空"}

    try:
        response = requests.post(
            _cfg.CHATGPT2API_ACCOUNTS_URL,
            headers=_headers(),
            json={"tokens": [access_token], "accounts": []},
            timeout=_cfg.CHATGPT2API_TIMEOUT,
        )
    except requests.RequestException as exc:
        return {"status": "failed", "ok": False, "message": f"{type(exc).__name__}: {exc}"}

    message = (response.text or "")[:200]
    if response.ok:
        return {"status": "success", "ok": True, "http_status": response.status_code, "message": message}
    return {"status": "failed", "ok": False, "http_status": response.status_code, "message": message}


def delete_accounts(tokens: list[str]) -> dict:
    """按 access token 删除 ChatGPT2API 上的账号。"""
    cleaned = [str(t).strip() for t in tokens if str(t or "").strip()]
    if not cleaned:
        return {"status": "skipped", "ok": True, "message": "没有需要删除的 token"}
    if not _cfg.CHATGPT2API_BEARER:
        return {"status": "skipped", "ok": False, "message": "CHATGPT2API_BEARER 为空"}

    try:
        response = requests.delete(
            _cfg.CHATGPT2API_ACCOUNTS_URL,
            headers=_headers(),
            json={"tokens": cleaned},
            timeout=_cfg.CHATGPT2API_TIMEOUT,
        )
    except requests.RequestException as exc:
        return {"status": "failed", "ok": False, "message": f"{type(exc).__name__}: {exc}"}

    message = (response.text or "")[:200]
    if response.ok:
        return {
            "status": "success",
            "ok": True,
            "http_status": response.status_code,
            "deleted_count": len(cleaned),
            "message": message,
        }
    return {"status": "failed", "ok": False, "http_status": response.status_code, "message": message}


def list_remote_accounts() -> dict:
    """拉取 ChatGPT2API 账号列表。"""
    if not _cfg.CHATGPT2API_BEARER:
        return {"ok": False, "items": [], "error": "CHATGPT2API_BEARER 为空"}

    try:
        response = requests.get(
            _cfg.CHATGPT2API_ACCOUNTS_URL,
            headers=_headers(),
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

    items = data if isinstance(data, list) else (data or {}).get("items")
    if not isinstance(items, list):
        return {"ok": False, "items": [], "error": "ChatGPT2API 响应缺少 items 数组"}
    return {"ok": True, "items": items}


def scan_abnormal_accounts() -> dict:
    """拉取远端异常账号并匹配本地账号，记住待删除的旧 token 供查活后使用。"""
    listed = list_remote_accounts()
    if not listed.get("ok"):
        return {"ok": False, "error": listed.get("error") or "拉取 ChatGPT2API 账号失败"}

    grouped: dict[str, list[str]] = {}
    unmatched_remote = 0
    abnormal_count = 0
    for item in listed["items"]:
        if not isinstance(item, dict) or str(item.get("status") or "").strip() != ABNORMAL_STATUS:
            continue
        abnormal_count += 1
        email = str(item.get("email") or "").strip().lower()
        token = str(item.get("access_token") or "").strip()
        if not email or not token:
            unmatched_remote += 1
            continue
        tokens = grouped.setdefault(email, [])
        if token not in tokens:
            tokens.append(token)

    account_ids: list[int] = []
    matched: list[dict] = []
    archived_count = 0
    for email, tokens in grouped.items():
        acc = db.get_account_by_email(email)
        acc_id = int((acc or {}).get("id") or 0)
        if not acc_id:
            unmatched_remote += 1
            continue
        _remember_old_tokens(acc_id, tokens)
        account_ids.append(acc_id)
        archived = bool(acc.get("archived"))
        if archived:
            archived_count += 1
        matched.append({
            "id": acc_id,
            "email": acc.get("email") or email,
            "token_count": len(tokens),
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


def handle_live_check_finished(account_id: int, result: dict | None = None) -> dict | None:
    """查活结束后同步 ChatGPT2API：成功=导入新 token 并删旧号，已废=只删旧号。

    其他失败（网络/CF 拦截等）不动远端账号，旧 token 继续保留以便重新查活。
    只处理点过「C2A异常」筛选、记录过旧 token 的账号。
    """
    old_tokens = _old_tokens(account_id)
    if not old_tokens:
        return None

    result = result or {}
    status = str(result.get("status") or "").strip()
    if not result.get("ok") and status != "deactivated":
        return {"handled": False, "reason": f"查活状态 {status or 'failed'}，保留 ChatGPT2API 账号"}

    new_token = str(result.get("access_token") or "").strip() if result.get("ok") else ""
    imported = sync_account(new_token, force=True) if new_token else {
        "status": "skipped",
        "ok": False,
        "message": "账号已废，跳过导入",
    }
    # 导入失败时不能删旧号，否则远端会一个可用账号都不剩。
    if new_token and not imported.get("ok"):
        return {"handled": False, "imported": imported, "reason": "导入新 token 失败，保留旧账号"}

    deleted = delete_accounts([t for t in old_tokens if t != new_token])
    if deleted.get("ok"):
        forget_old_tokens(account_id)
    logger.info(
        "[ChatGPT2API] 查活后同步 account_id=%s live_status=%s import=%s delete=%s",
        account_id, status or ("live" if result.get("ok") else "failed"),
        imported.get("status"), deleted.get("status"),
    )
    return {"handled": True, "imported": imported, "deleted": deleted}
