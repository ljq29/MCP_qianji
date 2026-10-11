# Qianji SDK — 钱迹记账 API 客户端与服务

逆向钱迹 APP 请求签名，封装为 Python SDK、本地 HTTP API 和 HTTP MCP 服务。

当前统一版本：`0.0.30`。

## 功能

- 纯 Python 实现钱迹请求签名算法。
- 覆盖 112 个上游接口。
- 提供 typed SDK 方法、HTTP 镜像路由、通用 API 透传和 MCP 工具。
- 支持多钱迹账号，按 `Authorization` 认证头区分请求账号。
- SQLite 持久化 session、设备 ID 和 token，token 过期后尝试自动恢复。
- FastAPI HTTP 服务，内置操作台、Swagger UI、ReDoc。
- MCP Streamable HTTP 服务，可接入支持 remote MCP 的客户端。

## 版本规则

- Python 包和运行时 API 版本的唯一来源是 `qianji/version.py` 的 `__version__`。
- `pyproject.toml` 通过 Hatch dynamic version 读取 `qianji/version.py`，不要在 `pyproject.toml` 里手写版本号。
- Docker 默认标签、`Dockerfile` 的 `APP_VERSION` / `IMAGE_REF` 默认值、`docker-compose.yml` 的 `QIANJI_IMAGE_TAG` 默认值、README 示例必须与 `__version__` 保持一致。
- 镜像名固定为 `mcp_qianji:<version>`。
- 发布新版本时先改 `qianji/version.py`，再同步 Docker 和 README 默认标签，最后运行测试。

当前镜像：

```bash
mcp_qianji:0.0.30
```

## Docker 部署

默认 `docker-compose.yml` 接入 Traefik：

- AppName: `mcp_qianji`
- Domain: `qianji.example.com`（默认值，用 `DOMAIN` 变量覆盖为你的域名）
- 内部端口: `50003`
- 外部网络: `traefik`
- HTTPS entrypoints: `https,https8443`
- HTTP 自动重定向到 HTTPS

启动前确保 Traefik 网络已存在：

```bash
docker network create traefik
```

构建镜像并启动（compose 已声明 `build: .`，构建产物标签与 `image:` 一致，`pull_policy: if_not_present` 会直接使用本地镜像）：

```bash
docker compose build
docker compose up -d
```

覆盖域名、应用名或镜像标签：

```bash
DOMAIN=qianji.example.com \
AppName=mcp_qianji \
QIANJI_IMAGE_TAG=0.0.30 \
docker compose up -d
```

本地构建镜像：

```bash
docker build \
  --build-arg APP_VERSION=0.0.30 \
  --build-arg IMAGE_REF=mcp_qianji:0.0.30 \
  -t mcp_qianji:0.0.30 .
```

不依赖 Traefik 直接运行（独立部署）：

默认 `docker-compose.yml` 只接入外部 Traefik 网络、不发布宿主端口。没有 Traefik 的环境用 standalone override 叠加启动，它会把 50003 发布到宿主机并移除 Traefik 网络与 labels（`docker-compose.standalone.yml` 的 `!reset` 语法需要 Docker Compose v2.24.4 及以上）：

```bash
docker compose -f docker-compose.yml -f docker-compose.standalone.yml up -d
# 本地构建镜像时：
docker compose -f docker-compose.yml -f docker-compose.standalone.yml up -d --build
```

也可以不用 compose，直接 `docker run`：

```bash
docker run -d --name qianji-sdk \
  -p 50003:50003 \
  -v qianji-data:/app/data \
  mcp_qianji:0.0.30
```

环境变量可以通过仓库根目录的 `.env` 文件注入（compose 自动读取），模板见 `.env.example`：

```bash
cp .env.example .env
```

服务地址：

- 操作台：`https://qianji.example.com/`
- 服务说明：`https://qianji.example.com/about`
- API 文档：`https://qianji.example.com/docs`
- ReDoc：`https://qianji.example.com/redoc`
- MCP HTTP：`https://qianji.example.com/mcp`
- 健康检查：`https://qianji.example.com/healthz`

## 首次运行自检

服务启动后按以下顺序验证：

1. 健康检查：`curl http://localhost:50003/healthz` 应返回 `{"ok": true}`（独立部署或本地开发用 localhost；Traefik 部署换成对应域名）。
2. 浏览器打开「账号管理」页 `http://localhost:50003/account`，用钱迹账号 + 密码（或 MD5 `pwd_hash`）登录；再打开操作台 `http://localhost:50003/`，确认能看到近期流水。
3. 打开 `http://localhost:50003/mcp-config`，页面会基于当前登录凭据生成带真实 `Authorization` 头的 MCP 客户端配置；把它粘贴到你的 MCP 客户端（Claude Desktop、Kimi Code 等）即可，不需要手填 token。
4. 在 MCP 客户端里调用 `get_login_status` 或 `list_recent_bills`，能返回数据即接入成功。

## 本地开发

需要 Python >= 3.10。

```bash
cd MCP_qianji
pip install -e ".[test]"
qianji-api
```

也可以直接运行脚本：

```bash
python scripts/run_api.py
```

默认监听 `127.0.0.1:50003`（仅本机访问；Docker 镜像内默认 `0.0.0.0`）。可通过环境变量覆盖：

```bash
QIANJI_HOST=0.0.0.0 QIANJI_PORT=50003 qianji-api
```

启动后访问：

- 操作台：`http://localhost:50003/`
- API 文档：`http://localhost:50003/docs`
- MCP HTTP：`http://localhost:50003/mcp`

## 配置项

完整模板见 `.env.example`（`cp .env.example .env` 后按需修改）。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `QIANJI_HOST` | `127.0.0.1`（Docker 镜像内为 `0.0.0.0`） | HTTP API 监听地址 |
| `QIANJI_PORT` | `50003` | HTTP API 监听端口 |
| `QIANJI_DB_PATH` | 项目根目录 `.qianji.db`，Docker 为 `/app/data/.qianji.db` | SQLite session 存储路径 |
| `QIANJI_LOG_LEVEL` | `INFO` | 日志级别（`DEBUG`/`INFO`/`WARNING`/`ERROR`） |
| `QIANJI_TIMEOUT` | `30.0` | 上游钱迹请求超时（秒） |
| `QIANJI_MAX_CONNECTIONS` | `20` | 上游 HTTP 连接池最大连接数 |
| `QIANJI_MAX_KEEPALIVE` | `10` | 上游 HTTP 连接池最大保活连接数 |
| `QIANJI_MCP_MODE` | `advanced` | MCP 默认工具范围：`read`、`write`、`advanced`、`full` |
| `QIANJI_BILL_CACHE_TTL` | `300` | 账单缓存软 TTL（秒）：查账时缓存超过此时长会自动增量同步；`0` 禁用（信任缓存直到显式同步）。可按账号用 `set_bill_cache_ttl` 覆盖 |
| `QIANJI_FILE_RETENTION_DAYS` | `7` | 导出账单和报表图片生成的临时文件保留天数；服务启动和生成新文件时会清理过期文件 |
| `QIANJI_PUBLIC_BASE_URL` | 空（相对路径） | 生成 `/files/{file_id}` 下载链接时使用的对外 base URL；REST 导出（`/bills/export`）和报表图片下载链接同样生效 |
| `QIANJI_MCP_AUTH_FAILURE_LIMIT` / `QIANJI_MCP_AUTH_FAILURE_WINDOW` | `10` / `60` | MCP 入口认证失败限流：窗口（秒）内连续失败达到上限后锁定 |
| `QIANJI_AUTH_FAILURE_LIMIT` / `QIANJI_AUTH_FAILURE_WINDOW` | `30` / `300` | REST 入口认证失败限流：窗口（秒）内连续失败达到上限后锁定 |
| `QIANJI_FORWARDED_ALLOW_IPS` | 空（不信任代理头） | 信任的反向代理 IP（逗号分隔）；默认不信任 `X-Forwarded-For`，限流按直接连接 IP 计数。Traefik 等反代部署必须显式设置 |
| `QIANJI_SESSION_MAX_AGE_DAYS` | `30` | 已保存 session 的最大复用天数，超过后强制重新登录；`0` 禁用 |
| `QIANJI_QINIU_UPLOAD_URL` | `https://upload.qiniup.com` | 账单图片上传的七牛上传地址，可按区域覆盖 |
| `MCP_PATH` | `/mcp` | HTTP MCP 路径 |
| `MCP_HOST` | `127.0.0.1` | 独立 MCP HTTP 服务监听地址（compose 不读，仅手动 export 或本地 `qianji-mcp-http` 生效） |
| `MCP_PORT` | `50004` | 独立 MCP HTTP 服务监听端口（同上，compose 不读） |
| `QIANJI_RELOAD` | 未启用 | 本地开发时启用 uvicorn reload |
| `QIANJI_IMAGE_TAG` | `0.0.30` | docker-compose 使用的镜像标签 |
| `PUID` / `PGID` | `1000` / `1000` | compose 中容器进程 UID/GID（与命名卷属主对齐） |
| `TZ` | `Asia/Shanghai` | compose 中容器时区 |

## 认证

REST API 和 HTTP MCP 都使用同一种认证头。只支持带前缀的 `pwd_hash` 凭据，不再接受明文密码或无前缀 token。

```bash
PWD_HASH="$(python - <<'PY'
import hashlib
print(hashlib.md5('your_password'.encode()).hexdigest())
PY
)"
AUTH="Authorization: Bearer qj1.md5.your@email.com~${PWD_HASH}"
# 或：
TOKEN="$(printf 'your@email.com~%s' "$PWD_HASH" | base64)"
AUTH="Authorization: Bearer qj1.md5-b64.${TOKEN}"
```

首次请求会用认证头中的钱迹账号和 `pwd_hash` 登录；后续请求复用 SQLite 中保存的 session。
SDK 中明文密码登录使用 `await client.login(email, password)`；如果调用方已经持有钱迹 MD5 `pwd_hash`，使用 `await client.login_md5(email, pwd_hash)`，避免把 `pwd_hash` 当明文再次哈希。

## REST API 示例

登录状态：

```bash
curl http://localhost:50003/auth/status -H "$AUTH"
```

显式登录（通常不需要，首次带认证头的请求会自动登录）：

```bash
curl -X POST http://localhost:50003/auth/login \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"email":"your@email.com"}'
```

`POST /auth/login` 的 `password` 字段是可选的二次校验：传入明文密码时服务端会计算 MD5 并与 Bearer 凭据中的 `pwd_hash` 比对，不一致返回 401；不传则只校验请求体账号与 Authorization 账号一致。

查询（`GET /bills`、`/bills/range`、`/bills/search` 返回的账单 `id` 均为字符串，避免 19 位整数在 JSON/JavaScript 中丢精度）：

```bash
curl http://localhost:50003/assets -H "$AUTH"
curl "http://localhost:50003/categories?type=0" -H "$AUTH"
curl http://localhost:50003/books -H "$AUTH"
curl http://localhost:50003/bills -H "$AUTH"
curl "http://localhost:50003/bills/range?start_time=1780243200&end_time=1782835199&book_id=-1" -H "$AUTH"
```

记账和修改账单：

```bash
curl -X PUT http://localhost:50003/tags/enabled \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"enabled":true}'

curl -X POST http://localhost:50003/bills \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"bill_type":"expense","amount":25.5,"occurred_at":"2026-04-12T19:30:00+08:00","asset_id":123,"category_id":60559217,"remark":"午餐","tags":["工作餐"]}'

curl -X PATCH http://localhost:50003/bills/1781362099609157301 \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"bill_type":"expense","amount":28.0,"remark":"午餐更新","tags":["工作餐","报销"]}'
```

退款、报销、借贷（账单 ID 路径参数按字符串使用）：

```bash
# 对某笔账单创建退款
curl -X POST http://localhost:50003/bills/1781362099609157301/refunds \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"amount":25.5,"occurred_at":"2026-04-13T10:00:00+08:00","asset_id":123,"remark":"午餐退款"}'

# 更新退款单
curl -X PATCH http://localhost:50003/refunds/1781362099609157302 \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"source_bill_id":"1781362099609157301","amount":25.5,"occurred_at":"2026-04-13T10:00:00+08:00","remark":"午餐退款（更正）"}'

# 创建报销（关联一笔或多笔源账单）
curl -X POST http://localhost:50003/reimbursements \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"source_bill_ids":["1781362099609157301"],"amount":25.5,"occurred_at":"2026-04-14T09:00:00+08:00","asset_id":123,"remark":"差旅报销"}'

# 更新 / 取消报销
curl -X PATCH http://localhost:50003/reimbursements/1781362099609157303 \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"source_bill_ids":["1781362099609157301"],"amount":25.5,"occurred_at":"2026-04-14T09:00:00+08:00","remark":"差旅报销（更正）"}'
curl -X POST http://localhost:50003/reimbursements/cancel \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"reimbursement_bill_ids":["1781362099609157303"]}'

# 创建借出资产并查询借贷资产列表
curl -X POST http://localhost:50003/assets/loans \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"name":"借给张三","amount":500,"startdate":"2026-04-01","remark":"月底还"}'
curl http://localhost:50003/assets/loans -H "$AUTH"

# 记录一笔收款（还回借出的钱）
curl -X POST http://localhost:50003/loans/456/collections \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"amount":500,"occurred_at":"2026-04-30T20:00:00+08:00","to_asset_id":123,"remark":"张三还款"}'
```

语义写入路由使用 `bill_type`、`amount`、`occurred_at`、`asset_id`、`category_id`、`from_asset_id`、`to_asset_id` 等字段，不再接受 `money`、`type`、`cateid`、`assetid`、`bill_time` 这类上游 raw 字段；raw 字段仍可通过镜像路由和 `/api/{ctrl}/{act}` 使用。`PATCH /bills/{bill_id}` 需要带 `bill_type`，用于明确调用 `update_expense`、`update_income` 或 `update_transfer`，不会改变原账单类型。`tags` 字段是标签名称列表，不是标签 ID，缺失标签会自动创建。

语义写入统一返回 `{ok, command, resource, ids, data, raw_result}`，其中账单 ID 会作为字符串返回：

```json
{
  "ok": true,
  "command": "create_expense",
  "resource": "bill",
  "ids": {
    "bill_id": "1781362099609157301",
    "new_ids": ["1781362099609157301"]
  },
  "data": {
    "bill_type": "expense",
    "amount": 25.5,
    "occurred_at": "2026-04-12T19:30:00+08:00",
    "book_id": -1,
    "asset_id": 123,
    "category_id": 60559217,
    "remark": "午餐",
    "tags": ["工作餐"],
    "tag_ids": ["2606166a30efaea0beb4525"]
  },
  "raw_result": {}
}
```

多账户使用方式是更换 `Authorization` 头：

```bash
AUTH2='Authorization: Bearer qj1.md5-b64.BASE64_ACCOUNT_TILDE_PWD_HASH'
curl http://localhost:50003/auth/accounts -H "$AUTH2"
curl http://localhost:50003/assets -H "$AUTH2"
```

通用 API 透传：

```bash
curl -X POST http://localhost:50003/api/saving/list \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"params":{}}'
```

上游路径镜像：

```bash
curl -X POST http://localhost:50003/asset/list \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"status":0}'

curl -X POST http://localhost:50003/book/submit \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"v":"{\"name\":\"新账本\"}"}'
```

## MCP 集成

HTTP MCP 使用 Streamable HTTP transport。服务地址：

```text
https://qianji.example.com/mcp
```

**不要手填占位 token。** 打开操作台的 `/mcp-config` 页面（如 `https://qianji.example.com/mcp-config`，本地为 `http://localhost:50003/mcp-config`），用真实凭据通过认证后，页面会生成带真实 `Authorization` 头的 MCP JSON 配置和 agent 接入提示，可选择工具模式（`read`/`write`/`advanced`/`full`）。生成的配置形如：

```json
{
  "mcpServers": {
    "qianji": {
      "type": "streamable-http",
      "url": "https://qianji.example.com/mcp",
      "headers": {
        "Authorization": "Bearer qj1.md5-b64.<由 /mcp-config 页面生成>"
      }
    }
  }
}
```

### 两个 MCP 入口，用哪个

- **主服务挂载的 `/mcp`（端口 50003）**：`qianji-api` / Docker 镜像默认入口，与 REST API、WebUI 操作台同进程。绝大多数场景用这个，只部署一个服务即可同时获得操作台、REST 和 MCP。
- **独立 MCP 服务 `qianji-mcp-http`（端口 50004）**：`python scripts/run_mcp_http.py` 启动，只跑 MCP、不带 WebUI 和 REST 路由。适合"只要 MCP 工具、不想暴露操作台和 HTTP API"的受限部署，或需要把 MCP 单独扩缩/挂到独立域名的场景。

两个入口共享同一份 `QIANJI_MCP_MODE` 配置、SQLite session 和凭据模型（`MCP_HOST`/`MCP_PORT`/`MCP_PATH` 只作用于独立服务）。

`QIANJI_MCP_MODE` 控制 MCP 暴露的工具：

| 模式 | 工具范围 |
|------|----------|
| `read` | 登录、账户切换、账单查询（`list_recent_bills`/`get_bills_range`/`get_bill_by_id`）、资产/分类/账本/标签/标签开关/货币查询 |
| `write` | `read` + 安全新增/更新账单工具（`create_expense`/`create_income`/`create_transfer`、`update_expense`/`update_income`/`update_transfer`）+ `sync_bills` + `set_bill_cache_ttl` + `set_tags_enabled` |
| `advanced` | `write` + 退款、报销、借贷资产、本金、收款、还款等业务写入工具 |
| `full` | 所有工具，包括底层兼容/管理员工具 `create_bill`、`update_bill`、`delete_bill`、`call_api` 和 112 个上游动态工具 |

默认是 `advanced`，兼容旧版 `write` 的业务写入范围；不暴露底层 `create_bill`、`update_bill`、`delete_bill` 或通用 `call_api`。如果只想给 agent 一个低噪音的核心记账面，可以把客户端 URL 写成 `https://qianji.example.com/mcp?mode=write`。URL query 的 `mode` 只对当前 MCP 请求生效；未传 `mode` 时使用服务默认值，非法值会返回 MCP/JSON-RPC 错误。注意 mode 只控制工具列表的可见性，不是权限边界——任何持有效 token 的客户端都可以自助切换到 `full`，详见「安全说明」。

### 账单查询与同步

面向 agent 的工具都返回结构化 JSON。查账与同步语义是分开的：

- **查账**：`list_recent_bills(limit, cursor?)`、`get_bills_range(start_at, end_at, limit, cursor?)`、`get_bill_by_id(bill_id)`。统一返回 `{ items: [Bill], next_cursor, has_more }`，用 `next_cursor` 翻页可取到全部账单。这些工具读取本地 SQLite 缓存，首次调用会自动从上游同步；之后缓存超过软 TTL（`QIANJI_BILL_CACHE_TTL`，默认 300 秒）才自动增量同步。查账工具可传 `refresh=true` 绕过 TTL 强制同步一次。
- **账单 ID**：MCP 中所有账单 ID 都按字符串暴露和返回，例如 `"1781668838987000000"`。钱迹账单 ID 通常是 19 位整数，超过 JavaScript 安全整数范围；调用 `get_bill_by_id`、`update_expense`、`create_refund`、`update_bill` 等工具时请给账单 ID 加引号。资产、分类、账本等普通 ID 仍按数字使用。
- **同步**：`sync_bills(book_id, full?)` 是唯一暴露增量同步细节的工具，把上游账单拉入本地缓存。同步是**服务器驱动**的：一次 `book_id=-1` 的同步会遍历账户下所有账本（含自定义账本），逐页回传服务端给的 `pageoffset`/`pagesign`，并按服务端返回的多账本 `lasttimes` 记录增量水位线。普通查账不需要手动调用它。
- **缓存新鲜度**：`get_bill_cache_ttl` / `set_bill_cache_ttl(seconds)` 按账号读写软 TTL（`0` 禁用、负数清除覆盖回落到 env 默认）。多端改账（如手机 App）后可调小 TTL 或用 `refresh`。
- **标签开关**：`get_tags_enabled` 可查询钱迹账号的标签开关，并会先刷新当前账号的上游配置；`set_tags_enabled(true|false)` 可显式打开或关闭钱迹 App 的标签功能入口。`login`、`get_login_status`、`list_accounts` 的返回里也会带 `tags_enabled`。SDK/API/MCP 不会在记账时自动打开该开关。
- **新增/更新**：核心 `write` 模式暴露 `create_expense`、`create_income`、`create_transfer` 以及 `update_expense`、`update_income`、`update_transfer`。金额必须为正数，`occurred_at` 必须是带时区的 ISO8601，例如 `2026-04-12T19:30:00+08:00`。更新工具不会改变账单类型，支出/收入/转账必须调用对应工具。标签使用 `tags` 名称列表，例如 `["咖啡", "出差"]`；后端会把名称解析为钱迹标签 ID，并自动创建缺失标签。
- **语义写入返回**：HTTP 和 MCP 的语义写工具统一返回 `{ok, command, resource, ids, data, raw_result}`。`ids` / `data` 里的账单 ID（如 `bill_id`、`new_ids`、`refund_bill_id`）都会转为字符串；`raw_result` 保留底层 SDK/上游响应。
- **进阶写入**：默认 `advanced` 模式额外暴露退款、报销、借贷资产、本金、收款、还款等工具；如需临时启用可用 `/mcp?mode=advanced`。
- **底层/破坏性工具**：`create_bill`、`update_bill`、`delete_bill`、`call_api` 只在 `full` 模式暴露；`create_bill` 保留为 low-level/admin compatibility tool，不建议 agent 普通记账使用。
- 所有带 `book_id` 的工具中，`-1` 表示默认账本（日常账本）。

每条 `Bill` 至少包含：`id`、`bill_type`、`type_code`、`counted`、`amount`、`currency`、`occurred_at`、`book_id`、`asset_id`、`from_asset_id`、`to_asset_id`、`category_id`、`remark`、`tag_ids`（`get_bill_by_id` 额外带 `raw`）。

## 常见错误对照表

| 状态码 | 含义 | 排查 |
|--------|------|------|
| 401 | 认证失败 | 检查 `Authorization` token 前缀格式：只接受 `qj1.md5.<account>~<pwd_hash>` 或 `qj1.md5-b64.<base64(account~pwd_hash)>`；不接受明文密码或无前缀 token。推荐用 `/mcp-config` 页面生成，避免手拼出错 |
| 409 | 业务状态冲突 | 记账/更新工具要求钱迹账号已打开标签开关时返回；先调 `PUT /tags/enabled`（或 MCP `set_tags_enabled(true)`）打开标签功能再重试 |
| 429 | 触发限流 | 服务全局限流 60 次/分钟、登录接口 10 次/分钟；降低请求频率后重试 |
| 502 | 上游钱迹服务故障 | 钱迹上游不可用或响应异常；稍后重试，持续出现可检查 `QIANJI_TIMEOUT` 是否过短及网络连通性 |
| 504 | 上游钱迹请求超时 | 上游在 `QIANJI_TIMEOUT`（默认 30 秒）内未响应；稍后重试，网络较差时可适当调大超时 |

## 安全说明

- `Authorization` 中的 `pwd_hash` 是钱迹上游登录使用的密码等价物；Base64 只是编码，不是加密。远程部署必须使用 HTTPS。
- SQLite 中保存的 `pwd_hash` 是钱迹上游登录使用的密码等价物，不是普通意义上的安全密码哈希；请保护 `.qianji.db` 和 Docker volume。
- 操作台可选择把凭据保存到浏览器本地存储。不要在共享电脑或不可信浏览器上启用记住凭据。
- HTTP API 默认监听 `127.0.0.1`（仅本机）；需要局域网/远程访问时显式设置 `QIANJI_HOST=0.0.0.0`，并务必配合 HTTPS 反向代理。Docker 镜像定位就是容器运行，镜像内默认 `0.0.0.0`。
- 默认**不信任** `X-Forwarded-For` 等代理头（`QIANJI_FORWARDED_ALLOW_IPS` 默认为空），限流按直接连接 IP 计数，避免伪造代理头绕过限流。经 Traefik 等反向代理部署时必须把代理出口 IP/网段显式写入 `QIANJI_FORWARDED_ALLOW_IPS`（逗号分隔），否则所有用户共享代理 IP 的限流额度。
- MCP 请求只能操作 Bearer token 对应的钱迹账号；即使服务中保存了多个账号，工具参数里的 `account_id` 也不能切换到其它账号。
- 账单导出和报表图片的 `/files/{file_id}` 下载链接是公开随机 URL，不需要 Authorization。不要把链接发给不可信对象；可用 `QIANJI_FILE_RETENTION_DAYS` 缩短保留期。
- `/about` 显示服务默认 MCP mode；URL query `?mode=...` 是单次请求覆盖，不会改变服务默认值。
- **MCP mode 不是权限边界。** `read`/`write`/`advanced`/`full` 只是工具可见性提示，用于降低 agent 工具列表噪音；任何持有有效 Bearer token 的客户端都可以在 URL 上加 `?mode=full` 自助切换到 `full`（含 `delete_bill`、`call_api`）。不要把它当成"只读账号"之类的访问控制手段；真正的边界是 token 对应的钱迹账号本身。
- `QIANJI_MCP_MODE=full` 会把底层创建、更新、删除和通用 API 能力放进默认工具列表，只建议在可信环境中启用。

## 项目结构

```text
MCP_qianji/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml            # Traefik 部署（默认）
├── docker-compose.standalone.yml # 无 Traefik 独立部署 override
├── .env.example                  # 环境变量模板
├── qianji/
│   ├── version.py          # 版本来源
│   ├── sign.py             # 签名算法
│   ├── auth_token.py       # Bearer token 解析/生成（qj1.md5. / qj1.md5-b64.）
│   ├── session.py          # SQLite session 管理
│   ├── client.py           # 核心客户端
│   ├── endpoints.py        # 112 个端点定义和动态注册
│   ├── constants.py        # 常量与超时/连接池/缓存 TTL 配置
│   ├── errors.py           # 异常体系
│   ├── bill_ops.py         # 账单业务操作
│   ├── bill_files.py       # 账单导出与报表生成
│   ├── file_service.py     # 导出临时文件与保留期清理
│   ├── image_upload.py     # 账单图片上传（七牛）
│   ├── tags.py             # 标签名称解析
│   ├── write_service.py    # 统一语义写入服务
│   ├── cli.py              # 命令行入口
│   ├── api/                # SDK typed mixin（asset/bill/budget/debtloan/refund/...）
│   ├── server/             # FastAPI 服务、REST 路由和操作台页面
│   └── mcp_server.py       # MCP 服务
├── scripts/
│   ├── run_api.py          # 主服务入口（REST + WebUI + /mcp，:50003）
│   └── run_mcp_http.py     # 独立 MCP 服务入口（:50004）
└── tests/
```

## 测试

```bash
python -m pytest -q
```

默认测试离线运行。真实钱迹 API 测试必须同时设置 `QIANJI_RUN_INTEGRATION=1`、`QIANJI_TEST_EMAIL` 和 `QIANJI_TEST_PASSWORD` 才会运行；未设置时会自动跳过。

integration 测试在同一轮 pytest 中共用一个临时 SQLite session，首次测试登录后后续测试复用同一设备 ID，并在该轮测试全部结束后调用 `logout()` 清理当前测试设备。可用 `QIANJI_TEST_DB_PATH` 指定测试 session 数据库位置。

## 签名算法

```python
from qianji.sign import generate_sign

reqidv2, tok = generate_sign("account", "login")
# tok = MD5(reqidv2 + "1172020" + ctrl + MD5(reqidv2 + "michaeljackson") + act)
```
