# 웹 파일만 압축한다(esbuild는 빌드에만 쓰고 최종 이미지에는 Node가 들어가지 않는다).
FROM node:20.18-slim AS web
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY scripts/build-web.mjs scripts/
COPY app/web app/web
RUN node scripts/build-web.mjs /web-dist

# API와 워커가 같은 이미지를 쓴다. Playwright 공식 이미지에 Google Chrome을 추가한다
# (Playwright 기본 Chromium은 H.264/AAC를 재생하지 못한다. Chrome은 linux/amd64만 제공된다).
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV TZ=Asia/Seoul PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
RUN playwright install --with-deps chrome

COPY . .
RUN uv sync --frozen --no-dev
COPY --from=web /web-dist /app/app/web-dist
ENV WEB_DIR=/app/app/web-dist

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
