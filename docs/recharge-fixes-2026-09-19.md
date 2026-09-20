# 充值交付缺陷修复说明（2026-09-19）

本文记录当前代码中充值交付、账单工作台和相关前端流程的安全修复与行为边界。文中只保留可由仓库源码和测试复核的内容；不代表真实上游充值契约、生产数据库或部署环境已经验收。

## 1. 模式与入口

充值蓝图挂载在 `/api/recharge`，C 端充值接口不要求管理员登录，但所有写请求必须是 JSON 对象、通过 Origin/Fetch 来源检查并带 `X-Requested-With: XMLHttpRequest`。充值接口还按 IP 和卡密摘要执行共享限流。

服务端支持三种模式：

- `disabled`：默认值；只有 `/config` 和 `/features` 正常返回，协议、平均耗时和其他充值操作均由 `ensure_enabled()` 返回 503。
- `mock`：测试/沙箱模式，任务和账单均为本地模拟数据；`TestingConfig` 使用此模式，development 也可通过 `RECHARGE_MODE=mock` 运行，只有 production 工厂禁止它。
- `live`：通过显式配置的 `RECHARGE_UPSTREAM_URL` 调用上游接口；非测试环境不会回退到默认真实上游地址。生产工厂拒绝 `mock` 和未知模式，生产默认仍是 `disabled`，必须显式配置为 `live` 才会发起上游请求。

主要接口如下：

| 用途 | 方法与路径 | 当前行为 |
| --- | --- | --- |
| 查询模式与特性 | `GET /api/recharge/config`、`GET /api/recharge/features` | `/config` 的 `data` 为 `{features, dual_mode:true, mode, version:'1.0.0'}`；开关字段位于 `features`，`/features` 直接返回这些开关 |
| 服务协议 | `GET /api/recharge/agreement` | enabled 模式返回 HTML 协议、`version:'2026.09'`；disabled 返回 503 |
| 平均耗时 | `GET /api/recharge/stats/avg-processing-time?product=gpt&category=card` | enabled 模式返回固定的 6 项套餐秒数；`product`/`category` 当前不参与计算，不应视为实时近 7 天统计 |
| 验证卡密 | `POST /api/recharge/redeem-codes/validate` | live 调上游；mock 按卡密文本生成沙箱套餐信息 |
| 签发挑战 | `POST /api/recharge/submission-challenges` | 绑定会话、模式、卡密、凭证、套餐和续费标志，返回 300 秒有效的 `challenge_token` |
| 创建任务 | `POST /api/recharge/tasks` | 先核验并消费挑战，再按模式创建 mock 任务或持久化 live `pending` 任务；即使随后因同卡已有任务而失败，已消费的 challenge 也不能重用 |
| 查询任务 | `GET /api/recharge/tasks/<task_no>`、`POST /api/recharge/tasks/lookup` | 按模式隔离任务；匿名任务查询统一移除 `redeem_code`、`account_email`、`notify_email`，live 查询会核对上游并回写本地状态 |
| 批量查询 | `POST /api/recharge/tasks/lookup-batch` | 最多 50 个卡密；live 通过上游批量接口，mock 读取本地记录 |
| 撤回/关闭 | `POST /api/recharge/tasks/recall`、`POST /api/recharge/tasks/close` | 必须提供 `task_no`、完整卡密、绑定邮箱和 `confirmed=true`；还须由原创建会话的 HMAC 所有权摘要或管理员会话授权。mock 仅更新本地状态，live 通过 mutation 调上游，异常时进入 `unknown`，不能保证外部卡密已销毁 |
| 账单工作台 | `POST /api/recharge/billing/query`、`.../cancel-subscription`、`.../resume-subscription` | live 使用数据库持久化的操作号、30 秒租约和 `pending/unknown/done` 状态调用上游；mock 为会话绑定的进程内模拟订阅 |
| 账单文件请求描述 | `POST /api/recharge/billing/invoice-file` | 仅 mock 模式可用；校验 `slug`/`file_type` 后返回本地收据下载的 POST 方法与 JSON payload，不把标识拼入 URL；disabled/live 返回 503 |
| 收据下载 | `POST /api/recharge/tasks/invoice/download` | 仅 mock 可用；标识只从 JSON body 读取，live 明确返回 503，不能把 mock 收据当作真实凭证 |

所有 JSON API 成功响应统一为 `{success:true,data:<结果>,message}`，错误响应为 `{success:false,data:null,message,error_code}`；`error_code` 按 HTTP 语义稳定映射为 `invalid_request`、`conflict`、`rate_limited`、`upstream_error`、`service_unavailable` 等，旧的 `message` 字段仍保留。收据下载成功时返回 UTF-8 `text/plain` 附件。主要请求字段和限制如下：

- `submission-challenges` 接收 `redeem_code`、`plan_type`、`token_input`（`FINISHED` 可为空）和布尔 `is_renewal`；套餐必须属于允许列表，其余套餐的凭证必填且不超过 65535 字符；返回 `{challenge_token, expires_in:300}`。
- `tasks` 必须接收 `redeem_code`、`plan_type`、`account_email`、`agreement_accepted:true`、`email_verified:true`、`challenge_token` 和布尔 `is_renewal`；`token_input` 在 `FINISHED` 外必填且不超过 65535 字符，`PLUS`/`PRO` 的 JSON 凭证须含 `accessToken`，`KYC` 须为 `http://` 或 `https://` URL。非 `FINISHED` 套餐还必须提交 `acknowledge_non_free:true`；`notify_channel` 当前仅支持 `site`（`email` 会被拒绝，直到通知投递能力上线），`notify_email` 默认使用目标邮箱。
- `tasks/lookup` 接收 `redeem_code`，也接受以 `TK-` 开头的任务号；后端不会把 `TASK-` 前缀识别为任务号。`lookup-batch` 接收 `redeem_codes` 数组，去重保序且最多 50 个。
- `recall`/`close` 接收 `task_no`（1-64 字符）、`redeem_code`（1-120 字符）、`email`（1-256 字符）和严格为 `true` 的 `confirmed`。任务号、卡密、邮箱、当前模式和创建会话所有权必须同时匹配；不匹配或无权统一返回 403。`close` 可处理尚未关闭的本地状态；返回任务对象，mock 不访问上游。
- `billing/query` 接收不超过 65535 字符的文本 `token_input`；取消/恢复接收同样的 `token_input` 与 `confirmed:true`。`billing/invoice-file` 接收可选 `slug`（默认 `default`）和 `file_type`（默认 `txt`，当前仅允许 `txt`），仅 mock 模式返回本地收据下载的 POST 描述。

## 2. 已修复问题与当前保证

| 问题 | 当前实现与边界 | 主要代码/测试 |
| --- | --- | --- |
| live 模式下载伪造的 mock 账单 | 收据路由先检查模式；模式不是 `mock` 时返回 503。任意未由当前 mock 任务或当前会话账单签发的固定标识都返回 404，只有真实本地任务或当前会话账单才可下载 | [`app/routes/recharge.py`](../app/routes/recharge.py)、[`tests/test_recharge_safety.py`](../tests/test_recharge_safety.py) |
| challenge 可省略套餐、换凭据或跨会话复用 | 套餐必须属于 `PLUS`、`PRO`、`Pro 5x`、`CLAUDE_CODE`、`FINISHED`、`KYC`；除 `FINISHED` 外必须有凭证。HMAC 绑定模式、会话上下文、卡密、凭证、套餐和 `is_renewal`，有效期 300 秒，并由数据库条件更新一次消费 | [`app/services/recharge_service.py`](../app/services/recharge_service.py)、[`app/models/one_time_token.py`](../app/models/one_time_token.py)、[`tests/test_recharge_safety.py`](../tests/test_recharge_safety.py)、[`tests/test_shared_recharge_state.py`](../tests/test_shared_recharge_state.py) |
| 原始 challenge 泄露或被转发到上游 | `OneTimeToken` 保存 token 的 SHA-256 摘要、绑定摘要、payload、过期和消费时间，不保存原始 token；`RechargeTask.challenge_token` 创建时为 `None`；调用上游前从 payload 移除 `challenge_token` | [`app/models/one_time_token.py`](../app/models/one_time_token.py)、[`tests/test_recharge_safety.py`](../tests/test_recharge_safety.py)、[`tests/test_live_recharge_contract.py`](../tests/test_live_recharge_contract.py) |
| OAuth state 在成功后可重放 | 普通回调和无会话回调都从数据库 `one_time_tokens` 消费共享 state；状态必须已注册且未过期。会话状态不匹配会拒绝且不消费另一条 state；取消或缺少授权码会消费匹配 state | [`app/routes/api.py`](../app/routes/api.py)、[`app/services/gmail_service.py`](../app/services/gmail_service.py)、[`tests/test_oauth_state_safety.py`](../tests/test_oauth_state_safety.py) |
| unknown 查询后本地状态不回写 | live 创建先持久化 `pending`，上游异常标记 `unknown`；查询会核对上游返回的状态、任务号，以及上游实际提供的非空 `redeem_code`、`account_email`、`client_task_no` 字段，使用 `recharge_operations.upstream_task_no` 关联后回写。上游失败不会伪造本地成功，终态不会被迟到的处理中状态覆盖 | [`app/services/recharge_service.py`](../app/services/recharge_service.py)、[`app/models/recharge_operation.py`](../app/models/recharge_operation.py)、[`tests/test_recharge_safety.py`](../tests/test_recharge_safety.py) |
| 同一卡密跨进程重复创建 | `recharge_operations.active_key` 对 `mode:redeem_code` 的 HMAC 建唯一约束，与任务在同一持久化流程中写入；`failed`/`recalled` 状态释放门禁，完成或关闭继续保留。数据库竞争只允许一条活动记录 | [`app/models/recharge_operation.py`](../app/models/recharge_operation.py)、[`tests/test_shared_recharge_state.py`](../tests/test_shared_recharge_state.py) |
| 撤回/关闭并发重复调用 | `recharge_mutations` 为每个任务保留操作号和 `pending`/`unknown`/`done` 状态；上游未确认时不允许重复操作，worker 维护阶段会恢复过期 mutation | [`app/models/recharge_mutation.py`](../app/models/recharge_mutation.py)、[`app/worker.py`](../app/worker.py) |
| 仅凭泄露的卡密和邮箱可撤回/关闭 | 新任务在创建事务中写入 `recharge_task_access`，只保存由 `SECRET_KEY`、`task_no` 和随机 `session.recharge_context` 计算的 HMAC，不保存会话 bearer；危险操作同时核验任务号、卡密、邮箱、模式和创建会话。历史任务没有 grant 时匿名失败关闭，管理员核对后仍可处理 | [`app/models/recharge_task_access.py`](../app/models/recharge_task_access.py)、[`app/routes/recharge.py`](../app/routes/recharge.py)、[`tests/test_recharge_task_access.py`](../tests/test_recharge_task_access.py) |
| 管理员登录/退出误删充值所有权 | `clear_admin_session()` 清除管理员和 OAuth 状态时仅保留合法的 `recharge_context`；退出后管理 API 重新为 401，但同一浏览器仍可操作自己创建的任务 | [`app/routes/api.py`](../app/routes/api.py)、[`tests/test_recharge_task_access.py`](../tests/test_recharge_task_access.py) |
| 账单取消/恢复使用永久或进程内幂等键 | `recharge_billing_mutations` 按凭证 HMAC 保存当前业务意图、UUID 操作号、每次 claim 独立的 `lease_token`、30 秒租约和 `pending/unknown/done`；同一已确认意图重放复用操作号，`cancel→resume→cancel` 每次产生新操作号，反向操作在未知状态下被阻断 | [`app/models/recharge_billing_mutation.py`](../app/models/recharge_billing_mutation.py)、[`app/services/recharge_service.py`](../app/services/recharge_service.py)、[`tests/test_billing_idempotency.py`](../tests/test_billing_idempotency.py) |
| 账单查询、过期租约和失败回写竞态 | 查询快照包含 `operation_id/action/state/lease_until/lease_token/updated_at`，过期 pending 由一条带原快照条件的 CAS 更新；旧请求失败回写还必须匹配本次 `lease_token`，不能清除另一进程重新 claim 的租约。只有上游明确达到该动作目标才转为 `done` | [`app/models/recharge_billing_mutation.py`](../app/models/recharge_billing_mutation.py)、[`tests/test_billing_idempotency.py`](../tests/test_billing_idempotency.py) |
| `SECRET_KEY` 轮换绕过账单未知操作门禁 | live 账单凭证摘要改由稳定的 `GMAIL_TOKEN_ENCRYPTION_KEY` 派生专用 HMAC 键；轮换 Flask session 密钥不会产生新的账单身份。数据库仅保存摘要，不保存原始账单凭证 | [`app/services/recharge_service.py`](../app/services/recharge_service.py)、[`tests/test_billing_idempotency.py`](../tests/test_billing_idempotency.py) |
| live 上游配置运行中漂移 | 非测试环境每次发起上游请求时再次校验 HTTPS、无 userinfo/query/fragment 且主机命中 `RECHARGE_UPSTREAM_ALLOWED_HOSTS`；测试环境保留隔离 loopback 上游 | [`app/services/recharge_service.py`](../app/services/recharge_service.py)、[`tests/test_production_hardening.py`](../tests/test_production_hardening.py) |
| live 批量响应漏项、重复或乱序 | 上游结果必须与请求侧去重集合一一对应；拒绝漏项、重复、未知卡密、非对象和缺失套餐的匿名结果，并按请求顺序返回 | [`app/services/recharge_service.py`](../app/services/recharge_service.py)、[`tests/test_recharge_safety.py`](../tests/test_recharge_safety.py) |
| 前端查询响应互相覆盖 | 单任务查询按请求序号和 `AbortController` 丢弃旧响应；轮询只覆盖 `pending`、`processing`、`unknown`，失败最多重试 3 次并采用 5-30 秒退避，终态停止；切换查询页签会中止旧请求 | [`frontend/src/components/RechargeView.jsx`](../frontend/src/components/RechargeView.jsx)、[`tests/frontend-ui.test.mjs`](../tests/frontend-ui.test.mjs) |
| 编辑输入后按钮永久 loading 或展示旧结果 | 编辑 CDK、单查或批量输入会推进请求代际、取消在途请求并解除对应 loading；批量输入还会清空旧结果，迟到响应不能覆盖当前输入 | [`frontend/src/components/RechargeView.jsx`](../frontend/src/components/RechargeView.jsx)、[`tests/frontend-ui.test.mjs`](../tests/frontend-ui.test.mjs) |
| 前端账单凭据生命周期不清晰 | 账单凭据变化会清空旧结果并中止旧请求；离开账单页会清空凭据和结果；取消/恢复订阅要求二次确认，随后重新查询当前状态 | [`frontend/src/components/RechargeView.jsx`](../frontend/src/components/RechargeView.jsx) |
| 前端收据下载展示不准确 | mock 任务收据下载通过 POST 传完整卡密；仅输入 `TK-` 任务号时不显示下载按钮，`TASK-` 不被当作任务号。live 结果显示“真实凭证下载暂未开放”；账单列表只有 `is_mock` 且有 `slug` 时展示模拟 TXT 链接 | [`frontend/src/components/RechargeView.jsx`](../frontend/src/components/RechargeView.jsx)、[`frontend/src/services/api.js`](../frontend/src/services/api.js) |
| fetch 已返回但 JSON/blob 响应体永久挂起 | 前端超时和外部 `AbortSignal` 覆盖 fetch 与后续 JSON/blob 消费；HTTP 200 且 `success:false`、非对象 JSON、空响应和非 JSON 错误均稳定抛出带 HTTP status 的异常，不再二次触发类型错误；只有后端确实返回结构化错误 payload 时才保留 `errorCode/response` | [`frontend/src/services/api.js`](../frontend/src/services/api.js)、[`tests/api-service.test.mjs`](../tests/api-service.test.mjs) |
| 协议保留原始 HTML 渲染带来潜在注入面 | 当前协议由服务端内置固定 HTML 返回，未发现外部内容写入入口；前端仍移除原始 HTML 注入路径，改为经 `DOMParser` 转成 React 节点，仅允许固定标签、class 和 `http/https/mailto` 链接。合成恶意输入中的脚本/SVG/表单/图片/事件属性和危险 URL 不会进入 DOM | [`frontend/src/components/RechargeView.jsx`](../frontend/src/components/RechargeView.jsx)、[`tests/frontend-ui.test.mjs`](../tests/frontend-ui.test.mjs) |
| CDK/凭证默认明文及危险操作目标漂移 | CDK、查询、批量和账单凭证默认掩码并提供显式显隐；撤回/关闭弹窗绑定查询结果中的 `task_no`，前端快速双击只发送一次请求，后端仍独立校验所有权和目标 | [`frontend/src/components/RechargeView.jsx`](../frontend/src/components/RechargeView.jsx)、[`frontend/src/services/api.js`](../frontend/src/services/api.js)、[`tests/frontend-ui.test.mjs`](../tests/frontend-ui.test.mjs) |
| 生产页面缺少脚本和连接来源约束 | production 响应增加 CSP：脚本和连接仅同源，禁用 object/base，限制 frame ancestor 和表单目标；开发环境不加该头以免影响 Vite HMR | [`app/__init__.py`](../app/__init__.py)、[`tests/test_production_hardening.py`](../tests/test_production_hardening.py)、[`tests/frontend-ui.test.mjs`](../tests/frontend-ui.test.mjs) |

## 2.1 当前上线结论

本模块目前是 CDK 履约与订阅管理工作台，不是现金充值/余额系统。仓库没有金额与币种字段、优惠赠送规则、支付渠道、支付订单、支付回调验签与事件去重、余额账本或原子余额更新；因此“输入充值金额 → 创建支付订单 → 支付回调 → 余额更新”的链路尚未实现，生产发布结论为 **No-Go**。

已落地的本地防护包括：live 上游响应字段白名单与身份核对、首次任务号精确回显、禁止上游重定向、JSON-only 卡密校验和收据 POST、卡密/凭证 HMAC 维度限流、一次性 challenge、任务创建会话所有权、任务 mutation 竞争保护和账单操作持久幂等。它们不能替代真实上游的 API key/HMAC/mTLS、支付 webhook 验签、回调去重和长期客户账户恢复方案。

仍需在获授权 sandbox 和生产预演中验收：上游服务间鉴权、撤回/关闭最终状态契约、账单 mutation 跨版本/跨密钥升级、丢失创建会话后的客户恢复流程、敏感数据加密迁移、KYC URL 是否会由上游抓取及其 SSRF 边界、备份恢复及 TLS/跨进程部署。

## 3. 数据模型与状态边界

- `one_time_tokens` 保存 namespace、token 摘要、绑定摘要、JSON payload、过期时间和消费时间。充值 challenge 与 Gmail OAuth state 使用不同 namespace；表中不保存原始 token 或充值凭证。
- `recharge_tasks` 保存任务编号、卡密、套餐、目标邮箱、状态、是否续费、是否 mock、通知信息和时间戳。`token_input` 不属于模型字段；新任务的 `challenge_token` 不落库。历史任务中的卡密等敏感字段没有在本轮自动清除或迁移。
- `recharge_operations` 保存任务到上游任务号的关联和唯一活动门禁 `active_key`。没有上游任务号时，live 任务查询先按卡密核对，成功后补写关联。
- `recharge_mutations` 保存撤回/关闭操作的幂等操作号和对账状态，避免重复调用上游。
- `recharge_task_access` 以 `task_no` 为主键，保存创建会话的 HMAC 所有权摘要。新匿名客户只能操作自己在当前浏览器会话中创建的任务；清 Cookie、换浏览器、轮换 `SECRET_KEY` 或历史任务缺少该行时，匿名操作失败关闭，管理员可在核对目标后处理。
- `recharge_billing_mutations` 以账单凭证 HMAC 为主键，只保存动作、业务操作号、单次执行 `lease_token`、租约、状态、已确认续费状态和结果摘要，不保存原始凭证。`pending` 超过 30 秒后可进入核对/恢复，同动作保留原业务操作号但获得新 lease；相反动作在核对前被阻断。
- mock 任务初始状态为 `processing`，系统不会自动完成；用户仍可通过撤回或关闭改变状态。mock 账单保存在 `RechargeService._mock_subscriptions` 进程内字典中，key 是会话上下文与账单凭证的 HMAC；发票 slug 为随机 `inv_mock_<uuid>`。`find_mock_invoice` 只接受签发该账单的当前会话，因此重启、跨进程或换会话不保证可见。

任务状态由服务端接受 `pending`、`processing`、`unknown`、`completed`（上游 `success` 会映射为此状态）、`failed`、`recalled` 和 `closed`。`completed`、`failed`、`recalled`、`closed` 是终态；状态核对不会把终态回退为处理中。

live 模式的后台 worker 维护阶段会按更新时间取最多 20 个 `pending`、`unknown`、`processing` 任务，以及存在未知 mutation 的任务，调用 `get_task_by_no` 尝试核对。前端轮询和用户手动查询仍是可见结果的即时入口；worker 不代表上游一定具备幂等或最终一致保证。

## 4. 前端使用流程

1. 从充值页验证 CDK，读取服务端返回的 `plan_type`。
2. 填写凭证、目标邮箱、是否续费，并勾选邮箱确认和服务协议。
3. 前端先调用 `/submission-challenges`，再将返回的 `challenge_token` 连同原始表单提交 `/tasks`。创建成功后清空 Session/Cookie 输入和确认勾选，只保留任务摘要。
4. 进入“进度查询”后可按完整卡密或任务编号查询；单个任务处于 `pending`/`processing`/`unknown` 时自动轮询，批量查询最多 50 个卡密。
5. 撤回/关闭弹窗绑定当前查询结果的任务号，并要求重新输入完整卡密和任务邮箱；请求还必须来自创建任务的原浏览器会话或管理员。关闭会将本地任务置为 `closed`，是否销毁外部卡密取决于上游契约，代码不提供独立保证且不可再次提交本地卡密。
6. 进入“账单续费”后输入 Session Token 或登录凭证，可查询订阅和发票；取消/恢复自动续费均需确认。mock 收据只用于测试，不等价于支付或履约凭证。

## 5. 验证入口

仓库没有统一 lint、类型检查或格式化脚本。可在根目录 PowerShell 中运行与本说明相关的检查：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
node --test tests/account-import.test.mjs tests/googlemail-local-copy.test.mjs tests/api-service.test.mjs
npm --prefix googlemail test
npm --prefix frontend run build
node --test tests/frontend-ui.test.mjs
.\.venv\Scripts\python.exe -m pip check
```

稳定代码快照的结果为：Python 213、根目录 Node.js 41、Googlemail 47、正式构建 Playwright UI 43，共 344 项通过；前端 production build、`pip check`、Compose 配置解析、Node 语法检查和 diff whitespace 检查通过。Python 仍报告既有 `datetime.utcnow()` DeprecationWarning 与 SQLite ResourceWarning。Playwright 的充值 API 使用隔离 fixture，不能替代真实上游验收。

充值修复的重点测试包括：

- [`tests/test_recharge.py`](../tests/test_recharge.py)：模式隔离、契约校验、任务状态机、账单操作、Origin 防护和上游异常。
- [`tests/test_recharge_safety.py`](../tests/test_recharge_safety.py)：challenge 绑定/过期/单次消费、mock 收据会话隔离、unknown 对账、身份校验和终态保护。
- [`tests/test_shared_recharge_state.py`](../tests/test_shared_recharge_state.py)：跨进程 OAuth state、challenge、卡密门禁、限流、worker job 和 mutation 竞争。
- [`tests/test_live_recharge_contract.py`](../tests/test_live_recharge_contract.py)：隔离 loopback 上游的真实 HTTP 适配，确认 challenge 不转发且上游任务号可查询。
- [`tests/test_recharge_task_access.py`](../tests/test_recharge_task_access.py)：跨会话拒绝、任务目标绑定、历史任务管理员兜底及登录/退出保留客户所有权。
- [`tests/test_billing_idempotency.py`](../tests/test_billing_idempotency.py)：取消/恢复业务意图、超时重试、跨进程占用、密钥稳定性和迟到查询 CAS。
- [`tests/api-service.test.mjs`](../tests/api-service.test.mjs)：JSON/blob 响应体超时、外部取消、结构化错误及撤回/关闭 `task_no` 请求契约。
- [`tests/frontend-ui.test.mjs`](../tests/frontend-ui.test.mjs)：协议白名单、凭据掩码、生产 CSP、任务弹窗绑定、双击防重、轮询退避/终止和旧响应丢弃。

这些测试使用内存或临时 SQLite、虚构卡密/凭证和隔离上游；它们不调用真实充值、Google 授权、支付或生产部署，也不构成上游服务的正式契约验收。

## 6. 部署与回滚注意

1. 新增/依赖的表包括 `one_time_tokens`、`recharge_operations`、`recharge_mutations`、`recharge_task_access` 和 `recharge_billing_mutations`；通过 [`app/manage.py`](../app/manage.py) 的 `python -m app.manage init-db` 创建。发布前应在预发布副本验证建表权限、备份和恢复。
2. 多 Web/worker 实例必须连接同一数据库，并使用稳定且一致的 `SECRET_KEY` 与 `GMAIL_TOKEN_ENCRYPTION_KEY`。前者保护 session、challenge 和任务所有权摘要；后者除加密 Gmail/队列数据外，还派生账单凭证 HMAC。轮换 `SECRET_KEY` 会使旧匿名会话所有权失效；轮换 Fernet 密钥前必须先使用旧版本/旧密钥核对并结清所有 `pending`/`unknown` 账单操作。
3. 旧进程内存中的 challenge、OAuth state 和 mock 账单不会迁移。部署前应停止接收新请求并等待在途写操作完成；旧挑战、旧账单链接需要重新生成。
4. live 任务如果没有 `upstream_task_no`，维护 worker 或用户查询会按卡密调用上游 lookup，再校验身份后补写映射。历史任务的原始卡密和旧 challenge 数据没有在本轮自动加密迁移。
5. 回滚必须同时处理 Flask 后端、前端静态产物和数据库 schema。旧版本不应重新开放本轮已关闭的 live 收据、challenge 绑定或状态核对路径；必要时先保持 `RECHARGE_MODE=disabled`。
6. 如果环境曾运行过使用临时摘要根密钥的旧实现，不得删除未决记录后直接切换。应保留原代码和原密钥，先核对上游并结清未知操作，再升级到当前稳定 Fernet 派生方案；仓库没有自动迁移旧摘要主键的工具。
7. 正式基线不存在旧 `recharge_billing_mutations` 表，首次 `init-db` 会按当前模型创建包含 `lease_token` 的完整结构。如果某个中间开发版本已经创建了不含该列的表，当前 `init-db` 不会自动补列；必须停服务、备份、核对旧 `pending/unknown` 后执行受控 schema 迁移，不能重建/删表丢失未知操作。
8. 不应把 `RECHARGE_MODE=mock` 用于生产，也不应把模拟收据、模拟订阅或隔离上游测试结果描述为真实交付能力。真实上游地址、鉴权契约、幂等执行、撤回/关闭顺序、权限、TLS、备份恢复仍需单独验收。

## 7. 实现索引

充值路由、服务和模型分别位于 [`app/routes/recharge.py`](../app/routes/recharge.py)、[`app/services/recharge_service.py`](../app/services/recharge_service.py)、[`app/models/recharge_task.py`](../app/models/recharge_task.py)、[`app/models/one_time_token.py`](../app/models/one_time_token.py)、[`app/models/recharge_operation.py`](../app/models/recharge_operation.py)、[`app/models/recharge_mutation.py`](../app/models/recharge_mutation.py)、[`app/models/recharge_task_access.py`](../app/models/recharge_task_access.py) 和 [`app/models/recharge_billing_mutation.py`](../app/models/recharge_billing_mutation.py)。前端实现位于 [`frontend/src/components/RechargeView.jsx`](../frontend/src/components/RechargeView.jsx) 与 [`frontend/src/services/api.js`](../frontend/src/services/api.js)，worker 维护逻辑位于 [`app/worker.py`](../app/worker.py)。逐项缺陷证据见 [`docs/project-fixes-2026-09-20.md`](project-fixes-2026-09-20.md)。
