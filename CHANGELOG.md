# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.0.30] - 2026-10-06

### Added
- 分类图标同源代理与缓存：新增公开路由 `GET /category-icons/{filename}`（`<img>` 不带 Authorization），依次查镜像内烘焙目录 `qianji/server/static/caticons/`、运行期缓存目录（`QIANJI_ICON_CACHE_DIR`，默认 DB 同目录 `caticons/`），未命中回源 `res3.qianjiapp.com`（失败回落 `res.qianjiapp.com`）；文件名严格白名单校验（防路径穿越），响应校验 PNG 且 ≤64KB，按文件名 in-flight 去重、回源超时 3s，尽力写缓存（只读文件系统直接透传不报错），命中返回 `Cache-Control: public, max-age=31536000, immutable`，总失败 404（前端渲染首字彩色圆兜底）。
- 新增 `scripts/prefetch_icons.py`：经 `_request_public("category","iconlist")` 取得全部 541 个图标 basename，并发 8 下载到包内静态目录，幂等跳过已存在、单失败仅告警不中断、输出 `manifest.json`；Dockerfile 在打包后执行（离线构建仅告警，运行期回源兜底），图标缓存目录已加入 `.gitignore`。
- 记账页交互重做：账单按日分组为白圆角卡（卡头 = 日期 + 当日收入/支出汇总，点卡头以该日期预填记一笔），账单行 = 4px 类型色条 + 分类图标 + 备注/资产 + 右对齐着色金额；点行弹底部预览层（金额 + 分类/类型/日期/资产/账本/备注/标签/ID 明细 + 编辑/复制为新账单/删除），点「编辑」才载入右侧表单；长按行进入批量模式（行首勾选圈、底部批量条：取消/全选/删除），批量删除走 `billbatch/deletev2` 并沿用二次确认。
- 资产查看：新增排序模式——拖拽手柄 + 底部「取消/保存」确认条，保存提交 `asset/reorder`（`v` = JSON 扁平 id 数组，形状与 App 端 `BaseAssetPresenter.reOrder()` 一致）；分组卡头支持折叠/展开。
- 卡片备份：点卡弹选项层（复制卡号/编辑/删除），新增搜索框与类型过滤 chips（类型 >2 时显示）；排序模式拖拽 + 确认条提交 `usercard/reorder`（`v` = JSON id 数组）。
- 分类管理：一级分类改为白卡（32px 图标圆 + 名称 + ⋯ + 折叠箭头），展开显示 5 列子分类图标格 + 尾部「＋添加子分类」；⋯/点按弹选项层（编辑/删除/转为二级分类/转为一级分类/更换所属分类，复用 `changelevel`/`movesubcate`/`deletev2`）；新增/编辑改用图标选择器（分组 chips + 宫格 + 搜索，数据来自 `/categories/icons`），替换 datalist 手填；排序模式拖拽 + 确认条，沿用 `category/reorder3` 的分组形状，移除 ↑/↓ 排序按钮。

### Changed
- 操作台视觉按钱迹 App 设计语言重做（限定 `.workspace` 作用域，`/about`、`/docs` 等页面不受影响）：配色/圆角/间距/字号令牌取自 jadx `res/values`（主色 `#3792fa`、支出红 `#eb595e`、收入绿 `#25b87a`、卡片圆角 8px 等），金额按账单类型着色，组件模式（日分组卡、底部弹层、确认条、批量条）对齐 App。
- 控制台 CSS/JS 从内联模板抽离为静态文件 `qianji/server/static/console/console.css` 与 `console.js`，页面只保留 boot 对象 `window.__QJ_BOOT__`，获得 ETag/长缓存；CSP 逐字节不变（`script-src 'self'` / `style-src 'self'` 已覆盖）。`test_homepage_is_login_console` 同步拆分为 HTML 结构断言 + 读盘 JS/CSS 不变量断言两个测试，鉴权/凭据/JSON 解析防护强度不变。
- 全局限流中间件豁免 `/static/` 与 `/category-icons/` 前缀：一次控制台加载会产生数十个静态资源与图标请求，与 API 调用共用同一 60 次/分/IP 预算会导致页面自我限流。

### Fixed
- `billbatch/deletev2`、`billbatch/modify` 的线上参数名与 App 不一致：App 实际发送 `billIds`/`values`（JSON 数组字符串，jadx `network/api/bill/e.java`），原先注册表声明的 `ids`/`v` 服务端不识别；现改为主参数 `billIds`/`values`，`ids`/`v` 保留为友好别名，既有调用不受影响。
- 分类编辑草稿在后台重渲染时丢失：重连、删除/移动结算等异步刷新会重建分类树，但从未捕获编辑器中未保存的输入；现 `renderCategoryTree` 在清空 DOM 前调用 `captureCategoryDraft` 保留名称/图标/父分类，且仅当屏幕上的编辑器仍属于当前编辑目标时才捕获（身份守卫），避免 ⋯→编辑 切换目标后旧值串入新草稿。

## [0.0.29] - 2026-09-29

### Changed
- 顶部导航栏统一为通栏布局：原先 `.topbar` 宽度分散在三处（操作台/账号管理页 `1760px`、其余页面 `1440px`），1920px 视口下后面 4 个按钮的右边缘会比前 2 个左移约 160px；现在 header 铺满视口、左右各留 28px 内边距，6 个页面右边缘完全一致，且与内容列宽解耦。
- 导航按钮改用自适应宽度（去掉写死的 `88px`）并常驻 `1px` 透明边框，hover/active 不再产生位移；激活项统一为浅灰药丸并显式 `text-decoration: none`。

### Fixed
- 顶部导航 markup 原先在 `pages.py` 中手工复制 6 份，导致页面之间样式漂移；现收敛为单一 `render_topbar()` 渲染函数（`_NAV_ITEMS` 为唯一来源），`/docs`、`/redoc` 的导航注入不再使用字符串替换 hack。

## [0.0.28] - 2026-09-29

### Added
- 资产查看支持详情页：点击资产行进入详情，展示余额/类型/币种/ID 指标卡与该资产的交易记录（`/bills/search?asset_ids=`，50 条/页「加载更多」），右侧可编辑名称与备注并保存（新增 `PATCH /assets/{id}` HTTP 路由与 SDK/WriteService 的 `update_asset`；借入/借出资产自动改走 `/assets/debts/{id}`、`/assets/loans/{id}`）。详情内账单只读，点击跳回「记账」页载入编辑表单；创建/修改/删除账单后详情自动刷新。
- 卡片备份支持编辑：右侧编辑器可新增/修改卡片（类型、卡名/银行、卡号、持卡人、备注、图片 URL），保存走 `usercard/submit`；每行新增「复制」（可读文本）与「编辑」按钮，编辑面板内提供「复制 JSON」。

### Changed
- 卡片备份展示修正与增强：卡片名改从真实字段 `bank` 解析并以类型作前缀（如 `[信用卡] 招商银行`），原先读取不存在的 `cardname`/`name` 等字段导致名称退化为「卡片 ID」；卡号由掩码改为完整显示、4 位分组等宽字体、点击即可全选复制；删除按钮从列表行收进编辑面板的「危险操作」区。
- 分类管理改为树状内联编辑：分类树支持 ▾/▸ 展开收起（展开状态存 localStorage，默认全展开），✏️ 在树内联展开编辑表单（名称、图标、父分类、↑↓ 排序、删除），＋ 在对应父分类下新增子类；父分类变更按 App 接口分派——一级↔二级走 `changelevel`、跨父级走 `movesubcate`，同级排序走 `reorder3`，编辑表单中未保存的改动在排序重渲染后保留。源码模式改为只读快照（文本缩进树 / JSON 双视图，可复制、可下载），不再支持源码编辑。

### Fixed
- 分类增删改的线上参数与 App 不一致：`category/submitv2` 应传 `v`（名称字符串）/`did`/`parentid`（>0 时）/`icon`（非空时），`category/deletev2` 应传 `did` + `deletebills`（+ 可选 `newcateid`）；原先传的是 `cateid`/`name`/`bookid`/`force`，服务端无法识别，导致分类新增/编辑/删除实际不生效。

## [0.0.27] - 2026-09-29

### Added
- 操作台（`/login`）工作区新增二级标签页，原「近期流水」整体归入「记账」标签，并新增三个功能页：
  - **资产查看**：顶部汇总资产合计 / 负债合计 / 净资产 / 账户数（按 `type`/`stype` 区分借贷与信用类负债），下方按资产类型分组展示余额、币种、隐藏标记与 ID，支持手动刷新。
  - **卡片备份**：展示卡包列表（卡名、银行、掩码卡号、持卡人、备注），支持一键导出全部卡片 JSON 备份文件，以及带确认的单卡删除（`usercard/delete`）。
  - **分类管理**：支出/收入切换的两级分类树，右侧编辑面板支持新增/编辑分类（名称、图标 datalist 取自 `category/iconlist`、父分类下拉，编辑时自动排除自身），删除失败时可二次确认强制删除；保存/删除后自动刷新分类并同步「记账」页的分类下拉框。
- 三个新标签页均按需懒加载：切换时自动补齐引用数据（资产/分类复用已加载的 refs，卡片独立缓存），断开连接或凭据变更时自动清空。

## [0.0.26] - 2026-09-28

### Fixed
- 统一六个页面顶栏链接样式：服务说明、API Docs、ReDoc 三个页面原先没有重置顶栏锚点样式，点击/激活导航标签时所有标签会出现浏览器默认下划线，品牌名「Qianji Bridge」还会显示为蓝色带下划线；现把 `.topbar a { color: inherit; text-decoration: none; }` 收敛到共享的 base.css，与操作台、账号管理、添加 MCP 页面表现一致。

## [0.0.25] - 2026-09-28

### Added
- Web UI 新增「账号管理」页（`/account`）：「连接钱迹账号」面板（账号/密码连接、本地账号记忆、MCP JSON 生成）整体从操作台迁入；操作台、服务说明、添加 MCP、API Docs、ReDoc 各页导航同步增加「账号管理」入口。

### Changed
- 操作台（`/login`）只保留「近期流水」工作区：页面加宽到 1760px，工作区改为撑满视口高度的弹性布局，账单列表与「记一笔」面板左右同步加宽，浏览器宽高合适时一屏即可完整展示，无需滚动页面；未连接账号时工作区保持可见并提示前往「账号管理」连接，不再整块隐藏（连接成功后才拉取分类/资产/账单数据）。

## [0.0.24] - 2026-09-28

### Fixed
- 「添加 MCP」页面宽度与其他页面不统一：该页面（含无凭据引导页）原先将 `.topbar`/`.shell` 覆盖为 1180px/760px，现统一回落到 base.css 的全局 1440px 约定，与操作台、服务说明、API Docs、ReDoc 四个页面一致。

## [0.0.23] - 2026-09-28

### Changed
- 操作台未连接钱迹账号时不再展示「近期流水」工作区，连接成功后才显示；断开连接、凭据变更或清除记忆时自动隐藏。

## [0.0.22] - 2026-09-28

### Changed
- 操作台「连接钱迹账号」面板改为全宽左右分栏布局：左侧为账号/密码/本地账号等登录字段（两列排布），右侧为 MCP JSON 配置卡，充分利用页面横向空间；移动端自动塌回单列。

## [0.0.21] - 2026-09-28

### Fixed
- 修复 CI：`test_endpoint_registry_matches_full_api_docs` 原先读取已从仓库删除的 `docs/api_endpoints_full.md`（541010b 备份到思源 Knowhow 后删除）导致 FileNotFoundError；现将删除前快照中的 112 个端点路径固化为测试常量，继续作为 `ENDPOINTS` 注册表的基准校验。

## [0.0.20] - 2026-09-28

### Changed
- 操作台布局调整：「连接钱迹账号」面板从左侧边栏移到「近期流水」工作区下方，页面改为单列纵向布局，登录面板居中限宽展示。

## [0.0.19] - 2026-09-28

### Changed
- Web UI 五个页面（操作台、服务说明、添加 MCP、API Docs、ReDoc）统一为同一套顶栏与配色：共享 base.css 的 `.topbar`/`.brand-mark` 品牌样式，移除服务说明页自带的 `.topnav` 与文档页注入的 `.qianji-doc-nav` 两套独立样式。

### Added
- Web UI 全部页面增加 favicon（`/static/docs/favicon.svg`），并新增 `/favicon.ico` 重定向路由。

### Fixed
- ReDoc 运行时不再从外部 CDN（cdn.redoc.ly）加载 logo：logo 已下载至本地 `/static/docs/redoc-logo-mini.svg` 并改写 `redoc.standalone.js` 引用，API Docs / ReDoc 至此完全离线可用。

## [0.0.18] - 2026-06-24

### Breaking Changes
- `GET /bills`、`/bills/range`、`/bills/search` 响应中的账单 `id` 从 JSON number 改为字符串（MCP 早已按字符串暴露），避免 19 位整数在 JSON/JavaScript 客户端丢精度。
- 默认不再信任 `X-Forwarded-For` 代理头（`QIANJI_FORWARDED_ALLOW_IPS` 默认空）；Traefik 等反代部署必须显式设置该变量，否则限流按代理 IP 计数。
- `/mcp-config` 无 Authorization 头时返回 200 引导页（不再 401）。
- 标签功能关闭时传 `tags` 现在返回 409（原先静默尝试上游调用）。
- `/bills/search` 的 `page_size` 上限从 5000 收紧为 500。

### Security
- 认证失败限流：REST 与 MCP 入口分别按窗口计数并在连续失败后锁定（`QIANJI_AUTH_FAILURE_LIMIT/WINDOW`、`QIANJI_MCP_AUTH_FAILURE_LIMIT/WINDOW`）。
- 新增统一安全响应头；已保存 session 增加最大复用天数限制（`QIANJI_SESSION_MAX_AGE_DAYS`，默认 30，0 禁用）。
- 图片上传校验 Pillow 解压炸弹；报表生成时区修正。

### Changed
- 上游错误码细分：超时返回 504，上游故障返回 502。
- 独立 MCP 服务监听地址默认改为 `127.0.0.1`（与 `qianji/cli.py` 实际默认一致）。
- Docker 镜像内置 `TZ=Asia/Shanghai` 并安装 `tzdata`；移除 `VOLUME /app/data`（避免裸 `docker run` 产生匿名卷，compose 已声明命名卷）；compose 健康检查端口改用 `${QIANJI_PORT:-50003}`。
- README 补退款/报销/借贷 curl 示例、504 错误码、代理头信任说明和新环境变量文档；`.env.example` 同步补全。

### Fixed
- 账单增量同步水位线在失败时回退，避免跳页丢账；同步与查询加并发锁；`delete_bill` 后正确失效本地缓存。
- 阻塞事件循环的同步调用改为线程执行。
- `TagFeatureDisabledError` 在服务层正确接通为 409。
- 兼容 fastapi 0.140 lazy include（`_IncludedRouter`）：全局限流不再依赖 slowapi 中间件的路由解析（该解析在新版本下静默失效），改为中间件内自建滑动窗口计数。

### Refactored
- `mcp_server.py` 改为表驱动注册（1979 → 1735 行），mcp 库私有 API 集中在 shim 区块。

### Added
- Gitea Actions CI（`.gitea/workflows/ci.yaml`）：ruff、mypy、pytest 门禁。

## [0.0.17] - 2026-09-08

### Security
- MCP endpoints now require bearer authentication and enforce per-IP rate limiting; unauthenticated or abusive callers get 401/429 instead of touching upstream.
- Bill IDs are generated with stronger uniqueness guarantees, fixing ID collisions when bills are created in quick succession.
- Loan/debt update operations now re-validate the target asset ownership and payload before submitting, closing an unsafe update path.

### Added
- CLI entry points now use argparse with proper `--help` and argument validation.
- `docker-compose.standalone.yml` override for deployments without Traefik: publishes port 50003 and strips the external `traefik` network and labels.
- `build: .` in `docker-compose.yml`, so users without private registry access can run `docker compose build`.
- `.env.example` template covering all documented environment variables (kept in git via a `.gitignore` exception).
- Dockerfile `HEALTHCHECK` hitting `/healthz` via urllib, and overridable `TZ` (default `Asia/Shanghai`) in compose.

### Changed
- Default bind host for local runs is now `127.0.0.1`; the Docker image and compose explicitly set `QIANJI_HOST=0.0.0.0` since containers must listen on all interfaces.
- Docker image CMD now runs `python scripts/run_api.py` instead of an inline `python -c`.
- `QIANJI_FILE_RETENTION_DAYS` compose default aligned to 7 days, matching code and README.
- README rewritten in parts: corrected repo directory name, refreshed project structure (incl. `jadx/` reference sources), dual MCP entry guidance, first-run self-check, common error table (401/409/429/502), and a clear note that MCP `?mode=` is a tool-visibility hint, not a permission boundary.
- Outdated feature-gap reports under `docs/` now carry a prominent "historical document" banner pointing at `TODO.md` / `CHANGELOG.md`.
- Bill export performance improved for large accounts.

### Fixed
- Tag-related operations that require the upstream tag switch now return HTTP 409 with an actionable error instead of a confusing generic failure.

### Refactored
- Duplicated logic across SDK/REST/MCP layers converged into shared helpers.

## [0.0.16] - 2026-06-22

### Added
- Unified semantic write command service (`qianji.write_service.WriteService`) backing both REST semantic write routes and MCP write tools, with a uniform `{ok, command, resource, ids, data, raw_result}` response shape and bill IDs returned as strings.

### Changed
- Bill export and report generation now paginate automatically, cap results at 50,000 rows, and flag truncation with `truncated`.
- Image upload HTTP route streams the request body and returns 413 beyond 20MB instead of buffering the whole file first.
- Generated-file retention default is now 7 days, with expired-file cleanup at startup and on file creation.
- Login now reuses the existing device record per account and cleans it up on logout, avoiding device accumulation upstream.

### Fixed
- `/mcp-config` now requires valid Authorization before rendering a real MCP config; the page only uses credentials from the current request and never exports saved sessions.
- Security fixes in export and upload handling.

## [0.0.15] - 2026-06-17

### Added
- `login_md5(email, pwd_hash)` SDK method for callers that already hold the upstream MD5 `pwd_hash`, avoiding double-hashing it as a plaintext password.

### Changed
- Bearer credentials are now restricted to the prefixed formats `qj1.md5.<account>~<pwd_hash>` and `qj1.md5-b64.<base64(account~pwd_hash)>`; plaintext passwords and unprefixed tokens are no longer accepted.

## [0.0.14] - 2026-06-17

### Added
- Accounting file workflows: bill search/export and report image generation (`bill_files.py`), backed by a generated-file service with retention-based cleanup (`file_service.py`) and public random `/files/{file_id}` download URLs.
- Bill image upload via Qiniu (`image_upload.py`, `QIANJI_QINIU_UPLOAD_URL` override).

### Fixed
- MCP bill IDs are now exposed and accepted as strings, preventing 19-bit-plus integer precision loss in JSON/JavaScript clients.
- Auth token parsing/generation hardening (`auth_token.py`).

## [0.0.13] - 2026-06-17

### Added
- Asset balance reconciliation support in the asset SDK facade, with corrected endpoint specs.

## [0.0.12] - 2026-06-17

### Added
- MCP mode `advanced` for the previous broad business-write tool set.
- Per-request MCP URL query overrides, e.g. `/mcp?mode=write`, `/mcp?mode=advanced`, and `/mcp?mode=full`.
- Web UI page `/mcp-config` for generating MCP JSON and an agent setup prompt with selectable mode.

### Changed
- Default MCP mode is now `advanced`.
- `write` MCP mode is narrowed to core bookkeeping tools plus sync/cache/tag controls.
- Login reuse now accepts a stored `pwd_hash` as the password-equivalent credential when a matching session exists.

## [0.0.11] - 2026-06-17

### Added
- Semantic MCP bill update tools: `update_expense`, `update_income`, `update_transfer` (type-safe updates that never change the bill type).
- Business API facades and routes for refunds, reimbursements, and debt/loan assets: SDK `RefundAPI`/`ReimbursementAPI`/`AssetAPI`/`DebtLoanAPI`, dedicated REST routes, matching MCP write-mode tools, and `GET /assets/loans` / `GET /assets/debts`.

### Fixed
- Tag create/update flow aligned with the official app's semantics; key endpoint field shapes (`baoxiao/baoxiao`, `asset/listloan`, `syncv2/pull`) corrected to match the real app forms.

## [0.0.10] - 2026-06-16

### Added
- Tag names in MCP/REST bill tools: tag name lists are resolved to upstream tag IDs with recursive group-context handling, and missing tags are auto-created.
- Tag switch exposed over HTTP API (`PUT /tags/enabled`, `get_tags_enabled`), with per-account `tags_enabled` surfaced in login/status responses.
- Persistent per-account user configs in the session store.

## [0.0.9] - 2026-06-16

### Added
- MCP safe import tools: `create_expense`, `create_income`, and `create_transfer`.

### Changed
- `write` MCP mode now exposes only safe create/import tools plus sync/cache TTL controls.
- `create_bill`, `update_bill`, `delete_bill`, and `call_api` are now full-mode/admin tools only.

## [0.0.8] - 2026-06-16

### Added
- Local SQLite bill cache backing agent-facing query tools, with keyset cursor pagination that walks every bill (no truncation).
- New MCP tools: `list_recent_bills`, `get_bills_range`, `get_bill_by_id`, `sync_bills`, `list_books`, `get_bill_cache_ttl`, `set_bill_cache_ttl`.
- Soft cache TTL (`QIANJI_BILL_CACHE_TTL`, default 300s) with per-account override stored in a generic `account_settings` table, plus a `refresh` flag on query tools to force an incremental sync.
- Normalized `Bill` shape with non-null `currency` + `currency_source`, and a `counted` flag (false for not-counted bills).
- REST `POST /bills/sync` endpoint.

### Changed
- `syncv2/pull` pagination now mirrors the official client: threads `pagesign`, echoes the server-returned `pageoffset`, and sends a multi-book `lasttimes` map so one `book_id=-1` sync walks every book. Fixes silent page truncation on large accounts.
- All agent-facing MCP tools now return structured JSON instead of formatted text.
- REST `GET /bills` and `/bills/range` are now cache-backed and return `{items, next_cursor, has_more}`.
- `update_bill` locates the original bill via the cache by id (no page scanning).

### Removed
- Dead `find_bill_by_id` page-scanning helper and the legacy `collect_bills_range`.

## [0.0.7] - 2026-06-16

### Added
- MCP `create_bill` support for historical bill time, transfer target asset, and comma-separated tags.

### Changed
- Docker image builds now copy source directly and run Python module entry points without building a project wheel.

## [0.0.6] - 2026-06-14

### Added
- Structured logging throughout SDK, server, and MCP modules (`QIANJI_LOG_LEVEL` env var)
- Request rate limiting on FastAPI server via slowapi (60/min global, 10/min on login)
- Configurable httpx timeout and connection pool (`QIANJI_TIMEOUT`, `QIANJI_MAX_CONNECTIONS`, `QIANJI_MAX_KEEPALIVE`)
- Async SQLite session storage via aiosqlite (non-blocking event loop)
- Ruff linter/formatter configuration
- Mypy type checker configuration with `py.typed` marker (PEP 561)
- Pre-commit hooks for ruff and mypy
- Complete `__init__.py` exports (UpstreamError, SessionManager, enums, etc.)
- CHANGELOG.md

### Changed
- `SessionManager` methods are now async (breaking change for direct callers)

### Fixed
- MD5 password hash function now documented as upstream protocol requirement

## [0.0.5] - 2026-06-14

### Changed
- Version bump and Docker/Compose/README sync

## [0.0.4] - 2026-06-14

### Added
- Traefik Docker Compose support

## [0.0.1] - 2026-06-14

### Added
- Initial SDK implementation with async client
- FastAPI HTTP API server
- MCP server with streamable HTTP transport
- Request signing (reverse-engineered)
- Multi-account session management with SQLite persistence
- Endpoint registry driving SDK methods, HTTP routes, and MCP tools
