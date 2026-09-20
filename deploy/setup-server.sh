#!/usr/bin/env bash
# ==============================================================================
# Google Manager Linux VPS 一键初始化与部署脚本 (Ubuntu 22.04/24.04 & Debian 12)
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

if [ ! -r /etc/os-release ]; then
    echo -e "${RED}[错误] 无法识别操作系统，仅支持 Ubuntu 22.04/24.04 与 Debian 12${NC}"
    exit 1
fi
. /etc/os-release
case "${ID:-}:${VERSION_ID:-}" in
    ubuntu:22.04|ubuntu:24.04|debian:12)
        ;;
    *)
        echo -e "${RED}[错误] 不支持的操作系统: ${PRETTY_NAME:-unknown}${NC}"
        echo -e "${RED}仅支持 Ubuntu 22.04/24.04 与 Debian 12${NC}"
        exit 1
        ;;
esac

if ! command -v python3 &> /dev/null \
    || ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo -e "${RED}[错误] 部署前必须预装 Python 3.9 或更高版本${NC}"
    exit 1
fi

NODE_VERSION="24.21.0"
case "$(uname -m)" in
    x86_64)
        NODE_ARCH="x64"
        NODE_SHA256="fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6"
        ;;
    aarch64|arm64)
        NODE_ARCH="arm64"
        NODE_SHA256="6ad1325edbdb5649c379b75a237147a666c95d4f9ae8d340fef2d1575d289ad2"
        ;;
    *)
        echo -e "${RED}[错误] 不支持的 Node.js CPU 架构: $(uname -m)${NC}"
        exit 1
        ;;
esac

# 1. 更新 apt 软件源并安装跨发行版基础工具。浏览器系统库由 Playwright
# 根据当前 Ubuntu/Debian 版本安装，避免硬编码 t64 前后的包名。
echo -e "\n${YELLOW}[1/6] 安装基础系统工具...${NC}"
apt-get update -y
apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    python3 \
    python3-venv \
    python3-pip \
    xz-utils

# 2. 安装经过校验的 Node.js 24 LTS
echo -e "\n${YELLOW}[2/6] 检查/安装 Node.js ${NODE_VERSION} LTS 环境...${NC}"
if ! command -v node &> /dev/null || [[ $(node -v) != "v${NODE_VERSION}" ]]; then
    NODE_TEMP_DIR=$(mktemp -d)
    trap 'rm -rf "${NODE_TEMP_DIR:-}"' EXIT
    NODE_ARCHIVE="node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"
    curl --fail --silent --show-error --location \
        "https://nodejs.org/dist/v${NODE_VERSION}/${NODE_ARCHIVE}" \
        --output "${NODE_TEMP_DIR}/${NODE_ARCHIVE}"
    echo "${NODE_SHA256}  ${NODE_TEMP_DIR}/${NODE_ARCHIVE}" | sha256sum --check --status || {
        echo -e "${RED}[错误] Node.js 安装包 SHA-256 校验失败${NC}"
        exit 1
    }
    tar --extract --xz --file "${NODE_TEMP_DIR}/${NODE_ARCHIVE}" \
        --directory /usr/local --strip-components=1
    rm -rf "${NODE_TEMP_DIR}"
    trap - EXIT
fi
if [[ $(node -v) != "v${NODE_VERSION}" ]]; then
    echo -e "${RED}[错误] Node.js 版本不符合要求，实际为 $(node -v)${NC}"
    exit 1
fi
echo -e "${GREEN}Node.js 版本: $(node -v), npm 版本: $(npm -v)${NC}"

# 3. 设置 Python 虚拟环境并安装 requirements.txt
echo -e "\n${YELLOW}[3/6] 初始化 Python 虚拟环境与依赖安装...${NC}"
cd "${PROJECT_DIR}"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt gunicorn==23.0.0
echo -e "${GREEN}Python 核心与依赖安装完成${NC}"

# 4. 初始化 googlemail 子模块依赖与 Playwright Chromium
echo -e "\n${YELLOW}[4/6] 初始化 Playwright 自动化环境与浏览器内核...${NC}"
cd "${PROJECT_DIR}/googlemail"
export PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
npm ci --omit=dev
./node_modules/.bin/playwright install --with-deps chromium
cd "${PROJECT_DIR}"

# 5. 编译前端静态资源（如果需要）
echo -e "\n${YELLOW}[5/6] 检查并编译前端资源...${NC}"
if [ -d "frontend" ]; then
    cd frontend
    npm ci
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
    RANDOM_ADMIN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    umask 077
    cat <<EOF > "${ENV_FILE}"
# Google Manager 生产环境配置
FLASK_ENV=production
ADMIN_PASSWORD=${RANDOM_ADMIN}
SECRET_KEY=${RANDOM_SECRET}
GMAIL_TOKEN_ENCRYPTION_KEY=${RANDOM_FERNET}
GMAIL_CLIENT_SECRET_FILE=${PROJECT_DIR}/credentials.json
# GMAIL_REDIRECT_URI=https://your-domain.com/api/gmail/oauth/callback
# PROXY=http://username:password@proxy-ip:port
HEADLESS=true
RECHARGE_MODE=disabled
EOF
    chmod 600 "${ENV_FILE}"
    echo -e "${GREEN}已为您生成专属 .env 配置文件: ${ENV_FILE}${NC}"
    echo -e "${YELLOW}⚠️ 请务必修改 .env 中的 ADMIN_PASSWORD 与 PROXY (住宅代理地址)${NC}"
fi
chmod 600 "${ENV_FILE}"

# 复制 systemd 服务
SERVICE_FILE="/etc/systemd/system/google-manager.service"
id -u googlemanager >/dev/null 2>&1 || useradd --system --create-home googlemanager
chown googlemanager:googlemanager "${ENV_FILE}"
chown -R googlemanager:googlemanager "${PROJECT_DIR}/instance" "${PROJECT_DIR}/googlemail/runtime" "${PROJECT_DIR}/googlemail/output"
chmod -R go-rwx "${PROJECT_DIR}/instance" "${PROJECT_DIR}/googlemail/runtime" "${PROJECT_DIR}/googlemail/output"
if [ -f "${PROJECT_DIR}/credentials.json" ]; then
    chown googlemanager:googlemanager "${PROJECT_DIR}/credentials.json"
    chmod 600 "${PROJECT_DIR}/credentials.json"
fi
chmod -R a+rX /ms-playwright
sed -e "s|/opt/google-manager|${PROJECT_DIR}|g" "${PROJECT_DIR}/deploy/systemd/google-manager.service" > "${SERVICE_FILE}"
sed -e "s|/opt/google-manager|${PROJECT_DIR}|g" "${PROJECT_DIR}/deploy/systemd/google-manager-worker.service" > /etc/systemd/system/google-manager-worker.service
sed -e "s|/opt/google-manager|${PROJECT_DIR}|g" "${PROJECT_DIR}/deploy/systemd/google-manager-monitor.service" > /etc/systemd/system/google-manager-monitor.service
install -m 0644 "${PROJECT_DIR}/deploy/systemd/google-manager-monitor.timer" /etc/systemd/system/google-manager-monitor.timer
chmod 0644 "${SERVICE_FILE}" \
    /etc/systemd/system/google-manager-worker.service \
    /etc/systemd/system/google-manager-monitor.service

systemctl daemon-reload
systemctl enable google-manager google-manager-worker google-manager-monitor.timer

echo -e "\n${GREEN}================================================================${NC}"
echo -e "${GREEN}🎉 Google Manager 服务器部署就绪！${NC}"
echo -e "${GREEN}================================================================${NC}"
echo -e "启动服务命令:     ${YELLOW}systemctl start google-manager google-manager-worker google-manager-monitor.timer${NC}"
echo -e "查看运行状态:     ${YELLOW}systemctl status google-manager${NC}"
echo -e "实时日志追踪:     ${YELLOW}journalctl -u google-manager -f${NC}"
echo -e "应用监听端口:     ${YELLOW}http://127.0.0.1:8002${NC}"
echo -e "\n提示: 若需通过公网域名访问，请参考 deploy/nginx/google-manager.conf 配置 Nginx 反代！"
