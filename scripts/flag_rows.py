#!/usr/bin/env python
"""Flag (never delete) rows of the live chunk table that should not be retrieved. Metadata only: metadata.superseded=true plus
metadata.flag_reason and metadata.flagged_at; retrieval already ignores superseded rows (rag.py). Nothing is deleted or re-embedded.

    python scripts/flag_rows.py                  # dry run: print the plan and write data/pipeline/flag_plan.json
    python scripts/flag_rows.py --apply          # write the flags (UPDATE of the metadata column only)
    python scripts/flag_rows.py --sql            # print review + delete SQL for the owner (nothing is executed)
    python scripts/flag_rows.py --db-json dump   # plan from a saved dump instead of a SELECT

Reasons:
  sensitive:<labels>      a shared login / password, a meeting passcode in a link, a personal cell number (pipeline/safety.py)
  duplicate_of:<doc>      a lower-ranked copy of text that a better copy of the same document already holds (>= 90% of its sentences),
                          rank = google_doc > manual > public_site/slides > nexus, then "has a Section path", then size. The best copy stays.
  replaced_by_public_refresh   an earlier capture of a page that pipeline/public_pages.py re-read (FAQ answers were missing from it)
Email, lunch and calendar rows and rows already flagged are never touched.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import freshness                    # noqa: E402
from pipeline import config, safety  # noqa: E402

DOC_SOURCES = {"google_doc": 0, "manual": 1, "public_site": 2, "google_slides": 2, "nexus": 3, "google_sheet": 3}
SKIP_SOURCES = {"calendar", "calendar_day", "calendar_sheet", "lunch", "email", "share_sheet", "share_doc", "school_day", "school_courses", "school_clubs",
                "school_advisory", "school_facts", "school_canonical"}
DOC_COVERAGE = 0.75      # share of a copy's sentences found in a better copy for it to count as a copy at all
ROW_COVERAGE = 0.60      # and per row, so a row that holds text only this copy has is kept
MIN_ROW_CHARS = 200


def body_of(content: str) -> str:
    return content.split("\n\n", 1)[-1]


def sentences(text: str) -> list[str]:
    out = []
    for s in re.split(r"(?<=[.!?:;])\s+|\n+", text):
        s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
        if len(s) >= 25:
            out.append(s)
    return out


def family(title: str) -> str:
    """Documents that are the same text under different titles are compared with each other; everything else only with the same title."""
    t = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    if "athletics" not in t and re.search(r"student (?:and )?family handbook", t):
        return "handbook"
    if re.search(r"(2026 27|26 27|2026 2027).*course catalog|course catalog.*(2026 27|2026 2027)", t):
        return "catalog-2026-27"
    return t


def doc_key(r: dict) -> str:
    """The 12-hex document prefix of a chunk id (<hash>-0007, <hash>-outline-001, <hash>-clubs-000); other ids (cal-, link-) keep their stem."""
    cid = r["chunk_id"]
    return cid[:12] if re.match(r"^[0-9a-f]{12}-", cid) else cid.rsplit("-", 1)[0]


def plan(rows: list[dict]) -> dict[str, tuple[str, str]]:
    """-> {chunk_id: (reason, note)} for every row that should be flagged."""
    flags: dict[str, tuple[str, str]] = {}
    live = [r for r in rows if not (r.get("metadata") or {}).get("superseded") and not freshness.is_ingested_row(r["chunk_id"], (r.get("metadata") or {}).get("source"), r.get("metadata"))]
    # 1. sensitive content
    for r in live:
        if (r["metadata"] or {}).get("source") in SKIP_SOURCES or (r["metadata"] or {}).get("source") in ("calendar", "calendar_day", "calendar_sheet", "lunch"):
            continue                                   # events are re-scrubbed by the calendar stage on the next refresh, never hidden
        labels = safety.find(r["content"])
        if labels:
            flags[r["chunk_id"]] = ("sensitive:" + ",".join(labels), "")
    # 2. replaced by a refreshed public page
    new_prefix = {hashlib.sha1((u + "#public").encode()).hexdigest()[:12]: u for u in config.PUBLIC_PAGES}
    present = {doc_key(r) for r in rows}
    refreshed = {u for p, u in new_prefix.items() if p in present}
    for r in live:
        m = r["metadata"] or {}
        if m.get("source") in ("nexus", "public_site") and m.get("source_url") in refreshed and doc_key(r) not in new_prefix and r["chunk_id"] not in flags:
            flags[r["chunk_id"]] = ("replaced_by_public_refresh", m.get("source_url", ""))
    # 3. duplicate copies of a document: a lower-ranked copy whose text a better copy of the SAME document family already holds
    docs = defaultdict(list)
    for r in live:
        m = r["metadata"] or {}
        if m.get("source") in DOC_SOURCES and r["chunk_id"] not in flags and "-outline-" not in r["chunk_id"] and "-clubs-" not in r["chunk_id"]:
            docs[doc_key(r)].append(r)
    info = {}
    for k, rs in docs.items():
        m0 = rs[0]["metadata"]
        has_sec = sum("\nSection:" in r["content"] for r in rs) / len(rs) > 0.5
        stale = sum(bool(r["metadata"].get("stale")) for r in rs) / len(rs) > 0.5
        info[k] = {"score": (DOC_SOURCES[m0.get("source")], 0 if has_sec else 1, -sum(len(r["content"]) for r in rs), k),
                   "family": family(m0.get("title") or ""), "stale": stale, "title": (m0.get("title") or "")[:60],
                   "sents": {s for r in rs for s in sentences(body_of(r["content"]))}}
    for k, rs in docs.items():
        mine = info[k]
        fam = [d for d in docs if info[d]["family"] == mine["family"] and not info[d]["stale"]]
        root = min(fam, key=lambda d: info[d]["score"]) if fam else None          # compare only with the best copy, never with another copy that is itself flagged
        better = [root] if root and root != k and info[root]["score"] < mine["score"] else []
        if not better or not mine["sents"]:
            continue
        top = max(better, key=lambda d: len(mine["sents"] & info[d]["sents"]))
        if len(mine["sents"] & info[top]["sents"]) / len(mine["sents"]) < DOC_COVERAGE:
            continue                                   # a different edition, not a copy: keep every row
        for r in rs:
            sents = sentences(body_of(r["content"]))
            if len(r["content"]) >= MIN_ROW_CHARS and sents and sum(s in info[top]["sents"] for s in sents) / len(sents) >= ROW_COVERAGE:
                flags[r["chunk_id"]] = (f"duplicate_of:{top}", info[top]["title"])
    return flags


def fetch_rows() -> list[dict]:
    from dotenv import load_dotenv
    from supabase import create_client
    load_dotenv(ROOT / ".env")
    sb = create_client(os.environ["SUPABASE_URL"].strip().strip("\"'"), os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip().strip("\"'"))
    rows, s = [], 0
    while True:
        r = sb.table("document_chunks_v2").select("chunk_id,content,metadata").range(s, s + 999).execute().data
        rows += r
        if len(r) < 1000:
            return rows
        s += 1000


def apply(flags: dict, rows: list[dict]) -> int:
    from dotenv import load_dotenv
    from supabase import create_client
    load_dotenv(ROOT / ".env")
    sb = create_client(os.environ["SUPABASE_URL"].strip().strip("\"'"), os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip().strip("\"'"))
    by = {r["chunk_id"]: r for r in rows}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    n = 0
    for cid, (reason, note) in flags.items():
        m = dict(by[cid]["metadata"] or {})
        if m.get("superseded"):
            continue
        m.update({"superseded": True, "flag_reason": reason + (f" ({note})" if note else ""), "flagged_at": now})
        sb.table("document_chunks_v2").update({"metadata": m}).eq("chunk_id", cid).execute()
        n += 1
    return n


def sql(n_flagged: int) -> str:
    return ("-- 1. review what was flagged (reason per row):\n"
            "select chunk_id, metadata->>'title' as title, metadata->>'source' as source, metadata->>'flag_reason' as reason\n"
            "from document_chunks_v2 where metadata->>'superseded' = 'true' and metadata->>'flagged_at' is not null order by 4, 1;\n\n"
            "-- 2. delete exactly the rows flagged by scripts/flag_rows.py (only after reviewing step 1):\n"
            "delete from document_chunks_v2\nwhere metadata->>'superseded' = 'true' and metadata->>'flagged_at' is not null;\n"
            f"-- ({n_flagged} rows are flagged right now)\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sql", action="store_true")
    ap.add_argument("--db-json")
    a = ap.parse_args()
    rows = json.loads(Path(a.db_json).read_text()) if a.db_json else fetch_rows()
    flags = plan(rows)
    by_reason = defaultdict(int)
    for reason, _ in flags.values():
        by_reason[reason.split(":")[0]] += 1
    print(f"{len(rows)} rows read; {len(flags)} to flag: {dict(by_reason)}")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "flag_plan.json").write_text(json.dumps({k: list(v) for k, v in flags.items()}, indent=1))
    if a.sql:
        print(sql(sum(1 for r in rows if (r["metadata"] or {}).get("flagged_at")) + len(flags)))
    if a.apply:
        print(f"flagged {apply(flags, rows)} rows (metadata only, nothing deleted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
