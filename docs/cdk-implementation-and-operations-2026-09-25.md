# CDK 首期实现、验收与操作手册

更新日期：2026-09-25。本文记录当前工作区实际实现；对应 [产品与技术方案](cdk-end-to-end-product-technical-plan-2026-09-25.md)。首期实现已按后续授权部署到 `123.206.210.86`，CDK 工作台已启用，真实充值保持关闭，原管理员密码未变更。服务器账号获取、镜像身份、迁移、备份及在线测试见 [CDK 服务器部署记录](cdk-server-deployment-2026-09-25.md)。

## 1. 交付范围与当前结论

主入口已整合：`/admin` 默认直接展示 CDK 管理；`/`、`/recharge` 默认直接展示 GM1 兑换。旧订单后台通过 `/admin?tab=orders` 访问，旧供应商卡密及订阅工具通过 `/recharge?service=legacy` 访问；原 `/admin/cdk`、`/recharge/cdk` 保留兼容。主后台沿用 CDK 具名员工认证，不把旧共享密码转换为发卡权限。具体点击步骤见 [充值后台使用说明](recharge-admin-guide.md#2-cdk-生成与前端使用)。

公开配置 `GET /api/cdk/config` 新增 `fulfillment_enabled`（当前充值模式非 disabled）。`enabled` 表示卡密模块开关，两者含义独立；`fulfillment_enabled` 不代表真实上游已验收。通道关闭时后台说明库存验证限制，前端显示关闭提示并禁用核销按钮；服务端仍按原配置拒绝履约。本次整合不增加数据库迁移或更换密钥。

已实现可运行的首期闭环：**供应商库存导入与验证 → 平台批次制卡 → 独立员工审批 → 分发/导出或客户领取 → 平台码验证 → 原子占用 → 原充值订单履约 → 查单/证据对账 → 卡密与库存同步终结**。

入口：`/admin` 为具名员工卡密管理，`/`、`/recharge` 为平台兑换页；`/admin?tab=orders` 管理旧协议充值任务，`/Googlemail` 管理邮箱资产。顶部导航可切换模块；新员工登录不赋予邮箱后台权限，原共享管理员会话也不赋予 CDK 权限。

`CDK_ENABLED` 模板默认 `0`；上述测试服务器已显式配置为 `1`，并初始化独立员工。现阶段适合隔离环境验收，不能将合成上游测试当作真实供应商、邮件通道或正式生产验收。原生产阻断项仍见 [上线阻断计划](release-blockers-remediation-plan-2026-09-24.md)。方案中的 P1 活动、P2 钱包/支付/会员/财务账本没有伪造实现。

## 2. 已实现功能和代码定位

| 功能 | 实际行为与验收条件 | 代码入口 |
| --- | --- | --- |
| 具名员工与权限 | admin/operator/reviewer/support/auditor 五种固定角色；显式全局或渠道范围；停用、改密、权限变更立即使旧会话失效；CSRF 与近期认证 | `app/services/cdk_identity.py:40`；`app/routes/cdk.py:255` |
| 客户身份 | SMTP over TLS 发送随机 6 位 OTP；5 分钟、最多 5 次尝试；无邮件配置时关闭邮箱登录/指定客户发行；客户停用与会话撤销 | `app/services/cdk_identity.py:148`；`app/routes/cdk.py:322` |
| 权益与渠道 | 不可覆盖的产品修订号，首期 PLUS/PRO 外部单份权益；续费开关；整数参考面额与币种；渠道停用阻止新核销 | `app/services/cdk_catalog.py:26` |
| 库存导入 | 每行一份供应商原码，最多 100 行/128 KiB；预览错误行；确认只导入有效行；全局查重与历史订单排除；先进入 quarantine | `app/services/cdk_catalog.py:61` |
| 库存验证 | 网络请求不持写锁；回写检查版本；套餐/环境/有效期核验；最近 24 小时内验证的独占库存才可发卡 | `app/services/cdk_catalog.py:130` |
| 批次制卡 | 1–100 张；每张独占库存；草稿批次全量生成在同一事务内；无库存则全部失败；草稿取消后库存回隔离区 | `app/services/cdk_catalog.py:169` |
| 编码与存储 | GM1 + 26 位随机 Crockford Base32 + 校验符；Fernet 认证加密，独立版本化 HMAC 查询索引；默认只显示尾号 | `app/services/cdk_crypto.py:22`；`app/services/cdk_crypto.py:66` |
| 双人审批 | 激活、解冻、关闭、作废、延期、补发、导出、证据释放；申请人与复核人不能相同；30 分钟有效；精确对象版本与参数快照；复核时重验申请人当前权限 | `app/services/cdk_catalog.py:247` |
| 分发与领取 | 每卡一个分发记录，随机 256 位票据存摘要与密文；领取与票据消费同事务；指定邮箱必须先领取；不能通过直接输入原平台码绕过 | `app/services/cdk_catalog.py:410`；`app/services/cdk_redemption.py:389` |
| 受控导出 | 先分发，再审批固定卡列表；只导出平台码；文件内容以密文保存数据库；15 分钟、最多 3 次下载；每次重验版本、状态与操作者，并审计 | `app/services/cdk_catalog.py:449` |
| 卡密控制 | 冻结阻止新核销；在途保持占用；成功永久消费；补发仅允许冻结且未使用卡，旧卡永久作废，同一库存转绑新卡并留历史 | `app/services/cdk_catalog.py:321` |
| 前端兑换 | 本地格式校验、服务器验证、权益确认、Session JSON/目标邮箱一致性、挑战、稳定请求键、提交结果找回、本人记录、撤回/关闭 | `frontend/src/components/CdkPortal.jsx:1` |
| 原子核销 | 卡/库存/挑战/请求键/订单/操作/核销意图/审计/outbox 同事务；跨进程以 SQLite BEGIN IMMEDIATE、唯一约束和版本检查保护 | `app/services/cdk_redemption.py:96` |
| 履约与恢复 | 提交前先持久化 dispatching；不保存 Session Token；超时 unknown 保留资产；worker 只查询，不重复创建上游订单；成功同步 redeemed/consumed | `app/services/cdk_redemption.py:138` |
| 查单和对账 | 托管任务的 worker 查单进入同一协调器；双人审批的未消费证据释放；撤回/关闭未知操作须证明未执行或由上游确认终态 | `app/services/cdk_redemption.py:183`；`app/services/cdk_redemption.py:319` |
| 兼容防绕过 | GM1 与已登记供应商原码不能走 legacy 写/查接口；旧后台不展示托管订单；内部 worker 保留托管查单入口 | `app/services/cdk_service.py:207`；`app/routes/recharge.py:122` |
| 审计与统计 | 发行/分配/补发/审批/导出/拒绝请求记录；卡与意图状态统计按渠道过滤；参考面额不作为收入 | `app/services/cdk_catalog.py:475` |
| 迁移/运维 | 新迁移版本、全表结构校验、每 SQLite 连接启用外键；CDK 全量校验、密钥轮换；健康检查抽样、过期占用恢复、未知/事件积压告警计数 | `app/services/cdk_maintenance.py:13`；`app/monitor.py:39` |

管理界面：`frontend/src/CdkAdminApp.jsx:1`。接口入口：`app/routes/cdk.py:1`。数据库：`app/models/cdk.py:1`。测试：`tests/test_cdk.py:1`、`tests/test_cdk_concurrency.py:1`、`tests/cdk-flow.test.mjs:1`。

## 3. 实现相对方案的明确收敛

以下为当前版本的真实契约，不能按最初拟议表名/路径直接对接：

1. 采用独立 `cdk_staff/cdk_staff_sessions`，不直接扩展旧共享密码会话，隔离 CDK 员工与邮箱管理员权限。旧后台保留历史任务管理，托管卡订单在卡密工作台管理。
2. `cdk_redemptions.task_no` 唯一外键关联原订单，不给历史 `recharge_tasks` 回填新卡，不改变其 `redeem_code` 的供应商原码含义。卡的 active/successful 指针通过状态约束、事务服务和一致性校验维护；未宣称这些两个指针有数据库外键。
3. 每次最多 100 张，以短且有上限的同步事务生成；导入预览与导出保存为 `cdk_jobs`。尚未实现方案中的 5,000 行异步分块作业、租约续跑和大文件服务。不能直接调大限额。
4. 库存只接入现有默认供应商，不支持多供应商路由。首期 PLUS/PRO，要求 Session JSON 的 `accessToken` 和 `user.email`；CLAUDE_CODE、KYC、成品账户仍走原受控协议，不允许用平台券假装支持这些交付契约。
5. 输入时间使用带时区 ISO8601，输出时间为 UTC Unix 秒；界面转换为本地时间。有效期 `[not_before, expires_at)`；卡级延期不得超过库存有效期或批次起始后 366 天。参考面额是展示字段，不是支付流水。
6. 领取 API 为 `/api/cdk/claims`；客户认证为 `/api/cdk/customer/*`；后台控制由 `/api/cdk/admin/approvals` 统一处理。没有对外伪造方案中每个拟议别名接口。
7. outbox 与核销同事务，worker 标记已观测，用于本地追踪和积压检查。没有配置外部事件总线或邮件通知消费者，不应解释为外部消息已经投递。
8. 无图形验证码服务接入，当前为数据库共享限流、单次挑战和 OTP 尝试限制；尚未完成真人验证码、供应商签名回调和多维行为评分。匿名持券无法提供“每真实用户限领”保证。
9. 单卡控制与批次控制已实现；撤销分发再换渠道、批量延期作业、活动配额、统计日报和财务对账系统仍属于后续工作。已分发卡如果泄露，应冻结并经审批补发，不能静默重复交付。
10. 审计在应用 API 层只追加，SQLite 文件本身不提供物理防篡改；需沿用受控服务器访问、备份和外部归档。未声称方案全部 P0 规模化/外部集成验收已完成。

## 4. 本地可执行验收

在项目根目录使用 PowerShell，沿用已安装依赖和原工具链：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m unittest tests.test_cdk tests.test_cdk_concurrency -q
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -q
.\.venv\Scripts\python.exe -m compileall -q app deploy tests/cdk_flow_server.py
npm --prefix frontend run build
node --test tests/cdk-flow.test.mjs
node --test tests/frontend-ui.test.mjs tests/recharge-ui.test.mjs tests/recharge-flow.test.mjs tests/cdk-flow.test.mjs tests/api-service.test.mjs
```

CDK 浏览器测试创建临时 SQLite、两个员工、隔离 Flask 和本机合成 HTTP 供应商，实际点击管理页面制卡、另一个浏览器审批、下载平台码、手机尺寸页面兑换，再验证仅一笔订单、券已核销、库存已消费。不会调用公网真实充值上游。测试结束清理测试进程与临时数据库。

跨进程测试用 4 个独立 Python 进程访问同一个临时 SQLite：同键重放同一意图；不同键仅一个成功占用。其他测试覆盖事务故障回滚、unknown 不重下单、过期 prepared 释放、上游身份冲突、OTP/领取、权限/范围、冻结、补发、导出失效、密钥轮换和防绕过。

执行结果将在本文件第 10 节记录。项目没有声明独立 Python lint、前端 lint 或 TypeScript typecheck 命令；Vite 构建通过不等同于 TypeScript 类型检查。

## 5. 启用前人工配置

测试阶段可以继续使用 IP 与 HTTP，不需要域名或证书。以下为新环境初始化步骤；当前服务器已完成独立密钥、迁移和员工初始化，不要重复创建或替换密钥。初始化必须针对受控测试库或正式发布窗口，不能把测试库覆盖到现有服务器数据库。

### 5.1 配置密钥与开关

在部署目录的受控 `.env` 中设置，**保留已有 SECRET_KEY、GMAIL_TOKEN_ENCRYPTION_KEY 和管理员配置**。新键格式如下，值必须自行生成，不能使用文档占位值：

```dotenv
CDK_ENABLED=1
CDK_ACTIVE_KEY_ID=v1
CDK_ENCRYPTION_KEYS='{"v1":"<独立的Fernet加密密钥>"}'
CDK_LOOKUP_KEYS='{"v1":"<另一个独立的32字节Base64URL密钥>"}'
```

在受控主机使用项目 Python 分别运行两次 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`。仅在受控终端完成配置，不复制到工单、聊天、仓库或日志。两组 CDK 键必须不同，且不能与 Gmail 加密键相同。`.env` 中 JSON 使用完整单引号包裹，兼容项目严格 dotenv 解析与 Compose。

Compose、受控 `compose_release.py` 字段白名单及 preflight 已支持这些变量。`CDK_ENABLED=1` 本身不会开启真实充值；`RECHARGE_MODE` 仍沿用原约束。production 禁止 mock；隔离合成履约验收运行 `tests/cdk_flow_server.py`，不要为了演示撤销现有生产模式保护。

备份数据库与对应密钥，密钥备份应与数据库备份分开受控保管。丢失密钥会导致卡密无法验证、找回或导出。

### 5.2 数据库迁移和员工初始化

已进入受控发布窗口、选择新镜像后，Linux Compose 测试部署操作为：

```bash
docker compose run --rm --no-deps initialize python -m app.manage init-db
docker compose run --rm --no-deps initialize python -m app.manage validate-db
docker compose run --rm --no-deps initialize python -m app.manage validate-cdk
docker compose run --rm --no-deps initialize python -m app.manage create-cdk-staff --username release-maker --role operator --scope '*'
docker compose run --rm --no-deps initialize python -m app.manage create-cdk-staff --username release-checker --role reviewer --scope '*'
docker compose run --rm --no-deps initialize python -m app.manage create-cdk-staff --username recharge-owner --role admin --scope '*'
```

密码通过 getpass 交互输入，不放命令参数或文档。员工密码至少 12 字符。`'*'` 是明确全局授权，不能给普通渠道运营默认套用；已有渠道后可用其 ID 初始化/调整范围。两个审核身份应由不同人员实际保管；技术上的员工 ID 分离不能替代组织职责分离。

迁移 `20260925_08_cdk_catalog` 只创建新增表，不给历史订单造库存。`validate-cdk` 必须返回通过；若 `PRAGMA foreign_key_check` 发现旧库孤儿数据，先在副本核对并制定数据修复，不能关闭外键跳过。

正式发布仍走现有 release bundle、不可变镜像、GO 签字与受控 `compose_release.py` 流程；上面的直接 Compose 命令是已授权测试部署的操作说明，不取代正式门禁。CLI 新入口也可在受控主机运行 `python -m app.manage ...`。

### 5.3 可选客户邮箱验证

```dotenv
CDK_SMTP_HOST=<SMTP TLS主机>
CDK_SMTP_PORT=465
CDK_SMTP_FROM=<已授权发信邮箱>
CDK_SMTP_USERNAME=<SMTP用户名>
CDK_SMTP_PASSWORD=<SMTP凭证>
```

当前实现为 SMTP_SSL，校验服务器证书；不是 STARTTLS/587。确认发信域授权、投递、垃圾箱、发送限额后才开放限定客户的批次和票据。邮件通道不可用时保留未配置状态：界面不显示 OTP 入口，后台拒绝指定客户发行，不返回固定验证码。不限客户的持券兑换仍可单独测试。

## 6. 实际运营操作顺序

1. 具名员工登录 `/admin`，创建渠道和 PLUS/PRO 权益版本。配置单份权益与参考面额；已创建权益版本不修改，调整商品用新的 revision。
2. 从供应商取得**未外发、未被其他系统使用**的原码，确认采购引用和真实有效期。在“供应商库存”粘贴，每行一份，预览后确认有效行。重复码、GM1 码和有历史订单的原码不能导入。
3. 逐份点击“验证供应商库存”，确认成为“可分配”。验证失败保留原记录；不要凭本地按钮改为有效库存。上游接口未配置时该步骤不能完成。
4. 新建批次，选择已发布的权益版本、渠道、有效期和数量。数量最多 100，库存有效期必须覆盖平台券有效期。点击“生成 CDK 卡密”产生草稿后申请激活；另一名员工在审批页核对并批准。
5. 在卡密列表分发，记录交接对象引用。普通匿名券可选择同批次卡密申请导出；原申请员工在“导入与导出”下载。指定邮箱的卡必须通过领取票据交付，禁止导出绕过领取身份。
6. 用户从首页 `/` 或 `/recharge` 验证平台码，粘贴 Session JSON 并确认目标邮箱/套餐。提交后记录请求编号与订单编号。断网后点击“找回提交结果”，不更换键强行重试。
7. processing/unknown 表示未完成。查询只观察已创建任务；unknown 保留库存。管理员只能在有明确证据时申请释放，另一员工复核后恢复券资格。已成功订单关闭后券仍为已核销。
8. 冻结立即拦截新核销；已占用意图继续对账。补发须先冻结且未使用，由审批原子转移库存，旧码永久作废；已消费/在途不允许补发或延期。

## 7. 事务、异常及恢复契约

- **T1**：校验当前主体/范围、规范化参数；先查请求键重放；然后在同一 SQLite 写事务消费挑战、占卡/库存、创建任务与核销意图并记审计/outbox。失败全部回滚。
- **T2**：prepared → dispatching 先提交，再发一次上游请求。上游 `client_task_no/idempotency_key` 均使用已保存 task_no，卡密使用供应商库存原码。调用端 Session 只在请求内存中存在。
- **T3**：观察结果必须匹配本地和远端身份及意图版本，同事务修改任务、卡、库存、审计/outbox。迟到旧版本响应不能覆盖新状态。
- prepared 超过 300 秒且从未 dispatch 才可释放；dispatching 超过 120 秒视为 unknown。未知不能根据超时、进程退出或日志缺失释放，也不重新创建供应商订单。
- failed/recalled/closed 不自动推断未消费；保留占用并等证据。completed 永久消费；后续 closed 不退券。
- 同主体、同操作、同请求键、同参数返回原资源；异参 409。浏览器只在 sessionStorage 保存请求编号，不保存 Session JSON、平台码或供应商原码。
- 稳定错误码包括 `IDEMPOTENCY_CONFLICT`、`CHALLENGE_INVALID`、`ALREADY_USED`、`REDEMPTION_IN_PROGRESS`、`CODE_FROZEN`、`CODE_VOID`、`CODE_EXPIRED`、`NOT_YET_VALID`、`USER_RESTRICTED`、`CLAIM_REQUIRED`、`INSUFFICIENT_STOCK`、`MODE_MISMATCH`、`REAUTH_REQUIRED`、`RATE_LIMITED`、`UPSTREAM_UNAVAILABLE`。
- 默认限流：IP 300 次/分钟，平台码探测 IP 30 次/分钟，同规范化卡或凭证 20 次/分钟；员工登录用户名+IP 10 次/15 分钟；邮箱 OTP 同邮箱 3 次/10 分钟，IP 10 次/10 分钟。429 带 Retry-After，不采用旧后台的 24 小时封禁规则。

## 8. 运维、密钥轮换与回滚

持续运行原 worker。它恢复中断意图、清理过期导入/导出密文、观察 outbox，并通过原充值查单调度器同步托管订单。选择待查任务时同时检查 CDK 未决意图和未知操作，因此订单已 completed 但关闭结果未知、或订单 failed 但消费事实未确认时仍继续查单；不会仅凭旧订单状态漏查。**未知创建只查单，不重新下单**。实现：`app/worker.py:170`；回归：`tests/test_cdk.py:448`、`tests/test_cdk.py:476`。

`/health/ready` 在 CDK 启用时抽样校验 20 条/类并返回 `cdkConfiguration`；`python -m app.manage validate-cdk` 执行全量密文/索引/关联状态校验。全量校验宜低峰或发布窗口执行，不应以抽样替代备份恢复演练。

`python -m app.monitor` 新增 `cdk_redemption_overdue`、`cdk_mutation_overdue`、`cdk_outbox_overdue` 计数；非零进入 issues，沿用现有监控退出码和告警脚本。验收时应人为制造测试积压确认真实接收告警，不能只看计数存在。

密钥轮换步骤：

1. 冻结发行/核销入口的变更窗口，备份数据库和旧密钥；确保旧备份仍保留可用密钥。
2. 将 v2 加入两组 keyring，保留 v1；设置 `CDK_ACTIVE_KEY_ID=v2`，所有 Web/worker 使用同一配置。旧索引和旧解密键必须仍在，不能先删旧键。
3. 执行 `python -m app.manage rotate-cdk-keys` 盘点，再执行 `python -m app.manage rotate-cdk-keys --apply`。当前是单事务工具，不适用于未经压测的大库在线轮换。
4. 执行 `python -m app.manage validate-cdk` 和 `validate-db`，验证新旧索引查重和业务密文。删除旧键前等待 OTP 5 分钟有效期结束，确认没有依赖旧键的存量密文；旧备份对应密钥独立保留。
5. 本版本不在线轮换 SECRET_KEY；它还用于既有会话、客户邮箱身份和请求指纹，不能按普通 CDK keyring 替换。

回滚：先冻结批次/停发新券，继续让认识 CDK 意图的新 worker 对账；备份订单、核销、审批与供应商事实。已经发卡或发生外部履约后，禁止直接回滚到不认识托管订单的旧镜像，禁止用旧数据库抹掉外部已消费事实。可以回退到兼容 CDK schema 的已验证版本；需要灾难恢复时，在隔离副本恢复数据库、旧密钥并完整对账后再切换。

## 9. 正式放行仍需完成的事项

| 优先级 | 未完成/需人工提供 | 执行与验收 |
| --- | --- | --- |
| 已完成测试环境；正式候选仍需复验 | 新版本目标 Linux/Compose 部署验收 | 已在目标服务器迁移现有库副本和在线库，完成外键、Web/worker、重启、备份恢复与公网登录；证据见服务器部署记录 |
| 上线前 | 真实供应商测试库存、鉴权、查询/撤回/关闭/未消费证据契约 | 用受控真实测试账号验证；必须证明超时查单能收敛及真实幂等；不得仅凭合成结果启用 live |
| 测试环境已初始化；正式职责待分配 | 持久独立密钥、具名员工与职责范围 | 测试密钥、admin/reviewer 和独立恢复校验已完成；正式操作人员、渠道范围与异地密钥备份仍需负责人落实 |
| 按能力门禁 | 实际 SMTP 发送和投递 | 若不提供，保持邮箱登录/指定客户发行关闭；只验收不限客户的匿名持券流程 |
| 上线前 | 目标容量与告警送达 | 当前限制单次 100；实际磁盘、锁等待、延迟、峰值和报警收件端需在目标环境测得 |
| 后续，扩大规模前 | 异步分块大批量、真人验证码、多供应商、独立事件投递、统计日报 | 另行实现与压测；不能把本次同步小批量结果作为大规模性能承诺 |
| P1/P2 | 活动配额、每用户限领、钱包、虚拟币、会员、商品及财务账本 | 单独产品评审、会计规则、迁移和资金验收后实施 |

## 10. 本轮验证记录

以下均为 2026-09-25 本机实际执行结果。表中 Python 使用项目 `.venv/Scripts/python.exe`；全量与专项回归设置 `PYTHONIOENCODING=utf-8`，并以 `PYTHONWARNINGS=ignore::ResourceWarning,ignore::DeprecationWarning` 隐藏既有警告，不影响失败退出码。

| 验证命令 | 最终结果 |
| --- | --- |
| `python -m unittest discover -s tests -p 'test_*.py' -q` | 共 499 项，482 项通过、17 项按平台跳过，0 失败；198.360 秒 |
| `python -m unittest tests.test_cdk tests.test_cdk_concurrency tests.test_runtime_recovery tests.test_runtime_reliability -q` | 62 项全部通过；其中 CDK 29 项、跨进程并发 2 项；31.302 秒 |
| `python -m unittest tests.test_recharge_mutation_reconciliation -q` | 15 项全部通过，包括新迁移下审计表幂等恢复及旧数据保留 |
| `node --test tests/frontend-ui.test.mjs tests/recharge-ui.test.mjs tests/recharge-flow.test.mjs tests/cdk-flow.test.mjs tests/api-service.test.mjs` | 83 项全部通过；包含真实浏览器 CDK 全链路，66.054 秒 |
| `npm --prefix frontend run build` | 通过，已更新实际 `static/` 产物 |
| `python -m compileall -q app deploy tests/cdk_flow_server.py tests/test_cdk.py tests/test_cdk_concurrency.py tests/test_recharge_mutation_reconciliation.py` | 通过 |
| `node --check tests/cdk-flow.test.mjs` | 通过 |
| `git -c core.safecrlf=false -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check` | 通过；保留原有 CRLF 文件，不为消除 CR 字符误报批量转码 |

17 项跳过均来自既有平台要求：Linux/root 目录属主与宿主机准备 12 项（`tests/test_compose_host_preparation.py:20`），容器 umask 1 项（`tests/test_container_permissions.py:11`），Linux 备份密钥属主/权限 1 项（`tests/test_backup.py:91`），POSIX 硬链接/文件类型/目录遍历 3 项（`tests/test_deployment_preflight.py:489`、`:524`、`:560`）。本轮没有新增跳过条件，需在目标 Linux 或 CI 补验这些项目。

回归中发现并已修复：外键开启后父任务与子操作的 ORM 写入顺序、旧孤儿测试夹具、迁移数量写死为 7 的旧断言、legacy 托管库存冲突后的事务回滚，以及 worker 漏查已完成订单的未知关闭操作。后者的两项测试先复现查单 0 次，再验证修复后查询一次、消费状态正确且不重发创建/关闭请求。最终全量包含这些回归，没有以跳过规避失败。

本节保留首轮本地验证结果；后续目标 Linux 部署、补测、备份恢复与公网验收已完成，见 [服务器部署记录](cdk-server-deployment-2026-09-25.md)。合成测试不证明真实充值或正式上线，真实上游、实际 SMTP 和目标生产容量仍未验证。本轮未运行额外依赖漏洞扫描，未新增第三方依赖，本记录不替代原发布安全审计。项目没有独立 lint/typecheck 命令，未将构建或语法检查冒充这些检查。

## 11. 主入口整合回归（2026-09-25）

本次将已实现的制卡和兑换嵌入主页面，新增充值模式显示，不改 CDK 数据结构、库存约束或员工认证。以下为整合后重新执行的结果，不沿用第 10 节基线计数。

| 环境与命令 | 结果 |
| --- | --- |
| 本机 `npm --prefix frontend run build` | 通过，生成并更新 `static/` |
| 本机 `python -m unittest tests.test_cdk tests.test_cdk_concurrency tests.test_recharge_admin tests.test_online_smoke -q` | 45 项通过，36.566 秒 |
| 本机 `node --test tests/frontend-ui.test.mjs tests/recharge-ui.test.mjs tests/recharge-flow.test.mjs tests/cdk-flow.test.mjs tests/api-service.test.mjs` | 85 项通过，0 跳过，67.157 秒 |
| `docker --context desktop-linux buildx build --platform linux/amd64 --load --metadata-file .test-tmp/cdk-integrated-build.json --tag google-manager:cdk-integrated-20260925 .` | Linux/amd64 多阶段镜像构建通过 |
| 服务器候选镜像内 `python -m unittest tests.test_cdk tests.test_cdk_concurrency tests.test_migration_constraints tests.test_container_permissions tests.test_recharge_admin tests.test_online_smoke -q` | 55 项通过，0 跳过，32.787 秒 |
| 服务器候选镜像内 `node --test tests/cdk-flow.test.mjs` | 1 项真实浏览器全链路通过，6.806 秒；使用临时 SQLite 和合成上游 |
| 本机 `python -m compileall -q app/routes/cdk.py tests/test_cdk.py` 及第 10 节 `git diff --check` | 通过 |

新增回归覆盖 `/admin` 内嵌具名登录及旧订单认证隔离，`/`、`/recharge`、`/recharge/cdk` 默认 CDK，充值关闭提示和禁用核销，旧模块导航，以及公开配置三个模式的区分。端到端测试现从 `/admin` 生成卡、由另一员工审批并导出，最终在 `/` 消费一次库存，断言仅创建一笔供应商订单。旧协议用例迁至明确的 legacy 入口，未删减原业务断言。
