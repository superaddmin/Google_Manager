# 🔐 GoogleManager

```
https://github.com/superaddmin/Google_Manager.git
```

<p align="center">
  <b>一款专业的谷歌账号资产管理系统</b>
  <br>
  <sub>支持批量导入、2FA验证码生成、账号状态管理、修改历史追踪等功能</sub>
</p>

***

## ✨ 功能特性

### 🔑 账号管理

- **批量导入** - 支持多种分隔符格式（`|`、`——`、`----`、`--`）快速导入账号
- **单个导入** - 表单式单个账号录入
- **智能搜索** - 按邮箱或备注内容搜索账号
- **状态筛选** - 筛选已售出/未售出账号

### 🛡️ 2FA 验证码

- **一键生成** - 点击即可获取当前 TOTP 验证码
- **实时倒计时** - 显示验证码剩余有效时间
- **进度条显示** - 直观展示验证码过期进度

### 📋 快捷复制

- 单独复制邮箱、密码、恢复邮箱
- **一键复制全部** - 快速复制完整账号信息

### 📊 出售状态管理

- 标记账号为"已售出"/"未售出"
- 切换回未售出需二次确认
- 状态变更历史记录

### 📜 修改历史

- 记录密码、2FA密钥、恢复邮箱、出售状态的每次修改
- 抽屉式面板展示修改历史
- 修改前后对比显示

### ✉️ Googlemail 自动化

- 从主页直接选择本地账号并启动 Googlemail 任务
- 支持无头模式、操作延迟、账号间隔、恢复邮箱池和运行超时配置
- 支持任务进度查询、取消、失败计数和人工复核计数
- 自动把已完成任务的新 2FA 密钥及已确认恢复邮箱写回账号历史

### 🔒 安全特性

- **管理员密码保护** - 需密码登录才能访问系统
- **7天登录有效期** - 登录后7天内无需重复登录
- **IP 封禁机制** - 连续3次密码错误，封禁该 IP 24小时
- **时间窗口校验** - 登录请求携带短时效盐值，不替代密码验证与 HTTPS

### 🎨 界面设计

- **暗色/亮色模式** - 支持一键切换主题
- **响应式布局** - 适配不同屏幕尺寸
- **现代化 UI** - 采用 TailwindCSS 打造精美界面

***

## 📸 界面预览

<details>
<summary>点击展开预览图</summary>

### 登录页面

!\[登录页面]<img width="2550" height="1292" alt="image" src="https://github.com/user-attachments/assets/0e3faef6-37ff-4a46-b03b-3a4c396eb30b" />

### 账号列表

!\[账号列表]<img width="2550" height="1292" alt="image" src="https://github.com/user-attachments/assets/6662353d-6f92-4edd-b007-f3aa94b5bf3f" />

### 批量导入

!\[批量导入]<img width="2550" height="1292" alt="image" src="https://github.com/user-attachments/assets/1889262e-5510-4a20-8b8f-faaf3e58d030" /> <img width="2550" height="1292" alt="image" src="https://github.com/user-attachments/assets/5132326f-9019-46fd-9d39-1e784b8b69cb" />

### 修改历史

!\[修改历史]<img width="2550" height="1292" alt="image" src="https://github.com/user-attachments/assets/a0befb6a-269c-4c8c-8320-5a98c4a34c54" />

</details>

***

## 🚀 快速开始

### 环境要求

- Python 3.8+
- Node.js 20.19+（包含 Googlemail 自动化和浏览器回归测试）
- npm 或 yarn

### 安装步骤

1. **克隆项目**

```bash
git clone https://github.com/superaddmin/Google_Manager.git
cd Google_Manager
```

1. **安装后端依赖**

```bash
pip install -r requirements.txt
```

1. **安装前端依赖**

```bash
cd frontend
npm install
```

1. **构建前端**

```bash
npm run build
cd ..
```

1. **启动服务**

```powershell
$env:ADMIN_PASSWORD = '<YOUR_ADMIN_PASSWORD>'
python run.py
```

1. **访问系统**
   打开浏览器访问 `http://localhost:8002`

***

## 📁 项目结构

```
GOO成品号管理/
├── app/                      # 后端应用
│   ├── models/               # 数据模型
│   │   ├── account.py        # 账号模型
│   │   └── account_history.py # 历史记录模型
│   ├── routes/               # API 路由
│   │   └── api.py
│   ├── services/             # 业务逻辑
│   │   ├── account_service.py
│   │   └── auth_service.py   # 认证服务
│   └── utils/                # 工具函数
│       └── totp.py           # TOTP 生成
├── frontend/                 # 前端应用
│   ├── src/
│   │   ├── components/       # React 组件
│   │   ├── hooks/            # 自定义 Hooks
│   │   └── services/         # API 服务
│   └── ...
├── static/                   # 静态文件（构建输出）
├── instance/                 # 数据库文件
├── run.py                    # 启动脚本
└── requirements.txt          # Python 依赖
```

***

## ⚙️ 配置说明

### 配置管理员密码

通过环境变量配置管理员密码，不要把凭证写入源码：

```powershell
$env:ADMIN_PASSWORD = '<YOUR_ADMIN_PASSWORD>'
```

生产环境和开发环境缺少该配置时应用会拒绝启动；测试环境使用隔离的测试 fixture。

### 修改服务端口

编辑 `run.py`：

```python
app.run(host='127.0.0.1', port=8002)  # 修改 port 值
```

默认只监听本机回环地址；调试开关遵循所选 Flask 配置，不再覆盖 production 的设置。只有明确需要局域网访问时才修改监听地址，不要向外网暴露开发调试服务。

登录封禁默认按连接来源 IP 计数，不直接信任请求中的 `X-Forwarded-For`。反向代理部署时，应按真实代理层数配置 Werkzeug `ProxyFix`，并限制后端端口只能由可信代理访问，避免伪造转发头绕过封禁。

### 配置生产会话密钥

production 配置要求通过环境变量提供至少 32 字节的随机会话密钥，缺少或过短时应用会停止启动：

```powershell
$env:SECRET_KEY = '<RANDOM_SECRET>'
```

### 修改登录有效期

修改 `app/config.py` 的 `PERMANENT_SESSION_LIFETIME`。前端以 `/api/auth/check` 返回的服务端会话状态为准，不再依赖浏览器本地登录时间：

```python
PERMANENT_SESSION_LIFETIME = timedelta(days=7)
```

***

## 🔧 开发指南

### 前端开发模式

```bash
cd frontend
npm run dev
```

### 前端构建

```bash
npm run build
```

### 数据库迁移

项目使用 SQLite，新增表结构时运行对应的迁移脚本：

```bash
python migrate_history.py  # 历史记录表迁移
```

### 本地回归验证

在仓库根目录执行以下命令；浏览器测试使用合成账号和拦截的 API，不读写实际账号数据库，也不会真实登录 Google 或修改账号安全设置：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
npm --prefix frontend run build
node --test tests/account-import.test.mjs tests/frontend-ui.test.mjs tests/googlemail-local-copy.test.mjs
npm --prefix googlemail test
```

前端浏览器测试复用 `googlemail/` 已安装的 Playwright；若 Chromium 不可用，在该目录执行 `npx playwright install chromium`。项目未配置独立的前端 lint、格式化或类型检查命令。

***

### Googlemail 本地集成

`googlemail/` 是从本机 `F:\Googlemail` 受控复制的源码快照，由当前仓库直接管理，不使用 Git submodule。账号文件、浏览器会话、运行输出、日志、覆盖率和依赖目录不会纳入版本控制。

登录主页后打开 `Googlemail` 视图，可选择账号、设置运行参数并启动、查询或取消任务。Flask 通过受控 Node.js 子进程调用本地 Googlemail，每次任务的输入与输出保存在忽略目录 `googlemail/runtime/tasks/<task-id>/`，HTTP 响应只返回任务状态和计数。

任务失败或取消后也会刷新已同步的账号信息。如果结果文件含损坏、未知账号或缺失密钥的记录，任务会返回 `RESULT_SYNC_FAILED` 并保留 `result.txt` 供人工恢复；该文件含敏感信息，不要上传或提交。

testing 配置会关闭实际 Googlemail 执行；development/production 配置默认开启。启动前需先安装本地 Node.js 依赖：

```powershell
Push-Location .\googlemail
try {
    npm ci
    npm test
    npm run test:startup
} finally {
    Pop-Location
}
```

复制与后续同步流程见 [Googlemail 本地复制集成执行计划](SUBMODULE_INTEGRATION_PLAN.md)。

***

## 📝 导入格式

支持以下分隔符格式：

```
邮箱|密码|恢复邮箱|2FA密钥|备注
邮箱——密码——恢复邮箱——2FA密钥——备注
邮箱----密码----恢复邮箱----2FA密钥----备注
邮箱--密码--恢复邮箱--2FA密钥--备注
```

其中：

- **邮箱** 和 **密码** 为必填
- **恢复邮箱**、**2FA密钥**、**备注** 可选
- 第五列国家或地区（如 `UnitedStates`）保存为备注；同一批次可混用上述分隔符，每行使用一种。
- 解析错误会显示原始行号，修正全部错误后才能提交；错误提示不会回显原始密码或密钥。
- 密码按原文保存；2FA 密钥自动去除空白。前四个字段不要包含本行使用的分隔符，备注中则可以使用。
- 已存在的邮箱会跳过，不覆盖原记录；本次无新增账号时会保留导入文本，请勿将重复提示当作服务异常。
- 单次最多导入 500 个账号；邮箱和非空恢复邮箱必须符合基本邮箱格式，后端会再次校验。

***

## 🛠️ 技术栈

| 类别    | 技术           |
| ----- | ------------ |
| 前端框架  | React 18     |
| UI 样式 | TailwindCSS  |
| 图标库   | Lucide React |
| 构建工具  | Vite         |
| 后端框架  | Flask        |
| 数据库   | SQLite       |
| ORM   | SQLAlchemy   |

***

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 提交 Pull Request

***

## 📄 开源协议

本项目采用 [MIT License](LICENSE) 开源协议。

***

## ⭐ 如果觉得有用，欢迎 Star！

<p align="center">
  Made with ❤️ for Google Account Management
</p>
---

## 🔎 代码审计与部署说明（2026-09-15）

### 当前审计结论

本项目由 Flask 后端、React/Vite 前端和 Node.js + Playwright 的 Googlemail 自动化子模块组成。当前发现的主要风险与限制如下：

- **生产跨域策略过宽**：`app/__init__.py` 当前使用全局 `CORS(app)`，生产环境应限制为实际前端域名，并配合 HTTPS。
- **敏感数据明文存储**：账号密码、恢复邮箱和 2FA 密钥直接写入 SQLite 及导出文件；生产环境必须限制文件权限、禁止公开下载，并规划数据库加密/密钥托管。
- **任务状态为进程内存**：Googlemail 任务管理器和进程对象保存在当前 Python 进程，重启或多 worker 部署会丢失任务状态；暂不适合直接水平扩展。
- **SQLite 适合单机小规模**：并发写入、备份、故障恢复能力有限；多人或高频任务场景建议迁移 PostgreSQL，并补充正式迁移流程。
- **自动化依赖真实浏览器环境**：Googlemail 子模块需要 Node.js、Playwright Chromium、可用网络/DNS、稳定系统时间和足够的临时目录空间；验证码、风控或人工复核不能保证无人值守完成。
- **测试尚未在当前环境收集**：2026-09-15 执行 `python -m pytest -q` 时缺少 `pyotp` 和 `flask_sqlalchemy`，需先安装 `requirements.txt` 后重新验证。

以上问题不等同于已经发生的线上故障，但应作为生产上线前的整改清单。

### 苹果项目能否部署到服务器？

1. **苹果客户端（iOS/macOS App）**：不能把 iOS App 本身部署成 Linux/Windows 服务器进程。iOS 构建和签名通常需要 macOS、Xcode、Apple Developer 账号；客户端应发布到 App Store、TestFlight 或企业/私有分发渠道。
2. **本项目后端和 Web 管理端**：可以部署到服务器。浏览器访问不依赖苹果系统；推荐 Linux x86_64/ARM64 服务器运行 Flask、数据库和可选的 Node 自动化服务。若必须构建或签名苹果客户端，则需要 macOS（实体 Mac 或合规的 macOS CI 环境）。

### 推荐生产配置

- **仅 Web/API**：Ubuntu 22.04+/Debian 12+，2 vCPU、4 GB RAM、40 GB SSD，Python 3.10+，Nginx/Caddy，PostgreSQL 14+。
- **包含 Playwright 自动化**：建议 4 vCPU、8 GB RAM、80 GB SSD；安装 Node.js 20 LTS、Playwright Chromium 及系统依赖，并为任务输出和截图设置配额。
- **安全**：固定域名和 TLS；生产设置 `FLASK_ENV=production`、长度不少于 32 字节的 `SECRET_KEY`、高强度 `ADMIN_PASSWORD`；限制 CORS、脱敏日志、限制导出文件访问。
- **进程**：不要用 Flask 内置开发服务器公网运行；Web/API 使用 Gunicorn 等 WSGI 服务，自动化任务独立运行。当前任务状态在进程内存中，不能直接多 worker 或水平扩展。

当前仓库没有 iOS 原生工程，也没有 PostgreSQL/Gunicorn/队列部署配置；因此现阶段“苹果部署”应理解为苹果设备访问服务器上的 Web/API，而不是在服务器运行 iOS App。

### 上线前验证

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
cd frontend; npm ci; npm run build; cd ..
cd googlemail; npm ci; npx playwright install --with-deps chromium; npm test
```
## ✉️ Gmail API 收件箱自动管理

项目现已接入 Gmail API 第一阶段能力：

- OAuth 2.0 授权并保存 Gmail 账号连接。
- 使用 `gmail.modify` 权限读取收件箱、查看邮件详情、标记已读和归档。
- 前端新增“Gmail 收件箱”页面，支持账号选择、Gmail 查询语法搜索和邮件详情查看。
- refresh token 使用 Fernet 加密后保存到 `gmail_connections` 表，不与账号密码或 2FA 密钥混用。

### Gmail Cloud 配置

1. 在 Google Cloud 创建项目并启用 Gmail API。
2. 配置 OAuth 同意屏幕和 OAuth Client ID（Web application）。
3. 添加授权回调地址：`https://你的域名/api/gmail/oauth/callback`。
4. 将下载的 client secret JSON 放到服务器受限目录。
5. 配置以下环境变量：

```powershell
$env:GMAIL_CLIENT_SECRET_FILE = 'C:\secrets\gmail-client-secret.json'
$env:GMAIL_TOKEN_ENCRYPTION_KEY = '<由 Fernet.generate_key() 生成的密钥>'
$env:GMAIL_PUBSUB_TOPIC = 'projects/<项目>/topics/<主题>'
$env:GMAIL_PUBSUB_VERIFICATION_TOKEN = '<随机高强度校验令牌>'
```

生产环境会强制检查 `GMAIL_TOKEN_ENCRYPTION_KEY`。丢失该密钥后，已保存的 Gmail 授权 Token 无法恢复，需要重新授权；不要将 client secret、Token 或密钥提交到 Git。

### API 入口

- `GET /api/gmail/oauth/start`：生成 OAuth 授权地址。
- `GET /api/gmail/oauth/callback`：完成授权并保存连接。
- `GET /api/gmail/connections`：列出已授权邮箱（不返回 Token）。
- `GET /api/gmail/<connectionId>/messages?q=...`：查询收件箱。
- `GET /api/gmail/<connectionId>/messages/<messageId>`：读取邮件详情。
- `PATCH /api/gmail/<connectionId>/messages/<messageId>/read`：标记已读。
- `PATCH /api/gmail/<connectionId>/messages/<messageId>/archive`：归档邮件。
- `GET /api/gmail/<connectionId>/labels`：读取 Gmail 标签列表。
- `PATCH /api/gmail/<connectionId>/messages/<messageId>/labels`：增加或移除邮件标签。
- `GET|POST /api/gmail/<connectionId>/rules`：查询或创建自动规则。
- `PATCH|DELETE /api/gmail/rules/<ruleId>`：更新或删除自动规则。
- `POST /api/gmail/<connectionId>/rules/run`：手工执行规则，支持 `dryRun` 和指定 `messageIds`。
- `GET /api/gmail/task-logs`：查询规则任务日志和待确认动作。
- `POST /api/gmail/task-logs/<logId>/confirm`：人工确认并执行动作。
- `POST /api/gmail/task-logs/<logId>/reject`：拒绝待确认动作。
- `POST /api/gmail/<connectionId>/watch`：为账号注册 Gmail `watch`。
- `POST /api/gmail/pubsub/webhook?token=...`：接收 Pub/Sub 推送并触发规则执行。

规则动作支持增加/移除标签、标记已读/未读、归档/取消归档和移入/移出垃圾箱；设置 `requiresConfirmation=true` 后，动作会先进入任务日志，必须由管理员确认或拒绝。Pub/Sub webhook 仅接受带 `GMAIL_PUBSUB_VERIFICATION_TOKEN` 的请求；Gmail `watch` 到期前应由外部定时任务重新注册。
