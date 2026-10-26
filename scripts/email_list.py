#!/usr/bin/env python
"""Review what the email ingest did: recent rows of email_ingest_log (status, subject, date). Read-only.

    python scripts/email_list.py                    # last 30
    python scripts/email_list.py --limit 100 --status needs_review
    python scripts/email_list.py --chunks           # also list the indexed email chunks per message (title, date, chunk count)

Statuses: indexed, skipped_privacy, rejected_sender, rejected_auth, duplicate, error, needs_review. The log holds no message bodies and no addresses.
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

STATUSES = ("indexed", "skipped_privacy", "rejected_sender", "rejected_auth", "duplicate", "error", "needs_review")


def fetch_log(sb, limit: int, status: str | None) -> list[dict]:
    q = sb.table("email_ingest_log").select("id,received_at,processed_at,status,subject,sender_domain,message_id_hash,chunk_count,reason,attachments_skipped")
    if status:
        q = q.eq("status", status)
    return q.order("processed_at", desc=True).limit(limit).execute().data or []


def fetch_chunk_summary(sb) -> list[dict]:
    rows, start = [], 0
    while True:
        r = sb.table(settings.CHUNKS_TABLE).select("chunk_id,m:metadata").like("chunk_id", "email-%").range(start, start + 999).execute().data or []
        rows += r
        if len(r) < 1000:
            break
        start += 1000
    by = collections.OrderedDict()
    for x in rows:
        m = x.get("m") or {}
        d = by.setdefault(m.get("content_key") or x["chunk_id"].rsplit("-", 1)[0], {"title": m.get("title", ""), "source_date": m.get("source_date", ""),
                                                                                  "message_id_hash": m.get("message_id_hash", ""), "chunks": 0})
        d["chunks"] += 1
    return sorted(by.values(), key=lambda d: d["source_date"], reverse=True)


def fmt_log(rows: list[dict]) -> str:
    out = [f"{'processed (UTC)':16} {'status':16} {'chunks':>6} {'att':>3}  {'id':8} subject  [reason]"]
    for r in rows:
        out.append(f"{str(r.get('processed_at') or '')[:16].replace('T', ' '):16} {r['status']:16} {r.get('chunk_count') or 0:>6} {r.get('attachments_skipped') or 0:>3}  "
                   f"{(r.get('message_id_hash') or '')[:8]:8} {(r.get('subject') or '(no subject)')[:70]}"
                   + (f"  [{r['reason'][:90]}]" if r.get("reason") else ""))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--status", choices=STATUSES)
    ap.add_argument("--chunks", action="store_true")
    a = ap.parse_args()
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set (put them in .env).")
        return 1
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"].strip().strip("\"'"), os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip().strip("\"'"))
    try:
        rows = fetch_log(sb, a.limit, a.status)
    except Exception as e:
        print(f"cannot read email_ingest_log ({type(e).__name__}); has supabase/email_ingest_log.sql been run?")
        rows = None
    if rows is not None:
        print(fmt_log(rows) if rows else "the log is empty")
    if a.chunks:
        print("\nindexed emails (newest first):")
        for d in fetch_chunk_summary(sb):
            print(f"  {d['source_date']}  {d['chunks']:>2} chunk(s)  {d['message_id_hash'][:8]:8}  {d['title'][:80]}")
    return 0 if rows is not None else 1


if __name__ == "__main__":
    sys.exit(main())
