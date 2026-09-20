# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

# Build tools use immutable official Node.js and Python image digests. The
# runtime is kept on the supported Ubuntu LTS baseline so application binaries
# are not coupled to the build stages' OS security lifecycle.
ARG NODE_IMAGE=node:24.21.0-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6
ARG PYTHON_IMAGE=python:3.11-slim-trixie@sha256:da047cb8f9d1d98e5c070f5300ba9f7274e33b8fc0e5be5ed88740aed1b95ba9
ARG RUNTIME_IMAGE=ubuntu:26.04@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78
# Bump this value after reviewing Ubuntu security advisories so the apt layer
# cannot silently reuse an older package index from the Docker build cache.
ARG UBUNTU_SECURITY_REFRESH=2026-09-20

FROM ${NODE_IMAGE} AS frontend-builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm --prefix frontend ci --ignore-scripts
COPY frontend ./frontend
RUN npm --prefix frontend run build

FROM ${NODE_IMAGE} AS googlemail-deps
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
WORKDIR /build
COPY googlemail/package.json googlemail/package-lock.json ./googlemail/
RUN npm --prefix googlemail ci --omit=dev --ignore-scripts \
    && npm cache clean --force

FROM ${PYTHON_IMAGE} AS browser-runtime
ARG TARGETPLATFORM
WORKDIR /browser-install
COPY deploy/install_browser.py deploy/browser-artifacts.json ./
RUN python install_browser.py \
        --platform "${TARGETPLATFORM}" \
        --destination /opt/google-manager/chrome

FROM ${PYTHON_IMAGE} AS python-deps
WORKDIR /build
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0 \
    && python -m pip uninstall -y pip setuptools wheel \
    && rm -f /usr/local/lib/python3.11/lib-dynload/_tkinter*.so

FROM ${RUNTIME_IMAGE} AS runtime

ARG UBUNTU_SECURITY_REFRESH

LABEL maintainer="Google Manager Team"
LABEL description="Google Manager with Playwright and Gmail API unattended worker"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH=/opt/google-manager/chrome/chrome \
    HEADLESS=true \
    FLASK_ENV=production \
    GUNICORN_BIND=0.0.0.0:8002

# Runtime-only OS libraries for CPython and Chromium. Compiler tools, curl, and
# frontend development packages remain outside; apt/dpkg metadata stays intact
# for patching and vulnerability traceability.
RUN test -n "${UBUNTU_SECURITY_REFRESH}" \
    && apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libasound2t64 \
        libatk-bridge2.0-0t64 \
        libatk1.0-0t64 \
        libatspi2.0-0t64 \
        libbz2-1.0 \
        libcairo2 \
        libcups2t64 \
        libdbus-1-3 \
        libdrm2 \
        libexpat1 \
        libffi8 \
        libgbm1 \
        libgdbm-compat4t64 \
        libgdbm6t64 \
        libglib2.0-0t64 \
        liblzma5 \
        libncursesw6 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libreadline8t64 \
        libsqlite3-0 \
        libssl3t64 \
        libstdc++6 \
        libuuid1 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        fonts-ipafont-gothic \
        fonts-liberation \
        fonts-wqy-zenhei \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Only the language runtimes and production modules are copied from build
# stages. npm, pip, setuptools, wheel, and development dependencies are absent.
COPY --from=python-deps /usr/local /usr/local
COPY --from=googlemail-deps /usr/local/bin/node /usr/local/bin/node
COPY --from=googlemail-deps /build/googlemail/node_modules /app/googlemail/node_modules
COPY --from=browser-runtime --chown=0:0 /opt/google-manager/chrome /opt/google-manager/chrome

WORKDIR /app
COPY app ./app
COPY deploy ./deploy
COPY googlemail/package.json googlemail/package-lock.json ./googlemail/
COPY googlemail/src ./googlemail/src
COPY run.py ./
COPY --from=frontend-builder /build/static ./static

RUN groupadd --gid 10001 googlemanager \
    && useradd --uid 10001 --gid 10001 --create-home googlemanager \
    && mkdir -p instance googlemail/runtime googlemail/output \
    && chown -R googlemanager:googlemanager instance googlemail/runtime googlemail/output \
    && chmod 0700 instance googlemail/runtime googlemail/output \
    && chmod 0555 deploy/container-entrypoint.sh \
    && test -x /opt/google-manager/chrome/chrome \
    && test ! -L /opt/google-manager/chrome/chrome \
    && chown -R root:root /opt/google-manager/chrome \
    && chmod -R a-w /opt/google-manager/chrome

USER googlemanager

EXPOSE 8002

ENTRYPOINT ["/app/deploy/container-entrypoint.sh"]
CMD ["gunicorn", "-c", "deploy/gunicorn.conf.py", "run:app"]
