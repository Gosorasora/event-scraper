"""Single Lambda. Branches on event source; one worker: process_row(page)."""
import json
import logging

import notion
import telegram
from summarize import Unreadable, fetch_rendered, summarize

CAPTION_MIN = 30
INSTAGRAM_NOTE = "인스타 릴스 · 내용 미확인"

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(event, context):
    if "http" in (event.get("requestContext") or {}):
        return _webhook(event, context)
    notion.init()
    if "_async" in event:
        return _handle_message(event["_async"])
    return _batch()


# --- Telegram path -----------------------------------------------------------

def _webhook(event, context):
    msg = telegram.verify(event)
    if msg:  # hand off to ourselves so Telegram gets its 200 immediately
        import boto3

        boto3.client("lambda").invoke(
            FunctionName=context.function_name, InvocationType="Event", Payload=json.dumps({"_async": msg}).encode()
        )
    else:
        log.warning("webhook rejected (bad secret or chat_id)")
    return {"statusCode": 200, "body": "ok"}


def _handle_message(msg):
    video = None
    try:
        p = telegram.parse_message(msg)
        if not p["url"] and not p["video"]:
            telegram.send("URL이나 영상을 보내줘.")
            return
        if p["video"]:
            if p["video"]["size"] > telegram.MAX_FILE:
                telegram.send("❌ 영상이 20MB를 넘어서 못 받음. 화면 녹화로 짧게 다시 보내줘.")
                return
            video = {"video_bytes": telegram.download_file(p["video"]["file_id"]), "mime": p["video"]["mime"]}
        page = notion.create_page(url=p["url"], source=telegram.source_of(p["url"]), memo=p["memo"])
    except Exception as e:
        log.exception("intake failed")
        telegram.send(f"❌ 접수 실패: {type(e).__name__}: {e}")
        raise
    try:
        result = process_row(page, video=video)
        if result.get("saved_only"):
            hint = " (인스타는 영상을 다운로드해서 보내거나 캡션을 붙여넣으면 분석)" if result["one_liner"] == INSTAGRAM_NOTE else ""
            telegram.send(f"📌 저장만 함: {result['one_liner']}{hint}\n{page['url']}")
        else:
            telegram.send(f"✅ {result['one_liner']}\n{page['url']}")
    except Exception as e:
        telegram.send(f"❌ 처리 실패: {type(e).__name__}: {e}\n{page['url']}")


# --- Batch path (EventBridge / manual invoke) ---------------------------------

def _batch():
    ok = fail = 0
    for page in notion.query_pending():
        try:
            process_row(page)
            ok += 1
        except Exception:
            fail += 1
    log.info("batch done ok=%d fail=%d", ok, fail)
    return {"ok": ok, "fail": fail}


# --- Worker -----------------------------------------------------------------

def _read(payload, url, tags):
    """Claude reads the input. A URL it can't read (JS-rendered page = empty shell) is rendered by Jina Reader
    and Claude reads that text. Raises Unreadable when neither worked."""
    try:
        return summarize(payload, tags)
    except Unreadable:
        text = "url" in payload and fetch_rendered(url)  # only the URL path: video, clipper body and captions have nothing to render
        if not text:
            raise
        return summarize({"text": f"원문 URL: {url}\n---\n{text}"}, tags)


def process_row(page, video=None):
    """Fill one row. On any error: status=실패 + error in body, then re-raise. No silent failures.
    video: {"video_bytes", "mime"} when the user sent the file itself (e.g. a downloaded reel)."""
    pid = page["id"]
    try:
        url = (page["properties"].get("url") or {}).get("url")
        memo = "".join(t.get("plain_text", "") for t in (page["properties"].get("memo") or {}).get("rich_text", []))
        if video:
            ctx = "\n".join(x for x in (f"원본: {url}" if url else "", f"사용자 메모: {memo}" if memo else "") if x)
            result = summarize({**video, "context": ctx}, notion.tag_options())
            notion.ensure_tag_options(result["tags"])
            notion.update_result(pid, result, url)
            return result
        if telegram.source_of(url) == "instagram" and len(memo.strip()) < CAPTION_MIN:
            # Can't read reels from a URL; summarizing would be a hallucination. Save and move on.
            notion.mark_saved_only(pid, INSTAGRAM_NOTE)
            return {"saved_only": True, "one_liner": INSTAGRAM_NOTE}
        body = notion.fetch_page_body(pid)
        if telegram.is_youtube(url):
            payload = {"youtube_url": telegram.youtube_watch_url(url)}
        elif telegram.source_of(url) == "instagram":  # caption pasted as memo
            payload = {"text": f"인스타 릴스 캡션 ({url}):\n{memo}"}
        elif body.strip():  # clipper already put the article text in the page
            payload = {"text": body}
        elif url:
            payload = {"url": url}  # Claude fetches it (web_fetch)
        else:
            raise ValueError("입력 없음 (url 또는 본문 필요)")
        try:
            result = _read(payload, url, notion.tag_options())
        except Unreadable as e:  # same rule as Instagram: can't read it -> keep the link, don't guess
            note = f"내용 미확인 · {str(e)[:150]}"
            notion.mark_saved_only(pid, note)
            return {"saved_only": True, "one_liner": note}
        notion.ensure_tag_options(result["tags"])
        notion.update_result(pid, result, url)
        return result
    except Exception as e:
        log.exception("process_row failed page=%s", pid)
        notion.mark_failed(pid, f"{type(e).__name__}: {e}")
        raise
