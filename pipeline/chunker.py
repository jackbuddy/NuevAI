"""Stage 5: structure-aware chunking.

The old pipeline cut text into 300-500 character windows, which splits course
descriptions and policies mid-thought. Here:
  * text is split on markdown headings first; each chunk carries its heading
    trail ("Course Catalog > Computer Science > Software Engineering")
  * sections are packed up to CHUNK_TARGET_CHARS on paragraph/line boundaries
  * table rows and list items are never cut in half
  * identical chunk bodies (site-wide boilerplate) are stored once
Chunk ids are derived from the source URL + position, content_hash lets the
indexer skip chunks whose text did not change.
"""
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config
from .common import load_pages
from .links import build as build_links, link_cards
from . import facts, sheets, workbooks

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))     # freshness.py lives at the repo root (shared with rag.py)
import freshness      # noqa: E402

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


RECORD_FIELD = re.compile(r"^(Term|Open to Grades?|Grades?|Prerequisites?|Credits?|Length|Duration)\s*:", re.I)


def promote_record_titles(text: str) -> str:
    """Catalog entries are 'Course Name' followed by 'Term : ...' / 'Open to Grades : ...'.
    Turn each course name into a heading so every course becomes its own section
    and every chunk of it carries the course name."""
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        nxt = next((l for l in lines[i + 1:i + 4] if l.strip()), "")
        if line.strip() and len(line) <= 100 and not line.startswith(("#", "|", "-")) \
                and not RECORD_FIELD.match(line.strip()) and RECORD_FIELD.match(nxt.strip()):
            out.append(f"#### {line.strip()}")
        else:
            out.append(line)
    return "\n".join(out)


def sections(text: str, title: str):
    """Yield (heading_trail, body) pairs."""
    trail, buf = [], []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            yield " > ".join([title] + [h for _, h in trail] if title else [h for _, h in trail]), body

    for line in text.splitlines():
        m = HEADING.match(line)
        if m:
            yield from flush()
            buf = []
            level = len(m.group(1))
            trail[:] = [(lv, h) for lv, h in trail if lv < level]
            trail.append((level, m.group(2).strip()))
        else:
            buf.append(line)
    yield from flush()


def pack(body: str):
    """Split one section body to <= CHUNK_MAX_CHARS on line boundaries."""
    lines = [l for l in body.split("\n")]
    out, cur = [], ""
    for line in lines:
        if len(line) > config.CHUNK_MAX_CHARS:   # one giant paragraph: split on sentences
            parts = re.split(r"(?<=[.!?])\s+", line)
        else:
            parts = [line]
        for part in parts:
            if cur and len(cur) + len(part) + 1 > config.CHUNK_TARGET_CHARS:
                out.append(cur.strip())
                # carry over the last whole sentence(s) as overlap, never a word fragment
                tail = cur[-config.CHUNK_OVERLAP_CHARS:]
                cut = max(tail.rfind(". "), tail.rfind("? "), tail.rfind("! "), tail.rfind("\n"))
                cur = (tail[cut + 1:].strip() + "\n" if cut >= 0 and tail[cut + 1:].strip() else "") + part
            else:
                cur = f"{cur}\n{part}" if cur else part
    if cur.strip():
        out.append(cur.strip())
    return out


def outline_chunks(page: dict):
    """For every heading with >=3 child headings, emit a 'contents' chunk listing the
    children. Answers 'list all engineering courses' / 'what departments are there'
    from one chunk instead of hoping vector search surfaces every course."""
    text = promote_record_titles(clean_text(page["text"]))
    nodes, stack = [], []   # (level, title, path)
    for line in text.splitlines():
        m = HEADING.match(line)
        if not m:
            continue
        level, title = len(m.group(1)), m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        nodes.append((level, title, [t for _, t in stack]))
    children = {}
    for level, title, path in nodes:
        if len(path) > 1:
            children.setdefault(tuple(path[:-1]), []).append(title)
    for parent, kids in children.items():
        kids = list(dict.fromkeys(kids))
        if len(kids) < 3 or len(parent) > 4:
            continue
        # split very long lists so each chunk stays embeddable
        for i in range(0, len(kids), 40):
            part = kids[i:i + 40]
            trail = " > ".join([page["title"]] + [p for p in parent if p != page["title"]])
            body = f"Contents of {trail} ({len(kids)} entries):\n" + "\n".join(f"- {k}" for k in part)
            yield trail, body


def clean_text(text: str) -> str:
    """Drop the BOM, separator rules and UI-chrome lines ("- Message", "- Add to Contacts") that
    carry no facts but dilute embeddings."""
    text = text.replace("\ufeff", "")
    return "\n".join(l for l in text.splitlines() if not config.BOILERPLATE_LINE.match(l.strip()))


def page_title(page: dict) -> str:
    """Google Docs imported through Nexus have their URL as the title. Use the document's first line
    instead so every chunk carries a readable title that lexical and vector search can match."""
    title = (page.get("title") or "").replace("\ufeff", "").strip()
    if not title.startswith("http"):
        return title
    for line in clean_text(page.get("text", "")).splitlines():
        line = re.sub(r"\s+", " ", line.replace("#", "")).strip()
        if len(line) < 4:
            continue
        line = re.split(r"_{3,}|\s{2,}| - |\. ", line)[0].strip()
        if len(line) > 80:
            line = line[:80].rsplit(" ", 1)[0]
        return line or title
    return title


SOURCE_RANK = {"google_doc": 0, "manual": 1, "public_site": 2, "google_slides": 2, "nexus": 3}


def page_rank(page: dict):
    """Keep the best copy when the same document was imported twice (Google Doc vs Nexus copy, #anchors):
    pages with a real title and a primary source come first, so their chunks win the near-duplicate check."""
    t = (page.get("title") or "").lstrip("\ufeff")
    return (t.startswith("http"), SOURCE_RANK.get(page.get("source"), 4), page.get("url", ""))


def near_duplicate(content: str, url: str, seen: dict) -> bool:
    """True when a chunk of ANOTHER url has the same beginning AND ending text (normalised) and a similar
    length: the same document imported under two URLs. Exact-hash dedupe misses these because the Title/
    Source header and chunk boundaries differ."""
    body = re.sub(r"[^a-z0-9]+", " ", content.split("\n\n", 1)[-1].lower()).strip()
    if len(body) < 300:
        return False
    head, tail = hashlib.sha1(body[:200].encode()).hexdigest(), hashlib.sha1(body[-200:].encode()).hexdigest()
    hit = seen.get((head, tail))
    if hit and hit[0] != url and 0.8 < len(body) / hit[1] < 1.25:
        return True
    seen[(head, tail)] = (url, len(body))
    return False


def chunk_page(page: dict):
    title = page["title"] = page_title(page)
    n = 0
    for trail, body in sections(promote_record_titles(clean_text(page["text"])), title):
        for piece in pack(body):
            if len(piece) < config.MIN_CHUNK_CHARS:
                continue
            header = f"Title: {title}\nSection: {trail}\nSource: {page['url']}\n\n" if trail != title else \
                     f"Title: {title}\nSource: {page['url']}\n\n"
            yield n, header + piece
            n += 1


def _day_of_file(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()
    except OSError:
        return None


def annotate_freshness(chunks: list[dict], pages: list[dict], today: date | None = None) -> dict:
    """Add indexed_at / source_date / school_year / stale to every chunk's metadata (not part of content_hash, so
    unchanged text is never re-embedded; index.py patches the metadata of rows that already exist).

      source_date  when the source was last captured or edited: a page's scrape date, a workbook's file date,
                   the day the calendar feed was read, a knowledge file's date
      school_year  the school year the text was written for, from its own wording ("2025-26 Course Catalog")
      stale        true when that is an earlier year than the current one (document level: every chunk of a page
                   shares the page's verdict, so a catalog whose later chunks never repeat the year is still caught)
    """
    today = today or date.today()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    by_url = {}
    for p in pages:
        text = (p.get("title") or "") + "\n" + (p.get("text") or "")
        by_url[p["url"]] = (freshness.parse_day(p.get("scraped_at")),
                            freshness.classify(text, p.get("source", ""), today, whole_page=freshness.is_public_site(p["url"])))
    wb_days = {p.stem: _day_of_file(p) for paths in workbooks.find_workbooks().values() for p in paths}
    cal_day = _day_of_file(config.CALENDAR_FILE) or today.isoformat()
    stats = {"annotated": 0, "stale": 0}
    for c in chunks:
        m = c["metadata"]
        url, src = m.get("source_url", ""), m.get("source", "")
        if url in by_url:
            day, cls = by_url[url]
        else:
            cls = freshness.classify(c["content"], src, today)
            if url.startswith("workbook://"):
                day = wb_days.get(url[len("workbook://"):])
            elif url.startswith("facts://"):
                day = _day_of_file(facts.DIR / url[len("facts://"):])
            elif src in ("calendar", "calendar_day"):
                day = cal_day
            elif src == "lunch":
                day = m.get("fetched_on")                # the day the menu PDF was read
            else:
                day = None
        if src == "lunch" and m.get("event_date"):      # the school year of the menu's own date, not of the day it was read
            cls = {"school_year": freshness.label(freshness.current_start(date.fromisoformat(m["event_date"]))), "stale": False, "stale_reason": ""}
        if src == "calendar_sheet" and url in by_url:        # a team schedule page: structured and current, dated by its fetch
            cls = freshness.classify("", "calendar_sheet", today)
        m["indexed_at"] = now
        m["source_date"] = day or today.isoformat()
        m["school_year"] = cls["school_year"]
        m["stale"] = cls["stale"]
        if cls["stale"]:
            m["stale_reason"] = cls["stale_reason"]
        else:
            m.pop("stale_reason", None)
        stats["annotated"] += 1
        stats["stale"] += bool(cls["stale"])
    return stats


def run(log=print) -> list[dict]:
    chunks, seen_bodies, seen_anchors = [], set(), {}
    skipped_private = skipped_old = skipped_noise = skipped_dup = 0
    for page in sorted(load_pages(), key=page_rank):
        if sheets.is_sensitive(page):
            skipped_private += 1
            continue
        if config.SUPERSEDED_TITLE.search(page.get("title", "") or ""):
            skipped_old += 1
            continue
        if config.NOISE_TITLE.search(page.get("title", "") or ""):
            skipped_noise += 1
            continue
        if page.get("source") == "google_sheet" and sheets.sports_events(page, date(2000, 1, 1), date(2100, 1, 1)):
            continue          # a team schedule: its games become structured events below, the raw CSV text would only add noise
        for n, content in chunk_page(page):
            body_hash = hashlib.sha1(content.split("\n\n", 1)[1].encode()).hexdigest()
            if body_hash in seen_bodies or near_duplicate(content, page["url"], seen_anchors):
                skipped_dup += 1
                continue
            seen_bodies.add(body_hash)
            chunks.append({
                "chunk_id": f"{hashlib.sha1(page['url'].encode()).hexdigest()[:12]}-{n:04d}",
                "content": content, "source_file": page["url"],
                "metadata": {"source_url": page["url"], "title": page["title"], "source": page["source"],
                             "content_hash": hashlib.sha1(content.encode()).hexdigest()},
            })
    today = date.today()
    for page in load_pages():
        if sheets.is_sensitive(page) or config.SUPERSEDED_TITLE.search(page.get("title", "") or "") \
                or config.NOISE_TITLE.search(page.get("title", "") or ""):
            continue
        page["title"] = page_title(page)
        if page.get("source") == "google_sheet":
            for e in sheets.sports_events(page, today - timedelta(days=14), today + timedelta(days=430)):
                chunks.append(sheets.event_chunk(e))
            for j, (title, body) in enumerate(sheets.club_outline(page)):
                content = f"Title: {title}\nSection: {title} (outline)\nSource: {page['url']}\n\n{body}"
                chunks.append({"chunk_id": f"{hashlib.sha1(page['url'].encode()).hexdigest()[:12]}-clubs-{j:03d}", "content": content,
                               "source_file": page["url"], "metadata": {"source_url": page["url"], "title": title, "source": page["source"],
                                                                      "kind": "outline", "content_hash": hashlib.sha1(content.encode()).hexdigest()}})
        for j, (trail, body) in enumerate(outline_chunks(page)):
            content = f"Title: {page['title']}\nSection: {trail} (outline)\nSource: {page['url']}\n\n{body}"
            chunks.append({"chunk_id": f"{hashlib.sha1(page['url'].encode()).hexdigest()[:12]}-outline-{j:03d}",
                           "content": content, "source_file": page["url"],
                           "metadata": {"source_url": page["url"], "title": page["title"], "source": page["source"],
                                        "kind": "outline", "content_hash": hashlib.sha1(content.encode()).hexdigest()}})
    chunks.extend(facts.build_chunks(log))                 # curated, human-confirmed facts (bell schedule, ...)
    chunks.extend(workbooks.build_chunks(log))            # teacher/course map, A/B day calendar, advisors, clubs
    links = build_links(log)
    for i, card in enumerate(link_cards(links)):
        content = f"Title: {card['title']}\nSource: link_registry\n\n{card['text']}"
        chunks.append({
            "chunk_id": f"link-{hashlib.sha1(card['source_url'].encode()).hexdigest()[:12]}",
            "content": content, "source_file": card["source_url"],
            "metadata": {"source_url": card["source_url"], "title": card["title"], "source": "link_registry",
                         "link_category": card["link_category"], "label": card["label"], "url": card["source_url"],
                         "found_on": card["found_on"], "pinned": card["pinned"],
                         "content_hash": hashlib.sha1(content.encode()).hexdigest()},
        })
    if config.CALENDAR_FILE.exists():
        chunks.extend(json.loads(config.CALENDAR_FILE.read_text(encoding="utf-8")))
    if config.LUNCH_FILE.exists():                   # the cafe's monthly menus, parsed by pipeline/lunch.py (one structured record per campus per day)
        chunks.extend(json.loads(config.LUNCH_FILE.read_text(encoding="utf-8")))
    unique, seen_ids = [], set()
    for c in chunks:                    # one row per id: Postgres rejects duplicates inside a single upsert
        if c["chunk_id"] not in seen_ids:
            seen_ids.add(c["chunk_id"])
            unique.append(c)
    chunks = unique
    fstats = annotate_freshness(chunks, load_pages())
    log(f"freshness metadata: {fstats}")
    config.CHUNKS_FILE.write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    sizes = sorted(len(c["content"]) for c in chunks)
    if skipped_old:
        log(f"superseded filter: skipped {skipped_old} page(s) from the previous school year")
    if skipped_noise:
        log(f"noise filter: skipped {skipped_noise} login/directory page(s)")
    if skipped_dup:
        log(f"near-duplicate filter: dropped {skipped_dup} chunk(s) that repeat another page's text")
    if skipped_private:
        log(f"privacy filter: skipped {skipped_private} sheet(s) with student/people data")
    log(f"chunking done: {len(chunks)} chunks, median {sizes[len(sizes)//2] if sizes else 0} chars -> {config.CHUNKS_FILE}")
    return chunks
