# 2026-09-25 功能整改与服务器验收记录

本轮只整改可证实的功能/运行缺陷，并更新用户授权的 `123.206.210.86` 隔离部署。真实充值保持 `disabled`，没有执行真实 Google 登录或账号变更。本记录与 [B01–B07 计划](release-blockers-remediation-plan-2026-09-24.md)、[人工配置步骤](production-manual-configuration.md) 配套。

补充：按用户最新要求，已开放无需域名/证书的公网 IP/HTTP 测试入口，详见第 6 节；最新入口为邮箱 `/Googlemail`、充值管理 `/admin`、用户充值 `/`，改名及前后台对接见第 9 节。第 4 节保留之前仅 loopback TLS 的历史验收；第 5 节正式生产门禁不阻断当前测试使用。

最新更新：CDK 版本 `google-manager:cdk-20260925` 已部署，新增 `/admin/cdk` 与 `/recharge/cdk`，数据库迁移现为 8 项。账号获取、当前镜像身份及本轮测试见 [CDK 服务器部署记录](cdk-server-deployment-2026-09-25.md)。下文各节中的旧镜像/迁移数量为对应历史阶段事实，不能作为当前版本身份。

## 1. 已修复的问题与依据

| 问题与影响 | 修复位置 | 复现/回归依据 |
| --- | --- | --- |
| Chromium 默认容器启动报 `No usable sandbox`，自动化任务不能运行 | `docker-compose.yml:9`，`deploy/chromium-seccomp.json:1` | 有限 profile 下执行 `startup-check.mjs`；普通/持久化启动、JS、locale、截图、临时 profile 清理通过；未改宿主 AppArmor/sysctl |
| 运行目录/凭据属主错误导致 SQLite 初始化失败或 Gmail readiness 失败；目录遍历错误可能被吞掉 | `deploy/prepare-compose-host.sh:46`、`:97`、`:108` | `tests/test_compose_host_preparation.py:24`，Linux root 下 12 项通过，覆盖正常、幂等、错误传播及各类异常路径 |
| Gmail 表缺失时健康检查可能仍通过；SQLite 未限定的双引号列名可能被当成字符串 | `app/services/schema_migration.py:38`、`:487`、`:551`，`app/routes/main.py:24` | `tests/test_sensitive_readiness.py:100`、`:105`；缺表/缺列均返回 503，使用表限定列名的零行查询 |
| 恢复校验遗漏 Gmail token/任务队列密文；探活全库解密随数据量增长 | `app/services/schema_migration.py:720`，`app/routes/main.py:56`，`app/manage.py:18` | `tests/test_sensitive_readiness.py`，`tests/test_database_validation.py:40`；探活每表最多 20 条，全量 `validate-db` 能发现第 22 条损坏值，源文件摘要不变 |
| history 中邮件已删除时规则处理不断失败，阻塞后续通知游标 | `app/services/gmail_rule_service.py:161` | `tests/test_runtime_reliability.py:105`、`tests/test_runtime_recovery.py:336`；modify 404 后再确认消息 404 才记 skipped，标签错误继续报错 |
| 撤回/关闭未先核对上游身份，错误任务响应可能占用操作状态 | `app/services/recharge_service.py:1290` | `tests/test_recharge_release_service.py:43`；冲突编号/卡密/账号在领取操作前拒绝 |
| 撤回/关闭超时后 mutation unknown 缺少人工失败结案；迟到查询可能覆盖人工结论 | `app/services/recharge_service.py:907`、`:938`、`:1328`，`app/models/recharge_mutation.py:51`，`app/models/recharge_mutation_reconciliation.py:7` | `tests/test_recharge_mutation_reconciliation.py:108` 起 15 项；管理员证据、重复/冲突请求、并发胜者、旧回执、新意图和既有库补表均覆盖 |
| CI 只有本地镜像，没有正式发布/同摘要证据链；Compose smoke 未正确准备凭据与目录；Docker 29 inspect 身份类型不兼容 | `.github/workflows/release-gate.yml:166`、`:216`、`:279`、`:385` | `tests/test_release_workflow.py:29` 起 13 项、`actionlint`；main 手动 publish、digest/平台/源码绑定、证据缺失拒绝，区分 config digest 与 containerd inspect 的 OCI index；尚未在真实 registry 执行 |

原工作区的邮箱规范化、锁定前状态恢复、OAuth 网络超时等修改继续保留并纳入候选；本轮未提交、创建分支或推送。原迁移见 `app/services/schema_migration.py:334`、`:365`。新增操作审计表通过 `init-db` 统一建表创建，版本化迁移数仍为 7。

## 2. 本地实际执行

工作目录为 `F:\Google_Manager`。日志位于被 Git 忽略的 `.test-tmp/`，这里只记录脱敏摘要。

| 命令 | 实测结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v` | 444 项，427 通过、17 跳过；108.843 秒，退出 0；`python-validation-20260925.log` |
| `npm --prefix frontend run build` | 成功，1263 modules；生产静态产物构建通过 |
| `node --test tests/account-import.test.mjs tests/googlemail-local-copy.test.mjs tests/api-service.test.mjs tests/frontend-ui.test.mjs tests/recharge-ui.test.mjs tests/recharge-flow.test.mjs` | 94 项全部通过，61.655 秒；包括浏览器→Flask→隔离上游→SQLite |
| `npm --prefix googlemail run test:coverage` | 8 文件/71 项通过；全局行覆盖率 28.72%，配置中的局部门槛通过；不能称为全部真实 Google 流程覆盖 |
| `actionlint` | 退出 0 |
| `.\.venv\Scripts\python.exe -m unittest tests.test_release_workflow -v` | 服务器发现 Docker 29 inspect ID 差异后补充兼容修复与回归，13 项全部通过；仅修改 CI/测试，不改变已构建运行镜像 |
| `.\.venv\Scripts\python.exe -m compileall -q app deploy run.py` | 退出 0 |
| 对 `googlemail/src/*.mjs` 逐项执行 `node --check` | 全部退出 0 |
| `git -c core.autocrlf=false -c core.whitespace=cr-at-eol diff --check` | 退出 0；保留既有 CRLF 文件 |
| `docker --context desktop-linux buildx build --platform linux/amd64 --load --metadata-file .test-tmp/build-metadata-validation-20260925.json --tag google-manager:validation-20260925 .` | 原始 Dockerfile 构建成功；最终重建复用本轮已从官方 URL 校验过的固定 Chrome 层 |
| 最终镜像内运行 Chromium startup-check | 退出 0，使用 `--security-opt seccomp=deploy/chromium-seccomp.json --shm-size=256m` |
| 最终镜像内运行下列 13 个 Python 测试模块 | 157 项，156 通过、1 跳过；55.263 秒，退出 0 |
| 最终镜像以 `--user 0` 运行 `tests.test_compose_host_preparation` | 12 项全部通过，继承入口的 umask 077 |
| 最终镜像内运行 `tests.test_backup.DatabaseBackupTestCase.test_backup_key_file_requires_exact_linux_mode_and_owner` | 1 项通过 |
| 按源码快照校验最终镜像中实际交付文件 SHA-256 | 88 个运行时源码/静态文件全部匹配 |

157 项容器验证命令（从仓库根目录执行）：

```powershell
docker --context desktop-linux run --rm --network none `
  --mount "type=bind,source=F:\Google_Manager\tests,target=/app/tests,readonly" `
  google-manager:validation-20260925 python -m unittest `
  tests.test_deployment_preflight tests.test_compose_release tests.test_recovery_drill `
  tests.test_container_permissions tests.test_browser_artifacts tests.test_database_validation `
  tests.test_recharge_mutation_reconciliation tests.test_recharge_reconciliation `
  tests.test_recharge_release_service tests.test_migration_constraints `
  tests.test_sensitive_readiness tests.test_runtime_recovery tests.test_runtime_reliability
```

Windows 跳过的 17 项均为 Linux owner/mode、文件类型、容器 umask 或 root 宿主准备测试，已在上述 Linux 容器补测。容器的 1 项跳过因 Dockerfile 不随运行镜像交付，该项在 Windows 源码测试通过。项目未声明独立 Python/前端类型检查命令；未推断类型检查已通过。Python 3.14 本地测试仍有既有 `datetime.utcnow` 弃用与测试连接 ResourceWarning；生产 Python 3.11 的本轮相关测试无失败。

一次中途尝试在精简运行镜像内跑全部 Python 测试产生 15 个 `git` 不存在错误（发布包测试需要构建工具，运行镜像有意不含 git），不能记为通过。随后改在本地源码环境验证发布工具，并在最终运行镜像验证可执行模块；没有把 git 安装进生产镜像。之前中间镜像/源码结果不代替这里记录的最终候选。

## 3. 候选身份与交付边界

- 测试 tag：`google-manager:validation-20260925`。
- Image config ID：`sha256:a2e86b4eabe3d7211802854dc5921c6861db1af3d2457e38de6460292532a7a8`。
- Buildx OCI index：`sha256:d317cff87e620c14d2995a690e13986508edc129c51ca6fb782acac20f3cd172`。
- 镜像 gzip 归档 SHA-256：`34a66c3eb1a2154548eaf45250092b7c1af5a915594a269a8eae8bae77e92987`。
- 197 个源码/测试/配置文件归档 SHA-256：`3173a3d4aafb60012ec0bc19e8f4dddcd552ee0e4a2a7d098346736660430195`；逐文件清单为 `SOURCE-SNAPSHOT.json`。Docker 29/containerd 在本机和服务器的 inspect ID 均为上述 OCI index，不能误记成 config digest。
- 源码快照明确 `dirty=true`、`production_approved=false`，包含未跟踪的新文件，不是 `git archive HEAD`。文档另行同步，避免把更新中的验收记录混入运行代码身份。

这是用户授权的隔离部署交付，不是 registry push、正式 Release Manifest 或 GO；最终生产必须按 [人工配置手册第 2 节](production-manual-configuration.md#2-b01b03发布镜像绑定证据和形成正式候选) 形成独立证据。

## 4. 目标服务器验证

目标为 `/opt/google-manager-online-test`，Compose 项目名 `google-manager-online-test`。Ubuntu 24.04.4、Docker 29.1.3、Compose 2.40.3、4 CPU、约 7.4 GiB 内存，测试使用持久化 SQLite、合成 OAuth 客户端和随机测试密钥。

本轮已在原服务上完成加密备份 `online-verified.gmbak`、verify、恢复到 `restored-online.db`，278528 字节；恢复副本通过全量 schema/业务密文验证。源文件未用普通文件复制替代 SQLite backup API。

Nginx 已安装，测试入口仅为 `127.0.0.1:8443`，30 天自签证书；`nginx -t` 通过。已启用 `google-manager-compose-monitor@google-manager-online-test.timer`，首次服务结果 `Result=success`、`ExecMainStatus=0`。没有配置或发送外部告警。

最终更新和更新后探测均退出 0；2026-09-25 09:46 UTC 核验 Web/worker 均为本记录 OCI index，非特权 `googlemanager` 用户、256 MiB shm。initialize 退出 0，Web/worker 均 healthy；端口仍仅 `127.0.0.1:8002`。

| 实际命令/操作 | 结果 |
| --- | --- |
| 镜像归档 `sha256sum -c` → `docker load`，源码快照逐文件校验 | 与第 3 节摘要一致；197 个源码文件匹配，保留原业务密钥 |
| 停止 Web/worker → 旧镜像 backup/verify/restore → `prepare-compose-host.sh` | 278528 字节升级前测试库备份/校验/恢复完成，用时约 1 秒；`.env` root:root 0600，OAuth/运行目录为 10001:10001、0600/0700 |
| `sudo docker compose --project-name google-manager-online-test --env-file .env -f docker-compose.yml up -d --no-build --wait --wait-timeout 180` | initialize 退出 0；Web/worker healthy |
| 两服务分别 `exec -T <服务> node googlemail/src/startup-check.mjs` | 均通过，两类 sandbox 启动/JS/locale/截图/清理正常 |
| `exec -T google-manager python -m app.manage validate-db` | 活测试库全量结构/完整性/密文通过 |
| `docker run --rm --network none google-manager:validation-20260925 python -m deploy.recovery_drill` | 436 ms，7 迁移，错误 key/覆盖目标拒绝，源库不变、0600、密文可读；`production_rpo_rto_validated=false` |
| 升级前恢复副本复制到新路径，候选 `init-db` → 只读 `validate-db` | 补建审计表后通过，保留原恢复副本；没有在原库试恢复 |
| 同时申请第二个 worker 锁 | 被拒绝，现有 worker 继续运行 |
| 创建合成加密账号 → 重启 Web/worker → 核对账号密码可解密与 pro 状态 → 删除 | 通过；readiness 恢复 200，合成记录已清理 |
| `exec -T google-manager python -m app.monitor --min-free-mib 999999999` | 预期退出 1，包含 `disk_space_low`；未实际写满磁盘，随后正常 monitor 退出 0、所有异常计数为 0 |
| `sudo python3 deploy/online_smoke.py --url https://127.0.0.1:8443 --env-file .env --ca-file /etc/google-manager-online-test/tls/server.crt --exercise-account` | 最终重启后通过首页/静态、401、disabled、Secure 登录会话、大小写去重、锁解恢复 pro、删除和登出；40 请求/4 并发，0 失败，p95 10.32 ms，最大 11.29 ms |
| `systemctl show google-manager-compose-monitor@google-manager-online-test.service -p Result -p ExecMainStatus` 与 timer | `success`、`0`、timer active；`nginx -t` 通过 |
| `docker stats --no-stream` | 该采样 Web 128.6 MiB/1.91% CPU、worker 55.75 MiB/0.29% CPU；仅空载快照，不是容量上限 |
| 本地浏览器经 SSH 隧道访问目标 TLS | 管理登录页/账号库/Gmail 空态渲染成功；经校验证书的 HTTPS API 建立管理员会话后导入浏览器，升级和重启后会话仍有效；点击退出返回登录页，随后账号接口返回 401、会话 authenticated=false |
| `sudo python3 deploy/preflight.py --env-file .env --project-root . --pretty` | 预期退出 1：2 failed（测试 tag 非 registry digest、无正式域名）、2 pending（公网 OAuth callback、Release Manifest）、5 passed（文件结构与权限）；没有把测试环境伪装成正式 GO |

浏览器 TLS 使用自签证书例外仅用于隔离页面渲染；API smoke 与建立会话的客户端均校验证书。SSH 隧道曾因连接重置断开，表现为本机端口拒绝连接；重建带 `ServerAliveInterval=15` 的隧道后恢复，服务器 readiness/TLS 当时仍正常。首次更新尝试因把 Docker 29 inspect 的 OCI index 与 config digest 比较而提前退出，尚未停服；现已修复校验与 CI 回归，再次更新通过。

`systemd-analyze verify` 退出 0，但提示服务器既有腾讯 `tat_agent.service` 使用旧 `/var/run` PID 路径；未改动该外部组件。未执行宿主重启、公网正式证书验证、真实告警送达或生产负载测试；这些仍需维护窗口与相应环境信息。

升级前资料位于 `/opt/google-manager-online-test-rollbacks/20260925`，包括原 `.env`/Compose、源码备份、加密 `data/preupdate.gmbak` 与新路径恢复副本。独立备份 key 在 `/opt/google-manager-online-test-keys/backup.key`，未写入仓库。旧镜像 `google-manager:online-test`（ID `sha256:54553352afad5fd057f81136a59ca7b1ddefe03f336c0a6377b2a955c11f9ed1`）保留用于受控回滚。

更新日志、最终探测日志、正式预检结果和源码逐文件清单已归档到服务器 `/opt/google-manager-online-test-evidence/20260925`（目录 root:root/0700，文件 0600）：`google-manager-update.log`、`google-manager-final-probes.log`、`google-manager-preflight.json`、`SOURCE-SNAPSHOT.json`。前三项亦下载到本地 `.test-tmp/server-update-20260925.log`、`server-probes-20260925.log`、`server-preflight-20260925.json`，便于复核；这些证据不含业务密钥、密码或会话 Cookie。

## 5. 正式上线仍需完成

正式生产保持 **不可上线**。剩余必须项为受控 registry/真实 CI、干净候选与许可证证据/独立 GO、正式数据库 RPO/RTO/异地备份、真实 Google 验收，以及公网域名 TLS/容量目标/真实告警送达。充值保持 disabled 时无需为本次测试提供真实上游，不能把它写成已经通过 live 验收。详细配置和负责人输入见 [人工配置手册](production-manual-configuration.md)。

此前本地 SBOM 共 244 个组件、46 项缺许可证元数据；这是历史制品证据，正式 digest 须重新生成并核对，不在本轮伪造补齐。安全性专项与依赖升级未作为本轮扩展整改范围。

## 6. 无域名/证书的公网 HTTP 测试部署

用户确认测试阶段无需域名或证书。2026-09-25 已将 Nginx HTTP 测试入口开放到 TCP 80，首页 `http://123.206.210.86/`、管理后台 `http://123.206.210.86/admin` 可从本地 Windows 电脑直接访问。服务器 UFW 为 inactive，实测公网可达，无需本轮额外修改云安全组；应用 8002 仍仅监听 loopback。

新增 `deploy/nginx/google-manager-http-test.conf:1`，仅 HTTP 代理对 `session` Cookie 使用 `nosecure`，使浏览器可在 HTTP 请求中保持登录。应用镜像仍为第 3 节的 OCI index，production 配置、持久化数据库、业务密钥、worker 和原 loopback TLS 入口保留；未为 HTTP 切换到内存数据库的 testing 配置。

`deploy/online_smoke.py:27` 新增显式 `--allow-http-test`，默认仍拒绝 HTTP；HTTPS 分支继续检查 Secure Cookie。主机上的 smoke 工具已更新，本次没有修改镜像内应用。原 `SOURCE-SNAPSHOT.json` 记录上轮基础快照，本轮宿主脚本/Nginx/测试/文档增量记录在部署目录 `HTTP-TEST-OVERLAY.json`，不能把旧源码摘要当成本轮宿主工具摘要。

| 验证命令/操作 | 实际结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m unittest tests.test_online_smoke tests.test_api.ApiTestCase.test_production_requires_strong_secret_and_secure_cookie_settings -v` | 4 项通过，1.080 秒；HTTP 明确启用、无效 origin 拒绝、实际 Flask 登录/合成账号清理、production Secure 默认值回归 |
| `sudo nginx -t` → `sudo systemctl reload nginx` | 均退出 0，新增 HTTP 入口，无需重启 Web/worker |
| Windows `curl.exe --noproxy '*' --connect-timeout 10 --max-time 20 -sS -o NUL -w 'public_http=%{http_code}\n' http://123.206.210.86/admin` | `public_http=200`，退出 0 |
| 服务器 `sudo python3 deploy/online_smoke.py --url http://123.206.210.86 --env-file .env --allow-http-test --exercise-account` | `status=passed`、`transport=http_test`；首页/静态、匿名 401、充值 disabled、登录、邮箱去重/锁解恢复/删除、退出全部通过；归档样本 40 请求/4 并发，0 失败，p95 10.04 ms、最大 10.64 ms |
| 原 HTTPS 入口 smoke，不带 `--allow-http-test`，仍校验测试证书 | `status=passed`、`transport=https`，Secure 会话与退出正常 |
| Playwright 直接访问公网 HTTP；经本机 API 建立会话后加载浏览器 | 登录页、账号库、Gmail 空态渲染成功；不依赖 SSH 隧道；点击退出后账号接口 401、authenticated=false |
| `docker inspect`、本机 `/health/ready`、monitor systemd 状态 | Web/worker healthy；readiness 全项 true；monitor Result=success、ExecMainStatus=0 |
| `python -m compileall -q deploy/online_smoke.py tests/test_online_smoke.py`、`git ... diff --check` | 均退出 0 |

HTTP smoke 原始 JSON 在 `/opt/google-manager-online-test-evidence/20260925/http-test-smoke.json`，本地副本 `.test-tmp/server-http-test-smoke-20260925.json`。改动前 smoke 工具备份位于 `/opt/google-manager-online-test-rollbacks/20260925/http-test/online_smoke.py`。测试入口安装、管理员凭据位置及关闭步骤见 [手册第 0 节](production-manual-configuration.md#0-当前测试阶段直接使用-ip-和-http)。

当前可以开展服务器在线功能测试。真实 Google 流程需要真实测试客户端/账号，充值继续 disabled；本次 HTTP 验收不宣称这些外部流程已通过，也不替代生产容量验收。

## 7. 测试管理员密码更新

按用户要求更新服务器管理员密码，具体值仅保存在服务器受控 `.env`。新增显式 `ALLOW_TEST_ADMIN_PASSWORD=1`，仅允许与充值 disabled 同用；未开启时继续拒绝短密码和示例密码，生产 preflight 也拒绝启用该开关的候选。实现见 `app/__init__.py:81`、`docker-compose.yml:21`、`deploy/preflight.py:809`，回归见 `tests/test_online_test_configuration.py:12`。数据加密、持久化 SQLite、队列、非 debug 配置均保留。

服务器以本记录基础验证镜像为底，仅更新应用工厂、preflight 和 smoke 工具，生成测试镜像 `google-manager:online-test-admin-20260925`；实际镜像 ID 为 `sha256:08eb218a85b7b787562aa4fc08995237a9ff27e538827befd90bd0a077e7d57a`。这仍是测试制品，不是 registry 正式发布。增量身份见部署目录 `ADMIN-TEST-OVERLAY.json`，基础和 HTTP 阶段的历史证据保留。

实际验证：本地认证、生产配置和预检相关 56 项运行完成（53 通过、3 项 Linux 权限条件跳过）；补充预检开关回归后，新测试模块 5 项通过，最终 Linux 镜像内同模块 5 项也全部通过。Compose 重新创建服务后 initialize 退出 0，Web/worker 均 healthy。使用更新后的 `.env` 执行 HTTP smoke，新密码登录、账号增删/去重/锁解及登出全部通过；40 请求/4 并发，0 失败，p95 10.50 ms。编译与差异检查退出 0。

变更前 `.env`/Compose/应用工厂/preflight 备份在 `/opt/google-manager-online-test-rollbacks/20260925/admin-password/before-update.tar.gz`；新密码 smoke 记录在 `/opt/google-manager-online-test-evidence/20260925/admin-password-smoke.json`。旧会话因凭据版本变化失效，测试人员需重新登录。后续修改与正式配置恢复步骤见 [操作手册第 0 节](production-manual-configuration.md#0-当前测试阶段直接使用-ip-和-http)。

## 8. 管理员登录旧 IP 封禁解除

用户报告登录页“访问被拒绝，剩余 23 小时 53 分钟”。公网请求 `/api/auth/check` 实际复现 `banned=true`；测试数据库仅有 1 条有效封禁，失败次数为 3，最后失败时间 `1790332305.2248626`，早于密码配置更新时间 `1790332567` 约 262 秒。该记录不是 Docker 网关或 loopback IP，没有证据表明本次由代理 IP 混用导致。

原因是 `app/services/auth_service.py:34` 按持久化到期时间判断封禁，`app/routes/api.py:953` 在校验新密码前即返回封禁；修改密码和容器重建不会清除 `login_attempts`。本轮先只重置“仍有效且早于本次改密”的旧记录，影响 1 条。随后同一公网出口 `/api/auth/check` 返回 `banned=false`，使用当前密码完成登录、会话确认和退出，均通过。没有记录明文 IP、密码或 Cookie。

新增 `python -m app.manage reset-admin-login --ip <客户端IP>` 运维入口（`app/manage.py:45`、`:76`），供改密或核实误封后定向重置；需要全部重置时必须显式指定 `--all`。无参数、无效地址或同时给出两种范围时在创建应用/访问数据库前退出 2。密码、账号及管理员会话不受命令影响；三次失败封禁规则保留。当前页面只在加载时检查封禁，解封后须刷新登录页。

验证记录：先运行新回归模块，因缺少解封命令出现 3 项预期错误；实现后运行 `python -m unittest tests.test_admin_login_reset tests.test_admin_auth tests.test_database_validation tests.test_online_test_configuration -q`，23 项全部通过，1.816 秒。最终 Linux 候选镜像内 `test_admin_login_reset.py` 的 4 项全部通过，0.437 秒；覆盖改密前封禁→定向解封→新密码登录、其他 IP 保持封禁、显式全量及幂等、非法参数拒绝。编译和差异检查通过。

测试镜像为 `google-manager:login-reset-20260925`，基于上一节镜像仅更新 `app/manage.py`，镜像 ID 为 `sha256:b37c545c1324cfd7390d2a1f097accc419b4a2feea9b861b7f8d47abb6f3493a`；增量清单为部署目录 `LOGIN-RESET-OVERLAY.json`。变更前 `.env` 和管理工具保存在 `/opt/google-manager-online-test-rollbacks/20260925/login-reset/`。恢复操作不涉及数据库结构变更；日后调整密码时按操作手册明确决定是否重置旧失败记录。

## 9. Googlemail 入口迁移与充值管理后台

用户确认继续部署 `123.206.210.86`，仅迁移邮箱页面路径。原邮箱管理改为 `/Googlemail`，页面名称 Googlemail；用户端 `/`、`/recharge` 改名 Chat GPT充值中心；`/admin` 新增充值管理后台。OAuth 回调及已有用户 API 保持原地址。

本轮后台对接真实本地订单表及既有充值服务，提供统计、分页/过滤/搜索、订单详情、显式状态同步、确认撤回/关闭、创建 unknown 和操作 unknown 人工对账及审计展示。列表/详情 GET 不请求上游；disabled 模式只读历史，模式不匹配的订单不能操作。统计待核对订单去重，401 后清理详情，避免继续请求已失效会话的订单。范围与步骤见 [充值后台说明](recharge-admin-guide.md)。

代码证据：`app/routes/main.py:79`、`app/routes/recharge.py:131`、`app/routes/recharge.py:265`、`app/services/recharge_admin_service.py:16`、`frontend/src/App.jsx:5`、`frontend/src/RechargeAdminApp.jsx:75`。回归入口为 `tests/test_recharge_admin.py:11`、`tests/frontend-ui.test.mjs:341`、`tests/recharge-flow.test.mjs:196`、`tests/test_online_smoke.py:28`。无新增数据库结构或迁移。

| 命令 / 实际操作 | 结果 |
| --- | --- |
| `npm --prefix frontend run build` | Vite 构建退出 0，1264 模块；生成独立充值后台、邮箱后台及共享登录块 |
| `.venv/Scripts/python.exe -m unittest tests.test_recharge_admin tests.test_recharge_reconciliation tests.test_recharge_mutation_reconciliation tests.test_recharge_release_service tests.test_recharge_release_routes tests.test_admin_auth -q` | 62 项全部通过，22.325 秒；包含模式限制、分页搜索、确切订单操作与待核对去重 |
| `node --test tests/api-service.test.mjs tests/frontend-ui.test.mjs tests/recharge-ui.test.mjs tests/recharge-flow.test.mjs` | 首次运行 82 项，80 通过，2 项因新增测试定位器错误失败：关闭订单匹配了详情关闭按钮，登录按钮实际名为“进入系统”；修正测试为精确匹配实际按钮 |
| `node --test --test-name-pattern 'recharge admin filters\|recharge completes' tests/frontend-ui.test.mjs tests/recharge-flow.test.mjs` | 上述两项修正后均通过，14.584 秒；未掩盖首次失败或宣称整组重跑 |
| `python -m unittest tests.test_online_smoke tests.test_recharge_release_routes -q` | 改名收据及新增三入口 smoke 后 8 项通过，1.327 秒 |
| 候选镜像 `docker run --rm --network none ... python -m unittest tests.test_recharge_admin tests.test_recharge_reconciliation tests.test_recharge_mutation_reconciliation tests.test_recharge_release_routes tests.test_online_smoke -q` | Linux 内 50 项全部通过，17.639 秒，无外部网络 |
| 候选镜像 `node --test --test-name-pattern 'separate entry points\|recharge admin\|recharge completes' tests/frontend-ui.test.mjs tests/recharge-flow.test.mjs` | Linux Chromium 6 项全部通过，14.086 秒；使用现有 seccomp、非 root 和 256 MiB shm、禁外部网络；覆盖三入口、只读/分页/375px布局、取消确认/失败恢复、证据对账、401、用户创建到管理员同步全链路 |
| `python -m compileall -q app/services/recharge_admin_service.py app/routes/recharge.py app/routes/main.py deploy/online_smoke.py tests/test_recharge_admin.py`、`git -c core.autocrlf=false -c core.whitespace=cr-at-eol diff --check` | 均退出 0；UTF-8 无 BOM、原换行风格保持，中文回读正常 |
| 备份 `.env`/原源文件 → 校验 72 个增量文件 → Compose `up -d --no-build --wait --wait-timeout 180` | initialize 退出 0，Web/worker 均 healthy，实际镜像身份一致 |
| `sudo python3 deploy/online_smoke.py --url http://123.206.210.86 --env-file .env --allow-http-test --exercise-account` | 三入口、静态资源、匿名管理接口 401、登录、充值后台只读概览/列表、邮箱合成账号增删/去重/锁解、退出全部通过；40 请求/4 并发，0 失败，p95 9.37 ms，最大 9.40 ms |
| Compose `exec -T google-manager python -m app.manage validate-db`、`exec -T worker python -m app.worker --check`、本机 `/health/ready` | 均退出 0；数据库结构/完整性/全部密文通过，readiness 所有健康项 true；monitor service Result=success、ExecMainStatus=0 |
| Playwright 从本地访问公网 HTTP | Googlemail 登录页/账号库、充值后台名称/统计/空列表/刷新和用户首页名称正常；同一服务端会话可进入两个后台；点击退出后 authenticated=false，订单与邮箱接口均返回 401；临时会话文件已清理 |

候选基于上一节已部署镜像，仅覆盖本轮路由、服务、smoke 和构建后的静态文件。镜像 tag `google-manager:portals-20260925`，Docker inspect ID 为 `sha256:85c00932dd56ea0425b3f3ce4f4c5733bd423fc2e44eedc010f46551d9e0f62d`。本地上传包 `.test-tmp/portals-20260925.tar.gz` 的 SHA-256 为 `4b15d11ceb313e1a68a700c87f9750454dab17be2eb4c0a1052fd15a2f6cda9b`；源文件、测试、文档和前端产物已同步到服务器。增量身份为 `PORTALS-OVERLAY.json`，标明 dirty/test-only；本文验收记录在部署后单独同步，不混入候选构建身份。

备份：`/opt/google-manager-online-test-rollbacks/20260925/portals/before-update.tar.gz`。证据：`/opt/google-manager-online-test-evidence/20260925/portals-update.log`、`portals-http-smoke.json`、`PORTALS-OVERLAY.json`。回滚恢复 `.env` 中上一节镜像引用并执行相同 Compose 命令；必要时从备份恢复源文件，不覆盖数据库。

没有独立 lint/typecheck 脚本，未编造对应通过结果。浏览器全链路使用隔离 HTTP 合成上游和临时 SQLite；当前在线库没有充值订单，未在公网服务写入模拟订单或启用真实履约。本轮仍保持 `RECHARGE_MODE=disabled`，没有完成真实 Google/充值上游验收；不改变第 5 节的正式发布门禁。
