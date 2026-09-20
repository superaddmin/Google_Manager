# P0/P1 整改与验收记录（2026-09-20）

## 发布范围与当前结论

项目负责人已明确：**本次只发布 CDK 工作台，现金支付另行排期**。因此金额输入、支付渠道、现金订单/回调和余额账本不属于本次验收范围；不能对外宣称已支持这些能力。原 B01 范围阻塞已关闭。

本文件记录初审后的实际代码整改和复核；[初审报告](release-readiness-review-2026-09-20.md)保留当时证据，不应把其中旧版本、漏洞数量和测试计数当作整改后的结果。当前仍为**暂缓生产上线**：镜像安全扫描确定不通过，且真实上游契约、目标服务器、恢复、容量和告警验收证据尚缺。下列“代码通过”只表示本地证据，不代表整项生产放行。

## 指派与验收状态

| 条目 | 执行责任 | 整改成果 | 验收状态 |
| --- | --- | --- | --- |
| B01 / P0 范围 | 主代理 + 项目负责人 | 确认只发布 CDK，现金支付另排期；同步部署文档 | 已关闭 |
| B09 / P1 管理员会话 | `p1_admin_session` | DB 持久会话、令牌摘要、退出吊销、密码轮换/过期失效、跨实例验证、异常 JSON 503 | 定向测试和独立代码审查通过 |
| B10 / P2 同路径发布门禁 | `p1_admin_session` | UTF-8 bytes 恒时比较；输入及生产配置限制；Unicode、异常类型、长度回归 | 定向测试和独立代码审查通过 |
| B03 / P1 CDK 异常闭环 | `p1_recharge_reconciliation`、主代理 | 管理员人工对账、审计记录、CAS、幂等重放；上游任务号/证据指纹唯一绑定；自动回写并发冲突受控；人工/自动编号规范化一致；首次审计查询后的重放竞态受控 | 对账 17 项、编号边界 3 项及隔离 HTTP 契约 1 项通过；两项并发回归各重复 10 次通过；人工证据仍是受信管理员录入，不是服务端验签；上游真实协议仍待确认 |
| B06 / P1 迁移与恢复 | `p1_data_recovery`、`final_migration_acceptance`、主代理 | 完整初始化锁、结构列/全表唯一键校验、拒绝非规范上游号、旧 pending 保留、显式存量加密、独立 key 加密备份/恢复及原子无覆盖发布 | 备份/迁移 22 项及约束/存量语义回归 5 项通过；约束测试在资源告警严格模式通过；目标库副本、密钥保管、RPO/RTO 和恢复演练仍待确认 |
| B08 / P1 数据与供应链 | `p1_data_recovery`、`p1_runtime_delivery`、`image_vulnerability_remediation`、主代理 | 账号/历史及 CDK 敏感字段静态保护；Python/npm 修补；镜像 OS 安全更新；移除运行时无用打包工具和前端开发依赖 | 静态保护/声明依赖检查通过；镜像 HIGH/CRITICAL 从 116 降至 84，剩余无 FixedVersion，门禁仍不通过 |
| B02 / P1 运行时 | `p1_runtime_delivery`、`image_vulnerability_remediation` | Node.js 24 LTS、固定基础镜像 digest、多阶段构建 | 镜像运行功能检查通过；安全扫描另行判定不通过，实际目标服务器仍待验收 |
| B04 / P1 发布流程 | `p1_runtime_delivery` | 新增 Linux 回归、覆盖率、依赖/镜像扫描、SBOM 与 smoke 流水线 | 流程代码已落地；远端执行、受保护分支、固定候选提交/镜像待确认 |
| B05 / P1 测试覆盖 | 主代理 + 各模块执行代理 | 新增会话、迁移、备份、人工对账、OAuth/监控异常与并发回归，旧夹具改真实登录 | Python 及 Googlemail/根 Node/UI 的结果见验证证据；真实 Google/充值 sandbox 验收仍缺证据 |
| B07 / P1 运维能力 | 主代理 + `p1_runtime_delivery`、`health_proxy_hardening` | 实际监听器探活、敏感数据就绪检查、积压/磁盘/心跳检查、systemd timer、Nginx 健康路径仅本机、部署顺序更新 | 本地检查通过；实际容量、TLS、告警送达、值班和恢复目标待确认 |

前轮独立审查由 `p1_acceptance_code_review`、`p1_data_security_review`、`p1_database_review` 执行；本轮由 `final_p1_review` 对迁移、竞态、备份、就绪检查及交付配置再验收。审查提出的问题必须修复并补回归后签收，不能用此前全绿测试替代复验。部分执行代理中断后，其已落盘修改由主代理接续核对；中断不等同验收完成。

### 第二轮验收发现与处理

| 严重度 / 复现条件 | 代码依据与影响 | 整改与验收要求 |
| --- | --- | --- |
| P1 / 两个初始化进程同时连接旧 SQLite 库 | `app/manage.py`、`app/services/schema_migration.py` 原先把建表与补列分开执行，可能重复建表或补列，阻断滚动启动 | 将完整初始化置于数据库写锁事务中；以不同连接并发初始化空库/旧库回归，不能只测顺序重复执行 |
| P1 / 两个 unknown 本地任务关联相同远端任务号 | `app/models/recharge_operation.py` 原来只有普通索引；人工及自动回写可能把同一上游结果计入多个本地任务 | 增加唯一约束、业务预检及并发冲突处理；同请求重放幂等，跨任务竞争仅一个成功；存量重复必须中止升级并人工核对 |
| P1 / 同一人工对账请求首次查审计为空后，另一个请求先提交 | `app/services/recharge_service.py::reconcile_unknown_task` 原先随后发现任务不再 unknown 就返回 409，违反完全相同请求幂等重放；全量测试曾实际失败 | 用 Event 固定真实双请求时序，旧实现稳定复现 409；捕获领域冲突后回滚并从新事务读取同任务、同指纹的已提交审计，相同请求返回 replay，不同请求保留 409；定向及各 10 次重复通过、独立审查签收 |
| P1 / 备份或恢复复制尚未完成时观察最终路径 | `deploy/backup_database.py` 原先先创建最终文件，旁观者可读取零字节或半成品 | 同目录受限 staging 完成认证/完整性检查后原子无覆盖发布；故障和并发发布回归必须通过 |
| P1 / 旧库敏感值仍为明文或密钥不匹配 | `app/services/field_encryption.py` 已拒绝生产明文读取，但 readiness 原先可能先返回 200；CDK 卡密/邮箱尚需保护 | 显式存量加密并接入生产就绪门禁；覆盖账号、历史及 CDK 敏感字段、旧库升级和错误密钥 |
| P1 / OAuth 等待结束或读取 body 时页面再次导航 | `googlemail/src/oauth-authorizer.mjs` 原先只在等待前检查回调 URL，外域 JSON 可被误判为成功 | 在等待后和读取后复核回调 URL；新增两个导航竞态回归，独立复验已通过 |
| P1 / 旧库只有 `WHERE 0` 等局部唯一索引 | `app/services/schema_migration.py::_unique_keys` 原先只比较列名，误判具备全表唯一性，使同一上游编号能绑定多个本地任务 | 排除所有带 `*_where` 谓词的索引；回归证明迁移补建完整唯一索引、重复插入失败、删去完整索引后 schema 校验失败；独立复验通过 |
| P1 / 上游返回纯空白编号或带首尾空格的重复编号 | `app/services/recharge_service.py::_reconcile_task` 原先与人工对账规则不同，既能写入无效编号，也能绕过同一逻辑编号的唯一绑定 | 统一文本/长度/控制字符校验及 strip 规范化后再比较和入库；迁移及完整校验拒绝旧非规范编号且保留原数据，已记录旧迁移版本的库也必须检查；拒绝、状态不变、重复绑定和完整回滚回归通过 |
| P1 / 公网反复请求 `/health/ready` | `app/routes/main.py::readiness` 每次验证全库密文，原 Nginx 通用代理允许匿名触发数据库及 CPU 开销 | `deploy/nginx/google-manager.conf` 收敛 `/health/` 到本机；远程监控须明确源地址白名单；不以缓存健康结果掩盖错误密钥或新明文 |
| P1 / 镜像系统库与自带 Python 打包工具含高危项 | `Dockerfile` 仅安装运行库未升级基础镜像已有包，且保留无业务用途的打包工具 | 安全层显式刷新、apt 升级、安装后移除 pip/setuptools/wheel；语言依赖归零，剩余 OS 项仍阻断发布；未添加忽略规则 |
| P1 / 镜像扫描失败导致后续取证步骤被跳过 | `.github/workflows/release-gate.yml` 原先只有前序成功才生成/上传 SBOM | 构建成功后无论扫描结果均尝试生成 SBOM 和上传构建元数据、扫描 JSON；扫描非零仍使 workflow 失败；actionlint 通过 |
| P2 / 旧 recharge_operations 表缺少迁移依赖列 | `app/services/schema_migration.py::_migrate_recharge_cross_process_uniques` 可能先执行无效 SELECT，抛原始 OperationalError | 查询前检查必需列，给明确迁移错误并完整回滚；畸形表初始化与独立完整校验回归均通过 |

以上条目是实际审查发现，实施者完成自测后仍须由独立代理复审。人工对账引用及摘要由管理员核验录入，其真实性属于受信操作边界；不能用格式正确来证明上游确认。

## 代码行为与升级影响

### 管理员会话

`app/models/admin_session.py` 仅保存随机会话令牌的 SHA-256、凭据版本及时间信息；Cookie 保留签名保护，但必须同时命中未吊销、未过期且凭据版本匹配的服务端记录。旧式仅含 `authenticated=true` 的 Cookie 不再有效，升级后需要重新登录。

退出登录服务端吊销旧令牌，保留独立的 CDK 创建会话上下文。所有 Web 实例必须同时更新管理员密码和应用密钥，否则旧实例仍使用旧配置。DB 查询/创建/吊销失败返回脱敏 JSON 503；不能在会话写入失败时放行管理操作。

### unknown 人工对账

入口：`POST /api/recharge/admin/tasks/<task_no>/reconcile`。需要有效管理员会话、`X-Requested-With: XMLHttpRequest`、同源请求及 JSON。仅用于 live 任务的 unknown 创建结果，不可在尚有 pending/unknown mutation 时人工解锁。

| 字段 | 约束 |
| --- | --- |
| `confirmed` | 必须是布尔 `true` |
| `resolution` | `created` 或 `not_created` |
| `evidence.source` | `upstream_api`、`provider_console` 或 `provider_ticket` |
| `evidence.reference` | 可追溯的归档/工单引用，8–256 字符，不含控制字符和凭据 |
| `evidence.sha256` | 实际归档证据文件的 64 位十六进制 SHA-256 |
| `evidence.observed_at` | 带时区的 ISO 时间，必须满足服务端时间边界 |
| `upstream_task` | created 必填；包含上游 `task_no`、匹配本地任务的 `client_task_no` 和明确 `status`；可带卡密/邮箱用于一致性核对 |

这是**受信管理员核验后的人工结论**。服务端校验身份、格式、状态及关联关系，并保存摘要；它不证明上游证据本身的真实性，也没有凭空实现上游签名验签。管理员必须先核对上游明确结论并归档；本地超时或临时“查无”不能直接作为 not_created 依据。

created 绑定上游任务并保存审计；not_created 保留原任务和幂等记录，再释放允许重新提交的活动键。完全相同请求重放返回原结论；不同结论返回 409。客户不能调用此接口，不能删记录绕过核对。真实上游未验收前保持 `RECHARGE_MODE=disabled`。

### 数据迁移、备份及回滚

生产升级顺序：停止入口写入和 Web/worker → 独立 key 加密备份并验证 → `init-db` → `encrypt-sensitive-data` 盘点 → `--apply` → 再盘点为零 → 启动并验收。具体命令见[部署技术说明](deployment-technical-guide.md)和[服务器指南](server-deployment-guide.md)。这些命令本轮未作用于真实数据库。

备份 CLI 已改为 `--key-file KEY backup SRC DEST`、`verify BACKUP`、`restore BACKUP NEW_DB`。恢复拒绝覆盖已存在文件；密钥和备份分开保管。字段加密使用原长期业务 key，备份使用独立 `DATABASE_BACKUP_ENCRYPTION_KEY`，不能混用或直接轮换业务根密钥。

账号密码、恢复邮箱、2FA 密钥及相关历史用 Fernet；CDK 卡密和账号邮箱使用按字段隔离的 AES-SIV 确定性密文，支持既有等值查询，但会暴露相等关系；通知邮箱用 Fernet。生产 readiness 每次批量验证密文，拒绝旧明文、损坏值和错误密钥；仅本机/获准监控来源可访问。明文迁移需要显式 `--apply`，再次盘点的 `account_values`、`history_values`、`recharge_task_values` 均须为零。存量重复/空白上游号或重复证据中止升级，不删除、合并或自动释放任务。

旧代码无法读取新密文，因此不能只回滚代码。必须确认旧版本、数据库备份、原密钥一致，并对恢复点之后的上游副作用逐项核对；数据库恢复不会撤销第三方操作。自动备份排程、异地副本、保留策略及 RPO/RTO 仍需目标环境验收。

### 依赖与构建

- Python 原声明依赖扫描发现 4 个包、14 项告警。升级 Flask 至 3.1.3、python-dotenv 至 1.2.2、cryptography 至 50.0.0；移除仓库代码未使用的 Flask-CORS。整改后声明依赖及其解析依赖共 37 个包，`pip-audit --strict` 为 0 项。
- npm 全量审计整改：Vite 升至受维护修复版本 6.4.3，Vitest/coverage 至 4.1.11，更新受影响的传递依赖；两个项目全量及 omit-dev 审计均为 0。
- Node.js 24 LTS 替换 EOL 的 Node 20；Docker 多阶段构建和固定基础镜像 digest，最终镜像不复制 frontend 源码或 devDependencies。
- 本地 Trivy 原镜像告警 116 条（OS 113、Python 3）；加固后 84 条，均为 OS，含 6 CRITICAL 和 78 HIGH，当前报告全部无 FixedVersion。这里是包与漏洞的匹配条目数，不等同独立可利用漏洞数；严格 HIGH/CRITICAL 门禁仍返回退出码 1。
- 镜像安全层使用 `DEBIAN_SECURITY_REFRESH` 显式失效缓存，运行期移除 pip/setuptools/wheel；更新依赖必须重建镜像，不在运行容器内安装。固定基础镜像不能冻结 apt 仓库状态，应记录最终镜像 digest 及 SBOM。
- 版本调整源于实际安全告警；不能把依赖扫描为零等同于不存在未知漏洞、OS 风险或已完成许可证合规。

### 监控与测试边界

`python -m app.monitor` 对实际 Web 监听器、DB、worker 心跳、队列/unknown 积压、实例目录剩余磁盘进行检查。只输出计数、固定错误分类和退出码；不输出账号、卡密或连接串，不自动重试业务、不重启 worker、不向外发送消息。

`google-manager-monitor.timer` 定时执行检查，仅有 timer/journal 不构成告警送达。部署方应接入已有告警平台，并演练 Web 断开、DB 不可用、积压、磁盘不足及恢复通知。当前监控查询包含多个业务表；数据增长后的扫描耗时和 SQLite 容量需目标环境压测，不以本地小库结果代替。

OAuth 离线回归覆盖目标回调 origin/path/state、结构化 JSON 成功、目标账号匹配、页面替换、超时、缺少 TOTP、恢复邮箱循环和错误脱敏。Googlemail 覆盖率阈值是防回退基线，不是生产充分性标准；`batch-oauth-worker.mjs`、`main.mjs` 的关键真实路径仍需授权验收。

## 验证证据

| 命令 / 验证 | 当前结果 |
| --- | --- |
| `.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_*.py'` | **281 项通过**，36.241 秒；后续迁移缺列提示微调再以 5 项约束测试复验；仍有既有 ResourceWarning/弃用提示 |
| `docker run --rm --network none --mount type=bind,source=F:/Google_Manager/tests,target=/app/tests,readonly google-manager:final python -X utf8 -m unittest discover -s tests -p 'test_*.py'` | 最终镜像 Linux/Python 3.11.14 **281 项通过**，38.995 秒，退出 0；仅挂载测试目录，不挂真实配置/数据库，无外网 |
| 并发幂等重复验证 | 原并发测试与确定性审计查询窗口回归各运行 10 次，共 20 项通过，9.844 秒；修复前确定性用例 0.575 秒稳定得到 409 |
| `.venv\Scripts\python.exe -X utf8 -W error::ResourceWarning -m unittest tests.test_migration_constraints` | 5 项通过，0.497 秒；含局部唯一、非规范值、旧迁移标记和畸形表完整回滚 |
| `npm --prefix googlemail run test:coverage` | 7 文件、61 项通过；行 26.47%、分支 31.54%，超过防回退基线 |
| `node --test tests/account-import.test.mjs tests/googlemail-local-copy.test.mjs tests/api-service.test.mjs` | 41 项通过 |
| `npm --prefix frontend run build` / `node --test tests/frontend-ui.test.mjs` | Vite 6.4.3 构建成功，**43 项 UI 通过**；HTML 为 UTF-8 无 BOM/LF，无 CRCRLF |
| `uvx --from pip-audit==2.9.0 python -X utf8 -m pip_audit -r requirements.txt --strict` | 0 项告警；原始结果位于被忽略的 `.test-tmp/release-python-audit-after.json` |
| 两项目 `npm audit` / `npm audit --omit=dev` | 均为 0 项告警 |
| `.venv\Scripts\python.exe -m pip check` | 通过 |
| `docker build --pull --tag google-manager:final .` | 最终稳定源码重建退出 0；index/RepoDigest `sha256:496ae9e80b8cd7078014abaeee3d5d56d676828aa32afb4caabe5400270eeca0`；config `sha256:0400e2613cf2afbebf0159d66f7d57487a42e8682f38df874a0d05f6ce7b471c`；平台 manifest `sha256:991cd3ed4e8fca502f0d7474cff1826de6405a505c23a5e39fae8253010ee8c5` |
| 最终镜像运行验收 | 退出 0；Python 3.11.14、Node v24.21.0、UID 10001、Chromium startup、`init-db`、Web readiness、worker heartbeat 和 monitor 均通过；使用合成密钥/临时 Docker volume |
| Trivy 0.72.0 最终镜像扫描 | **退出 1，不通过**；84 项（OS：6 CRITICAL / 78 HIGH；Python、Node：0），全部 FixedVersion 为空。报告 `.test-tmp/release-image-audit-final.json`，Metadata.ImageID 与最终 index digest 一致 |
| CycloneDX SBOM | 1.7 格式，221 个组件，29 个无许可证元数据；字体许可仍有 SPDX 映射提示，不能视为合规通过。报告 `.test-tmp/release-image-sbom-final.cdx.json` |
| Nginx 配置及动态边界 | `nginx -t` 退出 0；桥接外部来源/伪造 XFF/8 种路径变体均 403，本机 IPv4/IPv6 到合成上游 204，no-store 与代理头透传正确；3 项配置回归通过 |
| 流水线、格式与编码 | actionlint 退出 0；`git -c core.whitespace=cr-at-eol -c core.safecrlf=false diff --check` 通过（保留既有 CRLF）；新增中文文档 UTF-8 无 BOM/LF 严格回读及本地链接校验通过 |

测试使用合成数据和隔离数据库；未使用真实账号、充值凭据或生产配置。现有 SQLite ResourceWarning 与 `datetime.utcnow()` 弃用提示仍需后续定位，未将其隐藏或当作已解决。Docker smoke 使用 `RECHARGE_MODE=disabled`，没有执行真实上游请求或 Google 自动化。

本轮镜像扫描于 2026-09-20 20:56（UTC+8）生成；扫描 JSON 的 SHA-256 为 `0c2c545ed6b74588bd0adbdb3799256f351c864dd78f6a57ccc12f92ed888e25`，SBOM 为 `67c32943b28b419437da6b40666610791d797151510297a96225326d38b5ebac`。原始文件位于忽略目录，发布归档前需按上述摘要受控收集，不能仅依赖本机临时目录。扫描命令为 `trivy image --no-progress --db-repository ghcr.io/aquasecurity/trivy-db:2 --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed=false --exit-code 1 --format json --output .test-tmp/release-image-audit-final.json google-manager:final`。

阻塞条目的具体例子如下；完整 84 条以扫描 JSON 为准，表中状态来自本次漏洞数据库，尚未逐项确认业务可利用性：

| 包 | CRITICAL 告警 | 本次报告状态 |
| --- | --- | --- |
| libglib2.0-0 | CVE-2026-58016 | fix_deferred |
| libsqlite3-0 | CVE-2025-7458 | affected |
| perl-base | CVE-2026-13221、CVE-2026-42496、CVE-2026-8376 | affected / fix_deferred |
| zlib1g | CVE-2023-45853 | will_not_fix |

### 综合检查结论与边界

| 检查域 | 结论 | 证据及剩余条件 |
| --- | --- | --- |
| 本次产品范围 | 通过 | 负责人明确仅发布 CDK；现金支付不纳入验收或对外承诺 |
| CDK 本地权限、幂等和异常处理 | 通过（本地隔离） | 上游任务号唯一约束、证据指纹、自动回写并发冲突、人工对账幂等和敏感字段保护均有回归；需取得真实上游契约记录 |
| 管理员身份与会话 | 通过（本地） | 真实登录测试覆盖旧 Cookie、退出、轮换、到期、跨实例与数据库失败 |
| 自动化测试与构建 | 通过（所列本地命令）；覆盖充分性存在风险 | 用例数量不代表全链路或全项目覆盖；Googlemail 阈值仅防回退，UI 使用接口 fixture |
| 代码质量持续门禁 | 存在风险 | CI 有 Python 编译/Node 语法检查；项目未声明统一 Python lint、前端 lint/typecheck 入口；资源及弃用警告未关闭 |
| 数据结构、静态保护及恢复 | 通过（本地合成）；生产证据待确认 | 并发初始化、完整结构/唯一键校验、静态字段迁移和原子恢复回归通过；仍需目标库副本、密钥保管、恢复耗时和恢复点之后对账证据 |
| 声明依赖漏洞 | 通过（本次扫描范围） | Python、两个 npm 项目扫描为零；不代表最终 OS/Chromium 制品已通过 |
| 生产镜像漏洞门禁 | 不通过 | 加固后 Trivy 仍为 6 CRITICAL / 78 HIGH，均无 FixedVersion，严格门禁退出 1；不得用声明依赖零告警覆盖该结论 |
| 第三方许可证与分发义务 | 待确认 | 已生成 CycloneDX SBOM，但缺完整许可证审查；根 MIT 和锁文件不足，仍须核查 Python/系统包/Chromium/字体及 vendored 源码 |
| CI/CD 与候选版本 | 不通过（生产交付门禁尚未闭合） | 流水线已编写，但尚无固定完整候选提交、远端成功运行、受保护发布和回滚版本证据 |
| 生产配置、TLS、容量与可用性 | 待确认 | Nginx 本地合成来源/路径校验通过；仍须核查真实 nginx -T、外层代理与 real-ip 信任、域名证书、权限、容量指标和单 worker/SQLite 约束 |
| 监控、告警和日志 | 存在风险 | 本地健康检查及 timer 已实现；缺实际送达、恢复通知、集中日志/关联查询与值班交接证据 |
| 备份策略与容灾 | 待确认 | 工具测试不证明自动排程、异地副本、保留期及经批准 RPO/RTO，须目标环境演练 |
| 发布与部署文档 | 通过（文档本身）；实际执行待确认 | README、技术说明和服务器指南已同步；仍需执行人、维护窗口、发布观察/终止阈值和签署记录 |

本地测试工具链为 Windows、Python 3.14、Node 22；最终镜像为 Linux、Python 3.11、Node 24，跨运行时结果分别记录，不能用 Windows 通过代替 Linux。当前仅按 SQLite 发布路径验收；PostgreSQL/MySQL 的迁移分支存在不等于已验证对应驱动、备份及运行能力。制品 ID 证明本地构建对象，尚无固定完整候选 Git 提交和远端发布记录，不能视为可发布版本。

可后排的非阻塞改进包括统一静态检查规范、`utcnow()` 迁移、逐项消除资源警告、Nginx `listen ... http2` 弃用语法调整及扩大风险用例覆盖；建议模块负责人在上线后首个迭代内完成。真实上游、敏感迁移、目标运行环境、备份恢复和告警等 P1 证据缺口不能以这些后续改进计划代替。

## 发布前仍需提供的外部证据

| 阻塞项 / 必需行动与证据 | 建议负责人 | 建议时限 |
| --- | --- | --- |
| P1：为剩余镜像 OS HIGH/CRITICAL 制定可验证修补/替代基础环境方案，并重新完成 Chromium、Linux 全量测试和零阻塞扫描；当前不得忽略告警放行 | 安全 + 平台架构 + DevOps | D0+1 工作日确定方案；D0+3 工作日首轮复验 |
| 真实上游服务间鉴权、幂等键、重复请求、unknown 查询、撤销和授权 sandbox 记录 | 后端 + 上游方 + QA | D0+3 工作日首轮；完成前 live 不放行 |
| 目标 Linux/TLS/目录权限/配置、真实 Nginx 拓扑、容量与磁盘压力、重启恢复验证 | DevOps + QA | D0+3 工作日 |
| 目标库副本迁移、静态保护、异地备份/恢复及 RPO/RTO 记录 | 数据库 + 运维 + 安全 | D0+2 工作日 |
| 实际告警接收方、阈值、送达/恢复通知和值班交接 | SRE + 项目负责人 | D0+2 工作日 |
| 固定完整候选提交、受保护远端 CI 成功运行、最终镜像 digest/扫描/SBOM、逐项许可证核查和回滚制品 | 技术负责人 + DevOps + 安全/合规 | 安全阻塞解除后 D0+2 工作日 |

D0 为本轮整改验收日，以上是建议时限，不代表已承诺或实际指派到人员。工作区保留前轮修改；本轮未把全部既有改动擅自提交、推送或部署。

依赖修补参考：[Flask 变更记录](https://flask.palletsprojects.com/en/stable/changes/)、[python-dotenv 发布记录](https://github.com/theskumar/python-dotenv/releases)、[cryptography 变更记录](https://cryptography.io/en/latest/changelog/)。
