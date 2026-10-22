"""Stage 1: crawl the public Nueva website (no login needed).

Seeds include the HTML /site-map page (the site has no sitemap.xml), then
breadth-first follows same-host links. Honors robots.txt, including the
wildcard rules urllib.robotparser ignores, and the 5s crawl delay.
"""
import re
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import config
from .common import SKIP_EXT, Fetcher, extract_page, norm_url, save_page


class Robots:
    def __init__(self, text: str):
        self.rules = []  # (is_allow, compiled regex, length)
        applies = False
        for raw in text.splitlines():
            line = raw.split("#")[0].strip()
            if ":" not in line:
                continue
            k, v = (x.strip() for x in line.split(":", 1))
            k = k.lower()
            if k == "user-agent":
                applies = v == "*"
            elif applies and k in ("allow", "disallow") and v:
                pat = re.escape(v).replace(r"\*", ".*")
                pat = pat[:-2] + "$" if pat.endswith(r"\$") else pat
                self.rules.append((k == "allow", re.compile(pat), len(v)))

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        target = p.path + (f"?{p.query}" if p.query else "")
        best = None
        for is_allow, rx, n in self.rules:
            if rx.match(target) and (best is None or n > best[1] or (n == best[1] and is_allow)):
                best = (is_allow, n)
        return True if best is None else best[0]


def in_scope(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and p.netloc == config.PUBLIC_HOST and not SKIP_EXT.search(p.path)


def run(max_pages: int = config.MAX_PUBLIC_PAGES, log=print) -> dict:
    f = Fetcher()
    r = f.get(f"https://{config.PUBLIC_HOST}/robots.txt")
    robots = Robots(r.text if r is not None and r.ok else "")
    queue, seen = deque(config.PUBLIC_SEEDS), set(map(norm_url, config.PUBLIC_SEEDS))
    saved = skipped = failed = 0
    while queue and saved < max_pages:
        url = queue.popleft()
        if not robots.allowed(url):
            skipped += 1
            continue
        r = f.get(url)
        if r is None or r.status_code >= 400:
            failed += 1
            continue
        ctype = r.headers.get("content-type", "")
        final = norm_url(r.url)
        if "html" not in ctype:
            continue
        if final != url and not in_scope(final):
            continue
        title, text, links = extract_page(r.text, final)
        for l in links:
            u = l["url"]
            if in_scope(u) and u not in seen:
                seen.add(u)
                queue.append(u)
        if len(text) < 80:
            continue
        save_page({
            "url": final, "title": title, "source": "public_site", "text": text, "links": links,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
        saved += 1
        if saved % 25 == 0:
            log(f"  public: {saved} pages saved, {len(queue)} queued")
    stats = {"saved": saved, "robots_skipped": skipped, "failed": failed, "left_in_queue": len(queue)}
    log(f"public crawl done: {stats}")
    return stats
