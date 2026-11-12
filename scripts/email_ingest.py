#!/usr/bin/env python
"""Poll the dedicated Gmail account: forwarded school emails (IMAP) AND Google Sheets/Docs shared with it (Drive API). See README > Auto-ingestion.

    python scripts/email_ingest.py                 # one pass of both parts, incremental
    python scripts/email_ingest.py --only email    # just the mailbox (unread mail -> checks -> chunks -> Supabase, then marks it read)
    python scripts/email_ingest.py --only google   # just the shared Sheets/Docs (new or changed since the last successful ingest)
    python scripts/email_ingest.py --dry-run       # connect and analyse, but write nothing and mark nothing read
    python scripts/email_ingest.py --limit 10      # at most 10 messages / files this run (default: 50 messages, all changed files)
    python scripts/email_ingest.py --only google --file-id <Drive file id>   # re-ingest ONE file now, even if unchanged (--force: every file)
    python scripts/email_ingest.py --eml a.eml     # analyse saved .eml files offline (always a dry run; no mailbox, no database)
    python scripts/email_ingest.py --sheet-json s.json  # analyse a saved spreadsheets.get response offline (tables, events, chunks; no network)

Email needs EMAIL_INGEST_USER, EMAIL_INGEST_APP_PASSWORD, EMAIL_ALLOWED_SENDERS; Google needs GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET,
GOOGLE_OAUTH_REFRESH_TOKEN; both need SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY unless --dry-run. A part that is not configured prints a clear message and is
skipped (exit 0) without affecting the other. Exit 1 only on systemic failures (login/auth, Google API access, Drive listing, database). A bad message or a
oddly structured file is logged and skipped. Values are never printed; subjects / file names are only printed with --show-subjects / --show-names.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from pipeline import email_ingest as ei   # noqa: E402


def run_email(a) -> int:
    cfg = ei.load_config()
    if cfg is None:
        print(ei.NOT_CONFIGURED)
        return 0
    if a.eml:
        for p in a.eml:
            r = ei.handle(Path(p).read_bytes(), cfg, None, dry_run=True)
            print(f"{p}: {r.status} {r.reason} (chunks={r.chunk_count}, attachments skipped={r.attachments_skipped})")
            if r.status == "indexed":
                print(f"  title: {r.subject}\n  source date: {r.source_date}\n  thread key: {r.thread_key}\n  chunk ids: {[c['chunk_id'] for c in r.chunks]}")
                if a.show_subjects:
                    print("  ----\n" + "\n  ----\n".join(c["content"] for c in r.chunks))
        return 0
    store = None
    if not a.dry_run:
        try:
            store = ei.SupabaseStore()
        except SystemExit as e:
            print(str(e))
            return 1
    return ei.run(cfg, store, dry_run=a.dry_run, limit=a.limit or 50, show_subjects=a.show_subjects)


def run_google(a) -> int:
    from pipeline import gapi, share_ingest as si
    cfg = gapi.load_config()
    if cfg is None:
        print(gapi.NOT_CONFIGURED)
        return 0
    store = None
    if not a.dry_run:
        try:
            store = si.ShareStore()
        except SystemExit as e:
            print(str(e))
            return 1
    return si.run(cfg, store, dry_run=a.dry_run, limit=a.limit, show_names=a.show_names, file_id=a.file_id, force=a.force)


def analyze_sheet_json(path: str, show: bool) -> int:
    from pipeline import gsheets
    book = json.loads(Path(path).read_text(encoding="utf-8"))
    meta = {"file_id": "offline-" + Path(path).stem, "name": (book.get("properties") or {}).get("title") or Path(path).stem,
            "modified_time": datetime.now().astimezone().isoformat(), "version": "1", "owner_domain": ""}
    r = gsheets.analyze_workbook(book, meta)
    print(f"{path}: {r.status} ({r.reason}); chunks={r.chunk_count} events={r.events} tables={r.tables} team={r.team!r}")
    for n in r.notes:
        print(f"  note: {n}")
    if show:
        for c in r.chunks:
            print(f"  ---- {c['chunk_id']} ({c['metadata'].get('kind')})\n{c['content']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=("email", "google", "all"), default="all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="one pass and exit (this is already the default; accepted for clarity)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--file-id", help="re-ingest this one Drive file now, even if unchanged (implies --only google)")
    ap.add_argument("--force", action="store_true", help="re-ingest every shared file even if unchanged")
    ap.add_argument("--eml", nargs="+", help="saved .eml files to analyse offline instead of reading the mailbox")
    ap.add_argument("--sheet-json", help="a saved spreadsheets.get JSON to analyse offline")
    ap.add_argument("--show-subjects", action="store_true", help="print message subjects / chunk text (do not use where logs are public)")
    ap.add_argument("--show-names", action="store_true", help="print shared file names (do not use where logs are public)")
    a = ap.parse_args()
    if a.sheet_json:
        return analyze_sheet_json(a.sheet_json, a.show_subjects)
    if a.eml:
        return run_email(a)
    only = "google" if a.file_id else a.only
    code = 0
    if only in ("email", "all"):
        code = max(code, run_email(a))
    if only in ("google", "all"):
        code = max(code, run_google(a))
    return code


if __name__ == "__main__":
    sys.exit(main())
