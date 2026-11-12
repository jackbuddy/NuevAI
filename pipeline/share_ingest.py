"""Google Sheets / Docs shared with the ingest account -> knowledge base, incrementally, on the existing email-ingest GitHub Action.

Every 30 minutes (workflow `email-ingest.yml`, second step) this module:

  1. discovery    Drive files.list `sharedWithMe` for Sheets and Docs, plus the Sheets/Docs inside shared FOLDERS (recursively)
  2. manifest     `share_ingest_manifest` (Supabase; the runner is ephemeral): one row per file, keyed by a hash of the Drive file id, holding
                  modifiedTime + version of the last successful ingest, status and the bounded retry counter. A file whose modifiedTime and
                  version are unchanged is skipped without being fetched.
  3. processing   newest-changed first, time-boxed (SHARE_INGEST_BUDGET_SECONDS, default 150): what does not fit waits for the next run
                  Sheets: spreadsheets.get with grid data -> pipeline/gsheets.py (row-aligned chunks + dated event records)
                  Docs:   Drive export as HTML -> the pipeline's own html_to_markdown -> section chunks (+ the schedule summary/events)
  4. replace      all chunks of one file share the id prefix `share-<file hash>-`. An edit replaces EVERY chunk of the file: changed chunks are
                  upserted first, then ids that are no longer produced (rows removed from the sheet) are deleted, and only ids with that exact
                  prefix can ever be deleted. Nothing else in the table is touched.
  5. unshared     a file that is no longer listed (unshared / deleted / trashed) for 2 complete runs in a row keeps its chunks but they are flagged
                  metadata.superseded=true (retrieval ignores them) and the manifest says `unshared`. Nothing is deleted without scripts/share_remove.py.
  6. failures     a file that cannot be processed is logged with a reason (no contents, no addresses), counted in the manifest, retried up to
                  MAX_ATTEMPTS times (like the email ingest) and then marked `failed` until it changes. Only systemic problems (credentials,
                  Google API disabled, Drive listing down, database unreachable) make the run exit non-zero.

Share rows are NOT part of run_pipeline's plan: `freshness.is_share_row` exempts them from every prune / superseded / orphan routine.
"""
import hashlib
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import freshness   # noqa: E402
import guard       # noqa: E402
import settings    # noqa: E402

from . import chunker, gapi, gsheets, schedule_text as st   # noqa: E402

LOG_TABLE = "share_ingest_log"
MANIFEST_TABLE = "share_ingest_manifest"
MAX_ATTEMPTS = 3
UNSHARED_AFTER_RUNS = 2
DEFAULT_BUDGET_SECONDS = 150
MAX_DOC_CHARS = 120_000
PREFIX = freshness.SHARE_ID_PREFIX
STATUSES = ("indexed", "needs_review", "skipped", "error", "failed", "unshared", "removed")


def fhash(file_id: str) -> str:
    return gsheets.file_hash(file_id)


def own_prefix(fh: str) -> str:
    return f"{PREFIX}{fh}-"


def owner_domain(f: dict) -> str:
    for o in f.get("owners") or []:
        a = (o or {}).get("emailAddress") or ""
        if "@" in a:
            return a.rsplit("@", 1)[-1].lower()
    return ""


def file_meta(f: dict) -> dict:
    return {"file_id": f["id"], "name": re.sub(r"\s+", " ", f.get("name") or "").strip()[:200], "modified_time": f.get("modifiedTime", ""),
            "version": str(f.get("version", "")), "owner_domain": owner_domain(f), "mime_type": f.get("mimeType", ""),
            "source_type": "sheet" if f.get("mimeType") == gapi.MIME_SHEET else "doc"}


# ============================================================================================ documents
def analyze_doc(html: str, meta: dict, now: datetime | None = None) -> gsheets.Analysis:
    """A Google Doc (HTML export) -> chunks. Plain-text path of the existing pipeline (html_to_markdown + heading-aware sections), plus a
    schedule summary on top and event records when the doc holds practice times or a game list."""
    from bs4 import BeautifulSoup
    from .common import html_to_markdown
    now = now or datetime.now().astimezone()
    a = gsheets.Analysis()
    name = meta.get("name") or "Untitled document"
    fh = fhash(meta["file_id"])
    md = freshness.parse_day(meta.get("modified_time"))
    ref = date.fromisoformat(md) if md else now.date()
    if gsheets.STRICT_TITLE.search(name):
        a.status, a.reason = "needs_review", f"file name looks like people data ('{name[:40]}'): not indexed"
        return a
    soup = BeautifulSoup(html, "html.parser")
    for t in soup.select("style, script"):
        t.decompose()
    text = html_to_markdown(soup.body or soup)[:MAX_DOC_CHARS]
    text = chunker.clean_text(text)
    if len(text.strip()) < 40:
        a.status, a.reason = "skipped", "empty document"
        return a
    why = guard.student_data_reason(name + "\n" + text)
    if why:
        a.status, a.reason = "needs_review", f"looks like student-level data: {why}"
        return a
    text, _ = guard.redact_contacts(text)
    text = guard.scrub_context(text).strip()
    ctx = st.derive_context(name, "", text[:600], ref)
    ctx["title"] = name
    slots, games = st.find_practice_slots(text, ref), st.find_games(text, ref, name)
    summary = st.schedule_summary(slots, games)
    label = f'shared Google Doc "{name[:80]}", updated {freshness.day_label(ref.isoformat())}'
    bm = gsheets.base_meta(meta, fh, now, ctx, ref.isoformat(), "share_doc", "doc")
    bm["kind"] = "doc"
    pieces = []
    for trail, body in chunker.sections(chunker.promote_record_titles(text), name):
        pieces += _pack_section(trail, body)
    if not pieces:
        pieces = [(name, text)]
    if summary:
        pieces.insert(0, (name, summary))
    for n, (trail, piece) in enumerate(pieces[:gsheets.MAX_CHUNKS_PER_FILE]):
        head = f"Title: {name}\n" + (f"Section: {trail}\n" if trail != name else "") + f"Source: {label}\n\n"
        content = head + piece
        a.chunks.append({"chunk_id": f"{own_prefix(fh)}d-{n:03d}", "content": content, "source_file": f"share://{fh}",
                         "metadata": {**bm, "content_hash": hashlib.sha1(content.encode()).hexdigest()}})
    if len(pieces) > gsheets.MAX_CHUNKS_PER_FILE:
        a.truncated = True
        a.notes.append(f"truncated to {gsheets.MAX_CHUNKS_PER_FILE} chunks")
    evs = games + st.practice_events(slots)
    used: set[str] = set()
    win = (now.date() - timedelta(days=45), now.date() + timedelta(days=430))
    for e in evs:
        if not (win[0] <= e["date"] <= win[1]):
            continue
        ev_meta = {k: v for k, v in bm.items() if k not in ("source", "source_url", "title", "kind")}
        ev_meta["share_event"] = True
        a.chunks.append(st.event_chunk(e, ctx, id_prefix=f"{own_prefix(fh)}e", source_url=f"share://{fh}", source_label=label, extra_meta=ev_meta, used_ids=used))
        a.events += 1
    a.tables = 1
    a.team = ctx["team"]
    a.reason = f"{len(pieces)} chunk(s); {a.events} event record(s)" + ("; schedule summary added" if summary else "")
    return a


def _pack_section(trail: str, body: str) -> list[tuple[str, str]]:
    """Section text -> chunks. Pipe tables (rows '| a | b |') are cut between rows with their first row repeated; prose is packed on line boundaries."""
    out, prose, table = [], [], []

    def flush_prose():
        if prose:
            out.extend((trail, p) for p in chunker.pack("\n".join(prose)))
            prose.clear()

    def flush_table():
        if table:
            header, rows = (table[0], table[1:]) if len(table) > 1 and len(table[0]) < 400 else ("", table)
            for lo, hi in gsheets.chunk_rows(len(trail) + 40, header, rows):
                out.append((trail, "\n".join(([header] if header else []) + rows[lo:hi])))
            table.clear()

    for line in body.splitlines():
        if line.lstrip().startswith("|"):
            flush_prose()
            table.append(line)
        else:
            flush_table()
            prose.append(line)
    flush_table()
    flush_prose()
    return [(t, p) for t, p in out if p.strip()]


# ============================================================================================ storage
class ShareStore:
    """Chunks (document_chunks_v2), the manifest and the review log. The manifest degrades to a read-only view derived from the chunks'
    own metadata when its table does not exist yet; the log degrades to stdout."""

    def __init__(self, sb=None, embed_upsert=None, out=print, now=None):
        if sb is None:
            from .index import client
            sb = client()
        if embed_upsert is None:
            from .index import _embed_and_upsert as embed_upsert
        self.sb, self._embed_upsert, self.out = sb, embed_upsert, out
        self.manifest_ok = True
        self.log_ok = True
        self.now = now or (lambda: datetime.now(timezone.utc))

    # -- chunks
    def ids_for_file(self, fh: str) -> dict:
        rows, start = {}, 0
        while True:
            r = self.sb.table(settings.CHUNKS_TABLE).select("chunk_id,h:metadata->>content_hash").like("chunk_id", own_prefix(fh) + "%") \
                .range(start, start + 999).execute().data or []
            rows.update({x["chunk_id"]: x.get("h") for x in r})
            if len(r) < 1000:
                return rows
            start += 1000

    def upsert(self, chunks: list[dict]) -> None:
        for i in range(0, len(chunks), 4):
            self._embed_upsert(self.sb, chunks[i:i + 4])

    def delete_ids(self, ids: list[str], fh: str) -> int:
        """Only ids under this file's own prefix can be deleted."""
        pre = own_prefix(fh)
        ids = [i for i in ids if i.startswith(pre)]
        for i in range(0, len(ids), 50):
            self.sb.table(settings.CHUNKS_TABLE).delete().in_("chunk_id", ids[i:i + 50]).execute()
        return len(ids)

    def flag_superseded(self, fh: str, reason: str) -> int:
        n = 0
        rows = self.sb.table(settings.CHUNKS_TABLE).select("chunk_id,metadata").like("chunk_id", own_prefix(fh) + "%").execute().data or []
        for r in rows:
            m = r.get("metadata") or {}
            if r["chunk_id"].startswith(own_prefix(fh)) and not m.get("superseded"):
                self.sb.table(settings.CHUNKS_TABLE).update({"metadata": {**m, "superseded": True, "superseded_reason": reason[:100],
                                                                          "superseded_at": self.now().date().isoformat()}}).eq("chunk_id", r["chunk_id"]).execute()
                n += 1
        return n

    def unflag(self, fh: str) -> int:
        n = 0
        rows = self.sb.table(settings.CHUNKS_TABLE).select("chunk_id,metadata").like("chunk_id", own_prefix(fh) + "%").execute().data or []
        for r in rows:
            m = dict(r.get("metadata") or {})
            if r["chunk_id"].startswith(own_prefix(fh)) and m.get("superseded") and m.get("superseded_reason") and "flagged_at" not in m:
                for k in ("superseded", "superseded_reason", "superseded_at"):
                    m.pop(k, None)
                self.sb.table(settings.CHUNKS_TABLE).update({"metadata": m}).eq("chunk_id", r["chunk_id"]).execute()
                n += 1
        return n

    # -- manifest
    def manifest(self) -> dict:
        try:
            rows, start = [], 0
            while True:
                r = self.sb.table(MANIFEST_TABLE).select("*").range(start, start + 999).execute().data or []
                rows += r
                if len(r) < 1000:
                    break
                start += 1000
            return {x["file_hash"]: x for x in rows}
        except Exception:
            self.manifest_ok = False
            self.out(f"note: table {MANIFEST_TABLE} is not available (supabase/share_ingest.sql); falling back to the chunks' own metadata (retry counters are not kept)")
            return self._manifest_from_chunks()

    def _manifest_from_chunks(self) -> dict:
        out, start = {}, 0
        while True:
            r = self.sb.table(settings.CHUNKS_TABLE).select("chunk_id,m:metadata").like("chunk_id", PREFIX + "%").range(start, start + 999).execute().data or []
            for x in r:
                m = x.get("m") or {}
                fh = m.get("share_file_hash")
                if fh and fh not in out:
                    out[fh] = {"file_hash": fh, "status": "unshared" if m.get("superseded") else "indexed", "modified_time": m.get("modified_time", ""),
                               "version": m.get("drive_version", ""), "attempts": 0, "missing_runs": 0}
            if len(r) < 1000:
                return out
            start += 1000

    def save_manifest(self, row: dict) -> None:
        if not self.manifest_ok:
            return
        row = {**row, "updated_at": self.now().isoformat()}
        try:
            self.sb.table(MANIFEST_TABLE).upsert(row, on_conflict="file_hash").execute()
        except Exception:
            self.manifest_ok = False
            self.out(f"note: could not write {MANIFEST_TABLE}; continuing without it")

    # -- log
    def log(self, row: dict) -> None:
        row = {k: (str(v)[:300] if isinstance(v, str) and k == "reason" else v) for k, v in row.items()}
        if self.log_ok:
            try:
                self.sb.table(LOG_TABLE).insert(row).execute()
                return
            except Exception:
                self.log_ok = False
                self.out(f"note: table {LOG_TABLE} is not available (see supabase/share_ingest.sql); logging to stdout only")


# ============================================================================================ replace
def replace_file(store: ShareStore, fh: str, chunks: list[dict], was_unshared: bool = False) -> dict:
    """Make the file's chunks in the table equal `chunks`: new/changed ones are upserted (unchanged ones are not even re-embedded), then every
    id under this file's prefix that is no longer produced is deleted. Returns counts. Upsert comes first, so an interrupted run never loses data."""
    new = {c["chunk_id"]: c for c in chunks}
    bad = [i for i in new if not i.startswith(own_prefix(fh))]
    if bad:
        raise ValueError("a chunk id is outside the file's own prefix")
    have = store.ids_for_file(fh)
    changed = [c for i, c in new.items() if have.get(i) != c["metadata"]["content_hash"]]
    store.upsert(changed)
    if was_unshared:
        store.unflag(fh)
    stale = [i for i in have if i not in new]
    deleted = store.delete_ids(stale, fh)
    return {"upserted": len(changed), "unchanged": len(new) - len(changed), "deleted": deleted, "created": sum(1 for c in changed if c["chunk_id"] not in have)}


# ============================================================================================ run
@dataclass
class FileResult:
    status: str
    reason: str = ""
    chunks: int = 0
    events: int = 0
    detail: dict = field(default_factory=dict)


def should_process(prev: dict | None, f: dict, force: bool = False) -> tuple[bool, str]:
    if force:
        return True, "forced"
    if not prev:
        return True, "new file"
    same = prev.get("modified_time") and freshness.parse_day(prev.get("modified_time")) is not None and _same_time(prev.get("modified_time"), f.get("modifiedTime")) \
        and str(prev.get("version") or "") == str(f.get("version") or "")
    st_ = prev.get("status")
    if st_ == "unshared":
        return True, "shared again"
    if not same:
        return True, "changed since the last ingest"
    if st_ == "error" and int(prev.get("attempts") or 0) < MAX_ATTEMPTS:
        return True, "retry"
    return False, st_ or "unchanged"


def _same_time(a, b) -> bool:
    def p(x):
        try:
            return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        except ValueError:
            return None
    pa, pb = p(a), p(b)
    return pa is not None and pb is not None and pa == pb


def process_file(client: gapi.GoogleClient, store: ShareStore | None, f: dict, now: datetime, dry_run: bool, prev: dict | None) -> FileResult:
    meta = file_meta(f)
    fh = fhash(f["id"])
    warnings: list[str] = []
    if meta["source_type"] == "sheet":
        book, warnings = client.get_spreadsheet(f["id"])
        a = gsheets.analyze_workbook(book, meta, now)
    else:
        a = analyze_doc(client.export_doc_html(f["id"]), meta, now)
    reason = "; ".join([a.reason] + warnings)[:300] if a.reason else "; ".join(warnings)[:300]
    if a.status != "indexed" or dry_run or store is None:
        return FileResult(a.status, reason, a.chunk_count, a.events, {"analysis": a})
    stats = replace_file(store, fh, a.chunks, was_unshared=bool(prev and prev.get("status") == "unshared"))
    bits = [f"{stats['created']} new, {stats['upserted'] - stats['created']} changed, {stats['unchanged']} unchanged, {stats['deleted']} removed chunk(s)"]
    return FileResult("indexed", "; ".join(bits + ([reason] if reason else []))[:300], a.chunk_count, a.events, {"stats": stats, "analysis": a})


def manifest_row(f: dict, status: str, res: FileResult | None, prev: dict | None, attempts: int, now: datetime) -> dict:
    meta = file_meta(f)
    row = {"file_hash": fhash(f["id"]), "source_type": meta["source_type"], "name": meta["name"][:120], "mime_type": meta["mime_type"],
           "modified_time": meta["modified_time"] or None, "version": meta["version"], "status": status, "attempts": attempts,
           "missing_runs": 0, "owner_domain": meta["owner_domain"], "reason": (res.reason if res else "")[:300]}
    if res is not None:
        row.update({"chunk_count": res.chunks, "event_count": res.events})
    if status == "indexed":
        row["last_ingested_at"] = now.isoformat()
    return row


def run(cfg: gapi.GoogleConfig, store: ShareStore | None, dry_run: bool = False, limit: int | None = None, out=print, show_names: bool = False,
        file_id: str | None = None, force: bool = False, client: gapi.GoogleClient | None = None, budget: float | None = None,
        clock=time.monotonic, now: datetime | None = None) -> int:
    """One pass over the Drive files shared with the account. Returns a process exit code (1 only for systemic failures)."""
    import os
    budget = float(os.getenv("SHARE_INGEST_BUDGET_SECONDS", DEFAULT_BUDGET_SECONDS)) if budget is None else budget
    t0 = clock()
    now = now or datetime.now(timezone.utc)
    client = client or gapi.GoogleClient(cfg)
    try:
        client.access_token()
        files, warnings = client.discover()
    except gapi.GoogleAuthError as e:
        out(f"GOOGLE AUTH FAILURE: {e}")
        return 1
    except gapi.GoogleApiError as e:
        out(f"Google Drive listing failed ({e}); nothing was processed this run.")
        return 1
    for w in warnings:
        out(f"  warning: {w}")
    try:
        man = store.manifest() if store is not None else {}
    except Exception as e:
        out(f"cannot read the manifest ({type(e).__name__}); is Supabase reachable?")
        return 1
    by_hash = {fhash(f["id"]): f for f in files}
    complete = not any("could not be listed" in w or "stopped at" in w for w in warnings)
    out(f"{len(files)} shared file(s) found (Sheets/Docs, including shared folders)")
    todo, counts = [], {}
    only = fhash(file_id) if file_id else None
    for fh, f in by_hash.items():
        if only and fh != only:
            continue
        go, why = should_process(man.get(fh), f, force or bool(only))
        if go:
            todo.append((f, why))
        else:
            counts["unchanged"] = counts.get("unchanged", 0) + 1
    if only and not todo:
        out("that file id is not shared with the account (or is not a Sheet/Doc)")
        return 1
    todo.sort(key=lambda x: x[0].get("modifiedTime", ""), reverse=True)        # newest-changed first
    if limit:
        todo = todo[:limit]
    errors_systemic, deferred, handled = 0, 0, set()
    for k, (f, why) in enumerate(todo):
        if clock() - t0 > budget:
            deferred = len(todo) - k
            out(f"time budget of {budget:.0f}s reached: {deferred} file(s) wait for the next run")
            break
        meta, fh = file_meta(f), fhash(f["id"])
        prev = man.get(fh)
        handled.add(fh)
        try:
            res = process_file(client, store, f, now, dry_run, prev)
        except gapi.GoogleAuthError as e:
            out(f"GOOGLE AUTH FAILURE: {e}")
            return 1
        except Exception as e:                                          # one bad file never stops the run
            attempts = (int(prev.get("attempts") or 0) if prev and _same_time(prev.get("modified_time"), f.get("modifiedTime")) else 0) + 1
            status = "failed" if attempts >= MAX_ATTEMPTS else "error"
            kind = f"Google API {getattr(e, 'status', '')}".strip() if isinstance(e, gapi.GoogleApiError) else type(e).__name__
            res = FileResult(status, f"{kind} while processing (attempt {attempts} of {MAX_ATTEMPTS})")
            if not isinstance(e, (gapi.GoogleApiError, ValueError, KeyError, TypeError, AttributeError, IndexError)):
                errors_systemic += 1                                    # e.g. the database is unreachable
            _record(store, f, meta, fh, status, res, prev, attempts, now, dry_run, out, show_names, why)
            counts[status] = counts.get(status, 0) + 1
            continue
        status = {"indexed": "indexed", "needs_review": "needs_review", "skipped": "skipped"}[res.status]
        counts[status] = counts.get(status, 0) + 1
        _record(store, f, meta, fh, status, res, prev, 0, now, dry_run, out, show_names, why)
    # files that vanished from the listing (unshared / deleted / trashed): flag, never delete
    if store is not None and not dry_run and not only:
        if not complete:
            out("  (listing incomplete: no file was treated as unshared this run)")
        elif not files and any(r.get("status") == "indexed" for r in man.values()):
            out("  (the listing came back empty: not treating every file as unshared)")
        else:
            for fh, row in man.items():
                if fh in by_hash:
                    if int(row.get("missing_runs") or 0) and fh not in handled:
                        store.save_manifest({**{k: v for k, v in row.items() if k != "updated_at"}, "missing_runs": 0})
                    continue
                if row.get("status") in ("unshared", "removed"):
                    continue
                missing = int(row.get("missing_runs") or 0) + 1
                base = {k: v for k, v in row.items() if k != "updated_at"}
                if missing >= UNSHARED_AFTER_RUNS:
                    n = store.flag_superseded(fh, "file no longer shared with the ingest account")
                    store.save_manifest({**base, "status": "unshared", "missing_runs": missing})
                    store.log({"source_type": row.get("source_type") or "sheet", "file_hash": fh, "name": row.get("name"), "status": "unshared", "chunk_count": n,
                               "event_count": 0, "modified_time": row.get("modified_time"), "reason": f"no longer visible to the account; {n} chunk(s) flagged superseded (kept)", "attempts": 0})
                    out(f"  unshared [{fh[:8]}]: {n} chunk(s) flagged superseded, nothing deleted")
                    counts["unshared"] = counts.get("unshared", 0) + 1
                else:
                    store.save_manifest({**base, "missing_runs": missing})
    if counts:
        out("summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) + (f", deferred={deferred}" if deferred else "") + f"  ({client.calls} API calls, {clock() - t0:.0f}s)")
    else:
        out("nothing to do")
    return 1 if errors_systemic else 0


def _record(store, f, meta, fh, status, res, prev, attempts, now, dry_run, out, show_names, why):
    line = f"  {status}: {meta['source_type']} [{fh[:8]}]" + (f' "{meta["name"][:60]}"' if show_names else "") + \
           (f" chunks={res.chunks} events={res.events}" if res.chunks else "") + (f" ({res.reason})" if res.reason else "") + (" [dry run]" if dry_run else "")
    out(line)
    if dry_run or store is None:
        return
    store.log({"source_type": meta["source_type"], "file_hash": fh, "name": meta["name"][:120], "status": status, "chunk_count": res.chunks,
               "event_count": res.events, "modified_time": meta["modified_time"] or None, "reason": (f"{why}: " + res.reason)[:300], "attempts": attempts,
               "owner_domain": meta["owner_domain"]})
    store.save_manifest(manifest_row(f, "indexed" if status == "indexed" else status, res, prev, attempts, now))
