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

from . import config
from .common import load_pages
from .links import build as build_links, link_cards

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
    text = promote_record_titles(page["text"])
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


def chunk_page(page: dict):
    title = page["title"]
    n = 0
    for trail, body in sections(promote_record_titles(page["text"]), title):
        for piece in pack(body):
            if len(piece) < config.MIN_CHUNK_CHARS:
                continue
            header = f"Title: {title}\nSection: {trail}\nSource: {page['url']}\n\n" if trail != title else \
                     f"Title: {title}\nSource: {page['url']}\n\n"
            yield n, header + piece
            n += 1


def run(log=print) -> list[dict]:
    chunks, seen_bodies = [], set()
    for page in load_pages():
        for n, content in chunk_page(page):
            body_hash = hashlib.sha1(content.split("\n\n", 1)[1].encode()).hexdigest()
            if body_hash in seen_bodies:
                continue
            seen_bodies.add(body_hash)
            chunks.append({
                "chunk_id": f"{hashlib.sha1(page['url'].encode()).hexdigest()[:12]}-{n:04d}",
                "content": content, "source_file": page["url"],
                "metadata": {"source_url": page["url"], "title": page["title"], "source": page["source"],
                             "content_hash": hashlib.sha1(content.encode()).hexdigest()},
            })
    for page in load_pages():
        for j, (trail, body) in enumerate(outline_chunks(page)):
            content = f"Title: {page['title']}\nSection: {trail} (outline)\nSource: {page['url']}\n\n{body}"
            chunks.append({"chunk_id": f"{hashlib.sha1(page['url'].encode()).hexdigest()[:12]}-outline-{j:03d}",
                           "content": content, "source_file": page["url"],
                           "metadata": {"source_url": page["url"], "title": page["title"], "source": page["source"],
                                        "kind": "outline", "content_hash": hashlib.sha1(content.encode()).hexdigest()}})
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
    config.CHUNKS_FILE.write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    sizes = sorted(len(c["content"]) for c in chunks)
    log(f"chunking done: {len(chunks)} chunks, median {sizes[len(sizes)//2] if sizes else 0} chars -> {config.CHUNKS_FILE}")
    return chunks
