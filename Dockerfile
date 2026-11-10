# 대시보드 화면을 빌드한다(Vite). 최종 이미지에는 Node가 들어가지 않고 결과물만 복사한다.
FROM node:20.18-slim AS web
WORKDIR /src
COPY package.json package-lock.json tsconfig.json vite.config.ts ./
RUN npm ci --no-audit --no-fund
COPY app/web app/web
RUN npm run build

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
COPY --from=web /src/app/web-dist /app/app/web-dist

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
