# event-scraper

**A watch-later inbox in Notion, fed from your phone.** Share a YouTube video, a short, an article, or a downloaded reel to your own Telegram bot. A single AWS Lambda summarizes it with Gemini and Claude, then writes the result to a Notion database. Each row has a one-liner, tags, key points, things to try, and official links to the tools the content mentions.

[한국어 README](README.ko.md)

![Architecture](docs/event-scraper-aws.png)

- **Videos and YouTube** go to Gemini Flash, which watches the video itself. Models are tried in order (`gemini-3.8-flash`, then `2.5-flash`, then `3.5-flash-lite`): the free daily quota is per model, so when one runs out the next one takes over.
- **Articles and text** go to Claude Haiku 5.5, which reads the page with `web_fetch` and finds official links with `web_search`. A link is kept only if it actually appeared in the fetched or searched content.
- **If Gemini is down or out of quota**, Claude summarizes YouTube videos from their title and description, and the summary is marked as such.
- **Rows added by the Notion Web Clipper** (empty `one_liner`) are filled by a daily batch run.
- **Every row has the same body layout** (`## 요약 / 핵심 / 해볼 것 / 링크 / 원문`), so another agent can curate the inbox later.

Each person deploys their own copy: your bot, your Notion, your API keys, your AWS account.

## What you need

| | Where |
|---|---|
| AWS account | Permission to create CloudFormation, Lambda, IAM role, S3, SSM and EventBridge resources |
| Telegram bot token | Message [@BotFather](https://t.me/BotFather), `/newbot` |
| Gemini API key | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com) |
| Notion integration secret | [notion.so/profile/integrations](https://www.notion.so/profile/integrations), internal integration |
| A Notion page to hold the database | Open it, then **••• > Connections > add your integration** |

## Install with AWS CloudShell (nothing to install locally)

1. Sign in to the AWS console and pick a region (for example Seoul `ap-northeast-2`).
2. Open [CloudShell](https://docs.aws.amazon.com/cloudshell/latest/userguide/welcome.html) (the `>_` icon in the top bar). It already has the AWS CLI, SAM CLI, Python 3 and git, and it uses your console login.
3. Run:

   ```bash
   git clone https://github.com/Gosorasora/event-scraper.git
   cd event-scraper
   ./scripts/setup.sh
   ```

The script does the following:
1. Asks for the four keys. Input is hidden, and the keys are stored as SSM SecureString parameters under `/event-scraper/`.
2. Generates the webhook secret.
3. Reads your chat id after you send the bot a message.
4. Creates the Notion database with the right schema.
5. Deploys the stack with `sam deploy`.
6. Registers the Telegram webhook.

When it finishes, send your bot a link.

The script is safe to re-run. At a key prompt, press Enter to keep the stored value. To replace a key, for example after rotating it, run the script again and paste the new one. Deploying a second copy (another region, a test) while the real bot keeps its webhook: `SKIP_WEBHOOK=1 ./scripts/setup.sh`, then call the Function URL yourself with the webhook secret header.

<details>
<summary>Installing from your own machine instead</summary>

You need the AWS CLI with credentials, the SAM CLI, `python3` and `curl`. Then run:

```bash
AWS_PROFILE=<your-profile> AWS_REGION=ap-northeast-2 ./scripts/setup.sh
```

No `sam build` is needed. The function has no third-party dependencies, so `sam deploy` packages `src/` as is.
</details>

## Using it

| Send to the bot | Result |
|---|---|
| YouTube / Shorts link | Gemini watches it, and Claude finds official links |
| Article link | Claude reads it and finds official links |
| Link to a JavaScript-rendered page (someone's public Notion page, a SPA) | Claude only gets an empty shell, so the page is rendered through [Jina Reader](https://jina.ai/reader/) and Claude reads that text |
| Link + text on the next lines | The text is saved as `memo` |
| Video file up to 20 MB (for example, a downloaded reel) | Gemini watches the file |
| Instagram link only | Saved without a summary (Instagram can't be read from a link). Send the video file, or paste the caption as memo |

The bot replies with the one-liner and the Notion page link. If a page can't be read (login wall, someone else's private Notion page), the row is kept as **saved only** with the reason, instead of a guessed summary.

## Cost

- **AWS:** One Lambda, one EventBridge rule, standard SSM parameters and CloudWatch Logs. Personal use normally stays within the AWS free tier.
- **Gemini:** The free tier has a daily request quota. See [pricing](https://ai.google.dev/gemini-api/docs/pricing).
- **Claude:** Pay per use, including web search and fetch. See [pricing](https://www.anthropic.com/pricing).

## Customize

- **Categories and content types:** `CATEGORIES` and `TYPES` in `src/common.py`
- **Summary language and prompt:** `PROMPT` in `src/summarize.py`. Summaries are written in Korean by default.
- **Models:** the `GeminiModel` (comma-separated, tried in order) and `ClaudeModel` parameters in `template.yaml`. An existing stack keeps its old parameter values, so pass new ones explicitly: `sam deploy --parameter-overrides 'GeminiModel="gemini-3.8-flash,gemini-2.5-flash"'`

## Security

- **The Function URL is public.** Every call must carry the Telegram webhook secret header and come from your chat id. All other calls are ignored.
- **Keys live in SSM Parameter Store** as SecureString, not in code or Lambda environment variables. The function's IAM role can read only `/event-scraper/*`, and loads the keys once per cold start.
- **Third parties see your content.** What you send goes to Telegram, Google (Gemini), Anthropic (Claude) and Notion. A page Claude can't read is also sent to Jina Reader to be rendered.

## Uninstall

```bash
sam delete --stack-name event-scraper --region "$AWS_REGION"
aws ssm delete-parameters --region "$AWS_REGION" \
  --names $(aws ssm get-parameters-by-path --path /event-scraper --region "$AWS_REGION" --query 'Parameters[].Name' --output text)
```

## Project layout

```
src/            Lambda code (app.py entry, summarize.py models, notion.py, telegram.py, common.py)
scripts/        setup.sh, create-notion-db.py, put-params.sh, set-webhook.sh
template.yaml   SAM template
test_app.py     python3 test_app.py (no network)
SPEC.md, DECISIONS.md   design notes and rejected alternatives (Korean)
```

## License

[MIT](LICENSE)
