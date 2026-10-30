"""Telegram Bot API: verify webhook, parse update, download video, reply."""
import hmac
import json
import re
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

from common import get, request

URL_RE = re.compile(r"https?://\S+")


def _api(method, body=None):
    return request("POST", f"https://api.telegram.org/bot{get('telegram-bot-token')}/{method}", body=body)


def verify(event):
    """Returns the message dict if secret header and chat_id match, else None."""
    header = (event.get("headers") or {}).get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(header, get("telegram-secret-token")):
        return None
    update = json.loads(event.get("body") or "{}")
    msg = update.get("message") or {}
    if str((msg.get("chat") or {}).get("id")) != get("telegram-chat-id"):
        return None
    return msg


MAX_FILE = 20 * 1024 * 1024  # Bot API getFile limit


TRACKING = ("fbclid", "gclid", "igsh", "igshid", "si", "mibextid", "feature")


def clean_url(url):
    """Drop share-tracking query params. A Facebook fbclid alone made web_fetch reject a link as too long."""
    u = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k not in TRACKING and not k.startswith("utm_")]
    return urlunparse(u._replace(query=urlencode(q)))


def parse_message(msg):
    """-> {"url", "memo", "video"}. First URL is the link, remaining text is memo.
    video = {"file_id", "mime", "size"} for a video (or video document), else None. Photos are ignored."""
    text = msg.get("text") or msg.get("caption") or ""
    m = URL_RE.search(text)
    url = clean_url(m.group(0)) if m else None
    memo = (text.replace(m.group(0), "", 1) if m else text).strip()
    v = msg.get("video") or msg.get("video_note")
    doc = msg.get("document") or {}
    if not v and (doc.get("mime_type") or "").startswith("video/"):
        v = doc
    video = {"file_id": v["file_id"], "mime": v.get("mime_type") or "video/mp4", "size": v.get("file_size") or 0} if v else None
    return {"url": url, "memo": memo, "video": video}


def download_file(file_id):
    path = _api("getFile", {"file_id": file_id})["result"]["file_path"]
    return request("GET", f"https://api.telegram.org/file/bot{get('telegram-bot-token')}/{path}", raw=True)


def send(text):
    _api("sendMessage", {"chat_id": get("telegram-chat-id"), "text": text[:4000], "disable_web_page_preview": True})


def host(url):
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def is_youtube(url):
    return bool(url) and host(url) in ("youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be")


def youtube_watch_url(url):
    """Canonical watch URL. Gemini rejects e.g. m.youtube.com links (400 Unsupported MIME type)."""
    u = urlparse(url)
    if host(url) == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
    else:
        parts = u.path.strip("/").split("/")
        vid = parse_qs(u.query).get("v", [None])[0] or (parts[1] if len(parts) > 1 and parts[0] in ("shorts", "live", "embed") else None)
    return f"https://www.youtube.com/watch?v={vid}" if vid else url


def source_of(url):
    if not url:
        return "other"
    if is_youtube(url):
        return "youtube"
    h = host(url)
    if h.endswith("instagram.com"):
        return "instagram"
    if h.endswith("linkedin.com"):
        return "linkedin"
    return "article"
