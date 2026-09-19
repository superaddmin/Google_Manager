# ==============================================================================
# Google Manager 生产级 Docker 镜像
# 包含 Python 3.11 + Node.js 20 + Playwright Headless Chromium 及 Linux 系统底层运行库
# ==============================================================================

FROM python:3.11-slim-bookworm

LABEL maintainer="Google Manager Team"
LABEL description="Google Manager with Playwright & Gmail API Unattended Daemon"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HEADLESS=true \
    FLASK_ENV=production

# 1. 安装基础系统依赖、中文字体与 Playwright 所需的底层图形/音频链接库
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    build-essential \
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
    fonts-ipafont-gothic \
    && rm -rf /var/lib/apt/lists/*

# 2. 安装 Node.js 20.x
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node --version && npm --version

WORKDIR /app

# 3. 复制依赖描述文件并安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# 4. 复制并安装 googlemail 子模块依赖与 Playwright 浏览器二进制
COPY googlemail/package*.json ./googlemail/
WORKDIR /app/googlemail
RUN npm ci --omit=dev \
    && npx playwright install chromium

WORKDIR /app

# 5. 复制工程全部代码
COPY . .

# 6. 构建前端静态资源（如果尚未构建）
RUN if [ -d "frontend" ] && [ ! -f "static/index.html" ]; then \
        cd frontend && npm ci && npm run build && cd .. ; \
    fi

# 7. 创建数据持久化目录与权限
RUN mkdir -p instance googlemail/runtime googlemail/output /ms-playwright

EXPOSE 8002

# 8. 默认通过 Gunicorn 启动
CMD ["gunicorn", "-c", "deploy/gunicorn.conf.py", "run:app"]
