"""LLM boundary. summarize(payload) -> dict. Gemini watches video; Claude reads text and finds links."""
import json
import logging
import os
import time
import urllib.request

from common import CATEGORIES, TYPES, get, request

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "one_liner": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "key_points": {"type": "ARRAY", "items": {"type": "STRING"}},
        "category": {"type": "STRING", "enum": CATEGORIES},
        "type": {"type": "STRING", "enum": TYPES},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "author": {"type": "STRING"},
        "try_this": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["title", "one_liner", "summary", "key_points", "category", "type", "tags", "author", "try_this"],
}
LINKS = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}, "url": {"type": "STRING"}}, "required": ["name", "url"]}}
GEMINI_SCHEMA = {**SCHEMA, "properties": {**SCHEMA["properties"], "tools": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": [*SCHEMA["required"], "tools"]}
CLAUDE_SCHEMA = {**SCHEMA, "properties": {**SCHEMA["properties"], "links": LINKS}, "required": [*SCHEMA["required"], "links"]}

PROMPT = """다음 콘텐츠를 한국어로 분석해 JSON으로 답한다.
독자는 Claude Code·에이전트 스킬·새 AI 모델을 따라가는 개발자다. 나중에 에이전트가 이 결과를 모아 읽는다.

- title: 콘텐츠 제목. 원제가 있으면 그대로, 없으면 내용을 대표하는 제목
- one_liner: 한 줄 요약 (60자 이내)
- summary: 3~5문장 요약
- key_points: 핵심 포인트 3~6개
- category: 다음 중 하나 — {categories}
- type: 다음 중 하나 — {types}
- tags: 아래 기존 태그에서 우선 고르고, 맞는 게 없을 때만 신규 최대 1개. 총 2~4개
- author: 작성자/채널명. 모르면 빈 문자열
- try_this: 독자가 바로 해볼 수 있는 것(설치할 스킬·도구, 실행할 명령, 바꿀 설정, 열어볼 링크). 구체적으로 0~5개. 없으면 빈 배열
{extra}
기존 태그: {tags}
"""
GEMINI_EXTRA = "- tools: 콘텐츠가 소개한 스킬·도구·레포·서비스·모델의 고유 이름. 최대 5개. 없으면 빈 배열\n"
CLAUDE_EXTRA = """- links: 콘텐츠가 소개한 스킬·도구·레포·서비스마다 공식 다운로드·설치·저장소 링크. web_search로 찾고, 검색 결과나 본문에 나온 URL을 그대로 복사한다. 확인 못 한 것은 넣지 않는다. 최대 5개

분석이 끝나면 save_result 도구를 한 번 호출해 결과를 넘긴다. 원문을 읽지 못했으면(로그인 벽·빈 페이지 등) save_result를 호출하지 말고 못 읽은 이유만 한 문장으로 답한다.
"""
LINK_PROMPT = """다음은 영상이 소개한 스킬·도구 이름이다: {names}
영상 요약: {about}
각각의 공식 다운로드·설치·저장소 링크를 web_search로 찾아라. 검색 결과에 나온 URL을 그대로 복사하고, 확인 못 한 것은 뺀다.
끝나면 save_links 도구를 한 번 호출한다."""


API = "https://generativelanguage.googleapis.com"
CLAUDE_API = "https://api.anthropic.com/v1/messages"
WEB_SEARCH = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
WEB_FETCH = {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 2}
FALLBACK_NOTE = "(Gemini 사용 불가로 영상을 보지 못함. 페이지 제목·설명·검색 기반 요약)"


def upload_video(data, mime):
    """Gemini Files API (resumable upload). Returns file resource once ACTIVE. Files auto-expire after 48h."""
    key = {"x-goog-api-key": get("gemini-api-key")}
    start = urllib.request.Request(
        f"{API}/upload/v1beta/files",
        data=json.dumps({"file": {"display_name": "telegram-video"}}).encode(),
        headers={**key, "Content-Type": "application/json", "X-Goog-Upload-Protocol": "resumable",
                 "X-Goog-Upload-Command": "start", "X-Goog-Upload-Header-Content-Length": str(len(data)),
                 "X-Goog-Upload-Header-Content-Type": mime},
    )
    with urllib.request.urlopen(start, timeout=60) as r:
        upload_url = r.headers["x-goog-upload-url"]
    put = urllib.request.Request(upload_url, data=data, headers={
        "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize", "Content-Length": str(len(data))})
    with urllib.request.urlopen(put, timeout=180) as r:
        f = json.loads(r.read())["file"]
    for _ in range(60):  # video processing usually takes a few seconds
        if f["state"] == "ACTIVE":
            return f
        if f["state"] == "FAILED":
            raise RuntimeError(f"Gemini 영상 처리 실패: {f.get('error')}")
        time.sleep(2)
        f = request("GET", f"{API}/v1beta/{f['name']}", key)
    raise TimeoutError("Gemini 영상 처리 대기 시간 초과")


class Unreadable(Exception):
    """Claude couldn't read the source and declined to summarize instead of guessing. Message = its reason."""


def _post(url, headers, body, waits=(5, 20)):
    """POST with retry on transient errors (Gemini 503 'high demand', Claude 529 overloaded).
    429 is not retried: Gemini's free tier sends it for the daily quota, where waiting doesn't help."""
    for wait in (*waits, None):
        try:
            return request("POST", url, headers, body, timeout=240)
        except (RuntimeError, TimeoutError, OSError) as e:
            transient = isinstance(e, (TimeoutError, OSError)) or any(f"-> {c}:" in str(e) for c in (500, 503, 529))
            if wait is None or not transient:
                raise
            time.sleep(wait)


def _json_schema(s):
    """Gemini's OpenAPI-style schema -> JSON Schema for a Claude strict tool."""
    out = {k: v for k, v in s.items() if k not in ("type", "properties", "items")}
    out["type"] = s["type"].lower()
    if "properties" in s:
        out["properties"] = {k: _json_schema(v) for k, v in s["properties"].items()}
        out["additionalProperties"] = False
    if "items" in s:
        out["items"] = _json_schema(s["items"])
    return out


def _claude(prompt, name, schema, web_tools):
    """Claude with server web tools plus one strict client tool that carries the answer.
    Returns (tool input, prompt + every tool result as text) so links can be checked against what was retrieved."""
    tool = {"name": name, "description": "최종 결과를 저장한다", "input_schema": _json_schema(schema), "strict": True}
    seen, done = prompt, []
    for _ in range(4):  # pause_turn = server tool loop hit its cap; send the partial turn back to continue
        messages = [{"role": "user", "content": prompt}] + ([{"role": "assistant", "content": done}] if done else [])
        res = _post(CLAUDE_API, {"x-api-key": get("anthropic-api-key"), "anthropic-version": "2026-10-21"}, {
            "model": os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5"), "max_tokens": 16000,
            "tools": [*web_tools, tool], "messages": messages})
        done += res["content"]
        for b in res["content"]:
            if b["type"].endswith("_tool_result"):
                seen += json.dumps(b.get("content"), ensure_ascii=False)
            elif b["type"] == "tool_use" and b["name"] == name:
                return b["input"], seen
        if res["stop_reason"] != "pause_turn":
            said = " ".join(b["text"] for b in res["content"] if b["type"] == "text")
            raise Unreadable(" ".join(said.split()) or f"stop_reason={res['stop_reason']}")
    raise TimeoutError("Claude pause_turn 반복")


def _verified(links, seen):
    """Keep only URLs that actually appeared in the input or a search/fetch result. Unchecked, models invent URLs (seen in testing)."""
    return [x for x in links if x["url"].startswith("http") and x["url"].rstrip("/") in seen][:5]


def find_links(names, about):
    """Official links for tools a video names. Enrichment only: on failure the summary is kept and links=None."""
    if not names:
        return []
    try:
        r, seen = _claude(LINK_PROMPT.format(names=", ".join(names[:5]), about=about), "save_links",
                          {"type": "OBJECT", "properties": {"links": LINKS}, "required": ["links"]}, [WEB_SEARCH])
        return _verified(r["links"], seen)
    except Exception:
        log.exception("find_links failed")
        return None


def _gemini_video(payload, head):
    parts = [{"text": PROMPT.format(extra=GEMINI_EXTRA, **head)}]
    if "youtube_url" in payload:
        parts.append({"file_data": {"file_uri": payload["youtube_url"]}})
    else:
        f = upload_video(payload["video_bytes"], payload["mime"])
        parts.append({"file_data": {"file_uri": f["uri"], "mime_type": f["mimeType"]}})
        if payload.get("context"):
            parts.append({"text": payload["context"]})
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    res = _post(f"{API}/v1beta/models/{model}:generateContent", {"x-goog-api-key": get("gemini-api-key")}, {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": GEMINI_SCHEMA}})
    result = json.loads(res["candidates"][0]["content"]["parts"][0]["text"])
    result["links"] = find_links(result.pop("tools"), result["one_liner"])
    return result


def _claude_summary(task, head, web_tools):
    result, seen = _claude(f"{PROMPT.format(extra=CLAUDE_EXTRA, **head)}\n{task}", "save_result", CLAUDE_SCHEMA, web_tools)
    result["links"] = _verified(result["links"], seen)
    return result


def summarize(payload, existing_tags=()):
    """payload: {"youtube_url"} | {"video_bytes", "mime"(, "context")} -> Gemini watches, Claude finds links
    | {"url"} (Claude reads it via web_fetch) | {"text"} -> Claude.
    Gemini down (free-tier quota, 503): YouTube falls back to Claude reading the page's title/description."""
    head = dict(categories=" / ".join(CATEGORIES), types=" / ".join(TYPES), tags=", ".join(existing_tags) or "(없음)")
    if "youtube_url" in payload or "video_bytes" in payload:
        try:
            result = _gemini_video(payload, head)
        except Exception as e:
            if "youtube_url" not in payload:  # Claude has no video input; the file isn't kept, so say so
                raise RuntimeError(f"Gemini 사용 불가로 영상 파일 분석 못 함. 나중에 다시 보내줘 ({e})") from e
            log.warning("Gemini failed, falling back to Claude: %s", e)
            result = _claude_summary(
                f"이 유튜브 영상은 직접 볼 수 없다. web_fetch로 {payload['youtube_url']} 페이지의 제목·설명을 읽고, "
                "필요하면 web_search로 보충해 분석하라. 확인한 정보만 쓰고 확인 못 한 내용은 지어내지 않는다.",
                head, [WEB_FETCH, WEB_SEARCH])
            result["one_liner"] = f"[설명 기반] {result['one_liner']}"
            result["summary"] = f"{FALLBACK_NOTE} {result['summary']}"
    elif "url" in payload:
        result = _claude_summary(f"다음 URL을 web_fetch로 읽고 분석하라: {payload['url']}", head, [WEB_FETCH, WEB_SEARCH])
    else:
        result = _claude_summary(f"---\n{payload['text'][:200_000]}", head, [WEB_SEARCH])
    result["tags"] = [t.strip() for t in result["tags"] if t.strip()][:4]
    return result
