"""Instrumentation: anonymous-user event collection and metrics.

Model
  * every browser gets a random anon_id (no account, no personal data) and a
    session_id; both ride on every event, so every action is tied to its user
  * the app (and this server, for chat results) emit events {event, props}
  * events are buffered and written to the Supabase table `analytics_events`
    (see supabase/analytics.sql). If the table does not exist yet they are kept
    in memory so nothing breaks, and /api/metrics says so.
"""
import json
import os
import re
import threading
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone

EVENT_RX = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
ID_RX = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_PROPS_BYTES = 2000
FLUSH_EVERY_S = 5
TABLE = "analytics_events"


def _norm_query(q: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", q.lower()).strip()[:160]


def _pct(values, p):
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(len(v) * p))])


class Analytics:
    def __init__(self, get_supabase):
        self._sb = get_supabase
        self._buf: deque = deque()
        self._ring: deque = deque(maxlen=5000)   # fallback store when the table is missing
        self._lock = threading.Lock()
        self.table_ok: bool | None = None
        self.last_error = ""
        self._summary_cache: tuple[float, dict] | None = None
        threading.Thread(target=self._loop, daemon=True).start()

    # ---- ingestion ------------------------------------------------------
    def record(self, event: str, anon_id: str, session_id: str = "", props: dict | None = None,
               path: str = "", ua: str = "", ts: str | None = None) -> bool:
        if os.getenv("ANALYTICS_DISABLED") or not EVENT_RX.match(event or "") or not ID_RX.match(anon_id or ""):
            return False                         # ANALYTICS_DISABLED=1 keeps local test runs out of the real metrics
        props = props or {}
        raw = json.dumps(props, default=str)
        if len(raw) > MAX_PROPS_BYTES:
            props = {"truncated": True, "event_size": len(raw)}
        row = {"ts": ts or datetime.now(timezone.utc).isoformat(), "anon_id": anon_id,
               "session_id": session_id if ID_RX.match(session_id or "") else None,
               "event": event, "props": props, "path": path[:200], "ua": ua[:200]}
        with self._lock:
            self._buf.append(row)
            self._ring.append(row)
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
        except Exception as e:
            msg = str(e)
            self.last_error = msg[:200]
            if "PGRST205" in msg or "does not exist" in msg or "Could not find the table" in msg:
                if self.table_ok is not False:
                    print(f"[analytics] table '{TABLE}' missing - run supabase/analytics.sql. Keeping events in memory.", flush=True)
                self.table_ok = False
            else:
                with self._lock:             # transient failure: put them back
                    self._buf.extendleft(reversed(rows))

    def _load(self, days: int, cap: int = 20000) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        if self.table_ok is False:
            return [r for r in self._ring if r["ts"] >= since]
        rows, start = [], 0
        while len(rows) < cap:
            page = self._sb().table(TABLE).select("ts,anon_id,session_id,event,props,path") \
                .gte("ts", since).order("ts").range(start, start + 999).execute().data
            rows += page
            if len(page) < 1000:
                break
            start += 1000
        return rows

    # ---- metrics --------------------------------------------------------
    def summary(self, days: int = 14) -> dict:
        if self._summary_cache and time.time() - self._summary_cache[0] < 60 and self._summary_cache[1].get("days") == days:
            return self._summary_cache[1]
        self.flush()
        rows = self._load(days)
        users, sessions = set(), set()
        per_day = defaultdict(lambda: {"users": set(), "chats": 0})
        ev_counts, tab_views, prompts, unanswered = Counter(), Counter(), Counter(), Counter()
        resource_clicks, cal_filters = Counter(), Counter()
        lat_total, lat_ttft, chat_n, cache_hits, errors = [], [], 0, 0, 0
        thumbs = Counter()
        feedback_msgs, not_helpful = [], Counter()
        user_days = defaultdict(set)
        devices, returning, first_seen = Counter(), set(), set()
        for r in rows:
            e, p = r["event"], r.get("props") or {}
            day = r["ts"][:10]
            users.add(r["anon_id"])
            if r.get("session_id"):
                sessions.add(r["session_id"])
            per_day[day]["users"].add(r["anon_id"])
            user_days[r["anon_id"]].add(day)
            ev_counts[e] += 1
            if e == "chat_answered":
                chat_n += 1
                per_day[day]["chats"] += 1
                cache_hits += bool(p.get("cached"))
                if p.get("total_ms"):
                    lat_total.append(p["total_ms"])
                if p.get("ttft_ms"):
                    lat_ttft.append(p["ttft_ms"])
                q = _norm_query(p.get("query", ""))
                if q:
                    prompts[q] += 1
                    if p.get("no_context") or p.get("fallback"):
                        unanswered[q] += 1
            elif e == "chat_error":
                errors += 1
            elif e == "tab_view":
                tab_views[p.get("tab", "?")] += 1
            elif e == "feedback":
                thumbs[p.get("value", "?")] += 1
                if p.get("value") == "down" and p.get("query"):
                    not_helpful[_norm_query(p["query"])] += 1
            elif e == "feedback_text":
                feedback_msgs.append({"ts": r["ts"][:16].replace("T", " "), "kind": p.get("kind", ""), "text": str(p.get("text", ""))[:600],
                                      "tab": p.get("tab", "")})
            elif e == "resource_click":
                resource_clicks[p.get("label", "?")] += 1
            elif e in ("calendar_filter", "calendar_search"):
                cal_filters[str(p.get("category") or p.get("q") or "?")] += 1
            elif e == "session_start":
                devices[p.get("device", "?")] += 1
                (returning if p.get("returning") else first_seen).add(r["anon_id"])
        repeat_users = sum(1 for d in user_days.values() if len(d) >= 2)
        out = {
            "days": days, "storage": "supabase" if self.table_ok else "memory (run supabase/analytics.sql)" if self.table_ok is False else "supabase (unconfirmed)",
            "events_total": len(rows), "unique_users": len(users), "sessions": len(sessions),
            "chats": chat_n, "chat_errors": errors,
            "cache_hit_rate": round(cache_hits / chat_n, 3) if chat_n else None,
            "latency_ms": {"total_p50": _pct(lat_total, .5), "total_p95": _pct(lat_total, .95),
                           "first_token_p50": _pct(lat_ttft, .5), "first_token_p95": _pct(lat_ttft, .95)},
            "feedback": dict(thumbs), "feedback_messages": feedback_msgs[-30:][::-1],
            "not_helpful_questions": not_helpful.most_common(10), "repeat_users": repeat_users,
            "new_users": len(first_seen), "returning_users": len(returning), "devices": dict(devices),
            "tab_views": dict(tab_views), "events_by_type": dict(ev_counts.most_common(25)),
            "top_questions": prompts.most_common(15), "unanswered_questions": unanswered.most_common(10),
            "resource_clicks": resource_clicks.most_common(10), "calendar_filters": cal_filters.most_common(10),
            "daily": [{"day": d, "users": len(v["users"]), "chats": v["chats"]} for d, v in sorted(per_day.items())],
            "buffer": {"queued": len(self._buf), "last_error": self.last_error},
        }
        self._summary_cache = (time.time(), out)
        return out
