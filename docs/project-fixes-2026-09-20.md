# 项目缺陷审核与修复清单（2026-09-20）

本文是本轮上线前静态审核的证据清单，范围覆盖充值门户、后端接口、状态模型、前端请求层、生产安全头和部署升级。结论基于当前工作区源码与隔离测试，不代表真实充值上游、Google 账号、目标 Linux 主机或生产数据已经验收。

## 1. 负责人结论

当前代码可继续进入预发布验收，但**不能据此放行真实现金充值或余额业务**。仓库实现的是 CDK 履约、任务查询、撤回/关闭和订阅续费工作台；没有金额/币种模型、优惠赠送、支付渠道、支付订单、支付回调验签与去重、余额账户或账本原子更新。真实上游服务鉴权也没有形成仓库内契约，`RECHARGE_MODE=live` 在完成获授权 sandbox 验收前应保持关闭。

本轮已经修复的问题包括：匿名危险操作越权、账单操作跨进程幂等、迟到查询覆盖、响应体无限等待、协议 HTML 的潜在危险渲染面、敏感输入默认明文和 production CSP 缺失。下列条目给出复现条件、代码依据与测试证据。

## 2. 已修复问题

### 2.1 P1：知道卡密和邮箱即可撤回或关闭他人任务

- 旧影响与复现：会话 A 创建任务；会话 B 只要获得完整卡密和邮箱，即可向 `/api/recharge/tasks/recall` 或 `/close` 提交写操作。泄露查询条件会升级为破坏性权限。
- 修复：任务创建事务在 `RechargeService._persist_new_task()` 中写入 `recharge_task_access`；记录只保存由 `SECRET_KEY + task_no + session.recharge_context` 生成的 HMAC。`authorize_task_action()` 同时核验当前模式、`task_no`、卡密、邮箱和创建会话，管理员会话作为人工核对兜底；无权和目标不匹配统一返回 403。
- 代码依据：`app/models/recharge_task_access.py:8-36`、`app/services/recharge_service.py:514-524`、`app/routes/recharge.py:267-323`。
- 影响范围：所有匿名撤回/关闭；创建和脱敏查询仍可匿名使用。历史任务没有 grant、用户清 Cookie/换浏览器或轮换 `SECRET_KEY` 时匿名操作失败关闭。
- 测试：`tests/test_recharge_task_access.py:50-103` 覆盖跨会话拒绝、缺失/错误任务号、交叉拼接任务目标、历史任务管理员兜底及拒绝后不调用 mutation service；`tests/api-service.test.mjs:221-249` 验证前端发送 `task_no`；`tests/frontend-ui.test.mjs:428-485` 验证弹窗目标绑定和快速双击只发一次请求。

### 2.2 P1：管理员登录/退出会删除客户任务所有权

- 旧影响与复现：客户在同一浏览器创建任务后登录再退出管理端，原 `session.clear()` 同时删除 `recharge_context`，导致客户永久失去匿名撤回/关闭权限。
- 修复：`clear_admin_session()` 在清理 session 时仅保留合法非空的 `recharge_context`，其余管理员/OAuth 状态仍被删除。
- 代码依据：`app/routes/api.py:907-967`。
- 影响范围：同域名下充值门户与管理端共用 Flask session 的浏览器。
- 测试：`tests/test_recharge_task_access.py:105-121` 先创建任务，再登录/退出，确认管理 API 返回 401、OAuth 状态被清除且原任务仍可撤回。

### 2.3 P1：账单取消/恢复缺少持久幂等和崩溃恢复

- 旧影响与复现：连续执行 `cancel → resume → cancel` 若复用永久键，第三次可能被上游当作第一次重放；进程重启或跨 worker 并发也可能重复调用上游。超时结果未知时，反向操作可能与在途请求冲突。
- 修复：`recharge_billing_mutations` 以凭证 HMAC 为主键，保存动作、UUID `operation_id`、每次 claim 独立的 `lease_token`、30 秒租约、`pending/unknown/done`、目标续费状态和结果摘要。同一已确认业务意图重放复用操作号；业务意图变化生成新号；同一意图恢复保留操作号但获得新 lease；相反动作在 unknown 核对完成前被阻断。
- 代码依据：`app/models/recharge_billing_mutation.py:9-284`、`app/services/recharge_service.py:682-758`、`app/services/recharge_service.py:1176-1299`。
- 影响范围：live 模式的账单取消、恢复和查询；mock 账单仍是会话绑定的进程内模拟状态。
- 测试：`tests/test_billing_idempotency.py:92-427` 覆盖三段业务意图、确认重放、超时、跨进程竞争、过期租约、旧失败回写、模糊上游响应和外部状态变化。

### 2.4 P1：迟到的账单查询可能覆盖重新占用的操作

- 旧影响与复现：查询取得 unknown 快照后，另一进程用同一操作号重新占用为 pending；旧查询若只按 `operation_id` 写回，可能提前把新请求标记 done。
- 修复：查询快照包含 `operation_id/action/state/lease_until/lease_token/updated_at`。过期 pending 不先提交中间 unknown，而是以原快照条件执行单条 CAS；旧请求失败回写必须匹配本次 `lease_token`，无法清除另一进程的新 lease。只有观测到动作目标状态才转 done，反向状态保持 unknown。
- 代码依据：`app/models/recharge_billing_mutation.py:141-284`、`app/services/recharge_service.py:723-757`。
- 影响范围：live 账单查询与取消/恢复并发、worker 重启边界。
- 测试：`tests/test_billing_idempotency.py:196-374` 覆盖反向查询不解锁、目标查询解锁、旧 ORM 强引用、旧失败不能清租约，以及过期查询不能覆盖更新的相反动作。

### 2.5 P1：轮换 Flask `SECRET_KEY` 可改变账单凭证身份

- 旧影响与复现：若账单凭证摘要直接依赖 Flask session 密钥，轮换后相同凭证会落到新记录，绕过旧 unknown 操作门禁。
- 修复：live 账单摘要由长期 `GMAIL_TOKEN_ENCRYPTION_KEY` 派生专用 HMAC 键；数据库不保存原始账单凭证。所有实例必须使用相同 Fernet key。
- 代码依据：`app/services/recharge_service.py:681-708`。
- 影响范围：账单操作跨发布、跨实例身份稳定性；Fernet key 本身仍不能无迁移直接轮换。
- 测试：`tests/test_billing_idempotency.py:139-194` 验证不落原 token、超时重试复用 key、轮换 session secret 不改变账单摘要或 unknown 门禁。

### 2.6 P1：fetch 完成后读取 JSON/blob 可无限挂起

- 旧影响与复现：服务端先返回响应头，但响应体不结束；原超时只包围 fetch 时，按钮长期 loading，页面取消也不能结束 `response.json()`/`blob()`。
- 修复：`fetchWithTimeout()` 将 fetch 与 `readResponse` 放入同一超时/取消竞态；登录、普通 JSON 和发票 blob 共用该边界。HTTP 200 `success:false`、空/数组/non-JSON 错误都稳定抛出带 HTTP status 的异常；仅在后端提供结构化错误 payload 时保留对应的 `errorCode/response`。
- 代码依据：`frontend/src/services/api.js:144-245`、`frontend/src/services/api.js:603-648`。
- 影响范围：管理登录、充值配置/提交/查询、收据下载及使用该请求封装的管理 API。
- 测试：`tests/api-service.test.mjs:251-388` 覆盖 fetch 超时、JSON/blob body 超时、外部 AbortSignal、结构化错误和共享取消信号。

### 2.7 P2：移除服务协议的潜在危险 HTML 渲染面

- 旧影响与复现：当前 `get_agreement()` 只返回服务端内置固定 HTML，没有外部写入入口，因此未确认存在可远程利用的 XSS；但原始 HTML 渲染为未来配置化/外部化协议留下事件属性、SVG、图片 onerror 或 `javascript:` URL 等注入面。`<script>` 经 innerHTML 插入本身通常不会执行，不能把合成测试等同于现实攻击入口。
- 修复：协议经 `DOMParser` 解析后转换为 React 节点；只允许固定标签、class、有限表格属性和 `http/https/mailto` 链接。合成恶意内容中的脚本、SVG、表单、媒体、事件属性和危险 URL 被丢弃或降级为纯文本。
- 代码依据：`app/services/recharge_service.py:138-171`（当前固定内容）、`app/routes/recharge.py:137-140`、`frontend/src/components/RechargeView.jsx:30-101`。
- 影响范围：充值服务协议弹窗。
- 测试：`tests/frontend-ui.test.mjs:332-372` 注入恶意标签/属性/URL，确认无可执行节点且安全格式保留。

### 2.8 P2：充值敏感输入默认明文，操作弹窗目标未强绑定

- 旧影响与复现：CDK、批量卡密、Session Token/JSON 在共享屏幕上直接可见；查询状态变化后弹窗可能对错误任务发送操作；快速双击可能重复提交。
- 修复：相关单行输入使用 password 类型，多行凭证使用不暴露内容的遮罩层，并提供明确显隐按钮；切换页签重置显隐。弹窗持有查询时的任务对象并发送 `task_no`，通过同步 in-flight ref 防双击；后端仍再次执行独立权限检查。
- 代码依据：`frontend/src/components/RechargeView.jsx:103-157`、`frontend/src/components/RechargeView.jsx:188-236`、`frontend/src/components/RechargeView.jsx:720-770`、`frontend/src/services/api.js:603-616`。
- 影响范围：CDK 验证、单/批查询、任务撤回/关闭和账单凭证。
- 测试：`tests/frontend-ui.test.mjs:374-426`、`tests/frontend-ui.test.mjs:428-485`。

### 2.9 P2：生产页面缺少浏览器内容安全策略

- 旧影响与复现：生产页面没有 CSP；一旦模板、协议或依赖出现注入，浏览器没有额外的脚本/连接/对象来源约束。
- 修复：production 响应设置 `script-src 'self'`、`connect-src 'self'`、`object-src 'none'`、`base-uri 'none'`、`frame-ancestors 'self'`、`form-action 'self'` 等策略。开发环境不设置，避免影响 Vite HMR。
- 代码依据：`app/__init__.py:146-159`。
- 影响范围：生产静态页面和 API 同域响应；未来引入 CDN/跨域 API 前必须更新最小策略并回归。
- 测试：`tests/test_production_hardening.py:162-186` 校验关键 directive；`tests/frontend-ui.test.mjs:306-315` 在正式构建资源和 production CSP 下确认页面无 CSP violation（API 为 fixture）。

## 3. 未关闭风险与放行门槛

| 风险 | 当前证据 | 必须完成的动作 |
| --- | --- | --- |
| 真实支付/余额链路不存在 | 代码无金额、币种、优惠、支付订单、渠道、webhook、余额账本模型或接口 | 明确产品范围；若确需现金充值，单独设计支付域、金额最小单位、签名/去重、账本事务、退款和审计，不得把当前 CDK 页面直接标为已支持 |
| 上游服务鉴权未定义 | `_upstream_post` 是通用 HTTPS JSON 适配，仓库未定义 API key/HMAC/mTLS 或密钥注入协议 | 与上游签订版本化契约，在获授权 sandbox 验证鉴权、幂等、超时查询和错误码后才允许 live |
| 敏感业务字段仍明文 | `RechargeTask` 卡密/邮箱及账号密码、恢复邮箱、TOTP secret 仍存在 SQLite 字段 | 制定字段加密、密钥轮换、备份恢复、日志脱敏和存量迁移方案；上线前验证文件 ACL |
| KYC URL 的 SSRF 边界未知 | 本地只限制 `http://`/`https://`，是否由上游主动抓取不在仓库契约中 | 明确上游行为；如存在抓取，实施域名/IP/重定向/DNS 重绑定防护和专用出口策略 |
| 客户所有权不可恢复 | grant 绑定浏览器 session；历史任务和丢失 Cookie 只能管理员处理 | 建立客服核验 SOP；若需要跨设备自助恢复，应设计独立客户认证，不能退回“卡密+邮箱即授权” |
| Fernet key/旧摘要升级 | 新账单 HMAC 依赖稳定 Fernet key；仓库无自动轮换或旧摘要迁移工具 | 有 pending/unknown 时先用旧版本/旧密钥对账结清；若曾使用临时摘要算法，不得删记录绕过门禁 |
| Linux/真实服务未验收 | 当前证据来自 Windows、本地构建、临时数据库和 fixture | 完成 Docker/systemd/Nginx TLS、CSP、worker 心跳、数据库恢复、Pub/Sub、真实 OAuth 和上游 sandbox 演练 |

## 4. 数据库与部署变化

发布前必须执行 `python -m app.manage init-db`，确认新增表：

- `recharge_task_access`：任务号、创建会话所有权 HMAC；
- `recharge_billing_mutations`：凭证 HMAC、动作、业务操作号、单次 `lease_token`、租约、状态及确认结果。

该命令使用 `create_all()`，不是版本化迁移系统。正式基线首次创建新表会包含完整列；若中间开发版本已有不含账单 `lease_token` 的同名表，必须停服务、备份并保留旧 `pending/unknown` 后受控补列，不可删表重建。升级前还应备份 `.env`、OAuth 客户端和 Fernet key，在数据库副本上验证建表与回滚。具体步骤见 [服务器生产部署指南](server-deployment-guide.md#8-备份升级和回滚)。

## 5. 当前验证状态

| 验证 | 当前结果 |
| --- | --- |
| Python 全量 | 稳定代码快照 213/213 通过，退出码 0；存在既有 `datetime.utcnow()` DeprecationWarning 与 SQLite ResourceWarning |
| 根目录 Node.js | 41/41 通过 |
| Googlemail | 47/47 通过 |
| 前端生产构建 | 通过，产物已输出到 `static/` |
| Playwright UI | 正式构建产物 43/43 通过；页面响应带 production CSP，API 使用 fixture，未连接真实上游 |
| 依赖与 Compose | `pip check`、`docker compose config --quiet` 通过 |
| 静态检查 | 相关 Node 源文件语法检查与 `git -c core.whitespace=cr-at-eol diff --check` 通过 |

稳定代码快照共 344 项自动化测试通过（Python 213、根目录 Node.js 41、Googlemail 47、Playwright UI 43）。本轮未使用真实凭证执行外部业务，未读取生产数据库或真实账号，也未执行生产部署和第三方写操作。`docker compose config` 可能按工具默认行为读取本地配置以完成解析，但验证输出未用于披露真实值。最终放行仍取决于目标 Linux 与获授权上游的验收记录。
