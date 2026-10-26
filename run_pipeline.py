"""One command to refresh the NuevAI knowledge base, end to end, unattended.

    python run_pipeline.py                      # public site + Google docs + Nexus import -> chunk -> index
    python run_pipeline.py --refresh-nexus      # also re-scrape Nexus with the saved session
    python run_pipeline.py --stages chunk,index # re-chunk and re-index already-downloaded pages
    python run_pipeline.py --dry-run            # crawl/chunk, then show what indexing WOULD change

Stages: public (once), nexus, google, live, manual, calendar, lunch (cafe menus), chunk, index.  A JSON report of every run is
written to data/pipeline/report.json; documents that could not be read
(private Google Docs) are listed in data/pipeline/needs_access.json.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

from pipeline import calendar_feed, chunker, config, crawl_public, fetch_google, index, live_sheets, lunch, manual_import, nexus_import

ALL = ["public", "nexus", "google", "live", "manual", "calendar", "lunch", "chunk", "index"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stages", default=",".join(ALL))
    ap.add_argument("--max-public-pages", type=int, default=config.MAX_PUBLIC_PAGES)
    ap.add_argument("--refresh-public", action="store_true", help="re-crawl nuevaschool.org (normally crawled once)")
    ap.add_argument("--refresh-nexus", action="store_true", help="re-run the Nexus scraper with the saved session")
    ap.add_argument("--prune", action="store_true", help="delete chunks this pipeline wrote that no longer exist")
    ap.add_argument("--prune-legacy", action="store_true", help="delete rows from the old pipeline")
    ap.add_argument("--keep-expired", action="store_true", help="do not delete expired calendar events during indexing")
    ap.add_argument("--dry-run", action="store_true", help="do not embed or write to Supabase")
    ap.add_argument("--only-source", default=None, help="index stage: only plan and write rows of this metadata.source (for example lunch)")
    ap.add_argument("--no-lunch-download", action="store_true", help="lunch stage: read only manual drops (data/pipeline/manual_docs/lunch/), no network")
    args = ap.parse_args()
    load_dotenv()
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    bad = set(stages) - set(ALL)
    if bad:
        ap.error(f"unknown stages {bad}")

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    report = {"started": datetime.now(timezone.utc).isoformat(), "stages": {}}
    t0 = time.time()
    scraper_data = config.NEXUS_SCRAPER_DIR / "data"

    def stage(name, fn):
        if name in stages:
            print(f"\n=== {name} ===", flush=True)
            report["stages"][name] = fn()
            report["stages"][name]["seconds"] = round(time.time() - t0)

    def public():
        if config.PUBLIC_MARKER.exists() and not args.refresh_public:
            return {"status": "skipped: already crawled once (use --refresh-public to redo)"}
        s = crawl_public.run(args.max_public_pages)
        config.PUBLIC_MARKER.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **s}))
        return s

    stage("public", public)
    stage("nexus", lambda: nexus_import.run(args.refresh_nexus))
    stage("google", lambda: fetch_google.run([scraper_data / "google_links_deduped.json", scraper_data / "external_links.json"]))
    stage("live", live_sheets.run)
    stage("manual", manual_import.run)
    stage("calendar", calendar_feed.run)
    stage("lunch", lambda: lunch.run_soft(discover_online=not args.no_lunch_download))
    stage("chunk", lambda: {"chunks": len(chunker.run())})
    stage("index", lambda: index.run(args.prune, args.prune_legacy, args.dry_run, keep_expired=args.keep_expired, only_source=args.only_source))

    report["finished"] = datetime.now(timezone.utc).isoformat()
    config.REPORT_FILE.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"\nreport -> {config.REPORT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
