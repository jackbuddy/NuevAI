#!/usr/bin/env python
"""Guard against a bad scrape wiping the index: count rows in the chunk table before and after indexing.

    python scripts/index_guard.py before            # records the count to data/index_count_before.txt
    python scripts/index_guard.py after [--max-drop 0.10]   # exit 1 if rows dropped by more than 10%

Read-only (one exact-count SELECT; rows of forwarded emails, chunk_id 'email-%', are not counted). Needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY; never prints them.
"""
import argparse
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
STATE = ROOT / "data" / "index_count_before.txt"


def count_rows() -> int:
    from supabase import create_client
    import settings
    sb = create_client(os.environ["SUPABASE_URL"].strip().strip("\"'"), os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip().strip("\"'"))
    # Forwarded emails (chunk_id "email-...", added by scripts/email_ingest.py every 30 minutes) are not part of this pipeline's output: counting
    # them would let new emails mask a real loss of calendar/document rows, so the guard watches only the pipeline's own rows.
    return sb.table(settings.CHUNKS_TABLE).select("chunk_id", count="exact").not_.like("chunk_id", "email-%").limit(1).execute().count or 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["before", "after"])
    ap.add_argument("--max-drop", type=float, default=0.10)
    a = ap.parse_args()
    n = count_rows()
    if a.phase == "before":
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(str(n))
        print(f"index rows before: {n}")
        return 0
    if not STATE.exists():
        print("no 'before' count recorded; cannot compare")
        return 1
    before = int(STATE.read_text())
    drop = (before - n) / before if before else 0.0
    print(f"index rows: before {before}, after {n} ({-drop:+.1%})")
    if drop > a.max_drop:
        print(f"::error::index row count dropped {drop:.1%} (limit {a.max_drop:.0%}). A bad scrape may have deleted data; "
              f"check data/pipeline/report.json and restore from data/backup before the next run.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
