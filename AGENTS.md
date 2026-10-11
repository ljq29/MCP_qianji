# AGENTS.md

本文件面向在本仓库中工作的 AI 编码 agent，假定读者对项目一无所知。

## 项目概述

**qianji-sdk**（当前版本 `0.0.30`）逆向了钱迹记账 App 的请求签名，并用纯 Python 重新实现（无 SO 模拟 / Frida / 模拟器），同一个 Python 包对外提供三种形态：

1. **强类型异步 SDK**：`QianjiClient`（`qianji/client.py`），基于 `httpx.AsyncClient`，覆盖 112 个上游接口。
2. **FastAPI HTTP 服务**：REST 语义路由 + 112 个上游路径镜像路由 + 通用透传 `POST /api/{ctrl}/{act}`，内置操作台 WebUI、Swagger UI、ReDoc，默认监听 `127.0.0.1:50003`。
3. **MCP 服务**：流式 HTTP 传输（Streamable HTTP），两个入口——主服务挂载的 `/mcp`（与 REST/WebUI 同进程，端口 50003）和独立服务 `qianji-mcp-http`（只跑 MCP，端口 50004）。两者共享同一份 SQLite 会话和凭据模型。

HTTP 服务与 MCP 服务同进程运行，共享同一个 `QianjiClient`。技术栈：Python >= 3.10，FastAPI / Starlette / Uvicorn / httpx / Pydantic v2 / FastMCP（`mcp[cli]`）/ aiosqlite（SQLite 持久化）/ slowapi（限流）/ Pillow（报表图片）。

## 构建与测试命令

```bash
pip install -e ".[test]"                    # 安装（含测试依赖）；开发依赖用 ".[test,dev]"
python -m pytest -q                         # 运行全部测试（默认离线，是常规验证的标准方式）
python -m pytest tests/test_endpoints.py -q # 单文件
python -m pytest tests/test_client.py::test_name -q  # 单测试
python scripts/run_api.py                   # 启动主服务（REST + WebUI + /mcp，:50003）
python scripts/run_mcp_http.py              # 启动独立 MCP 服务（:50004）
python -m ruff check .                      # 代码检查
python -m mypy qianji                       # 类型检查
```

- 集成测试（`tests/test_integration.py`，标记 `integration`）会请求真实钱迹 API，必须同时设置 `QIANJI_RUN_INTEGRATION=1`、`QIANJI_TEST_EMAIL`、`QIANJI_TEST_PASSWORD` 才会运行，否则自动跳过；可用 `QIANJI_TEST_DB_PATH` 指定测试会话数据库。
- `tests/test_endpoints.py` 会从 `ENDPOINTS` 自动生成覆盖（按 `ParamSpec` 提供样本值），新增端点声明无需手写测试即被覆盖。
- pre-commit 配置了 ruff（`--fix` + `ruff-format`）和 mypy（作用域 `qianji/`）。
- CI（`.github/workflows/ci.yml`，GitHub Actions）依次跑 ruff、mypy、pytest。

### 构建纪律（必须遵守）

- **不要构建 wheel 产物**，除非用户明确要求。常规验证只用 `python -m pytest -q` 和有针对性的测试。
- 不要运行 `python -m pip wheel`、`pip wheel`、`python -m build` 等打包命令；不要产生 `dist/`、`build/` 或 wheel 文件。
- Docker 镜像**禁止**通过 `pip install .` 等 PEP 517 流程安装本项目：Dockerfile 只安装第三方依赖，复制 `qianji/` 和 `scripts/` 源码后直接运行 `python scripts/run_api.py`。

### 版本纪律（必须遵守）

- 版本唯一来源是 `qianji/version.py` 的 `__version__`；`pyproject.toml` 通过 Hatch 动态版本读取它，**不要在 `pyproject.toml` 手写版本号**。
- 用户说"小版本号"/补丁版本：只把 `x.y.z` 的 `z` 加 1；次版本加 `y`；主版本加 `x`。
- 每次版本提升**必须**在 `CHANGELOG.md` 增加对应条目（Keep a Changelog 格式，内容用中文）。
- 发版顺序：先改 `qianji/version.py`，再同步 `Dockerfile` 的 `APP_VERSION`/`IMAGE_REF` 默认值、`docker-compose.yml` 的 `QIANJI_IMAGE_TAG` 默认值、README 示例（`tests/test_version.py` 会校验一致性），最后跑测试。镜像名固定为 `mcp_qianji:<version>`。

## 架构要点

### 端点注册表是唯一事实源

`qianji/endpoints.py` 的 `ENDPOINTS`（`EndpointSpec` 元组，约 112 条）驱动三个层面的生成：

- **SDK 方法**：`install_endpoint_methods(QianjiClient)` 为每条端点声明生成 `{ctrl}_{act}` 异步方法（如 `asset_list`）；`_is_handwritten` 跳过手写混入类（mixin）中已存在的同名方法，注册表不会覆盖更丰富的手写逻辑。
- **HTTP 路由**：`qianji/server/routes.py` 循环 `ENDPOINTS` 注册镜像路由 `POST /{ctrl}/{act}`，叠加在手写 REST 路由和通用透传 `POST /api/{ctrl}/{act}` 之上。
- **MCP 工具**：`qianji/mcp_server.py` 循环 `ENDPOINTS` 注册动态工具（返回 JSON 文本）；另有一组精选的高层工具返回结构化字典（标注 `-> dict[str, Any]` 让 FastMCP 产出结构化内容）。

**新增/修改上游端点时改 `ENDPOINTS`，不要手写方法**，除非确实需要自定义逻辑（参数整形、多次调用编排）。`ParamSpec` 控制别名（如 `book_id` → `bookid`）、`hash_if_alias`（自动 MD5 密码）、`auto`（会话自动填充如 `uid`）。

### 手写 SDK 混入类

`QianjiClient` 多继承 `qianji/api/` 下的混入类（`BillAPI`、`CategoryAPI`、`SyncAPI` 等，均扩展 `BaseAPI`），承载声明式端点表达不了的逻辑。账单的增删改是通过 `bill/syncall` 提交完整账单字典的 `bills.changelist`，没有专用端点。

### 账单缓存与查询层

上游**没有**账单列表/按 ID 查询端点，唯一读取路径是分页的 `syncv2/pull`。因此面向 agent 的账单读取走**本地 SQLite 缓存**（`bills_cache` / `bill_sync_state` 表，逻辑在 `qianji/bill_ops.py`）：

- `sync_bills_to_cache` 复刻官方 Android 客户端的**服务器驱动**分页：首页 `pageoffset=0`/`pagesign=""`，后续页回传服务端返回的 `bookid`/`pageoffset`/`pagesign`（不在本地自增）；`lasttimes` 是多账本映射，一次 `bookid=-1` 同步遍历所有账本。
- `ensure_bill_cache` 首次全量同步，之后按软过期时间（`QIANJI_BILL_CACHE_TTL`，默认 300 秒，`0` 禁用，可按账号覆盖）增量同步；`refresh=True` 强制同步。
- 查询用键集分页（`(bill_time, bill_id)` 倒序游标），返回 `{items, next_cursor, has_more}`。
- 保持"查询 vs 同步"分离：只有 MCP 的 `sync_bills` 暴露同步细节，普通记账工具不暴露 `hasmore`/`lasttimes`/`pageoffset`。

### 请求签名

`qianji/sign.py`：每个请求需要 `tok = MD5(reqidv2 + "1172020" + ctrl + MD5(reqidv2 + "michaeljackson") + act)`，`reqidv2 = MD5(uuid4)`。密码以 `MD5(明文)` 存储和发送。`client._request_raw` 组装设备/版本头（`constants.py`）并以表单编码提交。

### 会话、多账号与自动恢复

- `qianji/session.py` 用 SQLite（`.qianji.db`，`QIANJI_DB_PATH` 可改）持久化会话、设备 ID 和令牌；单进程可持多账号。
- 当前账号由 `_effective_account_id()` 决定：按请求的 `ContextVar`（`client.use_account(id)`）优先于进程级 `_current_account`。多账号操作务必包在 `with client.use_account(...)` 里。
- `client._request` 捕获 `AuthenticationError`（上游 `ec == 8888`）后自动刷新令牌 / 重新登录，调用方无需处理令牌过期。
- 上游 `ec` 码映射：`400404` → `SignatureError`，`8888` → `AuthenticationError`，其他非 200 → `QianjiError`（`errors.py`）；FastAPI 在 `server/app.py` 的异常处理器里转成 HTTP 状态码。

### 认证模型（HTTP + MCP 共用）

Bearer 令牌**就是**钱迹凭据本身，只接受带前缀格式：`Bearer qj1.md5.<account>~<pwd_hash>` 或 `Bearer qj1.md5-b64.<base64(account~pwd_hash)>`（`qianji/auth_token.py` 解析）。不接受明文密码或无前缀令牌。请求会用它登录/复用会话，操作硬绑定到该账号，`account_id` 不能切换到别的账号（返回 403）。没有独立 API key。

### 服务组装与 MCP 模式

`qianji/server/app.py` 是入口：`lifespan` 构建共享 `QianjiClient`、把 FastMCP 流式 HTTP 应用挂到 `/mcp`（路径由 `MCP_PATH` 控制）、迁移旧的 `.qianji_sessions/` JSON。`QIANJI_MCP_MODE`（`read`/`write`/`advanced`/`full`，默认 `advanced`）决定 MCP 工具可见范围，URL 查询参数 `?mode=...` 只对单次请求生效。**模式只是工具列表可见性提示，不是权限边界**——任何持有效令牌的客户端都能自助切到 `full`。

## 代码风格约定

- 全部 client/server/MCP 代码是 `async`；SDK 基于 `httpx.AsyncClient`。
- Ruff：行宽 120，目标版本 py310，启用 `E,F,W,I,UP,B`；`known-first-party = ["qianji"]`。E501 在 `qianji/server/pages.py`、`server/app.py`、`endpoints.py` 豁免，`B008` 在 `server/routes.py` 豁免。
- mypy：`disallow_untyped_defs = false`；`mcp.*`、`aiosqlite.*`、`slowapi.*`、`limits.*` 忽略缺失导入。
- 上游参数约定：复杂值（字典/列表）JSON 序列化、布尔值变 `"1"`/`"0"`、`v` 是提交型端点的兜底 JSON 载荷参数。
- 账单 ID 一律按**字符串**对外暴露和返回（19 位整数超出 JavaScript 安全整数范围）；资产、分类、账本等普通 ID 仍按数字。
- 语义写入路由/工具用 `bill_type`、`amount`、`occurred_at`、`asset_id` 等语义字段（原始上游字段只在镜像路由和 `/api/{ctrl}/{act}` 可用），统一返回 `{ok, command, resource, ids, data, raw_result}`；`tags` 是标签名称列表，缺失标签自动创建。

## 项目结构速览

```
qianji/
├── version.py        # 版本唯一来源
├── sign.py           # 签名算法
├── auth_token.py     # Bearer 令牌解析（qj1.md5. / qj1.md5-b64.）
├── session.py        # SQLite 会话/账单缓存表
├── client.py         # 核心客户端（含 install_endpoint_methods）
├── endpoints.py      # 112 个端点声明（唯一事实源）
├── constants.py      # 常量与超时/连接池/缓存过期时间配置
├── errors.py         # 异常体系
├── bill_ops.py       # 账单缓存同步与查询层
├── bill_files.py     # 账单导出与报表生成
├── file_service.py   # 导出临时文件与保留期清理
├── image_upload.py   # 账单图片上传（七牛）
├── tags.py           # 标签名称解析
├── write_service.py  # 统一语义写入服务
├── cli.py            # 命令行入口（qianji-api / qianji-mcp-http）
├── api/              # 手写 SDK 混入类（asset/bill/budget/debtloan/refund/...）
├── server/           # FastAPI 应用、REST 路由、操作台页面与静态资源
└── mcp_server.py     # MCP 服务（精选工具 + 动态端点工具）
scripts/              # run_api.py（:50003）/ run_mcp_http.py（:50004）
tests/                # pytest 测试
```

## 部署

- Docker 镜像基于 `python:3.11-slim`（摘要固定，更新需有意为之），非 root 运行（PUID/PGID 默认 1000），只装第三方依赖 + 复制源码，健康检查打 `/healthz`。
- 默认 `docker-compose.yml` 接入外部 Traefik 网络（不发布宿主端口，域名默认 `qianji.example.com`，用 `DOMAIN` 变量覆盖）；无 Traefik 环境叠加 `docker-compose.standalone.yml` 把 50003 发布到宿主机。
- 环境变量完整清单见 `.env.example` 和 README「配置项」一节；常用：`QIANJI_HOST`/`QIANJI_PORT`、`QIANJI_DB_PATH`、`QIANJI_MCP_MODE`、`QIANJI_BILL_CACHE_TTL`、`QIANJI_FORWARDED_ALLOW_IPS`。

## 安全注意事项

- `pwd_hash` 是钱迹上游登录的密码等价物（Base64 只是编码不是加密）；远程部署必须 HTTPS，保护好 `.qianji.db` 和 Docker 数据卷。
- HTTP API 默认只监听 `127.0.0.1`；需要远程访问时显式 `QIANJI_HOST=0.0.0.0` 并配 HTTPS 反代。
- 默认不信任 `X-Forwarded-For`（`QIANJI_FORWARDED_ALLOW_IPS` 默认空）；反代部署必须显式配置，否则限流按代理 IP 计数。
- 限流：全局 60 次/分钟、登录 10 次/分钟，认证失败还有独立锁定窗口（`QIANJI_*_AUTH_FAILURE_LIMIT/WINDOW`）。
- `/files/{file_id}` 下载链接是公开随机 URL（无需认证），保留期由 `QIANJI_FILE_RETENTION_DAYS` 控制。
- MCP 模式不是权限边界（见上）；`QIANJI_MCP_MODE=full` 会暴露 `delete_bill`、`call_api` 等破坏性工具，只在可信环境启用。
