# -*- coding: utf-8 -*-
"""把新注册账号的 access token 同步到 ChatGPT2API。"""
import logging

import requests

from config import chatgpt2api as _cfg
from config.browser import USER_AGENT

logger = logging.getLogger(__name__)


def sync_account(access_token: str) -> dict:
    """同步一个 access token；失败不抛出，不能影响注册结果。"""
    if not _cfg.ENABLE_CHATGPT2API_SYNC:
        return {"status": "skipped", "ok": False, "message": "ENABLE_CHATGPT2API_SYNC=False"}
    if not access_token:
        return {"status": "skipped", "ok": False, "message": "access_token 为空"}
    if not _cfg.CHATGPT2API_BEARER:
        return {"status": "skipped", "ok": False, "message": "CHATGPT2API_BEARER 为空"}

    try:
        response = requests.post(
            _cfg.CHATGPT2API_ACCOUNTS_URL,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Authorization": f"Bearer {_cfg.CHATGPT2API_BEARER}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            json={"tokens": [access_token], "accounts": []},
            timeout=_cfg.CHATGPT2API_TIMEOUT,
        )
    except requests.RequestException as exc:
        return {"status": "failed", "ok": False, "message": f"{type(exc).__name__}: {exc}"}

    message = (response.text or "")[:200]
    if response.ok:
        return {"status": "success", "ok": True, "http_status": response.status_code, "message": message}
    return {"status": "failed", "ok": False, "http_status": response.status_code, "message": message}
