"""Stage 6: embed and upsert into Supabase, incrementally.

Only chunks that are new or whose content_hash changed are embedded, so a refresh of an unchanged site costs
seconds. Embeddings come from the same Supabase Edge Function the server uses for questions (gte-small, 384-d),
so documents and questions are always embedded identically. No ML libraries are needed here either.

Deletion is opt-in:
  --prune         remove chunks THIS pipeline wrote earlier that no longer exist
  --prune-legacy  remove rows without a source_url (the old pipeline; only exists in the v1 table)
Expired calendar events are removed unless keep_expired (--keep-expired) is set.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import calendar_feed, config

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # settings.py / embedder.py live at the repo root
import embedder          # noqa: E402
import freshness         # noqa: E402
import settings          # noqa: E402

PAGE = 1000
KEEP_FLAGS = ("superseded", "flag_reason", "flagged_at")
BATCH = embedder.MAX_BATCH
WORKERS = 8


def client():
    from supabase import create_client
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing")
    return create_client(url.strip().strip("\"'"), key.strip().strip("\"'"))


def existing(sb) -> dict:
    """chunk_id -> (content_hash or None, source_url or None, source or None)"""
    rows, start = {}, 0
    while True:
        r = sb.table(settings.CHUNKS_TABLE).select("chunk_id,h:metadata->>content_hash,u:metadata->>source_url,s:metadata->>source") \
            .range(start, start + PAGE - 1).execute().data
        for x in r:
            if not freshness.is_ingested_row(x["chunk_id"], x.get("s")):      # forwarded emails and shared Drive files (email_ingest.py / share_ingest.py) are not part of this pipeline's plan
                rows[x["chunk_id"]] = (x.get("h"), x.get("u"), x.get("s"))
        if len(r) < PAGE:
            return rows
        start += PAGE


def freshness_state(sb) -> dict:
    """chunk_id -> (source_date, stale as text) currently stored in metadata (None when the row predates freshness metadata)"""
    rows, start = {}, 0
    while True:
        r = sb.table(settings.CHUNKS_TABLE).select("chunk_id,d:metadata->>source_date,s:metadata->>stale,y:metadata->>school_year") \
            .range(start, start + PAGE - 1).execute().data
        for x in r:
            rows[x["chunk_id"]] = (x.get("d"), x.get("s"), x.get("y"))
        if len(r) < PAGE:
            return rows
        start += PAGE


def patch_metadata(sb, chunk: dict) -> None:
    """Write new metadata onto an existing row without touching its text or embedding (additive, no delete)."""
    for attempt in range(4):
        try:
            meta = dict(chunk["metadata"])
            cur = (sb.table(settings.CHUNKS_TABLE).select("metadata").eq("chunk_id", chunk["chunk_id"]).limit(1).execute().data or [{}])[0].get("metadata") or {}
            for k in KEEP_FLAGS:           # a review flag (scripts/flag_rows.py) set by a person or tool must survive a freshness patch
                if k in cur:
                    meta[k] = cur[k]
            sb.table(settings.CHUNKS_TABLE).update({"metadata": meta}).eq("chunk_id", chunk["chunk_id"]).execute()
            return
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


WORKBOOK_SOURCES = ("school_day", "school_courses", "school_clubs", "school_advisory", "school_facts")


def mark_superseded(sb, ids: list[str], log) -> int:
    """Flag rows this pipeline no longer produces (a renamed teacher record, a course whose only sections were dropped)
    with metadata.superseded = true instead of deleting them: retrieval ignores such rows, nothing is lost, and a later
    --prune can remove them. Returns how many rows were newly flagged."""
    n = 0
    ids = [i for i in ids if not freshness.is_ingested_row(i)]
    for i in range(0, len(ids), 100):
        batch = ids[i:i + 100]
        rows = sb.table(settings.CHUNKS_TABLE).select("chunk_id,metadata").in_("chunk_id", batch).execute().data or []
        for r in rows:
            m = r["metadata"] or {}
            if not m.get("superseded"):
                sb.table(settings.CHUNKS_TABLE).update({"metadata": {**m, "superseded": True}}).eq("chunk_id", r["chunk_id"]).execute()
                n += 1
    log(f"  flagged {n} superseded rows (metadata.superseded=true; not deleted)")
    return n


def flagged_by_pipeline(sb) -> set:
    """chunk_ids carrying metadata.superseded=true that were set by mark_superseded (no review flag: scripts/flag_rows.py always
    adds flagged_at). If such a row is back in the current plan it was flagged by a run that did not produce it (e.g. CI without
    some data) and must be restored."""
    ids, start = set(), 0
    while True:
        r = sb.table(settings.CHUNKS_TABLE).select("chunk_id").eq("metadata->>superseded", "true").is_("metadata->>flagged_at", "null") \
            .range(start, start + PAGE - 1).execute().data
        ids.update(x["chunk_id"] for x in r)
        if len(r) < PAGE:
            return ids
        start += PAGE


def unflag_current(sb, ids: list[str], log) -> int:
    """Clear superseded on rows the current plan produces again (metadata only; review flags with flagged_at are never touched)."""
    n = 0
    for i in range(0, len(ids), 100):
        rows = sb.table(settings.CHUNKS_TABLE).select("chunk_id,metadata").in_("chunk_id", ids[i:i + 100]).execute().data or []
        for r in rows:
            m = dict(r["metadata"] or {})
            if m.get("superseded") and "flagged_at" not in m:
                m.pop("superseded", None)
                sb.table(settings.CHUNKS_TABLE).update({"metadata": m}).eq("chunk_id", r["chunk_id"]).execute()
                n += 1
    log(f"  restored {n} rows wrongly flagged superseded (back in the current plan)")
    return n


FEED_SOURCES = ("calendar", "calendar_day")
CAL_SOURCES = FEED_SOURCES + ("calendar_sheet",)
MAX_REMOVE_FRACTION = 0.25      # a run that would remove more than this share of the calendar rows in the table is treated as a bad fetch
MAX_REMOVE_FLOOR = 30           # ... unless it is only a handful of rows


def calendar_rows(sb) -> list[dict]:
    """Every calendar row in the table as {id, source, date, end, url, summary} (read-only; paged)."""
    rows, start = [], 0
    while True:
        r = sb.table(settings.CHUNKS_TABLE).select("chunk_id,s:metadata->>source,d:metadata->>event_date,e:metadata->>end_date,u:metadata->>source_url,t:metadata->>summary") \
            .in_("metadata->>source", list(CAL_SOURCES)).range(start, start + PAGE - 1).execute().data
        rows += [{"id": x["chunk_id"], "source": x["s"], "date": x.get("d"), "end": x.get("e") or x.get("d"), "url": x.get("u"), "summary": x.get("t") or ""} for x in r]
        if len(r) < PAGE:
            return rows
        start += PAGE


def removal_refused(n_remove: int, n_table: int) -> bool:
    """True when the number of removals that depend on the feed or sheet being complete is too large to trust (a truncated feed or sheet
    would otherwise wipe the calendar). Rows older than the school year are removed by date alone and are not counted."""
    return n_remove > max(MAX_REMOVE_FLOOR, MAX_REMOVE_FRACTION * n_table)


def plan_calendar_removals(db_rows: list[dict], chunks: list[dict], today, floor=None) -> dict:
    """Which calendar rows in the table does this run's own output replace? Only rows of a source this run produced are ever considered,
    and only these kinds are removed (never anything else):
      * calendar / calendar_day rows missing from the feed that are in the FUTURE (or running today): the event was cancelled or moved;
      * the same event edited (same day and title, other time or place) or the day's schedule digest rewritten: the old row is a duplicate;
      * rows older than the history window (window_floor: the start of the school year): last year's events;
      * calendar_sheet rows of a team sheet this run read again, dated today or later, that are no longer on that sheet (a game dropped or
        changed opponent).
    Events of the current school year that already happened stay answerable ("when was X") even if they have left the feed.
    -> {"remove": [ids], "history_kept": n, "by_reason": {...}, "dated": [ids removed for age alone]}"""
    today_s = today.isoformat()
    floor_s = (floor or calendar_feed.window_floor(today)).isoformat()
    plan_ids = {c["chunk_id"] for c in chunks}
    produced = {c["metadata"].get("source") for c in chunks}
    plan_events = {(c["metadata"].get("event_date"), (c["metadata"].get("summary") or "").lower()) for c in chunks if c["metadata"].get("source") == "calendar"}
    plan_day_rows = {c["metadata"].get("event_date") for c in chunks if c["metadata"].get("source") == "calendar_day"}
    plan_sheet_urls = {c["metadata"].get("source_url") for c in chunks if c["metadata"].get("source") == "calendar_sheet"}
    remove, dated, kept, why = [], [], 0, {}

    def drop(i, reason):
        remove.append(i)
        if reason == "before_history_window":
            dated.append(i)
        why[reason] = why.get(reason, 0) + 1
    for r in db_rows:
        if r["id"] in plan_ids or r["source"] not in produced or r["source"] not in CAL_SOURCES or freshness.is_email_row(r["id"], r["source"]):
            continue
        d, end = r["date"] or "", r["end"] or r["date"] or ""
        if r["source"] == "calendar_sheet":
            if r["url"] in plan_sheet_urls and end >= today_s:
                drop(r["id"], "sheet_row_gone_from_sheet")
            else:
                kept += 1
            continue
        if d and end < floor_s:
            drop(r["id"], "before_history_window")
        elif end >= today_s:
            drop(r["id"], "cancelled_or_moved")
        elif (r["source"] == "calendar" and (d, r["summary"].lower()) in plan_events) or (r["source"] == "calendar_day" and d in plan_day_rows):
            drop(r["id"], "edited_duplicate")
        else:
            kept += 1                       # a past event of this school year that left the feed: kept as history
    return {"remove": remove, "history_kept": kept, "by_reason": why, "dated": dated}


def delete_ids(sb, ids, log):
    ids = [i for i in ids if not freshness.is_ingested_row(i)]          # email and shared-file chunks are never pruned here, whatever the plan says
    for i in range(0, len(ids), 100):
        sb.table(settings.CHUNKS_TABLE).delete().in_("chunk_id", ids[i:i + 100]).execute()
    log(f"  deleted {len(ids)} rows")


def _embed_and_upsert(sb, batch: list[dict]) -> int:
    vecs = embedder.embed([c["content"] for c in batch])
    # source_file is varchar(255); the full URL stays in metadata.source_url
    rows = [{**c, "source_file": c["source_file"][:250], "embedding": v} for c, v in zip(batch, vecs)]
    for attempt in range(4):
        try:
            sb.table(settings.CHUNKS_TABLE).upsert(rows, on_conflict="chunk_id").execute()
            return len(rows)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    return 0


def prunable_lunch(i: str, source, lunch_campuses: set) -> bool:
    """A lunch row (pipeline/lunch.py) may be pruned only when this run produced menus for the same campus: a campus whose menu could not be
    fetched keeps its rows, so the app still answers from the last menu instead of going blank. Rows of a past month then age out normally."""
    return freshness.lunch_campus(i) in lunch_campuses


def run(prune=False, prune_legacy=False, dry_run=False, log=print, keep_expired=False, only_source: str | None = None, additive_only=False) -> dict:
    """only_source (e.g. "lunch") restricts the whole plan (embed, patch, flag, prune) to rows of that source, so one stage can be indexed
    without touching anything else (a comma-separated list is accepted: "school_facts,school_canonical").
    additive_only INSERTS rows whose chunk_id is not in the table and does nothing else: no rewrite of a changed row, no metadata patch,
    no superseded flag, no expired-calendar delete, no prune. It is how new knowledge goes into the live table that the site reads."""
    chunks = json.loads(config.CHUNKS_FILE.read_text(encoding="utf-8"))
    chunks = list({c["chunk_id"]: c for c in chunks}.values())          # one row per id
    if only_source:
        wanted = {x.strip() for x in only_source.split(",") if x.strip()}
        chunks = [c for c in chunks if c["metadata"].get("source") in wanted]
    sb = client()
    have = existing(sb)
    if only_source:
        have = {i: v for i, v in have.items() if v[2] in wanted}
    new_ids = {c["chunk_id"] for c in chunks}
    lunch_campuses = {c["metadata"].get("campus") for c in chunks if c["metadata"].get("source") == "lunch"}
    todo = [c for c in chunks if have.get(c["chunk_id"], (None,))[0] != c["metadata"]["content_hash"]]
    changed_existing = [c for c in todo if c["chunk_id"] in have]      # same id, new text: a rewrite, which additive_only refuses
    if additive_only:
        todo = [c for c in todo if c["chunk_id"] not in have]
    stale = [i for i, (h, u, s) in have.items() if u and i not in new_ids
             and (not freshness.is_lunch_row(i, s) or prunable_lunch(i, s, lunch_campuses))]       # written by this pipeline
    legacy = [i for i, (h, u, s) in have.items() if not u and not freshness.is_lunch_row(i, s)]       # old pipeline
    # Calendar rows this run replaces (cancelled/moved/edited events, last year's, dropped team-sheet games) are removed automatically, but
    # past events of the current school year are kept as history. Only rows of a source this run produced are considered.
    cal_plan = {"remove": [], "history_kept": 0, "by_reason": {}, "dated": []}
    cal_table = []
    if any(c["metadata"].get("source") in CAL_SOURCES for c in chunks):
        cal_table = calendar_rows(sb)
        cal_plan = plan_calendar_removals(cal_table, chunks, calendar_feed.today_pacific())
    expired_cal = cal_plan["remove"]
    cal_refused = removal_refused(len(expired_cal) - len(cal_plan["dated"]), len(cal_table))
    if cal_refused:
        expired_cal = cal_plan["dated"]               # still drop last school year's rows (date alone); everything feed-dependent waits for a human look
    # rows whose text is unchanged but whose freshness metadata is missing or different: metadata-only patch
    fresh = freshness_state(sb)
    todo_ids = {c["chunk_id"] for c in todo}
    patch = []
    for c in chunks:
        if c["chunk_id"] in todo_ids or c["chunk_id"] not in have or "source_date" not in c["metadata"]:
            continue
        d, st, y = fresh.get(c["chunk_id"], (None, None, None))
        m = c["metadata"]
        if d is None or (st or "false").lower() != str(bool(m["stale"])).lower() or (y or "") != (m["school_year"] or ""):
            patch.append(c)
    present = {c["metadata"].get("source") for c in chunks}        # only sources this run regenerated (CI has no workbooks)
    superseded = [i for i, (h, u, s) in have.items() if s in WORKBOOK_SOURCES and s in present and i not in new_ids]
    restore = []
    if not additive_only:                       # additive runs never change an existing row
        try:
            restore = sorted(i for i in flagged_by_pipeline(sb) if i in new_ids)
        except Exception as e:                  # a missing capability must never block indexing
            log(f"  (could not check for wrongly flagged rows: {type(e).__name__})")
    stats = {"table": settings.CHUNKS_TABLE, "total_chunks": len(chunks), "to_embed": len(todo), "unchanged": len(chunks) - len(todo),
             "stale_in_db": len(stale), "legacy_in_db": len(legacy), "expired_calendar": len(expired_cal), "calendar_removals_by_reason": cal_plan["by_reason"],
             "calendar_history_kept": cal_plan["history_kept"], "calendar_removal_refused": cal_refused, "calendar_removals_held_back": (len(cal_plan["remove"]) - len(cal_plan["dated"])) if cal_refused else 0,
             "metadata_patch": len(patch), "orphans_to_flag": len(superseded), "unflag_current": len(restore), "additive_only": additive_only,
             "changed_existing_skipped": len(changed_existing) if additive_only else 0,
             "lunch_rows_held_back_from_prune": sum(1 for i, (h, u, s) in have.items() if freshness.is_lunch_row(i, s) and i not in new_ids and not prunable_lunch(i, s, lunch_campuses))}
    log(f"index plan: {stats}")
    if dry_run:
        return stats

    if additive_only:
        patch, superseded, expired_cal, stale, legacy, restore = [], [], [], [], [], []
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    done, t0 = 0, time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for n in pool.map(lambda b: _embed_and_upsert(sb, b), batches):
            done += n
            if (done // BATCH) % 10 == 0 or done == len(todo):
                log(f"  embedded+upserted {done}/{len(todo)}  ({time.time() - t0:.0f}s)")
    if patch:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(lambda c: patch_metadata(sb, c), patch))
        log(f"  patched freshness metadata on {len(patch)} existing rows")
    if restore:
        unflag_current(sb, restore, log)
    if superseded:
        mark_superseded(sb, superseded, log)
    if cal_refused:
        log(f"  REFUSED to remove {len(cal_plan['remove']) - len(cal_plan['dated'])} feed-dependent calendar rows (more than {MAX_REMOVE_FRACTION:.0%} of the "
            f"{len(cal_table)} calendar rows in the table: looks like a truncated feed or sheet). Check the calendar stage output. By reason: {cal_plan['by_reason']}")
    if expired_cal and not keep_expired:
        delete_ids(sb, expired_cal, log)
    if prune and stale:
        delete_ids(sb, stale, log)
    if prune_legacy and legacy:
        delete_ids(sb, legacy, log)
    return stats
