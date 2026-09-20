# 全功能与生产上线审查（2026-09-18，2026-09-20 复核）

> 本报告标题保留原审查日期；正文按 2026-09-20 当前工作区源码、未提交改动、配置和测试文件刷新。报告只判断仓库实现和可复核的本地证据，不代替目标 Linux 主机、真实 Google 账号或充值上游的验收。

## 1. 结论

**No-Go：当前工作区不能证明已具备真实充值或完整自动化业务的生产放行条件。**

当前代码已经实现了以下基础：

- Flask 应用工厂区分 development、production、testing；非测试环境要求管理员密码，production 额外要求至少 32 字节 `SECRET_KEY`、至少 16 字符非示例 `ADMIN_PASSWORD`、有效 Fernet 格式的 `GMAIL_TOKEN_ENCRYPTION_KEY`，并拒绝 `RECHARGE_MODE=mock` 或未知值。
- 充值服务区分 `disabled`、`mock`、`live`。live 任务先写入数据库 `pending`，网络 I/O 在互斥锁外执行；网络/上游不确定时写入 `unknown`，查询可将上游状态回写。challenge、OAuth state、任务所有权、运行任务和充值/账单变更均有数据库模型。
- web 与独立 `app.worker` 共享 `RuntimeJob`/`RuntimeState`，`/health/ready` 会检查数据库、worker 心跳、维护失败、自动化锁和 Gmail 动作重试状态。
- 管理 API 需要 Flask session；公共充值创建/查询接口匿名可用，但撤回/关闭须匹配任务号、卡密、邮箱和原创建会话 HMAC 所有权（或管理员会话）。写请求还需要 JSON、`X-Requested-With`、可信 Origin/Fetch 元数据，并按 IP 和卡密做数据库限流。
- live 账单取消/恢复使用 `recharge_billing_mutations` 持久化凭证摘要、业务意图、UUID 操作号、单次 claim 的 `lease_token`、租约及 `pending/unknown/done`；摘要键由长期 `GMAIL_TOKEN_ENCRYPTION_KEY` 派生，查询和失败回写通过快照/CAS 避免旧请求覆盖重新占用的操作。
- production 页面响应包含 CSP，脚本与连接仅允许同源；充值协议由前端标签/属性/URL 白名单转换为 React 节点，敏感输入默认掩码显示。

仍然不能放行的原因：

- `RECHARGE_UPSTREAM_URL` 只有通用 HTTP JSON 适配层，没有在仓库中定义或验证真实上游的鉴权、签名、幂等、退款和数据处理契约；live 只能在获授权的 sandbox 中验收。
- 充值蓝图仍是 CDK 履约/订阅工作台，不包含金额、币种、优惠、支付订单、支付渠道、支付 webhook 验签/去重、余额账户或账本原子更新；因此“金额输入 → 支付 → 回调 → 余额”链路仍为 P0 No-Go。
- 当前工作区没有完成目标 Linux 上的 Docker 构建、容器运行、systemd 启停、HTTPS/OAuth 回调、备份恢复、容量和故障演练。Windows 沙盒的临时目录 ACL 及 Node `spawn EPERM` 限制了本地部分命令，不能替代这些证据。
- 生产数据库由 `python -m app.manage init-db` 使用 `create_all()` 初始化，并只额外补齐 `gmail_executions.lease_token`；仓库没有通用迁移版本链。已有数据库升级前必须备份并人工核对表结构。
- 正式基线首次创建 `recharge_billing_mutations` 时会包含账单 `lease_token`；若曾运行中间开发版本并已有不含该列的同名表，`create_all()` 不会补列，必须停服务并在保留未决记录的前提下受控迁移。
- 新的任务所有权依赖浏览器 session；清 Cookie、换浏览器、轮换 `SECRET_KEY` 或历史任务缺少 `recharge_task_access` 行时匿名危险操作会失败关闭，只能由管理员核对处理。这不是长期客户账户恢复机制。
- 账号密码、恢复邮箱和 TOTP secret 在 SQLite 模型中仍以字段形式保存；接口会对锁定账号脱敏/拒绝读取，但数据库文件权限、密钥轮换和保留策略必须由部署方落实。
- 充值任务的卡密和邮箱仍按模型字段保存，未完成存量敏感字段加密迁移；KYC 当前只限制为 HTTP/HTTPS URL，上游是否抓取以及 SSRF 边界仍需真实契约确认。
- mock 账单下载输出 TXT，live 账单下载明确返回 503；这不是已验证的真实 PDF 发票或支付凭证。

## 2. 代码与目录事实

### 2.1 应用入口和运行组件

| 组件 | 实际入口或路径 | 当前行为 |
| --- | --- | --- |
| Flask 工厂 | `app/__init__.py` | `create_app('development'|'production'|'testing')`；注册主页面、管理 API 和充值蓝图 |
| 本地入口 | `run.py` | `load_dotenv()` 后创建 app；直接运行监听 `127.0.0.1:8002` |
| 数据库初始化 | `python -m app.manage init-db` | 调用 `db.create_all()`（包括 `recharge_task_access`、`recharge_billing_mutations`），再为 `gmail_executions` 补 `lease_token` 列 |
| 后台 worker | `python -m app.worker` | 单实例锁、队列消费、Gmail 同步/维护、live 充值对账；`--check` 检查最近 30 秒心跳 |
| web 进程 | `gunicorn -c deploy/gunicorn.conf.py run:app` | 默认 `127.0.0.1:8002`，gthread，默认 2 workers、4 threads |
| 健康端点 | `GET /health/ready` | 数据库、worker、配置的 Gmail 客户端文件可读性、维护失败计数、仍占用 `active_key` 的失败自动化任务，以及启用规则下带执行记录的失败 Gmail 动作均符合条件时 200，否则 503 |
| SQLite 备份 | `deploy/backup_database.py SOURCE DESTINATION` | 只读打开源数据库；目标父目录须存在、目标文件须不存在，先创建 0600 文件再执行 SQLite backup 和 integrity check |

### 2.2 主要模型

当前新增或参与运行流程的模型包括：

- 账号与历史：`Account`、`AccountHistory`；
- Gmail：`GmailConnection`、`GmailWatch`、`GmailRule`、`GmailTaskLog`、`GmailActionConfirmation`、`GmailExecution`；
- 自动化任务：`GooglemailTask`、`RuntimeJob`、`RuntimeState`；
- 充值：`RechargeTask`、`RechargeOperation`、`RechargeMutation`、`RechargeTaskAccess`、`RechargeBillingMutation`、`OneTimeToken`；
- 限流：`RequestLimit`、`LoginAttempt`。

`OneTimeToken` 保存 token 的 SHA-256 摘要、可空的绑定摘要、JSON payload、过期时间和消费时间；原始 token/challenge 不写入任务模型。`RechargeTaskAccess` 只保存任务号及创建会话 HMAC，`RechargeBillingMutation` 只保存账单凭证 HMAC 和操作状态，二者都不保存原始 bearer。`Account.to_dict()` 对锁定账号清空密码、恢复邮箱和 secret；导出端点会把锁定账号的密码和 secret 写成 `******`，但数据库原值仍需部署权限保护。

`GmailActionConfirmation` 与 `GmailTaskLog` 一对一关联，保存 `status`、`reviewer`、`note`、请求时间和审核时间；`GmailTaskLog.to_dict()` 会嵌入 confirmation 对象。

## 3. 接口与行为核对

### 3.1 管理接口

`app/routes/api.py` 中除登录、登出、鉴权检查、Gmail Pub/Sub webhook 和 OAuth callback 外，接口均要求 `session['authenticated']`。除 Pub/Sub webhook 这个外部推送例外外，写请求还经过 `app/services/request_security.py`：

- Origin 存在时必须与当前请求的 scheme/netloc 相同；
- `Sec-Fetch-Site` 为 `cross-site` 或 `same-site` 时拒绝；
- 必须带 `X-Requested-With: XMLHttpRequest`（Pub/Sub webhook 不适用）；
- 充值接口默认按 IP 和卡密各限 60 次/分钟，超限返回 429 和 `Retry-After: 60`。

主要管理功能是账号 CRUD/批量导入/出售状态/导出/统计、Gmail 消息/标签/规则/watch、批量 OAuth、Googlemail 自动化、安全中心和收信守护。Gmail 服务读取连接时会拒绝对应的锁定账号；锁定账号 API 不能读取 2FA、历史或敏感导出内容；删除账号前会取消关联自动化任务。

### 3.2 充值接口与状态

充值蓝图在 `app/routes/recharge.py` 下挂载 `/api/recharge`。配置与响应事实如下：

- `GET /config`、`/features` 不要求管理员登录；`/agreement` 虽为 GET 仍调用 `ensure_enabled()`，disabled 模式返回 503。`/config` 的 `data` 为 `{features, dual_mode:true, mode, version:'1.0.0'}`，开关字段位于 `features`。
- `GET /stats/avg-processing-time` 也调用 `ensure_enabled()`，接受 `product`（默认 `gpt`）和 `category`（默认 `card`），但当前返回固定的六项秒数常量，参数不参与计算。
- `POST /redeem-codes/validate`：disabled 返回 503；mock 按卡密文本生成沙箱套餐；live 调用上游 `/user/redeem-codes/validate`。
- `POST /submission-challenges`：challenge 有效 300 秒，绑定会话、模式、卡密、凭证、套餐和 `is_renewal`。
- `POST /tasks`：校验卡密、套餐、凭证、邮箱、协议确认、邮箱确认和 challenge；mock 返回 `TK-MOCK-...` 并写入 `is_mock=true`；live 先写 `TK-LIVE-...` 的 `pending` 记录，再向上游 `/user/tasks` 发送 `idempotency_key` 和 `client_task_no`。
- `GET /tasks/<task_no>`：按当前模式隔离任务；返回结果隐藏 `redeem_code`、`account_email`、`notify_email`。live 会按结构化上游任务号或卡密查询并回写。
- `POST /tasks/lookup` 和 `/tasks/lookup-batch`：单查支持卡密或 `TK-` 前缀任务号（`TASK-` 不按任务号识别）；批量输入去重且最多 50 个。live 批量结果必须与请求侧去重集合一一对应，拒绝漏项、重复、未知卡密和非对象项，并按请求顺序输出；无本地任务的公开结果还必须带有效 `plan_type`。
- `POST /tasks/recall`、`POST /tasks/close`：要求 `task_no`、`redeem_code`、`email` 和 `confirmed=true`；任务目标和当前模式必须匹配，且请求须来自创建任务的会话或管理员，否则统一返回 403。live 变更由 `RechargeMutation` 先占用操作，再调用上游，异常状态必须通过查询确认。
- `POST /billing/query`、`cancel-subscription`、`resume-subscription`：要求凭证；取消/恢复必须 `confirmed=true`。mock 状态只保存在进程内 `_mock_subscriptions`；live 先从数据库占用操作号再转发，超时进入 `unknown`，相反动作被阻断，只有明确达到动作目标的查询才能完成对账。
- `POST /tasks/invoice/download`：仅 mock 模式可用；标识从 JSON body 读取，必须命中当前 mock 任务或当前会话账单事实，返回 UTF-8 TXT。live 返回 503，不能对外宣称已完成真实发票下载。
- `POST /billing/invoice-file`：返回本地 `/api/recharge/tasks/invoice/download` 的 POST 方法和 JSON payload；它不会从第三方下载 PDF，也不会把账单标识放入 query。

### 3.3 OAuth、Gmail 与队列

- `GmailService` 使用 `GMAIL_CLIENT_SECRET_FILE` 和 `GMAIL_TOKEN_ENCRYPTION_KEY`；token 以 Fernet 加密保存。该 Fernet 密钥还派生 live 账单凭证的 HMAC 身份键，不能按普通可丢弃配置轮换。`GMAIL_REDIRECT_URI` 存在时覆盖 Gmail OAuth 自动回调 URL。
- `OAuthStateManager` 的 state 写入 `one_time_tokens`，TTL 1800 秒；若 session 中存在 state，则 query state 必须匹配，随后还必须在数据库中已注册、未过期且未消费，callback 才会消费它。无 session state 的后台/无头回调仍可凭数据库中的有效注册 state 继续；session 不匹配、未注册或过期的 state 会被拒绝且不消费，重复回调也会被拒绝。
- `GMAIL_PUBSUB_TOPIC` 是 watch 必填配置；webhook 校验 email/historyId，队列模式下写入 `gmail_notification` 任务。
- `RuntimeQueue` 持久化 `googlemail`、`oauth`、`gmail_notification`、`gmail_sync` 任务；worker 重启时将 Gmail 同步任务重新排队，将无法安全恢复的自动化标为 failed 并保留必要的任务锁。
- `GmailExecution` 为同一连接/规则/邮件建立执行键和租约，维护循环会重试到期动作；这只说明代码具备幂等和租约机制，未证明真实 Gmail 权限和 API 行为。

## 4. 配置、依赖和版本

### 4.1 环境变量

当前源码和部署模板读取的关键变量如下：

| 变量 | 实际用途与约束 |
| --- | --- |
| `FLASK_ENV` | `development`、`production` 或 `testing`；systemd/Compose 生产设为 `production` |
| `ADMIN_PASSWORD` | 非测试必填；production 至少 16 字符，不可含首尾空白或示例值 |
| `SECRET_KEY` | production 必填，UTF-8 编码至少 32 字节，不可使用示例值；用于 session 和 challenge 绑定 |
| `GMAIL_TOKEN_ENCRYPTION_KEY` | production 必填且必须可由 `cryptography.fernet.Fernet` 解析；所有实例一致并长期保存，同时是 Gmail/队列解密和 live 账单凭证 HMAC 的根密钥 |
| `GMAIL_CLIENT_SECRET_FILE` | Google OAuth client JSON 路径；Compose 容器内默认 `/app/credentials.json` |
| `GMAIL_REDIRECT_URI` | 可选，覆盖 Gmail OAuth 自动回调 URL |
| `GMAIL_PUBSUB_TOPIC` | 首次创建或续租 Gmail watch 时必需的 Pub/Sub topic；未启用 watch 时可不配 |
| `GMAIL_PUBSUB_VERIFICATION_TOKEN` | 启用 Pub/Sub webhook 时必填；query `token` 必须与它匹配，否则生产返回 401/503 |
| `RECHARGE_MODE` | 默认 `disabled`；testing 为 `mock`；production 只允许 `disabled` 或 `live` |
| `RECHARGE_UPSTREAM_URL` | live 充值 HTTP JSON 上游地址，非测试环境必须显式配置；未配置时不会回退到默认真实上游地址；鉴权方式未在仓库固定 |
| `DATABASE_URL` | 可选 SQLAlchemy URI；未设置时使用 `instance/accounts.db` |
| `TRUSTED_PROXY_CIDRS` | 只有来自这些网段的代理才会应用 `X-Forwarded-*`；Compose 默认 `172.30.8.1/32`，systemd 模板为 loopback |
| `HEADLESS`、`PROXY` | Playwright 无头和代理选项，由部署/Node 子模块读取；它们不是 Flask `Config` 中的环境读取项 |
| `GUNICORN_BIND`、`GUNICORN_WORKERS`、`GUNICORN_THREADS`、`GUNICORN_TIMEOUT`、`GUNICORN_LOG_LEVEL` | Gunicorn 绑定、并发和日志配置 |

不要把真实密钥写入仓库、镜像层或文档示例；Compose 使用 ${VAR:?message} 对三个核心密钥做必填检查。

`RECHARGE_RATE_LIMIT_ENABLED`、`RECHARGE_RATE_LIMIT`、`BACKGROUND_TASK_MODE`、`AUTO_CREATE_DB`、`GOOGLEMAIL_EXECUTION_ENABLED` 和 `MAX_CONTENT_LENGTH` 是 `app/config.py` 中的代码配置常量或按配置类覆盖项，不会从环境变量直接读取；需要调整时应修改对应配置类并重新验证。

### 4.2 依赖版本

- Python 依赖由 `requirements.txt` 固定为 Flask 3.0.0、Flask-SQLAlchemy 3.1.1、Flask-CORS 4.0.0、SQLAlchemy 2.0.51、pyotp 2.9.0、python-dotenv 1.0.0、google-api-python-client 2.187.0、google-auth-httplib2 0.2.0、google-auth-oauthlib 1.2.2、cryptography 46.0.1；Docker 另外安装 `gunicorn==23.0.0`。
- `googlemail/package.json` 固定 `otplib 13.4.1`、`playwright 1.60.0`，开发测试使用 `vitest 4.1.10`；Node engine 为 `>=20.19.0`。
- `frontend/package.json` 使用 React/React DOM `^18.2.0`、Vite `^4.4.5`、Tailwind `^3.3.3`、`@vitejs/plugin-react ^4.0.3`、`lucide-react ^0.263.1` 等范围版本；生产构建输出到 `static/`。
- `Dockerfile` 基于 `python:3.11-slim-bookworm`，安装 Node.js 20.x、Playwright Chromium，并以非 root `googlemanager`（UID/GID 10001）运行 Gunicorn。`deploy/setup-server.sh` 同样检查/安装 Node.js 20.x。
- `npm audit --omit=dev` 对 frontend 和 googlemail 均报告 0 个生产依赖漏洞；完整审计仍报告 frontend 6 个开发/构建依赖告警（4 high、2 moderate）和 googlemail 4 个开发/测试依赖告警（1 high、3 moderate）。它们不进入当前生产依赖集合，但开发机或预览服务暴露时仍有风险，升级 Vite/PostCSS 与 Vitest 前必须单独做兼容性回归。

## 5. 部署方式

### 5.1 Docker Compose

`docker-compose.yml` 定义三个服务：

- `initialize` 使用 `python -m app.manage init-db`，成功后 `google-manager` 才启动；
- `google-manager` 使用 Gunicorn，容器端口 8002 只绑定宿主 `127.0.0.1:8002`，挂载 `instance`、`googlemail/output`、`googlemail/runtime` 和只读 `credentials.json`；
- `worker` 使用 `python -m app.worker`，依赖 web 服务启动，30 秒心跳检查使用 `python -m app.worker --check`。

web/worker 共享相同环境变量和 SQLite 卷；Compose 已向各服务透传 `GMAIL_PUBSUB_TOPIC` 和 `GMAIL_PUBSUB_VERIFICATION_TOKEN`。Compose 健康检查使用 `/health/ready`；该端点在 queue 模式下要求 worker 心跳。镜像构建上下文由 `.dockerignore` 排除 `.env`、凭据、数据库、运行目录、依赖目录、测试和文档；秘密应在运行时注入。

### 5.2 Linux systemd + Nginx

`deploy/setup-server.sh` 在 Ubuntu/Debian 上安装 Python/Node 20.x、Playwright Chromium 和前端构建依赖，创建 `googlemanager` 专用用户，生成 0600 的 `.env`，并注册两个 systemd 单元：

- `deploy/systemd/google-manager.service`：执行 `python -m app.manage init-db` 后启动 Gunicorn；
- `deploy/systemd/google-manager-worker.service`：启动持久 worker，停止超时 60 秒；
Nginx 配置文件由部署方手动安装；脚本不会安装 Nginx。手工配置可使用 `deploy/nginx/google-manager.conf`：80 重定向到 443，反代到 `127.0.0.1:8002`，设置 HTTPS、请求体上限 2M 和受信任转发头。

安装脚本不会替用户生成 Google Cloud `credentials.json`；该文件必须由部署方安全放置并设置为 `googlemanager` 可读。Nginx 证书路径仍是模板中的 `/etc/letsencrypt/live/your-domain.com/...`，上线前必须替换域名并运行实际 TLS 验证。

### 5.3 数据库初始化、备份和回滚

生产 `AUTO_CREATE_DB=False`，必须显式执行 `python -m app.manage init-db`。该命令不是版本化迁移系统；已有数据库升级前应：

```bash
mkdir -p /secure/backup
backup="/secure/backup/accounts-$(date +%Y%m%d-%H%M%S).db"
python deploy/backup_database.py instance/accounts.db "$backup"
python -m app.manage init-db
# 启动 web/worker 服务后再执行以下两项检查
python -m app.worker --check
curl -f http://127.0.0.1:8002/health/ready
```

备份目标父目录必须已存在且目标文件不能已存在。脚本先创建 0600 目标文件，再执行 SQLite backup 和完整性检查；检查失败可能留下该目标文件。恢复时先停止 web/worker，将备份复制为数据库文件并重新执行健康检查。上述 Linux 命令尚未在本工作区执行，不能写成部署成功证据。

## 6. 验证状态

### 已执行且结果明确

| 命令 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pip check` | 通过：未发现破损依赖 |
| `docker compose config --quiet` | 通过；当前 Compose 文件无解析错误或 `version` 废弃警告 |
| `python -m unittest discover -s tests -p 'test_*.py'` | 通过：稳定代码快照 213 tests，退出码 0；存在既有 `datetime.utcnow()` DeprecationWarning 与 SQLite ResourceWarning |
| `node --test tests/account-import.test.mjs tests/googlemail-local-copy.test.mjs tests/api-service.test.mjs` | 通过：41 tests |
| `npm --prefix frontend run build` | 通过：Vite 生产构建输出到 `static/` |
| `node --test tests/frontend-ui.test.mjs` | 通过：正式生产构建产物 43 tests；页面响应带 production CSP，API 使用 fixture |
| `npm --prefix googlemail test` | 通过：6 files / 47 tests |
| `npm --prefix frontend audit --omit=dev --json` | 通过：生产依赖 0 个漏洞；完整审计仍有 6 个开发/构建依赖告警 |
| `npm --prefix googlemail audit --omit=dev --json` | 通过：生产依赖 0 个漏洞；完整审计仍有 4 个开发/测试依赖告警 |
| `create_app('testing')` 路由枚举 | 成功加载 Flask 路由表 |
| 源码/模型/配置静态核对 | 已完成；未使用真实凭证执行外部业务，未读取生产数据库或真实账号 |
| `git -c core.whitespace=cr-at-eol diff --check` | 通过：未发现空白错误 |

稳定代码快照共 344 项自动化测试通过（Python 213、根目录 Node.js 41、Googlemail 47、Playwright UI 43）。测试使用虚构凭证、临时数据库或隔离 fixture；Playwright 页面响应带 production CSP，但 API 由 fixture 提供。这些证据仍不覆盖真实上游、真实支付、Linux 部署或真实第三方服务。

### 尚未形成可放行证据

- Docker 镜像 build/run、Linux 部署和真实第三方服务仍未在本轮完成；本地 Windows 通过的测试不能替代这些验收证据。
- Docker 镜像 build/run、Compose 三服务、systemd 启停、Nginx TLS、`/health/ready` worker 心跳、数据库恢复和跨进程重启尚未在 Linux 目标环境验证。
- Google OAuth、Gmail Pub/Sub、真实上游充值、真实账单/发票、真实浏览器账号和容量/限流压测均未执行。

推荐的本地验证命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
node --test tests/account-import.test.mjs tests/googlemail-local-copy.test.mjs tests/api-service.test.mjs
npm --prefix googlemail test
node --test tests/frontend-ui.test.mjs
npm --prefix frontend run build
.\.venv\Scripts\python.exe -m pip check
docker compose config --quiet
```

## 7. 放行门槛

1. 在无外网的测试配置中，验证 disabled 返回 503、mock 不访问网络、live 异常保持 unknown、challenge/OAuth state 跨进程一次性消费、任务所有权失败关闭、账单 lease/CAS 和任务状态不被迟到响应覆盖。
2. 使用获授权且可回滚的上游 sandbox，确认 URL、服务间鉴权、幂等键、任务号关联、取消/关闭语义、超时后的核对接口、KYC URL 处理和数据最小化；禁止用 mock 结果代替。
3. 在 Linux 目标环境完成镜像构建和运行，确认非 root 权限、Playwright 浏览器路径、`credentials.json` 权限、web/worker 单实例、健康检查和日志脱敏。
4. 完成 `accounts.db` 备份/恢复、`init-db` 前后两张新增表及账单 `lease_token` 核对、密钥轮换、Nginx HTTPS/OAuth 回调和数据库权限演练。Fernet key 轮换前必须用旧版本/旧密钥结清全部 `pending`/`unknown` 账单操作；若曾使用旧临时摘要算法或无 lease 列的中间版本，不得删除/重建表来绕过核对。
5. 只有以上证据与对应回归退出码齐全，且用户明确批准真实操作后，才可从 No-Go 进入发布评审。

报告不对真实第三方平台、生产网络或所有潜在安全问题作保证；任何后续源码、配置或依赖变更都需要重新复核。
