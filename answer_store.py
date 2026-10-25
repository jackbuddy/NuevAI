"""Persistent (L2) answer cache in Supabase table `answer_cache` (see supabase/answer_cache.sql).

L1 is the in-process TTLCache in query_server.py; this survives Render restarts. Design rules:
  * key = normalized question text only; TTL is honoured through expires_at (reads ignore expired rows)
  * no personal data: no anon/session ids; questions that contain an email, a long digit run or are very long are never stored
  * never blocks a visitor: reads have a short timeout, writes happen on a background thread, and any failure (table missing,
    Supabase slow or down) trips a circuit breaker so the server silently runs on L1 only for a while
"""
import queue
import re
import threading
import time
from datetime import datetime, timedelta, timezone

TABLE = "answer_cache"
READ_TIMEOUT_S = 2.0
BREAKER_S = 120
PERSONAL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\d{6,}|\b\d{3}[-. ]\d{3,4}[-. ]\d{4}\b")


def norm_key(q: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", q.lower())).strip()[:160]


def storable(question: str) -> bool:
    return 3 <= len(question) <= 200 and not PERSONAL.search(question)


class AnswerStore:
    def __init__(self, get_supabase, enabled: bool = True):
        self._sb = get_supabase
        self.enabled = enabled
        self.down_until = 0.0
        self.last_error = ""
        self.stats = {"l2_hits": 0, "l2_misses": 0, "l2_writes": 0, "l2_errors": 0}
        self._q: queue.Queue = queue.Queue(maxsize=200)
        self._started = False
        self._lock = threading.Lock()

    # ---- breaker -------------------------------------------------------------
    def _usable(self) -> bool:
        return self.enabled and time.time() >= self.down_until

    def _trip(self, e: Exception):
        self.last_error = f"{type(e).__name__}: {str(e)[:120]}"
        self.stats["l2_errors"] += 1
        self.down_until = time.time() + BREAKER_S
        print(f"answer_cache L2 off for {BREAKER_S}s ({self.last_error})", flush=True)

    # ---- read ------------------------------------------------------------------
    def get(self, question: str) -> dict | None:
        if not self._usable() or not storable(question):
            return None
        out: dict = {}

        def run():
            try:
                now = datetime.now(timezone.utc).isoformat()
                r = (self._sb().table(TABLE).select("answer,model_used,sources,suggestions,date_bound")
                     .eq("key", norm_key(question)).gt("expires_at", now).limit(1).execute())
                out["rows"] = r.data or []
            except Exception as e:
                out["err"] = e

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(READ_TIMEOUT_S)
        if t.is_alive():
            self._trip(TimeoutError("L2 read slow"))
            return None
        if "err" in out:
            self._trip(out["err"])
            return None
        if not out.get("rows"):
            self.stats["l2_misses"] += 1
            return None
        row = out["rows"][0]
        self.stats["l2_hits"] += 1
        return {"answer": row["answer"], "model_used": row.get("model_used") or "cache", "sources": row.get("sources") or [],
                "suggestions": row.get("suggestions") or [], "date_bound": bool(row.get("date_bound"))}

    # ---- write (background) -------------------------------------------------------
    def put(self, question: str, payload: dict, ttl: float, date_bound: bool = False):
        if not self._usable() or not storable(question) or not payload.get("answer") or not payload.get("sources"):
            return
        row = {"key": norm_key(question), "answer": payload["answer"], "model_used": payload.get("model_used"),
               "sources": payload.get("sources") or [], "suggestions": payload.get("suggestions") or [], "date_bound": date_bound,
               "created_at": datetime.now(timezone.utc).isoformat(),
               "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()}
        self._start()
        try:
            self._q.put_nowait(row)
        except queue.Full:
            pass

    def _start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            row = self._q.get()
            if not self._usable():
                continue
            try:
                self._sb().table(TABLE).upsert(row, on_conflict="key").execute()
                self.stats["l2_writes"] += 1
            except Exception as e:
                self._trip(e)

    def flush(self, timeout: float = 3.0):
        """Tests: wait until queued writes are processed."""
        end = time.time() + timeout
        while not self._q.empty() and time.time() < end:
            time.sleep(0.02)
        time.sleep(0.05)
