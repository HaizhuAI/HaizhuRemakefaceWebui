#!/usr/bin/env bash
# RemakeFace Pro 生图工作站启动脚本
set -e
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin123}"
export GATEWAY_TOKEN="${GATEWAY_TOKEN:-}"
export SESSION_SECRET="${SESSION_SECRET:-remakeface-pro-secret-change-me}"
export PORT="${PORT:-8611}"
exec python3 -m uvicorn gateway.app:app --host 0.0.0.0 --port "$PORT" "$@"
