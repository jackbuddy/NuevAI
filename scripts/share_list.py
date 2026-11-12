#!/usr/bin/env python
"""Review what the Drive ingest did: recent rows of share_ingest_log, the manifest of shared files, and the indexed chunks. Read-only.

    python scripts/share_list.py                      # last 30 log rows (status, type, file title, chunks, events, reason)
    python scripts/share_list.py --status needs_review --limit 100
    python scripts/share_list.py --files              # the manifest: every shared file seen, its status, last modified, chunks, retry counter
    python scripts/share_list.py --chunks             # indexed share-* chunks grouped by file (title, tab, modified, team, chunk/event counts)

Statuses: indexed, needs_review (looks like people data: roster, sign-ups, student columns), skipped (nothing readable), error (will be retried, up to 3
times), failed (gave up until the file changes), unshared (no longer shared: chunks kept but flagged superseded), removed (scripts/share_remove.py).
The log and manifest hold no file contents, no Drive file ids and no owner addresses.
"""
import argparse
import collections
import os
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

STATUSES = ("indexed", "needs_review", "skipped", "error", "failed", "unshared", "removed")


def fetch_log(sb, limit: int, status: str | None) -> list[dict]:
    q = sb.table("share_ingest_log").select("processed_at,source_type,file_hash,name,status,chunk_count,event_count,modified_time,reason,attempts")
    if status:
        q = q.eq("status", status)
    return q.order("processed_at", desc=True).limit(limit).execute().data or []


def fetch_manifest(sb) -> list[dict]:
    return sorted(sb.table("share_ingest_manifest").select("*").limit(1000).execute().data or [], key=lambda r: r.get("modified_time") or "", reverse=True)


def fetch_chunk_summary(sb) -> list[dict]:
    rows, start = [], 0
    while True:
        r = sb.table(settings.CHUNKS_TABLE).select("chunk_id,m:metadata").like("chunk_id", "share-%").range(start, start + 999).execute().data or []
        rows += r
        if len(r) < 1000:
            break
        start += 1000
    by = collections.OrderedDict()
    for x in rows:
        m = x.get("m") or {}
        d = by.setdefault(m.get("share_file_hash") or x["chunk_id"].split("-")[1], {"title": m.get("file_title") or m.get("title", ""), "modified": str(m.get("modified_time", ""))[:10], "team": m.get("team", ""),
                                                                                    "type": m.get("source_type", ""), "text": 0, "events": 0, "superseded": bool(m.get("superseded"))})
        d["events" if m.get("source") == "calendar_sheet" else "text"] += 1
    return sorted(by.items(), key=lambda kv: kv[1]["modified"], reverse=True)


def fmt_log(rows: list[dict]) -> str:
    out = [f"{'processed (UTC)':16} {'status':12} {'type':5} {'chunks':>6} {'events':>6}  {'id':8} file  [reason]"]
    for r in rows:
        out.append(f"{str(r.get('processed_at') or '')[:16].replace('T', ' '):16} {r['status']:12} {r.get('source_type') or '':5} {r.get('chunk_count') or 0:>6} {r.get('event_count') or 0:>6}  "
                   f"{(r.get('file_hash') or '')[:8]:8} {(r.get('name') or '(untitled)')[:50]}" + (f"  [{r['reason'][:100]}]" if r.get("reason") else ""))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--status", choices=STATUSES)
    ap.add_argument("--files", action="store_true")
    ap.add_argument("--chunks", action="store_true")
    a = ap.parse_args()
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set (put them in .env).")
        return 1
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"].strip().strip("\"'"), os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip().strip("\"'"))
    ok = True
    if a.files:
        try:
            rows = fetch_manifest(sb)
            print(f"{'status':12} {'type':5} {'modified (UTC)':16} {'chunks':>6} {'events':>6} {'tries':>5} {'missing':>7}  {'id':8} file")
            for r in rows:
                print(f"{r['status']:12} {r['source_type']:5} {str(r.get('modified_time') or '')[:16].replace('T', ' '):16} {r.get('chunk_count') or 0:>6} {r.get('event_count') or 0:>6} "
                      f"{r.get('attempts') or 0:>5} {r.get('missing_runs') or 0:>7}  {r['file_hash'][:8]:8} {(r.get('name') or '')[:50]}")
        except Exception as e:
            print(f"cannot read share_ingest_manifest ({type(e).__name__}); has supabase/share_ingest.sql been run?")
            ok = False
    elif not a.chunks:
        try:
            rows = fetch_log(sb, a.limit, a.status)
            print(fmt_log(rows) if rows else "the log is empty")
        except Exception as e:
            print(f"cannot read share_ingest_log ({type(e).__name__}); has supabase/share_ingest.sql been run?")
            ok = False
    if a.chunks:
        print("\nindexed shared files (newest first):")
        for fh, d in fetch_chunk_summary(sb):
            print(f"  {d['modified']}  {d['type']:5} {d['text']:>3} text chunk(s) {d['events']:>3} event(s)  {fh[:8]:8} {d['title'][:50]}  {d['team']}" + ("  [superseded]" if d["superseded"] else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
