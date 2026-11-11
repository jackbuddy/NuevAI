"""Stage `public_pages`: re-read a short, curated list of PUBLIC nuevaschool.org pages whose content changes every year.

The one-time crawl (crawl_public.py) captured the admissions and transportation pages with their FAQ answers collapsed
(`aria-hidden` accordion panels were treated as page chrome), so the index held questions without answers, and the Visit & Apply
pages of the previous year. This stage fetches each page in `config.PUBLIC_PAGES` ONCE per run with a plain GET (project
user agent, robots.txt honoured, crawl delay, timeout), keeps the raw HTML for `CACHE_HOURS` so re-running chunk/index never
re-fetches, and saves the text as a `public_site` page whose chunk ids live in their own namespace (`id_seed` = url + "#public"):
the earlier copy of the same URL is never overwritten, it is only flagged `superseded` by scripts/flag_rows.py once this one is indexed.

Login-gated pages are not fetched (a login page is detected and skipped).
"""
import hashlib
import json
import time
from datetime import datetime, timezone

from . import config
from .common import Fetcher, extract_page, norm_url, slug
from .crawl_public import Robots

CACHE_HOURS = 20
CACHE_DIR = config.DATA_DIR / "public_html"


def page_path(url: str):
    return config.PAGES_DIR / f"pub-{slug(url)}.json"


def id_seed(url: str) -> str:
    return url + "#public"


def run(log=print, urls=None) -> dict:
    urls = list(urls or config.PUBLIC_PAGES)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    config.PAGES_DIR.mkdir(parents=True, exist_ok=True)
    f = Fetcher()
    robots = None
    fetched = cached = skipped = failed = 0
    for url in urls:
        url = norm_url(url)
        cache = CACHE_DIR / f"{slug(url)}.html"
        if cache.exists() and (time.time() - cache.stat().st_mtime) < CACHE_HOURS * 3600:
            html, when, cached = cache.read_text(encoding="utf-8"), datetime.fromtimestamp(cache.stat().st_mtime, timezone.utc), cached + 1
        else:
            if robots is None:
                r = f.get(f"https://{config.PUBLIC_HOST}/robots.txt")
                robots = Robots(r.text if r is not None and r.ok else "")
            if not robots.allowed(url):
                skipped += 1
                log(f"  public_pages: robots.txt disallows {url}")
                continue
            r = f.get(url)
            if r is None or r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
                failed += 1
                log(f"  public_pages: could not fetch {url}")
                continue
            html, when, fetched = r.text, datetime.now(timezone.utc), fetched + 1
            cache.write_text(html, encoding="utf-8")
        title, text, links = extract_page(html, url)
        if title.lower().startswith("login") or len(text) < 200:
            skipped += 1
            log(f"  public_pages: {url} is a login/empty page, skipped")
            continue
        rec = {"url": url, "title": title, "source": "public_site", "text": text, "links": links, "id_seed": id_seed(url),
               "scraped_at": when.isoformat(), "category": "public_pages"}
        rec["content_hash"] = hashlib.sha1(text.encode()).hexdigest()
        page_path(url).write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    stats = {"pages": len(urls), "fetched": fetched, "from_cache": cached, "skipped": skipped, "failed": failed}
    log(f"public_pages done: {stats}")
    return stats
