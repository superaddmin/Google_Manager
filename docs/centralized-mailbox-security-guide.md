# 集中管理邮箱与防盗安全架构指南 (Centralized Mailbox Security Guide)

> **修订日期**：2026-09-19
> **适用范围**：当前仓库中的 Gmail 托管、集中验证码、安全审计、账号风险评级与应急锁定功能
> **文档性质**：内部运维与安全技术说明；生产启用仍需按部署环境完成独立验收

---

## 1. 背景与能力边界

本项目把已授权 Gmail 连接、账号凭据和安全操作集中到 Flask API 与前端中控台。管理员登录后，可以查看账号风险评级、读取收件箱中的验证码与安全告警、审计 Gmail 转发/过滤规则，并对账号执行应急锁定。

安全模块提供的是检测、聚合和隔离能力，不是 Gmail 设置的自动修复器。安全模块本身不会自动删除转发规则、自动更改 Gmail 密码、自动轮换 TOTP 或自动替换恢复邮箱；Googlemail 自动化任务是独立入口，只有管理员显式启动且运行依赖满足时才会执行，自动化结果同步可能更新 TOTP secret 或恢复邮箱。

## 2. 威胁模型与对应控制

| 威胁类型 | 攻击手法与特征 | 当前实现的控制 | 未覆盖的事项 |
| --- | --- | --- | --- |
| **隐蔽外部转发** | 在 Gmail 中启用自动转发，或登记攻击者的转发地址 | `SecurityService.audit_forwarding_and_filters` 读取 `getAutoForwarding`、`forwardingAddresses.list`，并报告启用状态与地址 | 不判断地址是否经过业务授权，也不自动删除配置 |
| **恶意过滤与销毁规则** | 过滤验证码/安全邮件并转发、移出收件箱或移入垃圾箱 | 扫描 Gmail filters；带 `forward` 动作的规则会标记为可疑；针对验证码/安全关键词且移除 `INBOX` 或增加 `TRASH` 的规则会标记为可疑 | 规则扫描只产生报告，不修改 Gmail 规则 |
| **分散登录导致凭据暴露** | 在办公电脑浏览器中保存 Google Cookie 或密码 | Gmail API 授权凭据只由服务器使用；OAuth 凭据 JSON 使用 Fernet 加密后写入 `gmail_connections.token_data`，前端读取的是邮件摘要与受控字段 | 管理员仍需保护服务器、会话和加密密钥 |
| **验证码与安全告警遗漏** | 多个邮箱中的登录码或安全邮件无法及时查看 | `/api/security/central-otps` 按邮箱聚合收件箱邮件，提取验证码并标记安全告警；结果可在安全中控台复制 | 提取依赖主题查询和正则启发式，无法保证识别所有邮件；单个连接读取失败会跳过该连接 |
| **弱凭证或缺少 2FA** | 密码过短/字符单一、未配置 TOTP 或恢复邮箱 | 安全雷达按账号字段计算风险分数，并支持按 `critical`、`warning`、`locked` 筛选 | 风险检查不验证 TOTP 是否能成功生成，也不验证恢复邮箱是否可用 |
| **应急处置扩散** | 发现异常账号后仍允许查看、导出或继续执行任务 | `/api/security/accounts/<id>/lock` 将账号置为 `locked`，记录两条审计历史，并请求取消该账号的运行队列、Googlemail 和批量 OAuth 任务；导出时密码和 2FA 密钥脱敏 | 锁定不会删除凭据，也不是完全禁止导出；已在外部执行的动作不保证立即撤销；解锁不会恢复锁定前状态 |

## 3. 系统核心安全设计

### 3.1 Gmail OAuth 与凭证隔离

- Gmail API 使用的 scope 是 `https://www.googleapis.com/auth/gmail.modify`。授权入口为 `/api/gmail/oauth/start`，回调为 `/api/gmail/oauth/callback`。
- 授权状态由 `OAuthStateManager` 注册到 `one_time_tokens` 命名空间 `gmail_oauth`，表中保存 token 的 SHA-256 摘要、JSON payload、过期时间和消费时间（`binding` 对 OAuth state 为空；充值 challenge 才写入绑定摘要），未保存原始 token；有效期为 30 分钟。回调要求状态已注册、未过期且未消费，匹配且有效的状态在成功、取消或缺少授权码时都会消费；会话中的状态不匹配时直接拒绝且不消费另一条状态。
- `GMAIL_CLIENT_SECRET_FILE` 必须指向有效的 Google OAuth 客户端 JSON。`GMAIL_TOKEN_ENCRYPTION_KEY` 必须是有效的 Fernet 密钥。授权凭据 JSON（包含 refresh token 等字段）在写入 `GmailConnection.token_data` 前加密，读取或刷新 token 时解密；密钥丢失会导致已保存凭据无法解密，需要重新授权。
- Gmail 连接读取会检查同邮箱的 `Account.status`。账号为 `locked` 时，Gmail 服务拒绝访问；锁定操作还会取消该账号关联的运行任务。
- 除 OAuth 回调、登录、退出、认证检查和 Pub/Sub webhook 等明确公开端点外，API 蓝图要求管理员登录会话。普通写请求还需 `X-Requested-With: XMLHttpRequest`，并通过 Origin/Fetch 来源检查；Pub/Sub webhook 是由 Gmail 推送调用的写入例外。

### 3.2 集中验证码与安全告警

调用 `GET /api/security/central-otps?limit=<n>` 可从全部已授权 Gmail 连接读取最新邮件。接口将 `limit` 限制在 1 到 50，默认每个邮箱 10 封；前端中控台当前请求 20 封。

实现使用 Gmail 查询：

```text
subject:(验证码 OR code OR verification OR verify OR 安全 OR security OR alert OR "Sign-in")
```

邮件仅从 Gmail `INBOX` 列表读取。返回项包含 `connectionId`、邮箱、主题、发件人、日期、摘要、`extractedOtp`、`otpType`、`serviceName` 和 `isSecurityAlert`。

验证码提取规则按顺序为：`G-` 加 6 位数字、带 `验证码`/`code`/`pin`/`verification`/`security code` 上下文的 4 至 8 位数字、独立的 6 位数字。服务识别表覆盖 Google、Telegram、Twitter/X、Discord、GitHub、Microsoft、Apple、OpenAI、Binance、OKX、Facebook/Meta、Instagram、Amazon、Netflix 和 Steam，未命中时返回“其他服务”。安全告警由固定关键词（例如 `security alert`、`new sign-in`、`password changed`、`suspicious activity`、`2-step verification`、`recovery email changed` 等）判断。

连接读取或该连接内任一封邮件读取异常不会让其他邮箱的结果失败，但当前实现会跳过该连接剩余邮件；此接口是按请求读取聚合结果，安全服务本身不将 OTP 持久化到数据库。

### 3.3 转发、过滤器与 POP/IMAP 审计

调用 `GET /api/security/forwarding-audit` 会遍历 `GmailConnection`，对每个连接执行：

1. 检查 `users().settings().getAutoForwarding(userId='me')`；启用且有地址时生成 critical 发现。
2. 检查 `forwardingAddresses().list(userId='me')`；存在登记地址但未启用自动转发时生成 warning 发现。
3. 检查 `filters().list(userId='me')`；转发动作直接标记可疑，涉及验证码/安全关键词且移除 `INBOX` 或增加 `TRASH` 的规则也标记可疑。
4. 读取 `getImap` 与 `getPop`，返回 `imapEnabled`、`popStatus`。

报告包含过滤器总数、可疑数量、发现列表、`scanErrors`、`status`（`clean`、`warning` 或 `error`）和 `isClean`。任何设置读取错误都会记录到 `scanErrors`，并使 `isClean=false`、`status=error`，不能当作“干净”。

在默认的 `BACKGROUND_TASK_MODE='queue'`（生产配置）下，`/api/gmail/daemon/start`、`stop` 和 `sync-now` 不会在 Web 进程中启动线程，而是把状态或同步任务写入数据库队列；`sync-now` 由独立 `python -m app.worker` 的 Gmail lane 消费，维护循环则按持久化守护状态定时调用 `sync_once`。默认轮询间隔 180 秒（最小 30 秒）。只有 `BACKGROUND_TASK_MODE='inline'`（如测试配置）才由 `gmail_sync_daemon.start()` 在 Web 进程内启动 daemon 线程。每轮轮询未读邮件、执行已配置的 Gmail 规则，并执行上述转发审计。也可以通过中控台的“重新体检”直接调用安全接口。守护线程/worker 不会把 OTP 自动写入安全数据库。

### 3.4 全库风险评级

`SecurityService.calculate_account_risk` 对每个账号计算 0 至 100 的分数：

- `secret` 非空且去空格后长度至少 16 才计为有 2FA，否则加 40 分。
- `recovery` 非空且包含 `@` 才计为有恢复邮箱，否则加 35 分。
- 密码长度小于 8 加 25 分；长度足够但只包含数字或只包含字母（`isalnum()` 且为单一类别）加 15 分。
- 账号状态为 `locked` 时风险等级直接为 `locked`，不再按分数显示 `critical`/`warning`。

分级阈值为：`safe` 0-29、`warning` 30-59、`critical` 60-100；锁定账号为 `locked`。`GET /api/security/overview` 额外返回账号总数、各等级数量、未配 2FA/恢复邮箱数量、已授权 Gmail 连接数、健康指数和最多 20 个 critical 账号。健康指数按 `100 - (criticalCount*60 + warningCount*25) / totalAccounts` 计算后用 `int()` 向下取整，并截断到 0 以上；锁定账号计入总数但不增加 critical/warning 惩罚。

### 3.5 应急锁定与解锁

锁定流程：

1. 管理员调用 `POST /api/security/accounts/<account_id>/lock`，可在 JSON 中传入 `reason`。
2. 账号状态写为 `locked`，写入 `status` 和 `security_action` 两条 `account_history` 记录。
3. 请求取消该账号的 `RuntimeQueue`、Googlemail 和批量 OAuth 任务；已经在外部执行或正在收尾的动作不保证立即撤销。
4. 普通账号接口对锁定账号禁止读取 2FA 验证码和修改历史；`Account.to_dict()` 隐去密码、恢复邮箱和 2FA，账号导出接口仍返回账号行，但将密码和 2FA 密钥写为 `******`。
5. Gmail 服务在该邮箱对应账号锁定时拒绝读取邮件。

调用 `POST /api/security/accounts/<account_id>/unlock` 可解除锁定。当前实现将状态设置为 `inactive` 并记录解锁历史，不会恢复锁定前的 `pro`/其他状态，也不会自动修改密码、2FA 或恢复邮箱。

### 3.6 与 Googlemail 自动化的关系

Googlemail 任务支持管理员选择账号后，在独立的 Node/Playwright 进程中执行 2FA 与恢复邮箱任务。任务会拒绝 `locked` 账号；运行完成后由结果同步逻辑更新账号的 `secret`，有新恢复邮箱时更新 `recovery`，并记录历史。该流程不是安全雷达或锁定接口自动触发的轮换动作，是否可执行取决于 Node、`googlemail/src/main.mjs` 和 Playwright 依赖是否就绪。

## 4. 生产配置与运维要求

生产工厂在启动时强制检查：

- `SECRET_KEY` 至少 32 字节，不能使用示例值。
- `ADMIN_PASSWORD` 至少 16 个字符、不能有首尾空白，不能使用示例值。
- `GMAIL_TOKEN_ENCRYPTION_KEY` 必须存在且可由 Fernet 解析。
- 充值模式只能是 `disabled` 或 `live`，禁止 `mock`。

`GMAIL_CLIENT_SECRET_FILE`、Pub/Sub 参数和回调地址不是应用工厂的强制启动校验项，由具体 Gmail 功能读取；`/health/ready` 仅在配置了客户端文件路径时检查该文件存在且可读，不会解析 JSON。
生产配置类将 `AUTO_CREATE_DB` 设为 `False`，因此数据库初始化需要显式执行 `python -m app.manage init-db`。

反向代理地址处理使用 `TrustedProxyMiddleware`：只有请求来源 IP 落在 `TRUSTED_PROXY_CIDRS` 时才信任 `X-Forwarded-For`/`X-Forwarded-Proto`。不要把任意网段加入该配置。管理员登录失败 3 次会按来源 IP 封禁 24 小时；这不是 Gmail 风险评分的一部分。

加密密钥、管理员密码和数据库连接信息只能通过受控环境变量或部署配置注入，不应写入文档示例、日志或前端。轮换 `SECRET_KEY` 会使现有 Flask 会话和依赖其 HMAC 的一次性业务令牌失效；轮换 Gmail Fernet 密钥前必须设计凭据重新加密或重新授权窗口。

## 5. 推荐安全运维流程

1. 先通过管理员登录并确认生产环境的 `SECRET_KEY`、`ADMIN_PASSWORD`、`GMAIL_TOKEN_ENCRYPTION_KEY` 和 OAuth 客户端文件均已配置。
2. 在“安全防盗”中控台查看健康指数、风险账号和已授权 Gmail 数量；对 `critical` 账号人工核对密码、2FA 与恢复邮箱。
3. 使用“转发审计”检查 `auto_forwarding_enabled`、登记地址、可疑过滤器、POP/IMAP 状态及 `scanErrors`。出现 `error` 时先修复授权或连接问题，再判断是否安全。
4. 使用集中 OTP 视图读取验证码与安全告警。验证码提取结果只能作为人工核对线索，不要把“未识别”理解为邮箱没有验证码。
5. 发现异常账号时先执行应急锁定，确认队列和自动化任务已取消，再通过受控渠道更换密码、复核恢复邮箱和 TOTP。确认完成后再解锁；解锁后状态为 `inactive`，需按业务流程重新设置状态。
6. 需要持续轮询时显式启用 Gmail 守护状态；生产默认 queue 模式由独立 worker 的 maintenance 执行，只有 inline 模式才启动 Web 进程内线程，并监控状态、失败数和最近日志。停止服务或部署时确认 worker（以及 inline 模式下线程）的运行状态。

## 6. 实现与测试依据

核心实现位于 [`app/services/security_service.py`](../app/services/security_service.py)、[`app/services/gmail_service.py`](../app/services/gmail_service.py)、[`app/services/email_poller.py`](../app/services/email_poller.py)、[`app/routes/api.py`](../app/routes/api.py)、[`app/services/request_security.py`](../app/services/request_security.py) 与 [`app/config.py`](../app/config.py)。前端中控台为 [`frontend/src/components/SecurityCenterView.jsx`](../frontend/src/components/SecurityCenterView.jsx)，API 封装为 [`frontend/src/services/api.js`](../frontend/src/services/api.js)。

相关回归覆盖见 [`tests/test_security_service.py`](../tests/test_security_service.py)、[`tests/test_api.py`](../tests/test_api.py)、[`tests/test_email_poller.py`](../tests/test_email_poller.py) 和 [`tests/test_production_hardening.py`](../tests/test_production_hardening.py)。这些测试覆盖风险评分、OTP 提取、转发审计错误处理、锁定/解锁、锁定账号脱敏导出、OAuth/安全请求头和守护线程接口；它们不代表真实 Gmail、生产代理或真实恢复流程的授权验收。
