"""Notion API 2025-09-03. All page writes go through data_source_id."""
import datetime

from common import get, request

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"

FAIL_MARK = "❌ "  # prefix of the error note mark_failed appends; skipped when a row is reprocessed
_ds = {}  # data_source_id + known tag options, filled on cold start


def _call(method, path, body=None):
    return request(method, API + path, {"Authorization": f"Bearer {get('notion-token')}", "Notion-Version": VERSION}, body)


def init():
    if _ds:
        return
    db = _call("GET", f"/databases/{get('notion-database-id')}")
    ds_id = db["data_sources"][0]["id"]
    ds = _call("GET", f"/data_sources/{ds_id}")
    _ds["id"] = ds_id
    _ds["tags"] = {o["name"] for o in ds["properties"]["tags"]["multi_select"]["options"]}
    if "summary" not in ds["properties"]:  # one-time schema fix
        _call("PATCH", f"/data_sources/{ds_id}", {"properties": {"summary": {"rich_text": {}}}})


def tag_options():
    return sorted(_ds["tags"])


def _rt(s):
    return {"rich_text": [{"text": {"content": (s or "")[:2000]}}]}


def _sel(s):
    return {"select": {"name": s}}


def _para(s):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": s[:2000]}}]}}


def create_page(url=None, source="other", memo=""):
    props = {
        "title": {"title": [{"text": {"content": (url or "(영상)")[:2000]}}]},
        "source": _sel(source),
        "status": _sel("미확인"),
    }
    if url:
        props["url"] = {"url": url}
    if memo:
        props["memo"] = _rt(memo)
    return _call("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": _ds["id"]}, "properties": props})


def query_pending():
    """Rows with empty one_liner = the queue. Failed rows are skipped; set status back to 미확인 to retry."""
    body = {
        "filter": {"and": [
            {"property": "one_liner", "rich_text": {"is_empty": True}},
            {"property": "status", "select": {"does_not_equal": "실패"}},
        ]},
        "page_size": 50,
    }
    return _call("POST", f"/data_sources/{_ds['id']}/query", body)["results"]


def fetch_page_body(page_id):
    # ponytail: top-level blocks only; recurse if the clipper starts nesting content
    texts, cursor = [], None
    while True:
        q = "?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        res = _call("GET", f"/blocks/{page_id}/children{q}")
        for b in res["results"]:
            rich = b.get(b["type"], {}).get("rich_text", [])
            t = "".join(r.get("plain_text", "") for r in rich)
            if t and not t.startswith(FAIL_MARK):  # our own error note is not content to summarize
                texts.append(t)
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]
    return "\n".join(texts)


def ensure_tag_options(tags):
    """multi_select rejects unknown options (400) - add them to the schema first."""
    new = [t for t in tags if t not in _ds["tags"]]
    if not new:
        return
    options = [{"name": t} for t in sorted(_ds["tags"] | set(new))]
    _call("PATCH", f"/data_sources/{_ds['id']}", {"properties": {"tags": {"multi_select": {"options": options}}}})
    _ds["tags"].update(new)


def _h2(s):
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": s}}]}}


def _bullets(items):
    return [
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": i[:2000]}}]}}
        for i in items
    ]


def _now():
    return {"date": {"start": datetime.datetime.now(datetime.timezone.utc).isoformat()}}


def _links(links):
    return [
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [
            {"text": {"content": f"{x['name'][:200]}: "}}, {"text": {"content": x["url"][:2000], "link": {"url": x["url"]}}}]}}
        for x in links
    ]


def result_blocks(r, url):
    """Fixed heading order so a weekend agent can parse every row the same way."""
    links = r.get("links", [])  # None = link search failed
    return (
        [_h2("요약"), _para(r["summary"])]
        + [_h2("핵심")] + _bullets(r.get("key_points", []))
        + [_h2("해볼 것")] + (_bullets(r["try_this"]) if r.get("try_this") else [_para("없음")])
        + [_h2("링크")] + (_links(links) if links else [_para("검색 실패" if links is None else "없음")])
        + [_h2("원문"), _para(" · ".join(x for x in (url, r.get("author")) if x) or "-")]
    )


def update_result(page_id, r, url=None):
    """Writes AI fields only. memo is never touched."""
    props = {
        "title": {"title": [{"text": {"content": r["title"][:2000]}}]},
        "one_liner": _rt(r["one_liner"]),
        "summary": _rt(r["summary"]),
        "author": _rt(r.get("author", "")),
        "category": _sel(r["category"]),
        "type": _sel(r["type"]),
        "tags": {"multi_select": [{"name": t} for t in r["tags"]]},
        "status": _sel("미확인"),
        "saved_at": _now(),
    }
    _call("PATCH", f"/pages/{page_id}", {"properties": props})
    _call("PATCH", f"/blocks/{page_id}/children", {"children": result_blocks(r, url)})


def mark_saved_only(page_id, note):
    """Content we can't read (e.g. Instagram without caption): fill one_liner so it leaves the queue."""
    _call("PATCH", f"/pages/{page_id}", {"properties": {"one_liner": _rt(note), "status": _sel("미확인"), "saved_at": _now()}})


def mark_failed(page_id, err):
    _call("PATCH", f"/pages/{page_id}", {"properties": {"status": _sel("실패")}})
    _call("PATCH", f"/blocks/{page_id}/children", {"children": [_para(f"{FAIL_MARK}{err}")]})
