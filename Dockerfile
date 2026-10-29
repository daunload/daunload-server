# API와 워커가 같은 이미지를 쓴다. Playwright 공식 이미지에 Google Chrome을 추가한다
# (Playwright 기본 Chromium은 H.264/AAC를 재생하지 못한다. Chrome은 linux/amd64만 제공된다).
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV TZ=Asia/Seoul PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
RUN playwright install --with-deps chrome
# 전사용 오디오 추출(16kHz PCM)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY . .
RUN uv sync --frozen --no-dev

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
