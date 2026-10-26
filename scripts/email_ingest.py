#!/usr/bin/env python
"""Poll the dedicated Gmail inbox and index forwarded school emails (see pipeline/email_ingest.py).

    python scripts/email_ingest.py                 # one pass: unread mail -> checks -> chunks -> Supabase, then marks it read
    python scripts/email_ingest.py --dry-run       # connect and analyse, but write nothing and mark nothing read
    python scripts/email_ingest.py --limit 10      # at most 10 messages this run (default 50)
    python scripts/email_ingest.py --eml a.eml     # analyse saved .eml files offline (always a dry run; no mailbox, no database)

Needs EMAIL_INGEST_USER, EMAIL_INGEST_APP_PASSWORD, EMAIL_ALLOWED_SENDERS (and SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY unless --dry-run).
Without them it prints "not configured" and exits 0. Exit 1 only on real errors. Values are never printed; message subjects are only
printed with --show-subjects (Actions logs can be public).
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from pipeline import email_ingest as ei   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="one pass and exit (this is already the default; accepted for clarity)")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--eml", nargs="+", help="saved .eml files to analyse offline instead of reading the mailbox")
    ap.add_argument("--show-subjects", action="store_true", help="print message subjects (do not use where logs are public)")
    a = ap.parse_args()
    cfg = ei.load_config()
    if cfg is None:
        print(ei.NOT_CONFIGURED)
        return 0
    if a.eml:
        code = 0
        for p in a.eml:
            r = ei.handle(Path(p).read_bytes(), cfg, None, dry_run=True)
            print(f"{p}: {r.status} {r.reason} (chunks={r.chunk_count}, attachments skipped={r.attachments_skipped})")
            if r.status == "indexed":
                print(f"  title: {r.subject}\n  source date: {r.source_date}\n  chunk ids: {[c['chunk_id'] for c in r.chunks]}")
                if a.show_subjects:
                    print("  ----\n" + "\n  ----\n".join(c["content"] for c in r.chunks))
        return code
    store = None
    if not a.dry_run:
        try:
            store = ei.SupabaseStore()
        except SystemExit as e:
            print(str(e))
            return 1
    return ei.run(cfg, store, dry_run=a.dry_run, limit=a.limit, show_subjects=a.show_subjects)


if __name__ == "__main__":
    sys.exit(main())
