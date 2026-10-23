"""Curated, human-confirmed facts: every '## heading' in knowledge/*.md becomes one retrievable record.

Use this for things that live in people's heads rather than in a document (bell times, what A' means, ...). It is
committed to the repository, so every pipeline run (including the daily GitHub job) includes it.
"""
import hashlib
import re
from pathlib import Path

from . import config

DIR = config.ROOT / "knowledge"


def build_chunks(log=print) -> list[dict]:
    chunks = []
    for path in sorted(DIR.glob("*.md")) if DIR.exists() else []:
        text = path.read_text(encoding="utf-8")
        doc_title = (re.search(r"^# (.+)$", text, re.M) or [None, path.stem])[1].strip()
        for section in re.split(r"^## ", text, flags=re.M)[1:]:
            head, _, body = section.partition("\n")
            head, body = head.strip(), body.strip()
            if not body:
                continue
            content = f"Title: {head}\nSection: {doc_title}\nSource: school facts ({path.name})\n\n{head}\n{body}"
            chunks.append({"chunk_id": "fact-" + hashlib.sha1(f"{path.name}|{head}".encode()).hexdigest()[:14], "content": content,
                           "source_file": f"facts://{path.name}",
                           "metadata": {"source": "school_facts", "source_url": f"facts://{path.name}", "title": head, "kind": "fact",
                                        "content_hash": hashlib.sha1(content.encode()).hexdigest()}})
    log(f"  facts: {len(chunks)} records from {DIR.name}/")
    return chunks
