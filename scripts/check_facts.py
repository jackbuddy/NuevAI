#!/usr/bin/env python
"""Prove every curated fact against its stored source (offline, free).

    python scripts/check_facts.py            # exit 1 when a `verify:` token is missing from the named source page(s)

knowledge/*.md sections carry `<!-- verify: title~<page title> :: token ; token | url~<url part> :: token -->`. A selector
matches saved pages (data/pipeline/pages/*.json) by title or url substring (case-insensitive, any matching page may satisfy a
token); a token is found when it appears in the page text after whitespace normalisation. scripts/audit_data.py reports the same
result as group canon.verify.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import config, facts      # noqa: E402


def norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    s = re.sub(r"[﻿*_#|]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def load_pages():
    pages = []
    for p in sorted(config.PAGES_DIR.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        pages.append({"url": d.get("url", ""), "title": d.get("title", ""), "text": norm(d.get("text", ""))})
    return pages


def check(pages=None) -> tuple[int, list[str]]:
    """-> (number of tokens checked, problems)"""
    pages = pages if pages is not None else load_pages()
    n, problems = 0, []
    for spec in facts.verify_specs():
        if not spec["checks"] and spec.get("as_of"):          # owner-confirmed files without a date (bell schedule, campuses) are exempt
            problems.append(f"{spec['file']} :: {spec['heading']}: no verify spec (every fact needs a source check)")
        for sel, tokens in spec["checks"]:
            kind, _, needle = sel.partition("~")
            needle = needle.strip().lower()
            if kind.strip() not in ("title", "url"):
                problems.append(f"{spec['file']} :: {spec['heading']}: bad selector {sel!r}")
                continue
            cands = [p for p in pages if needle in (p["title"] if kind.strip() == "title" else p["url"]).lower()]
            if not cands:
                problems.append(f"{spec['file']} :: {spec['heading']}: no stored page matches {sel!r}")
                continue
            for t in tokens:
                n += 1
                if not any(norm(t) in p["text"] for p in cands):
                    problems.append(f"{spec['file']} :: {spec['heading']}: {t!r} not found in {sel}")
    return n, problems


def main() -> int:
    n, problems = check()
    for p in problems:
        print("FAIL", p)
    print(f"{n} tokens checked, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
