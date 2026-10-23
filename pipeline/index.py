"""Stage 6: embed and upsert into Supabase, incrementally.

Only chunks that are new or whose content_hash changed are embedded, so a refresh of an unchanged site costs
seconds. Embeddings come from the same Supabase Edge Function the server uses for questions (gte-small, 384-d),
so documents and questions are always embedded identically. No ML libraries are needed here either.

Deletion is opt-in:
  --prune         remove chunks THIS pipeline wrote earlier that no longer exist
  --prune-legacy  remove rows without a source_url (the old pipeline; only exists in the v1 table)
Expired calendar events are always removed.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # settings.py / embedder.py live at the repo root
import embedder          # noqa: E402
import settings          # noqa: E402

PAGE = 1000
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
            rows[x["chunk_id"]] = (x.get("h"), x.get("u"), x.get("s"))
        if len(r) < PAGE:
            return rows
        start += PAGE


def delete_ids(sb, ids, log):
    ids = list(ids)
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


def run(prune=False, prune_legacy=False, dry_run=False, log=print) -> dict:
    chunks = json.loads(config.CHUNKS_FILE.read_text(encoding="utf-8"))
    chunks = list({c["chunk_id"]: c for c in chunks}.values())          # one row per id
    sb = client()
    have = existing(sb)
    new_ids = {c["chunk_id"] for c in chunks}
    todo = [c for c in chunks if have.get(c["chunk_id"], (None,))[0] != c["metadata"]["content_hash"]]
    stale = [i for i, (h, u, s) in have.items() if u and i not in new_ids]       # written by this pipeline
    legacy = [i for i, (h, u, s) in have.items() if not u]                       # old pipeline
    # Expired calendar events are removed automatically (only when this run produced calendar chunks).
    expired_cal = [i for i, (h, u, s) in have.items() if s in ("calendar", "calendar_day") and i not in new_ids] \
        if any(c["metadata"].get("source") in ("calendar", "calendar_day") for c in chunks) else []
    stats = {"table": settings.CHUNKS_TABLE, "total_chunks": len(chunks), "to_embed": len(todo), "unchanged": len(chunks) - len(todo),
             "stale_in_db": len(stale), "legacy_in_db": len(legacy), "expired_calendar": len(expired_cal)}
    log(f"index plan: {stats}")
    if dry_run:
        return stats

    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    done, t0 = 0, time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for n in pool.map(lambda b: _embed_and_upsert(sb, b), batches):
            done += n
            if (done // BATCH) % 10 == 0 or done == len(todo):
                log(f"  embedded+upserted {done}/{len(todo)}  ({time.time() - t0:.0f}s)")
    if expired_cal:
        delete_ids(sb, expired_cal, log)
    if prune and stale:
        delete_ids(sb, stale, log)
    if prune_legacy and legacy:
        delete_ids(sb, legacy, log)
    return stats
