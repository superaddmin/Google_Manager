# 全功能与生产上线审查（2026-09-18）

## 1. 结论

**No-Go：当前工作区不能认定为按修订计划完成，也不具备直接对公网提供真实充值交付服务的生产条件。**

- 已具备 React 页面、Flask 路由、部分 Google/Gmail 业务能力和部署骨架；不等于业务闭环、生产一致性与安全边界完整。
- 充值仍是“尝试上游 + 自动模拟兜底”，不是修订计划要求的 disabled/mock/live 隔离。生产环境关闭上游后仍可创建模拟任务，故 `RECHARGE_UPSTREAM_ENABLED=false` **不是禁用充值的安全开关**。
- 现有 156 项测试全部通过，但不覆盖本次复现的严重缺陷；其中充值只有 6 个后端测试，现有前端 UI 套件没有充值流程用例。
- 本次只新增本审查报告，不修复业务代码、不更新依赖、不提交 Git、不消费真实卡密、不变更订阅、不登录真实账号。

| 审查轴 | 判定 | 主要原因 |
| --- | --- | --- |
| 规格符合性 | 未达标 | 模式隔离、真实挑战、状态持久化、幂等、轮询、下载与数据最小化未落实 |
| 生产架构与安全规范 | 未达标 | 默认会话密钥、镜像敏感文件、多进程状态、OAuth 部署与守护生命周期问题 |
| 现有自动化与构建 | 通过但不足 | 156 项通过且构建一致；不能代替缺失的错误路径、真实契约和生产环境验收 |

## 2. 范围与证据边界

- 基线：`92d452c3b11e0098e87801594188283b7071718d`，审查包含当前已跟踪改动和未跟踪源码，不仅是 HEAD 已提交内容。
- 规格来源：`C:\Users\www\.gemini\antigravity\brain\dbe5b115-d713-4d1c-9de7-9df6c4f35dbf\implementation_plan.md` 的修订版。
- 范围：账号管理、Googlemail、Gmail OAuth/收件箱/规则、批量授权、收信守护、安全防盗、充值前后端，以及 Docker/Compose/Gunicorn/Nginx/systemd/安装脚本。
- 验证使用虚构凭证、内存 SQLite、Mock HTTP；没有读取 `.env` 内容、真实凭据、生产数据库或浏览器账号数据。仅检查了敏感路径是否存在。
- 未验证真实第三方 API 契约、真实 Google 登录/回调、真实扣费/充值、Linux 容器运行、公网 TLS/防火墙、压力容量和恢复演练；不能对此作成功承诺。
- P0：安全或业务真实性的首要阻塞；P1：功能/架构上线阻塞；P2：补强项或需要限定条件的风险。未核实能力与已复现缺陷分开记录。

## 3. P0：首要上线阻塞

### P0-01 上游失败会变成“充值/取消续费成功”

- 位置：`app/services/recharge_service.py:102`、`app/services/recharge_service.py:210`、`app/services/recharge_service.py:396`、`app/services/recharge_service.py:435`、`app/services/recharge_service.py:492`；`app/config.py:26`。
- 根因：HTTP 异常、超时、解析失败被吞掉并返回 None，业务层接着生成本地有效卡密、处理中任务、已付款账单或取消/恢复成功。上游业务拒绝也可能走同一路径。
- 复现：模拟 live 请求超时后，任务创建仍为 HTTP 201/processing，取消续费仍为 HTTP 200/auto_renew=false。隔离 production 应用关闭上游后，同样可创建任务并返回 201。
- 后果：用户可能误认为已充值或已停止扣费；上游已受理但响应丢失时也无法辨识，后续重试有重复操作风险。
- 修正门槛：显式模式与独立适配器；生产默认 disabled，mock 不访问网络且有醒目标识；live 失败不得模拟成功，结果未知须持久记录并核对。按已验证能力开放，不以“官方代充”等硬编码文案替代证据。

### P0-02 部署模板默认会话密钥可伪造管理员登录

- 位置：`docker-compose.yml:17`、`docker-compose.yml:19`；`docker-compose.yml:12` 同时将 8002 发布到所有接口。
- 触发条件：部署者配置了启动所需其他项，但保留模板内公开的管理员密码/SECRET_KEY 默认值。并非断言当前真实 `.env` 正使用默认值。
- 复现：仅在隔离 production 内存库中使用模板默认会话密钥签发测试 Cookie，未输入管理员密码，`GET /api/accounts` 从匿名 401 变为 200。报告与日志不包含密钥、Cookie 或账号内容。
- 修正门槛：部署变量缺失立即失败，拒绝示例值；密钥单独生成和管理。若默认值或密钥曾暴露，需要轮换并失效已有会话。限制直接后端端口暴露，通过正确配置的 TLS 入口访问。

### P0-03 Docker 构建会携带敏感文件

- 位置：`Dockerfile:65` 的 `COPY . .` 与 `.dockerignore:1`。
- 根因：忽略清单未排除 `.env`、`credentials.json`、实例库本体、账号源文件及完整敏感输出目录；仅忽略 `instance/*.db-journal` 不能排除数据库。
- 本机事实：`.env` 与 `instance/` 存在；未读取其内容。按当前模板构建可能将秘密写入不可通过后续删除消除的镜像层。
- 修正门槛：补齐构建上下文排除规则，运行时注入秘密；扫描最终镜像及构建产物。已经构建或推送过含秘密镜像时另行评估轮换与清理，不在本次审查中擅自执行。

## 4. P1：充值功能与状态闭环

### R01 查询尚未提交的 CDK 会死锁

- 位置：`app/services/recharge_service.py:23`、`app/services/recharge_service.py:259`、`app/services/recharge_service.py:263`、`app/services/recharge_service.py:135`。
- `lookup_task()` 持有普通 Lock 后调用再次获取同一锁的 `validate_redeem_code()`，造成线程自锁。
- 离线复现：未知 CDK 查询线程等待 1 秒仍未返回，共享锁仍被持有；源码锁顺序证明不是普通网络慢。随后该进程内其他需锁的充值操作也会阻塞。
- 修复应重新划分锁内/锁外职责，尤其不能持锁进行网络校验；新增有截止时间的回归测试。

### R02 幂等、持久化和多进程一致性缺失

- 位置：`app/services/recharge_service.py:23`、`app/services/recharge_service.py:210`、`deploy/gunicorn.conf.py:16`。
- 任务、原始卡密与挑战都在进程内字典；计划中的 `app/models/recharge_operation.py` 不存在，默认 Gunicorn 为两个 worker。
- 创建与查询落在不同进程时本地记录不一致，重启丢失。live 请求先访问上游再检查本地重复，无法靠后续字典检查阻止重复发送。
- 离线复现：相同请求连续提交两次，模拟上游收到两次调用，未携带幂等键；没有持久化 pending/unknown 或恢复核对。
- 修正门槛：先落操作记录再发送，数据库唯一约束、明确未知结果、重启恢复；上游只有明确支持幂等协议时才能声称可幂等重试。单 worker 仅可减轻进程分裂，不解决重启和结果不确定性。

### R03 “契约校验”没有落实挑战与核心业务约束

- 位置：`app/services/recharge_service.py:80`、`app/services/recharge_service.py:156`、`app/services/recharge_service.py:186`、`app/routes/recharge.py:29`。
- challenge 生成后存入字典，但创建只校验非空，未核验签发、期限、一次性消费、会话/卡密/套餐/凭证绑定；live 也使用本地伪造挑战，没有完成上游挑战流程。
- 套餐任意字符串、任意非空凭证可通过；未再次核实真实卡密、bound_email 或 account_change_locked；协议是本地静态 HTML，未绑定版本。
- 离线复现：从未签发的 challenge、任意套餐、不带 CSRF 的请求仍返回 201。
- 17 个现有充值接口均已验证匿名 401，这是已落实项；但 `/csrf` 不存在，服务端无 CSRF/Origin 校验。携 Cookie 的 test_client 也接受不可信 Origin。
- 定性边界：上述 Origin 测试只证明服务端未做校验，**不等于已经证明浏览器能跨站利用**；SameSite、JSON 请求与 CORS 仍影响实际可达性。CSRF 在此作为 P1 安全门槛，而非无条件跨站攻击结论。
- 修正门槛：后端重验套餐/绑定关系、真实挑战与协议摘要，独立 CSRF 防护；未知能力禁用，不能为了接通界面假造成功条件。

### R04 撤回、关闭和重新提交的状态机相互矛盾

- 位置：`app/services/recharge_service.py:218`、`app/services/recharge_service.py:334`、`app/services/recharge_service.py:362`、`app/services/recharge_service.py:485`。
- 撤回默认 `confirmed=True`，未提供确认也能操作；撤回/关闭只检查邮箱非空，没有与任务绑定邮箱比较或验证状态允许操作。
- 已撤回状态不在允许重新创建的集合中，重新提交仍返回旧 recalled；closed 却允许重新创建，违背“卡密已注销”的提示。
- 同一码多条记录时 `_find_task_by_code` 返回插入最早项，因此新建后查询仍可能返回旧 closed。
- 上述路径均用虚构邮箱/CDK 离线复现。修复需明确状态转移、确认条件、卡密终止语义及当前任务唯一关联，不只是修改显示文字。

### R05 发票/收据下载没有可用闭环

- 位置：`app/routes/recharge.py:187`、`app/routes/recharge.py:195`、`app/routes/recharge.py:245`；`frontend/src/components/RechargeView.jsx:895`、`frontend/src/components/RechargeView.jsx:1093`。
- 前端打开 GET 链接，但下载路由仅支持 POST；账单接口返回的链接同样不满足路由方法与卡密参数契约。
- 实测 GET 为 405；直接 POST 又因未导入 datetime 抛 NameError。即使只补导入，当前内容也是硬编码 PAID/$20 的 TXT，不是真实 PDF 发票。
- 修正门槛：按已确认上游契约获取真实文件/链接，鉴权、参数、Blob 下载、类型/大小/文件名及目标白名单全部接通；不以虚构收据替代真实凭证。

### R06 前端向导、任务号查询和进度更新未闭环

- `frontend/src/services/api.js:434` 永远把输入作为 redeem_code 发到 lookup，没有调用任务号详情 API。
- `frontend/src/components/RechargeView.jsx:750` 先 setLookupCdk 再立即调用依赖旧状态的查询，首次跳转可能不查询或查询旧内容。
- CDK/邮箱修改未清除旧验证/确认；解析和选号还自动勾选邮箱确认：`frontend/src/components/RechargeView.jsx:148`、`frontend/src/components/RechargeView.jsx:509`、`frontend/src/components/RechargeView.jsx:622`、`frontend/src/components/RechargeView.jsx:1185`。
- 没有充值任务自动轮询、退避、终态停止或乱序保护；`getRechargeConfig` 虽定义但未被页面使用，不能展示模式或按能力禁用入口。
- 修正门槛：可测试的向导状态机、显式查询参数、任务号/CDK 分流、受控轮询和取消；增加真实界面级回归，不仅测试 API 函数。

### R07 凭证与产品边界没有按计划收敛

- `frontend/src/App.jsx:490` 向充值组件传完整账号对象，`app/models/account.py:43` 序列化含密码与 2FA 密钥；即使尚未把完整对象发送上游，也已违反最小组件边界。
- 原始 CDK 在进程字典中保留，教程要求完整 Cookie JSON，提交后敏感输入没有完整清理策略；缺少清晰的第三方接收方/用途确认。
- `app/services/recharge_service.py:113` 和教程仍将 Pro 5x 固定归入 Claude；原计划已要求核实映射。KYC 未按高敏感能力独立禁用或确认。
- 修正门槛：只传 `{id,email}`、只提取必要凭证字段、CDK 采用受保护关联标识，核验产品映射与用途授权；未验证 KYC/套餐分支关闭。

## 5. P1：项目其他模块与部署

| 问题 | 证据位置 | 影响与修正方向 |
| --- | --- | --- |
| OAuth/任务/守护状态仍为进程私有 | `app/services/gmail_service.py:22`、`app/services/batch_oauth_service.py:83`、`app/services/googlemail_service.py:145`、`app/services/email_poller.py:30`、`app/services/auth_service.py:18` | 默认两 worker 下，任务状态/取消/去重/封禁不一致；无管理员会话的批量 OAuth 回调落错进程会丢 state。普通浏览器回调有 session_state 回退，不能一概说所有授权必失败。应共享必要状态并隔离后台任务生命周期。 |
| 生产重定向配置链断开 | `app/services/gmail_service.py:103`、`app/config.py:22`、`deploy/nginx/google-manager.conf:59` | 服务读取 GMAIL_REDIRECT_URI，但配置类不加载该环境变量；代理转发 scheme 未由受信任代理配置处理，自动地址可能仍为 HTTP。显式加载/验证地址，按固定代理层数处理转发头并测试 HTTPS 回调。 |
| 安全扫描失败却报告“干净” | `app/services/security_service.py:217`、`app/services/security_service.py:237`、`app/services/security_service.py:281`、`app/services/security_service.py:310` | settings 查询异常被吞掉，最终 isClean 可为 true。辅助离线复现多个权限错误仍显示干净。将 clean/error/unknown 分开，按具体 API 确认可支持的 scopes/账号类型，不能承诺权限失败等于无风险。 |
| 锁号未阻断敏感操作 | `app/routes/api.py:456`、`app/services/account_service.py:487`、`frontend/src/components/AccountListView.jsx:406` | locked 账号仍能导出敏感数据，通用状态切换可绕过锁定；界面仍有显示/复制密码和 TOTP 的路径。后端统一执行锁定策略，不能仅用状态标签实现隔离。 |
| 守护不是可恢复的 24H 服务 | `app/routes/api.py:1098`、`app/services/email_poller.py:87`、`app/services/email_poller.py:130` | 仅手动启动线程，worker 重启后不恢复，快速 stop/start 未等待旧线程退出；需要持久配置、单实例协调、可观测的独立生命周期与健康状态。 |
| 收信动作缺少处理幂等 | `app/services/gmail_rule_service.py:219`、`app/models/gmail_task_log.py:30`、`app/services/security_service.py:383` | 未读邮件可反复产生规则日志/确认项，缺少 connection/rule/message 唯一处理键。OTP 页面主要为请求时读取，不能等同于守护已持久归集；补处理键、游标及准确文档。 |
| 账号秘密及运行日志保护不足 | `app/models/account.py:34`、`app/services/account_service.py:416`、`app/services/account_service.py:438`、`app/services/batch_oauth_service.py:171`、`googlemail/src/batch-oauth-worker.mjs:69` | 密码/TOTP 及修改历史明文存储，任务明文文件权限/保留期未收敛，代理参数可能含凭据而被记录。加密或严格限定存储访问，历史保留脱敏信息，运行目录限制权限并清理/脱敏。 |
| 原生安装与运行浏览器路径不同 | `deploy/setup-server.sh:80`、`deploy/systemd/google-manager.service:22` | 安装未指定 /ms-playwright，运行却指定该路径，默认原生部署可能找不到 Chromium；统一安装/运行路径并用服务用户做启动检查。 |
| 高权限运行且关闭浏览器沙箱 | `deploy/systemd/google-manager.service:9`、`Dockerfile:75`、`googlemail/src/batch-oauth-worker.mjs:85` | systemd/容器缺少专用低权限运行身份，浏览器带 --no-sandbox，扩大受损后的影响；落实非 root、目录权限、必要的系统隔离。 |
| Node.js 20 已不受常规维护 | `Dockerfile:45`、`deploy/setup-server.sh:60` | 官方日程显示 Node 20 已于 2026-04-30 EOL，并非本次审阅日才 EOL。模板仍安装 20，本地测试却用 22；切换到受支持 LTS 并验证整个自动化链，不在本次审查中擅自升级。 |

## 6. P2 与发布前待核验项

- 多个路由在校验 JSON 对象前调用 `.get`；发送 JSON 数组会产生 AttributeError/500，需统一输入形状、类型与长度校验。相关位置：`app/routes/recharge.py:73`。
- 直接插入协议 HTML：`frontend/src/components/RechargeView.jsx:1232`。当前来源是本地硬编码，因此不认定已发生远程 XSS；接上第三方内容前必须安全清洗或改纯文本。
- 缺少 accessToken 的 JSON 仍可能展示“已验证”；批量查询没有先去重：`frontend/src/components/RechargeView.jsx:597`、`frontend/src/components/RechargeView.jsx:271`。
- Gmail 新增轮询使用没有请求互斥的 setInterval，慢响应可能重叠并覆盖新状态：`frontend/src/components/GmailInboxView.jsx:118`。
- 健康检查只访问 `/api/auth/check`，不能代表数据库可写或守护正常；Docker 在已有 `static/index.html` 时跳过重建。当前产物本次哈希比对一致，但未来构建仍有使用旧资源风险。
- Gmail Pub/Sub 验证参数可能进入访问日志，watch 续期与部署配置链需要专项验收；不能仅以端点存在认定推送完整。
- 未见明确的数据库一致性备份、恢复演练和版本升级流程。当前相对 HEAD 的模型改动仅为历史字段显示名，未发现新增列迁移，因此不把“缺迁移框架”说成本次已经证实的表结构启动故障；未来新增表/升级仍须提供可复核迁移与恢复方案，可复用项目机制，不限定必须引入某框架。
- 生产启动只检查 Gmail 密钥非空而未验证 Fernet 格式，文档示例需要避免让用户直接复制占位值；启动验收应验证格式且不输出秘密。
- `docs/recharge-integration.md` 不存在；README 对完整交付、锁号阻止导出、守护归集等表述需改为与可验证能力一致。

## 7. 功能完整性矩阵

| 功能 | 当前判断 | 尚缺的上线闭环 |
| --- | --- | --- |
| 登录、账号 CRUD、批量导入、统计、基础导出 | 主流程已有实现与回归 | 部署密钥安全、锁号强制策略、敏感存储与审计 |
| Googlemail 安全设置自动化 | 有模块、单元与界面测试 | 真实授权账号验证、低权限浏览器运行、生产任务生命周期 |
| Gmail 收件箱与规则 | 有实现与离线测试 | HTTPS OAuth、必要权限、重复事件幂等、真实 Google 环境验证 |
| 批量 OAuth | 部分实现 | 跨 worker state/任务共享、重启恢复、服务器回调配置 |
| 安全中心与应急锁定 | 界面/服务存在但语义不完整 | 失败不报干净、锁定阻断敏感动作 |
| 24H 收信守护与 OTP | 部分实现 | 持久生命周期、单实例、停止等待、游标/幂等/归集 |
| 充值导航、四个 Tab、外观 | 已接入 | 无专属 UI 回归，缺模式和能力限制 |
| 充值蓝图管理员会话鉴权 | 已核验 | 17 条接口匿名均 401；仍需独立 CSRF/业务认证 |
| CDK/凭证验证与协议挑战 | 不完整 | 真正契约、失效规则、挑战核验与数据最小化 |
| 创建/查询/撤回/关闭任务 | 不完整 | 真实结果、死锁修复、状态机、持久化、幂等和恢复 |
| 账单与取消/恢复续费 | 不完整 | 禁止模拟成功、真实权限与结果核对 |
| 发票与收据 | 不可用 | 路由、参数、运行时异常及真实文件链全部修复 |
| Docker/原生生产部署 | 有骨架，不可直接放行 | 秘密隔离、配置必填、运行身份、支持期与实际容器验收 |

## 8. 实际验证结果

### 8.1 项目原有自动化

| 检查 | 结果 | 备注 |
| --- | --- | --- |
| Python unittest discover | 70/70 通过 | 包含充值 6 项；有未关闭 SQLite 连接 ResourceWarning，不影响本次退出码 |
| 根目录 Node 三文件测试 | 19/19 通过 | 账号导入、本地 Googlemail 与 API 基础行为 |
| Googlemail Vitest | 47/47 通过 | 6 个测试文件，离线/mock |
| Playwright 前端 UI 套件 | 20/20 通过 | 无充值交付完整流程用例 |
| 合计 | 156/156 通过 | 不重复计入辅助审查运行的子集 |
| Vite 生产构建 | 通过 | 输出到临时目录，未覆盖原 static |
| 构建产物比对 | 全部一致 | index.html、JS、CSS 的 SHA-256 均与当前 static 一致 |
| Python AST | 29 文件通过 | 不执行真实业务 |
| Node ESM 语法 | 11 文件通过 | googlemail/src/*.mjs |
| Bash 语法 | 通过 | 通过 Windows System32 bash 启动器运行 bash -n |
| 充值匿名鉴权探针 | 17/17 返回 401 | csrf 路由不存在 |
| Python pip check | 通过 | 只证明依赖一致性，不是漏洞扫描 |
| Compose config --quiet | 通过 | version 字段有过时警告，不代表容器可运行 |
| Docker 实际构建/运行 | 未验证 | Docker Desktop Linux Engine 命名管道不存在；且须先修复敏感构建上下文 |

### 8.2 新增审计探针（不属于上述 156 项）

使用临时内存应用与 Mock HTTP 得到以下实际结果：

| 场景 | 观测结果 |
| --- | --- |
| 关闭上游，任意长度合格的假 CDK | 200 / valid |
| 未签发 challenge、任意套餐、无 CSRF | 201 / processing |
| 撤回不提供确认并使用错误邮箱 | 200 / recalled |
| 撤回后重新提交 | 201，但仍为 recalled |
| 已关闭卡密再次提交 | 新建 processing |
| 新建后以卡密查询 | 仍返回旧 closed |
| 直接 POST 下载任务发票 | NameError: datetime 未定义 |
| 使用账单返回链接 GET 下载 | 405 |
| validate 接口提交 JSON 数组 | AttributeError |
| live 任务提交模拟超时 | 201 / processing，伪成功 |
| live 取消续费模拟超时 | 200 / auto_renew=false，伪成功 |
| 相同 live 创建请求发送两次 | 上游调用 2 次，无幂等 Header |
| 未提交 CDK 查询 | 自锁，线程仍阻塞且共享锁被占用 |
| production 使用模板默认密钥签发 Cookie | accounts 从 401 变 200，无需密码登录 |

### 8.3 依赖安全与运行时

- 2026-09-18 执行 `npm audit --json`：frontend 报告 6 个受影响包项（4 high、2 moderate），googlemail 报告 4 个（1 high、3 moderate）。这不是“10 个独立 CVE”的统计。
- 两个项目执行 `npm audit --omit=dev --json` 均为 0；本次告警集中在开发/构建/测试依赖，不能据此宣称生产运行依赖全部存在高危漏洞，也不能忽略构建链风险。
- 本地没有 pip-audit，未完成 Python CVE 扫描；`pip check` 不替代它。未执行 audit fix 或任何依赖升级。
- 本地为 Python 3.14.7、Node 22.23.2；Docker 模板为 Python 3.11、Node 20，当前测试不构成容器环境兼容性证明。
- 官方 Node 发布页与 Release 日程确认 Node 20 的结束日期为 **2026-04-30**。审阅时 Node 22/24 仍在支持期；选定版本后仍需项目兼容验证。
- 核验来源：`https://nodejs.org/en/about/previous-releases`；`https://raw.githubusercontent.com/nodejs/Release/main/schedule.json`。

### 8.4 可复核命令与日志

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
node --test tests/account-import.test.mjs tests/googlemail-local-copy.test.mjs tests/api-service.test.mjs
npm --prefix googlemail test
node --test tests/frontend-ui.test.mjs
.\.venv\Scripts\python.exe -m pip check
docker compose config --quiet
docker version --format '{{.Server.Version}}'
& "$env:SystemRoot\System32\bash.exe" -n deploy/setup-server.sh
npm --prefix frontend audit --json
npm --prefix googlemail audit --json
npm --prefix frontend audit --omit=dev --json
npm --prefix googlemail audit --omit=dev --json
```

本次构建使用项目 build 入口，仅通过 `--outDir` 指向临时目录。UI 测试读取现有 static，已用哈希证明与本次新构建相同。

审计日志目录：`C:\Users\www\AppData\Local\Temp\google-manager-audit-20260918-a3197bbb`。其中含各组测试输出、构建输出、`static-comparison.json`、`recharge-reproductions.json`、`production-auth-reproduction.json`、依赖审计和 Docker 可用性记录。该目录仅供本次核对，不作为生产持久证据库；日志不包含真实凭据。

## 9. 最小修复顺序与放行门槛

1. **先阻断错误交付与秘密泄漏**：实现真正 disabled，移除 live 模拟回退、默认会话密钥及危险构建上下文；不得将“关闭上游”冒充关闭充值。
2. **再修业务正确性**：解决锁死、challenge/输入契约、状态机、任务号查询、下载和前端失效规则；每个已复现问题先加回归测试。
3. **完成可靠性与权限边界**：持久化操作记录、未知结果核对、幂等及多进程协调；CSRF、最小凭证、锁定策略、安全扫描错误态同步补齐。
4. **修复生产链路**：OAuth HTTPS/重定向、浏览器安装路径、低权限运行、支持中的 Node LTS、守护生命周期、备份恢复与有效健康检查。
5. **在预发布验收**：明确授权的上游测试资源逐项验证；Linux 镜像实际构建运行，跨 worker/重启/断网/限流/慢响应/重复提交/浏览器下载验证；执行备份恢复和密钥轮换演练。
6. **最后放行**：P0/P1 关闭且有回归证据，未验证能力继续禁用，文档与代码相符。真实充值或订阅操作需独立确认，不能靠测试成功率替代业务验收。

结论有效范围是本次工作区快照；后续修复需要复审。本报告不是对真实生产环境、第三方平台或所有潜在漏洞的安全保证。
