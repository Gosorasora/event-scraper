#!/usr/bin/env bash
# Usage: NOTION_TOKEN=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... scripts/put-params.sh
# Only the variables you set are written; the rest are left as they are.
set -euo pipefail
R="${AWS_REGION:-ap-northeast-2}"
put() { [ -n "${2:-}" ] || return 0; aws ssm put-parameter --name "/event-scraper/$1" --value "$2" --type SecureString --overwrite --region "$R" >/dev/null && echo "ok /event-scraper/$1"; }
put gemini-api-key        "${GEMINI_API_KEY:-}"
put notion-token          "${NOTION_TOKEN:-}"
put telegram-bot-token    "${TELEGRAM_BOT_TOKEN:-}"
put telegram-secret-token "${TELEGRAM_SECRET_TOKEN:-}"
put telegram-chat-id      "${TELEGRAM_CHAT_ID:-}"
put notion-database-id    "${NOTION_DATABASE_ID:-}"
put anthropic-api-key     "${ANTHROPIC_API_KEY:-}"
aws ssm get-parameters-by-path --path /event-scraper --region "$R" --query 'Parameters[].Name' --output text
