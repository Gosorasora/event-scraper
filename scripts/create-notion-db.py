#!/usr/bin/env python3
"""Create the Notion database the bot writes to.

Usage: NOTION_TOKEN=... python3 scripts/create-notion-db.py <parent page URL or id>
The parent page must have your integration added (page menu > Connections).
Prints the new database id.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from common import CATEGORIES, TYPES, request  # noqa: E402
from notion import API, VERSION  # noqa: E402


def _select(names):
    return {"select": {"options": [{"name": n} for n in names]}}


# Must match what src/notion.py reads and writes.
PROPERTIES = {
    "title": {"title": {}},
    "url": {"url": {}},
    "source": _select(["youtube", "instagram", "linkedin", "article", "other"]),
    "status": _select(["미확인", "읽음", "실패"]),
    "category": _select(CATEGORIES),
    "type": _select(TYPES),
    "tags": {"multi_select": {"options": []}},
    "one_liner": {"rich_text": {}},
    "summary": {"rich_text": {}},
    "memo": {"rich_text": {}},
    "author": {"rich_text": {}},
    "saved_at": {"date": {}},
}


def page_id(s):
    """Last 32 hex chars of a Notion URL path (the slug before them may contain hex too)."""
    pid = s.split("?")[0].split("#")[0].replace("-", "")[-32:]
    int(pid, 16)  # ValueError if it isn't an id
    return pid


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    db = request(
        "POST",
        API + "/databases",
        {"Authorization": "Bearer " + os.environ["NOTION_TOKEN"], "Notion-Version": VERSION},
        {
            "parent": {"type": "page_id", "page_id": page_id(sys.argv[1])},
            "title": [{"text": {"content": "Content Archive"}}],
            "initial_data_source": {"properties": PROPERTIES},
        },
    )
    print(db["id"])


if __name__ == "__main__":
    main()
