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
import qnorm
import settings
from cache import TTLCache
from followup import apply_grade, rewrite_followup, valid_grade
from intent import DATE_HINT, SPORTS as SPORTS_WORDS, CalendarIntent, LunchIntent, parse_intent, parse_lunch

CANDIDATES = 32
VECTOR_BUDGET_S = float(os.getenv("VECTOR_BUDGET_S", "14"))
FTS_ENABLED = os.getenv("FTS_ENABLED", "1") != "0"
FTS_CANDIDATES = 12
FTS_RARE_CANDIDATES = 10
RARE_BOOST, DIV_BONUS, DIV_PENALTY_EXPLICIT, DIV_PENALTY_DEFAULT = 0.25, 0.08, 0.30, 0.10   # rerank knobs: rare-word match, audience division
TITLE_CAP, FAMILY_CAP = 2, 3        # max rows from one document title / from the handbook family in the final top-k
VECTOR_THRESHOLD = 0.0      # gte-small cosine scores sit high (unrelated ~0.75), so rank, do not threshold
W_VECTOR, W_LEXICAL = 0.3, 0.7
OUTLINE_BOOST, CAL_PENALTY, HEAD_BOOST, LEN_EXP = 0.2, 0.2, 1.0, 0.4   # rerank knobs (tuned by sweep_rerank.py)
OLD_COPY_SOURCES = ("nexus", "google_doc", "google_slides", "google_sheet", "manual", "public_site")
EMAIL_RECENCY_MAX, EMAIL_HALF_LIFE_DAYS = 0.10, 30     # forwarded school emails: a modest boost that halves every 30 days, so the newest email wins ties without drowning calendar/structured data
STALE_PENALTY, OLDER_VERSION_PENALTY = 0.25, 0.15      # documents written for an earlier school year; older twin of a newer document
TEACH_CUE = re.compile(r"\b(teach\w*|taught|class(es)?|room|block|course|prof|instructor|mr|mrs|ms|dr)\b|'s\b", re.I)


def norm_words(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("&", " and ").replace("+", " and "))).strip()


LINK_GENERIC = set("book booking schedule reserve register registration sign up tour visit appointment event link use form submit fill out".split())
LIST_WORDS = re.compile(r"\b(list|all|every|which|what (courses?|classes|electives?|sports|clubs|departments?)|offered|options|available|are there (any|some)|is there (any|a) (list|club))\b", re.I)
MONEY_Q = re.compile(r"\bhow much\b|\bcosts?\b|\bprices?\b|\bfees?\b|\btuition\b|\bhow expensive\b", re.I)
CLUB_Q = re.compile(r"\bclubs?\b|\baffinity\b", re.I)

LINK_INTENTS = [
    ("booking", re.compile(r"\b(book|booking|schedule|reserve|register|registration|sign[- ]?up|rsvp|tour|visit|open house|info(rmation)? session|admissions event)\b", re.I)),
    ("payment", re.compile(r"\b(donat\w*|giv(e|ing)|gift|pay|payment|tuition|store|buy|merch\w*|spirit ?wear)\b", re.I)),
    # "How do I apply / what is the application fee": the admissions booking/payment cards (Ravenna) are the answer
    ("booking", re.compile(r"\b(apply|applying|application|applications|admissions?|enrol\w*)\b(?!.*\b(college|colleges|university|universities|common app)\b)", re.I)),
    ("payment", re.compile(r"\bapplication fee\b|\bfee to apply\b|\bapply(?:ing)? fee\b", re.I)),
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
    r"^(?!.*\bclubs?\b)(?s:.*)(?:\bai\b|\bchatgpt\b|\bgpt\b)": "artificial intelligence generative AI academic integrity",
    r"\bcheat(ing)?\b|\bcopy(ing)?\b": "plagiarism academic integrity honor",
    r"\bhomework\b": "homework assignments late work",
    r"\bgrad(e|es|ing)\b|\bgpa\b|\breport cards?\b": "grading grades assessment reporting",
    r"\bbus(es)?\b|\btransport\w*\b|\bshuttle\b": "transportation shuttle bus",
    r"^(?!.*\b(leave|leaving|off[- ]campus|open campus|closed campus|allowed|permitted)\b)(?s:.*)(?:\blunch\b|\bfood\b|\bcafeteria\b|\bcafe\b)": "lunch cafe meals dining",
    r"\bleav(e|ing) (campus|school)\b|\boff[- ]campus\b|\b(open|closed)[- ]campus\b|\bgo off campus\b|\bsign(ed)? out\b": "closed-campus school grades 9 & 10 open campus leave campus during the school day policy",
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
    r"\bfirst day of (school|classes)\b|\bwhen (did|does|do) (school|classes) (start|begin)\b|\bschool (started|starts|begins)\b": "First Day of School classes begin opening day",
    # nicknames, acronyms and the words the school's own documents use
    r"\bhoco\b|\bhomecoming\b": "HoCo homecoming spirit week dance",
    r"\bhonou?r (code|council|system|pledge)\b": "Academic Integrity plagiarism cheating honesty community norms",
    r"(?-i:\bAP\b)|\bap (classes|courses|exams?|tests?)\b|\badvanced placement\b": "AP exams advanced placement courses not designed to prepare students for AP exams",
    r"(?-i:\bSAT\b|\bACT\b|\bPSAT\b)|\bsat (test|exam|date|dates|score|scores)\b|\bstandardi[sz]ed test\w*": "SAT ACT PSAT test date standardized testing",
    r"\bapplication fees?\b|\bfee to apply\b|\bhow (do|can) i apply\b|\bapply(ing)? (to|for|at) nueva\b|\badmissions?\b": "admissions apply Ravenna application fee family information form visit and apply",
    r"\bhead of school\b|\bprincipal\b|\bdivision heads?\b|\bwho (runs|leads) (nueva|the school)\b|\bwho is in charge\b|\b(upper|middle|lower) school (head|director)\b": "Head of School Division Head leadership team",
    r"\bparking\b|\bpark (on|at|in)\b|\bpark my car\b|\bdrive to school\b|\bdriving to school\b": "parking request form Gr. 11 & 12 driving to campus",
    r"\breport\w*\b.{0,25}\babsen\w+|\babsen\w+.{0,25}\breport\w*|\bcall(ing)? in sick\b|\bout sick\b|\bsick (day|note)\b": "attendance reporting form absence reporting US Attendance Reporting Form",
    r"(?-i:\bPE\b)|\bphys(ical)? ed\w*\b|\bgym class\b": "physical education PE requirement athletics",
    r"\bjv\b|\bjunior varsity\b": "junior varsity JV varsity levels",
    r"\bpre-?participation\b|\bsports physical\b|\bpaperwork\b.{0,40}\b(team|sport|athlet\w*)\b|\b(team|sport|athlet\w*)\b.{0,40}\b(paperwork|forms?|physical)\b": "pre-participation physical athletics forms clearance",
    r"\b(is|are) there\b.{0,40}\b(team|teams|sports?|varsity|jv|baseball|softball|football|hockey|wrestling|cheer\w*|lacrosse|rugby|basketball|b-?ball)\b|\b(do|does) (we|nueva|the school|you)( even)? (have|offer)\b.{0,40}\b(team|teams|sport|sports|baseball|softball|football|hockey|wrestling|cheer\w*|lacrosse|rugby|golf|swim\w*|track|tennis|volleyball|badminton|fencing)\b|\bwhat sports\b|\bsports (offered|available|does nueva)\b": "varsity offerings by season athletics teams no-cut limited-roster teams Fall Winter Spring",
    r"\bbus\b|\bbuses\b|\bshuttle\b|\bkidzjet\b|\bcaltrain\b": "transportation bus shuttle Kidzjet routes timetable ridership",
}


DOMAIN_WORDS = ["labor", "thanksgiving", "memorial", "veterans", "juneteenth", "presidents", "columbus", "indigenous", "holiday",
                "vacation", "graduation", "homecoming", "orientation", "assembly", "concert", "volleyball", "basketball", "baseball",
                "softball", "lacrosse", "badminton", "swimming", "wrestling", "tutorial", "advisory", "chemistry", "calculus"]


KNOWN_TERMS = frozenset(set(SPORTS_WORDS) | set(DOMAIN_WORDS) | {"meche", "baseball", "softball", "lacrosse", "hockey", "cheer", "cheerleading", "football", "rugby", "pickleball", "fencing", "archery"})


def fix_typos(q: str) -> str:
    """Slang/typo repair from the index vocabulary (qnorm.py: 'tommrow' -> 'tomorrow', 'skool' -> 'school', 'b ball' -> 'basketball'),
    then one-letter slips in the few event/sport words the calendar and course lookups key on ('labr day' -> 'labor day').
    Tokens that are not close to a known word are left alone."""
    q = qnorm.fix_typos(q, KNOWN_TERMS)
    def fix(m):
        w = m.group(0)
        if len(w) < 4 or w.lower() in DOMAIN_WORDS or qnorm.known(w):
            return w
        near = difflib.get_close_matches(w.lower(), DOMAIN_WORDS, n=1, cutoff=0.82)
        return near[0] if near else w
    return re.sub(r"[A-Za-z]+", fix, q)


def expand_query(q: str) -> str:
    extra = [w for rx, w in SYNONYMS.items() if w and re.search(rx, q, re.I)]
    return f"{q} ({' '.join(extra)})" if extra else q


_pool = ThreadPoolExecutor(max_workers=6)


STOP = set("""a an the is are was were be been do does did can could will would should what when where which who whom how why of for on in at to from
with about into over under this that these those it its my our your their me us we you i there here any some all more most next last new
today tomorrow tonight please tell show give list find get have has had am pm""".split())
TOKEN = re.compile(r"[a-z0-9]+(?:[.'-][a-z0-9]+)*")


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP and len(t) > 1]


def stokens(text: str) -> list[str]:
    """tokens() with a light stemmer: used where plurals and tenses must match (rerank, context window)."""
    return [qnorm.stem(t) for t in tokens(text)]


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


def _shingles(body: str, k: int = 5) -> set:
    w = body.split()
    return {" ".join(w[i:i + k]) for i in range(max(0, len(w) - k + 1))}


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """Drop lower-ranked rows whose text repeats a higher-ranked one (the same document indexed under two URLs or titles, even when the
    copies were cut into chunks at different places). Exact: same first and last 200 characters and a similar length. Fuzzy: 70% of the
    shorter row's 5-word shingles already appear in one kept row. Structured rows (calendar, links, facts) are never fuzzy-dropped."""
    seen, out, kept = {}, [], []
    for r in rows:
        body = re.sub(r"[^a-z0-9]+", " ", r["content"].split("\n\n", 1)[-1].lower()).strip()
        key = (body[:200], body[-200:])
        if key in seen and 0.8 < (len(body) + 1) / (seen[key] + 1) < 1.25:
            continue
        sh = _shingles(body)
        src = (r.get("metadata") or {}).get("source", "")
        if len(sh) >= 25 and src in OLD_COPY_SOURCES and any(len(sh & k) / min(len(sh), len(k)) >= 0.7 for k in kept if k):
            continue
        seen[key] = len(body)
        kept.append(sh if src in OLD_COPY_SOURCES else set())
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



# ---------------------------------------------------------------------------------------------- audience division (US / MS / LS)
DIV_RX = {"LS": r"lower school", "MS": r"middle school", "US": r"upper school|high school"}
DIV_NAME = {"US": "Upper School", "MS": "Middle School", "LS": "Lower School"}
_Q_LS = re.compile(r"\b(lower school|kindergarten|pre-?k|(?:1st|2nd|3rd|4th|5th|first|second|third|fourth|fifth) grade|(?:1st|2nd|3rd|4th|5th)[- ]graders?)\b|(?-i:\bLS\b)", re.I)
_Q_MS = re.compile(r"\b(middle school|(?:6th|7th|8th|sixth|seventh|eighth) grade|(?:6th|7th|8th)[- ]graders?|hillsborough)\b|(?-i:\bMS\b)", re.I)
_Q_US = re.compile(r"\b(upper school|high school|highschool|(?:9th|10th|11th|12th|ninth|tenth|eleventh|twelfth) grade|(?:9th|10th|11th|12th)[- ]graders?|freshm[ae]n|sophomores?|juniors?|seniors?|varsity|san mateo|college counseling|advisory \d+\.\d+)\b|(?-i:\bUS\b)", re.I)


def query_division(query: str, campus: str | None = None, grade: int | None = None) -> tuple[str, bool]:
    """-> (division 'US'|'MS'|'LS', explicit). Explicit when the question names a division, grade level or campus; otherwise the saved
    campus/grade, else the Upper School (most likely asker), flagged not explicit."""
    hits = [d for d, rx in (("LS", _Q_LS), ("MS", _Q_MS), ("US", _Q_US)) if rx.search(query)]
    if len(hits) == 1:
        return hits[0], True
    if hits:
        return ("US" if "US" in hits else hits[0]), False        # mentions several divisions: compare, so no single audience
    if grade:
        return "US", False
    if campus == "hillsborough":
        return "MS", False
    return "US", False


def row_division(r: dict) -> str | None:
    """The division a document row is written for (from its title / section path, else from what the text itself says), or None."""
    m = r.get("metadata") or {}
    head = (m.get("title") or "") + " " + " ".join(re.findall(r"^Section: (.*)$", (r.get("content") or "")[:700], re.M))
    found = [d for d, rx in DIV_RX.items() if re.search(rx, head, re.I)]
    if len(found) == 1:
        return found[0]
    if found:
        return None
    body = r.get("content") or ""
    counts = {d: len(re.findall(rx, body, re.I)) for d, rx in DIV_RX.items()}
    top = [d for d, c in counts.items() if c]
    return top[0] if len(top) == 1 else None


def _title_group(r: dict) -> str:
    t = re.sub(r"\s+", " ", ((r.get("metadata") or {}).get("title") or "")).strip().lower()
    return "handbook" if "handbook" in t else t[:60]


def cap_per_title(rows: list[dict], limit: int, title_cap: int = TITLE_CAP, family_cap: int = FAMILY_CAP) -> list[dict]:
    """Keep the ranking order but allow at most `title_cap` rows per document title and `family_cap` rows from the handbook (which is
    indexed under several titles), so duplicate copies and one long document cannot fill the whole context."""
    out, n = [], {}
    top = max((r.get("rerank", 0.0) for r in rows), default=0.0)
    for r in rows:
        g = _title_group(r)
        cap = family_cap if g == "handbook" else title_cap
        strong = top > 0 and r.get("rerank", 0.0) >= 0.85 * top      # a row that is nearly as relevant as the best one is never dropped for being a 3rd/4th of its title
        if n.get(g, 0) >= cap and not strong and (r.get("metadata") or {}).get("source") not in ("link_registry", "school_facts"):
            continue
        n[g] = n.get(g, 0) + 1
        out.append(r)
        if len(out) >= limit:
            break
    return out


BODY_MAX = 1300         # characters of a document body shown to the model (chunks are at most 1,600; the median is under 900)


def window(content: str, q_terms: set[str], body_max: int = BODY_MAX) -> str:
    """The header lines (Title / Section / Source) plus the part of the body that matters: the whole body when it fits, otherwise a
    slice of the original text made of whole sentences / lines / list items around the best matches for the question, instead of
    blindly cutting at a fixed length. Long run-on lists (a clubs outline) are split at commas so a match deep inside one still shows."""
    head, sep, body = content.partition("\n\n")
    if not sep:
        head, body = "", content
    if len(body) <= body_max:
        return content
    cuts = [0] + [m.end() for m in re.finditer(r"(?<=[.!?:;])\s+|\n+|,\s+|\s+-\s+", body)] + [len(body)]
    spans = [(a, b) for a, b in zip(cuts, cuts[1:]) if b > a]
    q_terms = {qnorm.stem(t) for t in q_terms}
    scores = [sum(qnorm.idf(t) for t in set(stokens(body[a:b])) & q_terms) for a, b in spans]
    best = max(range(len(spans)), key=lambda i: scores[i]) if any(scores) else 0
    lo = hi = best
    while True:                          # grow around the best piece, alternating after / before, within the budget
        grew = False
        for nxt in (hi + 1, lo - 1):
            if 0 <= nxt < len(spans):
                a = spans[min(lo, nxt)][0]
                b = spans[max(hi, nxt)][1]
                if b - a <= body_max:
                    lo, hi = min(lo, nxt), max(hi, nxt)
                    grew = True
        if not grew:
            break
    if lo <= 1:                          # the match is near the start: keep the start
        lo = 0
        while hi + 1 < len(spans) and spans[hi + 1][1] - spans[0][0] <= body_max:
            hi += 1
    text = body[spans[lo][0]:spans[hi][1]].strip()
    text = ("... " if lo > 0 else "") + text[:body_max + 60] + (" ..." if hi < len(spans) - 1 else "")
    return (head + "\n\n" if head else "") + text


class Retriever:
    def __init__(self, get_supabase):
        self.sb = get_supabase
        self.embed_cache = TTLCache(max_items=2000, ttl=24 * 3600)
        self.calendar_cache = TTLCache(max_items=256, ttl=600)
        self._fts_off = False
        self._fts_pause_until = 0.0

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
        for attempt in (0, 1):          # a connection the server closed between requests works on the second try
            try:
                r = self.sb().rpc(settings.MATCH_RPC, {"query_embedding": qvec, "match_threshold": VECTOR_THRESHOLD,
                                                       "match_count": CANDIDATES}).execute()
                return r.data or []
            except Exception as e:
                if attempt or "disconnect" not in f"{type(e).__name__} {e}".lower() and "closed" not in str(e).lower() and "reset" not in str(e).lower():
                    raise
        return []

    def fts_search(self, query: str, rare: list[str] | None = None) -> list[dict]:
        """Keyword candidates from Postgres full-text search (RPC search_chunks_fts, see supabase/fts.sql). They catch
        rare words (names, room numbers, event titles) that embeddings blur. Two passes: the broad OR query, and a second
        one on only the question's rarest words (names / acronyms such as Priory, Guild, HoCo, AMC), so a proper noun is not
        outvoted by the common words in the question. Silently disabled if the RPC is missing."""
        if not FTS_ENABLED or self._fts_off or time.time() < self._fts_pause_until:
            return []
        terms = [t for t in dict.fromkeys(re.findall(r"[a-z0-9]{3,}", query.lower())) if t not in STOP][:12]
        if not terms:
            return []
        out: dict[str, dict] = {}

        def call(query_text: str, n: int):
            for attempt in (0, 1):          # one retry: a reused connection that the server closed ("Server disconnected") works on the second try
                try:
                    return self.sb().rpc("search_chunks_fts", {"query_text": query_text, "match_count": n}).execute()
                except Exception as e:
                    if attempt or re.search(r"does not exist|could not find the function|PGRST202|42883", str(e), re.I):
                        raise
            return None
        try:
            r = call(" | ".join(terms), FTS_CANDIDATES)
            for row in r.data or []:
                out[row["chunk_id"]] = row
            rare = [t for t in (rare or []) if re.fullmatch(r"[a-z0-9]{3,}", t)]
            if rare:
                r2 = call(" | ".join(rare), FTS_RARE_CANDIDATES)
                for row in r2.data or []:
                    row = out.setdefault(row["chunk_id"], row)
                    row["fts_rare"] = True
                # the rarest word AND the next most distinctive word: a long email that mentions "Priory" once among many other
                # words is not in the top hits for "priory" alone, but it is the only chunk with "priory" and "tennis" together
                pair = [t for t in qnorm.rare_terms(query, STOP, max_df=600, limit=4) if re.fullmatch(r"[a-z0-9]{3,}", t)]
                if len(pair) >= 2:
                    r3 = call(f"{pair[0]} & ({' | '.join(pair[1:])})", FTS_RARE_CANDIDATES)
                    for row in r3.data or []:
                        row = out.setdefault(row["chunk_id"], row)
                        row["fts_rare"] = row["fts_pair"] = True
            return list(out.values())
        except Exception as e:
            msg = str(e)
            if re.search(r"does not exist|could not find the function|PGRST202|42883", msg, re.I):
                self._fts_off = True        # the function is not installed: vector-only for the life of the process
            else:
                self._fts_pause_until = time.time() + 30      # a timeout or hiccup pauses keyword search for 30 s instead of switching it off for good
            print(f"fts unavailable: {msg[:120]}", flush=True)
            return list(out.values())

    def rerank(self, query: str, rows: list[dict], today: date | None = None, division: tuple[str, bool] | None = None,
               rare: list[str] | None = None) -> list[dict]:
        """Hybrid score = vector similarity + lexical overlap (idf-weighted, title/section boosted), both min-max
        normalised over the candidates. Cheap, no model, and it fixes exact-wording misses that embeddings blur."""
        if len(rows) < 2:
            return rows
        q = set(stokens(query))
        docs = []
        for r in rows:
            m = r.get("metadata") or {}
            head = f"{m.get('title', '')} {r['content'].split(chr(10) + chr(10), 1)[0]}"
            body = stokens(r["content"][:1800])
            docs.append((body, set(stokens(head))))
        n = len(rows)
        df = {t: sum(1 for body, _ in docs if t in set(body)) for t in q}
        # local idf (rarity among these candidates) blended with global idf (rarity in the whole index, qnorm.vocab) so a word that is
        # common across the school's documents but happens to be rare among 40 candidates does not dominate
        idf = {t: 0.5 * math.log(1 + n / (1 + df[t])) + (0.5 * qnorm.idf(t) / 2.5 if qnorm.vocab() else 0.5 * math.log(1 + n / (1 + df[t]))) for t in q}
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
            if m.get("source") in ("calendar_day", "school_day") and not DATE_HINT.search(query):
                r["rerank"] -= 0.25                # a day record for a question that names no date is noise (it only matches a stray word)
            if MONEY_Q.search(query) and re.search(r"\$\s?\d", r["content"]):
                r["rerank"] += 0.20                # "how much is tuition / the fee": the chunk with the amounts, not the prose about affordability
            if m.get("source") == "school_clubs" and CLUB_Q.search(query):
                r["rerank"] += 0.10                # the 2026-27 clubs workbook beats older club-catalog copies
            if rare:
                words = set(stokens(f"{m.get('title', '')} {r['content']}"))
                r["rerank"] += RARE_BOOST * sum(1 for t in rare if qnorm.stem(t) in words) / len(rare)     # the question's names/acronyms decide ties
            if division:
                rd = row_division(r)
                if rd:
                    r["division"] = rd
                    r["rerank"] += DIV_BONUS if rd == division[0] else -(DIV_PENALTY_EXPLICIT if division[1] else DIV_PENALTY_DEFAULT)
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
               intent.division, intent.home_away, tuple(intent.keywords), tuple(intent.holidays), tuple(intent.holiday_dates))
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
        if intent.holiday_dates and not digest:
            have = {r["chunk_id"] for r in rows}
            extra = self.sb().table(settings.CHUNKS_TABLE).select("chunk_id,content,metadata").in_("metadata->>source", ["calendar_day", "school_day"]) \
                .in_("metadata->>event_date", [d.isoformat() for d in intent.holiday_dates]).limit(10).execute().data or []
            rows += [r for r in extra if r["chunk_id"] not in have]
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

    def keyword_events(self, terms: list[str], today: date) -> list[dict]:
        """Calendar rows whose title contains a name the question carries ('Nueva Rocks the Guild', 'Priory', 'Fika') when the question has
        no date or sport signal for the structured lookup. A handful of exact hits is shown as the calendar block."""
        key = ("kw", tuple(terms), today.isoformat())
        hit = self.calendar_cache.get(key)
        if hit is not None:
            return hit
        rows: list[dict] = []
        for t in terms[:2]:
            r = self.sb().table(settings.CHUNKS_TABLE).select("chunk_id,content,metadata").in_("metadata->>source", ["calendar", "calendar_sheet"]) \
                .ilike("metadata->>summary", f"%{t}%").gte("metadata->>end_date", (today - timedelta(days=30)).isoformat()).limit(40).execute().data or []
            rows += [x for x in r if x["chunk_id"] not in {y["chunk_id"] for y in rows}]
        rows = [r for r in rows if len(rows) <= 12]           # a word that matches dozens of rows is not a name
        rows.sort(key=lambda r: ((r["metadata"] or {}).get("event_date", ""), (r["metadata"] or {}).get("start_time") or ""))
        self.calendar_cache.set(key, rows, ttl=600)
        return rows

    def link_cards(self, query: str, expanded: str = "") -> list[dict]:
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
        # cards the question's own words point at (a non-pinned card about the application fee) stay next to the pinned booking / payment cards
        qtok = {t for t in tokens(expanded or query) if qnorm.df(t) <= 300 and t not in LINK_GENERIC}
        def overlap(r):
            return len(qtok & set(tokens(r["content"])))
        pinned = [r for r in rows if (r.get("metadata") or {}).get("pinned")]
        if pinned:
            rows = pinned + [r for r in rows if r not in pinned and overlap(r) >= 2]
        rows.sort(key=lambda r: (-overlap(r), not (r.get("metadata") or {}).get("pinned")))
        applying = bool(re.search(r"\b(appl\w+|admissions?|application fee|ravenna)\b", query, re.I))
        rows = [r for r in rows if not re.search(r"login|sign-?in", (r.get("metadata") or {}).get("url", ""), re.I)
                or re.search(r"login|sign[- ]?in", query, re.I) or (applying and overlap(r) >= 2)]
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
        division = query_division(resolved, campus, valid_grade(grade))
        rare = qnorm.rare_terms(resolved, STOP)
        timings = {}
        dep_errors = []         # analytics only: which dependency failed and how (never shown to the visitor)

        def _dep(dep: str, e: Exception):
            n = type(e).__name__.lower()
            dep_errors.append({"dep": dep, "kind": "timeout" if "timeout" in n else "connection" if "connect" in n else "error", "err": type(e).__name__[:40]})

        def vec_task():
            t = time.time()
            qv = self.embed(search_q)
            timings["embed_ms"] = round((time.time() - t) * 1000)
            t = time.time()
            rows = self.vector_search(qv)
            timings["vector_ms"] = round((time.time() - t) * 1000)
            return rows

        # names the question carries in capitals ("Nueva Rocks the Guild", "Priory", "HoCo") that no date/sport signal covers
        orig_words = re.findall(r"[A-Za-z][A-Za-z0-9']+", resolved)
        kw_terms = [w.lower() for i, w in enumerate(orig_words) if i > 0 and not w.islower() and w.lower() in set(rare)][:2]

        def cal_task():
            nonlocal intent
            t = time.time()
            if not intent:
                rows = self.keyword_events(kw_terms, today) if kw_terms else []
                if rows:
                    intent = CalendarIntent(today, today + timedelta(days=365), keywords=list(kw_terms), label="events matching " + " / ".join(kw_terms))
                    timings["calendar_ms"] = round((time.time() - t) * 1000)
                return rows
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
        f_vec, f_cal, f_links, f_fts = _pool.submit(vec_task), _pool.submit(cal_task), _pool.submit(self.link_cards, resolved, search_q), _pool.submit(self.fts_search, search_q, rare)
        vec_rows, degraded = [], False
        try:
            vec_rows = f_vec.result(timeout=VECTOR_BUDGET_S)
        except Exception as e:        # embedding function 546/5xx, slow database: carry on with the structured lookups
            degraded = True
            _dep("embedding" if "embed_ms" not in timings else "vector", e)
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
            _dep("links", e)
            print(f"link lookup failed: {e}", flush=True)
        try:
            events = f_cal.result(timeout=12)
        except Exception as e:
            _dep("calendar", e)
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
        # calendar chunks come mostly from the structured lookup; a vector-found calendar / day row that the structured lookup did not
        # return (a second date in the same question, "when do classes resume") is kept, but never twice
        event_ids = {e["chunk_id"] for e in events}
        rows = [r for r in rows if r["chunk_id"] not in event_ids]

        t = time.time()
        ranked = dedupe_rows(self.rerank(search_q, rows, today, division, rare))
        # protected keyword slot: the best row that contains ALL the question's rarest words (a name, an acronym: Priory, Guild, HoCo)
        # is always shown, even when long handbook chunks outscore it
        protect = []
        names = [t for t in rare if qnorm.df(t) <= 25]        # real names / acronyms only (a word in 58 chunks, like "freshmen", is not worth a protected slot)
        if names and len(rare) <= 4:
            need = {names[0]}
            for key in ("fts_pair", "fts_rare"):          # first the row that has the rarest word together with another question word, then the best rare-word row
                for r in ranked:
                    m = r.get("metadata") or {}
                    if r.get(key) and r not in protect and need <= set(tokens(f"{m.get('title', '')} {r['content']}")):
                        protect.append(r)
                        break
        timings["rerank_ms"] = round((time.time() - t) * 1000)
        link_rows = link_rows[:3]
        link_ids = {l["chunk_id"] for l in link_rows}
        ranked = [d for d in ranked if d["chunk_id"] not in link_ids]
        if protect:
            pids = {p_["chunk_id"] for p_ in protect}
            ranked = protect + [d for d in ranked if d["chunk_id"] not in pids]
        docs = link_rows + cap_per_title(ranked, top_k)
        timings["retrieve_ms"] = round((time.time() - t0) * 1000)
        ent_ids = {e["chunk_id"] for e in ents}
        if intent and intent.label == "the next two weeks" and any(p["chunk_id"] == "advisory-lines" for p in pinned):
            events, intent = [], None      # "advisory 11.1" is not a date question; the two-week digest would only add noise
        if intent and events and ents and intent.category == "athletics" and not (intent.sport or intent.kind or intent.date_given or intent.holidays) \
                and any(re.search(r"\bteams?\b", ((e.get("metadata") or {}).get("course") or ""), re.I) for e in ents):
            events, intent = [], None      # "Biology Research Teams 2" is a course, not a sports-team question: no team overview
        pin_ids = {p["chunk_id"] for p in pinned}
        docs = [d for d in docs if d["chunk_id"] not in ent_ids | pin_ids]
        return {"pinned": pinned, "docs": docs, "events": events, "intent": intent, "timings": timings, "candidates": len(rows), "entities": ents, "degraded": degraded, "resolved": standalone, "dep_errors": dep_errors,
                "division": division, "rare": rare}


def fmt_event(r: dict, detail: bool = False) -> str:
    m = r.get("metadata") or {}
    if m.get("source") in ("calendar_day", "school_day"):
        return r["content"].split("\n\n", 1)[-1]
    when = m.get("when", m.get("event_date", ""))
    time_s = "all day" if m.get("all_day") else m.get("time", "")
    loc = f" @ {m['location']}" if m.get("location") else ""
    desc = re.sub(r"\s+", " ", m.get("description") or "").strip()[:260] if detail else ""
    return f"- {when}, {time_s}: {m.get('summary', '')}{loc}" + (f" - Details: {desc}" if desc else "")


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


def select_events(events: list[dict], intent, query: str, max_events: int = 14) -> list[dict]:
    """Which calendar rows to show when there are more than fit: the ones whose summary matches the question's words come first
    (the 7th-grade Shakespeare performance is not the first of 31 arts events by date), then the earliest dates; shown in date order.
    'Next game' questions skip rows that are not games (First Day of Practice, tryouts) before taking the first few."""
    if intent and intent.next_only and intent.kind == "game":
        games = [e for e in events if (e.get("metadata") or {}).get("kind") == "game"] or events
        return games[:3]
    if len(events) <= max_events:
        return events
    q = set(tokens(query)) | {k.lower() for k in (intent.keywords if intent else [])}
    q -= {"performance", "performances", "event", "events", "school", "nueva", "grade"} if len(q) > 2 else set()

    def score(e):
        m = e.get("metadata") or {}
        words = set(tokens(f"{m.get('summary', '')} {m.get('description', '') or ''} {m.get('location', '') or ''}"))
        return sum(qnorm.idf(t) for t in q & words)
    scored = [(score(e), i, e) for i, e in enumerate(events)]
    top = sorted((x for x in scored if x[0] > 0), key=lambda x: (-x[0], x[1]))[:max_events]
    chosen = {id(x[2]) for x in top}
    for _, i, e in scored:                      # fill the rest in date order
        if len(chosen) >= max_events:
            break
        chosen.add(id(e))
    return [e for e in events if id(e) in chosen]


def build_context(result: dict, max_events: int = 14, query: str = "") -> tuple[str, list[dict]]:
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
        shown = select_events(events, intent, query or result.get("resolved") or "", max_events)
        more = f"\n(+{len(events) - len(shown)} more matching events not shown)" if len(events) > len(shown) else ""
        label = f"{intent.label}" if intent else ""
        parts.append(f"SCHOOL CALENDAR ({label}, sorted by date; today is accurate to this list):\n"
                     + "\n".join(fmt_event(e, detail=len(shown) <= 4) for e in shown) + more)
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
    q_terms = set(tokens(query or result.get("resolved") or "")) | set(result.get("rare") or [])
    div = result.get("division")
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
        rd = d.get("division") or row_division(d)
        if rd and m.get("source") not in ("calendar", "calendar_day", "school_day", "link_registry"):
            tag = f"[{DIV_NAME[rd]} document] " + tag          # labelled so the model answers for the right division
        parts.append(tag + window(d["content"], q_terms))      # a window around the answer, not a blind cut: the LLM plan is token-rate limited
        sources.append({"chunk_id": d["chunk_id"], "title": m.get("title"), "url": m.get("source_url") if str(m.get("source_url", "")).startswith("http") else None,
                        "similarity": round(d.get("similarity", 0), 3), "type": m.get("source", "doc")})
    present = {d.get("division") or row_division(d) for d in result["docs"]} - {None}
    if div and parts and (present - {div[0]}):
        label, explicit = DIV_NAME[div[0]], div[1]
        parts.insert(0, f"AUDIENCE: the question is about the {label}" + ("." if explicit else
                     " (assumed: the asker did not say which division). Answer for the " + label + "; if a document written for another division says something different, add one short sentence naming that difference."))
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
