# Antigravity 工作督办与验收台账

> 本台账按 2026-09-19 工作区源码、配置、测试和部署文件复核。它记录可从仓库复核的事实和仍未取得的证据，不代表已经启动后台监督，也不代表已经批准真实充值、订阅变更或生产发布。

## 当前结论

- 当前结论为 **No-Go**。代码已经具备充值模式门禁、持久化任务、一次性挑战令牌、数据库级运行队列、独立 worker 和就绪检查；真实上游契约、生产 Linux 运行、Google OAuth/Gmail 实际授权、备份恢复和容量验证仍未在本工作区完成。
- 非测试配置的 `RECHARGE_MODE` 默认值是 `disabled`；`TestingConfig` 明确使用 `mock`。生产配置只接受 `disabled` 或 `live`，生产启动会拒绝 `mock`、未知模式、弱密钥、无效 Fernet 密钥和示例管理员密码。
- 本次复核没有把旧文档中的 156/167/173/204 等历史测试数字当作当前结果。当前测试入口和失败边界见“验证记录”；本机执行受到 Windows `spawn EPERM` 和临时目录 ACL 限制，因此不能把本次命令写成全量通过。
- 任何“代码已存在”只表示静态实现已核对；只有可复现的命令、退出码和环境记录才能把条目标为“已验收”。

## 监督规则

| 角色 | 职责 |
| --- | --- |
| 用户 / 项目负责人 | 决定范围、真实第三方操作授权和最终发布 |
| Antigravity（实施方） | 按问题 ID 修改代码、补回归并提交可复核证据 |
| 审查方 | 对照源码、配置和测试独立复核；记录证据边界与遗留风险 |

- 状态只使用：待接收、待修复、实施方自报完成、待验收、验收未通过、已验收、外部阻塞。
- 每次交付必须写出问题 ID、变更文件、命令、退出码、结果、未覆盖范围和回滚方式。
- 禁止未经明确授权的真实充值、撤回/关闭订单、订阅变更、真实账号登录、镜像发布和工作区数据删除。
- 本台账只描述主动检查时的现场，不承诺后台轮询或持续监督。

## 已核对的当前实现

### 充值模式与任务状态

- `app/services/recharge_service.py` 定义 `disabled`、`mock`、`live` 三种模式。`disabled` 在路由层返回 503；`mock` 只写入本地 `RechargeTask`，任务标记 `is_mock=true`；`live` 先持久化 `pending` 任务，再在锁外调用 `RECHARGE_UPSTREAM_URL`，请求携带 `idempotency_key` 和 `client_task_no`。
- live 上游异常或返回未知业务结果会把任务置为 `unknown`，不会降级为模拟成功。任务查询会按任务号或卡密调用上游并回写状态；worker 的维护循环会查询最多 20 条 `pending`、`processing`、`unknown` 或存在未知变更的 live 任务。
- 任务状态为 `pending`、`processing`、`unknown`、`completed`、`failed`、`recalled`、`closed`。`closed` 卡密不能再次提交；`recall` 和 `close` 都要求绑定邮箱及 `confirmed=true`，并由 `RechargeMutation` 抑制重复的 live 写操作。
- `recharge_tasks`、`recharge_operations`、`recharge_mutations`、`one_time_tokens` 是数据库模型；原始 challenge 不落库，仅记录 token_hash、binding 等元数据，任务记录不保存原始 challenge。
- challenge 有效期为 300 秒，只能使用一次，并绑定当前会话上下文、模式、卡密、凭证、套餐和续费标志。允许的套餐是 `PLUS`、`PRO`、`Pro 5x`、`CLAUDE_CODE`、`FINISHED`、`KYC`；除 `FINISHED` 外均要求凭证，`PLUS`/`PRO` 的 JSON 凭证必须含 `accessToken`，`KYC` 凭证必须是 HTTP(S) 链接。
- 公共充值接口允许匿名客户访问，但写请求必须是 JSON 对象、带 `X-Requested-With: XMLHttpRequest`，并通过 Origin/Fetch 元数据检查；IP 和卡密各按数据库 `RequestLimit` 做 60 次/分钟限制（测试配置为 10000）。管理员 API 仍由 Flask 会话保护。
- 账单查询、取消续费、恢复续费在 `mock` 模式使用进程内模拟订阅状态；live 模式只转发上游。模拟账单下载是带事实任务或当前会话模拟账单的 TXT；live 下载明确返回 503，不能描述为已提供真实 PDF 或正式发票。

### OAuth、Gmail 与后台任务

- Gmail OAuth 状态由 `OAuthStateManager` 写入 `one_time_tokens`，有效期 30 分钟；若 session 中存在 state，query state 必须匹配，随后还必须是数据库中已注册、未过期且未消费的 state 才会在回调成功或错误时消费。无 session state 的后台/无头回调仍可凭数据库中的有效注册 state 继续；session 不匹配、未注册或过期的 state 会被拒绝且不消费，重复回调会被拒绝。
- `RuntimeJob`/`RuntimeState` 和 `RuntimeQueue` 为批量 OAuth、Googlemail 自动化、Gmail 通知、同步和守护状态提供共享数据库队列。`app/worker.py` 使用 `worker.lock` 保证单个后台 worker 实例，并在重启时恢复或失败标记中断任务。
- `/health/ready` 检查数据库、worker 心跳、Gmail 凭据可读性、维护失败次数、自动化任务锁和 Gmail 动作重试状态；队列模式下 worker 心跳失效会返回 503。
- Gmail token 使用 `GMAIL_TOKEN_ENCRYPTION_KEY` 的 Fernet 加密；Gmail 服务读取连接时会拒绝对应的锁定账号，账号 API 还禁止读取 2FA、历史和敏感导出字段。数据库本身仍保存账号密码、恢复邮箱和 TOTP secret，生产部署必须限制数据库文件权限。

## 督办项

| ID | 状态 | 当前事实 | 关闭条件 |
| --- | --- | --- | --- |
| SUP-01 | 实施方自报完成 | 充值模式枚举、challenge 绑定、状态机和一次性消费已写入服务与模型 | 运行 Python 充值回归，并在独立进程/重启后复核共享 SQLite 行为 |
| SUP-02 | 实施方自报完成 | OAuth state、充值任务、操作变更和运行队列均有数据库记录；OAuth state 有 30 分钟 TTL | 运行跨进程并发消费、重放和重启恢复测试；不得用单进程结果替代 |
| SUP-03 | 实施方自报完成 | live 创建/撤回/关闭在网络异常时保留 unknown 或 unknown mutation，并由查询核对 | 使用获授权的上游测试契约核对 idempotency、上游任务号和迟到响应 |
| SUP-04 | 实施方自报完成 | queue 模式由独立 `app.worker` 执行；web 与 worker 共享数据库，健康检查依赖 worker 心跳 | Linux systemd/Docker 实际启动、停止、重启和健康检查通过 |
| SUP-05 | 外部阻塞 | 生产只允许 disabled/live；上游地址可由 `RECHARGE_UPSTREAM_URL` 配置，但仓库没有真实上游鉴权契约 | 获得授权的 sandbox 上游、鉴权方式、幂等和退款/撤回语义，并完成隔离验收 |
| SUP-06 | 待验收 | 生产启动校验 `SECRET_KEY`、`ADMIN_PASSWORD`、Gmail Fernet key；账号敏感字段仍在 SQLite 中保存 | 完成密钥轮换、数据库权限、备份保管和敏感数据保留策略验证 |
| SUP-07 | 外部阻塞 | Dockerfile、Compose、systemd、Nginx 和安装脚本均已更新到 web/worker 双进程模型 | 在目标 Linux 主机完成镜像构建、迁移、HTTPS、回调、恢复和回滚演练 |
| SUP-08 | 待验收 | 测试文件覆盖充值、OAuth、运行队列、恢复、锁号和安全中心；当前环境未形成全量绿色结果 | 串行运行全部测试并保存退出码，补足真实浏览器和错误路径证据 |

## 对外接口核对

当前 Flask 路由由 `app/routes/main.py`、`app/routes/api.py`、`app/routes/recharge.py` 和 `app/routes/queued_tasks.py` 注册。主要入口如下，路径以源码为准：

- 页面与健康：`/`、`/recharge`、`/admin`、`/admin/<path>`、`/health/ready`、`/assets/<path>`。
- 管理认证：`POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/check`。
- 账号与统计：`/api/accounts`、`/api/accounts/batch`、`/api/accounts/export`、`/api/accounts/batch-delete`、`/api/accounts/batch-sold`、`/api/accounts/batch-remark`、`/api/accounts/<id>`、`/api/accounts/<id>/status`、`/api/accounts/<id>/sold`、`/api/accounts/<id>/2fa`、`/api/accounts/<id>/history`、`GET /api/stats`。
- Gmail：`/api/gmail/oauth/start`、`/api/gmail/oauth/callback`、`/api/gmail/connections`、消息/标签/规则/任务日志/watch、`/api/gmail/pubsub/webhook`、批量授权和 daemon 系列接口。
- Googlemail 自动化：`/api/googlemail/status`、`/api/googlemail/tasks`、`/api/googlemail/tasks/<task_id>`、`/api/googlemail/tasks/<task_id>/cancel`。
- 安全中心：`/api/security/overview`、`/api/security/accounts`、`/api/security/forwarding-audit`、`/api/security/central-otps`、`/api/security/accounts/<id>/lock|unlock`。
- 充值：`/api/recharge/config`、`agreement`、`features`、`stats/avg-processing-time`、`redeem-codes/validate`、`submission-challenges`、`tasks`、`tasks/<task_no>`、`tasks/lookup`、`tasks/lookup-batch`、`tasks/recall`、`tasks/close`、`tasks/invoice/download`、`billing/query`、`billing/cancel-subscription`、`billing/resume-subscription`、`billing/invoice-file`。

JSON 接口返回通常使用 `{success, data, message}`；收据下载成功时返回 UTF-8 文本附件；`/health/ready` 返回组件布尔状态并以 200/503 表示就绪与否；`GET /api/recharge/tasks/<task_no>` 会隐藏 `redeem_code`、`account_email` 和 `notify_email`。

## 验证记录

静态核对已执行：

```powershell
.\.venv\Scripts\python.exe -m pip check
docker compose config --quiet
.\.venv\Scripts\python.exe -c "from app import create_app; a=create_app('testing'); print(len(list(a.url_map.iter_rules())))"
```

`pip check` 通过；Compose 配置可解析但提示 `version` 字段已废弃；路由表由测试配置成功加载。下列命令是仓库声明的验证入口，必须在目标环境串行执行并记录退出码：

本轮串行复核已通过以下充值/安全回归：`test_recharge.py` 18/18、`test_recharge_safety.py` 19/19、`test_production_hardening.py` 11/11、`test_oauth_state_safety.py` 4/4、`test_live_recharge_contract.py` 1/1。结果只覆盖这些文件，不代表全量测试或真实上游验收。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
node --test tests/account-import.test.mjs tests/googlemail-local-copy.test.mjs tests/api-service.test.mjs
npm --prefix googlemail test
node --test tests/frontend-ui.test.mjs
npm --prefix frontend run build
.\.venv\Scripts\python.exe -m pip check
```

本机执行受到 Windows 临时目录 ACL 和 Node 子进程 `spawn EPERM` 影响，未将失败命令误记为通过；也没有运行真实第三方请求。

## 交付要求

1. 交付说明按“问题 ID → 文件 → 命令与退出码 → 结果 → 未完成项”组织，并提供可定位的提交或工作区快照。
2. 先完成 disabled/mock/live 的模式边界、未知结果核对、跨进程幂等和回调重放测试，再申请真实上游 sandbox。
3. 生产发布前必须完成 `python -m app.manage init-db`、数据库备份恢复、web/worker 双服务、`/health/ready`、HTTPS OAuth 回调、Nginx 代理信任和权限核查。
4. 未取得用户明确批准前，维持 No-Go，不发布镜像，不操作真实资金、订阅或账号。
