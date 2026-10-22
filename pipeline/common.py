"""HTTP, text-extraction and page-storage helpers shared by every stage."""
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config

NOISE_SELECTORS = [
    "script", "style", "noscript", "svg", "iframe", "form", "nav", "header", "footer",
    "aside", "[role=navigation]", "[aria-hidden=true]", ".fsSkipLink", ".fsBreadcrumbs",
    ".fsSocialMedia", ".fs-global-aria-live-polite", ".fs-global-aria-live-assertive",
]
SKIP_EXT = re.compile(r"\.(jpe?g|png|gif|svg|webp|ico|mp4|mov|mp3|zip|css|js|woff2?|ttf)($|\?)", re.I)


class Fetcher:
    """requests session that rate-limits itself and retries politely."""

    def __init__(self, min_delay: float = config.MIN_DELAY_SECONDS):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = config.USER_AGENT
        self.min_delay = min_delay
        self._last = 0.0

    def get(self, url: str, retries: int = 3, **kw):
        for attempt in range(retries):
            wait = self.min_delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.s.get(url, timeout=40, allow_redirects=True, **kw)
                if r.status_code in (429, 503):
                    time.sleep(10 * (attempt + 1))
                    continue
                return r
            except requests.RequestException:
                time.sleep(3 * (attempt + 1))
        return None


def norm_url(url: str, base: str = "") -> str:
    url = urldefrag(urljoin(base, url.strip()))[0]
    p = urlparse(url)
    return p._replace(netloc=p.netloc.lower(), path=p.path.rstrip("/") or "/").geturl() if p.scheme in ("http", "https") else url


def slug(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def html_to_markdown(root) -> str:
    """Headings, lists, tables and paragraphs -> compact markdown-ish text."""
    out = []

    def walk(node):
        for el in node.children:
            name = getattr(el, "name", None)
            if name is None:
                continue
            if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                t = el.get_text(" ", strip=True)
                if t:
                    out.append(f"\n{'#' * int(name[1])} {t}\n")
            elif name == "table":
                for tr in el.find_all("tr"):
                    cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                    if any(cells):
                        out.append("| " + " | ".join(cells) + " |")
                out.append("")
            elif name in ("ul", "ol"):
                for li in el.find_all("li", recursive=False):
                    t = li.get_text(" ", strip=True)
                    if t:
                        out.append(f"- {t}")
                out.append("")
            elif name in ("p", "blockquote", "pre", "dd", "dt", "figcaption"):
                t = el.get_text(" ", strip=True)
                if t:
                    out.append(t + "\n")
            elif name in ("div", "section", "article", "main", "span", "li", "td", "tr"):
                has_block = el.find(["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "table", "div", "section"])
                if has_block:
                    walk(el)
                else:
                    t = el.get_text(" ", strip=True)
                    if t:
                        out.append(t + "\n")
            else:
                t = el.get_text(" ", strip=True)
                if t:
                    out.append(t + "\n")

    walk(root)
    text = "\n".join(out)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_links(root, base_url: str):
    """Every anchor with its text; context is the sentence/paragraph around it."""
    links = []
    for a in root.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        absolute = href if href.startswith(("mailto:", "tel:")) else norm_url(href, base_url)
        text = a.get_text(" ", strip=True) or a.get("aria-label", "") or a.get("title", "")
        parent = a.find_parent(["p", "li", "td", "div"])
        context = parent.get_text(" ", strip=True)[:300] if parent else ""
        links.append({"url": absolute, "text": text[:200], "context": context})
    return links


def extract_page(html: str, url: str):
    """Returns (title, markdown, links). Links are collected BEFORE noise removal
    so 'Book a tour' buttons living in headers or sidebars are not lost."""
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "") or url
    title = re.sub(r"\s*\|\s*The Nueva School$", "", title)
    links = extract_links(soup, url)
    for sel in NOISE_SELECTORS:
        for n in soup.select(sel):
            n.decompose()
    root = soup.select_one("main") or soup.body or soup
    return title, html_to_markdown(root), links


def save_page(record: dict) -> Path:
    config.PAGES_DIR.mkdir(parents=True, exist_ok=True)
    path = config.PAGES_DIR / f"{slug(record['url'])}.json"
    record["content_hash"] = hashlib.sha1(record["text"].encode()).hexdigest()
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_pages():
    for p in sorted(config.PAGES_DIR.glob("*.json")):
        yield json.loads(p.read_text(encoding="utf-8"))
