# Google Manager 服务器生产部署与挂机收信全套运维指南

本文档专门指导如何将 **Google Manager (包含 Playwright 自动授权与 Gmail API 挂机收信系统)** 完整部署至生产服务器（海外 VPS / 云服务器 / Docker 容器环境），实现 24 小时无人值守全自动批量授权与挂机收信。

---

## 1. 架构与资源要求

### 1.1 服务器硬件推荐
- **CPU**：1 核心（轻量使用）或 2 核心及以上（批量高并发）
- **内存 (RAM)**：
  - **最低要求**：1GB RAM + 2GB Swap（虚拟内存，防止 Chromium 启动瞬时 OOM）
  - **推荐配置**：2GB RAM 或以上
- **操作系统**：Ubuntu 20.04 / 22.04 / 24.04 LTS、Debian 11 / 12、CentOS 8/9 Stream 等主流 Linux 发行版
- **网络出站**：必须能够正常无阻断访问 Google 登录端点 (`accounts.google.com`) 与 Gmail API (`gmail.googleapis.com`)。

### 1.2 Google 风控与住宅代理核心说明（极其重要）
> [!IMPORTANT]
> **机房 IP 风险**：各大云厂商机房（如 AWS, DigitalOcean, 阿里云海外, Vultr, 搬瓦工等）所属 ASN 在 Google 风控库中均被标记为“数据中心 IP”。若直接在海外机房 VPS 上使用浏览器登录 Google，极易触发强制短信验证或不可跳过的图形验证码（CAPTCHA）。
> **解决方案**：在服务器配置文件 `.env` 或 Docker Compose 中配置优质住宅级代理：
> ```bash
> PROXY=http://username:password@residential-proxy.net:port
> ```
> Playwright 会自动通过该代理节点驱动浏览器与 Google 通信，彻底规避机房 IP 风控！

---

## 2. 方案一：Docker 容器化部署（强烈推荐）

Docker 镜像已内置 Python 3.11、Node.js 20、Playwright Chromium 浏览器及其全部 Linux 底层依赖库，是保持跨平台环境一致的最优选择。

### 步骤 1：克隆工程并准备密钥
```bash
cd /opt
git clone https://github.com/your-repo/Google_Manager.git google-manager
cd google-manager
```

### 步骤 2：生成并配置环境变量
在工程根目录下创建或编辑 `.env` 文件：
```bash
# 生产运行模式
FLASK_ENV=production

# 管理员后台密码（必填）
ADMIN_PASSWORD=YourComplexPassword_2026!

# Flask Session 密钥（至少 32 字符）
SECRET_KEY=c3f8e910245a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c

# Gmail OAuth 令牌加密密钥（32 字节 Base64 Fernet 密钥）
GMAIL_TOKEN_ENCRYPTION_KEY=dGhpcy1pcy1hLXRlc3QtZmVybmV0LWtleS0zMi1ieXRlcyE=

# Google API 客户端凭证文件路径（挂载到容器中）
GMAIL_CLIENT_SECRET_FILE=/app/credentials.json

# 住宅代理地址（防 Google 登录风控）
PROXY=http://user:pass@pr.oxylabs.io:7777

# 可选：如果配置了独立公网域名反向代理
GMAIL_REDIRECT_URI=https://mail.yourdomain.com/api/gmail/oauth/callback
```

> [!TIP]
> 快速生成强随机密钥命令：
> ```bash
> python3 -c "import secrets; print(secrets.token_hex(32))"
> python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```

### 步骤 3：放入 Google API 授权文件
将从 Google Cloud Console 下载的 OAuth 2.0 Web Client 密钥文件重命名为 `credentials.json` 放置于工程根目录下：
```bash
ls -l credentials.json
```

### 步骤 4：一键构建并启动 Docker
```bash
docker compose up -d --build
```

### 步骤 5：查看容器运行日志
```bash
docker compose logs -f google-manager
```
看到类似如下日志即代表部署成功：
```
Google Manager Gunicorn 服务正在启动...
Listening at: http://0.0.0.0:8002
```

---

## 3. 方案二：Linux VPS 原生 Systemd 部署

如果您偏好直接在宿主机上运行服务，可以使用我们提供的一键自动化初始化脚本。

### 步骤 1：执行一键安装脚本
```bash
cd /opt/google-manager
chmod +x deploy/setup-server.sh
sudo bash deploy/setup-server.sh
```
该脚本会自动完成：
1. 更新 apt 并安装 Playwright 所需的全部 Linux 图形、字体及动态链接库 (`libnss3`, `libgbm1`, `fonts-wqy-zenhei` 等)。
2. 安装 Node.js 20 LTS。
3. 创建 Python 虚拟环境 `.venv` 并安装依赖库与 Gunicorn。
4. 安装 `googlemail` 依赖与 Chromium 浏览器二进制内核。
5. 自动生成专属 `.env` 配置文件并注册 `google-manager.service` Systemd 服务。

### 步骤 2：配置凭据与代理
根据提示编辑生成的 `.env` 文件，填入您的密码与代理：
```bash
nano /opt/google-manager/.env
```
确保放入 `credentials.json`。

### 步骤 3：启动并管理服务
```bash
# 启动服务
systemctl start google-manager

# 查看运行状态
systemctl status google-manager

# 开机自启（脚本已默认开启）
systemctl enable google-manager

# 查看实时日志
journalctl -u google-manager -f
```

---

## 4. Nginx 反向代理与 HTTPS 配置

为了保证公网访问安全，建议通过 Nginx 进行反向代理并配置免费的 Let's Encrypt SSL 证书。

### 步骤 1：安装 Nginx 与 Certbot
```bash
apt-get install -y nginx certbot python3-certbot-nginx
```

### 步骤 2：复制配置模板
```bash
cp /opt/google-manager/deploy/nginx/google-manager.conf /etc/nginx/sites-available/google-manager.conf
nano /etc/nginx/sites-available/google-manager.conf
```
将文件中的 `your-domain.com` 替换为您的实际域名。

### 步骤 3：启用站点并签发证书
```bash
ln -s /etc/nginx/sites-available/google-manager.conf /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx

# 自动签发 Let's Encrypt 证书
certbot --nginx -d your-domain.com
```

---

## 5. Google Cloud Console 授权回调配置

为了使全自动批量授权和手动授权能在服务器公网或域名下正常跳转回调，需要在 Google Cloud Console 中正确配置：

1. 登录 [Google Cloud Console](https://console.cloud.google.com/)。
2. 进入 **APIs & Services (API 与服务)** -> **Credentials (凭据)**。
3. 点击您的 **OAuth 2.0 Client IDs** 条目。
4. 在 **Authorized redirect URIs (已获授权的重定向 URI)** 中，追加您的生产服务器回调地址：
   - 如果使用域名：`https://your-domain.com/api/gmail/oauth/callback`
   - 如果直接使用 IP：`http://YOUR_SERVER_IP:8002/api/gmail/oauth/callback`
   - 本地回环（备用）：`http://localhost:8002/api/gmail/oauth/callback`
5. 点击保存。

---

## 6. 全自动“批量授权 + 挂机收信”操作手册

### 6.1 一键批量全自动授权
1. 浏览器打开您的服务器面板：`https://your-domain.com`，输入管理员密码登录。
2. 点击顶部导航栏进入 **Gmail 收件箱**。
3. 点击右上角 **批量自动授权** 按钮：
   - 弹窗会自动列出所有本地已录入、但尚未绑定 Gmail 凭据的账号。
   - 默认开启“后台无头模式 (Headless)”。
   - 填入您的住宅代理地址（若已在服务器 `.env` 配置，可直接留空继承环境变量）。
4. 点击 **开始批量自动授权**：
   - 系统将在服务器后台顺序拉起 Playwright 实例。
   - 自动进入登录流程 -> 自动填入账号密码 -> 自动根据 Secret 计算 TOTP 2FA -> 自动填入恢复邮箱校验 -> 自动跳过“Google 未验证应用”警告 -> 自动勾选权限并完成回调闭环。
   - 前端弹窗支持实时查看进度条与滚动运行日志。

### 6.2 24H 挂机收信守护进程 (GmailSyncDaemon)
完成批量授权后，即可开启 24 小时无人值守挂机收信：
1. 在 Gmail 收件箱页面点击 **挂机收信中控** 展开控制台。
2. 选择合适的轮询周期（建议 **3 分钟** 或 **5 分钟**）。
3. 点击 **开启挂机收信**：
   - 守护进程将在后台以极低的系统资源消耗（纯 Python REST API，不占用浏览器与显存）周期性轮询所有已授权邮箱。
   - 自动归集所有最新验证码至 **安全防盗中控台 (OTP Hub)**，免去频繁登网页被 Google 风控封号的风险。
   - 自动扫描各邮箱是否被恶意设置了“隐蔽转发出站”或“静默删除过滤器”，一旦发现立即告警。

### 6.3 资源占用与性能测试指标
- **浏览器执行期 (授权阶段)**：单并发消耗约 120MB - 180MB RAM，持续 20-40 秒完成一个账号，完成后即刻销毁上下文，无内存泄露。
- **稳态挂机期 (收信阶段)**：内存占用维持在约 **30MB - 45MB RAM**，单台 1C1G VPS 可稳定挂机维护 300+ 邮箱的实时邮件与验证码同步。

---

## 7. 常见故障排查 (Troubleshooting)

### Q1: Playwright 报错 `Executable doesn't exist at ...` 或缺少 `libX11` 等动态库
- **原因**：Linux 环境缺少 Chromium 底层图形库。
- **解决**：在服务器上运行：
  ```bash
  cd /opt/google-manager/googlemail
  npx playwright install --with-deps chromium
  ```

### Q2: 批量授权时日志提示 `OAuth 自动授权超时` 或遇到 Google 验证码阻拦
- **原因**：使用了数据中心原生机房 IP，Google 拒绝无验证登录。
- **解决**：在 `.env` 中正确配置住宅级代理：`PROXY=http://user:pass@ip:port`。

### Q3: 回调提示 `Gmail OAuth 状态无效`
- **原因**：服务端与外部回调未对齐或超期（默认有效时间 30 分钟）。
- **解决**：检查服务器系统时钟是否准确（`timedatectl`），检查 Google Cloud Console 中回调重定向 URI 是否与请求地址精确匹配。

### Q4: 容器内重启后 SQLite 数据消失？
- **解决**：检查 `docker-compose.yml` 中的 volumes 映射，确保 `./instance:/app/instance` 目录正确映射到宿主机持久化卷。
