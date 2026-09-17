#!/usr/bin/env python3
"""Crawl authenticated MySchoolApp/Nexus pages into citation-ready JSON.

This tool deliberately has no credential fields. It opens a regular browser for
manual SSO/2FA login, stores the resulting Playwright storage state locally,
then reuses that state for later runs.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

try:
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:  # Gives a useful message in main(), but still allows py_compile.
    BeautifulSoup = None  # type: ignore[assignment,misc]
    NavigableString = Tag = Any  # type: ignore[assignment,misc]

try:
    from playwright.async_api import Browser, BrowserContext, Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError, async_playwright
except ImportError:
    Browser = BrowserContext = Page = Any  # type: ignore[assignment,misc]
    PlaywrightError = PlaywrightTimeoutError = Exception  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]


LOGGER = logging.getLogger("nexus_scraper")
SCHEMA_VERSION = 1
SKIP_PATH_MARKERS = ("logout", "logoff", "signout", "sign-out", "signin", "sign-in", "login", "download", "print", "evals", "eval/", "grade", "gradebook", "transcript", "progressreport", "sso/auth", "guidedtour", "myday", "navassignmentseap", "navcontactcard", "navofficialnotes", "navprogress", "navschedule")
LOGIN_MARKERS = ("login", "signin", "sign-in", "sso", "oauth", "auth")
SKIP_SUFFIXES = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".csv", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".mp3", ".mp4"}
REMOVE_SELECTORS = (
    "script, style, noscript, svg, canvas, iframe, form, nav, footer, header, aside, "
    "[role='navigation'], [role='banner'], [role='complementary'], "
    ".navbar, .navigation, .nav, .sidebar, .footer, .header, .breadcrumb, "
    ".cookie, .modal, .widget, .advertisement, .ad, .social-share, .skip-link"
)
MAIN_SELECTORS = (
    "main, [role='main'], #main-content, #mainContent, #content, .main-content, "
    ".mainContent, .page-content, .pageContent, .content-area, .contentArea, "
    "article, .article-content, .articleContent"
)
BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "pre", "blockquote"}


class SessionExpired(RuntimeError):
    """The portal redirected to an authentication screen while crawling."""


class ParseFailure(RuntimeError):
    """The page did not contain enough readable content to produce a record."""


class TransientFetchError(RuntimeError):
    """A retryable fetch issue such as a timeout or a server-side error."""


@dataclass
class RunSummary:
    pages_visited: int = 0
    pages_written: int = 0
    pages_skipped: Counter[str] = field(default_factory=Counter)
    errors: list[dict[str, str]] = field(default_factory=list)

    def add_error(self, url: str, error: Exception | str) -> None:
        message = str(error).replace("\n", " ")[:500]
        self.errors.append({"url": url, "error": message})
        LOGGER.warning("%s: %s", url, message)


@dataclass
class CrawlConfig:
    output_dir: Path
    auth_state: Path
    max_depth: int
    max_pages: int
    delay: float
    timeout_ms: int
    retries: int
    render_wait_ms: int
    chunk_size: int
    chunk_overlap: int
    allowed_prefixes: tuple[str, ...]
    refresh_urls: set[str]
    refresh_all: bool
    auth_host: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def approx_tokens(value: str) -> int:
    """A dependency-free token estimate suitable for sizing retrieval chunks."""
    return len(re.findall(r"\w+(?:['’-]\w+)?|[^\w\s]", value, re.UNICODE))


def normalize_url(url: str) -> str:
    """Normalize crawl URLs without changing meaningful portal query parameters."""
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    fragment = parsed.fragment.strip()
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urlencode(query, doseq=True), fragment))


def read_seed_urls(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Seed URL file not found: {path}")
    seeds = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        normalized = normalize_url(stripped)
        if not normalized:
            raise ValueError(f"Seed URL is not http(s): {stripped}")
        seeds.append(normalized)
    if not seeds:
        raise ValueError("The seed URL file is empty.")
    return list(dict.fromkeys(seeds))


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "updated_at": None, "pages": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Manifest is invalid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("pages", {}), dict):
        raise ValueError(f"Manifest has an unexpected shape: {path}")
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("pages", {})
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["schema_version"] = SCHEMA_VERSION
    manifest["updated_at"] = utc_now()
    write_json(path, manifest)


def url_slug(url: str) -> str:
    parsed = urlsplit(url)
    source = f"{parsed.netloc}{parsed.path}".strip("/") or "page"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", source).strip("-").lower()[:64] or "page"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}.json"


def classify_page(url: str, title: str, text: str) -> str:
    haystack = f"{url} {title} {text[:2500]}".lower()
    rules = (
        ("course", ("course", "class page", "syllabus", "curriculum")),
        ("faculty", ("faculty", "staff directory", "directory", "teacher profile", "employee")),
        ("handbook", ("handbook", "policy", "policies", "student life")),
        ("calendar", ("calendar", "event", "athletics schedule", "schedule")),
        ("news", ("news", "announcement", "bulletin", "headline")),
        ("faq", ("faq", "frequently asked", "help center", "knowledge base")),
    )
    for category, terms in rules:
        if any(term in haystack for term in terms):
            return category
    return "nexus"


def is_non_content_url(url: str, allowed_hosts: set[str], prefixes: tuple[str, ...]) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in allowed_hosts:
        return True
    path = parsed.path.lower()
    if prefixes and not any(parsed.path.startswith(prefix) for prefix in prefixes):
        return True
    marker_target = f"{path} {parsed.fragment.lower()}"
    if any(marker in marker_target for marker in SKIP_PATH_MARKERS):
        return True
    if Path(path).suffix.lower() in SKIP_SUFFIXES:
        return True
    return False


def is_google_host(host: str) -> bool:
    host = host.lower()
    return host in {"google.com", "goo.gl"} or host.endswith(".google.com")


def classify_google_link(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    if "/spreadsheets/" in path:
        return "sheet"
    if "/presentation/" in path:
        return "slides"
    if "/forms/" in path:
        return "form"
    if "/document/" in path:
        return "doc"
    if host.startswith("drive."):
        return "drive-file"
    if host.startswith("calendar."):
        return "calendar"
    return "other-google"


def build_google_export_hint(url: str, kind: str) -> str | None:
    path = urlsplit(url).path
    published = re.search(r"/d/e/([a-zA-Z0-9_-]+)", path)
    if published:
        pub_id = published.group(1)
        if kind == "doc":
            return f"https://docs.google.com/document/d/e/{pub_id}/pub?output=txt"
        if kind == "sheet":
            return f"https://docs.google.com/spreadsheets/d/e/{pub_id}/pub?output=csv"
        return None
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", path)
    if not match:
        return None
    doc_id = match.group(1)
    if kind == "doc":
        return f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
    if kind == "sheet":
        return f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=csv"
    if kind == "slides":
        return f"https://docs.google.com/presentation/d/{doc_id}/export/pptx"
    return None


def collect_links(soup: BeautifulSoup, base_url: str, allowed_hosts: set[str], prefixes: tuple[str, ...]) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    internal: set[str] = set()
    external: dict[str, str] = {}
    google: dict[str, dict[str, str]] = {}
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        if not href or href in ("#", "#!") or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        normalized = normalize_url(urljoin(base_url, href))
        if not normalized:
            continue
        host = urlsplit(normalized).netloc.lower()
        if host not in allowed_hosts:
            text = clean_text(anchor.get_text(" ", strip=True)) or normalized
            if is_google_host(host):
                if normalized not in google:
                    kind = classify_google_link(normalized)
                    entry = {"url": normalized, "text": text, "kind": kind}
                    hint = build_google_export_hint(normalized, kind)
                    if hint:
                        entry["suggested_export_url"] = hint
                    google[normalized] = entry
            else:
                external.setdefault(normalized, text)
            continue
        if not is_non_content_url(normalized, allowed_hosts, prefixes):
            internal.add(normalized)
    external_list = [{"url": url, "text": text} for url, text in external.items()]
    google_list = list(google.values())
    return sorted(internal), external_list, google_list


def remove_noise(soup: BeautifulSoup) -> None:
    for node in soup.select(REMOVE_SELECTORS):
        node.decompose()
    for node in soup.select("[aria-hidden='true']"):
        node.decompose()
    for node in soup.find_all(string=lambda value: isinstance(value, str) and not value.strip()):
        node.extract()


def score_content_node(node: Tag) -> int:
    text_size = len(clean_text(node.get_text(" ", strip=True)))
    semantic_bonus = len(node.find_all(["h1", "h2", "h3", "p", "li", "table"])) * 80
    return text_size + semantic_bonus


def choose_content_root(soup: BeautifulSoup) -> Tag:
    candidates: list[Tag] = []
    for selector in MAIN_SELECTORS.split(","):
        candidates.extend(soup.select(selector.strip()))
    if not candidates:
        body = soup.body or soup
        candidates = [body]
    return max(candidates, key=score_content_node)


def table_to_markdown(table: Tag) -> str:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [clean_text(cell.get_text(" ", strip=True)).replace("|", "\\|") for cell in tr.find_all(["th", "td"], recursive=False)]
        if cells and any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    divider = ["---"] * width
    return "\n".join("| " + " | ".join(row) + " |" for row in (header, divider, *rows[1:]))


def has_block_parent(node: Tag, root: Tag) -> bool:
    parent = node.parent
    while isinstance(parent, Tag) and parent is not root:
        if parent.name in BLOCK_TAGS:
            return True
        parent = parent.parent
    return False


NOISE_BLOCK_MARKERS = (
    "switch persona",
    "impersonating",
    "my day back",
    "view other classes",
    "find more",
    "directories directories back",
    "nueva gmail",
    "profile files & forms settings",
    "getting started sign out",
    "account back profile",
    "assignment center",
    "course requests checklist",
    "faculty & staff board of trustees",
    "board of trustees my contacts",
)

NOISE_EXACT_LABELS = {
    "resources",
    "calendar",
    "student",
    "parents",
    "students",
    "my contacts",
    "faculty & staff",
    "board of trustees",
}


def is_noise_block(text: str, block_type: str = "") -> bool:
    lowered = text.lower().strip()
    if any(marker in lowered for marker in NOISE_BLOCK_MARKERS):
        return True
    if block_type == "list_item" and lowered in NOISE_EXACT_LABELS:
        return True
    return False


def extract_blocks(root: Tag) -> list[dict[str, Any]]:
    """Extract headings, paragraphs, list items, and tables without double-counting."""
    blocks: list[dict[str, Any]] = []
    for element in root.find_all(list(BLOCK_TAGS)):
        if has_block_parent(element, root):
            continue
        name = element.name.lower()
        if name == "table":
            text = table_to_markdown(element)
            kind = "table"
        else:
            text = clean_text(element.get_text(" ", strip=True))
            kind = "heading" if name.startswith("h") else "list_item" if name == "li" else name
        if not text or len(text) < 2:
            continue
        block: dict[str, Any] = {"type": kind, "text": text}
        if kind == "heading":
            block["level"] = int(name[1])
        blocks.append(block)
    if not blocks:
        fallback = clean_text(root.get_text(" ", strip=True))
        if fallback:
            blocks.append({"type": "paragraph", "text": fallback})
    return blocks


def markdown_from_blocks(blocks: Iterable[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in blocks:
        text = block["text"]
        if block["type"] == "heading":
            parts.append("#" * block.get("level", 2) + " " + text)
        elif block["type"] == "list_item":
            parts.append("- " + text)
        elif block["type"] == "blockquote":
            parts.append("> " + text)
        else:
            parts.append(text)
    return "\n\n".join(parts).strip()


def labelled_value(text: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        match = re.search(rf"\b{re.escape(label)}\s*[:\-]\s*([^\n|•]{{2,180}})", text, flags=re.IGNORECASE)
        if match:
            return clean_text(match.group(1))
    return None


def extract_faculty_entities(soup: BeautifulSoup, root: Tag, category: str) -> list[dict[str, Any]]:
    """Best-effort generic directory extraction; portal markup differs by school."""
    selector = ".faculty-card, .staff-card, .directory-item, .employee, .profile, [data-role*='faculty'], [data-role*='staff']"
    candidates = root.select(selector)
    if not candidates and category == "faculty":
        candidates = [root]
    people: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for candidate in candidates[:500]:
        text = clean_text(candidate.get_text(" ", strip=True))
        if len(text) < 8:
            continue
        heading = candidate.find(["h1", "h2", "h3", "h4", ".name", ".title"])
        name = clean_text(heading.get_text(" ", strip=True)) if heading else None
        if not name or len(name) > 100:
            continue
        email_link = candidate.select_one("a[href^='mailto:']")
        email = email_link.get("href", "").split(":", 1)[-1].split("?", 1)[0] if email_link else None
        key = (name.lower(), email.lower() if email else None)
        if key in seen:
            continue
        seen.add(key)
        entity = {
            "name": name,
            "department": labelled_value(text, ("Department", "Division", "Office")),
            "email": email,
            "courses_taught": labelled_value(text, ("Courses", "Classes", "Teaches")),
            "bio": labelled_value(text, ("Bio", "Biography", "About")),
        }
        entity = {key: value for key, value in entity.items() if value}
        if len(entity) >= 2:
            people.append(entity)
    return people


def split_long_text(text: str, max_tokens: int) -> list[str]:
    if approx_tokens(text) <= max_tokens:
        return [text]
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if len(sentences) <= 1:
        words = text.split()
        return [" ".join(words[index:index + max_tokens]) for index in range(0, len(words), max_tokens)]
    pieces: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        if current and approx_tokens(" ".join(current + [sentence])) > max_tokens:
            pieces.append(" ".join(current))
            current = [sentence]
        else:
            current.append(sentence)
    if current:
        pieces.append(" ".join(current))
    return pieces


def split_table_markdown(text: str, max_tokens: int) -> list[str]:
    """Split a large Markdown table by rows while repeating its header."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 3 or approx_tokens(text) <= max_tokens:
        return [text]
    header, divider, *rows = lines
    pieces: list[str] = []
    current = [header, divider]
    for row in rows:
        if len(current) > 2 and approx_tokens("\n".join(current + [row])) > max_tokens:
            pieces.append("\n".join(current))
            current = [header, divider, row]
        else:
            current.append(row)
    if len(current) > 2:
        pieces.append("\n".join(current))
    return pieces or [text]


def overlap_tail(parts: list[str], target_tokens: int) -> list[str]:
    selected: list[str] = []
    total = 0
    for part in reversed(parts):
        if part.startswith("#"):
            continue
        count = approx_tokens(part)
        if selected and total + count > target_tokens:
            break
        selected.insert(0, part)
        total += count
        if total >= target_tokens:
            break
    return selected


def build_chunks(blocks: list[dict[str, Any]], parent: dict[str, Any], chunk_size: int, overlap: int) -> list[dict[str, Any]]:
    """Chunk at semantic block boundaries and keep heading context + parent metadata."""
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    current_heading = ""
    page_digest = hashlib.sha256(parent["url"].encode("utf-8")).hexdigest()[:10]

    def content_count(items: list[str]) -> int:
        return approx_tokens("\n\n".join(items))

    def flush() -> None:
        nonlocal current
        text = "\n\n".join(current).strip()
        content_without_heading = re.sub(r"^#{1,6}\s+[^\n]+\n*", "", text).strip()
        if not content_without_heading or approx_tokens(text) < 8:
            return
        index = len(chunks) + 1
        chunks.append({
            "chunk_id": f"{page_digest}-{index:04d}",
            "text": text,
            "heading": current_heading or None,
            "approx_tokens": approx_tokens(text),
            "source_url": parent["url"],
            "page_title": parent["title"],
            "category": parent["category"],
            "scraped_at": parent["scraped_at"],
        })

    for block in blocks:
        block_type = block["type"]
        if block_type == "heading":
            if current and content_count(current) > 20:
                flush()
            # A new heading is a stronger semantic boundary than an arbitrary
            # overlap. Never attach text from the prior section to it.
            current = []
            current_heading = block["text"]
            heading_markdown = "#" * block.get("level", 2) + " " + current_heading
            current.append(heading_markdown)
            continue

        prefix = "- " if block_type == "list_item" else "> " if block_type == "blockquote" else ""
        segments = (
            split_table_markdown(block["text"], max(80, chunk_size - 20))
            if block_type == "table"
            else split_long_text(prefix + block["text"], max(80, chunk_size - 20))
        )
        for segment in segments:
            if current and content_count(current + [segment]) > chunk_size and content_count(current) > 20:
                previous = current[:]
                flush()
                current = []
                if current_heading:
                    current.append("## " + current_heading)
                current.extend(overlap_tail(previous, overlap))
            current.append(segment)
    flush()
    return chunks


async def looks_like_login(page: Page) -> bool:
    url = page.url.lower()
    if any(re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", url) for marker in LOGIN_MARKERS):
        return True
    try:
        password = await page.locator("input[type='password']").count()
        if password:
            return True
        text = (await page.locator("body").inner_text(timeout=4_000)).lower()
        return any(phrase in text for phrase in ("sign in", "log in", "blackbaud id", "single sign-on")) and "password" in text
    except PlaywrightError:
        return False


async def manual_login(context: BrowserContext, login_url: str, auth_state: Path) -> None:
    page = await context.new_page()
    print("\nA browser window is open for manual sign-in. Complete your normal SSO/2FA flow.")
    print("When you are back inside Nexus, return here and press Enter. No credentials are collected by this script.")
    await page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.to_thread(input, "Press Enter after you are signed in: ")
    # A few SSO providers open a second tab. Accept any authenticated tab in
    # this context, but never mistake a remaining login tab for success.
    authenticated_pages = [candidate for candidate in reversed(context.pages) if not await looks_like_login(candidate)]
    if not authenticated_pages:
        await page.close()
        raise SessionExpired("The current page still looks like a login screen. Please run again and finish sign-in first.")
    auth_state.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(auth_state))
    try:
        auth_state.chmod(0o600)
    except OSError:
        pass
    await page.close()
    print(f"Saved authenticated Playwright state to {auth_state}. Keep this file private.\n")


async def rendered_page(page: Page, url: str, config: CrawlConfig) -> str:
    response = await page.goto(url, wait_until="domcontentloaded", timeout=config.timeout_ms)
    if response and response.status >= 500:
        raise TransientFetchError(f"Server returned HTTP {response.status}")
    if response and response.status >= 400:
        raise ParseFailure(f"Server returned HTTP {response.status}")
    await page.wait_for_timeout(config.render_wait_ms)
    if urlsplit(page.url).netloc.lower() == config.auth_host and await looks_like_login(page):
        raise SessionExpired(f"Authentication expired while opening {url}")
    return await page.content()


async def fetch_html(context: BrowserContext, url: str, config: CrawlConfig) -> str:
    for attempt in range(1, config.retries + 1):
        page = await context.new_page()
        try:
            return await rendered_page(page, url, config)
        except SessionExpired:
            raise
        except (PlaywrightTimeoutError, TransientFetchError) as exc:
            if attempt == config.retries:
                raise TransientFetchError(f"Failed after {config.retries} attempts: {exc}") from exc
            delay = min(12.0, 1.5 * (2 ** (attempt - 1)))
            LOGGER.info("Retrying %s in %.1fs (%s)", url, delay, exc)
            await asyncio.sleep(delay)
        except PlaywrightError as exc:
            raise ParseFailure(f"Browser error: {exc}") from exc
        finally:
            await page.close()
    raise AssertionError("unreachable")


def parse_document(
    html: str,
    source_url: str,
    allowed_hosts: set[str],
    allowed_prefixes: tuple[str, ...],
) -> tuple[dict[str, Any], list[str]]:
    if BeautifulSoup is None:
        raise RuntimeError("BeautifulSoup is unavailable. Run: pip install -r requirements.txt")
    soup = BeautifulSoup(html, "lxml")
    page_title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else "Untitled Nexus page"
    links, external_links, google_links = collect_links(soup, source_url, allowed_hosts, allowed_prefixes)
    remove_noise(soup)
    root = choose_content_root(soup)
    blocks = extract_blocks(root)
    blocks = [block for block in blocks if not is_noise_block(block["text"], block.get("type", ""))]
    raw_text = markdown_from_blocks(blocks)
    if len(raw_text) < 40:
        raise ParseFailure("No readable main content found after removing navigation and page chrome")
    category = classify_page(source_url, page_title, raw_text)
    faculty = extract_faculty_entities(soup, root, category)
    document = {
        "url": source_url,
        "title": page_title,
        "category": category,
        "scraped_at": utc_now(),
        "raw_text": raw_text,
        "content_blocks": blocks,
        "entities": {"faculty": faculty} if faculty else {},
        "external_links": external_links,
        "google_links": google_links,
    }
    return document, links


async def crawl(context: BrowserContext, seeds: list[str], config: CrawlConfig, manifest: dict[str, Any]) -> RunSummary:
    summary = RunSummary()
    allowed_hosts = {urlsplit(seed).netloc.lower() for seed in seeds}
    prefixes = config.allowed_prefixes or ("/",)
    queue: deque[tuple[str, int]] = deque()
    queued: set[str] = set()
    for seed in seeds:
        if seed not in queued:
            queue.append((seed, 0))
            queued.add(seed)
    queue_path = config.output_dir / "queue.json"
    if queue_path.exists():
        try:
            pending = json.loads(queue_path.read_text(encoding="utf-8"))
            for item in pending:
                pending_url, pending_depth = item["url"], item["depth"]
                if pending_url not in queued:
                    queue.append((pending_url, pending_depth))
                    queued.add(pending_url)
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            LOGGER.warning("Could not read existing queue.json; starting from seeds only")
    seen_this_run: set[str] = set()
    manifest_path = config.output_dir / "manifest.json"
    external_links_path = config.output_dir / "external_links.json"
    google_links_path = config.output_dir / "google_links.json"
    external_links_all: dict[str, list[dict[str, str]]] = {}
    google_links_all: dict[str, list[dict[str, str]]] = {}
    if external_links_path.exists():
        try:
            external_links_all = json.loads(external_links_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            external_links_all = {}
    if google_links_path.exists():
        try:
            google_links_all = json.loads(google_links_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            google_links_all = {}

    try:
        while queue and summary.pages_visited < config.max_pages:
            url, depth = queue.popleft()
            if url in seen_this_run:
                continue
            seen_this_run.add(url)
            if is_non_content_url(url, allowed_hosts, prefixes):
                summary.pages_skipped["out_of_scope"] += 1
                continue
            existing = manifest["pages"].get(url, {})
            should_refresh = config.refresh_all or url in config.refresh_urls
            if existing.get("status") == "ok" and not should_refresh:
                summary.pages_skipped["already_scraped"] += 1
                continue

            summary.pages_visited += 1
            LOGGER.info("[%s/%s] depth=%s %s", summary.pages_visited, config.max_pages, depth, url)
            try:
                html = await fetch_html(context, url, config)
                document, extracted_links = parse_document(html, url, allowed_hosts, prefixes)
                document["chunks"] = build_chunks(document["content_blocks"], document, config.chunk_size, config.chunk_overlap)
                if not document["chunks"]:
                    raise ParseFailure("No RAG chunks were created")
                filename = url_slug(url)
                output_path = config.output_dir / "pages" / filename
                write_json(output_path, document)
                manifest["pages"][url] = {
                    "url": url,
                    "title": document["title"],
                    "category": document["category"],
                    "scraped_at": document["scraped_at"],
                    "file": str(output_path.relative_to(config.output_dir)),
                    "chunk_count": len(document["chunks"]),
                    "content_sha256": hashlib.sha256(document["raw_text"].encode("utf-8")).hexdigest(),
                    "status": "ok",
                }
                save_manifest(manifest_path, manifest)
                summary.pages_written += 1
                if document["external_links"]:
                    external_links_all[url] = document["external_links"]
                    external_links_path.write_text(json.dumps(external_links_all, indent=2), encoding="utf-8")
                if document["google_links"]:
                    google_links_all[url] = document["google_links"]
                    google_links_path.write_text(json.dumps(google_links_all, indent=2), encoding="utf-8")

                if depth < config.max_depth:
                    for link in extracted_links:
                        if link not in seen_this_run and link not in queued:
                            queue.append((link, depth + 1))
                            queued.add(link)
            except SessionExpired:
                # Put this URL back at the front so the next run retries it first, then stop.
                queue.appendleft((url, depth))
                raise
            except Exception as exc:  # Keep the remaining crawl useful if one page is malformed.
                summary.add_error(url, exc)
                manifest["pages"][url] = {"url": url, "scraped_at": utc_now(), "status": "error", "error": str(exc)[:500]}
                save_manifest(manifest_path, manifest)
            if queue and summary.pages_visited < config.max_pages:
                await asyncio.sleep(config.delay)

        if queue and summary.pages_visited >= config.max_pages:
            summary.pages_skipped["max_pages_cap"] += len(queue)
    finally:
        remaining = [{"url": u, "depth": d} for u, d in queue]
        if remaining:
            queue_path.write_text(json.dumps(remaining, indent=2), encoding="utf-8")
        elif queue_path.exists():
            queue_path.unlink()
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl authenticated Nexus/MySchoolApp pages into clean, chunked JSON for RAG.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--seed-urls", type=Path, required=True, help="Text file with one authenticated Nexus URL per line")
    parser.add_argument("--output", type=Path, required=True, help="Directory for pages/ JSON and manifest.json")
    parser.add_argument("--max-depth", type=int, default=2, help="Internal link depth from each seed")
    parser.add_argument("--max-pages", type=int, default=100, help="Maximum pages to request in this run")
    parser.add_argument("--delay", type=float, default=1.25, help="Seconds to wait between page requests")
    parser.add_argument("--timeout", type=int, default=30, help="Per-page navigation timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="Retries for timeouts and 5xx responses")
    parser.add_argument("--render-wait", type=float, default=0.9, help="Extra seconds to wait for JavaScript-rendered content")
    parser.add_argument("--chunk-size", type=int, default=420, help="Approximate maximum tokens per retrieval chunk")
    parser.add_argument("--chunk-overlap", type=int, default=60, help="Approximate natural-boundary overlap tokens")
    parser.add_argument("--auth-state", type=Path, help="Path to Playwright storage_state JSON; defaults inside --output")
    parser.add_argument("--login", action="store_true", help="Force a headed manual sign-in and overwrite the saved session")
    parser.add_argument("--login-url", help="Portal URL to open for manual sign-in; defaults to the first seed")
    parser.add_argument("--headed", action="store_true", help="Keep later crawl runs visible for debugging")
    parser.add_argument("--allow-prefix", action="append", default=[], help="Allowed path prefix; repeat to allow multiple portal areas")
    parser.add_argument("--refresh", action="append", default=[], metavar="URL", help="Re-scrape this URL even if it is in manifest; repeatable")
    parser.add_argument("--refresh-all", action="store_true", help="Re-scrape every discovered page in the current crawl")
    parser.add_argument("--verbose", action="store_true", help="Show browser and retry diagnostics")
    args = parser.parse_args()
    if args.max_depth < 0 or args.max_pages < 1:
        parser.error("--max-depth must be 0 or greater and --max-pages must be at least 1")
    if args.delay < 0 or args.chunk_overlap < 0 or args.chunk_size < 100:
        parser.error("Use non-negative delay/overlap and a --chunk-size of at least 100")
    return args


async def async_main(args: argparse.Namespace) -> int:
    if async_playwright is None or BeautifulSoup is None:
        print("Missing dependencies. Install them with: pip install -r requirements.txt", file=sys.stderr)
        return 2
    seeds = read_seed_urls(args.seed_urls)
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    auth_state = (args.auth_state or output_dir / "auth.json").resolve()
    refresh_urls = {normalize_url(url) for url in args.refresh}
    if "" in refresh_urls:
        raise ValueError("Each --refresh value must be an http(s) URL")
    prefixes = tuple(prefix if prefix.startswith("/") else "/" + prefix for prefix in args.allow_prefix)
    login_url = normalize_url(args.login_url) if args.login_url else seeds[0]
    if not login_url:
        raise ValueError("--login-url must be an http(s) URL")
    auth_host = urlsplit(login_url).netloc.lower()
    config = CrawlConfig(
        output_dir=output_dir,
        auth_state=auth_state,
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        delay=args.delay,
        timeout_ms=args.timeout * 1_000,
        retries=args.retries,
        render_wait_ms=round(args.render_wait * 1_000),
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        allowed_prefixes=prefixes,
        refresh_urls=refresh_urls,
        refresh_all=args.refresh_all,
        auth_host=auth_host,
    )
    manifest = load_manifest(output_dir / "manifest.json")
    needs_login = args.login or not auth_state.exists()

    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(headless=not (needs_login or args.headed))
        context: BrowserContext | None = None
        try:
            context = await browser.new_context(storage_state=None if needs_login else str(auth_state))
            if needs_login:
                await manual_login(context, login_url, auth_state)
            summary = await crawl(context, seeds, config, manifest)
        except SessionExpired as exc:
            print("\nSession expired or sign-in was not completed. Stopping before any login page can be saved.", file=sys.stderr)
            print(f"Reason: {exc}", file=sys.stderr)
            print(f"Re-authenticate with:\n  python scrape_nexus.py --login --seed-urls {args.seed_urls} --output {args.output}", file=sys.stderr)
            return 3
        finally:
            if context:
                await context.close()
            await browser.close()

    print("\nNexus scrape complete")
    print(f"  Pages visited: {summary.pages_visited}")
    print(f"  Pages written: {summary.pages_written}")
    if summary.pages_skipped:
        print("  Pages skipped: " + ", ".join(f"{reason}={count}" for reason, count in sorted(summary.pages_skipped.items())))
    print(f"  Errors: {len(summary.errors)}")
    if summary.errors:
        print(f"  Error details: {output_dir / 'manifest.json'}")
    return 0


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nStopped by user.", file=sys.stderr)
        return 130
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
