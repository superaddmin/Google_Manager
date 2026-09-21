# 🔐 GoogleManager

```
https://github.com/superaddmin/Google_Manager.git
```

<p align="center">
  <b>专业谷歌账号资产管理、批量授权与集中邮箱防盗控制系统</b>
  <br>
  <sub>支持 Playwright 批量自动授权、24H 无人值守挂机收信、集中邮箱防盗体检、隐蔽转发检测、跨邮箱验证码聚合、Docker/VPS 生产部署</sub>
</p>

***

## ✨ 功能特性

### 🔑 账号管理与批量操作

- **批量导入** - 支持多种分隔符格式（`|`、`——`、`----`、`--`）快速导入账号，自动校验邮箱格式与去重。
- **单个录入** - 表单式单个账号录入，支持自定义备注与初始状态。
- **智能搜索与状态筛选** - 支持按邮箱、备注进行模糊搜索，支持全部 / 已售出 / 未售出状态即时筛选。
- **批量管理操作** - 支持全选/多选账号，支持一键批量删除、批量标记出售状态（已售出/未售出）、批量修改备注。
- **多格式安全导出** - 支持根据当前搜索和出售状态，将账号资产导出为 `CSV`、`TXT` 或 `JSON` 格式，支持锁定账号脱敏隔离。

### 🛡️ 2FA 验证码与凭证安全

- **一键生成 TOTP** - 点击秒级生成 6 位 2FA 动态验证码。
- **实时倒计时与过期进度条** - 直观展示当前 TOTP 验证码有效剩余秒数及过期进度。
- **独立/一键复制** - 单独复制邮箱、密码、恢复邮箱，或一键复制完整账号组合信息。
- **密码显隐防窥保护** - 列表默认掩码显示密码，独立按钮切换显示/隐藏；切换筛选或搜索时自动重置为掩码状态，避免敏感凭证泄漏。

### 🤖 Playwright 全自动批量挂机授权 (Batch OAuth Auto-Authorizer)

- **无头浏览器自动化流水线** - 驱动 Chromium 依次自动输入账号、密码、根据 TOTP 密钥动态计算 2FA、处理恢复邮箱挑战。
- **自动突破未验证警告** - 智能识别 Google OAuth “未验证此应用”提示，自动点击高级 -> 前往不安全。
- **自动权限勾选与回调闭环** - 自动识别未选中的权限复选框并全部勾选，点击继续并拦截回调，自动完成 Token 换取与 Fernet 强加密入库。
- **服务端 State 彻底解耦** - 独创 `OAuthStateManager` 服务端状态注册与缓存机制，彻底解决无头沙箱与独立 Session 隔离冲突。
- **实时进度看板与动态日志** - 前端弹窗支持实时展示当前授权账号、进度条、成功/失败统计与滚动运行日志。

### ⚡ 24H 无人值守挂机收信守护者 (GmailSyncDaemon)

- **零浏览器开销轻量守护** - 授权完成后纯走 Google 官方 REST API（单进程约 30MB 内存占用），不启动任何浏览器，适合 1C1G/1C2G VPS 长期常驻。
- **周期性自动轮询** - 支持 1/3/5/10 分钟自定义轮询周期，自动拉取所有已授权邮箱的未读邮件。
- **集中验证码 (OTP) 自动归集** - 自动正则识别邮件中的 6 位数字代码与 Google 代码，无需登录网页，在防盗面板集中直接复制。
- **自动化防盗排查** - 轮询过程中自动排查隐蔽转发与恶意过滤器，发现被盗迹象立即告警。

### 🚨 集中邮箱防盗与安全中控 (SecurityCenterView)

专为大规模批量管理 Google 邮箱资产打造的防盗与反劫持防护体系：

- **全库防盗健康度雷达** - 综合评估账号安全风险分（0-100分），自动归类为安全良好、中度风险、高危被盗风险、已应急锁定。
- **Gmail 隐蔽外部转发排查 (Stealth Forwarding Scanner)** - 通过官方 API 自动检测 Gmail 后台是否存在非法的 `Auto-Forwarding` 自动外发、已配置转发白名单，防止黑客暗中盗取后续邮件。
- **黑客恶意过滤规则扫描 (Malicious Filter Detection)** - 自动扫描静默销毁、删除或绕过收件箱的恶意过滤规则，识破黑客拦截验证码并销毁安全告警的手段。
- **跨邮箱验证码与安全告警聚合流 (Central OTP & Security Feed)** - 无需操作员逐个在本地浏览器登录账号，系统后台集中拉取各邮箱最新验证码，智能正则提取 Google、Telegram、Twitter/X、OpenAI、Discord 等平台 OTP 验证码，支持一键快捷复制，杜绝因分散登录导致的浏览器指纹风控与 Cookie 被盗。
- **一键防盗锁号与应急 SOP** - 针对异常或受威胁账号，提供一键应急锁定，阻止导出和信息流转，并在修改历史中详细记录安全处置动作。

### 📊 资产统计看板 (DashboardView)

- **全局资产概览** - 统计账号总量、Pro 账号数与标准账号数分布。
- **出售与在库率** - 实时计算未售出库存数（在库率）与已售出数（售出率）。
- **安全覆盖率透视** - 2FA 密钥覆盖率、安全恢复邮箱覆盖率及缺失数量直观图表展示。
- **近期趋势分析** - 动态统计最近 14 天的入库新增走势与出售流转走势柱状对比。

### 🔒 核心防御与安全机制

- **管理员密码鉴权** - 管理端 API 与管理界面受可吊销的服务端会话保护；面向客户的 CDK 创建/查询接口按独立契约开放。
- **防暴力破解 IP 封禁** - 连续 3 次输入错误密码，自动封禁该来源 IP 24 小时。
- **短时效动态盐值校验** - 登录请求携带时间戳动态盐值校验。
- **凭证强加密存储** - Gmail API 凭证强制要求配置 `GMAIL_TOKEN_ENCRYPTION_KEY` 采用 Fernet 对称强加密落盘；该长期密钥同时派生 live 账单操作的凭证摘要键，因此所有实例必须保持一致，轮换前必须先结清 `pending`/`unknown` 操作。

***

## 📁 项目结构

```
Google_Manager/
├── app/                              # 后端应用核心
│   ├── models/                       # SQLAlchemy 数据模型
│   │   ├── account.py                # 账号资产模型
│   │   ├── account_history.py        # 修改历史模型
│   │   ├── gmail_connection.py       # Gmail OAuth 连接模型
│   │   ├── gmail_rule.py             # 自动化规则模型
│   │   ├── gmail_task_log.py         # 规则执行日志与人工审核模型
│   │   ├── gmail_watch.py            # Gmail Pub/Sub 订阅模型
│   │   ├── googlemail_task.py        # Playwright 任务状态模型
│   │   ├── recharge_task_access.py   # 充值任务创建会话所有权摘要
│   │   └── recharge_billing_mutation.py # 账单变更幂等与租约状态
│   ├── routes/                       # 路由控制器
│   │   ├── api.py                    # RESTful API（含批量授权、挂机守护、防盗、账号）
│   │   └── main.py                   # 静态页面与 SPA 渲染入口
│   ├── services/                     # 业务服务层
│   │   ├── account_service.py        # 账号增删改查、统计与批量处理
│   │   ├── auth_service.py           # 登录鉴权、IP 封禁与盐值校验
│   │   ├── batch_oauth_service.py    # 批量 Playwright OAuth 自动授权调度器
│   │   ├── email_poller.py           # 24H 挂机收信守护进程 (GmailSyncDaemon)
│   │   ├── gmail_service.py          # Gmail 官方 API 对接、OAuthStateManager 与加密
│   │   ├── gmail_rule_service.py     # 邮件自动化规则与任务流
│   │   ├── googlemail_service.py     # 2FA 自动化轮换子进程适配器
│   │   └── security_service.py       # 集中防盗体检、隐蔽转发扫描与 OTP 聚合
│   ├── utils/                        # 辅助工具
│   ├── config.py                     # 环境配置类
│   └── __init__.py                   # 应用工厂函数
├── frontend/                         # 前端 React SPA
│   ├── src/
│   │   ├── components/               # React UI 视图组件
│   │   │   ├── AccountListView.jsx   # 账号列表视图（支持批量与显隐防窥）
│   │   │   ├── DashboardView.jsx     # 统计分析看板
│   │   │   ├── SecurityCenterView.jsx# 集中邮箱防盗与安全中控台
│   │   │   ├── GmailInboxView.jsx    # Gmail 收件箱、批量授权弹窗与挂机中控
│   │   │   ├── GooglemailView.jsx    # 2FA 轮换自动化任务面板
│   │   │   ├── ImportView.jsx        # 批量导入视图
│   │   │   └── LoginPage.jsx         # 登录界面
│   │   ├── services/api.js           # 前端 API 请求封装
│   │   └── App.jsx                   # 前端根组件与主导航路由
├── googlemail/                       # Node.js + Playwright 自动化子模块
│   ├── src/                          # 自动化核心源码
│   │   ├── oauth-authorizer.mjs      # Playwright Google OAuth 自动授权核心
│   │   ├── batch-oauth-worker.mjs    # 批量授权 CLI 工作入口
│   │   ├── google-automator.mjs      # 2FA 自动修改与登录逻辑
│   │   ├── totp.mjs                  # TOTP 计算工具
│   │   └── redaction.mjs             # 日志敏感数据脱敏工具
├── deploy/                           # 生产服务器部署套件
│   ├── gunicorn.conf.py              # Gunicorn 生产多线程 WSGI 配置
│   ├── setup-server.sh               # Linux VPS 一键部署 Shell 脚本
│   ├── systemd/
│   │   └── google-manager.service    # Systemd 系统服务单元
│   └── nginx/
│       └── google-manager.conf       # Nginx 反向代理与 SSL 模板
├── docs/                             # 架构与运维文档
│   ├── server-deployment-guide.md    # 服务器生产部署指南
│   └── centralized-mailbox-security-guide.md # 集中邮箱防盗管理架构白皮书
├── Dockerfile                        # 生产级 Docker 镜像构建文件
├── docker-compose.yml                # Docker Compose 编排文件
├── instance/                         # SQLite 数据库运行目录
├── static/                           # 前端生产打包静态资源目录
├── tests/                            # Python、Node.js 与 Playwright 回归测试
│   ├── test_batch_oauth.py           # 批量 OAuth 授权单元测试
│   ├── test_email_poller.py          # 挂机收信守护进程测试
│   ├── test_api.py                   # 账号与鉴权后端测试
│   ├── test_security_service.py      # 安全防盗与 OTP 提取后端测试
│   ├── test_gmail_service.py         # Gmail 加密与解析测试
│   ├── test_gmail_automation.py      # 邮件规则与自动化测试
│   ├── test_googlemail_service.py    # Playwright 适配层测试
│   └── frontend-ui.test.mjs          # Playwright 前端回归测试
├── requirements.txt                  # Python 依赖清单
└── run.py                            # 本地开发启动入口
```

***

## 🚀 服务器生产部署快速上手

生产部署提供了 **Docker 容器化部署（强烈推荐）** 与 **Linux VPS 原生 Systemd 部署** 两种方案：

### 方案 A：Docker 容器化部署（推荐）

```bash
# 1. 克隆代码
git clone https://github.com/superaddmin/Google_Manager.git /opt/google-manager
cd /opt/google-manager

# 2. 从模板准备配置；已有部署必须保留原密钥
umask 077
test ! -e .env || { echo '.env 已存在，请保留原密钥'; exit 1; }
cp deploy/env.production.example .env
# 在受控编辑器中填写强随机密钥、实际域名和已验收镜像 repo@sha256:digest
nano .env

# 3. 放入 Google Cloud 下载的 credentials.json
cp /path/to/your/credentials.json ./credentials.json

# 4. 首次空数据目录启动容器
sudo install -d -m 700 -o 10001 -g 10001 instance googlemail/runtime googlemail/output
sudo chown 10001:10001 credentials.json
sudo chmod 600 credentials.json
python3 deploy/preflight.py --env-file .env --project-root .
python3 deploy/compose_release.py config --env-file .env --project-root .
python3 deploy/compose_release.py pull --env-file .env --project-root .
# 由负责人从独立的 GO 审批记录提供；禁止自取包内 hash 冒充批准
read -r -p '已批准的 manifest SHA-256: ' approved_manifest_sha256
python3 deploy/compose_release.py up --env-file .env --project-root . --approved-manifest-sha256 "$approved_manifest_sha256"
```

生产执行目录必须是完整发布准备包，单独 clone 的源码不含发布 manifest，不能直接上线。预检返回 2 时，只有人工签字一项待确认才可继续配置检查和拉取；其他失败或待确认均停止。签字完成后才允许初始化和启动。以上仅适用于首次空数据目录安装。生产 Compose 不含构建入口，必须指定已验收的不可变镜像。发布前完成[部署准备说明](docs/deployment-preparation.md)和[发布签字单](docs/release-signoff-template.md)。已有 `instance/accounts.db` 的环境必须先按部署指南停止写入、完成加密备份、结构迁移和存量敏感字段加密，不能直接执行 `up` 跳过升级步骤。

容器启动并按部署指南完成健康与安全验收后，使用两个独立入口：

- C 端充值门户：`https://你的域名/` 或 `https://你的域名/recharge`
- Google 邮箱管理后台：`https://你的域名/admin`

充值门户面向终端用户开放，不要求管理员登录；账号库、Gmail 收件箱、批量授权和安全中心仅在 `/admin` 管理后台提供。

### 方案 B：Linux VPS (Ubuntu/Debian) 原生一键部署

```bash
cd /opt/google-manager
chmod +x deploy/setup-server.sh
sudo bash deploy/setup-server.sh
```

脚本将安装固定补丁版本的 Node.js 24 LTS、Python 虚拟环境、Playwright 浏览器与 Linux 图形依赖，并注册、启用 Web/worker 的 systemd 单元；脚本不会自动启动业务服务。启动前必须按部署指南依次执行 `init-db`、存量敏感字段盘点/迁移和健康检查。

详细服务器运维、Nginx 反代、Let's Encrypt 证书签发与代理防风控技巧详见：
👉 **[服务器生产部署指南](docs/server-deployment-guide.md)**

***

## 📡 RESTful API 接口总览

### 1. 批量 OAuth 2.0 自动授权
- `POST /api/gmail/batch-authorize`：启动指定账号集合的一键自动授权任务。
- `GET /api/gmail/batch-authorize/status`：获取当前运行中或最新的批量授权任务进度与日志。
- `POST /api/gmail/batch-authorize/cancel`：取消当前执行中的批量授权任务。

### 2. 24H 挂机收信守护进程 (GmailSyncDaemon)
- `GET /api/gmail/daemon/status`：获取后台挂机收信守护进程的运行状态、轮询指标与日志。
- `POST /api/gmail/daemon/start`：启动后台挂机收信守护进程（可配置轮询周期）。
- `POST /api/gmail/daemon/stop`：停止后台挂机收信守护进程。
- `POST /api/gmail/daemon/sync-now`：立即触发一次全量邮箱同步与安全扫描。

### 3. 集中安全与防盗
- `GET /api/security/overview`：全库防盗安全态势总览与健康指数。
- `GET /api/security/accounts?filter=...`：带风险分与安全维度的账号列表。
- `GET /api/security/forwarding-audit`：扫描已授权 Gmail 账号的隐蔽外部自动转发与恶意规则。
- `GET /api/security/central-otps?limit=10`：跨所有邮箱集中提取最新验证码与安全告警流。
- `POST /api/security/accounts/<id>/lock`：一键应急锁号并阻断导出。
- `POST /api/security/accounts/<id>/unlock`：解除账号应急锁定。

### 4. 账号与资产管理
- `GET /api/accounts?search=...`：获取账号列表，支持关键词模糊搜索。
- `POST /api/accounts/batch`：批量导入账号（单次上限 500 个）。
- `POST /api/accounts/batch-delete`：批量删除选中账号。
- `POST /api/accounts/batch-sold`：批量标记出售状态。
- `POST /api/accounts/batch-remark`：批量设置账号备注。
- `GET /api/accounts/export?format=csv&sold=all`：多格式安全导出账号。
- `GET /api/stats`：获取看板统计数据。

***

## 🔎 代码审计与测试状态（2026-09-20）

整改后的验证结果、已关闭问题和剩余生产门禁统一记录在 [2026-09-20 整改与验收记录](docs/release-remediation-2026-09-20.md)。仓库内自动化测试和构建结果只证明对应隔离场景，不能替代目标 Linux 镜像、TLS、真实 Google 服务、充值上游、容量、恢复和告警送达验收。

充值中心当前是 CDK 履约与订阅管理工作台，不是现金充值/余额系统；本次发布范围也仅包含 CDK 工作台，现金支付另行排期。真实上游鉴权协议和生产 sandbox 仍须按整改记录验收，不能仅凭本地 mock 或隔离测试开启 live。

充值中心最新的前后端契约、问题复现与修复、测试证据及上线限制见 [2026-09-21 全链路审查](docs/recharge-release-audit-2026-09-21.md)。新增浏览器链路测试会启动隔离 Flask、临时数据库和本地 HTTP 上游，不使用真实卡密或支付服务。

### 全量自动化回归命令

```powershell
# 1. 运行 Python 全量测试（以本次命令实际输出为准）
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"

# 2. 构建前端生产资源
npm --prefix frontend run build

# 3. 运行 Node.js 基础测试与 Googlemail 自动化测试
node --test tests/account-import.test.mjs tests/googlemail-local-copy.test.mjs tests/api-service.test.mjs
npm --prefix googlemail test

# 4. 运行 Playwright 浏览器端全量回归测试
node --test tests/frontend-ui.test.mjs tests/recharge-ui.test.mjs tests/recharge-flow.test.mjs
```

***

## 📄 开源协议

本项目采用 [MIT License](LICENSE) 开源协议。

## 生产部署与验收要点

- 部署架构、配置约束、发布顺序和排障方法见 [部署技术说明](docs/deployment-technical-guide.md)。
- C 端 `/`、`/recharge` 与管理端 `/admin` 分流；默认禁用真实充值，完成上游验收后才启用 `RECHARGE_MODE=live`。
- 撤回/关闭必须同时匹配 `task_no`、完整卡密、目标邮箱和原创建会话的所有权摘要；历史任务没有所有权记录时匿名操作失败关闭，只能由管理员核对处理。
- 同时运行 Web 与 `python -m app.worker`：任务、取消请求、收信开关持久化，执行中断的账号自动化不自动重放。
- Compose 的 initialize 服务先创建包括 `recharge_task_access`、`recharge_billing_mutations` 在内的新增表并补齐 Gmail 执行租约字段，再启动 Web/worker；原生安装通过 ExecStartPre 执行 `python -m app.manage init-db`。
- `/health/ready` 检查数据库、worker 心跳和生产敏感密文；Nginx 默认仅允许本机访问 `/health/`，远程监控应使用受控采集器或明确的地址白名单。仅页面返回 200 不代表可交付。
- 备份恢复、专用用户权限、可信代理、上线验收和回滚步骤见 [生产部署指南](docs/server-deployment-guide.md#生产修复后的部署与验收)；是否具备上线条件以[整改与验收记录](docs/release-remediation-2026-09-20.md)为准。
