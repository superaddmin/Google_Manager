# 生产发布准入审查（2026-09-20）

> 本文为整改前初审快照。项目负责人随后确认“本次只发布 CDK 工作台，现金支付另行排期”，B01 范围阻塞已关闭；后续代码修复、重新验证和未关闭项以[整改与验收记录](release-remediation-2026-09-20.md)为准，以下旧测试和版本证据保留用于追溯。

## 1. 总体结论：暂缓上线

本次针对当前工作区重新审查并执行验证，不直接沿用历史报告的“通过”结论。**本地回归可通过，但当前证据不足以批准整个项目生产发布。** 可以继续在隔离预发布环境验收；这不等于“有条件生产上线”。未确认的项目不按通过处理。

主要依据：管理员退出后旧 Cookie 仍可重放；部署模板使用已结束维护的 Node.js 20；待发布修改尚未形成固定版本；第三方业务、目标 Linux 运行、容量、数据库升级恢复和告警闭环缺少验收证据；Googlemail 子项目实测覆盖率偏低。若发布仍包含原“金额充值—支付—余额”要求，当前缺失支付域实现属于功能阻断；若仅发布 CDK 工作台，应先由产品负责人书面收敛范围。

本次未修改业务代码、依赖或生产配置，未使用真实凭证执行第三方业务，也未执行部署。重建的前端制品与审查前 SHA-256 一致。新增此报告和本地覆盖率产物用于复核。

## 2. 审查基线与判定规则

- Git HEAD：`30d1e8a`；工作区存在大量修改、删除和未跟踪文件，包括模型、worker、测试及静态资源。此提交不能单独代表当前候选版本。
- 实测环境：Windows/PowerShell，Python `3.14.7`，Node `22.23.2`，npm `10.9.8`。Docker daemon 可访问，版本 `29.6.1`，本地未列出 `google-manager` 镜像；本次未构建或运行该 Linux 镜像。
- 部署环境声明：Docker Python `3.11`、Node `20.x`；与本地测试环境不同。目标操作系统、规格、域名、网络策略、容量目标及值班人未提供。
- **通过**：限定范围内有本次可复核证据；不自动延伸到外部服务。
- **存在风险**：已有实现但仍有缺陷、缺口或残余风险，需要整改或书面接受。
- **不通过**：已确认不满足本次发布要求，不能仅凭说明放行。
- **待确认**：未取得必要证据；发布关键项待确认同样阻断放行，不表示已发现故障。

## 3. 逐项检查矩阵

| 检查项 | 结论 | 可验证证据与限制 | 放行所需证据 / 整改 |
| --- | --- | --- | --- |
| 功能完整性、验收范围 | 不通过（现金充值）；CDK 发布范围待确认 | `app/services/recharge_service.py:415` 创建 CDK 任务；`app/models/recharge_task.py` 没有金额/币种/支付订单/余额账本；`app/routes/recharge.py:337`、`:467` 的 live 发票下载返回 503 | 签署本次功能清单、排除项和业务验收用例；若包含现金充值，须另行实现支付域并验收 |
| CDK 核心状态机与权限控制（隔离测试） | 通过（限定测试范围） | challenge 单次消费、任务归属、账单租约/CAS、超时和终态保护回归通过；见 `tests/test_recharge*.py`、`tests/test_billing_idempotency.py` | 真实上游幂等、鉴权、订单状态语义仍不能由 mock/loopback 证明 |
| 第三方集成验收 | 待确认，live 发布阻断 | `app/services/recharge_service.py:1394` 的适配层未配置服务间 API key/HMAC/mTLS；只发送 JSON 和 User-Agent；真实 OAuth/PubSub/Google 自动化未执行 | 上游版本化协议、凭据注入设计、授权测试账号、超时/重放/撤销/额度故障记录；在完成前保持 disabled |
| 充值未知结果与人工处理 | 不通过（live 异常闭环） | `app/services/recharge_service.py:423` 拒绝同卡 unknown 后的新提交；`:476` 创建超时进入 unknown；`:857` 查询只有获得可关联上游任务才能恢复；`app/routes/recharge.py` 无管理员 resolve/resubmit 接口 | 提供经上游确认的对账、人工处理和审计流程；覆盖长期超时及上游明确查无任务，不得用删记录直接解锁 |
| 代码质量与静态门禁 | 存在风险 | `git diff --check` 通过；`frontend/package.json` 无 lint/typecheck；未取得 Python 静态检查门禁；回归有 SQLite ResourceWarning 和 `utcnow()` 弃用警告 | 固定 lint/静态分析与关键模块规则；定位资源警告；人工评审不能替代持续门禁 |
| 自动化测试通过情况 | 通过（本地） | Python 213、根 Node 41、Googlemail 47、UI 43，共 344 项通过，详见第 4 节 | 在发布 Linux 工具链上复跑；UI API 为 fixture，不能称真实端到端验收 |
| 测试覆盖充分性 | 存在风险；全项目覆盖待确认 | Googlemail 行 17.03%、分支 21.24%；`oauth-authorizer.mjs`、`batch-oauth-worker.mjs` 在该 Vitest 报告中 0%；`googlemail/vitest.config.mjs:8` 无阈值 | 覆盖 OAuth/自动化成功、失败、取消、超时、重启、部分成功；补 Python/前端覆盖报告和风险用例映射，不把子项目比例当全项目比例 |
| 前端构建和产物一致性 | 通过（当前本地快照） | Vite 生产构建成功；4 个产物 SHA-256 重建前后一致；正式构建上的 CSP/UI 用例通过 | 干净检出、固定工具链构建及镜像内 smoke；当前一致性不证明跨机器完全可复现 |
| 性能与容量 | 待确认，发布阻断 | `app/worker.py:213` 仅 3 线程，automation/gmail 各一个活动任务；默认 SQLite；未发现 k6/Locust 等压测报告或 SLO | 给出用户数、账号数、峰值流量、队列等待、p95/p99、错误率和资源预算；做持续压测、上游限流与磁盘耗尽演练 |
| 安全基础控制 | 通过（已覆盖控制）；整体安全存在风险 | 生产密钥校验、Secure/HttpOnly/SameSite Cookie、Origin 防护、限流、CSP、一次性 challenge、任务所有权已测；见 `app/__init__.py`、`app/services/request_security.py`、权限测试 | 仍需安全扫描、会话生命周期验证、目标环境访问边界与敏感数据控制验收 |
| 管理员会话吊销 | 不通过 | `app/routes/api.py:411` 只检查 authenticated；`:907`、`:963` 退出仅清当前客户端会话；`app/config.py:17` 默认 7 天签名会话。隔离复现退出后当前客户端 401、旧 Cookie 在另一客户端 200 | 服务端会话或会话版本吊销；补退出、密码轮换及应急吊销的旧 Cookie 回归测试，见第 4.1 节 |
| 登录输入与异常响应 | 存在风险，发布前修复 | `app/services/auth_service.py:50` 对 str 直接 compare_digest；`app/__init__.py:99` 允许 Unicode 密码。非 ASCII 输入实测触发 TypeError/HTML 500，未进入失败计数 | UTF-8 bytes 恒时比较或明确输入约束；合法密码可登录、错误输入返回规范 JSON 4xx 并计数；不能只依靠配置 ASCII 密码规避 |
| 敏感数据及密钥 | 存在风险，真实凭证发布须关闭风险 | `app/models/account.py:34` 密码/TOTP 和 `account_history.py:16` 历史值仍明文落库；充值卡密/邮箱同样明文；队列/Gmail Token 使用 Fernet | 字段加密或经批准的隔离/ACL/备份加密补偿控制；形成存量迁移、密钥保管与轮换演练记录 |
| KYC URL 边界 | 待确认 | `app/services/recharge_service.py:356` 仅限制 HTTP/HTTPS；仓库未证明上游是否抓取 URL，不能直接认定本地已存在 SSRF | 明确上游抓取行为；如抓取，补域名/IP/DNS/重定向和出口控制；完成前不启用该能力 |
| 匿名订阅变更授权模型 | 待确认 | `app/services/recharge_service.py:1229` 的查询/取消/恢复以 token_input 作为授权凭据，变更额外要求布尔确认；已有摘要与 IP 限流，但未绑定客户身份 | 产品/安全确认“持有凭据即可操作”是否满足业务授权模型；若要求账户归属，应补会话绑定或一次性授权，再验收 live |
| 环境变量和生产配置 | 待确认，发布阻断（代码校验/合成解析通过） | 合成变量、`--env-file NUL` 下 Compose config 退出 0；生产禁 mock、弱密钥和不合规上游地址 | 目标环境脱敏配置清单、文件权限、同实例密钥一致性、代理/TLS/OAuth 地址验证；没有读取真配置即不能判部署配置正确 |
| 运行时与依赖安全 | 不通过（Node 20 生命周期）；其他存在风险/待确认 | `Dockerfile:46`、`deploy/setup-server.sh` 安装 Node 20；npm 全量 frontend 6、googlemail 4 项告警；生产依赖过滤均 0，但镜像保留前端开发依赖，不能据此判镜像安全；`pip check` 只验证依赖一致性 | 升级受支持运行时并回归；评估并修复依赖告警；补 Python 及 OS/Chromium/最终镜像扫描和漏洞例外记录 |
| 镜像供应链与最小化 | 存在风险 | `Dockerfile:69` 同阶段安装 frontend 全部依赖后未裁剪；`FROM` 未 pin digest；Python 仅直接依赖固定、没有完整哈希锁；Compose 使用 latest | 多阶段构建/移除构建依赖；固定可追溯镜像和依赖锁，生成 SBOM/扫描报告；本次未证明漏洞可由公网访问 |
| 许可证 | 待确认 | 根 `LICENSE` 为 MIT；npm 非 dev 锁条目：frontend 6（MIT/ISC），googlemail 12（MIT/Apache-2.0），无缺失元数据；未发现完整 SBOM/第三方 NOTICE | 核对 Python、系统包、Chromium、字体、vendored 源码来源与实际分发义务；metadata 不是法律合规结论 |
| 数据迁移 | 不通过（已有 billing 表缺列的升级路径）；目标库待确认 | `app/manage.py:26` create_all；只显式补 Gmail lease 列。已有中间版本 billing 表不会自动新增 lease_token；首次建库不属于该缺陷触发条件 | 生产结构快照、预发布副本升级/回滚记录；保留未决账单，不通过删表迁移；首次建库与存量升级分别验收 |
| 数据备份与恢复 | 待确认，发布阻断（备份工具单测通过） | `tests/test_backup.py` 验证合成 SQLite 备份读取/完整性和防覆盖；`deploy/backup_database.py` 只备份单个 SQLite 库 | 自动备份排程、异地/加密副本、保留期、恢复权限与实测耗时；同时保管密钥/配置，明确 RPO/RTO |
| 基础设施与可用性 | 待确认，发布阻断 | Compose/systemd/Nginx 模板存在、非 root、loopback 端口；`worker.lock` 为本地锁；域名/证书仍是模板 | Linux 镜像 build/run、用户权限、网络、证书续期、重启恢复和单 worker 验证；不能假定多机 HA |
| 外部数据库兼容性 | 待确认（启用时阻断） | `DATABASE_URL` 可配置，但 `requirements.txt` 未包含 PostgreSQL/MySQL 常见驱动；备份脚本只支持 SQLite，未取得外库测试记录 | 此次明确限定 SQLite，或先添加/锁定目标驱动并完成迁移、并发、备份恢复验收；不能认为修改 URI 即可切库 |
| CI/CD、版本与发布审批 | 不通过（仓库交付门禁） | 未发现受版本管理的 GitHub/GitLab/Jenkins/Azure pipeline；大量未提交/未跟踪文件；无本次签名镜像或发布候选标签证据 | 提交完整候选版本，建立自动检查/构建/扫描及受保护发布，或提供等效可审计发布流水线；外部 CI 是否存在待确认 |
| 发布步骤与变更控制 | 待确认，发布阻断 | `docs/deployment-technical-guide.md` 已描述初始化、服务启停、验收和回滚顺序；未取得本次目标环境执行记录 | 明确执行人/审批人、维护窗口、放行与终止阈值、发布观察时长；演练完整步骤并留档，纳入 B04/B07 验收 |
| 监控告警 | 存在风险；告警闭环待确认，发布阻断 | `/health/ready` 与 worker 心跳已实现；未发现 metrics/告警规则/通知渠道配置；Compose unhealthy 本身不触发 restart 策略 | 外部探活、队列/unknown 积压、5xx、资源/磁盘告警；值班责任、阈值和真实告警送达测试 |
| 日志与可观测性 | 存在风险 | Gunicorn/Nginx 模板移除 query 日志，Compose 20MB×5 轮转；有业务记录；未发现统一 request/correlation ID、集中检索或不可变管理审计 | 关联请求/任务/上游操作号、脱敏检查、保留策略和集中查询；确认代理和自动化产物不会额外暴露凭据 |
| 容灾与回滚 | 存在风险；演练待确认，发布阻断 | 文档给停服务/备份/回滚步骤，worker 恢复时不盲目重放自动化；无异地切换和目标恢复演练证据 | 选定 RPO/RTO，演练代码+schema+密钥恢复及上游结果对账；恢复数据库不撤销第三方副作用 |
| 文档完整性 | 存在风险 | 部署技术说明、安装指南、缺陷清单已存在并覆盖主要操作；`docs/deployment-technical-guide.md:195` 仍指向旧审查；旧报告仍把 dev 依赖告警与最终镜像混为一谈，并保留历史环境受限表述 | 发布前按本报告修订 Node 版本/镜像依赖说明及审查入口，补实例化参数、负责人、演练记录和签署验收表 |

## 4. 本次实测证据

以下结果均来自本轮命令执行，不是从 README 抄录。测试在临时数据库/合成凭证/隔离上游上运行；未使用真实账号执行自动化。

| 命令 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'` | 213 tests，23.340 秒，退出 0；有既有资源/弃用警告；异常分支测试会产生预期 ERROR 日志 |
| `node --test tests/account-import.test.mjs tests/googlemail-local-copy.test.mjs tests/api-service.test.mjs` | 41 tests，退出 0 |
| `npm --prefix googlemail test` | 6 files / 47 tests，退出 0 |
| `npm --prefix frontend run build` | Vite 4.5.14，1267 模块，生产构建成功 |
| `node --test tests/frontend-ui.test.mjs` | 43 tests，61.419 秒，退出 0；CSP 下 Chromium 页面回归，API 为 fixture |
| `npm --prefix googlemail run test:coverage` | 原 47 项再次通过，不重复计入 344；lines 193/1133=17.03%，branches 116/546=21.24% |
| `npm --prefix frontend audit --json` | audit 退出 1：6 个受影响包（4 high / 2 moderate），不是 6 个已证实可利用漏洞 |
| `npm --prefix googlemail audit --json` | audit 退出 1：4 个受影响包（1 high / 3 moderate） |
| 两项目 `npm audit --omit=dev --json` | 均退出 0、0 项告警；只覆盖过滤后的 npm 集合 |
| `.\.venv\Scripts\python.exe -m pip check` | 退出 0；不等同 CVE 扫描 |
| `.\.venv\Scripts\python.exe -m pip show pip-audit coverage` | 两者未安装；Python CVE/全项目覆盖率不据此判通过 |
| `docker compose --env-file NUL config --quiet` | 使用合成必填环境变量，退出 0；仅配置解析，不是镜像或容器运行验收 |
| `git -c core.autocrlf=false -c core.whitespace=cr-at-eol diff --check` | 退出 0 |

覆盖率文件在 `googlemail/coverage/coverage-summary.json`（已被 Git 忽略），可用上述命令重新生成。该报告没有把 Python 对 Node 进程的 mock 测试映射成 JavaScript 覆盖率，也未把 UI 用例纳入全项目 coverage，因此不应把 17.03% 写成“整个项目覆盖率”。

产物 SHA-256（重建前后相同）：

| 文件 | SHA-256 |
| --- | --- |
| `static/index.html` | `9985CCB76F77189B3DB5EE07B7BED6FC3A4928B913261E434F3C1C7AF15CA7BC` |
| `static/assets/index-00db4e8a.js` | `F8478D30BBC1A25C91917F08D1131F46AEDDFFBF69F0DBA1642CB146D70DF919` |
| `static/assets/AdminApp-35862d0a.js` | `28E7CDFC8EF946AAEE2D03144B6A99EA66F6E81F2F578D484C8C19589367444C` |
| `static/assets/index-494a80da.css` | `494A80DAF5D7EEE468C2009B62919A9ADC2950E602953DAE194B20D07F0006A2` |

### 4.1 新确认的安全缺陷及复现条件

以下验证仅使用 `create_app('testing')`、内存 SQLite、虚构凭据和 Flask test_client；不读取真实 Cookie，不连接第三方业务。它们是额外的一次性诊断，不计入上述 344 项已有测试。

| 缺陷 | 最小复现步骤及实际结果 | 影响与修复验收 |
| --- | --- | --- |
| 退出不吊销旧会话 | 使用 testing 虚构密码 `admin123` 和 `AuthService.generate_salt(int(time.time()))`，带 X-Requested-With 请求头 POST `/api/auth/login` 为 200，保存响应 Cookie 到内存；GET `/api/stats` 为 200；POST `/api/auth/logout` 为 200；原客户端再 GET 为 401；另一客户端注入退出前的 Cookie 后 GET 为 200。此结果使用真实登录入口，未通过直接设置 authenticated 构造会话 | 前提是攻击者已经持有有效 Cookie，不代表可无凭据登录。泄露会话无法通过退出吊销，仍能使用管理权限。补服务端会话 ID/版本及吊销校验，回归要求重放返回 401。密码轮换后旧会话可能继续有效是代码推断，尚未单独动态验证 |
| Unicode 密码触发 500 | 设置虚构 ADMIN_PASSWORD 为 `管理员强密码_满足十六字符_2026`，关闭 testing 异常传播；以 `AuthService.generate_salt(int(time.time()))` 生成盐，带 X-Requested-With 请求头 POST `/api/auth/login`；password 分别为 `错误密码🔒` 和相同配置值，两次均返回 `500 text/html; charset=utf-8` | `hmac.compare_digest` 抛出 `comparing strings with non-ASCII characters is not supported`。合法非 ASCII 管理密码无法登录，错误输入在失败次数记录前异常，形成匿名 500/日志放大；未发现因此绕过身份认证。验证 ASCII/Unicode 正确与错误密码、非字符串、超长输入，以及统一 JSON 错误与限流 |

## 5. 阻塞项、建议负责人及完成时限

严重度用于本次发布决策：P0 是范围/关键能力不满足；P1 是发布前必须解决的安全、可靠性或证据缺口；P2 通常可在具备补偿控制、责任人和书面接受后排期，但明确列为发布门禁的 P2 也必须关闭。下列责任人是角色建议，未实际指派；D0 为评审结论确认日，时限是建议而非承诺，所有适用 P0/P1 和 B10 关闭前不得发布。

| ID / 严重度 | 阻塞问题 | 整改及关闭证据 | 建议负责人 | 建议时限 |
| --- | --- | --- | --- | --- |
| B01 / P0（范围条件） | 原现金充值/支付/余额验收链路缺失 | 1 个工作日内确定范围；缩减为 CDK 时明确功能关闭和宣传边界，否则提交支付域设计、实施排期并完整验收 | 产品负责人 + 技术负责人 | D0+1 工作日确定范围；实现工期另评估，完成前不发布该业务 |
| B02 / P1 | Node 20 已 EOL，测试与目标运行时不一致 | 选择受支持 LTS，更新工具链并在实际 Linux 镜像完成构建、全量回归和 Chromium smoke；记录版本/digest | 平台/DevOps + Node 维护人 | D0+2 工作日 |
| B03 / P1（启用 live 时） | 第三方鉴权/幂等/状态与凭据边界未验收，unknown 无人工闭环 | 上游协议和授权 sandbox 证据，覆盖未知结果、重复请求、撤销、KYC 与匿名订阅授权；提供受审计的 unknown 人工对账处理，明确查无任务时也可安全闭环；无法完成则保持 disabled 并从发布范围剔除 | 后端负责人 + 上游接口负责人 + QA + 运营 | D0+1 工作日确认协议与负责人；D0+3 工作日完成首轮联调，依赖外部响应 |
| B04 / P1 | 发布版本与流水线门禁未形成 | 干净候选 tag、完整新增文件、Linux 自动回归/依赖扫描/构建记录及镜像 digest；保留审批与回滚版本 | 技术负责人 + DevOps | D0+2 工作日 |
| B05 / P1 | 关键自动化路径覆盖和真实验收不足 | 给出风险用例映射，新增流程/故障测试；获得授权账号上的 OAuth/Googlemail/PubSub 验收记录 | QA + 自动化模块负责人 | D0+3 工作日首轮，未通过路径禁用或延后 |
| B06 / P1 | 生产数据迁移/备份恢复缺证据 | 用目标结构副本演练升级、备份和恢复，记录完整性、耗时、RPO/RTO及未决账单保护 | 后端/DB 负责人 + 运维 | D0+2 工作日 |
| B07 / P1 | 目标基础设施、性能容量和告警闭环未验收 | Linux 三服务/TLS/权限/重启验证；签署容量目标，压测达标；真实告警送达和值班交接 | SRE/运维 + QA + 项目负责人 | D0+1 工作日定指标；D0+3 工作日交演练报告 |
| B08 / P1 | 真实凭证与供应链风险未形成处置结论 | 明文敏感字段保护方案与迁移/补偿控制批准；Python/最终镜像扫描或证明受影响包已从镜像移除；依赖告警逐项修复或风险接受；第三方许可证清单 | 安全负责人 + 后端 + DevOps | D0+2 工作日给处置清单，发布前完成验证 |
| B09 / P1 | 管理员退出后旧 Cookie 仍可重放 | 实现服务端会话吊销；覆盖退出后重放、密码轮换及应急吊销；旧 Cookie 访问受保护接口必须返回 401，不依赖客户端自行删除 | 后端负责人 + 安全 | D0+1 工作日 |
| B10 / P2（发布门禁） | Unicode 登录请求稳定触发 500 | 修复密码比较和输入处理，保持恒时比较；补正确/错误 Unicode、类型边界、JSON 错误及失败计数回归，避免管理员不可登录和匿名错误放大 | 后端负责人 + QA | D0+1 工作日 |

## 6. 可排期的非阻塞风险

以下仅在不影响 B01–B10 适用项关闭、且有风险接受记录时可不阻塞受限范围发布，不表示现在已经接受：

| 风险 | 建议措施 | 建议负责人 / 时限 |
| --- | --- | --- |
| 构建/测试依赖告警，生产 API 可利用性尚未证实 | 禁止暴露 Vite/Vitest 服务、隔离构建；升级并从最终镜像移除构建依赖；高危可达路径发现后升级为阻塞 | 前端/DevOps，D0+5 工作日；发布前完成可达性判断 |
| SQLite 单机、单任务 worker，无横向容灾 | 明确单机支持范围和降级策略；容量与恢复指标先达标，再规划数据库/调度扩展 | 架构/SRE，D0+5 工作日形成路线图 |
| 客户任务授权依赖浏览器会话，丢失后需管理员核验 | 建立工单核验 SOP，界面说明授权边界；后续规划客户认证恢复 | 产品/客服/后端，发布前 SOP，D0+5 工作日补流程 |
| Fernet 同时承担加密和账单身份根密钥职责 | 保持密钥稳定并备份；设计版本化 key ring 和迁移，避免无迁移轮换 | 安全/后端，D0+10 工作日设计 |
| 无统一请求关联 ID、完整管理操作审计 | 先保证日志可检索和告警可定位；后续统一请求/任务/上游操作关联及审计留存 | 后端/SRE，D0+5 工作日 |
| 资源/弃用警告，前端组件过大、缺静态门禁 | 定位测试连接释放、UTC 时间兼容；渐进补静态规则和模块边界，避免无关大重构 | 模块负责人，D0+5 工作日 |

## 7. 上线前必须完成的行动清单

- [ ] 产品、技术、QA 签署发布范围及验收标准；现金充值未实现不得按已支持发布。
- [ ] 固定完整候选版本、依赖、受支持运行时和镜像 digest；通过目标 Linux 回归与扫描。
- [ ] 对启用的 Google/充值集成完成授权验收及 unknown 人工对账闭环；未验收功能明确关闭。
- [ ] 修复管理员旧 Cookie 无法吊销及 Unicode 登录 500，完成回归；复核权限、敏感字段和漏洞处置，风险接受须有责任人和期限。
- [ ] 用目标数据库副本执行升级、备份、恢复及回滚；记录 RPO/RTO 和上游副作用对账结果。
- [ ] 完成容量、故障和重启演练，证明单 worker/SQLite 方案满足已签署指标。
- [ ] 提供实际域名 TLS/代理/配置/权限证据，验证前端静态资源及 CSP。
- [ ] 完成监控告警送达、日志脱敏和值班交接；约定回滚触发条件和发布观察窗口。
- [ ] 汇总测试、扫描、SBOM/许可证、部署和演练证据，由项目负责人重新作出放行决定。

“有条件上线”的重新评审前提是：范围已明确、所有适用 P0/P1 及 B10 已关闭，剩余仅为有补偿控制及书面接受的 P2。目前不满足该前提。

## 8. 来源与复核入口

- [Node.js 官方发布与维护计划](https://github.com/nodejs/Release)：本次查询显示 Node 20 于 2026-04-30 EOL；Node 22/24 仍为受支持 LTS。更新版本须结合项目兼容性验证。
- [Vite 官方安全公告示例](https://github.com/vitejs/vite/security/advisories/GHSA-fx2h-pf6j-xcff)：说明 Windows 开发服务器文件访问边界风险；不能据此推断 Flask 静态站点具有同一攻击入口。
- npm 告警以本轮 `npm audit --json` 为准：frontend 涉及 baseline-browser-mapping、browserslist、esbuild、nanoid、postcss、vite；googlemail 涉及 coverage-v8、mocker、nanoid、vitest。数据库会更新，发布当天应重扫，不能仅凭固定计数放行。
- [部署技术说明](deployment-technical-guide.md)、[服务器安装指南](server-deployment-guide.md)、[上一轮缺陷修复清单](project-fixes-2026-09-20.md)。本报告是新增的发布准入判断，不将历史文档中的推断当成实测证据。
