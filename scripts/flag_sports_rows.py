#!/usr/bin/env python
"""Flag (never delete) the athletics rows that the sports update replaced.

    python scripts/flag_sports_rows.py            # dry run: list what would be flagged
    python scripts/flag_sports_rows.py --apply    # set metadata.superseded=true + flag_reason + flagged_at on exactly those rows

Three groups, all metadata-only (retrieval ignores superseded rows; the row text and embedding are untouched, a later delete is a person's decision):
  A  calendar_sheet rows the current plan (data/pipeline/chunks.json) no longer produces: games a team sheet has since moved, the stale second
     "Home schedule" table copies, "Bye" rows, duplicates the merge now collapses
  B  the athletics fact records whose headings were rewritten (older wording: golf/flag football/ultimate frisbee, Lower School list)
  C  raw table copies of the girls volleyball sheet in the Nexus import (stale times; the structured schedule rows replace them)
Rows already flagged, e-mail, lunch and gshare rows are never touched. Needs SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv        # noqa: E402

from pipeline import config, index    # noqa: E402
import settings                       # noqa: E402

load_dotenv(ROOT / ".env")

OLD_FACT_TITLES = [
    "What sports does Nueva offer? All sports and teams at a glance (Upper School, Middle School, Lower School grade 4), by season (part 1 of 2)",
    "What sports does Nueva offer? All sports and teams at a glance (Upper School, Middle School, Lower School grade 4), by season (part 2 of 2)",
    "Upper School interscholastic sports 2026-27, by season (all sports Nueva lists for grades 9-12)",
    "Which Upper School sports are listed in only some sources (golf, ultimate frisbee, flag football, swimming): conflicts to know about (part 1 of 2)",
    "Which Upper School sports are listed in only some sources (golf, ultimate frisbee, flag football, swimming): conflicts to know about (part 2 of 2)",
    "Does Nueva have football, hockey, baseball, softball, lacrosse, rugby, wrestling, badminton or water polo? (sports with NO team: not in any published team list)",
    "Middle School and Lower School (grade 4) sports by season, 2026-27 (part 1 of 2)",
    "Middle School and Lower School (grade 4) sports by season, 2026-27 (part 2 of 2)",
    "Teams with games or practices on the schedule (by level and gender)",
    "Teams with games or practices on the schedule (by level and gender) (part 1 of 3)",
    "Teams with games or practices on the schedule (by level and gender) (part 2 of 3)",
    "Teams with games or practices on the schedule (by level and gender) (part 3 of 3)",
]
VB_SHEET = "1xGpYttrtbyu8h3SWryYQH4ILj5VeL3oT_y36TYnYNEI"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    plan = {c["chunk_id"] for c in json.loads(config.CHUNKS_FILE.read_text(encoding="utf-8"))}
    sb = index.client()
    rows, start = [], 0
    while True:
        r = sb.table(settings.CHUNKS_TABLE).select("chunk_id,metadata").in_("metadata->>source", ["calendar_sheet", "school_facts", "school_canonical", "nexus"]) \
            .range(start, start + 999).execute().data
        rows += r
        if len(r) < 1000:
            break
        start += 1000
    todo = []
    for r in rows:
        m, cid = r["metadata"] or {}, r["chunk_id"]
        if m.get("superseded") or m.get("flagged_at") or cid in plan:
            continue
        src = m.get("source")
        if src == "calendar_sheet":
            todo.append((r, "A", "team schedule row no longer produced (game moved, repeated home-table copy, bye or duplicate); replaced by the merged schedule"))
        elif src in ("school_facts", "school_canonical") and m.get("title") in OLD_FACT_TITLES:
            todo.append((r, "B", "athletics record rewritten 2026-11-12 (verified Upper School sports list); the new record replaces it"))
        elif src == "nexus" and VB_SHEET in (m.get("source_url") or ""):
            todo.append((r, "C", "raw Nexus copy of the girls volleyball sheet (stale times); the structured schedule rows replace it"))
    by = {}
    for r, g, why in todo:
        by.setdefault(g, []).append(r["chunk_id"])
    for g in sorted(by):
        print(f"group {g}: {len(by[g])} rows  e.g. {by[g][:3]}")
    if not a.apply:
        print(f"dry run: {len(todo)} rows would be flagged (python scripts/flag_sports_rows.py --apply)")
        return 0
    now = datetime.now(timezone.utc).isoformat()
    for r, g, why in todo:
        sb.table(settings.CHUNKS_TABLE).update({"metadata": {**r["metadata"], "superseded": True, "flag_reason": f"sports {g}: {why}", "flagged_at": now}}) \
            .eq("chunk_id", r["chunk_id"]).execute()
    print(f"flagged {len(todo)} rows (metadata only, nothing deleted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
