# CDK 测试服务器更新与验收记录

日期：2026-09-25。目标服务器 `123.206.210.86`，部署目录 `/opt/google-manager-online-test`，Compose 项目 `google-manager-online-test`。按用户要求继续使用 IP/HTTP，无需新增域名或证书。本次更新已有测试服务，没有启用真实充值。

主入口整合版本现为 `google-manager:cdk-integrated-20260925`；当前入口见第 1 节，最新部署及验收见第 7 节。第 3–5 节保留首次 CDK 部署历史，不代表当前运行镜像。

## 1. 当前可用入口与账号

| 入口 | 用途 | 认证 |
| --- | --- | --- |
| `http://123.206.210.86/admin` | 默认显示 CDK 卡密系统，包含生成流程与批次制卡 | 独立具名员工 |
| `http://123.206.210.86/`、`/recharge` | 默认显示平台卡密验证、兑换及记录 | 持券/客户身份；履约当前关闭 |
| `http://123.206.210.86/admin?tab=orders` | 原充值任务管理 | 原管理员密码，未修改 |
| `http://123.206.210.86/Googlemail` | Google 邮箱管理 | 原管理员密码，未修改 |
| `http://123.206.210.86/recharge?service=legacy` | 原卡密订单与订阅工具 | 原有用户凭据协议 |
| `/admin/cdk`、`/recharge/cdk` | 兼容地址，分别进入整合后的主页面 | 与对应主页面一致 |

首次部署初始化两个独立测试员工：`cdk-admin`（admin）、`cdk-reviewer`（reviewer），明确授权全局范围 `*`。2026-09-26 管理员已按负责人要求更名为 `admin@cattoken.vip` 并重置为负责人指定的测试密码，复核员保持原配置；详见第 8 节。新员工会话不能访问邮箱管理 API；reviewer 无库存写入或员工管理权限。

密码仅写入服务器 root 可读文件，不记录在仓库、命令参数或部署日志。通过自己的 SSH 会话查看：

```bash
ssh ubuntu@123.206.210.86
sudo cat /opt/google-manager-online-test-keys/cdk-staff-initial-20260925.json
```

取得账号后，在工作台“员工权限”创建实际操作人的具名账号并分配必要渠道范围。初始管理员自身权限/密码不能通过自改接口修改，可由另一个具名 admin 调整或停用；正式双人审批必须由不同人员保管账号。初始文件包含敏感密码，不要复制到聊天、工单或 Git。

## 2. 实际配置与能力边界

- `CDK_ENABLED=1`，独立加密和查询密钥已在服务器生成，版本 `v1`；原 SECRET_KEY、Gmail 加密键及管理员配置保留。
- 密钥在受控 `.env` 中配置，另存 `/opt/google-manager-online-test-keys/cdk-keyring-20260925.json`（root/0600）；原备份密钥不变。
- `RECHARGE_MODE=disabled`。可登录、维护渠道/权益、导入隔离库存和查看管理界面；供应商验证和真实兑换尚不可执行，不要把空库存当作可发卡库存。
- SMTP 未配置，`claim_enabled=false`；邮箱 OTP 和指定客户领取不开放。配置步骤见 [CDK 操作手册第 5 节](cdk-implementation-and-operations-2026-09-25.md#5-启用前人工配置)。
- 完整制卡→双人审批→导出→兑换→对账已在目标服务器的独立临时库与本机合成上游中实际验证；线上持久库没有植入测试卡、库存或模拟订单。
- 原有 3 个邮箱账号保留；充值订单为 0。升级后 2 名 CDK 员工，CDK 卡、库存和核销记录均为 0；登录/权限测试有正常审计记录。

## 3. 构建身份和更新过程

| 项目 | 实际值 |
| --- | --- |
| 镜像 | `google-manager:cdk-20260925` |
| Docker inspect / OCI index ID | `sha256:160ac5a14372acda4b1247ff9f4c35d236909664e9ee4d43f120ac2832717a04` |
| Image config digest | `sha256:37cdff26dfaf4921cc188d5b98647dacb968c0124d536f836423b8da992e72f4` |
| 前一版本 | `google-manager:portals-20260925`，ID `sha256:85c00932dd56ea0425b3f3ce4f4c5733bd423fc2e44eedc010f46551d9e0f62d` |
| 镜像压缩包 | 447,909,546 字节；SHA-256 `afdb3ce258a1f1616d108ec7e3acfabd217775110a4972c2759d39aa32574785` |
| 源码压缩包 | 213 个文件；SHA-256 `07c5d531f61fab311a8746e908c29eb7b99f253a766e22c2117cae06be3c3d4d` |
| 迁移 | 新增 `20260925_08_cdk_catalog`，现共 8 个版本 |

沿用项目原 Dockerfile、锁定依赖和浏览器产物，在本机 Docker Desktop 构建 Linux/amd64 镜像；前端在构建阶段重新构建，未使用服务器热改容器。镜像和源文件经 SHA-256 验证后上传，Web/worker 均运行上述同一镜像；两容器各 104 个运行时代码/静态文件与源码快照一致。

先以旧版本完成在线加密备份、verify、恢复到独立路径；新镜像在恢复副本执行 `init-db`、`validate-db`、`validate-cdk`，账号/订单数量保持一致后才切换。随后停止 Web/worker，再做最终升级前备份，更新源码/Compose/受控配置，执行初始化和健康等待。切换流程计时 38.12 秒，包含停止、备份、启动和健康等待，不代表精确 HTTP 不可用时长。

源码快照为 `CDK-SOURCE-SNAPSHOT.json`，明确 `dirty=true`、`production_approved=false`，包含当前未提交及新增实现。文档在部署后另行同步；不把本次测试 tag 当作正式 registry 发布或 GO。

## 4. 实际验证命令与结果

以下容器命令的工作目录为 `/opt/google-manager-online-test`，统一使用：

```bash
compose=(sudo docker compose --project-name google-manager-online-test --env-file .env -f docker-compose.yml)
```

| 验证 | 命令/方式 | 结果 |
| --- | --- | --- |
| 原构建入口 | `docker --context desktop-linux buildx build --platform linux/amd64 --load --metadata-file .test-tmp/cdk-build-metadata-20260925.json --tag google-manager:cdk-20260925 .` | 成功，Vite 1267 modules，使用原依赖缓存 |
| Linux 候选回归 | 候选镜像 `python -m unittest tests.test_cdk tests.test_cdk_concurrency tests.test_runtime_recovery tests.test_runtime_reliability tests.test_recharge_mutation_reconciliation tests.test_migration_constraints tests.test_deployment_preflight tests.test_container_permissions tests.test_backup -q` | 141 项全部通过，75.292 秒，无外部网络 |
| Linux 宿主准备回归 | 候选镜像 `--user 0` 执行 `python -m unittest tests.test_compose_host_preparation -q` | 12 项全部通过，1.857 秒 |
| 本机 Linux 浏览器闭环 | 候选镜像 `node --test tests/cdk-flow.test.mjs` | 通过，9.874 秒；真实 Chromium/Flask/SQLite 与合成 HTTP 上游 |
| 目标服务器候选回归 | 同镜像 `python -m unittest tests.test_cdk tests.test_cdk_concurrency tests.test_migration_constraints tests.test_container_permissions -q` | 41 项全部通过，28.915 秒；含 4 进程竞争 SQLite |
| 目标服务器隔离浏览器闭环 | 同镜像 `node --test tests/cdk-flow.test.mjs`，`--network none`、现有 seccomp、256 MiB shm | 通过，6.208 秒；仅一笔履约、卡 redeemed、库存 consumed |
| 旧库迁移 | 恢复副本 `init-db`→`validate-db`→`validate-cdk`，再执行真实 Compose 初始化 | 全部通过；账号/订单计数不变，外键检查无异常 |
| 启动 | `"${compose[@]}" up -d --no-build --wait --wait-timeout 180` | initialize 退出 0；Web/worker healthy |
| 运行源码校验 | 两服务内对快照中的运行时代码/静态产物逐文件 SHA-256 校验 | 各 104 个文件全部匹配 |
| 浏览器环境 | 两服务分别 `exec -T <服务> node googlemail/src/startup-check.mjs` | 两类启动、脚本、locale、截图、清理全部通过 |
| 数据校验 | `"${compose[@]}" exec -T google-manager python -m app.manage validate-db` 和 `validate-cdk` | 结构、完整性、全部业务密文及 CDK 索引/状态校验通过 |
| CDK 公网 API | 两名员工登录、列表/统计、reviewer 写入拒绝、邮箱权限隔离、退出后 401 | 全部通过；没有发卡或产生真实充值 |
| CDK 公网浏览器 | Chromium 实际访问 IP/HTTP，两个员工登录/导航/退出、390px 兑换页 | 通过，0 页面脚本异常；退出后 API 401 |
| 旧入口回归 | `sudo python3 deploy/online_smoke.py --url http://123.206.210.86 --env-file .env --allow-http-test --exercise-account` | 通过：三入口、登录、充值只读、合成邮箱增删/去重/锁解、退出；40 请求/4 并发、0 失败、p95 9.87 ms；合成账号已删除 |
| 升级后备份恢复 | 新版本 backup→verify→独立路径 restore→`validate-db`→`validate-cdk` | 569,344 字节恢复副本通过；活库未被恢复覆盖 |
| 监控与代理 | `python -m app.monitor`、`nginx -t`、systemd monitor service | healthy=true，issues=[]，CDK 等 8 项异常计数均 0；Nginx 通过，监控 Result=success、ExecMainStatus=0 |
| 容器重启 | `"${compose[@]}" restart worker google-manager`→`up -d --no-build --wait --wait-timeout 180`，再次执行 CDK API/公网浏览器验证 | Web/worker 恢复 healthy；readiness 全项 true；两员工仍可登录、角色隔离/退出/移动页全部通过 |

浏览器测试未禁用 Chromium sandbox。上述请求样本仅用于功能/可用性检查，不是生产容量承诺。真实上游、SMTP 投递、真实 Google 登录、外部告警送达及宿主机重启未在本次验证。

## 5. 备份、证据和回滚

回滚资料：`/opt/google-manager-online-test-rollbacks/20260925/cdk/`。

- `.env`、`docker-compose.yml`、`before-update.tar.gz`：更新前配置和源码。
- `data/precheck.gmbak`、`data/restored-precheck.db`：首次加密备份与原恢复副本。
- `migration-copy/accounts.db`：在独立路径完成 CDK 升级的副本。
- `data/preupdate.gmbak`：停服后的最终升级前备份。
- `data/postupdate.gmbak`、`data/restored-postupdate.db`：升级后备份与已校验恢复副本。
- `candidate.docker.env`：副本验证使用的秘密配置，仅限 root，不可提交。

证据目录：`/opt/google-manager-online-test-evidence/20260925/cdk/`。包含镜像/切换摘要、源码快照、部署日志、运行探测、API/浏览器、旧入口 smoke、恢复验证与本次脚本。只记录脱敏结果；凭据和密钥单独位于前述 keys 目录。

发生功能异常时，先暂停新批次/冻结受影响批次，保持兼容 worker 对账。已有库存、发卡或履约后不得直接回退到 `portals-20260925`，应使用认识 CDK 的兼容镜像；禁止用旧数据库覆盖外部已消费事实。当前首次部署没有库存和核销记录，若仍未开始发行，可在核对最新业务状态后恢复备份中的配置及旧镜像启动，保留新增 schema；不要在未核查新写入的情况下照搬回滚操作。详细边界见 [CDK 操作手册](cdk-implementation-and-operations-2026-09-25.md#8-运维密钥轮换与回滚)。

## 6. 后续手工配置

1. 按第 1 节获取测试员工凭据并分配给实际人员。
2. 需要邮件领取时，按 CDK 手册配置 SMTP_SSL/465 并验证实际发送、投递、频率限制；未配置期间保持关闭。
3. 需要真实兑换时，提供独占测试库存和供应商接口配置，验证幂等、超时查询、撤回/关闭和未消费证据；不能直接切换模式跳过验收。现有测试短管理员密码例外只允许 disabled，启用 live 前也必须按原流程恢复合格配置。
4. 正式放行仍须完成 [原 B01–B07 门禁](release-blockers-remediation-plan-2026-09-24.md) 中未完成的 registry/CI、证据签署、正式 RPO/RTO、真实外部业务、容量与告警验收。当前 HTTP 测试可用不等于正式生产放行。

## 7. 主后台与首页整合更新（2026-09-25）

### 7.1 版本与数据保留

- 当前镜像：`google-manager:cdk-integrated-20260925`；Docker inspect / OCI index：`sha256:8e14fe5f9d992ad88aaf85a93811b05c1d629a22fb9d3faafce537cf1aca8a6c`；config digest：`sha256:abf38b8f44e3ef1c566ac36785424adde2b22798b614ee350c754d368919c52c`。
- 原镜像 `google-manager:cdk-20260925` 保留。新包包含 220 份源码、静态资源及测试/文档文件，上传前后校验归档 SHA-256；Web、worker 各 110 份运行文件与源码快照逐项一致。
- 本轮不新增迁移、不重建员工、不更换密钥；仍为 8 个迁移。更新前后 3 个邮箱、2 个 CDK 员工，订单、平台卡、供应商库存、核销均为 0。SECRET_KEY、Gmail/CDK 密钥和原管理员密码逐项比较保持一致。
- 新接口字段 `fulfillment_enabled=false` 已在线返回。后台展示充值关闭和库存前提；用户首页展示关闭提示，不能确认真实核销。完整制卡仍要求真实上游验证过的库存。
- 切换前在线加密备份并恢复副本，经新镜像 `validate-db`、`validate-cdk` 通过；停止 Web/worker 后再次备份，再启动新版本。切换流程计时 36.66 秒，包含停止、备份与健康等待，不作为精确 HTTP 中断时长。

### 7.2 本次执行与结果

本地构建和 45 项后端、85 项前端/API 回归见 [操作手册第 11 节](cdk-implementation-and-operations-2026-09-25.md#11-主入口整合回归2026-09-25)。服务器以 `sudo python3 /tmp/cdk_integrated_deploy.py prepare` 校验镜像/源码并执行隔离候选测试，55 项后端、1 项从主入口开始的浏览器制卡兑换测试均通过；随后 `sudo python3 /tmp/cdk_integrated_deploy.py update` 完成备份、更新和健康等待。

| 验证 | 实际结果 |
| --- | --- |
| `sudo python3 /tmp/cdk_integrated_verify.py verify` | 完成运行文件对照、浏览器启动、结构校验、CDK API、真实公网浏览器、备份恢复；首次监控检查的例外见下文 |
| 两个容器内 `node googlemail/src/startup-check.mjs` | Chromium 启动、脚本、locale、截图和清理通过 |
| 两个员工经公网访问 `/admin` | 登录/退出通过；admin 可进入新建批次表单，reviewer 无制卡及员工权限；切至原订单仍需旧认证，切回 CDK 恢复独立会话 |
| 公网 `/`、`/recharge`、`/recharge/cdk` | 默认平台码输入与关闭提示可见；390px 手机布局无横向溢出，错误格式就地提示；旧服务导航和 Googlemail 正常，无页面异常 |
| `python -m deploy.online_smoke --url http://123.206.210.86 --env-file .env --allow-http-test` | 原管理员登录/退出、只读订单、匿名拒绝和静态资源通过；40 次/4 并发静态请求零失败，p95 11.4ms（非生产容量验收） |
| `python -m app.manage validate-db`、`validate-cdk` | 在线库及独立恢复副本均通过；外键无孤儿 |
| `python -m app.worker --check`、`python -m app.monitor`、`nginx -t`、systemd monitor | 最终复测通过；监控 healthy=true、issues=[]，8 类异常计数为 0，systemd Result=success |

**保留的异常观察**：首次切换后验证以及紧接着的一次 `python -m app.monitor` 返回 `worker_heartbeat_stale`，同一时段容器健康、HTTP readiness 和独立 worker check 正常。之后不改业务代码或监控阈值，监控复测恢复正常，200 次直接监控采样未再复现。没有捕获失败当刻的具体心跳数值，暂不能认定为时间竞态或宣称根因已修复。保留失败 `verification.log` 与通过 `verification-recheck.log`；后续若重现，优先同时取样检查开始时间、实际读取心跳、worker 日志及宿主机时钟，继续定位，不通过调大阈值掩盖。

### 7.3 证据、回滚与操作

本次证据：`/opt/google-manager-online-test-evidence/20260925/cdk-integrated/`，含候选测试、版本身份、源码快照、API/公网浏览器、旧协议 smoke、失败和复查日志。备份与前版本源码：`/opt/google-manager-online-test-rollbacks/20260925/cdk-integrated/`，含 `.env`、Compose、`before-update.tar.gz`、`data/preupdate.gmbak`、`data/postupdate.gmbak`、经过验证的独立恢复数据库；密钥目录不变。秘密配置与备份仅受控账号可读。

若仅需撤销主入口整合：暂停发行，备份当前数据；在受控 `.env` 中**只将** `GOOGLE_MANAGER_IMAGE` 改回 `google-manager:cdk-20260925`，保留当前密钥和数据库，然后在部署目录运行：

```bash
docker compose --project-name google-manager-online-test --env-file .env -f docker-compose.yml up -d --no-build --wait --wait-timeout 180
docker compose --project-name google-manager-online-test --env-file .env -f docker-compose.yml exec -T google-manager python -m app.manage validate-cdk
docker compose --project-name google-manager-online-test --env-file .env -f docker-compose.yml exec -T google-manager python -m app.monitor
```

旧镜像仍认识 CDK 数据，但 `/admin` 恢复原订单页，CDK 回到 `/admin/cdk` 和 `/recharge/cdk`。不得把旧数据库覆盖现有数据。仅回退镜像不会回退宿主机参考源码；如需同步参考文件，从受控源码归档恢复对应目录并记录版本，不涉及 instance、密钥目录。真实上游、验证库存、SMTP 的手工配置和初始员工获取步骤仍按第 1、6 节及操作手册执行。

## 8. 管理员凭据更新（2026-09-26）

按负责人明确要求，对当前 `/admin` 使用的 CDK 管理员执行受控服务器重置：`cdk-admin` 更名为 `admin@cattoken.vip`，密码更新为负责人指定值，本文不记录明文。沿用同一员工 ID、admin 角色和原渠道范围；密码保存为 Werkzeug 哈希，认证版本从 1 增至 2，原有管理员会话已删除，追加 `staff.credentials_reset` 审计。复核员、邮箱后台、旧充值订单后台、充值模式、密钥和运行镜像保持原配置。

此为已授权 disabled 测试环境的单账号维护，没有修改代码或放宽全体员工的创建/改密策略。当前通用创建接口仍限制普通用户名格式及 12–256 字符密码；本次邮箱形式账号通过服务器维护设置，现有登录接口可正常使用。

执行前使用现有 `deploy.backup_database` 完成加密备份和 verify。备份及受控操作结果位于 `/opt/google-manager-online-test-rollbacks/20260926/cdk-credentials/`；当前员工凭据文件 `/opt/google-manager-online-test-keys/cdk-staff-initial-20260925.json` 已原子更新并保持 root/0600，密码未写入仓库或命令参数。

实际公网 HTTP 验证：新用户名及新密码登录 200；员工 ID、角色和范围保持一致；员工列表与批次接口可访问；重置前会话访问返回 401；退出后会话返回 401。未变更应用代码，无需重新构建或重启服务。恢复单账号凭据时应针对同一员工进行受控重置并再次递增认证版本，不得为恢复密码覆盖整个业务数据库。
