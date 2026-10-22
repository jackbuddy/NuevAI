"""Stage 2: pull the text of every Google Doc / Sheet / Slides deck that any
crawled page links to (this is where the course catalog and handbooks live).

Only files shared as "anyone with the link" can be read without a login. Files
that are private are written to needs_access.json so nothing fails silently.
Linked docs are followed MAX_GOOGLE_DEPTH hops deep.
"""
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from . import config
from .common import Fetcher, extract_links, html_to_markdown, load_pages, norm_url, save_page

ID_RX = re.compile(r"/(document|spreadsheets|presentation|file)/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,})")


def parse_google(url: str):
    """-> (kind, file_id) or None."""
    p = urlparse(url)
    if p.netloc not in config.GOOGLE_HOSTS:
        return None
    m = ID_RX.search(p.path)
    if m:
        return {"document": "doc", "spreadsheets": "sheet", "presentation": "slides", "file": "file"}[m.group(1)], m.group(2)
    q = parse_qs(p.query).get("id")
    return ("file", q[0]) if q and "drive.google.com" in p.netloc else None


def fetch_one(f: Fetcher, kind: str, fid: str, source_url: str):
    """-> (title, text, links) or (None, reason, [])"""
    if kind == "doc":
        r = f.get(f"https://docs.google.com/document/d/{fid}/export?format=html")
    elif kind == "sheet":
        r = f.get(f"https://docs.google.com/spreadsheets/d/{fid}/export?format=csv")
    elif kind == "slides":
        r = f.get(f"https://docs.google.com/presentation/d/{fid}/export/txt")
    else:
        return None, "drive file (not a Google Doc) - skipped", []
    if r is None:
        return None, "network error", []
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or ("text/html" in ctype and kind != "doc") or "accounts.google.com" in r.url:
        return None, f"not publicly readable (HTTP {r.status_code}, needs sign-in or sharing)", []
    if kind == "doc":
        soup = BeautifulSoup(r.text, "html.parser")
        title = (soup.title.get_text(strip=True) if soup.title else "") or source_url
        links = extract_links(soup, source_url)
        for l in links:  # Google wraps links in a redirector
            m = re.search(r"[?&]q=([^&]+)", l["url"]) if "google.com/url" in l["url"] else None
            if m:
                from urllib.parse import unquote
                l["url"] = norm_url(unquote(m.group(1)))
        for s in soup.select("style, script"):
            s.decompose()
        return title, html_to_markdown(soup.body or soup), links
    return f"{kind} {fid}", r.text, []


def good_label(t: str) -> bool:
    t = (t or "").strip()
    return len(t) > 3 and not t.lower().startswith("http") and not re.fullmatch(r"(click )?here|link|view|open|copy", t, re.I)


def pick_title(html_title: str, anchor: str, text: str, url: str) -> str:
    """Google's export has no usable <title>: prefer what people call the doc on
    the page that links to it, then the doc's own title, then its first line."""
    first = next((l.lstrip("# ").strip() for l in text.splitlines() if l.strip()), "")
    for cand in (anchor, html_title, first):
        if good_label(cand):
            return re.sub(r"\s*\(opens in new window/tab\)", "", cand.strip())[:140]
    return url


def candidate_urls(extra_files=()):
    found = {}   # (kind, id) -> (url, anchor)

    def add(url, anchor=""):
        g = parse_google(url)
        if not g:
            return
        cur = found.get(g)
        if cur is None or (not good_label(cur[1]) and good_label(anchor)):
            found[g] = (norm_url(url), anchor)

    for page in load_pages():
        for l in page.get("links", []):
            add(l["url"], l["text"])
        for m in re.findall(r"https?://(?:docs|drive)\.google\.com/[^\s)\]>\"']+", page.get("text", "")):
            add(m)
    for path in extra_files:  # links harvested by older scrapes
        try:
            for item in json.loads(open(path, encoding="utf-8").read()):
                u = item.get("url") if isinstance(item, dict) else item
                if u:
                    add(u, item.get("text", "") if isinstance(item, dict) else "")
        except (OSError, ValueError):
            pass
    return {url: anchor for url, anchor in found.values()}


def write_download_list(needs_access: list, extra_files=()):
    """DOWNLOAD_THESE.md: one line per unreadable file, with where it was linked from."""
    meta = {}
    for path in extra_files:
        try:
            for item in json.loads(open(path, encoding="utf-8").read()):
                if isinstance(item, dict) and item.get("url") and parse_google(item["url"]):
                    meta[parse_google(item["url"])] = item
        except (OSError, ValueError):
            pass
    docs = [n for n in needs_access if "drive file" not in n["reason"]]
    drive = [n for n in needs_access if "drive file" in n["reason"]]
    out = ["# Files the pipeline could not read", "",
           "Open each link while signed in to your school account, then File -> Download (.docx, .pdf or .txt) and save it into",
           f"`{config.DATA_DIR / 'manual_docs'}` . Name the file the way people would ask for it. Then run `python run_pipeline.py`.", ""]
    for title, group in (("Private Google Docs / Sheets / Slides", docs), ("Drive files (PDFs, images, other uploads)", drive)):
        out += [f"## {title} ({len(group)})", ""]
        for n in group:
            g = parse_google(n["url"])
            m = meta.get(g, {})
            label = n["anchor_text"] or m.get("text") or "(untitled)"
            where = ", ".join(m.get("found_on", [])[:1])
            out.append(f"- [ ] **{label[:80]}** - {n['url']}" + (f"  \n      linked from: {where}" if where else ""))
        out.append("")
    config.MANUAL_LIST_FILE.write_text("\n".join(out), encoding="utf-8")


def run(extra_files=(), log=print) -> dict:
    f = Fetcher(min_delay=1.5)
    done, needs_access = set(), []
    got = 0
    frontier = candidate_urls(extra_files)
    for depth in range(config.MAX_GOOGLE_DEPTH + 1):
        discovered = {}
        for url, anchor in frontier.items():
            g = parse_google(url)
            if not g or g in done:
                continue
            done.add(g)
            kind, fid = g
            title, text, links = fetch_one(f, kind, fid, url)
            if title is None:
                needs_access.append({"url": url, "anchor_text": anchor, "reason": text})
                continue
            if len(text.strip()) < 40:
                needs_access.append({"url": url, "anchor_text": anchor, "reason": "empty document"})
                continue
            canonical = f"https://docs.google.com/{ {'doc': 'document', 'sheet': 'spreadsheets', 'slides': 'presentation'}[kind] }/d/{fid}"
            title = pick_title(title, anchor, text, canonical)
            save_page({"url": canonical, "title": title, "source": f"google_{kind}", "text": text, "links": links,
                       "scraped_at": datetime.now(timezone.utc).isoformat()})
            got += 1
            for l in links:
                if parse_google(l["url"]):
                    discovered.setdefault(norm_url(l["url"]), l["text"])
        frontier = discovered
        if not frontier:
            break
    config.NEEDS_ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.NEEDS_ACCESS_FILE.write_text(json.dumps(needs_access, indent=1), encoding="utf-8")
    write_download_list(needs_access, extra_files)
    stats = {"fetched": got, "needs_access": len(needs_access)}
    log(f"google docs done: {stats}  (details: {config.NEEDS_ACCESS_FILE})")
    return stats
