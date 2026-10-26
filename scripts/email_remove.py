#!/usr/bin/env python
"""Remove one ingested email from the knowledge base (its chunks in document_chunks_v2).

    python scripts/email_remove.py "Early dismissal"            # a subject substring: SHOWS what would be removed (dry run is the default)
    python scripts/email_remove.py 3fa9c1d2e4b5a678             # a message-id hash or content key (as printed by email_list.py)
    python scripts/email_remove.py "<CAF...@mail.gmail.com>"    # a raw Message-ID
    python scripts/email_remove.py "Early dismissal" --yes      # actually delete

Only rows whose chunk_id starts with "email-" can ever be matched or deleted. The review log keeps its row (the message stays marked as handled
in the mailbox, so it is not ingested again unless you forward it again). If a query matches more than 3 distinct emails, add --allow-many.
"""
import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import settings   # noqa: E402

PREFIX = "email-"


def email_rows(sb) -> list[dict]:
    rows, start = [], 0
    while True:
        r = sb.table(settings.CHUNKS_TABLE).select("chunk_id,metadata").like("chunk_id", PREFIX + "%").range(start, start + 999).execute().data or []
        rows += r
        if len(r) < 1000:
            return [x for x in rows if x["chunk_id"].startswith(PREFIX)]
        start += 1000


def hash_id(value: str) -> str:
    from pipeline.email_ingest import hash_id as h
    return h(value)


def match(rows: list[dict], query: str) -> list[dict]:
    q = query.strip()
    cands = {q.lower()}
    if "@" in q or q.startswith("<"):
        cands.add(hash_id(q))
    out = []
    for r in rows:
        m = r.get("metadata") or {}
        key = r["chunk_id"][len(PREFIX):].rsplit("-", 1)[0]
        keys = {str(m.get("message_id_hash", "")).lower(), str(m.get("content_key", "")).lower(), key.lower()}
        if cands & keys or (len(q) >= 8 and re.fullmatch(r"[0-9a-f]{8,64}", q.lower()) and any(k.startswith(q.lower()) for k in keys if k)):
            out.append(r)
        elif len(q) >= 4 and q.lower() in str(m.get("title", "")).lower():
            out.append(r)
    return out


def group(rows: list[dict]) -> dict:
    g = {}
    for r in rows:
        m = r.get("metadata") or {}
        d = g.setdefault(r["chunk_id"].rsplit("-", 1)[0], {"title": m.get("title", ""), "source_date": m.get("source_date", ""), "ids": []})
        d["ids"].append(r["chunk_id"])
    return g


def remove(sb, ids: list[str]) -> int:
    ids = [i for i in ids if i.startswith(PREFIX)]          # belt and braces: never anything but email rows
    for i in range(0, len(ids), 50):
        sb.table(settings.CHUNKS_TABLE).delete().in_("chunk_id", ids[i:i + 50]).execute()
    return len(ids)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", help="message-id, message-id hash / content key, or a substring of the email's subject (min 4 characters)")
    ap.add_argument("--dry-run", action="store_true", help="show what would be removed (this is the default)")
    ap.add_argument("--yes", action="store_true", help="really delete (without it nothing is deleted)")
    ap.add_argument("--allow-many", action="store_true", help="allow a query that matches more than 3 distinct emails")
    a = ap.parse_args()
    if len(a.query.strip()) < 4:
        print("query too short (at least 4 characters)")
        return 2
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set (put them in .env).")
        return 1
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"].strip().strip("\"'"), os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip().strip("\"'"))
    return execute(sb, a.query, delete=a.yes and not a.dry_run, allow_many=a.allow_many)


def execute(sb, query: str, delete: bool, allow_many: bool = False, out=print) -> int:
    hits = match(email_rows(sb), query)
    g = group(hits)
    if not g:
        out("no matching email chunks")
        return 1
    for k, d in g.items():
        out(f"  {d['source_date']}  {len(d['ids'])} chunk(s)  {k}  {d['title'][:80]}")
    if len(g) > 3 and not allow_many:
        out(f"{len(g)} different emails match; narrow the query or add --allow-many. Nothing deleted.")
        return 2
    if not delete:
        out(f"DRY RUN: {len(hits)} chunk(s) of {len(g)} email(s) would be removed. Add --yes to delete them.")
        return 0
    n = remove(sb, [r["chunk_id"] for r in hits])
    out(f"deleted {n} chunk(s) of {len(g)} email(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
