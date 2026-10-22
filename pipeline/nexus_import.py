"""Stage 3: bring in pages from the authenticated Nexus scraper.

Nexus sits behind school SSO/2FA, so a human has to sign in once
(`python scrape_nexus.py --login ...`). After that the saved session lets this
stage refresh headlessly. When the session expires the refresh is skipped
and reported, and the pages from the last good scrape are still imported.

Everything indexed is queryable through the public chat endpoint, so pages that
contain personal data are filtered out. Put one term per line (a person's name,
class sections, student IDs...) in data/pipeline/private_terms.txt, which is
gitignored; any page containing a term is skipped and counted in the report.
"""
import json
import re
import subprocess
import sys

from . import config
from .common import save_page


def refresh(log=print, timeout_s: int = 3600) -> str:
    scraper = config.NEXUS_SCRAPER_DIR
    auth = scraper / "data" / "auth.json"
    py = scraper / ".venv" / "bin" / "python"
    if not (scraper / "scrape_nexus.py").exists():
        return "skipped: nexus-scraper not found"
    if not auth.exists():
        return "skipped: no saved Nexus session (run scrape_nexus.py --login once)"
    cmd = [str(py if py.exists() else sys.executable), "scrape_nexus.py", "--seed-urls", "urls.txt",
           "--max-depth", "3", "--max-pages", "400", "--delay", "1.5", "--output", "./data"]
    try:
        r = subprocess.run(cmd, cwd=scraper, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return "failed: nexus refresh timed out"
    tail = (r.stdout + r.stderr)[-600:]
    if r.returncode != 0:
        expired = any(w in tail.lower() for w in ("login", "sign-in", "session"))
        return "SESSION EXPIRED: re-run scrape_nexus.py --login" if expired else f"failed (exit {r.returncode}): {tail[-200:]}"
    return "refreshed"


def private_terms() -> list[str]:
    f = config.DATA_DIR / "private_terms.txt"
    return [t.strip().lower() for t in f.read_text(encoding="utf-8").splitlines() if t.strip()] if f.exists() else []


def run(do_refresh: bool, log=print) -> dict:
    status = refresh(log) if do_refresh else "not refreshed (use --refresh-nexus)"
    log(f"nexus refresh: {status}")
    pages_dir = config.NEXUS_SCRAPER_DIR / "data" / "pages"
    terms = private_terms()
    imported = filtered = 0
    for p in sorted(pages_dir.glob("*.json")) if pages_dir.exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        except ValueError:
            continue
        text = d.get("raw_text") or ""
        if len(text) < 80:
            continue
        if re.search(r"contactcard|#profile/|/profile/", d.get("url", ""), re.I):   # individual people's cards
            filtered += 1
            continue
        low = text.lower()
        if any(t in low for t in terms):
            filtered += 1
            continue
        save_page({"url": d["url"], "title": d.get("title") or d["url"], "source": "nexus",
                   "category": d.get("category", ""), "text": text, "links": [],
                   "scraped_at": d.get("scraped_at", "")})
        imported += 1
    stats = {"refresh": status, "imported": imported, "filtered_private": filtered, "private_terms_loaded": len(terms)}
    log(f"nexus import done: {stats}")
    return stats
