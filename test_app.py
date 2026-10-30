"""Run: python test_app.py. Pure-logic checks, no network."""
import logging
import os

logging.disable(logging.CRITICAL)
import sys

sys.path.insert(0, "src")
os.environ.update(
    GEMINI_API_KEY="g", NOTION_TOKEN="n", TELEGRAM_BOT_TOKEN="t",
    TELEGRAM_SECRET_TOKEN="secret", TELEGRAM_CHAT_ID="42", NOTION_DATABASE_ID="db", ANTHROPIC_API_KEY="a",
)

import app  # noqa: E402
import telegram  # noqa: E402

# reprocessing a failed row: our error note is not read back as clipper body
import notion  # noqa: E402
_orig_call = notion._call
notion._call = lambda m, path, body=None: {"results": [
    {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "❌ RuntimeError: x"}]}},
    {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "본문"}]}}], "has_more": False}
assert notion.fetch_page_body("p") == "본문"
notion._call = _orig_call

# parse_message
assert telegram.parse_message({"text": "https://youtu.be/abc"}) == {"url": "https://youtu.be/abc", "memo": "", "video": None}
assert telegram.parse_message({"text": "https://x.com/a\n나중에 읽기"}) == {"url": "https://x.com/a", "memo": "나중에 읽기", "video": None}
fb = telegram.parse_message({"text": "https://app.notion.com/p/Mods-3-3f8a?fbclid=PAR123&utm_source=ig&id=7 메모"})
assert fb == {"url": "https://app.notion.com/p/Mods-3-3f8a?id=7", "memo": "메모", "video": None}, fb
assert telegram.parse_message({"photo": [{"file_id": "L"}], "caption": "슬라이드"}) == {"url": None, "memo": "슬라이드", "video": None}  # photo ignored
v = telegram.parse_message({"video": {"file_id": "V", "mime_type": "video/mp4", "file_size": 123}, "caption": "https://www.instagram.com/reel/x/ 클로드 팁"})
assert v == {"url": "https://www.instagram.com/reel/x/", "memo": "클로드 팁", "video": {"file_id": "V", "mime": "video/mp4", "size": 123}}, v
assert telegram.parse_message({"document": {"file_id": "D", "mime_type": "video/quicktime"}})["video"]["mime"] == "video/quicktime"
assert telegram.parse_message({"document": {"file_id": "P", "mime_type": "application/pdf"}})["video"] is None

# host classification
assert telegram.is_youtube("https://www.youtube.com/shorts/x") and telegram.is_youtube("https://youtu.be/x")
assert not telegram.is_youtube("https://example.com/youtube.com") and not telegram.is_youtube(None)
for u in ("https://m.youtube.com/watch?v=AbC_12-x&feature=share", "https://youtube.com/shorts/AbC_12-x?si=q",
          "https://youtu.be/AbC_12-x?si=q", "https://www.youtube.com/live/AbC_12-x", "https://music.youtube.com/watch?v=AbC_12-x"):
    assert telegram.youtube_watch_url(u) == "https://www.youtube.com/watch?v=AbC_12-x", u
assert telegram.source_of("https://www.linkedin.com/posts/x") == "linkedin"
assert telegram.source_of("https://blog.example.com/p") == "article"
assert telegram.source_of(None) == "other"

# verify: secret + chat_id both required
ok = {"headers": {"x-telegram-bot-api-secret-token": "secret"}, "body": '{"message":{"chat":{"id":42},"text":"hi"}}'}
assert telegram.verify(ok)["text"] == "hi"
assert telegram.verify({**ok, "headers": {"x-telegram-bot-api-secret-token": "nope"}}) is None
assert telegram.verify({**ok, "body": '{"message":{"chat":{"id":7},"text":"hi"}}'}) is None

# handler routing
calls = []
app._webhook = lambda e, c: calls.append("webhook")
app._handle_message = lambda m: calls.append("async")
app._batch = lambda: calls.append("batch")
app.notion.init = lambda: None
app.handler({"requestContext": {"http": {}}, "headers": {}, "body": "{}"}, None)
app.handler({"_async": {}}, None)
app.handler({"source": "aws.events"}, None)
assert calls == ["webhook", "async", "batch"], calls

# process_row: payload routing + failure is marked, never silent
seen = []
app.notion.fetch_page_body = lambda pid: {"clip": "본문 텍스트"}.get(pid, "")
app.notion.tag_options = lambda: []
app.notion.ensure_tag_options = lambda tags: None
app.notion.update_result = lambda pid, r, url=None: None
saved = []
app.notion.mark_saved_only = lambda pid, note: saved.append(pid)
app.summarize = lambda payload, tags: seen.append(payload) or {"one_liner": "x", "tags": []}
app.process_row({"id": "yt", "properties": {"url": {"url": "https://youtu.be/v"}}})
app.process_row({"id": "clip", "properties": {"url": {"url": "https://a.com/p"}}})
app.process_row({"id": "art", "properties": {"url": {"url": "https://a.com/p"}}})
assert seen == [{"youtube_url": "https://www.youtube.com/watch?v=v"}, {"text": "본문 텍스트"}, {"url": "https://a.com/p"}], seen

# instagram: link only -> saved, no LLM call; link + caption memo -> summarize caption
ig = {"url": {"url": "https://www.instagram.com/reel/abc/"}}
assert app.process_row({"id": "ig1", "properties": ig})["saved_only"] and saved == ["ig1"] and len(seen) == 3
cap = "Claude Code 새 스킬 기능 소개. 설치 명령과 사용법을 보여주는 릴스"
app.process_row({"id": "ig2", "properties": {**ig, "memo": {"rich_text": [{"plain_text": cap}]}}})
assert seen[-1]["text"].endswith(cap) and saved == ["ig1"], seen[-1]

# instagram url + video file -> video is analyzed (not saved-only), with url + memo as context
app.process_row({"id": "ig3", "properties": {**ig, "memo": {"rich_text": [{"plain_text": "팁"}]}}}, video={"video_bytes": b"x", "mime": "video/mp4"})
assert seen[-1]["video_bytes"] == b"x" and "instagram.com/reel/abc" in seen[-1]["context"] and "팁" in seen[-1]["context"], seen[-1]
assert saved == ["ig1"]

# source Claude can't read -> saved only with its reason, not 실패
def _unreadable(payload, tags):
    raise app.Unreadable("원문을 읽지 못함")
app.summarize = _unreadable
app.fetch_rendered = lambda url: None  # Jina can't read it either
r = app.process_row({"id": "np", "properties": {"url": {"url": "https://app.notion.com/p/x"}}})
assert r == {"saved_only": True, "one_liner": "내용 미확인 · 원문을 읽지 못함"} and saved[-1] == "np", r

# JS-rendered page: Claude gets an empty shell -> Jina renders it -> Claude reads the text (not saved-only)
def _url_unreadable(payload, tags):
    if "url" in payload:
        raise app.Unreadable("빈 페이지")
    seen.append(payload)
    return {"one_liner": "r", "tags": []}
app.summarize = _url_unreadable
app.fetch_rendered = lambda url: "렌더된 본문"
r = app.process_row({"id": "js", "properties": {"url": {"url": "https://app.notion.com/p/x"}}})
assert r["one_liner"] == "r" and seen[-1]["text"].startswith("원문 URL: https://app.notion.com/p/x\n") and "렌더된 본문" in seen[-1]["text"], seen[-1]
assert saved[-1] == "np"
# nothing rendered -> saved only with Claude's reason; the YouTube path never asks Jina
rendered = []
app.fetch_rendered = lambda url: rendered.append(url) or None
r = app.process_row({"id": "js2", "properties": {"url": {"url": "https://app.notion.com/p/x"}}})
assert r == {"saved_only": True, "one_liner": "내용 미확인 · 빈 페이지"} and saved[-1] == "js2" and rendered == ["https://app.notion.com/p/x"], r
app.summarize = _unreadable
app.process_row({"id": "yt2", "properties": {"url": {"url": "https://youtu.be/v"}}})
assert saved[-1] == "yt2" and len(rendered) == 1, rendered

# notion body: fixed heading order for weekend agents
import notion  # noqa: E402
blocks = notion.result_blocks({"summary": "s", "key_points": ["k"], "try_this": [], "author": "a"}, "https://u")
heads = [b["heading_2"]["rich_text"][0]["text"]["content"] for b in blocks if b["type"] == "heading_2"]
assert heads == ["요약", "핵심", "해볼 것", "링크", "원문"], heads
failed = notion.result_blocks({"summary": "s", "links": None}, None)
assert any(b["type"] == "paragraph" and b["paragraph"]["rich_text"][0]["text"]["content"] == "검색 실패" for b in failed)

# links: only URLs that appeared in retrieved content survive
import summarize  # noqa: E402
seen = '[{"url": "https://github.com/a/skill", "title": "x"}]'
got = summarize._verified([{"name": "a", "url": "https://github.com/a/skill/"}, {"name": "b", "url": "https://sume.com"}], seen)
assert got == [{"name": "a", "url": "https://github.com/a/skill/"}], got
# Jina Reader: rendered page -> its text; missing page, empty shell or error -> None
_orig_request = summarize.request
summarize.request = lambda m, url, headers=None, raw=False, timeout=120: ("Title: T\n\nURL Source: " + url + "\n\nMarkdown Content:\n" + "본문 " * 100).encode()
assert summarize.fetch_rendered("https://x").startswith("본문 본문")
summarize.request = lambda *a, **k: ("Title: Notion\n\nMarkdown Content:\nThis page couldn’t be found\n\n" + "x" * 300).encode()
assert summarize.fetch_rendered("https://x") is None
summarize.request = lambda *a, **k: b"Markdown Content:\nshort"
assert summarize.fetch_rendered("https://x") is None
summarize.request = lambda *a, **k: ("Title: T\n\nWarning: Target URL returned error 404: Not Found\n\nMarkdown Content:\n" + "custom 404 page " * 30).encode()
assert summarize.fetch_rendered("https://x") is None
summarize.request = lambda m, url, *a, **k: ("Markdown Content:\n" + url + "\n" + "본문 " * 100).encode()
assert summarize.fetch_rendered("https://namu.wiki/w/파이썬?a=1&b=%20c#s").startswith("https://r.jina.ai/https://namu.wiki/w/%ED%8C%8C%EC%9D%B4%EC%8D%AC?a=1&b=%20c#s")
def _boom(*a, **k):
    raise RuntimeError("GET jina -> 429")
summarize.request = _boom
assert summarize.fetch_rendered("https://x") is None
summarize.request = _orig_request
# Gemini quota is per model: the first model's 429 hands over to the next one; links are not looked up for no tools
calls = []
def _quota_then_ok(url, headers, body):
    calls.append(url.rsplit("/", 1)[1].split(":")[0])
    if len(calls) == 1:
        raise RuntimeError(f"POST {url} -> 429: quota")
    return {"candidates": [{"content": {"parts": [{"text": '{"one_liner": "g", "tags": ["t"], "tools": []}'}]}}]}
summarize._post = _quota_then_ok
os.environ["GEMINI_MODEL"] = "m1, m2,m3"
r = summarize.summarize({"youtube_url": "https://www.youtube.com/watch?v=x"})
assert calls == ["m1", "m2"] and r["one_liner"] == "g" and r["links"] == [], (calls, r)
# Gemini down on every model: all are tried, YouTube falls back to Claude (marked); an uploaded video is uploaded
# once and then fails loudly with the last model's error
tried, uploads = [], []
def _down(url, *a, **k):
    tried.append(url.rsplit("/", 1)[1].split(":")[0])
    raise RuntimeError(f"POST {url} -> 429: quota")
summarize._post = _down
summarize.upload_video = lambda data, mime: uploads.append(mime) or {"uri": "files/x", "mimeType": mime}
summarize._claude = lambda prompt, name, schema, tools: ({"one_liner": "o", "summary": "s", "tags": ["t"], "links": []}, prompt)
r = summarize.summarize({"youtube_url": "https://www.youtube.com/watch?v=x"})
assert tried == ["m1", "m2", "m3"] and r["one_liner"] == "[설명 기반] o" and r["summary"].startswith(summarize.FALLBACK_NOTE), (tried, r)
tried.clear()
try:
    summarize.summarize({"video_bytes": b"v", "mime": "video/mp4"})
    raise AssertionError("video upload should fail when Gemini is down")
except RuntimeError as e:
    assert "다시 보내줘" in str(e) and "m3:generateContent -> 429" in str(e) and uploads == ["video/mp4"] and tried == ["m1", "m2", "m3"], (e, uploads, tried)
js = summarize._json_schema(summarize.CLAUDE_SCHEMA)
assert js["type"] == "object" and js["additionalProperties"] is False and js["properties"]["links"]["items"]["additionalProperties"] is False
assert js["properties"]["category"]["enum"] == summarize.CATEGORIES
marked = []
app.notion.mark_failed = lambda pid, err: marked.append((pid, err))
try:
    app.process_row({"id": "p1", "properties": {}})
    raise AssertionError("should raise")
except ValueError:
    pass
assert marked and marked[0][0] == "p1" and "입력 없음" in marked[0][1]

# setup: Notion page id from a URL whose slug also contains hex
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("create_notion_db", "scripts/create-notion-db.py")
cndb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cndb)
assert cndb.page_id("https://app.notion.com/p/Mods-3-3f8ad772ed178103be0bf8f24935a5ad?pvs=4") == "3f8ad772ed178103be0bf8f24935a5ad"
assert cndb.page_id("3f8ad772-ed17-8103-be0b-f8f24935a5ad") == "3f8ad772ed178103be0bf8f24935a5ad"
try:
    cndb.page_id("https://www.notion.so/hello")
    raise AssertionError("non-id should fail")
except ValueError:
    pass

print("ok")
