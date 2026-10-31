#!/usr/bin/env bash
# One-shot install. Made for AWS CloudShell (aws, sam, python3, curl preinstalled); works anywhere with those.
# Safe to re-run: press Enter at a key prompt to keep the value already stored in SSM.
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_REGION="${AWS_REGION:-ap-northeast-2}"
R=$AWS_REGION

for t in aws sam python3 curl; do command -v "$t" >/dev/null || { echo "missing: $t"; exit 1; }; done
echo "AWS account $(aws sts get-caller-identity --query Account --output text), region $R"

have() { aws ssm get-parameter --name "/event-scraper/$1" --region "$R" >/dev/null 2>&1; }
val() { aws ssm get-parameter --name "/event-scraper/$1" --with-decryption --query Parameter.Value --output text --region "$R"; }

secret() {  # secret ENV_VAR ssm-name "prompt": hidden input, required unless already stored
  local v="" hint=""
  have "$2" && hint=" [Enter = keep current]"
  while :; do
    read -rsp "$3$hint: " v; echo
    [ -n "$v" ] || [ -n "$hint" ] && break
  done
  export "$1=$v"
}
secret TELEGRAM_BOT_TOKEN telegram-bot-token "Telegram bot token (from @BotFather)"
secret GEMINI_API_KEY     gemini-api-key     "Gemini API key (aistudio.google.com/apikey)"
secret ANTHROPIC_API_KEY  anthropic-api-key  "Anthropic API key (console.anthropic.com)"
secret NOTION_TOKEN       notion-token       "Notion integration secret (notion.so/profile/integrations)"

# Telegram echoes this in a header on every webhook call; the Lambda rejects calls without it.
have telegram-secret-token || export TELEGRAM_SECRET_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

if ! have telegram-chat-id; then  # only this chat may use the bot
  read -rp "Send any message to your bot in Telegram, then press Enter. " _
  BOT="${TELEGRAM_BOT_TOKEN:-$(val telegram-bot-token)}"
  TELEGRAM_CHAT_ID=$(curl -s "https://api.telegram.org/bot$BOT/getUpdates" | python3 -c '
import json, sys
r = [u for u in json.load(sys.stdin).get("result") or [] if "message" in u]
print(r[-1]["message"]["chat"]["id"] if r else "")')
  [ -n "$TELEGRAM_CHAT_ID" ] || read -rp "Could not read it. Enter your chat id: " TELEGRAM_CHAT_ID
  echo "chat id: $TELEGRAM_CHAT_ID"
  export TELEGRAM_CHAT_ID
fi

if ! have notion-database-id; then
  echo "In Notion, open the page that will hold the database and add your integration (... > Connections)."
  read -rp "Paste that page's URL: " PARENT
  NOTION_DATABASE_ID=$(NOTION_TOKEN="${NOTION_TOKEN:-$(val notion-token)}" python3 scripts/create-notion-db.py "$PARENT")
  echo "created Notion database $NOTION_DATABASE_ID"
  export NOTION_DATABASE_ID
fi

scripts/put-params.sh
# No `sam build`: the function has no dependencies, so deploy packages src/ as is
# (and CloudShell's python3 doesn't have to match the python3.12 runtime).
sam deploy --region "$R" --no-fail-on-empty-changeset
# A bot token holds one webhook. SKIP_WEBHOOK=1 when this is a second copy (another region, a test) and the
# real bot must keep pointing at the first one; drive this copy by calling the Function URL yourself.
[ -n "${SKIP_WEBHOOK:-}" ] && echo "SKIP_WEBHOOK set: webhook left as is" || scripts/set-webhook.sh
echo "Done. Send a YouTube or article link to your bot."
