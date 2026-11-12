#!/usr/bin/env python
"""Remove one shared Sheet/Doc's data from the knowledge base (its chunks in document_chunks_v2).

    python scripts/share_remove.py "Varsity Soccer"            # a file-title substring (min 4 chars): SHOWS what would be removed (dry run is the default)
    python scripts/share_remove.py 3fa9c1d2e4b5                # the file hash printed by share_list.py (8-12 hex)
    python scripts/share_remove.py 1AbC...DriveFileId          # a Drive file id (hashed locally; never printed or stored)
    python scripts/share_remove.py "Varsity Soccer" --yes      # actually delete

Only rows whose chunk_id starts with "share-" can ever be matched or deleted. The manifest row is kept and set to status 'removed' with the file's current
modifiedTime, so the file is NOT ingested again until it changes (or you force it: `python scripts/email_ingest.py --only google --file-id <id>`).
If a query matches more than 3 distinct files, add --allow-many. Never run this against a database you did not mean to change: the default is a dry run.
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

PREFIX = "share-"


def share_rows(sb) -> list[dict]:
    rows, start = [], 0
    while True:
        r = sb.table(settings.CHUNKS_TABLE).select("chunk_id,metadata").like("chunk_id", PREFIX + "%").range(start, start + 999).execute().data or []
        rows += r
        if len(r) < 1000:
            return [x for x in rows if x["chunk_id"].startswith(PREFIX)]
        start += 1000


def file_hash(value: str) -> str:
    from pipeline.gsheets import file_hash as h
    return h(value)


def _fh(r: dict) -> str:
    return str((r.get("metadata") or {}).get("share_file_hash") or r["chunk_id"].split("-")[1]).lower()


def match(rows: list[dict], query: str) -> list[dict]:
    """Every chunk (text AND event records) of every file the query names: a file hash, a Drive file id, or a substring of the file title.
    A match on any one row selects the whole file, so no event record is ever left behind."""
    q = query.strip()
    cands = {file_hash(q)} if re.fullmatch(r"[A-Za-z0-9_-]{20,}", q) else set()
    hashes = set()
    for r in rows:
        m = r.get("metadata") or {}
        fh = _fh(r)
        if fh in cands or (re.fullmatch(r"[0-9a-f]{8,12}", q.lower()) and fh.startswith(q.lower())):
            hashes.add(fh)
        elif len(q) >= 4 and q.lower() in (str(m.get("file_title", "")) + " " + str(m.get("title", ""))).lower():
            hashes.add(fh)
    return [r for r in rows if _fh(r) in hashes]


def group(rows: list[dict]) -> dict:
    g = {}
    for r in rows:
        m = r.get("metadata") or {}
        fh = str(m.get("share_file_hash") or r["chunk_id"].split("-")[1])
        d = g.setdefault(fh, {"title": m.get("file_title") or m.get("title", ""), "modified": str(m.get("modified_time", ""))[:10], "ids": []})
        d["ids"].append(r["chunk_id"])
    return g


def remove(sb, ids: list[str]) -> int:
    ids = [i for i in ids if i.startswith(PREFIX)]          # belt and braces: never anything but share rows
    for i in range(0, len(ids), 50):
        sb.table(settings.CHUNKS_TABLE).delete().in_("chunk_id", ids[i:i + 50]).execute()
    return len(ids)


def mark_removed(sb, fh: str) -> None:
    try:
        cur = (sb.table("share_ingest_manifest").select("*").eq("file_hash", fh).execute().data or [None])[0]
        if cur:
            cur = {k: v for k, v in cur.items() if k != "updated_at"}
            cur.update({"status": "removed", "chunk_count": 0, "event_count": 0, "reason": "removed with scripts/share_remove.py"})
            sb.table("share_ingest_manifest").upsert(cur, on_conflict="file_hash").execute()
    except Exception:
        pass


def execute(sb, query: str, delete: bool, allow_many: bool = False, out=print) -> int:
    hits = match(share_rows(sb), query)
    g = group(hits)
    if not g:
        out("no matching shared-file chunks")
        return 1
    for k, d in g.items():
        out(f"  {d['modified']}  {len(d['ids'])} chunk(s)  {k}  {d['title'][:80]}")
    if len(g) > 3 and not allow_many:
        out(f"{len(g)} different files match; narrow the query or add --allow-many. Nothing deleted.")
        return 2
    if not delete:
        out(f"DRY RUN: {len(hits)} chunk(s) of {len(g)} file(s) would be removed. Add --yes to delete them.")
        return 0
    n = remove(sb, [r["chunk_id"] for r in hits])
    for fh in g:
        mark_removed(sb, fh)
    out(f"deleted {n} chunk(s) of {len(g)} file(s); the manifest says 'removed' until the file changes")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", help="file hash, Drive file id, or a substring of the file title (min 4 characters)")
    ap.add_argument("--dry-run", action="store_true", help="show what would be removed (this is the default)")
    ap.add_argument("--yes", action="store_true", help="really delete (without it nothing is deleted)")
    ap.add_argument("--allow-many", action="store_true")
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


if __name__ == "__main__":
    sys.exit(main())
