"""Retrieval for NuevAI.

For each question, in parallel:
  1. embed (cached) -> vector search over document_chunks (wide: 24 candidates)
  2. structured calendar lookup when the question has a date / sport / event signal
Then a small cross-encoder reranks the vector candidates, and everything is
shaped into a compact, correctly ordered context block for the model.
"""
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from cache import TTLCache
from intent import CalendarIntent, parse_intent

EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-large-en-v1.5")
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
USE_RERANK = os.getenv("RERANK", "1") != "0"
CANDIDATES = 24
VECTOR_THRESHOLD = 0.30
LIST_WORDS = re.compile(r"\b(list|all|every|which|what (courses?|classes|electives?|sports|clubs|departments?)|offered|options|available)\b", re.I)

LINK_INTENTS = [
    ("booking", re.compile(r"\b(book|booking|schedule|reserve|register|registration|sign[- ]?up|rsvp|tour|visit|open house|info(rmation)? session|admissions event)\b", re.I)),
    ("payment", re.compile(r"\b(donat\w*|giv(e|ing)|gift|pay|payment|tuition|store|buy|merch\w*|spirit ?wear)\b", re.I)),
]

# Never served, even if such rows exist in the database: sheets/forms that hold people's data.
BLOCKED_TITLE = re.compile(r"diploma|rsvp|sign[- ]?up|roster|responses|attendance sheet|transcript|salary|payroll|medical", re.I)

# Everyday wording -> the words the handbook actually uses. Added to the search text only (never shown).
SYNONYMS = {
    r"\blate\b|\bleft early\b": "tardy tardies attendance",
    r"\babsent\b|\bmiss(ing)? school\b|\bsick\b": "absence absences attendance illness",
    r"\bphones?\b|\bairpods?\b|\bearbuds?\b": "cell phone policy devices",
    r"\bai\b|\bchatgpt\b|\bgpt\b": "artificial intelligence generative AI academic integrity",
    r"\bcheat(ing)?\b|\bcopy(ing)?\b": "plagiarism academic integrity honor",
    r"\bhomework\b": "homework assignments late work",
    r"\bgrad(e|es|ing)\b|\bgpa\b|\breport cards?\b": "grading grades assessment reporting",
    r"\bbus(es)?\b|\btransport\w*\b|\bshuttle\b": "transportation shuttle bus",
    r"\blunch\b|\bfood\b|\bcafeteria\b|\bcafe\b": "lunch cafe meals dining",
    r"\bpark(ing)?\b|\bdrop[- ]?off\b|\bpick[- ]?up\b": "parking drop-off pick-up",
    r"\buniform\b|\bwear\b|\bclothes\b": "dress code",
    r"\binjur\w*\b|\bhurt\b|\btrainer\b": "sports medicine athletic training injury",
    r"\btuition\b|\bcost\b|\bfinancial aid\b|\bscholarship\b": "tuition financial assistance",
    r"\bcounsel\w*\b|\bstress(ed)?\b|\banxi\w*\b|\bmental\b": "counseling wellness support",
    r"\bcollege\b|\bapplications?\b|\bcommon app\b": "college counseling",
    r"\blaptop\b|\bwi-?fi\b|\bprint\w*\b|\bpassword\b|\bwifi\b": "technology IT help desk laptop wifi printing",
    r"\bgraduat\w*\b": "graduation requirements credits",
    r"\belective\b": "elective courses",
    r"\bclubs?\b|\baffinity\b|\bstudent organi[sz]ations?\b": "clubs and affinity groups list",
    r"\bmakeup\b|\bmake[- ]up\b|\bretake\b": "make-up retake assessment policy",
}


def expand_query(q: str) -> str:
    extra = [w for rx, w in SYNONYMS.items() if re.search(rx, q, re.I)]
    return f"{q} ({' '.join(extra)})" if extra else q


_pool = ThreadPoolExecutor(max_workers=6)


class Models:
    """Lazy, thread-safe model holder so startup and health checks never block on loading."""

    def __init__(self):
        self._embed = self._rerank = None
        self._lock = threading.Lock()
        self.rerank_failed = False

    def embed_model(self):
        with self._lock:
            if self._embed is None:
                from sentence_transformers import SentenceTransformer
                print("Loading embedding model...", flush=True)
                self._embed = SentenceTransformer(EMBED_MODEL, device="cpu")
                print("Embedding model ready.", flush=True)
        return self._embed

    def rerank_model(self):
        if not USE_RERANK or self.rerank_failed:
            return None
        with self._lock:
            if self._rerank is None:
                try:
                    from sentence_transformers import CrossEncoder
                    self._rerank = CrossEncoder(RERANK_MODEL, device="cpu", max_length=384)
                    print("Reranker ready.", flush=True)
                except Exception as e:     # retrieval still works without it
                    print(f"Reranker unavailable ({e}); using vector order.", flush=True)
                    self.rerank_failed = True
        return self._rerank

    @property
    def ready(self) -> dict:
        return {"embedding": self._embed is not None, "reranker": self._rerank is not None}


class Retriever:
    def __init__(self, get_supabase):
        self.sb = get_supabase
        self.models = Models()
        self.embed_cache = TTLCache(max_items=2000, ttl=24 * 3600)
        self.calendar_cache = TTLCache(max_items=256, ttl=600)

    # ---- pieces -----------------------------------------------------------
    def embed(self, text: str) -> list[float]:
        key = re.sub(r"\s+", " ", text.lower().strip())
        hit = self.embed_cache.get(key)
        if hit is not None:
            return hit
        vec = self.models.embed_model().encode(text, normalize_embeddings=True).tolist()
        self.embed_cache.set(key, vec)
        return vec

    def vector_search(self, qvec: list[float]) -> list[dict]:
        r = self.sb().rpc("match_chunks", {"query_embedding": qvec, "match_threshold": VECTOR_THRESHOLD,
                                           "match_count": CANDIDATES}).execute()
        return r.data or []

    def rerank(self, query: str, rows: list[dict]) -> list[dict]:
        model = self.models.rerank_model()
        if not model or len(rows) < 2:
            return rows
        scores = model.predict([(query, r["content"][:1500]) for r in rows], show_progress_bar=False)
        for r, s in zip(rows, scores):
            r["rerank"] = float(s)
            # contents/outline chunks answer "list all ..." questions; nudge them up for those
            if (r.get("metadata") or {}).get("kind") == "outline" and LIST_WORDS.search(query):
                r["rerank"] += 3.0
            # calendar digests are looked up structurally, not by similarity
            if (r.get("metadata") or {}).get("source") in ("calendar", "calendar_day"):
                r["rerank"] -= 2.0
        return sorted(rows, key=lambda r: r["rerank"], reverse=True)

    def calendar_events(self, intent: CalendarIntent) -> list[dict]:
        key = (intent.start, intent.end, intent.sport, intent.kind, intent.category, intent.gender,
               intent.division, intent.home_away, tuple(intent.keywords))
        cached = self.calendar_cache.get(key)
        if cached is not None:
            return cached
        digest = intent.short_range() and not (intent.sport or intent.kind or intent.keywords or intent.category)
        q = self.sb().table("document_chunks").select("chunk_id,content,metadata") \
            .in_("metadata->>source", ["calendar_day"] if digest else ["calendar", "calendar_sheet"]) \
            .gte("metadata->>event_date" if digest else "metadata->>end_date", intent.start.isoformat()) \
            .lte("metadata->>event_date", intent.end.isoformat())
        if not digest:
            if intent.sport:
                q = q.ilike("metadata->>summary", f"%{intent.sport}%")
            if intent.kind:
                q = q.eq("metadata->>kind", intent.kind)
            if intent.category:
                q = q.eq("metadata->>category", intent.category)
            if intent.gender:
                q = q.ilike("metadata->>summary", f"%{intent.gender[:-1]}%")      # boys -> boy(s'), girls -> girl(s')
            if intent.division:
                q = q.ilike("metadata->>summary", f"{intent.division} %")
            if intent.home_away:
                q = q.eq("metadata->>home_away", intent.home_away)
            if intent.keywords:
                strict = q
                for k in intent.keywords:
                    strict = strict.ilike("metadata->>summary", f"%{k}%")
                rows = strict.limit(300).execute().data or []
                if not rows:     # fall back to "any keyword" when the strict match finds nothing
                    rows = q.or_(",".join(f"metadata->>summary.ilike.%{k}%" for k in intent.keywords)).limit(300).execute().data or []
                q = None
        if q is not None:
            rows = q.limit(300).execute().data or []
        rows.sort(key=lambda r: ((r["metadata"] or {}).get("event_date", ""), (r["metadata"] or {}).get("start_time") or ""))
        self.calendar_cache.set(key, rows)
        return rows

    def link_cards(self, query: str) -> list[dict]:
        """'How do I book a tour / donate?' -> the booking / payment link cards, looked up by category
        (the right card says 'Ravenna' or 'Give now', which looks nothing like the question to a vector search)."""
        cats = [c for c, rx in LINK_INTENTS if rx.search(query)]
        if not cats:
            return []
        rows = []
        for c in cats:
            key = ("links", c)
            hit = self.calendar_cache.get(key)
            if hit is None:
                hit = self.sb().table("document_chunks").select("chunk_id,content,metadata").eq("metadata->>source", "link_registry") \
                    .eq("metadata->>link_category", c).limit(8).execute().data or []
                self.calendar_cache.set(key, hit, ttl=3600)
            rows += hit
        pinned = [r for r in rows if (r.get("metadata") or {}).get("pinned")]
        if pinned:
            rows = pinned
        rows = [r for r in rows if not re.search(r"login|sign-?in", (r.get("metadata") or {}).get("url", ""), re.I)
                or re.search(r"login|sign[- ]?in", query, re.I)]
        for r in rows:
            r["similarity"] = 1.0
        return rows

    # ---- orchestration ----------------------------------------------------
    def retrieve(self, query: str, history: list[dict] | None = None, top_k: int = 6, today: date | None = None) -> dict:
        t0 = time.time()
        today = today or datetime.now().date()
        history = history or []
        prev_user = next((m["content"] for m in reversed(history) if m.get("role") == "user"), "")
        # short follow-ups ("what about tomorrow?") borrow the previous question for search
        search_q = expand_query(f"{prev_user} {query}" if prev_user and len(query.split()) <= 6 else query)
        intent = parse_intent(query, today, prev_user)
        timings = {}

        def vec_task():
            t = time.time()
            qv = self.embed(search_q)
            timings["embed_ms"] = round((time.time() - t) * 1000)
            t = time.time()
            rows = self.vector_search(qv)
            timings["vector_ms"] = round((time.time() - t) * 1000)
            return rows

        def cal_task():
            if not intent:
                return []
            t = time.time()
            rows = self.calendar_events(intent)
            timings["calendar_ms"] = round((time.time() - t) * 1000)
            return rows

        f_vec, f_cal, f_links = _pool.submit(vec_task), _pool.submit(cal_task), _pool.submit(self.link_cards, query)
        rows, events = f_vec.result(), []
        try:
            link_rows = f_links.result()
        except Exception as e:
            link_rows = []
            print(f"link lookup failed: {e}", flush=True)
        try:
            events = f_cal.result()
        except Exception as e:
            print(f"calendar lookup failed: {e}", flush=True)

        rows = [r for r in rows if not BLOCKED_TITLE.search((r.get("metadata") or {}).get("title", "") or "")]
        # Prefer rows written by the current pipeline; old-pipeline rows (no source_url) only as a fallback.
        fresh = [r for r in rows if (r.get("metadata") or {}).get("source_url")]
        if len(fresh) >= 3:
            rows = fresh
        # calendar chunks come from the structured lookup, not from similarity
        rows = [r for r in rows if (r.get("metadata") or {}).get("source") not in ("calendar", "calendar_day")] if events else rows

        t = time.time()
        ranked = self.rerank(search_q, rows)
        timings["rerank_ms"] = round((time.time() - t) * 1000)
        link_rows = link_rows[:3]
        docs = (link_rows + [d for d in ranked if d["chunk_id"] not in {l["chunk_id"] for l in link_rows}])[:top_k + len(link_rows)]
        timings["retrieve_ms"] = round((time.time() - t0) * 1000)
        return {"docs": docs, "events": events, "intent": intent, "timings": timings, "candidates": len(rows)}


def fmt_event(r: dict) -> str:
    m = r.get("metadata") or {}
    if m.get("source") == "calendar_day":
        return r["content"].split("\n\n", 1)[-1]
    when = m.get("when", m.get("event_date", ""))
    time_s = "all day" if m.get("all_day") else m.get("time", "")
    loc = f" @ {m['location']}" if m.get("location") else ""
    return f"- {when}, {time_s}: {m.get('summary', '')}{loc}"


def team_overview(events: list[dict]) -> str:
    """'What sports are playing?' -> one line per team with its next game, not 14 practices."""
    teams: dict[str, dict] = {}
    for e in events:
        m = e.get("metadata") or {}
        name = re.sub(r"\s*-\s*(practice|game).*$", "", m.get("summary", ""), flags=re.I).strip(" -")
        t = teams.setdefault(name, {"games": 0, "practices": 0, "next_game": None, "first": m.get("event_date")})
        if m.get("kind") == "game":
            t["games"] += 1
            if t["next_game"] is None:
                t["next_game"] = f"{m.get('when', '')} {m.get('summary', '').split('Game', 1)[-1].strip(' -')}".strip()
        elif m.get("kind") == "practice":
            t["practices"] += 1
    lines = [f"- {n}: {t['games']} games, {t['practices']} practices on the calendar" + (f"; next game {t['next_game']}" if t["next_game"] else "")
             for n, t in sorted(teams.items())]
    return "TEAMS ON THE SCHOOL CALENDAR (all teams that have scheduled games or practices):\n" + "\n".join(lines[:60])


def practice_overview(events: list[dict]) -> str:
    """'What time is basketball practice?' -> each team's next practice and how often they meet, not 20 sessions."""
    teams: dict[str, list[dict]] = {}
    for e in events:
        m = e.get("metadata") or {}
        name = re.sub(r"\s*-\s*practice.*$", "", m.get("summary", ""), flags=re.I).strip(" -")
        teams.setdefault(name, []).append(m)
    lines = []
    for name, ms in sorted(teams.items()):
        first = ms[0]
        days = sorted({datetime.fromisoformat(x["event_date"]).strftime("%a") for x in ms if x.get("event_date")},
                      key=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].index)
        lines.append(f"- {name}: next {first.get('when', '')}, {first.get('time', '')}"
                     + (f" at {first['location']}" if first.get("location") else "")
                     + f" (practices on {', '.join(days)}; {len(ms)} scheduled)")
    return "PRACTICE SCHEDULE (one line per team; times can differ by day):\n" + "\n".join(lines[:30])


def build_context(result: dict, max_events: int = 14) -> tuple[str, list[dict]]:
    """-> (context string for the model, sources list for the UI)"""
    parts, sources = [], []
    events, intent = result["events"], result["intent"]
    if events and intent and intent.category == "athletics" and not intent.sport and not intent.kind:
        parts.append(team_overview(events))
        shown = []
        sources.append({"chunk_id": "calendar-teams", "title": "Team schedules", "type": "calendar"})
    elif events and intent and intent.kind == "practice" and len(events) > 4:
        parts.append(practice_overview(events))
        shown = []
        sources.append({"chunk_id": "calendar-practices", "title": "Practice schedule", "type": "calendar"})
    elif events:
        shown = events[:3] if (intent and intent.next_only and intent.kind == "game") else events[:max_events]
        more = f"\n(+{len(events) - len(shown)} more matching events not shown)" if len(events) > len(shown) else ""
        label = f"{intent.label}" if intent else ""
        parts.append(f"SCHOOL CALENDAR ({label}, sorted by date; today is accurate to this list):\n"
                     + "\n".join(fmt_event(e) for e in shown) + more)
        for e in shown[:5]:
            m = e.get("metadata") or {}
            sources.append({"chunk_id": e["chunk_id"], "title": m.get("summary") or m.get("title"), "type": "calendar"})
    elif intent and (intent.sport or intent.kind or intent.category or intent.date_given):
        parts.append(f"SCHOOL CALENDAR: no events found for {intent.label} matching that request.")
    for d in result["docs"]:
        m = d.get("metadata") or {}
        parts.append(d["content"][:1400])          # keeps prompts small: the LLM plan is token-rate limited
        sources.append({"chunk_id": d["chunk_id"], "title": m.get("title"), "url": m.get("source_url") if str(m.get("source_url", "")).startswith("http") else None,
                        "similarity": round(d.get("similarity", 0), 3), "type": m.get("source", "doc")})
    return "\n\n---\n\n".join(parts), sources
