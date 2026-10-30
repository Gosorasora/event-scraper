"""Shared config (SSM, cached per container) and a tiny JSON HTTP helper."""
import json
import os
import urllib.error
import urllib.request

KEYS = [
    "gemini-api-key",
    "notion-token",
    "telegram-bot-token",
    "telegram-secret-token",
    "telegram-chat-id",
    "notion-database-id",
    "anthropic-api-key",
]

CATEGORIES = ["AI·LLM", "Cloud·인프라", "에이전트", "개발도구", "크리에이티브", "비즈니스·전략", "학습·기타"]
TYPES = ["뉴스", "Repo", "논문", "블로그·에세이", "서비스·툴"]

_cfg = {}


def get(key):
    """Config value. Env var (GEMINI_API_KEY style) wins for local runs; otherwise SSM, loaded once."""
    if not _cfg:
        for k in KEYS:
            v = os.environ.get(k.upper().replace("-", "_"))
            if v:
                _cfg[k] = v
        if len(_cfg) < len(KEYS):
            import boto3

            resp = boto3.client("ssm").get_parameters_by_path(
                Path=os.environ.get("SSM_PREFIX", "/event-scraper"), WithDecryption=True
            )
            for p in resp["Parameters"]:
                _cfg.setdefault(p["Name"].rsplit("/", 1)[1], p["Value"])
    return _cfg[key]


def request(method, url, headers=None, body=None, raw=False, timeout=120):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read() if raw else json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read()[:500].decode(errors="replace")
        raise RuntimeError(f"{method} {url.split('?')[0]} -> {e.code}: {detail}") from e
