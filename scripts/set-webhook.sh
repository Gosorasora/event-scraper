#!/usr/bin/env bash
# Registers the Lambda Function URL as the Telegram webhook. Run after `sam deploy`.
set -euo pipefail
R="${AWS_REGION:-ap-northeast-2}"
get() { aws ssm get-parameter --name "/event-scraper/$1" --with-decryption --query Parameter.Value --output text --region "$R"; }
URL=$(aws cloudformation describe-stacks --stack-name event-scraper --query "Stacks[0].Outputs[?OutputKey=='FunctionUrl'].OutputValue" --output text --region "$R")
curl -s "https://api.telegram.org/bot$(get telegram-bot-token)/setWebhook" \
  -H 'Content-Type: application/json' \
  -d "{\"url\":\"$URL\",\"secret_token\":\"$(get telegram-secret-token)\"}"
echo
