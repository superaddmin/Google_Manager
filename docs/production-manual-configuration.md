# 正式上线人工配置与验收操作手册

更新日期：2026-09-25。本文与 [B01–B07 整改计划](release-blockers-remediation-plan-2026-09-24.md) 配套。服务器测试环境为 `/opt/google-manager-online-test`；正式环境请使用独立目录、密钥和数据。本轮用户要求暂不提供外部环境信息，因此以下项目保留为待配置，不以合成测试代替验收。

## 0. 当前测试阶段：直接使用 IP 和 HTTP

用户已明确测试阶段不需要域名和证书。当前测试入口：邮箱管理 `http://123.206.210.86/Googlemail`，充值管理后台 `http://123.206.210.86/admin`，Chat GPT充值中心 `http://123.206.210.86/`；不需要 SSH 隧道，也不跳转 HTTPS。继续部署在原服务器，不迁移到 64.83.11.94。以下正式域名、证书和发布签字要求不阻断本阶段测试。

旧邮箱书签 `/admin` 请改为大小写一致的 `/Googlemail`；原 `/api/accounts`、Gmail OAuth 回调和邮箱业务 API 不变。两个后台使用同一管理员密码及会话；充值后台操作、接口及模式限制见 [操作说明](recharge-admin-guide.md)。

管理员密码按用户要求更新，保存在服务器 `/opt/google-manager-online-test/.env` 的 `ADMIN_PASSWORD` 中，可通过 SSH 登录后使用 `sudoedit /opt/google-manager-online-test/.env` 查看；它与 SSH 登录密码不同，不在仓库文档中记录密码值。只使用合成测试数据，充值保持 `disabled`，真实 Google 授权与上游履约仍待对应环境准备。

本测试部署显式设置 `ALLOW_TEST_ADMIN_PASSWORD=1`，允许短测试密码；默认值 `0` 继续执行原生产密码校验。开关仅允许与 `RECHARGE_MODE=disabled` 同时使用，仍保留数据库持久化、队列和加密配置。修改密码后执行 `sudo docker compose --project-name google-manager-online-test --env-file .env up -d --no-build --wait --wait-timeout 180` 重新创建服务，旧会话因凭据版本变化失效。正式部署须删除该开关或设为 `0` 并配置符合生产规则的密码；正式 preflight 会拒绝该测试开关。

管理员密码更新不会自动清除已持久化的 IP 登录失败记录。遇到“访问被拒绝/封禁 24 小时”，先核对该 IP 的失败原因，确认是测试误输或旧密码后，在服务器执行定向解封：

```bash
cd /opt/google-manager-online-test
sudo docker compose --project-name google-manager-online-test --env-file .env \
  exec -T google-manager python -m app.manage reset-admin-login --ip <需要解封的客户端IP>
```

该命令仅重置指定 IP 的失败次数、失败时间和封禁到期时间，输出受影响条数；不会修改密码、账号或会话。必须明确指定 `--ip`，只有确认要重置全部管理员登录失败记录时才改用 `--all`。修改测试密码并需要所有测试人员重新登录时，可明确执行一次 `reset-admin-login --all`。执行后刷新浏览器登录页再用新密码登录；已显示的封禁页不会主动重新检查状态。三次失败的保护规则继续有效。

HTTP 入口使用 `deploy/nginx/google-manager-http-test.conf`。该配置仅在 HTTP 测试代理中去除 `session` Cookie 的 Secure 标记，解决登录后浏览器不回传 Cookie 的问题；应用继续使用 production 配置和原持久化数据库，`ProductionConfig.SESSION_COOKIE_SECURE=True`，没有切换到会创建内存库的单元测试配置。原 loopback TLS 入口仍可使用。

复现部署与验证（在服务器执行）：

```bash
cd /opt/google-manager-online-test
sudo install -m 644 deploy/nginx/google-manager-http-test.conf /etc/nginx/conf.d/
sudo nginx -t && sudo systemctl reload nginx
sudo docker compose --project-name google-manager-online-test --env-file .env ps
sudo python3 deploy/online_smoke.py --url http://123.206.210.86 \
  --env-file .env --allow-http-test --exercise-account
```

预期 Web/worker 为 healthy，smoke 输出 `status=passed`、`transport=http_test`，完成登录、合成账号增删/锁解/去重、登出和 40 请求/4 并发样本。默认 smoke 仍要求 HTTPS；HTTP 必须显式传 `--allow-http-test`。从其他电脑访问上述地址确认公网可达；若新服务器连接超时，检查云安全组是否放行 TCP 80，不需要开放 8002。

停止 HTTP 测试入口时，执行 `sudo mv /etc/nginx/conf.d/google-manager-http-test.conf /etc/nginx/conf.d/google-manager-http-test.conf.disabled`，再执行 `sudo nginx -t && sudo systemctl reload nginx`；不改变容器、数据库或既有 TLS 配置。正式域名部署时必须停用这份测试配置，它不纳入正式发布包的 Nginx 模板。

## 1. 最后需要提供的信息

| 对应门禁 | 待提供内容 | 配置位置与完成依据 |
| --- | --- | --- |
| B01/B03 | 镜像仓库路径、仓库写入账号或 GHCR 权限、发布分支和责任人 | GitHub Actions Variables/Secrets；同一次 CI 的 digest、证据文件和正式发布包 |
| B07 | 域名、DNS 管理权、证书联络邮箱、云安全组权限 | DNS A 记录、Nginx、Certbot；公网 HTTPS 与续期验收 |
| B05 | 专用 Google Cloud 项目、Web OAuth 客户端、授权测试账号 | 服务器 `credentials.json` 与 Google Cloud Console；完整 OAuth/Gmail 验收 |
| B05（可选） | Pub/Sub 项目、topic、subscription | `.env` 与 GCP IAM；启用 watch 后收到实际通知 |
| B06（可选启用） | 上游 sandbox 地址、版本化契约、鉴权方式、测试卡密、幂等/查询/撤回协议 | 按契约接入并验收；完成前 `RECHARGE_MODE=disabled` |
| B04 | 正式库来源、数据量、原业务密钥托管引用、RPO/RTO 目标、异地备份位置 | 数据副本演练、加密备份、独立恢复实测 |
| B07/B03 | 并发与延迟目标、告警渠道和接收人、维护窗口、回滚负责人 | 容量报告、真实故障/恢复通知、独立 GO 记录 |

密码、OAuth secret、Token、业务密钥通过受控文件或 Secret 注入；不要填入本文、提交信息或聊天。

## 2. B01/B03：发布镜像、绑定证据和形成正式候选

1. 审查当前工作区全部修改（包括未跟踪的新脚本/测试/文档）。由维护者将需要交付的文件提交到受保护 `main`；本轮未自动提交或推送。`.env`、`credentials.json`、数据库和 `.test-tmp` 不加入提交。
2. 在 GitHub 仓库 Settings → Secrets and variables → Actions 配置 Variable `RELEASE_IMAGE`，格式如 `ghcr.io/<组织>/google-manager`，不带 tag/digest。使用该仓库的 GHCR 时允许工作流 `packages:write`；其他仓库配置 Secrets `REGISTRY_USERNAME`、`REGISTRY_PASSWORD`。
3. 在 Actions → Release gate → Run workflow，选择 `main`，设置 `publish=true`。默认 `false` 只构建检查；没有发布到 registry。
4. 工作流先执行测试和依赖门禁，再构建并推送候选，通过 registry digest 拉取同一制品，执行 Compose、Chromium、恢复演练和镜像扫描。下载本次 `google-manager-build-evidence-*` artifact，检查 `build-evidence.json` 的检查结果、`image-digest.txt` 和 `EVIDENCE-SHA256SUMS`。失败流水线的候选镜像不得使用。
5. 在干净 checkout、同一源码和可信 CI 证据上生成发布包。将下载的证据置于仓库内受忽略的 `.test-tmp/release-evidence/`，执行下列命令（占位符须替换，输出目录必须尚不存在）。缺少许可证信息时补充可审计来源后重新生成，不能伪造 SBOM 或删掉门禁。
6. 核对 manifest 中源码、镜像、公开文件和证据摘要；记录旧镜像、数据库备份与原业务密钥引用。依据 [签字单](release-signoff-template.md) 独立签署 GO，保存获批准的 manifest SHA-256。工具不自动生成 GO。

```bash
python3 deploy/release_bundle.py \
  --image '<registry>/<repository>@sha256:<digest>' --platform linux/amd64 \
  --scan .test-tmp/release-evidence/image-vulnerabilities.json \
  --sbom .test-tmp/release-evidence/google-manager-sbom.cdx.json \
  --ci-url 'https://github.com/<组织>/<仓库>/actions/runs/<run-id>' \
  --output .test-tmp/release-candidate
```

上述命令不使用 `--draft`；正式证据未齐时会拒绝。完整目录交付后，使用 root 会话进入目录，按 [部署准备说明](deployment-preparation.md) 执行 preflight 和 `compose_release.py`。工作流通过不代表许可证元数据完整；此前本地 SBOM 有 46 项缺失，最终制品须重新统计并补齐来源。

B02 所需 seccomp 文件必须随发布包分发。它来源于 [Playwright v1.63.0 官方 profile](https://github.com/microsoft/playwright/blob/v1.63.0/utils/docker/seccomp_profile.json)，仓库内格式化文件为 `deploy/chromium-seccomp.json`，SHA-256 为 `d4906af343fd3bdf044a774ff6969bc506fcd141dab5acc8d02d8323e9991c44`。配置保持默认拒绝动作并允许 Chromium 所需命名空间调用，见 [Playwright Docker 指南](https://playwright.dev/docs/docker)。Web/worker 默认 Compose 已引用，且使用独立 256 MiB 共享内存。

## 3. B07：域名、证书与生产入口

1. DNS 为正式域名添加指向 `123.206.210.86` 的 A 记录；没有验收 IPv6 前不要配置 AAAA。云安全组允许需要的来源访问 TCP 80/443；8002 保持宿主 loopback。
2. 在目标服务器安装 `nginx certbot`，用 `dig +short <域名>` 和外网请求确认 DNS 与端口可达。
3. `sudo install -d -m 755 /var/www/certbot`。先创建仅监听 80 的 Nginx server，`server_name` 填域名，`/.well-known/acme-challenge/` 的 `root` 为 `/var/www/certbot`；其余 location 暂时返回 503。执行 `sudo nginx -t`、`sudo systemctl reload nginx`。
4. `sudo certbot certonly --webroot -w /var/www/certbot -d <域名> --email <联络邮箱> --agree-tos`。取得证书后，将 `deploy/nginx/google-manager.conf` 复制到 `/etc/nginx/conf.d/google-manager.conf`，替换所有 `your-domain.com`（server_name 和证书路径都要替换）。勿直接启用带不存在证书路径的模板。
5. 执行 `sudo nginx -t && sudo systemctl reload nginx`。外部浏览器验证有效证书、首页、管理端、登录/退出；外网访问 `/health/ready` 应为 403，本机访问应为 200。
6. 执行 `sudo certbot renew --dry-run`；确认 `certbot.timer` 已启用，并配置续期成功后 reload Nginx 的 deploy hook。将证书到期和续期失败纳入告警。申请/续期机制依据 [Certbot webroot 操作说明](https://eff-certbot.readthedocs.io/en/stable/using.html#webroot)，正式验证必须从公网完成。

当前隔离测试入口使用 `deploy/nginx/google-manager-online-test.conf`，仅监听服务器 `127.0.0.1:8443`，证书位于 `/etc/google-manager-online-test/tls`，为 30 天测试证书。它证明 TLS/代理链路可工作，不是公网证书验收。需要浏览器访问时，在本机执行：

```powershell
ssh -N -o ServerAliveInterval=15 -o ExitOnForwardFailure=yes -L 8443:127.0.0.1:8443 ubuntu@123.206.210.86
```

访问 `https://localhost:8443/admin`。测试证书默认不受浏览器信任；通过已验证 SSH 通道核对/导入测试证书，或等待正式域名证书。管理员凭据从服务器受控 `.env` 取得，SSH 密码不是管理员密码。

## 4. B05：Google OAuth/Gmail/Pub/Sub

1. 在 Google Cloud Console 新建/选择专用测试项目，启用 Gmail API；配置 OAuth consent screen、应用名称、受众和测试用户，声明代码实际使用的 `https://www.googleapis.com/auth/gmail.modify` scope。客户端类型、回调与 refresh 要求按 [Google Web OAuth 官方说明](https://developers.google.com/identity/protocols/oauth2/web-server) 核对。
2. 创建 **Web application** OAuth client，将 Authorized redirect URI 精确设为 `https://<域名>/api/gmail/oauth/callback`。下载客户端 JSON，通过受控传输存为正式部署目录的 `credentials.json`，不能继续使用合成客户端。
3. 由 `sudoedit <部署目录>/.env` 配置 `PUBLIC_DOMAIN=<域名>`、`GMAIL_REDIRECT_URI=https://<域名>/api/gmail/oauth/callback`、`GMAIL_HTTP_TIMEOUT_SECONDS=30`。配置完成、停止 Web/worker 后执行 `sudo bash deploy/prepare-compose-host.sh`，OAuth 文件须为 `10001:10001/0600`；重新启动后新配置才生效。脚本不是事务，操作系统错误可能留下部分权限变更，应保持停服、排错后重跑。
4. 从 HTTPS 管理端发起授权，用指定测试账号同意授权；回调成功后验证连接列表、邮件列表/读取/归档、Token refresh、拒绝授权和撤销授权后的错误恢复。账号与邮箱只在受控验收材料中脱敏记录。
5. 如启用 Pub/Sub：创建 topic，授权 `gmail-api-push@system.gserviceaccount.com` 对该 topic 发布消息；创建 push subscription，endpoint 指向 `https://<域名>/api/gmail/pubsub/webhook?token=<verification-token>`。按预检生成足够强度 token，并在 `.env` 填 `GMAIL_PUBSUB_TOPIC=projects/<项目>/topics/<topic>`、`GMAIL_PUBSUB_VERIFICATION_TOKEN`。Nginx 访问日志不记录 query string。
6. 管理端启用 watch，用测试邮件验证通知进入 worker、重复和延迟通知不重复处理、已删除邮件不阻断游标、watch 定时续订及 worker 重启恢复。topic 项目须与调用 Gmail watch 的开发项目相同，watch 至少每 7 天续订；配置依据 [Gmail 推送官方说明](https://developers.google.com/workspace/gmail/api/guides/push)。未启用 Pub/Sub 时 topic/token 同时留空，并在签字单排除实时通知范围。
7. 如服务器无法访问 Google 官方 API，提供可用网络/受控代理并完成实际可达性测试；不要把本地 mock 或合成 `credentials.json` 的 readiness 当作外部服务可用证据。

## 5. B06：充值上游配置

1. 当前保持 `RECHARGE_MODE=disabled`。先取得上游文档、专用 sandbox 和可撤销测试凭据，明确真实鉴权方式及请求/响应字段；现有 `RECHARGE_UPSTREAM_URL` 不是鉴权协议的替代品。
2. 按契约确认创建请求的 `client_task_no`、查询 `task_no`、任务号回显、撤回/关闭、卡密和邮箱匹配规则。鉴权契约若与当前适配器不同，先改代码并添加隔离回归测试。
3. 在独立 sandbox 配置 URL、允许主机、鉴权材料，执行单次履约、重复提交、丢失响应、超时后查询、查无任务、冲突编号、撤回/关闭以及最终 unknown 人工对账。
4. 验收同一意图不重复履约、冲突不会占住长期租约、unknown 不会被盲目重试或伪造终态。由业务方签字后才评估 `RECHARGE_MODE=live`；否则在发布说明中明确充值功能禁用。

### 5.1 撤回/关闭 unknown 的人工失败结案

该通道用于上游已明确确认“本次操作未执行或被拒绝”的情形，不用于仅有超时、查无回执或一次任务查询的情况。当前测试服务器为 disabled，接口不会执行 live 结案；以下供后续授权 sandbox/生产运维使用。

1. 管理员从 HTTPS 管理端登录，凭工单定位本地 `task_no`。在同一浏览器的开发者工具中请求 `GET /api/recharge/admin/tasks/<task_no>/mutations/current`，取得 `data.mutation.operation_id/action/state/started_at`。必须是需要处理的确切操作，且 `state=unknown`。
2. 向上游取得该操作不会继续执行的明确依据，保存到受控制品库。计算证据文件 SHA-256，记录工单/控制台记录编号、带时区观测时间和说明；观测时间不能早于本次操作开始。不要把真实卡密、token 或密码写入说明。
3. 向 `POST /api/recharge/admin/tasks/<task_no>/mutations/<operation_id>/reconcile` 发送 JSON；同源请求带 `Content-Type: application/json`、`X-Requested-With: XMLHttpRequest` 和当前管理员会话。请求模板：

   ```json
   {
     "confirmed": true,
     "action": "recall",
     "resolution": "not_applied",
     "basis": "上游工单明确确认此操作已拒绝且不会继续执行，具体依据见受控工单",
     "evidence": {
       "source": "provider_ticket",
       "reference": "<工单编号>",
       "sha256": "<证据文件的64位SHA256>",
       "observed_at": "<带时区的ISO8601时间>"
     },
     "upstream_task": {
       "task_no": "<已核对的上游任务编号>",
       "client_task_no": "<本地task_no>",
       "status": "processing"
     }
   }
   ```

   `action` 只能为查询到的 `recall` 或 `close`；`status` 必须是证据支持的任务状态，不能照抄示例。`source` 支持 `upstream_api/provider_console/provider_ticket`，但材料须明确证明该操作未执行。
4. 成功应为 HTTP 200：操作变为 `rejected`，充值任务保持证据对应状态，并写入 `recharge_mutation_reconciliations`。相同请求重放返回 `replayed=true`；不同结论、过期操作或竞态返回 409，应重新查询，不能直接改库。请求本身不会再向上游发起操作。
5. 后续确有业务需要时，再由原流程创建新的操作意图，使用新的 `operation_id`。旧操作的迟到成功/超时/查询结果不能覆盖新状态；`rejected` 不等于充值任务 `failed`，不要直接释放正在履约的任务锁。

新增审计表由 `init-db` 的统一建表事务创建，现有版本化迁移仍为 7 项；需要执行初始化，不能只替换 Web 进程。旧镜像不理解新的 `rejected` 操作状态；发生实际结案后回滚必须使用匹配的升级前备份，并核对外部副作用，不能只切镜像沿用新状态。

## 6. B04：数据库、备份恢复与回滚

1. 明确正式数据源、业务密钥和 RPO/RTO；从源库制作一致性副本，使用 SQLite backup API 或本项目加密备份 CLI，不能在线仅复制 `.db` 而丢失 WAL。
2. 维护窗口停止 Web/worker，保留当前镜像 digest、Compose 文件和原 `.env`；在独立受控目录生成备份密钥（0600，属主 10001）。备份密钥与 `GMAIL_TOKEN_ENCRYPTION_KEY` 分开托管。
3. 按 [服务器指南备份章节](server-deployment-guide.md) 用候选镜像执行 `deploy/backup_database.py --key-file ... backup <源库> <全新备份>`，随后 `verify <备份>`、`restore <备份> <不存在的目标库>`。源库、备份、恢复路径不得相同；错误 key/覆盖目标应失败。
4. 在副本执行 `init-db`；邮件大小写冲突时先人工核对再迁移，不自动合并。`encrypt-sensitive-data` 先盘点后 `--apply`，再次盘点三类计数均为零。保持原业务密钥，执行 `python -m app.manage validate-db` 全量校验表/列/唯一约束、SQLite 完整性、账号/历史/充值/Gmail/队列密文；该命令不初始化或修改数据库，失败退出 1。核对原 pending/unknown 记录。对恢复副本使用下列容器命令，路径按实际环境替换：

   ```bash
   sudo docker run --rm --network none --env-file /opt/google-manager/.env \
     -e DATABASE_URL=sqlite:////restore/restored.db \
     -v /opt/google-manager-restore:/restore:ro \
     '<registry>/<repository>@sha256:<digest>' python -m app.manage validate-db
   ```

   恢复目录/文件分别为 10001 所有的 0700/0600；上述检查使用默认全量模式，不受 readiness 的每表 20 条样本限制。新版本需要新表时，先在可写隔离副本执行 `init-db` 再校验，保留迁移前副本供旧版本回滚。
5. 记录备份开始/结束、恢复开始/结束、源数据量、恢复点、RPO/RTO、异地复制与保留策略。运行 `python -m deploy.recovery_drill` 只证明合成闭环，不证明正式数据量达标。
6. 回滚时先停止新写入，保留故障数据库及 WAL/SHM，验证备份后恢复到全新目录，再切换匹配的旧镜像/原密钥/恢复库。不要覆盖故障库或删除 unknown，不自动重放第三方副作用。

本轮服务器升级的受控回滚资料保存在 `/opt/google-manager-online-test-rollbacks/20260925`，独立备份密钥在 `/opt/google-manager-online-test-keys/backup.key`。测试环境回滚先进入 root shell，停止新服务，再在该目录核对旧 Compose、`.env`、加密备份和旧镜像 ID；按上述新路径恢复流程执行并重跑 smoke。它不是正式生产备份或异地备份。

## 7. B07：监控、故障演练和容量

Compose 部署不使用原生 `.venv` systemd 服务。新增模板按 `/opt/<Compose项目名>` 工作，使用系统 Docker 执行容器内 monitor。以本轮测试部署为例：

```bash
sudo install -m 644 deploy/systemd/google-manager-compose-monitor@.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/google-manager-compose-monitor@.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now google-manager-compose-monitor@google-manager-online-test.timer
sudo systemctl start google-manager-compose-monitor@google-manager-online-test.service
sudo journalctl -u google-manager-compose-monitor@google-manager-online-test.service -n 10 --no-pager
```

正式目录为 `/opt/google-manager` 时使用实例名 `google-manager`。每分钟检查 Web、worker、队列、unknown、维护状态和磁盘；失败返回非零并保留 journal。接收人/通知渠道尚未提供，因此自动发送告警未配置：在现有监控系统采集 unit 失败、容器 unhealthy、磁盘和应用计数，接入指定渠道后分别触发故障和恢复，要求接收人确认。不要仅凭 timer active 判定告警送达。

在目标环境执行 Web/worker 重启、单 worker 锁、持久化、恢复演练；使用 `python -m app.monitor --min-free-mib <大于可用空间的阈值>` 验证磁盘告警分支，避免实际写满磁盘。容量目标由业务方填写（账号量、并发、p95/p99、错误率、队列时延）；40 请求/4 并发的首页样本不等于完整业务容量验收。

## 8. HTTPS smoke 与最终放行

在已有部署目录执行（正式证书场景省略 `--ca-file`）：

```bash
sudo python3 deploy/online_smoke.py \
  --url https://127.0.0.1:8443 --env-file .env \
  --ca-file /etc/google-manager-online-test/tls/server.crt --exercise-account
```

输出仅包含检查名称、状态和延迟；不打印密码、Cookie 或业务响应。`--exercise-account` 创建一个随机 `example.test` 合成账号，验证大小写去重、锁定/解锁，再在 finally 删除；失败需核对有无遗留 `deployment-smoke-*` 测试账号。默认不创建账号，仍验证登录/退出；脚本要求充值 disabled 并且验证 HTTPS 证书。

正式发布必须使用已签字 manifest 的受控 `compose_release.py up` 入口；在 root 会话运行 preflight/compose（准备脚本会把 `.env` 设为 root:root 0600）。启动后检查 readiness、worker、monitor、TLS smoke 和错误日志。出现健康失败、业务状态不一致或不满足签署容量阈值时，停止放量，按匹配版本备份回滚。剩余任一必需证据未完成，签字单保留 NO-GO。
