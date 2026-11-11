"""Curated, human-confirmed facts: every '## heading' in knowledge/*.md becomes one retrievable record.

Use this for things that live in people's heads rather than in a document (bell times, what A' means, ...) and for canonical
lists written from published sources (every sport, leadership by role, who to contact for what). It is committed to the repository,
so every pipeline run (including the daily GitHub job) includes it.

File conventions (checked by scripts/audit_data.py, group canon.*):
  * a line `Last updated: YYYY-MM-DD` before the first `##` is the as-of date of the whole file; it is written into every record
    (text and metadata `as_of`) so the model and the freshness code know how old the fact is
  * each section states its Source and the school year it applies to in its own text
  * an HTML comment `<!-- verify: title~<page title> :: token ; token | url~<url part> :: token -->` names the stored source pages
    and the exact strings that must appear in them. It is NOT indexed; the audit re-checks it against data/pipeline/pages.
"""
import hashlib
import re
from pathlib import Path

from . import config, safety

DIR = config.ROOT / "knowledge"
LAST_UPDATED = re.compile(r"^Last updated:\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)
VERIFY = re.compile(r"<!--\s*verify:(.*?)-->", re.S)


def split_file(path: Path):
    """-> (doc_title, as_of or None, [(head, body, verify_spec_text)])"""
    text = path.read_text(encoding="utf-8")
    doc_title = (re.search(r"^# (.+)$", text, re.M) or [None, path.stem])[1].strip()
    as_of = (LAST_UPDATED.search(text.split("\n## ", 1)[0]) or [None, None])[1]
    out = []
    for section in re.split(r"^## ", text, flags=re.M)[1:]:
        head, _, body = section.partition("\n")
        spec = " | ".join(m.strip() for m in VERIFY.findall(body))
        body = VERIFY.sub("", body).strip()
        if head.strip() and body:
            out.append((head.strip(), body, spec))
    return doc_title, as_of, out


def verify_specs() -> list[dict]:
    """Every verify spec of every knowledge file: [{file, heading, checks: [(selector, [tokens])]}] (for the audit)."""
    specs = []
    for path in sorted(DIR.glob("*.md")) if DIR.exists() else []:
        _, as_of, secs = split_file(path)
        for head, _, spec in secs:
            checks = []
            for part in filter(None, (p.strip() for p in spec.split(" | "))):
                sel, _, toks = part.partition("::")
                checks.append((sel.strip(), [t.strip() for t in toks.split(";") if t.strip()]))
            specs.append({"file": path.name, "heading": head, "checks": checks, "as_of": as_of})
    return specs


BODY_LIMIT = 1150      # body characters per record: with its header a record stays under CHUNK_MAX_CHARS (1,600), so nothing is cut when shown


def split_section(head: str, body: str, limit: int = BODY_LIMIT) -> list[tuple[str, str]]:
    """A section longer than `limit` becomes several records "(part i of n)". The lines before the first bullet (source, date, school
    year) are repeated in every part so each part is self-explanatory; bullets are never cut."""
    if len(body) <= limit:
        return [(head, body)]
    lines = body.split("\n")
    k = next((i for i, l in enumerate(lines) if l.lstrip().startswith("- ")), len(lines))
    pre, items = "\n".join(lines[:k]), lines[k:]
    pre = pre if len(pre) < 420 else pre[:420].rsplit(" ", 1)[0] + " ..."
    parts, cur = [], []
    for ln in items:
        if cur and len(pre) + sum(len(x) + 1 for x in cur) + len(ln) > limit:
            parts.append(cur)
            cur = []
        cur.append(ln)
    if cur:
        parts.append(cur)
    if len(parts) == 1:
        return [(head, body)]
    return [(f"{head} (part {i + 1} of {len(parts)})", (pre + "\n" if pre else "") + "\n".join(p)) for i, p in enumerate(parts)]


def build_chunks(log=print) -> list[dict]:
    chunks = []
    for path in sorted(DIR.glob("*.md")) if DIR.exists() else []:
        doc_title, as_of, secs = split_file(path)
        for head0, body0, _ in secs:
            for head, body in (split_section(head0, body0) if as_of else [(head0, body0)]):       # dated files are the new, long ones
                stamp = f"\nLast updated: {as_of}" if as_of else ""
                content = f"Title: {head}\nSection: {doc_title}\nSource: school facts ({path.name}){stamp}\n\n{head}\n{body}"
                content, _ = safety.scrub(content)
                meta = {"source": "school_facts", "source_url": f"facts://{path.name}", "title": head, "kind": "fact",
                        "content_hash": hashlib.sha1(content.encode()).hexdigest()}
                if as_of:
                    meta["as_of"] = as_of
                chunks.append({"chunk_id": "fact-" + hashlib.sha1(f"{path.name}|{head}".encode()).hexdigest()[:14], "content": content,
                               "source_file": f"facts://{path.name}", "metadata": meta})
    log(f"  facts: {len(chunks)} records from {DIR.name}/")
    return chunks
