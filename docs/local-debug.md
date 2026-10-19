# 本地启动与调试

日常使用看根目录 `README.md`。本文只写在本机把服务拉起来、打断点、看日志。

## 环境

- Python **3.12**（`.python-version`）。`pyproject.toml` 要求 `>=3.12`。
- Node.js **18+**。协议注册会调用 `sentinel/sentinel-runner.js` 生成 Sentinel token。找不到 `node` 时设置 `NODE_EXECUTABLE` 为绝对路径。
- 浏览器驱动按需：Roxy 需要本机 RoxyBrowser API；Cloak / Browser Use / Skyvern 见 `README.md`。
- 套餐查询默认走本机上游代理 `http://127.0.0.1:7897`（`.env` 里的 `PLAN_CHECK_UPSTREAM_PROXY`、`PROXY_POOL_UPSTREAM_PROXY`）。本机没有这个代理时，相关请求会失败，不影响先把 WebUI 启动起来。

依赖以 `requirements.txt` 为准。`pyproject.toml` 的 `dependencies` 是空的，`uv sync` **不会**装上 Flask 等运行时包。

## 第一次准备

在项目根目录：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
node --version
cp .env.example .env
```

`.env` 已存在就不要覆盖。至少确认：

```dotenv
WEBUI_AUTH_CODE=你的授权码
```

没配授权码时，进程会在日志里打印一次性临时码。优先级：命令行 `--auth-code` > 环境变量 `WEBUI_AUTH_CODE` > `.env`。

验证解释器：

```bash
.venv/bin/python -c 'import sys, flask; print(sys.executable); print(flask.__version__)'
```

macOS 升级后如果 `.venv` 里的 Python 软链失效：

```bash
rm -rf .venv
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 调试时怎么启动

调试用前台进程，方便看 stdout、挂断点。后台脚本 `./webui.sh` 会 `nohup` 拉起进程，断点挂不上去。

先确认没有已经在跑的实例（同一端口有文件锁，再启动会直接退出码 2）：

```bash
./webui.sh stop
```

前台启动 WebUI：

```bash
.venv/bin/python web.py --verbose
```

默认只监听 `http://127.0.0.1:5000`，不自动打开浏览器。常用参数：

| 参数 | 作用 |
|---|---|
| `--host 127.0.0.1` | 只本机。`0.0.0.0` 会暴露到局域网 |
| `--port 5000` | 端口。换端口同时换锁文件 |
| `--verbose` | 根日志调到 DEBUG |
| `--open-browser` | 启动约 1 秒后打开浏览器 |
| `--auth-code` | 覆盖本次授权码 |

`web.py` 里 Flask 固定 `debug=False`。不要改成 debug reloader：重载会拉起第二个进程，注册线程池和定时器会跑两份。

CLI 单次注册（和 WebUI 进程独立，不要同时对同一批邮箱开跑）：

```bash
.venv/bin/python main.py -n 1 --verbose
```

已有账号单独补跑 Codex：

```bash
.venv/bin/python tools/test_codex_oauth.py --email 已注册邮箱 --verbose
```

## PyCharm

1. Interpreter 选项目根目录 `.venv/bin/python`。
2. Run/Debug Configuration：Script path 填 `web.py`，Working directory 填项目根目录。
3. Parameters 填 `--verbose`。需要换端口时加 `--port 8000`。
4. 调试前执行 `./webui.sh stop`，否则会报「端口 5000 的 WebUI 已在运行」并以退出码 2 结束。
5. 断点打在入口附近即可跟进：`web.py` 的 `main`、`webui/app.py` 的 `create_app`、`core/registration_service.py` 的 `_run_one_job`。

CLI 调试把 Script path 换成 `main.py`，Parameters 用 `-n 1 --verbose`。

## 后台启动（不调试时）

```bash
./webui.sh start
./webui.sh status
./webui.sh logs
./webui.sh restart
./webui.sh stop
```

脚本优先用 `.venv/bin/python`。日志在 `logs/webui.log`，PID 在 `run/webui.pid`。

```bash
PORT=8000 VERBOSE=1 ./webui.sh start
HOST=0.0.0.0 PORT=5000 ./webui.sh restart
```

`VERBOSE=1` 等价于给 `web.py` 加 `--verbose`。

## 日志在哪

| 位置 | 内容 |
|---|---|
| 前台终端 | `web.py` / `main.py` 的进程日志 |
| `logs/webui.log` | `./webui.sh` 的 stdout/stderr |
| `注册日志/<任务uuid>.log` | WebUI 每个注册任务一条，只收该任务线程的日志 |
| `注册日志/codex-retry-<邮箱>.log` | Codex 补跑 |
| `注册日志/twofa-<邮箱>.log` | 2FA |
| `注册日志/live-check-<邮箱>.log` | 查活 |
| `注册日志/email-change-<账号id>.log` | 换绑邮箱 |

任务日志的 FileHandler 级别是 INFO。`--verbose` 只让控制台更细，任务文件里仍主要是 INFO。

运行时数据在项目根目录 `turb.sqlite3`（WAL）。首次启动会把旧 JSON/TXT 迁进去，之后不再读写那些文件。

## 调试开关

改 `.env` 后，WebUI 配置页保存会热加载。直接改文件时，正在跑的进程要重启才读到新值（`config/__init__.py` 启动时 `load_env(override=False)`，已在环境里的变量不会被 `.env` 盖掉）。

看页面卡在哪一步，把浏览器留着：

```dotenv
ROXY_KEEP_BROWSER_OPEN=True
ROXY_OPEN_HEADLESS=False
```

Cloak / Browser Use / Skyvern 对应 `CLOAK_KEEP_BROWSER_OPEN`、`BROWSER_USE_KEEP_BROWSER_OPEN`、`SKYVERN_KEEP_BROWSER_OPEN`。调试完改回 `False`，否则环境不关闭、不删除。

看流量和脚本是否执行（只对 Roxy / Cloak）：

```dotenv
BROWSER_TRAFFIC_DETAIL_LOG=True
BROWSER_JS_COVERAGE_LOG=True
```

任务结束日志里找 `[资源明细]`、`[JS执行汇总]`。

协议注册卡在 Sentinel 时，确认 `node --version` 可用，并看日志里的 `[SentinelRunner]`。子进程超时 60 秒。

## 启动失败时先看这些

- **端口的 WebUI 已在运行**：`./webui.sh stop`，或换 `--port`。锁文件在系统临时目录 `turb-gpt-free-register-web-<端口>.lock`。
- **`.venv` 失效或没有 Flask**：按上面重建虚拟环境，再装 `requirements.txt`。
- **未找到 Node**：安装 Node 18+，或设置 `NODE_EXECUTABLE`。只影响协议注册的 Sentinel，不影响 WebUI 页面本身打开。
- **授权码不对**：看 `.env` 的 `WEBUI_AUTH_CODE`。接口也可带 `X-Auth-Code` 或 `Authorization: Bearer <授权码>`。
- **改了 `config/*.py` 没生效**：WebUI 以 `.env` 覆盖为准。配置页保存会写 `.env` 并热加载；手改 `.py` 默认值时，同名 `.env` 项仍然优先。
