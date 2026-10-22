"""Stage 6: embed and upsert into Supabase, incrementally.

Only chunks that are new or whose content_hash changed are embedded, so a
weekly refresh of an unchanged site costs seconds. Embeddings use the same
model and normalization as query_server.py (BAAI/bge-large-en-v1.5, 1024-d).

Deletion is opt-in:
  --prune         remove chunks THIS pipeline wrote earlier that no longer exist
  --prune-legacy  remove rows from the old pipeline (metadata has no source_url)
"""
import json
import os
import time

from . import config

PAGE = 1000


def client():
    from supabase import create_client
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing")
    return create_client(url.strip().strip("\"'"), key.strip().strip("\"'"))


def existing(sb) -> dict:
    """chunk_id -> (content_hash or None, source_url or None)"""
    rows, start = {}, 0
    while True:
        r = sb.table("document_chunks").select("chunk_id,h:metadata->>content_hash,u:metadata->>source_url,s:metadata->>source") \
            .range(start, start + PAGE - 1).execute().data
        for x in r:
            rows[x["chunk_id"]] = (x.get("h"), x.get("u"), x.get("s"))
        if len(r) < PAGE:
            return rows
        start += PAGE


def delete_ids(sb, ids, log):
    ids = list(ids)
    for i in range(0, len(ids), 100):
        sb.table("document_chunks").delete().in_("chunk_id", ids[i:i + 100]).execute()
    log(f"  deleted {len(ids)} rows")


def run(prune=False, prune_legacy=False, dry_run=False, log=print) -> dict:
    chunks = json.loads(config.CHUNKS_FILE.read_text(encoding="utf-8"))
    sb = client()
    have = existing(sb)
    new_ids = {c["chunk_id"] for c in chunks}
    todo = [c for c in chunks if have.get(c["chunk_id"], (None,))[0] != c["metadata"]["content_hash"]]
    stale = [i for i, (h, u, s) in have.items() if u and i not in new_ids]    # written by this pipeline
    legacy = [i for i, (h, u, s) in have.items() if not u]                    # old pipeline
    # Expired calendar events are removed automatically (only when this run produced calendar chunks).
    expired_cal = [i for i, (h, u, s) in have.items() if s in ("calendar", "calendar_day") and i not in new_ids] \
        if any(c["metadata"].get("source") in ("calendar", "calendar_day") for c in chunks) else []
    stats = {"total_chunks": len(chunks), "to_embed": len(todo), "unchanged": len(chunks) - len(todo),
             "stale_in_db": len(stale), "legacy_in_db": len(legacy), "expired_calendar": len(expired_cal)}
    log(f"index plan: {stats}")
    if dry_run:
        return stats

    if todo:
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("BAAI/bge-large-en-v1.5", device="cpu")
        t0 = time.time()
        for i in range(0, len(todo), 48):
            batch = todo[i:i + 48]
            vecs = model.encode([c["content"] for c in batch], normalize_embeddings=True, batch_size=16).tolist()
            # document_chunks.source_file is varchar(255); the full URL stays in metadata.source_url
            rows = [{**c, "source_file": c["source_file"][:250], "embedding": v} for c, v in zip(batch, vecs)]
            for attempt in range(4):
                try:
                    sb.table("document_chunks").upsert(rows, on_conflict="chunk_id").execute()
                    break
                except Exception as e:
                    if attempt == 3:
                        raise
                    time.sleep(2 ** attempt)
            done = min(i + 48, len(todo))
            if (i // 48) % 10 == 0 or done == len(todo):
                log(f"  embedded+upserted {done}/{len(todo)}  ({time.time() - t0:.0f}s)")
    if expired_cal:
        delete_ids(sb, expired_cal, log)
    if prune and stale:
        delete_ids(sb, stale, log)
    if prune_legacy and legacy:
        delete_ids(sb, legacy, log)
    return stats
