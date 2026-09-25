# 生产上线阻断项整改与验收计划（2026-09-24）

> 本计划承接 2026 年 9 月 24 日上线 readiness 复核，针对仍然阻断上线的 1–7 项问题制定执行顺序、责任域、证据要求和放行门槛。
> 更新至 2026-09-25。正式生产结论仍为 **不可上线**；隔离服务器测试可以继续。运行缺陷、测试结果和待人工配置已分开记录；详见 [人工配置步骤](production-manual-configuration.md)。
> 根据用户最新要求，测试阶段无需域名和证书。测试入口分为用户充值 `/`、邮箱管理 `/Googlemail`、充值管理 `/admin`，仍使用 `123.206.210.86`。下列正式发布门禁不阻断当前 HTTP 测试；访问和运维步骤见 [测试入口说明](production-manual-configuration.md#0-当前测试阶段直接使用-ip-和-http)，后台范围见 [充值后台说明](recharge-admin-guide.md)。

## 1. 适用范围与发布边界

- 本次候选范围仍限定为 **CDK 工作台**。
- 现金支付、支付回调、余额账户、账本和现金充值不属于本次发布范围，不能在产品说明或运营话术中宣称已支持。
- `RECHARGE_MODE` 在真实上游 sandbox 验收、鉴权、幂等和 unknown 闭环完成前必须保持 `disabled`。
- 用户已授权更新 `123.206.210.86` 的隔离测试部署；真实 Google 账号、充值上游和正式数据尚未提供。本轮只操作合成数据，真实外部验收待准备完成。
- 任何“本地通过”只证明对应本地或合成场景，不得替代目标 Linux、真实外部服务和生产数据副本验收。

## 2. 当前状态摘要

以下为 2026 年 9 月 24 日历史基线，本轮新结果以 3.2 节为准：

- Python 全量测试 `384` 项通过、`4` 项跳过；根 Node 测试 `41` 项通过；前端 UI/充值测试 `53` 项通过；Googlemail coverage 测试 `71` 项通过。
- 容器内迁移、备份恢复、部署工具和 worker 重启验证通过；合成数据库恢复演练应用 `7` 个迁移并通过 SQLite 完整性检查。
- Python/npm 依赖审计无已知漏洞；仓库精确 Trivy 扫描无 HIGH/CRITICAL secret 或 misconfiguration；等价本地镜像扫描无 HIGH/CRITICAL 漏洞。
- Compose 配置、`actionlint`、Python 编译检查和工作区差异检查通过。
- 生产镜像原始联网构建未完成；使用本地已校验 Chrome 制品构建的 `google-manager:ci-local` 仅作为等价验证镜像，不能替代正式 registry 制品。
- 目标服务器 `123.206.210.86` 已完成隔离在线测试：Ubuntu 24.04.4、Docker Engine 29.1.3、Compose 2.40.3；Web/worker readiness、worker check、monitor、管理员登录、鉴权、登出、数据库重启持久化和合成账号闭环均通过。
- 当时目标服务器 Chromium 默认容器检查报 `No usable sandbox`。9 月 25 日已用有限 seccomp profile 修复；仅凭原日志不能判定宿主 AppArmor 是根因，详见 B02。
- 在线测试首次暴露 Compose bind mount 权限缺陷：目录/凭据误属宿主登录用户时，`initialize` 报 `sqlite3.OperationalError: unable to open database file`，readiness 报 `gmailConfiguration=false`；已新增 `deploy/prepare-compose-host.sh` 固化 UID/GID `10001:10001`、目录 `0700`、凭据 `0600` 的准备步骤，并更新部署包白名单和文档。
- 本地 Ubuntu 24.04 隔离容器已验证宿主准备脚本：正常路径将嵌套目录/文件收敛到 `0700/0600` 与 `10001:10001`；错误 UID/GID、嵌套符号链接均拒绝，且异常拒绝发生在已有目录权限修改前。
- 真实 Google OAuth/Gmail/Pub/Sub、真实充值上游、目标 Linux/TLS/Nginx、真实数据库副本、实际告警送达和正式 CI/Release Signoff 尚未闭环。

## 3. 阻断项矩阵

| 编号 | 严重度 | 阻断项 | 当前状态 | 正式上线前必须满足 |
| --- | --- | --- | --- | --- |
| B01 | P0 | 正式生产镜像推送和制品身份未闭环 | 原始 Dockerfile 联网构建已成功，CI 已支持 main 手动 publish 和同 digest 验证；registry/远端 CI 待配置 | 正式 CI 构建成功；镜像推送到受控 registry；记录平台、digest、构建元数据和制品来源 |
| B02 | P0（正式制品复验） | 目标 Linux Chromium 启动 | 测试服务器已通过官方有限 seccomp profile 下两类 sandbox 启动；Compose/CI 已统一，无需修改宿主 AppArmor | 在正式 B01 digest 上重跑两类启动、截图、locale、清理和实际任务 smoke；保持 UID 10001、sandbox 启用 |
| B03 | P0 | 干净候选版本、Release Manifest、人工 GO 未形成 | 未提交/推送；临时 tag 不可替代 registry；此前本地 SBOM 有 46 项许可证元数据缺失，最终制品须复核 | 固定 commit/tag；远端 CI 全绿；正式 digest、SBOM、扫描、许可证和回滚版本均绑定 manifest；负责人独立签署 GO |
| B04 | P1 | 生产数据库迁移、备份、恢复和 RPO/RTO 缺少正式数据证据 | 补齐 Gmail 表/列/唯一约束及 Gmail/队列密文校验；合成和测试库演练不代表生产 RPO/RTO | 正式结构副本完成迁移、备份、恢复、回滚、密钥恢复和 `pending/unknown` 保护；记录耗时、恢复点、保留策略和审批结果 |
| B05 | P1 | 真实 Google OAuth/Gmail/Pub/Sub 尚未验收 | 仅完成本地 fixture、合成凭据和代码回归 | 授权测试账号完成 OAuth 回调、Token refresh、Gmail API、worker、Pub/Sub（如启用）及失败恢复验收；记录账号、时间、范围和结果，不记录 token |
| B06 | P1 | 真实充值上游鉴权、幂等和 unknown 闭环未验收 | `RECHARGE_MODE=disabled`；本地隔离测试不能证明上游契约 | 授权 sandbox 完成鉴权、重复请求、超时查询、撤回/关闭、查无任务、人工对账和审计验证；完成前不得启用 `live` |
| B07 | P1 | 正式入口、容量、告警和运维验收 | 目标服务器 loopback TLS 和公网 IP/HTTP 测试入口均已通过登录/账号闭环；正式域名/证书、业务容量和实际告警接收仍待配 | Linux 三服务、域名/DNS/TLS/Nginx、目录权限、健康路径、资源容量、重启恢复、告警送达和值班交接全部有记录 |

## 3.1 目标服务器在线执行记录

本节记录本轮对 `123.206.210.86` 的隔离在线测试。测试使用随机合成密钥、合成 OAuth 客户端文件和 `RECHARGE_MODE=disabled`，未读取或写入真实生产数据，服务仍只绑定 `127.0.0.1:8002`。

| 阶段 | 执行与证据 | 结果 |
| --- | --- | --- |
| 环境准备 | Ubuntu 24.04.4；Docker Engine 29.1.3；Compose 2.40.3；原有容器/卷为空 | 通过 |
| Compose 权限缺陷复现 | 宿主目录为 `ubuntu:0700` 时，`initialize` 报 `sqlite3.OperationalError: unable to open database file`；`credentials.json` 为 `ubuntu:0600` 时 `gmailConfiguration=false` | 已复现 |
| 权限修复 | 将 `instance/`、`googlemail/runtime/`、`googlemail/output/` 及其普通子项设置为 `10001:10001/0700`、`0600`，拒绝错配 UID/GID、符号链接、特殊文件、跨设备项和硬链接 | 通过；已固化到 `deploy/prepare-compose-host.sh`，并在 Ubuntu 24.04 隔离容器实测 |
| 应用启动 | `init-db` 完成迁移；Web/worker 均 healthy；`/health/ready` 返回 200 | 通过 |
| 运行检查 | `python -m app.worker --check` 返回 0；`python -m app.monitor --min-free-mib 1` 返回 0，异常计数为 0 | 通过 |
| 业务 smoke | 首页、静态资源、管理员登录/鉴权/登出、充值禁用门禁、合成账号创建/重启后查询/删除 | 通过 |
| 恢复检查 | worker 重启、Web 重启、SQLite 文件持久化和 readiness 恢复 | 通过 |
| 浏览器 sandbox | `docker run --rm --init --network none google-manager:online-test node googlemail/src/startup-check.mjs` | 失败；B02 继续阻断 |

以上为历史执行记录；B02 失败已在 2026-09-25 修复并重测。通过项只证明隔离测试可运行，不能替代正式制品与真实外部服务验收。

## 3.2 2026-09-25 全链路复核与整改

本轮代码修复、复现依据、精确命令/结果和候选 SHA-256 见 [服务器验收记录](server-validation-2026-09-25.md)。本地全量 Python 444 项，427 通过、17 个平台条件跳过项均在 Linux 补测；Node 94 项、Googlemail 71 项通过。最终镜像相关容器测试 157 项，156 通过、1 项因未交付 Dockerfile 跳过（源码环境已通过）。

- B01：原始 Dockerfile 已成功构建，CI 支持 main 手动发布和同 digest 验证；没有代替用户提交/push，也没有使用 synthetic registry 伪造证据。
- B02：有限 seccomp profile 修复实际启动失败，测试服务器已验证；正式 digest 复验仍是发布条件。
- B04：Gmail schema/密文和队列密文纳入完整恢复检查；新增只读 `python -m app.manage validate-db`。readiness 使用有界样本，不能代替全量恢复验证。
- B05：已删除 Gmail 消息不再永久阻塞规则/通知游标；真实账号与网络验收仍待准备。
- B06：冲突上游任务先拒绝，新增操作级 unknown 人工失败结案与审计；管理员凭确切证据将操作置 `rejected`，旧回执不能覆盖新意图。人工步骤见手册 5.1；真实充值保持 disabled。
- B07：隔离 Nginx/TLS、HTTPS smoke 和 Compose monitor timer 已落地；正式域名/证书、业务容量目标、告警接收与宿主重启窗口待配置。

用户暂不提供外部环境信息。剩余输入统一列于 [人工配置手册第 1 节](production-manual-configuration.md#1-最后需要提供的信息)，不把待配置项目标成通过。生产结论保持 NO-GO，隔离测试可继续。

## 4. 分项整改方案

### B01：完成正式镜像构建和制品交付

**问题与依据**

- 正式构建入口为 `Dockerfile`；CI 镜像构建与检查见 `.github/workflows/release-gate.yml:216`、`:279`、`:385`。
- 浏览器安装器使用固定版本和 SHA-256 清单，见 `deploy/browser-artifacts.json`、`deploy/install_browser.py`。
- 2026-09-25 已使用原始 Dockerfile 从官方 URL 完成 Chrome 固定版本与 SHA-256 校验构建；本地候选不是受控 registry 发布证明。

**执行步骤**

1. 在干净候选 commit 上由 CI 执行：

   ```bash
   docker buildx build \
     --platform linux/amd64 \
     --pull \
     --push \
     --tag registry.example.org/google-manager:<git-sha> \
     --metadata-file build-metadata.json \
     .
   ```

2. 固定记录：Git SHA、目标平台、基础镜像 digest、Chrome 版本和 SHA-256、镜像 digest、构建时间及构建日志。
3. 用同一个 registry digest 执行镜像内容检查、启动检查、容器 smoke、恢复演练和 Trivy/SBOM。
4. 将证据放入受控制品库，不把正式证据只保留在 `.test-tmp/` 或本地 Docker cache。

**验收标准**

- 构建命令返回 `0`，不使用临时 Dockerfile、本地替代浏览器或浮动 tag。
- 镜像以 `registry/...@sha256:<64位摘要>` 形式交付。
- 镜像内容满足 `.github/workflows/release-gate.yml:258` 的 UID、Node、静态文件、Playwright 和 startup-check 要求。
- 同一 digest 被写入候选 manifest、Compose 候选配置、扫描报告和签字单。

**失败处理**

- 浏览器下载失败时先排查 CI 网络、官方制品可达性、缓存和总超时，不得跳过 SHA-256 校验或改为下载 latest。
- 若目标平台不能获得固定制品，停止发布并提交平台/网络变更，不通过复制本地目录绕过构建链路。

### B02：完成 Linux Chromium sandbox 验收

**问题与依据**

- 浏览器运行时要求生产使用绝对 executable path 并强制 `chromiumSandbox: true`，见 `googlemail/src/browser-runtime.mjs:59-88`。
- 禁止的 sandbox 参数由 `googlemail/src/browser-runtime.mjs:10-13,48-55` 拒绝。
- 原默认容器 seccomp 下出现 `No usable sandbox`；现已加入 `deploy/chromium-seccomp.json` 并在目标 Linux 通过。`seccomp=unconfined` 不是修复方案。

**执行步骤**

1. 在目标 Linux 主机、目标 Docker/Compose 版本和目标架构上运行：

   ```bash
   docker run --rm --init --network none --shm-size=256m \
     --security-opt seccomp=deploy/chromium-seccomp.json \
     registry.example.org/google-manager@sha256:<digest> \
     node googlemail/src/startup-check.mjs
   ```

2. 检查宿主机 user namespace、seccomp、AppArmor/SELinux 和容器运行用户，不修改为特权模式。
3. 运行两类 Playwright 启动 API、固定 Chrome 版本、locale、截图非空、临时 profile 清理和实际最小任务 smoke。
4. 采集最终 Chromium 进程参数，确认没有 `--no-sandbox`、`--disable-setuid-sandbox` 或同类绕过项。

**验收标准**

- 发布包声明的有限 seccomp profile 和 UID 10001 下 startup-check 返回 `0`；profile 摘要须纳入发布清单。
- 容器以 UID `10001` 非 root 运行。
- 无 `seccomp=unconfined`、`privileged`、`--no-sandbox` 等补偿性绕过。
- 目标内核和容器配置由运维负责人记录并复核。

**失败处理**

- 目标宿主不支持 Chromium sandbox 时，不能修改应用代码关闭 sandbox；应修正内核/容器运行时能力或暂停发布。
- 若确需例外，必须由安全负责人书面批准补偿控制、有效期和回滚方案；本计划默认不接受该例外。

### B03：形成正式候选版本、Manifest、GO 和回滚制品

**问题与依据**

- 预检要求不可变镜像摘要，见 `deploy/preflight.py:810-817`。
- Manifest 校验和人工签署状态见 `deploy/preflight.py:948-960`、`docs/release-signoff-template.md`。
- 当前工作区有未提交变更，且 `app/utils/email.py` 尚未纳入 Git 跟踪。

**执行步骤**

1. 将当前需要发布的所有代码、迁移、配置、测试和文档纳入候选提交；确认不包含真实 `.env`、凭据、数据库、日志和运行目录。
2. 在干净 checkout 上复跑完整 CI，并保存成功运行 URL。
3. 生成正式发布包，至少绑定：
   - Git SHA 和 `dirty=false`
   - 镜像 digest 和平台
   - 锁文件及公开发布文件 SHA-256
   - Trivy JSON、CycloneDX SBOM、许可证清单
   - 变更范围、排除项、回滚版本和数据库备份引用
4. 由发布负责人、技术负责人、运维/安全负责人分别核对签字单；独立保存被批准 manifest 的 SHA-256。

**验收标准**

- `deploy/preflight.py` 对候选目录不返回 failed；除明确人工签署状态外，不存在未解释 pending。
- `GOOGLE_MANAGER_IMAGE` 使用真实 registry digest，不使用 `:latest`、临时 tag 或 synthetic registry。
- Release bundle 的 `SHA256SUMS`、manifest、扫描和 SBOM 相互一致。
- 回滚镜像、旧 schema 兼容性、原业务密钥引用和备份文件均已登记但不把密钥明文写入文档。

### B04：完成目标数据库迁移、备份、恢复和 RPO/RTO 演练

**问题与依据**

- 合成恢复脚本明确输出 `production_rpo_rto_validated=false`，见 `deploy/recovery_drill.py:99`。
- 备份 CLI 入口和原子恢复逻辑见 `deploy/backup_database.py`。
- 目标环境验收要求见 `docs/deployment-preparation.md:76,93-94`。

**执行步骤**

1. 从目标数据库制作只读/隔离结构副本，禁止直接在生产活库上试迁移。
2. 先执行备份并验证认证、完整性、大小、SHA-256、权限和备份保留策略。
3. 在副本上执行 `init-db` 和全部版本化迁移，记录每一步耗时、锁等待和失败回滚结果。
4. 验证邮箱规范化、唯一约束、敏感字段加密、旧 `pending/unknown` 记录保留和跨版本密钥读取。
5. 恢复到全新路径，执行 `PRAGMA integrity_check`、schema 校验和关键业务抽样。
6. 模拟回滚：保留故障副本，使用旧镜像、旧 schema 兼容版本和原业务密钥恢复，不覆盖原数据库。
7. 记录实际 RPO/RTO、备份时间、恢复时间、丢失窗口、异地副本位置、密钥恢复流程和审批人。

**验收标准**

- 目标数据库副本迁移成功，生产源库未被修改。
- 备份和恢复均能验证完整性，错误 key/篡改/覆盖目标被拒绝。
- RPO/RTO 达到经批准目标，并有可复核时间戳。
- 回滚不删除或伪造充值 unknown 记录，不自动重放第三方副作用。
- 本轮新增操作结案 `rejected` 状态；旧镜像不能继续处理该状态，真实使用结案后的回滚必须恢复匹配的升级前备份并核对上游副作用。

### B05：完成真实 Google OAuth、Gmail 和 Pub/Sub 验收

**问题与依据**

- 当前仅完成本地 fixture、合成 credentials 和回归测试。
- 真实外部集成验收要求见 `docs/server-deployment-guide.md:379`、`docs/release-readiness-review-2026-09-20.md:29,105`。

**执行步骤**

1. 由业务方提供专用、可撤销、非生产数据的 Google 测试账号和授权窗口。
2. 使用目标域名和 TLS 完成 OAuth 授权回调，验证 state 单次消费、权限范围、Token 加密存储和 refresh。
3. 验证 Gmail profile、列表、读取、归档、API 超时、Token 失效和重试后的状态一致性。
4. 如启用 Pub/Sub，验证 topic、订阅、verification token、重复通知、延迟通知和 worker 恢复；如不启用，明确记录本次发布排除项。
5. 测试账号注销、权限撤销、浏览器任务取消和服务重启后的清理行为。

**验收标准**

- 所有流程使用授权测试账号完成并有时间戳、请求 correlation ID 和脱敏日志。
- 不在日志、截图、文档和 CI artifact 中保存 OAuth code、access token、refresh token、cookie 或账号密码。
- 失败和恢复路径均有明确结果；不能用本地 mock 结果替代真实外部证据。

### B06：完成充值上游 sandbox、鉴权、幂等和 unknown 闭环

**问题与依据**

- 生产模板要求默认 `RECHARGE_MODE=disabled`，见 `deploy/env.production.example:13`。
- 真实上游契约、鉴权和 unknown 处理要求见 `docs/server-deployment-guide.md:322,356`、`docs/release-readiness-review-2026-09-20.md:29,105`。

**执行步骤**

1. 由上游方提供版本化 sandbox 契约：鉴权方式、签名范围、幂等键、任务查询、撤回/关闭、错误码、限额和 KYC URL 处理。
2. 通过受控 secret 注入完成单笔创建、重复提交、网络超时、响应丢失、上游查无任务、任务延迟终态和取消/关闭验证。
3. 验证同一意图不会重复履约，本地账单 mutation 与上游 task number 绑定，unknown 进入人工对账而非盲目重试。
4. 验证人工对账权限、审计记录、证据指纹、CAS/租约冲突和最终关闭条件。
5. 只有 sandbox 证据和负责人签字齐全后，才允许在候选配置中评估 `live`；否则继续保持 `disabled`。

**验收标准**

- 上游鉴权失败会明确失败，不会降级为匿名请求。
- 重复请求、超时未知、查询查无任务和撤回/关闭均有可审计终态。
- 不删除 unknown、不伪造 done、不绕过人工对账门禁。
- sandbox 证据与最终生产上游版本化契约一致；差异必须重新验收。

### B07：完成目标基础设施、TLS、容量、监控和告警验收

**问题与依据**

- 服务器部署和 Nginx 验收步骤见 `docs/server-deployment-guide.md:206,314,379`。
- 目标环境输入和告警要求见 `docs/deployment-preparation.md:91-94`。
- Compose/Web/worker/readiness 入口见 `docker-compose.yml`。

**执行步骤**

1. 记录目标 Linux 发行版、CPU 架构、CPU/内存/磁盘、Docker/Compose 版本、账号量和并发目标。
2. 在隔离维护窗口完成 Nginx、DNS、TLS 证书、代理头和健康路径配置；从公网验证健康路径禁止访问，从本机验证探活。
3. 按 UID、owner、mode 验证 `.env`、OAuth 文件、`instance/`、`googlemail/runtime/` 和 `googlemail/output/`。
4. 启动顺序固定为 `initialize` → Web → worker，执行 `/health/ready`、worker check、monitor 和日志检查。
5. 执行容量基线：登录、账户列表、任务查询、Gmail 受控 API、队列积压、磁盘增长、CPU/内存和浏览器并发；记录 p95/p99、错误率和资源上限。
6. 执行 Web/worker 重启、宿主机重启、数据库锁等待、磁盘阈值和维护失败演练。
7. 触发故障告警和恢复告警，确认实际接收人、通知渠道、事件编号和值班交接。

**验收标准**

- 目标域名 TLS、CSP、反向代理和健康路径符合签字单要求。
- readiness、worker、磁盘、队列和维护失败的故障/恢复状态都可观测。
- 容量测试达到预先批准的账号量、并发、响应时间和资源阈值。
- 重启和故障恢复不产生重复 worker、不盲目重放自动化或充值操作。
- 告警由真实接收人收到并确认，不能只证明 timer 或 journal 有输出。

## 5. 依赖关系与执行顺序

### 5.1 关键路径

```text
冻结发布范围与候选提交
        ↓
B01 正式构建/推送镜像
        ↓
B02 目标 Linux sandbox 与容器 smoke
        ↓
B04 目标数据库迁移/备份/恢复
        ↓
B05 Google 外部验收 + B06 充值 sandbox 验收
        ↓
B07 目标基础设施/容量/告警验收
        ↓
B03 生成最终 manifest、签字、回滚包
        ↓
GO / NO-GO 决策
```

### 5.2 并行工作

- B04 数据库演练可与 B02 Linux 镜像验收并行，但必须使用同一个候选 schema/镜像版本。
- B05 Google 验收和 B06 充值 sandbox 验收可并行，但必须使用隔离账号、隔离密钥和同一候选版本。
- B07 的 TLS/Nginx、目录权限、告警接收人可以并行准备，但容量和重启演练必须在目标候选镜像可运行后执行。
- B03 的最终签字必须最后执行，不能用中间版本 manifest 预先签署。

### 5.3 依赖阻断规则

- B01 未完成：不生成可部署正式 manifest；用户授权的隔离测试可用已校验本地制品继续，但不能替代正式 registry 验收。
- B02 未完成：不允许启用任何浏览器自动化生产任务。
- B04 未完成：不允许对生产数据库执行迁移或密钥轮换。
- B05/B06 未完成：Google 相关真实流程和 `RECHARGE_MODE=live` 不得放行。
- B07 未完成：不允许把本机 smoke、日志或 timer 输出当作生产可用性证明。
- 任一 P0/P1 证据缺失：最终签字单保持 `NO-GO`。

## 6. 证据目录和命名约定

正式证据应存入受控制品库；以下是建议的脱敏文件名，不要求把真实密钥或个人数据提交到仓库：

| 证据 | 建议文件名 | 必须包含 |
| --- | --- | --- |
| 构建元数据 | `build-metadata.json` | Git SHA、平台、基础镜像 digest、构建引用 |
| 镜像摘要 | `image-digest.txt` | registry 完整引用和 digest |
| 镜像漏洞 | `image-vulnerabilities.json` | 同一 digest、扫描时间、HIGH/CRITICAL 结果 |
| SBOM | `google-manager-sbom.cdx.json` | 同一 digest、组件和许可证元数据 |
| 发布清单 | `manifest.json` | 文件摘要、范围、平台、镜像、CI、扫描和 signoff 状态 |
| 数据演练 | `database-recovery-drill.json` | 迁移、备份、恢复、RPO/RTO、回滚和 unknown 保护 |
| Google 验收 | `google-integration-acceptance.md` | 测试账号标识、流程、时间、脱敏结果和失败恢复 |
| 上游验收 | 受控制品库中的 `recharge-sandbox-acceptance.md`（待生成） | 契约版本、鉴权、幂等、unknown、撤回和审计结果 |
| 基础设施验收 | 受控制品库中的 `infrastructure-acceptance.md`（待生成） | OS、架构、TLS、权限、容量、重启和告警送达 |
| 最终签字 | `RELEASE-SIGNOFF.md` | 各负责人签署、GO/NO-GO、回滚负责人和维护窗口 |

证据不得包含密码、token、cookie、OAuth code、refresh token、完整卡密、真实用户邮箱或数据库明文。所有文件必须与最终 manifest 的 SHA-256 绑定。

## 7. 上线执行与回滚控制

### 上线前

- 冻结候选 commit、镜像 digest、数据库 schema 和配置版本。
- 完成生产数据库备份并独立 verify；确认恢复密钥可由授权人员取用。
- 确认 `RECHARGE_MODE=disabled`，除非 B06 已签字允许其它模式。
- 确认旧镜像、旧配置、旧业务密钥引用和回滚数据库备份均可用。
- 确认维护窗口、执行人、观察人、回滚负责人、告警接收人和中止阈值。

### 上线中

- 先停止重复运行的旧 worker，保留旧数据库和日志，不直接删除锁文件。
- 按 `initialize` → Web → worker 顺序发布，等待 readiness 和 worker check。
- 首先执行只读登录、账户列表、任务查询和健康检查，再逐步放开受控业务流量。
- 出现 readiness 连续失败、数据库迁移异常、队列积压超阈值、unknown 增长、错误率超阈值或告警未送达时立即中止。

### 回滚

- 停止入口写入和 Web/worker，保留故障数据库、日志和容器状态。
- 在隔离路径验证旧镜像、旧 schema、旧密钥和备份兼容性。
- 恢复旧版本前先核对 Google/上游副作用；回滚数据库不会撤销已发生的外部操作。
- 恢复后重新执行 readiness、worker、数据一致性、告警和关键只读接口检查。
- 所有充值 `pending/unknown` 必须经过对账，不得因回滚而删除或盲目重放。

## 8. 最终放行门槛

只有以下条件全部满足，项目才可以从“不可上线”转为“有条件上线”或“可以上线”：

- [ ] B01 正式镜像构建、推送和 digest 绑定完成。
- [ ] B02 正式 digest 在目标 Linux 的有限 seccomp profile 下 sandbox 验收通过（测试镜像已通过）。
- [ ] B03 干净候选、远端 CI、manifest、人工 GO 和回滚制品完成。
- [ ] B04 目标数据库副本迁移、备份、恢复、RPO/RTO 和回滚通过。
- [ ] B05 真实 Google OAuth/Gmail/Pub/Sub 验收通过或明确排除并签字。
- [ ] B06 充值 sandbox 契约和 unknown 闭环通过；未通过时保持 disabled。
- [ ] B07 目标 TLS/Nginx、权限、容量、重启、监控和告警送达通过。
- [ ] 所有证据与最终 manifest SHA-256 一致。
- [ ] 发布负责人、技术负责人、运维/安全负责人完成独立签署。

任一复核结果为 failed、未解释 pending、证据与候选 digest 不一致或无法复现时，自动保持 `NO-GO`。

## 9. 执行记录与待办模板

| 日期/时间 | 阻断项 | 执行人 | 环境/候选版本 | 命令或操作 | 结果 | 证据文件 | 复核人 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-09-24 | B07/Compose 权限 | Codex | `123.206.210.86` / `google-manager:online-test` | `deploy/prepare-compose-host.sh` 等价权限修复；init-db、readiness、worker、monitor、重启和 API smoke | 目标隔离环境通过；正式 TLS/告警仍待验收 | 本次服务器命令输出；不得提交密钥/cookie | 待复核 |
| 2026-09-24 | B02 | Codex | `123.206.210.86` / `google-manager:online-test` | `docker run --rm --init --network none ... startup-check.mjs` | 失败：`No usable sandbox`，保持 NO-GO | 浏览器启动日志；不得提交完整原始日志中的路径外敏感数据 | 待复核 |
| 2026-09-24 | B01 | Codex | 目标服务器 | Docker Hub IPv4/IPv6 registry 访问、离线镜像导入 | 远端联网构建未闭环；本机镜像导入仅供隔离测试 | 镜像 ID 与传输校验记录 | 待复核 |
| 待填写 | B01 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |
| 待填写 | B02 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |
| 待填写 | B03 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |
| 待填写 | B04 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |
| 待填写 | B05 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |
| 待填写 | B06 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |
| 待填写 | B07 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |

## 10. 关联文档

新增 CDK 首期实现已落盘并更新到测试服务器，完成目标 Linux 回归、现有库迁移、备份恢复、工作台公网登录及权限隔离；具体证据见 [CDK 服务器部署记录](cdk-server-deployment-2026-09-25.md)。真实充值和邮件领取仍关闭，未以合成测试替代真实供应商验收。配置、迁移回滚及剩余功能边界见 [CDK 实现与操作手册](cdk-implementation-and-operations-2026-09-25.md)。原 1–7 项正式放行结论不因此自动解除。

- [2026-09-25 功能整改与服务器验收记录](server-validation-2026-09-25.md)
- [正式上线人工配置与验收操作手册](production-manual-configuration.md)
- [生产发布准入审查](release-readiness-review-2026-09-20.md)
- [P0/P1 整改与验收记录](release-remediation-2026-09-20.md)
- [部署前准备](deployment-preparation.md)
- [部署技术说明](deployment-technical-guide.md)
- [服务器生产部署指南](server-deployment-guide.md)
- [CDK 工作台发布签字单](release-signoff-template.md)
- [浏览器安全基线](browser-security-baseline-2026-09-20.md)
