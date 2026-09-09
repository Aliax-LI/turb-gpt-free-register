# -*- coding: utf-8 -*-
"""注册成功账号同步到 ChatGPT2API 的配置。"""
from config.env_loader import apply_env_overrides

# 注册成功并保存账号后，是否同步 access token 到 ChatGPT2API。
ENABLE_CHATGPT2API_SYNC: bool = False

# ChatGPT2API 的账号接口：POST 导入 token、GET 拉账号列表、DELETE 删除旧账号。
CHATGPT2API_ACCOUNTS_URL: str = "https://chatgpt2api.343426.xyz/api/accounts"

# ChatGPT2API 管理端 Bearer Token；只保存在 .env。
CHATGPT2API_BEARER: str = ""

# 请求超时（秒）。
CHATGPT2API_TIMEOUT: int = 15

apply_env_overrides(globals(), {
    "ENABLE_CHATGPT2API_SYNC": "bool",
    "CHATGPT2API_ACCOUNTS_URL": "str",
    "CHATGPT2API_BEARER": "str",
    "CHATGPT2API_TIMEOUT": "int",
})
