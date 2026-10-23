"""Retrieval for NuevAI.

For each question, in parallel:
  1. embed (cached) -> vector search over the chunk table (wide: 24 candidates)
  2. structured calendar lookup when the question has a date / sport / event signal
Then a small cross-encoder reranks the vector candidates, and everything is
shaped into a compact, correctly ordered context block for the model.
"""
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import embedder
import settings
from cache import TTLCache
from intent import CalendarIntent, parse_intent

CANDIDATES = 24
VECTOR_THRESHOLD = 0.0      # gte-small cosine scores sit high (unrelated ~0.75), so rank, do not threshold
W_VECTOR, W_LEXICAL = 0.5, 0.5
TEACH_CUE = re.compile(r"\b(teach\w*|taught|class(es)?|room|block|course|prof|instructor|mr|mrs|ms|dr)\b|'s\b", re.I)


def norm_words(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("&", " and ").replace("+", " and "))).strip()


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
    r"\badvis(ory|or|ors|er|ers)\b": "advisory advisors advising team room",
    r"\bclubs?\b|\baffinity\b|\bstudent organi[sz]ations?\b": "clubs and affinity groups list",
    r"\bmakeup\b|\bmake[- ]up\b|\bretake\b": "make-up retake assessment policy",
}


def expand_query(q: str) -> str:
    extra = [w for rx, w in SYNONYMS.items() if re.search(rx, q, re.I)]
    return f"{q} ({' '.join(extra)})" if extra else q


_pool = ThreadPoolExecutor(max_workers=6)


STOP = set("""a an the is are was were be been do does did can could will would should what when where which who whom how why of for on in at to from
with about into over under this that these those it its my our your their me us we you i there here any some all more most next last new
today tomorrow tonight please tell show give list find get have has had am pm""".split())
TOKEN = re.compile(r"[a-z0-9]+(?:[.'-][a-z0-9]+)*")


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP and len(t) > 1]


class Retriever:
    def __init__(self, get_supabase):
        self.sb = get_supabase
        self.embed_cache = TTLCache(max_items=2000, ttl=24 * 3600)
        self.calendar_cache = TTLCache(max_items=256, ttl=600)

    # ---- pieces -----------------------------------------------------------
    def embed(self, text: str) -> list[float]:
        key = re.sub(r"\s+", " ", text.lower().strip())
        hit = self.embed_cache.get(key)
        if hit is not None:
            return hit
        vec = embedder.embed_one(text)
        self.embed_cache.set(key, vec)
        return vec

    def vector_search(self, qvec: list[float]) -> list[dict]:
        r = self.sb().rpc(settings.MATCH_RPC, {"query_embedding": qvec, "match_threshold": VECTOR_THRESHOLD,
                                               "match_count": CANDIDATES}).execute()
        return r.data or []

    def rerank(self, query: str, rows: list[dict]) -> list[dict]:
        """Hybrid score = vector similarity + lexical overlap (idf-weighted, title/section boosted), both min-max
        normalised over the candidates. Cheap, no model, and it fixes exact-wording misses that embeddings blur."""
        if len(rows) < 2:
            return rows
        q = set(tokens(query))
        docs = []
        for r in rows:
            m = r.get("metadata") or {}
            head = f"{m.get('title', '')} {r['content'].split(chr(10) + chr(10), 1)[0]}"
            body = tokens(r["content"][:1800])
            docs.append((body, set(tokens(head))))
        n = len(rows)
        df = {t: sum(1 for body, _ in docs if t in set(body)) for t in q}
        idf = {t: math.log(1 + n / (1 + df[t])) for t in q}
        lex = []
        for body, head in docs:
            tf = {}
            for t in body:
                if t in q:
                    tf[t] = tf.get(t, 0) + 1
            score = sum(idf[t] * (1 + math.log(c)) for t, c in tf.items()) + 2.0 * sum(idf[t] for t in q & head)
            lex.append(score / (len(body) ** 0.25 + 1))
        def norm(xs):
            lo, hi = min(xs), max(xs)
            return [(x - lo) / (hi - lo) if hi > lo else 0.0 for x in xs]
        vec = norm([r.get("similarity", 0.0) for r in rows])
        lexn = norm(lex)
        for r, v, l in zip(rows, vec, lexn):
            m = r.get("metadata") or {}
            r["rerank"] = W_VECTOR * v + W_LEXICAL * l
            if m.get("kind") == "outline" and LIST_WORDS.search(query):
                r["rerank"] += 0.35        # contents lists answer "list all ..." questions
            if m.get("source") in ("calendar", "calendar_day", "calendar_sheet"):
                r["rerank"] -= 0.2         # calendar is looked up structurally, not by similarity
        return sorted(rows, key=lambda r: r["rerank"], reverse=True)

    def calendar_events(self, intent: CalendarIntent) -> list[dict]:
        key = (intent.start, intent.end, intent.sport, intent.kind, intent.category, intent.gender,
               intent.division, intent.home_away, tuple(intent.keywords))
        cached = self.calendar_cache.get(key)
        if cached is not None:
            return cached
        digest = intent.short_range() and not (intent.sport or intent.kind or intent.keywords or intent.category)
        q = self.sb().table(settings.CHUNKS_TABLE).select("chunk_id,content,metadata") \
            .in_("metadata->>source", ["calendar_day", "school_day"] if digest else ["calendar", "calendar_sheet"]) \
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
                hit = self.sb().table(settings.CHUNKS_TABLE).select("chunk_id,content,metadata").eq("metadata->>source", "link_registry") \
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

    # ---- teachers and courses ---------------------------------------------
    def _entities(self) -> list[dict]:
        """All teacher and course records (about 400 small rows), cached for 30 minutes."""
        hit = self.calendar_cache.get("entities")
        if hit is None:
            hit = self.sb().table(settings.CHUNKS_TABLE).select("chunk_id,content,metadata").eq("metadata->>source", "school_courses") \
                .in_("metadata->>kind", ["teacher", "course"]).limit(1000).execute().data or []
            self.calendar_cache.set("entities", hit, ttl=1800)
        return hit

    def entity_chunks(self, query: str, today: date) -> list[dict]:
        """Direct lookup for 'who teaches Chemistry', 'what does Wes Chao teach', 'which room is Mr. Lee in'.
        Matches teacher names and course names against the question, so it never depends on semantic search."""
        qn = norm_words(query)
        tokens_q = set(qn.split())
        cue = bool(TEACH_CUE.search(query))
        hits = []
        for r in self._entities():
            m = r.get("metadata") or {}
            if m.get("kind") == "teacher":
                full = norm_words(m.get("teacher", ""))
                names = {m.get("teacher_last", "")} | set(m.get("aliases", []))
                ok = bool(full) and f" {full} " in f" {qn} "
                ok = ok or any(n and n in tokens_q and (len(n) >= 6 or cue) for n in names)
                if ok:
                    hits.append((0, r))
            elif m.get("kind") == "course":
                ck = m.get("course_key", "")
                if len(ck) >= 5 and f" {ck} " in f" {qn} ":
                    hits.append((1, r))
        def sem_rank(r):
            sem = (r.get("metadata") or {}).get("semester", "")
            return 0 if (("Fall" in sem) == (today.month >= 8)) else 1
        hits.sort(key=lambda x: (x[0], sem_rank(x[1]), -len((x[1].get("metadata") or {}).get("course_key", ""))))
        return [r for _, r in hits][:6]

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

        try:
            ents = self.entity_chunks(query, today)
        except Exception as e:
            ents = []
            print(f"entity lookup failed: {e}", flush=True)
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
        ent_ids = {e["chunk_id"] for e in ents}
        docs = [d for d in docs if d["chunk_id"] not in ent_ids]
        return {"docs": docs, "events": events, "intent": intent, "timings": timings, "candidates": len(rows), "entities": ents}


def fmt_event(r: dict) -> str:
    m = r.get("metadata") or {}
    if m.get("source") in ("calendar_day", "school_day"):
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
    ents = result.get("entities") or []
    if ents:
        parts.append("TEACHING ASSIGNMENTS (from the master schedule; Blocks 1-4 meet on A/A' days, Blocks 5-8 on B/B' days):\n\n"
                     + "\n\n".join(e["content"].split("\n\n", 1)[-1] for e in ents))
        for e in ents[:3]:
            sources.append({"chunk_id": e["chunk_id"], "title": (e.get("metadata") or {}).get("title"), "type": "schedule"})
    for d in result["docs"]:
        m = d.get("metadata") or {}
        parts.append(d["content"][:1400])          # keeps prompts small: the LLM plan is token-rate limited
        sources.append({"chunk_id": d["chunk_id"], "title": m.get("title"), "url": m.get("source_url") if str(m.get("source_url", "")).startswith("http") else None,
                        "similarity": round(d.get("similarity", 0), 3), "type": m.get("source", "doc")})
    return "\n\n---\n\n".join(parts), sources
