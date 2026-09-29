# 两阶段构建:前端 dist 拷进后端镜像,单容器运行
# 默认优先中国大陆镜像,镜像异常时自动降级到第二国内镜像/官方源,不要求代理。
# 可传 --build-arg USE_CN_MIRROR=0 强制只用官方源。
# 可选:stock-sdk 插件默认不打包(它抓取第三方财经网站接口,存在版权与反爬风险)。
#       如确需启用,传入 --build-arg INCLUDE_STOCKSDK=1 显式开启,使用风险自负。
ARG USE_CN_MIRROR=1
ARG INCLUDE_STOCKSDK=0
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG NPM_FALLBACK=https://registry.npmjs.org
ARG APT_MIRROR=mirrors.aliyun.com
ARG APT_FALLBACK=mirrors.tuna.tsinghua.edu.cn
ARG PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
# 备用 PyPI 源:主源同步延迟/故障时自动兜底(阿里云与清华互为补充)
ARG PYPI_FALLBACK=https://mirrors.aliyun.com/pypi/simple
ARG PYPI_OFFICIAL=https://pypi.org/simple
ARG BACKEND_EXTRAS=
ARG CODEX_CLI_VERSION=0.144.3

# === Stage 1: 前端构建 ===
FROM node:20-alpine AS frontend-builder
ARG USE_CN_MIRROR=1
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG NPM_FALLBACK=https://registry.npmjs.org
WORKDIR /build
# 国内默认优先 npmmirror;镜像故障时自动回退 npm 官方源。
# 不使用 corepack,避免它绕过 npm registry 再次联网下载 pnpm。
RUN if [ "$USE_CN_MIRROR" = "1" ]; then \
      npm config set registry "$NPM_REGISTRY"; \
      npm install -g pnpm@9 || { \
        echo "npmmirror unavailable, falling back to npmjs.org"; \
        npm config set registry "$NPM_FALLBACK"; \
        npm install -g pnpm@9; \
      }; \
    else \
      npm install -g pnpm@9; \
    fi
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN if [ "$USE_CN_MIRROR" = "1" ]; then \
      pnpm config set registry "$NPM_REGISTRY"; \
      pnpm install --frozen-lockfile || { \
        echo "npmmirror dependency install failed, falling back to npmjs.org"; \
        pnpm config set registry "$NPM_FALLBACK"; \
        pnpm install --frozen-lockfile || pnpm install; \
      }; \
    else \
      pnpm install --frozen-lockfile || pnpm install; \
    fi
COPY frontend/ ./
RUN pnpm build

# === Stage 1b: stock-sdk 插件依赖(可选,默认跳过) ===
# ⚠️ 合规提示: stock-sdk 通过 node bridge.mjs 抓取第三方财经网站(如东方财富)的行情接口,
#    未经对方授权,可能违反其服务条款并涉及交易所行情版权。默认不打包(INCLUDE_STOCKSDK=0)。
#    如确需启用,构建时传 --build-arg INCLUDE_STOCKSDK=1,即视为使用者知悉并自行承担合规责任。
# INCLUDE_STOCKSDK=0 时,本 stage 仅产出空 node_modules 目录,保证后续 COPY 不报错。
FROM node:20-bookworm-slim AS stocksdk-builder
ARG USE_CN_MIRROR=1
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG NPM_FALLBACK=https://registry.npmjs.org
ARG INCLUDE_STOCKSDK=0
WORKDIR /build
COPY backend/app/plugins/stocksdk/package.json backend/app/plugins/stocksdk/package-lock.json ./
# INCLUDE_STOCKSDK=1 时安装依赖;国内镜像失败自动回退官方源。
RUN if [ "$INCLUDE_STOCKSDK" = "1" ]; then \
      if [ "$USE_CN_MIRROR" = "1" ]; then \
        npm config set registry "$NPM_REGISTRY"; \
        npm ci || npm install || { \
          npm config set registry "$NPM_FALLBACK"; \
          npm ci || npm install; \
        }; \
      else \
        npm ci || npm install; \
      fi; \
    else \
      mkdir -p /build/node_modules; \
    fi

# === Stage 1c: Codex CLI ===
# 固定版本保证镜像可复现；只复制安装产物到运行镜像，不保留 npm。
FROM node:20-bookworm-slim AS codex-builder
ARG USE_CN_MIRROR=1
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG NPM_FALLBACK=https://registry.npmjs.org
# 版本由顶层 ARG CODEX_CLI_VERSION 提供, 这里仅声明以继承, 不再重复默认值。
ARG CODEX_CLI_VERSION
RUN if [ "$USE_CN_MIRROR" = "1" ]; then \
      npm config set registry "$NPM_REGISTRY"; \
      npm install --global --prefix /opt/codex "@openai/codex@${CODEX_CLI_VERSION}" || { \
        echo "npmmirror Codex install failed, falling back to npmjs.org"; \
        npm config set registry "$NPM_FALLBACK"; \
        npm install --global --prefix /opt/codex "@openai/codex@${CODEX_CLI_VERSION}"; \
      }; \
    else \
      npm install --global --prefix /opt/codex "@openai/codex@${CODEX_CLI_VERSION}"; \
    fi \
    && CODEX_NATIVE="$(find /opt/codex -type f -path '*/vendor/*/bin/codex' -print -quit)" \
    && test -n "$CODEX_NATIVE" \
    && cp "$CODEX_NATIVE" /opt/codex-native \
    && chmod +x /opt/codex-native \
    && /opt/codex-native --version

# === Stage 2: Python 运行时 ===
FROM python:3.11-slim AS runtime
ARG USE_CN_MIRROR=1
ARG APT_MIRROR=mirrors.aliyun.com
ARG APT_FALLBACK=mirrors.tuna.tsinghua.edu.cn
ARG PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PYPI_FALLBACK=https://mirrors.aliyun.com/pypi/simple
ARG PYPI_OFFICIAL=https://pypi.org/simple
ARG BACKEND_EXTRAS=
ARG INCLUDE_STOCKSDK=0
WORKDIR /app

# Node.js 运行时: 仅在启用 stock-sdk 插件时安装(供 node bridge.mjs 使用)。
# Codex CLI 从官方 npm 包提取原生二进制，不依赖运行时 Node.js。
# bookworm 自带 nodejs 18.19, 满足插件 engines>=18; --no-install-recommends 精简,
# 自带 libnode/libc-ares 等全部动态依赖, 无需手动补库。
# 国内构建优先阿里云 apt;单包 5xx/超时会重试,仍失败则切清华镜像,
# 最后才回退 Debian 官方源。这样默认无需代理,也不会被单一镜像故障卡死。
# tesseract-ocr: 自选截图导入（始终安装）; nodejs: 仅 INCLUDE_STOCKSDK=1 时安装
RUN set -eu; \
    install_runtime_packages() { \
      apt-get -o Acquire::Retries=3 update && \
      apt-get -o Acquire::Retries=3 install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng && \
      if [ "$INCLUDE_STOCKSDK" = "1" ]; then \
        apt-get -o Acquire::Retries=3 install -y --no-install-recommends nodejs && node --version; \
      fi; \
    }; \
    if [ "$USE_CN_MIRROR" = "1" ]; then \
      sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true; \
      if ! install_runtime_packages; then \
        echo "Primary CN apt mirror failed; trying secondary CN mirror"; \
        rm -rf /var/lib/apt/lists/*; \
        sed -i "s|$APT_MIRROR|$APT_FALLBACK|g" /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true; \
        if ! install_runtime_packages; then \
          echo "CN apt mirrors failed; falling back to deb.debian.org"; \
          rm -rf /var/lib/apt/lists/*; \
          sed -i "s|$APT_FALLBACK|deb.debian.org|g" /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true; \
          install_runtime_packages; \
        fi; \
      fi; \
    else \
      install_runtime_packages; \
    fi; \
    rm -rf /var/lib/apt/lists/*; \
    tesseract --version

# 安装 uv(快) —— 国内镜像下三重兜底:主源 → 备用源 → 官方源,
# 任一成功即可,避免单一镜像同步延迟/故障导致构建失败。
# uv 发版极频繁,国内镜像同步存在时间窗口,不锁版本且无 fallback 时
# 容易遇到 "from versions: none"(索引解析不到最新版)。
RUN if [ "$USE_CN_MIRROR" = "1" ]; then \
      pip install --no-cache-dir uv -i "$PYPI_INDEX" || \
      pip install --no-cache-dir uv -i "$PYPI_FALLBACK" || \
      pip install --no-cache-dir uv; \
    else \
      pip install --no-cache-dir uv; \
    fi

# Backend deps
COPY README.md /README.md
COPY backend/pyproject.toml backend/uv.lock* ./
# uv 默认同时使用清华 + 阿里云;两国内源整体失败时才回退 PyPI 官方源。
RUN set -- --no-dev; \
    for extra in $BACKEND_EXTRAS; do \
      set -- "$@" --extra "$extra"; \
    done; \
    if [ "$USE_CN_MIRROR" = "1" ]; then \
      export UV_DEFAULT_INDEX="$PYPI_INDEX" UV_EXTRA_INDEX_URL="$PYPI_FALLBACK"; \
      if ! uv sync --frozen "$@"; then \
        echo "CN PyPI mirrors failed; falling back to pypi.org"; \
        export UV_DEFAULT_INDEX="$PYPI_OFFICIAL"; \
        unset UV_EXTRA_INDEX_URL; \
        uv sync --frozen "$@" || uv sync "$@"; \
      fi; \
    else \
      uv sync --frozen "$@" || uv sync "$@"; \
    fi

# Backend code
# 注意:Docker 里 WORKDIR=/app, 而 config.py 的 _PROJECT_ROOT 是按开发布局
# (<root>/backend/app/) 推导的, 容器内会错算到 /。这里用环境变量显式指定
# 三个关键路径, 确保 static / tiers / data 都指向容器内正确位置。
COPY backend/app ./app
# stock-sdk 插件依赖: 从 stocksdk-builder 拷入。
# INCLUDE_STOCKSDK=0(默认) 时, stocksdk-builder 产出空目录,此处拷入空目录,
# 即最终镜像不含 stock-sdk 依赖,插件默认不可用。
# COPY --from 不受 .dockerignore 的 **/node_modules 规则影响。
COPY --from=stocksdk-builder /build/node_modules ./app/plugins/stocksdk/node_modules
COPY tiers.yaml /app/tiers.yaml
ENV STATIC_DIR=/app/static \
    TIERS_YAML=/app/tiers.yaml \
    DATA_DIR=/app/data \
    TICKFLOW_ENV_FILE=/app/.env

# Frontend 静态产物
COPY --from=frontend-builder /build/dist ./static

# Codex CLI 使用官方 npm 包携带的当前平台原生二进制，无需运行时 Node.js。
COPY --from=codex-builder /opt/codex-native /usr/local/bin/codex
RUN codex --version

ENV PYTHONPATH=/app
# 运行时 uv 镜像源持久化: CMD 用 `uv run` 启动, 锁与 pyproject 不一致等场景下
# uv 会在容器内重新解析/安装 —— 无源配置时默认 pypi.org, 国内网络会卡死启动
# (实测阿里云 ECS)。与构建期 RUN 内的 export 同源, 这里让它跨层存活。
ARG PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PYPI_FALLBACK=https://mirrors.aliyun.com/pypi/simple
ARG PYPI_OFFICIAL=https://pypi.org/simple
ENV UV_DEFAULT_INDEX=${PYPI_INDEX} \
    UV_EXTRA_INDEX_URL="${PYPI_FALLBACK} ${PYPI_OFFICIAL}"
# 兜底时区: 交易时段判断已在代码里显式用北京时间 (app/market_time.py),
# 此处让日志时间戳等其余 naive 时间也对齐北京时间。
ENV TZ=Asia/Shanghai
EXPOSE 3018
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3018"]
