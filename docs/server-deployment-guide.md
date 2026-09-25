# Google Manager 服务器生产部署指南

本文档描述当前仓库提供的 Docker Compose 和 Linux systemd 部署方式，以及两种方式共用的配置、运行检查和数据维护流程。文档中的命令均以项目根目录为工作目录；代码没有定义固定的 CPU、内存或邮箱数量基线，正式上线前应按实际账号规模和 Google API 配额自行压测。

部署组件依赖、配置生命周期、制品一致性和发布排障规范见[部署技术说明](deployment-technical-guide.md)。本次范围为 CDK 工作台，现金支付另行排期；当前是否满足发布条件以[部署前验收状态](predeployment-status-2026-09-20.md)为准。

<a id="生产修复后的部署与验收"></a>
## 生产部署与验收

以下章节是当前 web/worker 双进程实现的部署与验收步骤。

## 1. 运行架构

生产配置由 Web、任务初始化和后台 worker 三部分组成：

| 组件 | 实际入口 | 作用 |
| --- | --- | --- |
| Web | gunicorn -c deploy/gunicorn.conf.py run:app | Flask 页面、管理员 API、充值 API、OAuth 回调和健康检查。 |
| 初始化 | python -m app.manage init-db | 创建缺失表并执行版本化增量迁移，记录 `schema_migrations`、校验 Gmail/billing 必需字段。执行一次后退出；账号旧明文须另行显式迁移。 |
| worker | python -m app.worker | 从数据库队列执行 Googlemail/批量 OAuth、Gmail 同步和通知重试等后台任务；通过 instance/worker.lock 保证同一共享目录只有一个任务 worker。 |

默认数据库是 instance/accounts.db（SQLite）。应用工厂在生产模式下关闭自动建表，因此必须先完成 init-db。运行队列的 `RuntimeJob.payload` 使用 `GMAIL_TOKEN_ENCRYPTION_KEY` 加密后写入数据库；同一长期密钥还派生 live 账单凭证的 HMAC 身份键和 CDK 字段的 AES-SIV 密钥。充值任务卡密及账号邮箱采用字段隔离的确定性密文，通知邮箱采用 Fernet 密文。升级时必须保留原密钥，否则已有密文无法解密，精确查询和未决账单摘要也无法匹配。

公共充值创建和查询无需管理员登录，但撤回/关闭不是“知道卡密即可操作”：请求必须同时匹配 `task_no`、卡密、邮箱和原创建浏览器的 `recharge_context` HMAC 所有权，管理员会话可作为人工核对后的兜底。登录/退出管理员只清除管理员/OAuth 状态并保留该充值上下文。历史任务没有 `recharge_task_access` 记录、用户清 Cookie/换浏览器或轮换 `SECRET_KEY` 后，匿名危险操作会失败关闭。

Docker 构建阶段使用固定 digest 的 Python 3.11 和 Node.js 24 LTS，运行阶段使用 Ubuntu LTS；具体版本与 digest 以 Dockerfile 及最终制品证据为准。前端单独构建，最终运行镜像不包含其源码和开发依赖；保留 Googlemail 生产依赖、Playwright Chromium 和 Gunicorn。浏览器路径为 /ms-playwright。原生部署使用 Node.js 24 LTS 及发行版 python3 虚拟环境，仍需验收目标 Python 版本。生产用 GOOGLE_MANAGER_IMAGE 指定已验收镜像，记录 digest，不以可变本地标签代替发布版本。

管理员登录新增服务端会话，旧 Cookie 需要重新登录；退出、过期和密码轮换会使旧会话失效。账号密码、恢复邮箱、TOTP 和相应历史值使用 Fernet 加密，生产环境拒绝遗留明文；升级期间必须保持原 GMAIL_TOKEN_ENCRYPTION_KEY 并完成下述存量迁移。CDK 确定性字段仅支持精确等值查询，会泄露相等关系，仍须限制存储卷、备份及密钥访问权限。

## 2. 部署前准备

### 2.1 共用要求

- 需要能访问 apt 软件源、nodejs.org、PyPI、npm registry 和 Playwright 下载源；构建过程会下载系统包、Python 包、Node 包和 Chromium。Docker 宿主机生成初始随机密钥时还需可用的 `python3` 标准库，但不要求项目 `.venv`。
- 发起 Gmail OAuth 需要 Google Cloud OAuth 客户端 JSON 文件。应用从 GMAIL_CLIENT_SECRET_FILE 读取该文件，内容必须是 google-auth-oauthlib 支持的客户端配置；已有授权连接的邮件读取和 watch 续租使用数据库中的加密 token。
- credentials.json、.env、instance/ 和运行时目录都包含敏感数据，应限制文件权限并纳入备份策略。
- 当前部署模板使用 SQLite 和本地文件锁；不要在多台主机上同时运行指向同一个 instance 目录的 worker。

### 2.2 生产环境变量

生产模式由 FLASK_ENV=production 启用。应用启动时会校验下列值；Docker Compose 还会在变量缺失时直接拒绝启动：

| 变量 | 要求和默认行为 |
| --- | --- |
| FLASK_ENV | 部署时设为 production。生产配置关闭调试和自动建表。 |
| ADMIN_PASSWORD | 必填；生产环境至少 16 个字符，不能有首尾空白，也不能使用代码中列出的示例密码。 |
| SECRET_KEY | 必填；生产环境 UTF-8 编码至少 32 字节，不能使用示例密钥。 |
| GMAIL_TOKEN_ENCRYPTION_KEY | 必填；必须是 cryptography.fernet.Fernet 可解析的密钥。所有 Web/worker 实例保持一致并长期保管；用于 Gmail Token、队列 payload、账号敏感字段及历史，并派生 CDK 等值查询密文和 live 账单凭证 HMAC 的密钥。不能直接替换旧密钥，否则旧数据无法读取。 |
| GMAIL_CLIENT_SECRET_FILE | OAuth 客户端 JSON 路径。Compose 固定为 /app/credentials.json；systemd 初始化脚本写入项目根目录的绝对路径。路径非空且文件不可读时 `/health/ready` 返回失败；OAuth start/callback 需要该客户端 JSON。已有 token 的 Gmail 消息读取和 watch 续租使用 Fernet 与已保存 token，不会再次读取客户端 JSON。 |
| GMAIL_REDIRECT_URI | 可选的 OAuth 回调地址。配置后必须与 Google Cloud Console 中的授权重定向 URI 完全一致；未配置时由 Flask 根据当前请求生成 /api/gmail/oauth/callback。 |
| GMAIL_PUBSUB_TOPIC | 可选。首次调用 Gmail watch 或 worker 续租已有 active watch 时必须配置；Compose 会透传该变量。 |
| GMAIL_PUBSUB_VERIFICATION_TOKEN | 启用 `/api/gmail/pubsub/webhook` 时必填；请求须携带匹配的 `?token=`，否则生产环境返回 401/503。Compose 会透传该变量。 |
| RECHARGE_MODE | disabled（默认）或 live。生产禁止 mock 和未知值；disabled 会阻断充值交付写操作。 |
| RECHARGE_UPSTREAM_URL | `RECHARGE_MODE=live` 时必须显式配置；地址必须是 HTTPS、无 userinfo/query/fragment，并且主机命中白名单。未配置时不会回退到默认真实上游地址。充值 API 和 worker 的对账流程都会使用它。 |
| RECHARGE_UPSTREAM_ALLOWED_HOSTS | live 上游主机白名单，逗号分隔，默认 `aichong666.com`；Compose 会透传。 |
| DATABASE_URL | 可选 SQLAlchemy 数据库 URI；未设置时使用 `instance/accounts.db`。Compose 会透传该变量。 |
| PROXY | 可选，传给 Playwright 的代理服务器，例如 http://user:password@host:port。代码不会保证代理能绕过 Google 风控。 |
| TRUSTED_PROXY_CIDRS | 仅信任这些来源地址的 X-Forwarded-For/X-Forwarded-Proto。Compose 默认 172.30.8.1/32；systemd Web 单元显式固定为 127.0.0.1/32,::1/128。 |

Compose 模板向 Web/worker 容器传入 HEADLESS=true 作为默认值；任务请求可以通过受校验的 headless 选项覆盖子进程模式。模板使用镜像中的 GUNICORN_BIND=0.0.0.0:8002。原生 Gunicorn 默认绑定 127.0.0.1:8002。Gunicorn 可通过环境变量调整 GUNICORN_WORKERS（默认 2）、GUNICORN_THREADS（默认 4）、GUNICORN_TIMEOUT（默认 120 秒）和 GUNICORN_LOG_LEVEL（默认 info），Compose 会把这些变量透传到 Web/worker。

## 3. Docker Compose 部署

### 3.1 获取代码并准备配置

```bash
cd /opt
git clone https://github.com/superaddmin/Google_Manager.git google-manager
cd google-manager
```

首次部署从完整模板准备 `.env`。命令会在文件已存在时终止，避免覆盖现有密钥；模板占位符必须全部替换后才能通过预检：

```bash
umask 077
test ! -e .env || { echo '.env 已存在，请保留原密钥并手动更新配置'; exit 1; }
cp deploy/env.production.example .env
nano .env
chmod 600 .env
```

由密钥管理系统生成强随机管理员密码、至少 32 字节的应用密钥和有效 Fernet key；填写真实域名、OAuth 回调与已验收的 `repo@sha256:digest`。业务密钥必须与现有数据保持一致，备份 key 独立保管。参考[部署准备说明](deployment-preparation.md)，不要使用示例占位符或把完整配置输出到流水线日志。

将 Google Cloud 下载的 OAuth 客户端文件放在项目根目录并命名为 credentials.json。Compose 以只读方式将它挂载为 /app/credentials.json：

```bash
test -f credentials.json
sudo bash deploy/prepare-compose-host.sh
```

必须先停止 Web/worker 等写入服务再运行脚本。它拒绝缺失、符号链接或硬链接形式的 `.env`/`credentials.json`，并拒绝覆盖固定的 `10001:10001` 容器 UID/GID。先检查三个运行目录及所有已有子项，再将普通目录/文件分别收敛为 `0700/0600`、`10001:10001`，`.env` 单独设为 `root:root/0600`；嵌套符号链接、特殊文件或跨设备项在权限修改前拒绝，操作系统错误可能留下部分权限变更，应停服排错重跑。不要设为 `777` 或宿主登录用户所有：属主不匹配会导致 `initialize` 报 `sqlite3.OperationalError: unable to open database file`，或 readiness 报 `gmailConfiguration=false`。后续 preflight/Compose 必须在 root 会话执行，详见 [人工配置手册](production-manual-configuration.md)。

镜像构建上下文通过 .dockerignore 排除 .env、credentials.json、数据库和运行时目录，因此这些文件只从宿主机挂载或由 Compose 注入。

### 3.2 拉取已验收镜像和启动

以下 `up` 流程只适用于首次空的 `instance` 目录。若宿主机已有 `instance/accounts.db`，先按第 8 节停止写入、完成加密备份、结构迁移和存量敏感字段加密，再启动新版本；不得让 Compose 自动初始化后直接尝试读取旧明文。

```bash
sudo python3 deploy/preflight.py --env-file .env --project-root .
python3 deploy/compose_release.py config --env-file .env --project-root .
python3 deploy/compose_release.py pull --env-file .env --project-root .
read -r -p '独立 GO 审批记录中的 manifest SHA-256: ' approved_manifest_sha256
python3 deploy/compose_release.py up --env-file .env --project-root . --approved-manifest-sha256 "$approved_manifest_sha256"
python3 deploy/compose_release.py status --env-file .env --project-root .
```

执行目录必须包含正式准备包的 manifest、SHA256SUMS 和证据；单独源码检出或 BLOCKED 草稿不能部署。预检只有人工签字项待确认时返回 2，允许配置检查和拉取；所有其他待确认或失败均停止。启动所需摘要须来自独立 GO 审批记录，工具不代替人工签字。受控入口防止 shell 配置覆盖，并核对实际 Compose 环境，详见[部署准备说明](deployment-preparation.md)。根 Compose 没有 `build:`，生产机只能使用已验收制品；构建机器使用 `docker build --pull -t google-manager:candidate .`，或显式叠加 `docker-compose.build.yml` 进行本地构建。本机 tag 不能代替生产镜像摘要。

Compose 定义的服务和依赖顺序如下：

1. initialize 使用 python -m app.manage init-db 初始化共享的 instance 目录，成功退出。
2. google-manager 等待 initialize 成功后启动 Gunicorn。容器端口为 8002，宿主机只绑定 127.0.0.1:8002。
3. worker 等待 Web 服务启动后运行 python -m app.worker，并以 60 秒的优雅停止期限运行。

三个服务共享以下宿主目录：./instance:/app/instance、./googlemail/output:/app/googlemail/output、./googlemail/runtime:/app/googlemail/runtime。删除容器不会删除这些绑定目录；不要用删除卷的方式解决权限或升级问题。

Compose 默认网络为 172.30.8.0/24，网关 172.30.8.1。若修改网段，必须同步修改 TRUSTED_PROXY_CIDRS，并确认反向代理请求实际来自该网关。

### 3.3 日志和健康检查

```bash
docker compose logs -f google-manager worker
curl -i http://127.0.0.1:8002/health/ready
docker compose exec worker python -m app.worker --check
```

/health/ready 会检查数据库、worker 最近 30 秒心跳、Gmail 客户端文件可读性、维护任务状态、仍占用 `active_key` 的失败自动化任务、启用规则下带有执行记录的失败 Gmail 动作，以及生产敏感字段是否可按当前密钥解密；健康时返回 HTTP 200 和 ready: true，否则返回 HTTP 503。敏感数据逐批全量验证，不缓存健康结果，须按目标数据量验收耗时。首次启动需等待 worker 写入心跳。python -m app.worker --check 在心跳新鲜时返回退出码 0，否则返回 1。

容器日志使用 json-file 驱动，单文件 20 MB、最多 5 个文件。查看一次性初始化日志：

```bash
docker compose logs initialize
```

停止或重启时同时操作 Web 和 worker，避免队列没有消费者：

```bash
docker compose stop google-manager worker
docker compose up -d google-manager worker
```

## 4. Linux systemd 部署

deploy/setup-server.sh 仅接受 Ubuntu 22.04/24.04、Debian 12 的 x64/arm64 主机，需预装 Python 3.9 以上；其他系统/旧 Python 在修改系统前被拒绝。脚本必须由 root 或通过 sudo 运行，并会安装系统库、Node.js 24 LTS、Python 虚拟环境、googlemail 依赖和 Chromium，构建前端，创建 googlemanager 用户，生成 .env（仅文件不存在时）并注册 systemd 单元；它不会安装 Nginx 或 Certbot。Playwright 按发行版安装 Chromium 系统依赖，Ubuntu 24 的 t64 包名不再由脚本硬编码。

```bash
cd /opt/google-manager
chmod +x deploy/setup-server.sh
sudo bash deploy/setup-server.sh
```

脚本会把项目的 instance、googlemail/runtime、googlemail/output 和 .env 设为 googlemanager 所有，并将 Playwright 浏览器放在 /ms-playwright。脚本只执行 systemctl enable，不会自动启动服务。

如果脚本没有找到凭据文件，请在启动前安装并验证权限：

```bash
sudo install -o googlemanager -g googlemanager -m 600 /path/to/credentials.json /opt/google-manager/credentials.json
sudo -u googlemanager test -r /opt/google-manager/credentials.json
sudo -u googlemanager test -r /opt/google-manager/run.py
```

检查生成的 .env，尤其是管理员密码、SECRET_KEY、Fernet 密钥和 GMAIL_CLIENT_SECRET_FILE。启动服务前显式完成数据库初始化与敏感字段盘点；若盘点不为 0，必须先按第 8 节完成可恢复备份，再执行 `--apply`，并再次确认计数为 0。Web 单元的 `ExecStartPre` 只会幂等执行 `init-db`，不能代替存量加密：

```bash
cd /opt/google-manager
sudo -u googlemanager .venv/bin/python -m app.manage init-db
sudo -u googlemanager .venv/bin/python -m app.manage encrypt-sensitive-data
# 仅当已完成第 8 节的备份和恢复验证、且上一步存在待加密值时执行：
sudo -u googlemanager .venv/bin/python -m app.manage encrypt-sensitive-data --apply
sudo -u googlemanager .venv/bin/python -m app.manage encrypt-sensitive-data
sudo systemctl start google-manager
sudo systemctl start google-manager-worker
sudo systemctl status google-manager google-manager-worker
sudo systemctl enable google-manager google-manager-worker
sudo journalctl -u google-manager -u google-manager-worker -n 100 --no-pager
```

systemd Web 服务默认监听 127.0.0.1:8002，worker 服务的入口是 /opt/google-manager/.venv/bin/python -m app.worker。两个单元都以 googlemanager 用户运行；worker 单元要求 .env 文件存在。

如果需要手动运行初始化命令，必须使用项目虚拟环境并从项目根目录执行：

```bash
sudo -u googlemanager /opt/google-manager/.venv/bin/python -m app.manage init-db
```

## 5. Nginx 反向代理和 HTTPS

项目提供的 deploy/nginx/google-manager.conf 只代理本机 Gunicorn，不负责申请证书。模板包含 HTTP 到 HTTPS 跳转、ACME 路径、HTTPS 站点、/assets/ 缓存和 / 主代理，默认域名和证书路径都是占位值。`/health/` 仅允许 `127.0.0.1` 和 `::1`，公网请求应返回 403，避免匿名请求触发全库解密检查。监控优先通过本机采集器访问 8002；必须远程访问时，在对应 location 的 `deny all` 前显式加入固定监控源地址，并限制频率。不要将 8002 端口直接发布到公网。模板的 HTTPS server 块直接引用 /etc/letsencrypt/live/your-domain.com 下的证书，因此在证书文件存在前不要直接启用该完整配置并执行 nginx -t。

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
sudo systemctl stop nginx  # 若已有 Nginx 占用 80 端口
sudo certbot certonly --standalone -d your-domain.example
sudo cp /opt/google-manager/deploy/nginx/google-manager.conf /etc/nginx/sites-available/google-manager.conf
sudo nano /etc/nginx/sites-available/google-manager.conf
sudo ln -s /etc/nginx/sites-available/google-manager.conf /etc/nginx/sites-enabled/google-manager.conf
sudo nginx -t
sudo systemctl start nginx
sudo systemctl reload nginx
```

配置中必须替换 server_name、证书路径和域名。模板将 X-Forwarded-For 设置为 $remote_addr，并转发 X-Forwarded-Proto；应用只对 TRUSTED_PROXY_CIDRS 中的来源启用这些头。不要把任意地址加入可信代理网段，也不要将 8002 端口直接暴露到公网。
standalone 方式申请证书时必须暂时释放 80 端口；后续证书自动续期和续期时的端口协调不由仓库脚本保证，应由部署方配置并验证。

上线前受控审查 `sudo nginx -T` 的完整配置及外层代理拓扑。如果公网流量由另一本机代理转发，环回白名单不再能区分公网请求，必须在最外层阻断 `/health/` 或使用独立环回监听器；同时排除宽泛的 `set_real_ip_from`。从真实外部机器验证健康路径 403、伪造转发头无效，再从本机验证 200/503。该检查不能用公网首页 200 替代。

## 6. OAuth、Pub/Sub 和应用入口

管理页面和公共页面都由同一份前端静态资源提供：/、/recharge、/admin 以及 /admin/<path>。管理员 API 在登录会话外不可用；登录、登出、认证检查、OAuth 回调和 Pub/Sub webhook 是公开路由，充值 API 位于 /api/recharge/*，由 RECHARGE_MODE 控制是否启用。

除 Pub/Sub webhook 外，API 的写请求还会经过请求安全检查：如果请求带有 Origin，必须与当前请求的 scheme 和 host 完全匹配；写请求必须带有 X-Requested-With: XMLHttpRequest，带有 cross-site 或 same-site Sec-Fetch-Site 的请求会被拒绝。反向代理应保留正确的 Host、X-Forwarded-For 和 X-Forwarded-Proto。

production 应用为页面响应添加 CSP：`script-src`、`connect-src` 仅允许同源，并限制 object、base、frame ancestor 和表单目标。部署方若额外引入 CDN、外部脚本或跨域 API，浏览器会按当前策略阻断；不要直接放宽为 `unsafe-inline`/`unsafe-eval`，应先完成资源本地化或为具体可信来源设计最小策略并重新执行浏览器回归。

### 6.1 Gmail OAuth 回调

在 Google Cloud Console 的 OAuth 客户端中登记与实际请求完全相同的回调地址：

```text
https://your-domain.example/api/gmail/oauth/callback
```

如果设置 GMAIL_REDIRECT_URI，它必须与登记值完全一致；未设置时，应用使用当前请求的 scheme/host 生成地址。通过 Nginx 使用 HTTPS 时，要确认可信代理配置正确，否则生成的外部 URL 可能使用错误的协议。

OAuth 状态由服务端保存，默认有效期为 30 分钟且只能消费一次。回调异常时检查客户端 JSON、Fernet 密钥、域名/协议和 Google Cloud Console 的 URI，而不是重复提交已经过期的授权状态。

### 6.2 Gmail Pub/Sub

首次启用 Gmail watch 仍需管理员对每个连接调用 `POST /api/gmail/<connection_id>/watch`，且配置 `GMAIL_PUBSUB_TOPIC`。之后 worker 只为已有 `active` watch 且距离到期不足一天的记录续租。配置 GMAIL_PUBSUB_VERIFICATION_TOKEN 后，Pub/Sub 请求必须访问：

```text
POST /api/gmail/pubsub/webhook?token=<同一令牌>
```

请求体需要包含 Gmail 推送的 base64url 消息。生产环境未配置令牌时 webhook 返回 503；令牌不匹配返回 401。当前 Docker Compose 已向 initialize、Web 和 worker 透传 `GMAIL_PUBSUB_TOPIC` 与 `GMAIL_PUBSUB_VERIFICATION_TOKEN`；修改 `.env` 后应执行 `docker compose up -d --force-recreate` 重新注入容器环境，单独 `docker compose restart` 不会更新 Compose 注入的变量。systemd 部署需重启 Web 和 worker。随后用获授权的 Pub/Sub 推送验证令牌和消息格式。

## 7. 后台任务运行流程

- Web 将 Googlemail、批量 OAuth、Gmail 同步等请求写入 runtime_jobs，敏感 payload 使用 Fernet 加密。worker 每秒轮询队列，并分别处理自动化和 Gmail 任务 lane。
- python -m app.worker 启动时会把中断的 Gmail 通知/同步任务重新置为待处理；其他中断的自动化任务标记失败，并提示核对外部账号状态后手动重试。无法确认 Playwright 子进程已停止时会保留任务锁。
- Gmail 收信守护状态写入 runtime_states。queue 模式接口接受 30 至 86400 秒的轮询间隔，默认 180 秒；worker 维护循环按持久化状态执行同步、规则重试、watch 续租和（live 模式下的）充值对账。inline 模式的线程入口只在启动时校验最小 30 秒。
- 批量 OAuth 和 Googlemail 任务依赖系统 node、googlemail/src 入口和 googlemail/node_modules/playwright。Docker 与 setup 脚本会安装这些依赖；原生手动运行前必须确认 node --version 和 npm --prefix googlemail ci --omit=dev 已成功。
- 一个 worker 服务进程即可覆盖任务队列。Gunicorn 的多个 Web worker 只处理 HTTP 请求，不能替代 python -m app.worker，也不能在同一 instance 目录启动第二个任务 worker。

## 8. 备份、升级和回滚

### 8.1 备份 SQLite

先停止 Web 和 worker，让队列和数据库写入静止：

```bash
# Docker
docker compose stop google-manager worker

# systemd
sudo systemctl stop google-manager-worker google-manager
```

deploy/backup_database.py 只支持 SQLite，使用独立备份密钥和流式 AES-GCM，校验认证、大小、SHA-256 及 SQLite 完整性；目标文件必须不存在，目标目录必须已创建。旧版两位置参数已改为以下子命令。先由密钥管理系统提供权限为 0600 的独立 `/secure/keys/google-manager-backup.key`，不要复用业务 Fernet key。

Docker 部署在候选镜像内执行备份工具，宿主机无需创建 `.venv`：

```bash
sudo install -d -m 700 -o 10001 -g 10001 /secure/backups/google-manager /secure/restore/google-manager
sudo chown 10001:10001 /secure/keys/google-manager-backup.key
sudo chmod 600 /secure/keys/google-manager-backup.key
docker compose run --rm --no-deps \
  --volume /secure/keys/google-manager-backup.key:/run/secrets/database-backup-key:ro \
  --volume /secure/backups/google-manager:/backups \
  initialize python deploy/backup_database.py --key-file /run/secrets/database-backup-key \
  backup /app/instance/accounts.db /backups/accounts-before-release.gmbak
docker compose run --rm --no-deps \
  --volume /secure/keys/google-manager-backup.key:/run/secrets/database-backup-key:ro \
  --volume /secure/backups/google-manager:/backups \
  initialize python deploy/backup_database.py --key-file /run/secrets/database-backup-key \
  verify /backups/accounts-before-release.gmbak
```

Docker 恢复演练写入隔离目录中的新文件，不覆盖生产库：

```bash
docker compose run --rm --no-deps \
  --volume /secure/keys/google-manager-backup.key:/run/secrets/database-backup-key:ro \
  --volume /secure/backups/google-manager:/backups:ro \
  --volume /secure/restore/google-manager:/restore \
  initialize python deploy/backup_database.py --key-file /run/secrets/database-backup-key \
  restore /backups/accounts-before-release.gmbak /restore/accounts.db
```

systemd 部署使用安装脚本创建的项目 `.venv`，并以 `googlemanager` 用户执行：

```bash
sudo install -d -m 700 -o googlemanager -g googlemanager /secure/backups/google-manager /secure/restore/google-manager
sudo chown googlemanager:googlemanager /secure/keys/google-manager-backup.key
sudo chmod 600 /secure/keys/google-manager-backup.key
sudo -u googlemanager .venv/bin/python deploy/backup_database.py --key-file /secure/keys/google-manager-backup.key \
  backup instance/accounts.db /secure/backups/google-manager/accounts-before-release.gmbak
sudo -u googlemanager .venv/bin/python deploy/backup_database.py --key-file /secure/keys/google-manager-backup.key \
  verify /secure/backups/google-manager/accounts-before-release.gmbak
sudo -u googlemanager .venv/bin/python deploy/backup_database.py --key-file /secure/keys/google-manager-backup.key \
  restore /secure/backups/google-manager/accounts-before-release.gmbak /secure/restore/google-manager/accounts.db
```

也可通过 DATABASE_BACKUP_ENCRYPTION_KEY 环境变量提供备份 key 并省略 --key-file。脚本拒绝覆盖；密钥错误/认证失败不能发布恢复文件。`.env`、`credentials.json`、原 GMAIL_TOKEN_ENCRYPTION_KEY 和独立备份 key 均须受控保存；密钥与备份分开保管。仅有数据库文件不足以恢复完整业务。

### 8.2 升级和回滚

1. 在隔离目录准备已验收候选代码，或构建/拉取候选镜像，但不替换当前运行版本；检查 `recharge_billing_mutations`，不得带着未核对的 `pending`/`unknown` 操作直接轮换 Fernet key 或升级摘要算法。
2. 阻断新请求并停止 worker/Web。Docker 用候选镜像、systemd 用当前 `.venv` 加隔离目录中的候选版本备份脚本，按 8.1 备份数据库并立即 verify；同时受控备份 `.env`、OAuth 客户端和原 Fernet 密钥。备份验证失败即终止升级。
3. 激活候选代码。Docker 继续使用候选镜像；systemd 执行 `sudo bash deploy/setup-server.sh` 更新依赖、完整前端产物和两个单元，再执行 `systemctl daemon-reload`。安装脚本不会替换已有 `.env`，也不会自动启动服务。
4. 运行 `python -m app.manage init-db` 应用版本化迁移，核对新增表与租约字段，保留原操作号和未决记录。
5. 运行 `python -m app.manage encrypt-sensitive-data` 盘点；仅在可恢复备份已验证的前提下加 `--apply`，再次盘点必须显示 `account_values`、`history_values`、`recharge_task_values` 三个计数均为 0。Compose 使用 `docker compose run --rm --no-deps initialize python -m app.manage ...`，systemd 使用 `sudo -u googlemanager .venv/bin/python -m app.manage ...`。
6. 先启动 Web、再启动 worker，并检查 `/health/ready`、`python -m app.worker --check` 和 `python -m app.monitor`。健康检查失败时先查看 worker 日志、数据库表、凭据可读性和维护失败计数。监控 timer 仅产生日志/退出码，须另外验收告警接收链路。
7. 回滚时先停止两个服务，保留当前数据库副本，用 restore 命令将备份解密到隔离新文件并校验；确认代码版本、schema、摘要算法与原密钥匹配后，再按维护流程恢复生产路径。备份工具不会覆盖生产库。旧代码不能读取新增加密字段，禁止仅回滚代码；外部副作用仍须逐项对账。

### 8.3 密钥轮换和未决账单操作

`GMAIL_TOKEN_ENCRYPTION_KEY` 现在同时承担 Fernet 加密和账单身份根密钥职责，不能只替换环境变量后重启：

1. 保持旧版本和旧密钥运行，暂停新的账单取消/恢复入口。
2. 对 `recharge_billing_mutations` 中所有 `pending`/`unknown` 记录调用获授权的上游查询核对，确认进入 `done`；无法确认时保持 `RECHARGE_MODE=disabled`，不要删除记录。
3. 备份数据库、旧 `.env` 和旧 Fernet key，在隔离环境验证所有 Gmail Token 与队列 payload 可解密。
4. 只有未决操作已结清且具备凭证重加密/摘要迁移方案时才能轮换；仓库当前没有自动密钥轮换工具。

若某环境曾运行使用 `SECRET_KEY` 或其他临时值生成账单摘要的旧版本，旧记录不能由新摘要自动命中。必须使用旧代码和旧密钥先结清上游状态，再升级；删除未知记录会绕过反向操作门禁，可能造成重复取消/恢复。

正式基线此前没有 `recharge_billing_mutations`，首次 `init-db` 会建完整表；已有中间版本缺列由版本化迁移补齐，并将没有租约的旧 pending 留待对账。必须先停 Web/worker、备份及验证目标库副本，不得删表/重建或直接改状态绕过未知操作。人工对账须通过管理员专用接口并归档上游证据。

## 9. 常见问题

### 容器启动即退出或 Web 报配置错误

确认 .env 中 ADMIN_PASSWORD、SECRET_KEY、GMAIL_TOKEN_ENCRYPTION_KEY 已填写并满足生产校验，且 RECHARGE_MODE 不是 mock。Compose 使用变量必填检查前三个密钥，缺失时不会创建 Web/worker。

### /health/ready 返回 503

等待 worker 心跳写入后再次请求；随后检查 JSON 中的 gmailConfiguration、maintenance、automation、gmailActions 和 rechargeConfiguration 字段。live 模式下若 `rechargeConfiguration=false`，检查上游 URL、HTTPS 和主机白名单；若 worker 不在线，检查 worker 日志和 instance/worker.lock。

### Googlemail 能力显示 NODE_NOT_FOUND 或 DEPENDENCIES_NOT_INSTALLED

在运行 Web/worker 的同一环境中执行 node --version，并确认 googlemail/node_modules/playwright 存在。Docker 重新构建镜像；原生环境从项目根目录执行：

```bash
cd /opt/google-manager/googlemail
npm ci --omit=dev
PLAYWRIGHT_BROWSERS_PATH=/ms-playwright npx playwright install chromium
```

### OAuth 回调状态无效

OAuth 状态有效期为 30 分钟且只能使用一次。核对回调 URI、反向代理协议头、系统时间、客户端 JSON 和 GMAIL_TOKEN_ENCRYPTION_KEY，并重新开始一次授权流程。

### 充值接口返回 503

生产默认 RECHARGE_MODE=disabled，这是代码的安全门禁。只有完成真实上游契约验收后才能设置 live；production 应用会拒绝 mock，development 可显式设置 `RECHARGE_MODE=mock` 运行沙箱，TestingConfig 固定为 mock。

### 撤回/关闭返回 403

确认请求来自创建任务的原浏览器会话，并同时提交查询结果中的 `task_no`、完整卡密和目标邮箱。换浏览器、清 Cookie、`SECRET_KEY` 轮换或历史任务没有所有权记录时，匿名请求按设计失败关闭；由管理员登录后核对任务目标再处理，不要通过删除 grant 或放宽路由校验恢复访问。

### 数据在重启后消失

Docker 必须保留 ./instance:/app/instance 绑定目录；原生必须确认服务使用同一个项目目录。不要执行 docker compose down -v 或删除 instance，先检查挂载和目录属主。

## 10. 上线验收

部署后至少完成以下检查：

```bash
curl -f http://127.0.0.1:8002/health/ready
# Docker
docker compose exec worker python -m app.worker --check

# systemd
sudo -u googlemanager /opt/google-manager/.venv/bin/python -m app.worker --check
```

再通过实际域名验证 TLS、生产 CSP、管理员登录/退出后充值所有权保持、历史任务管理员兜底、OAuth 回调、Gmail API 凭据读取、worker 重启恢复、Pub/Sub（如启用）、两张新增表、账单 unknown 对账、备份完整性和充值模式门禁。容量、代理可用性、Google 风控结果、上游服务鉴权、KYC URL 的外部抓取行为和真实上游履约不由仓库内本地测试保证，必须使用获授权的测试账号和测试卡密单独验收。
