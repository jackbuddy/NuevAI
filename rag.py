"""Retrieval for NuevAI.

For each question, in parallel:
  1. embed (cached) -> vector search over the chunk table (wide: 24 candidates)
  2. structured calendar lookup when the question has a date / sport / event signal
Then a small cross-encoder reranks the vector candidates, and everything is
shaped into a compact, correctly ordered context block for the model.
"""
import difflib
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import embedder
import freshness
import guard
import lunch_lookup
import settings
from cache import TTLCache
from followup import apply_grade, rewrite_followup, valid_grade
from intent import CalendarIntent, LunchIntent, parse_intent, parse_lunch

CANDIDATES = 32
VECTOR_BUDGET_S = float(os.getenv("VECTOR_BUDGET_S", "14"))
FTS_ENABLED = os.getenv("FTS_ENABLED", "1") != "0"
FTS_CANDIDATES = 12
VECTOR_THRESHOLD = 0.0      # gte-small cosine scores sit high (unrelated ~0.75), so rank, do not threshold
W_VECTOR, W_LEXICAL = 0.3, 0.7
OUTLINE_BOOST, CAL_PENALTY, HEAD_BOOST, LEN_EXP = 0.2, 0.2, 1.0, 0.4   # rerank knobs (tuned by sweep_rerank.py)
OLD_COPY_SOURCES = ("nexus", "google_doc", "google_slides", "google_sheet", "manual", "public_site")
EMAIL_RECENCY_MAX, EMAIL_HALF_LIFE_DAYS = 0.10, 30     # forwarded school emails: a modest boost that halves every 30 days, so the newest email wins ties without drowning calendar/structured data
STALE_PENALTY, OLDER_VERSION_PENALTY = 0.25, 0.15      # documents written for an earlier school year; older twin of a newer document
TEACH_CUE = re.compile(r"\b(teach\w*|taught|class(es)?|room|block|course|prof|instructor|mr|mrs|ms|dr)\b|'s\b", re.I)


def norm_words(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("&", " and ").replace("+", " and "))).strip()


LIST_WORDS = re.compile(r"\b(list|all|every|which|what (courses?|classes|electives?|sports|clubs|departments?)|offered|options|available)\b", re.I)

LINK_INTENTS = [
    ("booking", re.compile(r"\b(book|booking|schedule|reserve|register|registration|sign[- ]?up|rsvp|tour|visit|open house|info(rmation)? session|admissions event)\b", re.I)),
    ("payment", re.compile(r"\b(donat\w*|giv(e|ing)|gift|pay|payment|tuition|store|buy|merch\w*|spirit ?wear)\b", re.I)),
]

BELL_Q = re.compile(r"\bbl(o|a)c?ks?\b|\bblocks?\b|\bbell\b|\blunch\b|\bperiods?\b|\bschool day\b|\b(when|what time) (does|do|is) (school|class\w*|the day|the school day) (start|end|begin|over|out|finish)|\bclasses (per|a|each) day\b|\bhow (long|many)\b.*\b(class|classes|block|blocks|lessons?)\b|\bprime\b|\b[ab]['\u2019] ?day\b|\bstart of school\b|\bend of (the )?(school )?day\b|\bschool (start|end)s?\b", re.I)
CAMPUS_Q = re.compile(r"\bphone\b|\bcall\b|\baddress\b|\bwhere (is|are)\b|\blocated\b|\blocation\b|\bfront desk\b|\bmain office\b|\bcampus(es)?\b|\bdirections\b|\bcontact\b|\bhillsborough\b|\bsan mateo\b", re.I)
ADVISORY_NO = re.compile(r"\b(9|10|11|12)\.\d{1,2}\b")
CLUB_GENERIC = set("club clubs team nueva group society association the and of for at in to a an".split())
CLUB_ALIASES = [(re.compile(r"\bmodel un\b", re.I), "model united nations mun"), (re.compile(r"\bmun\b", re.I), "model united nations mun"),
                (re.compile(r"\brobotics\b", re.I), "robotics team"), (re.compile(r"\bamc\b|\baime\b", re.I), "amc aime")]

# Never served, even if such rows exist in the database: sheets/forms that hold people's data.
# chunk text that is personal-record shaped (home address fields, student ids, parent contact cards): never shown to the model
BLOCKED_CONTENT = guard.RECORD_CONTENT      # shared record-shape patterns (guard.py); the email ingest uses them too
BLOCKED_TITLE = re.compile(r"diploma|rsvp|sign[- ]?up|roster|responses|attendance sheet|transcript|salary|payroll|medical", re.I)

# Everyday wording -> the words the handbook actually uses. Added to the search text only (never shown).
SYNONYMS = {
    r"\blate\b|\bleft early\b": "tardy tardies attendance",
    r"\babsent\b|\bmiss(ing)? school\b|\bsick\b": "absence absences attendance illness",
    r"\bphones?\b(?!\s+numbers?)|\bairpods?\b|\bearbuds?\b": "cell phone policy devices",
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
    r"\bphone number\b|\bcall\b|\bmain office\b|\bfront desk\b|\baddress\b|\blocated\b|\blocation\b|\bdirections\b|\bwhere is (the )?(school|campus|nueva)\b": "campus locations and main phone numbers address front desk main office",
    r"\badvis(ory|or|ors|er|ers)\b": "advisory advisors advising team room",
    r"\bclubs?\b|\baffinity\b|\bstudent organi[sz]ations?\b": "clubs and affinity groups list",
    r"\blanguages?\b|\bforeign language\b": "world languages Chinese Spanish French Japanese Latin Korean language courses",
    r"\bmakeup\b|\bmake[- ]up\b|\bretake\b": "make-up retake assessment policy",
}


DOMAIN_WORDS = ["labor", "thanksgiving", "memorial", "veterans", "juneteenth", "presidents", "columbus", "indigenous", "holiday",
                "vacation", "graduation", "homecoming", "orientation", "assembly", "concert", "volleyball", "basketball", "baseball",
                "softball", "lacrosse", "badminton", "swimming", "wrestling", "tutorial", "advisory", "chemistry", "calculus"]


def fix_typos(q: str) -> str:
    """Correct one-letter slips in the few event/sport words the calendar and course lookups key on
    ('labr day' -> 'labor day'). Tokens that are not close to a domain word are left alone."""
    import difflib
    def fix(m):
        w = m.group(0)
        if len(w) < 4 or w.lower() in DOMAIN_WORDS:
            return w
        near = difflib.get_close_matches(w.lower(), DOMAIN_WORDS, n=1, cutoff=0.82)
        return near[0] if near else w
    return re.sub(r"[A-Za-z]+", fix, q)


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


COURSE_NOISE = set("teach teaches teacher teachers taught class classes course courses room rooms block blocks instructor offered section sections period periods fall spring semester year mr mrs ms dr".split())


def _tmatch(a: str, b: str) -> bool:
    """Loose token match: equal, shared 4+ letter prefix (abbreviations like 'phil'/'philosophy', 'consc'/'consciousness'), or a typo."""
    if a == b:
        return True
    if a.isdigit() or b.isdigit():
        return False
    cp = len(os.path.commonprefix([a, b]))
    if cp >= 4 and cp >= min(len(a), len(b)) - 1:
        return True
    return len(a) >= 7 and len(b) >= 7 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


COURSE_ALIASES = {"meche": "mechanical engineering", "mech e": "mechanical engineering", "compsci": "computer science",
                  "comp sci": "computer science", "econ": "economics", "calc": "calculus", "bio": "biology", "orgo": "organic chemistry"}


def _alias_expand(qn: str) -> str:
    """Student shorthand for courses ('meche', 'comp sci') -> the catalog words."""
    out = f" {qn} "
    for a, full in COURSE_ALIASES.items():
        out = out.replace(f" {a} ", f" {full} ")
    return out.strip()


def _fuzzy_last(first: str, last: str, tokens_q: list[str]) -> bool:
    """'dan norgren' -> Dan Norgan: the first name is present and a neighbouring token is a near-spelling of the last name."""
    for i, t in enumerate(tokens_q):
        if t == first and i + 1 < len(tokens_q):
            nxt = tokens_q[i + 1]
            if len(nxt) >= 5 and len(last) >= 5 and difflib.SequenceMatcher(None, nxt, last).ratio() >= 0.75:
                return True
    return False


def _course_loose_match(qcontent: set[str], course_key: str) -> bool:
    ct = [t for t in course_key.split() if t not in STOP]
    if not ct:
        return False
    if all(any(_tmatch(q, c) for c in ct) for q in qcontent):
        return len(qcontent) >= 2 or any(len(q) >= 6 for q in qcontent)
    return False


def email_recency_boost(source_date, today: date | None = None) -> float:
    """Rerank bonus for a forwarded school email: EMAIL_RECENCY_MAX when sent today, halving every EMAIL_HALF_LIFE_DAYS; 0 if undated."""
    age = freshness.email_age_days(source_date, today)
    return 0.0 if age is None else EMAIL_RECENCY_MAX * 0.5 ** (age / EMAIL_HALF_LIFE_DAYS)


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """Drop lower-ranked rows whose text repeats a higher-ranked one (the same document indexed under two
    URLs or titles). Compared on the normalised body: same first and last 200 characters and a similar length."""
    seen, out = {}, []
    for r in rows:
        body = re.sub(r"[^a-z0-9]+", " ", r["content"].split("\n\n", 1)[-1].lower()).strip()
        key = (body[:200], body[-200:])
        if key in seen and 0.8 < (len(body) + 1) / (seen[key] + 1) < 1.25:
            continue
        seen[key] = len(body)
        out.append(r)
    return out


def merge_fts_rows(rows: list[dict], fts: list[dict]) -> list[dict]:
    have = {r["chunk_id"] for r in rows}
    floor = min((r.get("similarity", 0.0) for r in rows), default=0.0)
    for r in fts:
        if r["chunk_id"] not in have:         # keyword-only hit: neutral vector score, the lexical rerank decides
            r["similarity"] = floor
            rows.append(r)
    return rows


class Retriever:
    def __init__(self, get_supabase):
        self.sb = get_supabase
        self.embed_cache = TTLCache(max_items=2000, ttl=24 * 3600)
        self.calendar_cache = TTLCache(max_items=256, ttl=600)
        self._fts_off = False

    # ---- pieces -----------------------------------------------------------
    def embed(self, text: str) -> list[float]:
        key = re.sub(r"\s+", " ", text.lower().strip())
        hit = self.embed_cache.get(key)
        if hit is not None:
            return hit
        vec = embedder.embed_query(text)
        self.embed_cache.set(key, vec)
        return vec

    def vector_search(self, qvec: list[float]) -> list[dict]:
        r = self.sb().rpc(settings.MATCH_RPC, {"query_embedding": qvec, "match_threshold": VECTOR_THRESHOLD,
                                               "match_count": CANDIDATES}).execute()
        return r.data or []

    def fts_search(self, query: str) -> list[dict]:
        """Keyword candidates from Postgres full-text search (RPC search_chunks_fts, see supabase/fts.sql). They catch
        rare words (names, room numbers, event titles) that embeddings blur. Silently disabled if the RPC is missing."""
        if not FTS_ENABLED or self._fts_off:
            return []
        terms = [t for t in dict.fromkeys(re.findall(r"[a-z0-9]{3,}", query.lower())) if t not in STOP][:12]
        if not terms:
            return []
        try:
            r = self.sb().rpc("search_chunks_fts", {"query_text": " | ".join(terms), "match_count": FTS_CANDIDATES}).execute()
            return r.data or []
        except Exception as e:
            self._fts_off = True            # function not installed (or DB error): fall back to vector-only, do not retry per request
            print(f"fts disabled: {str(e)[:120]}", flush=True)
            return []

    def rerank(self, query: str, rows: list[dict], today: date | None = None) -> list[dict]:
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
            score = sum(idf[t] * (1 + math.log(c)) for t, c in tf.items()) + HEAD_BOOST * sum(idf[t] for t in q & head)
            lex.append(score / (len(body) ** LEN_EXP + 1))
        def norm(xs):
            lo, hi = min(xs), max(xs)
            return [(x - lo) / (hi - lo) if hi > lo else 0.0 for x in xs]
        vec = norm([r.get("similarity", 0.0) for r in rows])
        lexn = norm(lex)
        for r, v, l in zip(rows, vec, lexn):
            m = r.get("metadata") or {}
            r["rerank"] = W_VECTOR * v + W_LEXICAL * l
            if m.get("kind") == "outline" and LIST_WORDS.search(query):
                r["rerank"] += OUTLINE_BOOST        # contents lists answer "list all ..." questions
            if m.get("source") in ("calendar", "calendar_day", "calendar_sheet"):
                r["rerank"] -= CAL_PENALTY         # calendar is looked up structurally, not by similarity
            if freshness.is_email_row(r.get("chunk_id", ""), None, m):
                r["rerank"] += email_recency_boost(m.get("source_date"), today)     # newest email wins; emails are never "stale"
            elif freshness.meta_of(r)["stale"]:
                r["rerank"] -= STALE_PENALTY       # written for an earlier school year: only wins when nothing current matches
        # the same document in two years ("2025-26 Course Catalog" / "2026-27 Course Catalog"): the older copy loses
        newest: dict[str, int] = {}
        for r in rows:
            if freshness.is_email_row(r.get("chunk_id", ""), None, r.get("metadata")):
                continue                                 # an email never demotes (or is demoted by) a document of another year
            f = freshness.meta_of(r)
            k = freshness.title_key((r.get("metadata") or {}).get("title", ""))
            if k and f["school_year"]:
                newest[k] = max(newest.get(k, 0), int(f["school_year"][:4]))
        for r in rows:
            if freshness.is_email_row(r.get("chunk_id", ""), None, r.get("metadata")):
                continue
            f = freshness.meta_of(r)
            k = freshness.title_key((r.get("metadata") or {}).get("title", ""))
            if k in newest and f["school_year"] and int(f["school_year"][:4]) < newest[k]:
                r["rerank"] -= OLDER_VERSION_PENALTY
        return sorted(rows, key=lambda r: r["rerank"], reverse=True)

    def calendar_events(self, intent: CalendarIntent) -> list[dict]:
        key = (intent.start, intent.end, intent.sport, intent.kind, intent.category, intent.gender,
               intent.division, intent.home_away, tuple(intent.keywords), tuple(intent.holidays))
        cached = self.calendar_cache.get(key)
        if cached is not None:
            return cached
        digest = intent.short_range() and not (intent.sport or intent.kind or intent.keywords or intent.category)
        def base():      # postgrest builders mutate in place, so the "strict" and "any keyword" queries each need their own
            return self.sb().table(settings.CHUNKS_TABLE).select("chunk_id,content,metadata") \
                .in_("metadata->>source", ["calendar_day", "school_day"] if digest else ["calendar", "calendar_sheet"]) \
                .gte("metadata->>event_date" if digest else "metadata->>end_date", intent.start.isoformat()) \
                .lte("metadata->>event_date", intent.end.isoformat())
        def filtered():
            qb = base()
            if intent.sport:
                qb = qb.ilike("metadata->>summary", f"%{intent.sport}%")
            if intent.kind:
                qb = qb.eq("metadata->>kind", intent.kind)
            if intent.category:
                qb = qb.eq("metadata->>category", intent.category)
            if intent.gender:
                qb = qb.ilike("metadata->>summary", f"%{intent.gender[:-1]}%")      # boys -> boy(s'), girls -> girl(s')
            if intent.division:
                qb = qb.ilike("metadata->>summary", f"{intent.division} %")
            if intent.home_away:
                qb = qb.eq("metadata->>home_away", intent.home_away)
            return qb
        q = base() if digest else filtered()
        if not digest:
            if intent.keywords:
                strict = filtered()
                for k in intent.keywords:
                    strict = strict.ilike("metadata->>summary", f"%{k}%")
                rows = strict.limit(300).execute().data or []
                if not rows:     # fall back to "any keyword" when the strict match finds nothing
                    rows = filtered().or_(",".join(f"metadata->>summary.ilike.%{k}%" for k in intent.keywords)).limit(300).execute().data or []
                if not rows and intent.short_range():       # named-keyword miss on a single date: show that day's schedule instead
                    digest_rows = self.sb().table(settings.CHUNKS_TABLE).select("chunk_id,content,metadata") \
                        .in_("metadata->>source", ["calendar_day", "school_day"]).gte("metadata->>event_date", intent.start.isoformat()) \
                        .lte("metadata->>event_date", intent.end.isoformat()).limit(50).execute().data or []
                    rows = digest_rows
                q = None
        if q is not None:
            rows = q.limit(300).execute().data or []
        if intent.holidays and not digest and intent.category == "no_school":
            # a named holiday (Thanksgiving) is often its own event rather than part of the "no school" category
            have = {r["chunk_id"] for r in rows}
            extra = self.sb().table(settings.CHUNKS_TABLE).select("chunk_id,content,metadata").eq("metadata->>source", "calendar") \
                .gte("metadata->>end_date", intent.start.isoformat()).lte("metadata->>event_date", intent.end.isoformat()) \
                .or_(",".join(f"metadata->>summary.ilike.%{h}%" for h in intent.holidays)).limit(50).execute().data or []
            rows += [r for r in extra if r["chunk_id"] not in have and "basketball" not in (r["metadata"] or {}).get("summary", "").lower()]
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
            hit = [r for r in hit if not (r.get("metadata") or {}).get("superseded")]      # records the current workbooks no longer produce
            self.calendar_cache.set("entities", hit, ttl=1800)
        return hit

    def entity_chunks(self, query: str, today: date) -> list[dict]:
        """Direct lookup for 'who teaches Chemistry', 'what does Wes Chao teach', 'which room is Mr. Lee in'.
        Matches teacher names and course names against the question, so it never depends on semantic search."""
        qn = _alias_expand(norm_words(query))
        tokens_q = set(qn.split())
        tok_list = qn.split()
        cue = bool(TEACH_CUE.search(query))
        hits = []
        loose = bool(re.search(r"\b(who|teach\w*|taught|instructor|prof\w*|room|which block|what block|when is|offered|sections?)\b", query, re.I))
        qcontent = {t for t in qn.split() if t not in STOP and t not in COURSE_NOISE}
        for r in self._entities():
            m = r.get("metadata") or {}
            if m.get("kind") == "teacher":
                full = norm_words(m.get("teacher", ""))
                names = {m.get("teacher_last", "")} | set(m.get("aliases", []))
                ok = bool(full) and f" {full} " in f" {qn} "
                ok = ok or any(n and n in tokens_q and (len(n) >= 6 or cue) for n in names)
                parts = full.split()
                ok = ok or (len(parts) >= 2 and _fuzzy_last(parts[0], parts[-1], tok_list))
                if ok:
                    hits.append((0, r))
            elif m.get("kind") == "course":
                ck = m.get("course_key", "")
                if len(ck) >= 5 and f" {ck} " in f" {qn} ":
                    hits.append((1, r))
                elif loose and qcontent and _course_loose_match(qcontent, ck):
                    hits.append((2, r))
        def sem_rank(r):
            sem = (r.get("metadata") or {}).get("semester", "")
            return 0 if (("Fall" in sem) == (today.month >= 8)) else 1
        hits.sort(key=lambda x: (x[0], sem_rank(x[1]), -len((x[1].get("metadata") or {}).get("course_key", ""))))
        return [r for _, r in hits][:6]

    # ---- pinned facts, advisory numbers, clubs -----------------------------
    def _by_source(self, source: str, kinds: list[str] | None = None) -> list[dict]:
        key = ("src", source)
        hit = self.calendar_cache.get(key)
        if hit is None:
            q = self.sb().table(settings.CHUNKS_TABLE).select("chunk_id,content,metadata").eq("metadata->>source", source)
            hit = [r for r in (q.limit(1000).execute().data or []) if not (r.get("metadata") or {}).get("superseded")]
            self.calendar_cache.set(key, hit, ttl=1800)
        return [r for r in hit if not kinds or (r.get("metadata") or {}).get("kind") in kinds]

    def pinned_facts(self, query: str) -> list[dict]:
        """Curated school facts (bell schedule, campus phones) are short and decisive. When the question is about
        them they are always shown, instead of competing with 5,000 other chunks in a similarity search."""
        out = []
        facts = self._by_source("school_facts")
        if BELL_Q.search(query):
            out += [r for r in facts if "bell_schedule" in str((r.get("metadata") or {}).get("source_url", ""))]
        if CAMPUS_Q.search(query):
            out += [r for r in facts if "campus" in str((r.get("metadata") or {}).get("source_url", ""))]
        return out

    def advisory_lines(self, query: str) -> list[dict]:
        """'Which room is advisory 11.1' -> the exact advisor line(s) for that advisory number."""
        nums = {m.group(0) for m in ADVISORY_NO.finditer(query)}
        if re.search(r"\bintergrade\b", query, re.I) and not re.search(r"\bclub\b", query, re.I):
            # 'Where does intergrade Group A meet?' -> the intergrade groups record (advisors and meeting place per group)
            recs = [r for r in self._by_source("school_advisory", ["intergrade"])]
            return recs[:1]
        if not nums or not re.search(r"advis|room|who|where|teacher", query, re.I):
            return []
        lines = []
        for r in self._by_source("school_advisory", ["advisors"]):
            for ln in r["content"].split("\n"):
                m = re.match(r"\s*-\s*Advisory (\d+\.\d+):", ln)
                if m and m.group(1) in nums:
                    lines.append(ln.strip(" -"))
        if not lines:
            return []
        return [{"chunk_id": "advisory-lines", "content": "Title: Advisory assignments\n\nAdvisors and rooms for the advisories asked about (2026-27):\n"
                 + "\n".join(f"- {l}" for l in lines), "metadata": {"source": "school_advisory", "title": "Advisory assignments"}}]

    def club_chunks(self, query: str) -> list[dict]:
        """Direct lookup of a named club ('Model UN', 'AMC/AIME', 'Robotics') so its leader, advisor, meeting time and room
        reach the model even when similarity search buries the club record."""
        qx = query
        for rx, extra in CLUB_ALIASES:
            if rx.search(query):
                qx += " " + extra
        qt = {t for t in norm_words(qx).split() if t not in CLUB_GENERIC}
        if not qt:
            return []
        hits = []
        for r in self._by_source("school_clubs", ["club"]):
            name = (r.get("metadata") or {}).get("club", "")
            # acronym in parentheses, e.g. "Nueva Model United Nations (MUN)"; "AMC/AIME"
            acr = re.findall(r"\(([^)]+)\)", name)
            acr_t = {a.lower() for x in acr for a in re.split(r"[/&\s]+", x) if a and a.lower() not in CLUB_GENERIC}
            acr_t.discard("club")
            nt = [t for t in norm_words(re.sub(r"\([^)]*\)", " ", name)).split() if t not in CLUB_GENERIC]
            if not nt:
                continue
            matched = sum(1 for t in nt if t in qt or any(len(t) >= 6 and len(q) >= 6 and q[:6] == t[:6] for q in qt))
            if bool(qt & acr_t) or matched == len(nt) and (len(nt) >= 2 or len(nt[0]) >= 5):
                hits.append((matched, r))
        hits.sort(key=lambda x: -x[0])
        return [r for _, r in hits[:2]]

    # ---- orchestration ----------------------------------------------------
    def retrieve(self, query: str, history: list[dict] | None = None, top_k: int = 6, today: date | None = None, grade: int | None = None,
                 campus: str | None = None) -> dict:
        t0 = time.time()
        today = today or datetime.now().date()
        history = history or []
        query = fix_typos(query)
        prev_user = next((m["content"] for m in reversed(history) if m.get("role") == "user"), "")
        # follow-ups ("what about JV?", "who teaches it?") become standalone questions for every lookup below
        standalone = rewrite_followup(query, history)
        resolved = apply_grade(standalone, valid_grade(grade))
        followed = resolved != query
        # the cafe's lunch menu is an exact lookup of the day record(s) for the campus: no vector search, no calendar block
        try:
            li = parse_lunch(resolved, today, lunch_lookup.valid_campus(campus), prev_user)
            if li:
                data = lunch_data(self.sb(), li, today)
                return {"pinned": [], "docs": [], "events": [], "intent": None, "timings": {"retrieve_ms": round((time.time() - t0) * 1000)},
                        "candidates": 0, "entities": [], "degraded": False, "resolved": standalone, "lunch": data}
        except Exception as e:
            print(f"lunch lookup failed: {type(e).__name__}: {str(e)[:120]}", flush=True)
        # short follow-ups ("what about tomorrow?") borrow the previous question for search
        search_q = expand_query(resolved if followed else f"{prev_user} {query}" if prev_user and len(query.split()) <= 6 else query)
        intent = (parse_intent(resolved, today, prev_user) if followed else None) or parse_intent(query, today, prev_user)
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
            ents = self.entity_chunks(resolved, today)
        except Exception as e:
            ents = []
            print(f"entity lookup failed: {e}", flush=True)
        pinned = []
        try:
            pinned = self.pinned_facts(resolved) + self.advisory_lines(resolved)
            clubs = self.club_chunks(resolved)
            if not clubs and prev_user and len(query.split()) <= 6:
                clubs = self.club_chunks(prev_user)
            pinned += clubs
        except Exception as e:
            print(f"pinned lookup failed: {e}", flush=True)
        self.sb()      # create the client once, before worker threads import it concurrently
        f_vec, f_cal, f_links, f_fts = _pool.submit(vec_task), _pool.submit(cal_task), _pool.submit(self.link_cards, resolved), _pool.submit(self.fts_search, search_q)
        vec_rows, degraded = [], False
        try:
            vec_rows = f_vec.result(timeout=VECTOR_BUDGET_S)
        except Exception as e:        # embedding function 546/5xx, slow database: carry on with the structured lookups
            degraded = True
            print(f"vector search unavailable ({type(e).__name__}: {str(e)[:100]}); continuing without it", flush=True)
        try:
            fts_rows = f_fts.result(timeout=3)
        except Exception:
            fts_rows = []
        rows, events = merge_fts_rows(vec_rows, fts_rows), []
        try:
            link_rows = f_links.result(timeout=12)
        except Exception as e:
            link_rows = []
            print(f"link lookup failed: {e}", flush=True)
        try:
            events = f_cal.result(timeout=12)
        except Exception as e:
            print(f"calendar lookup failed: {e}", flush=True)

        rows = [r for r in rows if (r.get("metadata") or {}).get("source") != "lunch"          # day records are reached only through the lunch lookup
                and not BLOCKED_TITLE.search((r.get("metadata") or {}).get("title", "") or "")
                and not BLOCKED_CONTENT.search(r.get("content") or "") and not (r.get("metadata") or {}).get("superseded")]
        # Prefer rows written by the current pipeline; old-pipeline rows (no source_url) only as a fallback.
        fresh = [r for r in rows if (r.get("metadata") or {}).get("source_url")]
        if len(fresh) >= 3:
            rows = fresh
        # Document rows without indexed_at were written by an older chunker (superseded copies with URL titles, repeated pages):
        # every row the current pipeline produces carries it, so the old copies stay out of the context until they are pruned.
        current = [r for r in rows if (r.get("metadata") or {}).get("indexed_at") or (r.get("metadata") or {}).get("source") not in OLD_COPY_SOURCES]
        if len(current) >= 3:
            rows = current
        # calendar chunks come from the structured lookup, not from similarity
        rows = [r for r in rows if (r.get("metadata") or {}).get("source") not in ("calendar", "calendar_day", "school_day")] if events else rows

        t = time.time()
        ranked = dedupe_rows(self.rerank(search_q, rows, today))
        timings["rerank_ms"] = round((time.time() - t) * 1000)
        link_rows = link_rows[:3]
        docs = (link_rows + [d for d in ranked if d["chunk_id"] not in {l["chunk_id"] for l in link_rows}])[:top_k + len(link_rows)]
        timings["retrieve_ms"] = round((time.time() - t0) * 1000)
        ent_ids = {e["chunk_id"] for e in ents}
        if intent and intent.label == "the next two weeks" and any(p["chunk_id"] == "advisory-lines" for p in pinned):
            events, intent = [], None      # "advisory 11.1" is not a date question; the two-week digest would only add noise
        if intent and events and ents and intent.category == "athletics" and not (intent.sport or intent.kind or intent.date_given or intent.holidays) \
                and any(re.search(r"\bteams?\b", ((e.get("metadata") or {}).get("course") or ""), re.I) for e in ents):
            events, intent = [], None      # "Biology Research Teams 2" is a course, not a sports-team question: no team overview
        pin_ids = {p["chunk_id"] for p in pinned}
        docs = [d for d in docs if d["chunk_id"] not in ent_ids | pin_ids]
        return {"pinned": pinned, "docs": docs, "events": events, "intent": intent, "timings": timings, "candidates": len(rows), "entities": ents, "degraded": degraded, "resolved": standalone}


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
    if result.get("lunch"):
        return guard.scrub_context(lunch_context(result["lunch"])), lunch_sources(result["lunch"])
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
    for p in result.get("pinned") or []:
        pm = p.get("metadata") or {}
        if pm.get("source") == "school_facts" and not any(x.startswith("KEY FACTS") for x in parts):
            parts.append("KEY FACTS (confirmed by the school; authoritative):")
        parts.append(p["content"].split("\n\n", 1)[-1][:1800])
        sources.append({"chunk_id": p["chunk_id"], "title": pm.get("title"), "type": pm.get("source", "doc")})
    for d in result["docs"]:
        m = d.get("metadata") or {}
        f = freshness.meta_of(d)
        tag = ""
        if freshness.is_email_row(d.get("chunk_id", ""), None, m):
            sent = freshness.day_label(m.get("source_date"))
            tag = f"[School email, sent {sent}] " if sent else "[School email] "          # a recent official update; newest wins over older sources
        elif f["stale"]:
            why = f["reason"] or (f"written for the {f['school_year']} school year or earlier" if f["school_year"] else "dated before this school year")
            tag = f"[OUTDATED: {why}; use only if nothing current answers, and say it may have changed]\n"
        elif f["source_date"]:
            tag = f"[as of {f['source_date']}]\n"
        parts.append(tag + d["content"][:1100])          # keeps prompts small: the LLM plan is token-rate limited
        sources.append({"chunk_id": d["chunk_id"], "title": m.get("title"), "url": m.get("source_url") if str(m.get("source_url", "")).startswith("http") else None,
                        "similarity": round(d.get("similarity", 0), 3), "type": m.get("source", "doc")})
    # retrieved text is data, never instructions: drop lines that try to command the model
    return guard.scrub_context("\n\n---\n\n".join(parts)), sources


# ---------------------------------------------------------------------------------------------- lunch menu (cafe PDFs, see pipeline/lunch.py)
LUNCH_NOTE = lunch_lookup.NOTE


def lunch_data(sb, li: LunchIntent, today: date) -> dict:
    """The day record(s) a lunch question asks for, straight from the index (campus x day), or, for 'when is pizza day', the matching days."""
    data = {"intent": li, "today": today, "entries": [], "search": None}
    if li.keywords and not li.date_given:
        data["search"] = {c: lunch_lookup.search_days(sb, c, li.keywords, today) for c in li.campuses}
        data["months"] = {c: lunch_lookup.available_months(sb, c) for c in li.campuses}
        return data
    data["entries"] = [] if li.hours_only else [lunch_lookup.day_info(sb, c, d) for d in li.days for c in li.campuses]
    if li.hours:
        data["hours"] = {c: lunch_lookup.cafe_hours(sb, c, li.days[0]) for c in li.campuses}
    return data


def _day_long(d: date) -> str:
    return d.strftime("%A, %B ") + str(d.day) + d.strftime(", %Y")


def _unavailable_line(info: dict, today: date) -> str:
    d = date.fromisoformat(info["date"])
    if not info["month_available"]:
        mon = info["month_label"]
        if d < today.replace(day=1):
            return f"I do not have the {mon} menu (only the current month's menu and later ones are kept)."
        return f"The {mon.split()[0]} menu isn't posted yet. Say that, and do not show any other month's menu."
    if str(info["closed_reason"]).startswith("Weekend"):
        return "No lunch service (weekend)."
    return f"No lunch service listed: {info['closed_reason']}"


def lunch_context(data: dict) -> str:
    li, today = data["intent"], data["today"]
    out = [f"SCHOOL LUNCH MENU (the cafe's monthly menus; today is {_day_long(today)}). This block is authoritative: answer only from it.",
           "RULES: Name the campus for every menu. List the dishes exactly as written, grouped as the cafe groups them. Never add, change, translate or guess dishes, "
           "ingredients, allergens or dietary labels. The cafe's menu key (vegetarian, vegan, gluten free, dairy free ...) is a row of icons that is NOT attached to "
           "individual dishes in the posted menu, so no dish is officially marked vegetarian, vegan, gluten free, dairy free or nut free: only call a dish vegetarian or "
           "vegan when its own name says so (for example 'Vegetarian Quesadillas'); 'Earthly Selections' and 'Plant Inspired' are the cafe's own section names, not "
           "a promise about ingredients. For allergies or dietary needs tell the student to check with the cafe. If a menu is missing or not posted, say so plainly. "
           f"End the answer with this exact sentence on its own line: {LUNCH_NOTE}"]
    if li.campus_from == "none":
        out.append("The campus is not known: show BOTH campuses, clearly labelled, and mention the student can say which campus they want.")
    if li.diet:
        out.append(f"The student asks about {li.diet} food: start with one sentence like 'The posted menu does not mark individual dishes as {li.diet}, but the cafe lists "
                   "these Earthly Selections and Plant Inspired dishes (and any dish whose own name says it)', then list them, and tell them to confirm with the cafe. "
                   "Never claim a dish is gluten free, dairy free or nut free.")
    if li.prek:
        out.append("The student mentions PreK or Kindergarten: their menu is a separate cafe PDF that is not available to you; say so. The Hillsborough menu below is "
                   "for the Lower and Middle School only.")
    if li.wide:
        out.append("The student asked about a long period: only the first school days are shown; offer to look up a specific day.")
    if data.get("search") is not None:
        for c, hits in data["search"].items():
            name = lunch_lookup.CAMPUS_NAME[c]
            if hits:
                out.append(f"{name}: days with '{' '.join(li.keywords)}' on the menu (posted menus only: {', '.join(data['months'][c]) or 'none'}):")
                for dkey, rec in hits:
                    m = [x for x in rec["items"] if all(w in x.lower() for w in li.keywords)]
                    out.append(f"- {_day_long(date.fromisoformat(dkey))}: " + " | ".join(m))
            else:
                out.append(f"{name}: no day from today on in the posted menus ({', '.join(data['months'][c]) or 'none posted'}) lists '{' '.join(li.keywords)}'. "
                           "Say that, and that later menus may differ.")
        return "\n".join(out)
    by_day: dict[str, list[dict]] = {}
    for e in data["entries"]:
        by_day.setdefault(e["date"], []).append(e)
    for dkey, infos in by_day.items():
        d = date.fromisoformat(dkey)
        rel = {today: " (today)", today + timedelta(days=1): " (tomorrow)"}.get(d, "")
        out.append(f"\n{_day_long(d)}{rel}:")
        if all(not i["available"] for i in infos) and len({_unavailable_line(i, today) for i in infos}) == 1:
            out.append(f"  {'Both campuses' if len(infos) > 1 else lunch_lookup.CAMPUS_NAME[infos[0]['campus']]}: {_unavailable_line(infos[0], today)}")
            continue
        for i in infos:
            out.append(f"  {lunch_lookup.CAMPUS_NAME[i['campus']]}:")
            if i["available"]:
                out += [f"  - {x}" for x in i["items"]]
            else:
                out.append(f"  {_unavailable_line(i, today)}")
    if li.hours:
        hrs = {c: h for c, h in (data.get("hours") or {}).items() if h}
        out.append("\nCafe hours as printed on the cafe's menus: " + ("; ".join(f"{lunch_lookup.CAMPUS_NAME[c]} {h}" for c, h in hrs.items()) if hrs
                                                                       else "not listed in the posted menu; say you do not have them."))
    return "\n".join(out)


def lunch_sources(data: dict) -> list[dict]:
    seen, out = set(), []
    for e in data.get("entries") or []:
        if e["available"] and (e["campus"], e["date"]) not in seen:
            seen.add((e["campus"], e["date"]))
            d = date.fromisoformat(e["date"])
            out.append({"chunk_id": f"lunch-{e['campus']}-{d:%Y%m%d}", "title": f"Lunch menu - {lunch_lookup.CAMPUS_NAME[e['campus']]} - {d.strftime('%b')} {d.day}",
                        "type": "lunch", "url": e["source_url"] if str(e["source_url"]).startswith("http") else None})
    for c, hits in (data.get("search") or {}).items():
        for dkey, rec in hits[:2]:
            out.append({"chunk_id": f"lunch-{c}-{dkey.replace('-', '')}", "title": f"Lunch menu - {lunch_lookup.CAMPUS_NAME[c]} - {dkey}", "type": "lunch",
                        "url": rec["source_url"] if str(rec["source_url"]).startswith("http") else None})
    return out[:6]


def _hours_line(data: dict) -> str:
    hrs = {c: h for c, h in (data.get("hours") or {}).items() if h}
    return ("Cafe hours as printed on the cafe's menus: " + "; ".join(f"{lunch_lookup.CAMPUS_NAME[c]} {h}" for c, h in hrs.items())) if hrs else \
        "The cafe hours are not listed in the posted menu."


def lunch_plain_answer(data: dict) -> str:
    """The same facts as plain text, for when the model is unavailable: direct answer first, campus named, the note last. Built only from the records."""
    li, today = data["intent"], data["today"]
    head = []
    if li.hours:
        head.append(_hours_line(data))
    if li.diet:
        head.append(f"The posted menu does not mark individual dishes as {li.diet}; these are the cafe's Earthly Selections and Plant Inspired dishes (confirm with the cafe):")
    if li.hours_only:
        return "\n".join(head + ["", LUNCH_NOTE])
    if head:
        return "\n".join(head + [""]) + "\n" + _lunch_plain_body(data)
    return _lunch_plain_body(data)


def _lunch_plain_body(data: dict) -> str:
    li, today = data["intent"], data["today"]
    if data.get("search") is not None:
        lines = []
        for c, hits in data["search"].items():
            name = lunch_lookup.CAMPUS_NAME[c]
            if hits:
                lines.append(f"{name}:")
                lines += [f"- {date.fromisoformat(k).strftime('%a, %b')} {date.fromisoformat(k).day}: " + " | ".join(x for x in r["items"] if all(w in x.lower() for w in li.keywords)) for k, r in hits]
            else:
                lines.append(f"{name}: no day in the posted menus lists '{' '.join(li.keywords)}'.")
        return "\n".join(lines + ["", LUNCH_NOTE])
    lines, by_day = [], {}
    for e in data["entries"]:
        by_day.setdefault(e["date"], []).append(e)
    for dkey, infos in by_day.items():
        d = date.fromisoformat(dkey)
        lines.append(f"{_day_long(d)}:")
        for i in infos:
            name = lunch_lookup.CAMPUS_NAME[i["campus"]]
            if i["available"]:
                lines.append(f"{name}:")
                lines += [f"- {x}" for x in i["items"]]
            else:
                lines.append(f"{name}: {_unavailable_line(i, today).split(' Say that')[0]}")
    return "\n".join(lines + ["", LUNCH_NOTE])
