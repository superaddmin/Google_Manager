#!/usr/bin/env bash
# ==============================================================================
# Google Manager Linux VPS 一键初始化与部署脚本 (Ubuntu 20.04/22.04/24.04 & Debian 11/12)
# ==============================================================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}================================================================${NC}"
echo -e "${BLUE}          Google Manager 生产服务器一键部署安装向导              ${NC}"
echo -e "${BLUE}================================================================${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[错误] 请使用 root 用户或 sudo 执行此脚本！${NC}"
    exit 1
fi

PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
echo -e "${GREEN}>>> 当前项目根目录: ${PROJECT_DIR}${NC}"

# 1. 更新 apt 软件源并安装基础系统工具
echo -e "\n${YELLOW}[1/6] 安装底层系统依赖、C编译链与中文开源字体...${NC}"
apt-get update -y
apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    build-essential \
    git \
    python3 \
    python3-venv \
    python3-pip \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    fonts-liberation \
    fonts-wqy-zenhei \
    fonts-ipafont-gothic

# 2. 安装 Node.js 20.x LTS
echo -e "\n${YELLOW}[2/6] 检查/安装 Node.js 20.x 环境...${NC}"
if ! command -v node &> /dev/null || [[ $(node -v) != v20* ]]; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
fi
echo -e "${GREEN}Node.js 版本: $(node -v), npm 版本: $(npm -v)${NC}"

# 3. 设置 Python 虚拟环境并安装 requirements.txt
echo -e "\n${YELLOW}[3/6] 初始化 Python 虚拟环境与依赖安装...${NC}"
cd "${PROJECT_DIR}"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt gunicorn
echo -e "${GREEN}Python 核心与依赖安装完成${NC}"

# 4. 初始化 googlemail 子模块依赖与 Playwright Chromium
echo -e "\n${YELLOW}[4/6] 初始化 Playwright 自动化环境与浏览器内核...${NC}"
cd "${PROJECT_DIR}/googlemail"
npm install --production
npx playwright install chromium
cd "${PROJECT_DIR}"

# 5. 编译前端静态资源（如果需要）
echo -e "\n${YELLOW}[5/6] 检查并编译前端资源...${NC}"
if [ -d "frontend" ] && [ ! -f "static/index.html" ]; then
    cd frontend
    npm install
    npm run build
    cd "${PROJECT_DIR}"
fi

# 6. 配置生产环境 .env 模版与 Systemd 服务
echo -e "\n${YELLOW}[6/6] 配置环境变量文件与 Systemd 守护服务...${NC}"
mkdir -p "${PROJECT_DIR}/instance" "${PROJECT_DIR}/googlemail/runtime" "${PROJECT_DIR}/googlemail/output"

ENV_FILE="${PROJECT_DIR}/.env"
if [ ! -f "${ENV_FILE}" ]; then
    RANDOM_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    RANDOM_FERNET=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    cat <<EOF > "${ENV_FILE}"
# Google Manager 生产环境配置
FLASK_ENV=production
ADMIN_PASSWORD=ChangeMeStrongPassword123!
SECRET_KEY=${RANDOM_SECRET}
GMAIL_TOKEN_ENCRYPTION_KEY=${RANDOM_FERNET}
GMAIL_CLIENT_SECRET_FILE=${PROJECT_DIR}/credentials.json
# GMAIL_REDIRECT_URI=https://your-domain.com/api/gmail/oauth/callback
# PROXY=http://username:password@proxy-ip:port
HEADLESS=true
EOF
    chmod 600 "${ENV_FILE}"
    echo -e "${GREEN}已为您生成专属 .env 配置文件: ${ENV_FILE}${NC}"
    echo -e "${YELLOW}⚠️ 请务必修改 .env 中的 ADMIN_PASSWORD 与 PROXY (住宅代理地址)${NC}"
fi

# 复制 systemd 服务
SERVICE_FILE="/etc/systemd/system/google-manager.service"
sed -e "s|/opt/google-manager|${PROJECT_DIR}|g" "${PROJECT_DIR}/deploy/systemd/google-manager.service" > "${SERVICE_FILE}"

systemctl daemon-reload
systemctl enable google-manager

echo -e "\n${GREEN}================================================================${NC}"
echo -e "${GREEN}🎉 Google Manager 服务器部署就绪！${NC}"
echo -e "${GREEN}================================================================${NC}"
echo -e "启动服务命令:     ${YELLOW}systemctl start google-manager${NC}"
echo -e "查看运行状态:     ${YELLOW}systemctl status google-manager${NC}"
echo -e "实时日志追踪:     ${YELLOW}journalctl -u google-manager -f${NC}"
echo -e "应用监听端口:     ${YELLOW}http://127.0.0.1:8002${NC}"
echo -e "\n提示: 若需通过公网域名访问，请参考 deploy/nginx/google-manager.conf 配置 Nginx 反代！"
