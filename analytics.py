"""Instrumentation: anonymous-user event collection, storage, data health, audience overrides, and the metrics entry points.

Model
  * every browser gets a random anon_id (no account, no personal data, no IP) and a session_id; both ride on every event
  * the app (and this server, for chat results) emit events {event, props}; every event is stamped at ingestion with its audience
    (props._aud = user|internal|test|bot, props._why) and a dedupe id (props._eid). See audience.py.
  * events are buffered and written to the Supabase table `analytics_events` (supabase/analytics.sql). Reserved props keys start with "_"
    so no schema change was needed on the live table.
  * aggregation lives in insights.py (pure functions). This class loads a bounded, projected window, caches it for 60 s and hands it over.
  * pipeline reliability: buffer flushed every 5 s, drained on shutdown (FastAPI lifespan + atexit), counted drops, last-flush status,
    in-process duplicate suppression, and aggregation-time dedupe by _eid.
"""
import atexit
import json
import os
import re
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone

import audience as aud_mod

EVENT_RX = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
ID_RX = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
EID_RX = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
MAX_PROPS_BYTES = 2000          # unchanged: the richer chat_answered event is trimmed to fit (see _fit) instead of raising the limit
FLUSH_EVERY_S = 5
BUFFER_CAP = 5000
TABLE = "analytics_events"
OVERRIDES_TABLE = "analytics_audience_overrides"
MAX_ROWS = int(os.getenv("INSIGHTS_MAX_ROWS", "60000"))
CACHE_S = 60
_TRIM_ORDER = ("attempts", "n_att", "waited_ms", "rss_mb", "up_s", "fb_why", "retries")


def _fit(props: dict, limit: int = MAX_PROPS_BYTES) -> dict:
    """Keep the event under `limit` bytes without losing it: shorten the question, then drop optional detail, last resort a marker."""
    raw = json.dumps(props, default=str)
    if len(raw) <= limit:
        return props
    p = dict(props)
    if isinstance(p.get("query"), str):
        p["query"] = p["query"][:120]
    if isinstance(p.get("attempts"), list):
        p["attempts"] = p["attempts"][:3]
    for k in _TRIM_ORDER:
        if len(json.dumps(p, default=str)) <= limit:
            return p
        p.pop(k, None)
    if len(json.dumps(p, default=str)) <= limit:
        return p
    return {"truncated": True, "event_size": len(raw), **{k: v for k, v in props.items() if k.startswith("_")}}


class Analytics:
    def __init__(self, get_supabase, start_thread: bool = True):
        self._sb = get_supabase
        self._buf: deque = deque()
        self._ring: deque = deque(maxlen=5000)   # fallback store when the table is missing
        self._lock = threading.Lock()
        self._seen_eids: OrderedDict = OrderedDict()
        self.table_ok: bool | None = None
        self.last_error = ""
        self.counters = {"accepted": 0, "written": 0, "dropped": 0, "duplicates": 0, "flush_ok": 0, "flush_err": 0, "rejected": 0}
        self.last_flush_at: float | None = None
        self.last_flush_rows = 0
        self.last_event_at: float | None = None
        self.boot_at = time.time()
        self._raw_cache: dict = {}
        self._out_cache: dict = {}
        self._profile_cache: tuple | None = None
        self._override_cache: tuple | None = None
        self._mem_overrides: dict = {}
        self.overrides_ok: bool | None = None
        self.profiles_ok: bool | None = None
        if start_thread:
            threading.Thread(target=self._loop, daemon=True).start()
            atexit.register(self.flush_all)

    # ---- ingestion ------------------------------------------------------
    def record(self, event: str, anon_id: str, session_id: str = "", props: dict | None = None,
               path: str = "", ua: str = "", ts: str | None = None, flags: dict | None = None,
               eid: str | None = None, server_event: bool = False) -> bool:
        if os.getenv("ANALYTICS_DISABLED"):
            return False                         # ANALYTICS_DISABLED=1 keeps local test runs out of the real metrics
        if not EVENT_RX.match(event or "") or not ID_RX.match(anon_id or ""):
            self.counters["rejected"] += 1
            return False
        now = datetime.now(timezone.utc)
        stamp = now.isoformat()
        if ts:                                   # client clocks drift; accept a time within 24 h of ours, else use ours
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if abs((t - now).total_seconds()) <= 86400:
                    stamp = t.isoformat()
            except Exception:
                pass
        if eid:
            eid = eid if EID_RX.match(eid) else None
        eid = eid or ("s" + os.urandom(6).hex())
        with self._lock:
            if eid in self._seen_eids:
                self.counters["duplicates"] += 1
                return False
            self._seen_eids[eid] = 1
            if len(self._seen_eids) > 8000:
                for _ in range(2000):
                    self._seen_eids.popitem(last=False)
        props = _fit(props if isinstance(props, dict) else {})
        a, why = aud_mod.event_audience(anon_id, ua, flags)
        props = {**{k: v for k, v in props.items() if not str(k).startswith("_")}, "_aud": a, "_why": why, "_eid": eid}
        if server_event:
            props["_src"] = "server"
        row = {"ts": stamp, "anon_id": anon_id, "session_id": session_id if ID_RX.match(session_id or "") else None,
               "event": event, "props": props, "path": path[:200], "ua": ua[:200]}
        with self._lock:
            if len(self._buf) >= BUFFER_CAP:
                self._buf.popleft()
                self.counters["dropped"] += 1
            self._buf.append(row)
            self._ring.append(row)
            self.counters["accepted"] += 1
            self.last_event_at = time.time()
        return True

    # ---- storage --------------------------------------------------------
    def _loop(self):
        while True:
            time.sleep(FLUSH_EVERY_S)
            try:
                self.flush()
            except Exception as e:           # never let analytics take the server down
                self.last_error = str(e)[:200]

    def flush(self):
        with self._lock:
            rows = [self._buf.popleft() for _ in range(min(len(self._buf), 200))]
        if not rows:
            return
        if self.table_ok is False and time.time() % 300 > FLUSH_EVERY_S:
            return                           # table missing: retry only every ~5 minutes
        try:
            self._sb().table(TABLE).insert(rows).execute()
            self.table_ok, self.last_error = True, ""
            self.counters["flush_ok"] += 1
            self.counters["written"] += len(rows)
            self.last_flush_at, self.last_flush_rows = time.time(), len(rows)
        except Exception as e:
            msg = str(e)
            self.last_error = msg[:200]
            self.counters["flush_err"] += 1
            if "PGRST205" in msg or "does not exist" in msg or "Could not find the table" in msg:
                if self.table_ok is not False:
                    print(f"[analytics] table '{TABLE}' missing - run supabase/analytics.sql. Keeping events in memory.", flush=True)
                self.table_ok = False
            else:
                with self._lock:             # transient failure: put them back
                    self._buf.extendleft(reversed(rows))

    def flush_all(self, max_s: float = 4.0):
        """Drain the buffer (shutdown hook). Bounded so a dead database cannot stall a restart."""
        end = time.time() + max_s
        try:
            while self._buf and time.time() < end:
                n = len(self._buf)
                self.flush()
                if len(self._buf) >= n:
                    break
        except Exception:
            pass

    # ---- data health ------------------------------------------------------
    def health(self) -> dict:
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat() if t else None
        return {"table_ok": self.table_ok, "queued": len(self._buf), "last_error": self.last_error, **self.counters,
                "last_flush_at": iso(self.last_flush_at), "last_flush_rows": self.last_flush_rows,
                "last_event_at": iso(self.last_event_at), "boot_at": iso(self.boot_at),
                "overrides_ok": self.overrides_ok, "profiles_ok": self.profiles_ok, "max_rows": MAX_ROWS}

    # ---- audience overrides (owner managed; own table, deleting an override never touches events) ----
    def overrides(self) -> dict:
        c = self._override_cache
        if c and time.time() - c[0] < 30:
            return c[1]
        data = dict(self._mem_overrides)
        try:
            rows = self._sb().table(OVERRIDES_TABLE).select("anon_id,audience,note,created_at").execute().data or []
            data.update({r["anon_id"]: {"audience": r["audience"], "note": r.get("note") or "", "created_at": r.get("created_at")} for r in rows})
            self.overrides_ok = True
        except Exception as e:
            self.overrides_ok = False
            self.last_error = self.last_error or f"overrides: {str(e)[:120]}"
        self._override_cache = (time.time(), data)
        return data

    def set_override(self, anon_id: str, audience: str | None, note: str = "") -> dict:
        """audience None clears the override. Falls back to memory (lost on restart) if the table does not exist yet."""
        if not ID_RX.match(anon_id or ""):
            raise ValueError("bad anon_id")
        if audience is not None and audience not in aud_mod.AUDIENCES:
            raise ValueError("bad audience")
        persisted = True
        try:
            t = self._sb().table(OVERRIDES_TABLE)
            if audience is None:
                t.delete().eq("anon_id", anon_id).execute()
            else:
                t.upsert({"anon_id": anon_id, "audience": audience, "note": (note or "")[:200]}).execute()
            self.overrides_ok = True
            self._mem_overrides.pop(anon_id, None)
        except Exception as e:
            persisted = False
            self.overrides_ok = False
            self.last_error = f"overrides: {str(e)[:120]}"
            if audience is None:
                self._mem_overrides.pop(anon_id, None)
            else:
                self._mem_overrides[anon_id] = {"audience": audience, "note": (note or "")[:200], "created_at": None}
        self._override_cache = None
        self._out_cache.clear()
        return {"ok": True, "persisted": persisted}

    # ---- loading --------------------------------------------------------
    def _load(self, since_iso: str | None, cap: int = MAX_ROWS) -> tuple[list[dict], bool]:
        """Newest-first, projected, paginated. Returns (rows oldest-first, truncated). `truncated` means older rows were left out."""
        if self.table_ok is False:
            return [r for r in self._ring if not since_iso or r["ts"] >= since_iso], False
        rows, start, truncated = [], 0, False
        while True:
            q = self._sb().table(TABLE).select("ts,anon_id,session_id,event,props,ua")
            if since_iso:
                q = q.gte("ts", since_iso)
            page = q.order("ts", desc=True).range(start, start + 999).execute().data
            rows += page
            if len(page) < 1000:
                break
            start += 1000
            if len(rows) >= cap:
                truncated = True
                break
        rows.reverse()
        return rows, truncated

    def _profiles(self) -> list[dict] | None:
        """All-time per-anon_id profile via the SQL function (None if it is not installed: callers fall back to the loaded rows)."""
        c = self._profile_cache
        if c and time.time() - c[0] < CACHE_S:
            return c[1]
        out, start = [], 0
        try:
            while True:
                page = self._sb().rpc("analytics_anon_profile").range(start, start + 999).execute().data or []
                out += page
                if len(page) < 1000:
                    break
                start += 1000
            self.profiles_ok = True
        except Exception as e:
            self.profiles_ok = False
            self.last_error = self.last_error or f"profiles: {str(e)[:120]}"
            out = None
        self._profile_cache = (time.time(), out)
        return out

    # ---- metrics --------------------------------------------------------
    def summary(self, days: int = 7, selector: str = "users", now: datetime | None = None, force: bool = False) -> dict:
        """days: 1, 7, 30, 90 or 0 (= all time). selector: users | users_internal | test_bots | all."""
        import insights
        key = (days, selector)
        c = self._out_cache.get(key)
        if c and not force and time.time() - c[0] < CACHE_S:
            return c[1]
        t0 = time.time()
        self.flush()
        now = now or datetime.now(timezone.utc)
        # window plus the previous equal period (for deltas); all-time has no previous period
        since = None if days == 0 else (now - timedelta(days=days * 2)).isoformat()
        rc = self._raw_cache.get(days)
        if rc and not force and time.time() - rc[0] < CACHE_S:
            rows, truncated = rc[1], rc[2]
        else:
            rows, truncated = self._load(since)
            self._raw_cache[days] = (time.time(), rows, truncated)
        out = insights.build(rows, now, days, selector, self._profiles(), self.overrides(), truncated=truncated, health=self.health())
        out["compute_ms"] = round((time.time() - t0) * 1000)
        self._out_cache[key] = (time.time(), out)
        return out

    def raw_window(self, days: int):
        """(rows, truncated) used by the drill-down endpoints; shares the 60 s cache."""
        self.summary(days, "all")
        rc = self._raw_cache.get(days)
        return (rc[1], rc[2]) if rc else ([], False)

    def profiles(self):
        return self._profiles()

    def day(self, date_str: str, selector: str = "users") -> dict:
        """Hourly timeline of one Pacific date (loads just that day plus a day either side)."""
        import insights
        from datetime import date as _d
        d = _d.fromisoformat(date_str)
        lo = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) - timedelta(days=1)
        rows, _ = self._load(lo.isoformat(), cap=20000)
        return insights.timeline_for(rows, date_str, selector, self._profiles(), self.overrides())

    def inspect(self, key: str, days: int, selector: str) -> list:
        import insights
        rows, _ = self.raw_window(days)
        return insights.inspect_question(rows, key, selector, self._profiles(), self.overrides())

    def flagged(self, days: int, selector: str = "users", force: bool = False) -> dict:
        """Flagged & low-rated answers for the Quality tab (flagged.py). Bounded (<= flagged.MAX_ROWS rows) and cached for CACHE_S like the rest."""
        import flagged
        key = ("flagged", days, selector)
        c = self._out_cache.get(key)
        if c and not force and time.time() - c[0] < CACHE_S:
            return c[1]
        if force:
            self.summary(days, "all", force=True)            # reload the raw window too
        rows, truncated = self.raw_window(days)
        out = flagged.build(rows, datetime.now(timezone.utc), days, selector, self._profiles(), self.overrides())
        out["meta"]["truncated_source"] = truncated
        self._out_cache[key] = (time.time(), out)
        return out

    def heaviest(self, limit: int = 40) -> dict:
        import insights
        return {"ids": insights.heaviest(self._profiles(), self.overrides(), limit), "overrides_ok": self.overrides_ok, "profiles_ok": self.profiles_ok}

    def mark(self, short_id: str, audience: str | None, note: str = "") -> dict:
        import insights
        anon = insights.resolve_short_id(self._profiles(), short_id)
        if not anon:
            raise KeyError("unknown id")
        out = self.set_override(anon, audience, note)
        self._profile_cache = None
        return out
