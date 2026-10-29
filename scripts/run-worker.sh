#!/usr/bin/env bash
# Mac 등 호스트에서 워커를 실행한다. Redis 재시작 등으로 워커가 종료되면 5초 뒤 다시 띄운다.
# 사용: ./scripts/run-worker.sh   (중지: Ctrl+C 두 번)
cd "$(dirname "$0")/.."
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://lms:lms@localhost:5432/lms}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
trap 'echo "워커 실행 스크립트를 종료합니다."; exit 0' INT TERM
while true; do
  uv run arq app.worker.main.WorkerSettings
  echo "[$(date '+%F %T')] 워커가 종료되었습니다(코드 $?). 5초 뒤 다시 시작합니다."
  sleep 5
done
