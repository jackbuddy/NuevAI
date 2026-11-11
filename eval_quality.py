"""Quality eval for NuevAI: deterministic grading of answers against a verified golden set (eval/quality_set.json).

    python eval_quality.py --mode retrieval                      # FREE: is the expected fact in the context the model would see?
    python eval_quality.py --mode llm --sample 40 --seed 1       # real answers (spends Groq quota), cached by prompt hash
    python eval_quality.py --mode llm --category lunch,clubs_activities --token-budget 60000
    python eval_quality.py --mode llm --ids ath-05,gm-02         # specific questions
    python eval_quality.py --mode llm --only-failed data/eval/quality_<ts>.json
    python eval_quality.py --mode llm --model openai/gpt-oss-20b  # alternate model: results are labelled NON-PRIMARY
    python eval_quality.py --self-test                           # grader unit tests (no network, no tokens)

Everything runs in-process through query_server._prepare (the same retrieval + prompt assembly as production), so no server
is needed; `--backend http://localhost:8780` instead POSTs the question to a running local backend (answers only; context is
still retrieved in-process for the groundedness check). Ids sent to a backend are `test-quality-<n>`.

Labels (exactly one per answer): correct | correct_overhedged | unnecessary_refusal | partial | wrong | hallucinated.
Not graded (counted separately, never as a pass): ungraded_quota, stale_dynamic, error.
Launch bar: correct (incl. overhedged) >= 95%, hallucinated == 0, unnecessary_refusal < 3%. Exit code 1 below the bar.

The grader is rule based (no LLM): expect/forbid patterns (eval_golden helpers), a hedge/refusal phrase detector with severity by
behavior, a groundedness check (dates, times, phones, emails, URLs, rooms, scores, money, large numbers, invented names) against the
retrieved context + question + history + index vocabulary, and behavior rules (no_such_thing, past_event, ambiguous, partial, redirect_ok, safety).
"""
import argparse
import collections
import hashlib
import json
import os
import random
import re
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
os.environ.setdefault("ANALYTICS_DISABLED", "1")
os.environ.setdefault("WARMUP_DISABLED", "1")
os.environ.setdefault("ANSWER_L2_DISABLED", "1")
sys.path.insert(0, str(HERE))
from eval_golden import normalize, pattern      # noqa: E402  (shared match helpers)

PACIFIC = ZoneInfo("America/Los_Angeles")
SET_PATH = HERE / "eval" / "quality_set.json"
OUT_DIR = HERE / "data" / "eval"
CACHE_DIR = OUT_DIR / "cache"
LABELS = ["correct", "correct_overhedged", "unnecessary_refusal", "partial", "wrong", "hallucinated"]
NOT_GRADED = ["ungraded_quota", "stale_dynamic", "error"]
BAR = {"correct": 0.95, "hallucinated": 0, "unnecessary_refusal": 0.03}
PRIMARY_MODEL = "openai/gpt-oss-120b"

# ------------------------------------------------------------------------------------------------ hedge / refusal detector
from refusal import REFUSAL, REDIRECT, TAIL_POINTER, PRECISE_POINTER, CONFIDENT_NO, LUNCH_NOTE_RX, hedge_info   # noqa: E402  (one source of truth, shared with the server)
UPCOMING = re.compile(r"\b(upcoming|will (take place|be held|happen|be on|occur)|is scheduled for|are scheduled for|coming up|next (one )?(is|will))\b", re.I)
PAST_MARK = re.compile(r"\b(was|were|already|took place|has (already )?(passed|happened|ended|taken place)|have (already )?(passed|happened)|past|ended|happened|earlier this)\b", re.I)
DECLINE = re.compile(r"\b(can't|cannot|can not|won't|unable|not able|don't|do not|not (something|allowed|permitted)|decline|only (help|answer)|private|personal)\b", re.I)
LIMIT_STATEMENT = re.compile(REFUSAL.pattern + r"|\b(not (listed|published|available|posted|included|specified|yet)|still being updated|draft|no (published|listed))\b", re.I)



# ------------------------------------------------------------------------------------------------ groundedness
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
MON_RX = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE_RANGE = re.compile(MON_RX + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*(?:-|to|through|until)\s*(?:" + MON_RX + r"\.?\s+)?(\d{1,2})(?:st|nd|rd|th)?\b(?!\s*(?::|am\b|pm\b|a\.m|p\.m))", re.I)
DATE_ONE = re.compile(MON_RX + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!\s*(?:am|pm|a\.m|p\.m|:|\d))", re.I)
DATE_DAYFIRST = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + MON_RX + r"\b", re.I)
DATE_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b")
DATE_ISO = re.compile(r"\b20\d\d-(\d\d)-(\d\d)\b")
TIME_RX = re.compile(r"\b(\d{1,2}):(\d{2})\s*(?:am|pm|a\.m\.|p\.m\.)?|\b(\d{1,2})\s*(?:am|pm|a\.m\.|p\.m\.)", re.I)
PHONE_RX = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})(?!\d)")
EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RX = re.compile(r"https?://[^\s)\]>\"']+")
ROOM_RX = re.compile(r"\broom\s+#?([A-Za-z]?\d{2,4}[A-Za-z]?)\b", re.I)
SCORE_RX = re.compile(r"(?<![\d:/.-])(\d{1,3})\s?-\s?(\d{1,3})(?![\d:/-])(?!\s*(?:am|pm|a\.m|p\.m))")
MONEY_RX = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
BIGNUM_RX = re.compile(r"(?<![\w$/:.-])(\d{2,4}(?:,\d{3})*)(?![\w:/%.-]|\s?(?:am|pm|a\.m|p\.m))")
CAP_WORD = re.compile(r"\b[A-Z][a-z]{3,}\b")
SAFE_CAPS = set("""the this that there these those here what when where which who why how and but for with from into over under about after before also then than
monday tuesday wednesday thursday friday saturday sunday january february march april june july august september october november december
nueva school upper middle lower campus san mateo hillsborough california block blocks advisory advisor advisors room rooms lunch break day days today tomorrow
please note yes none sorry unfortunately however generally typically usually currently often sometimes both either each every most some many several other another
varsity basketball soccer volleyball tennis golf swimming swim track field cross country football flag baseball softball boys girls coed home away game games practice practices
club clubs meets meeting after school faculty student students teacher teachers class classes course courses semester fall spring winter summer yearlong term open grade grades
here's i'll you'll they'll that's it's let's don't doesn't isn't aren't can't won't""".split())


def _ctx_dates(text: str, base_year: int) -> set[tuple[int, int]]:
    out = set()
    t = normalize(text)
    for m in DATE_RANGE.finditer(t):
        m1 = MONTHS[m.group(1)[:3].lower()]
        m2 = MONTHS[m.group(3)[:3].lower()] if m.group(3) else m1
        d1, d2 = int(m.group(2)), int(m.group(4))
        if m1 == m2 and d1 <= d2 <= 31:
            out.update((m1, d) for d in range(d1, d2 + 1))
        else:
            out.update([(m1, d1), (m2, d2)])
    for m in DATE_ONE.finditer(t):
        out.add((MONTHS[m.group(1)[:3].lower()], int(m.group(2))))
    for m in DATE_DAYFIRST.finditer(t):
        out.add((MONTHS[m.group(2)[:3].lower()], int(m.group(1))))
    for m in DATE_NUM.finditer(t):
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= 12 and 1 <= b <= 31:
            out.add((a, b))
    for m in DATE_ISO.finditer(t):
        out.add((int(m.group(1)), int(m.group(2))))
    return out


def _times(text: str) -> set[tuple[int, int]]:
    out = set()
    for m in TIME_RX.finditer(normalize(text)):
        if m.group(1) is not None:
            h, mi = int(m.group(1)), int(m.group(2))
        else:
            h, mi = int(m.group(3)), 0
        if 0 <= h <= 24 and mi < 60:
            out.add((h % 12, mi))
    return out


def _durations(text: str) -> set[int]:
    """minutes of every 'a:bc-d:ef' range in the context (a model may say '75 minutes' for 8:55-10:10)."""
    out = set()
    for m in re.finditer(r"(\d{1,2}):(\d{2})\s*(?:am|pm)?\s*(?:-|to)\s*(\d{1,2}):(\d{2})", normalize(text), re.I):
        a, b = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
        out.add((b - a) % 720)
    return out


def derived_dates(today: date) -> set[tuple[int, int]]:
    return {((today + timedelta(days=k)).month, (today + timedelta(days=k)).day) for k in range(-1, 8)}


_VOCAB: set[str] | None = None


def index_vocab() -> set[str]:
    """Every capitalised-or-not word in the index (cached on disk as data/eval/index_vocab.json); a name found nowhere in it is invented."""
    global _VOCAB
    if _VOCAB is not None:
        return _VOCAB
    path = OUT_DIR / "index_vocab.json"
    if path.exists() and time.time() - path.stat().st_mtime < 86400:
        _VOCAB = set(json.load(open(path)))
        return _VOCAB
    import query_server as qs
    words, off, sb = set(), 0, qs.get_supabase()
    while True:
        rows = sb.table("document_chunks_v2").select("content").range(off, off + 999).execute().data
        for r in rows:
            words.update(re.findall(r"[a-z][a-z'-]{2,}", normalize(r["content"]).lower()))
        off += 1000
        if len(rows) < 1000:
            break
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json.dump(sorted(words), open(path, "w"))
    _VOCAB = words
    return words


def grounding(answer: str, context: str, question: str, history: list[dict], today: date, vocab: set[str] | None = None, extra: str = "") -> list[dict]:
    """Specifics in the answer that appear neither in the retrieved context nor in the question/history. Each flag: {type, token, why}."""
    a = normalize(answer)
    corpus = normalize(context + "\n" + question + "\n" + "\n".join(h.get("content", "") for h in history) + "\n" + extra)
    corpus_l = corpus.lower()
    digits = re.sub(r"\D", "", corpus)
    flags = []
    cdates = _ctx_dates(corpus, today.year) | derived_dates(today)
    for rx, kind in ((DATE_RANGE, "range"), (DATE_ONE, "one"), (DATE_DAYFIRST, "dayfirst")):
        for m in rx.finditer(a):
            if kind == "range":
                m1 = MONTHS[m.group(1)[:3].lower()]
                m2 = MONTHS[m.group(3)[:3].lower()] if m.group(3) else m1
                pts = [(m1, int(m.group(2))), (m2, int(m.group(4)))]
            elif kind == "one":
                pts = [(MONTHS[m.group(1)[:3].lower()], int(m.group(2)))]
            else:
                pts = [(MONTHS[m.group(2)[:3].lower()], int(m.group(1)))]
            for p in pts:
                if p not in cdates and 1 <= p[1] <= 31:
                    flags.append({"type": "date", "token": m.group(0), "why": "date not in context"})
    for m in DATE_NUM.finditer(a):
        p = (int(m.group(1)), int(m.group(2)))
        if 1 <= p[0] <= 12 and 1 <= p[1] <= 31 and p not in cdates:
            flags.append({"type": "date", "token": m.group(0), "why": "date not in context"})
    ctimes = _times(corpus)
    for m in TIME_RX.finditer(a):
        h, mi = (int(m.group(1)), int(m.group(2))) if m.group(1) is not None else (int(m.group(3)), 0)
        if 0 <= h <= 24 and mi < 60 and (h % 12, mi) not in ctimes:
            if m.group(1) is None and re.search(rf"(?<![\d:.$]){h}(?![\d:%])", corpus):
                continue          # "6 PM" when the context writes the hour as a bare number ("Fridays 4-6")
            flags.append({"type": "time", "token": m.group(0).strip(), "why": "time not in context"})
    for m in PHONE_RX.finditer(a):
        d = m.group(1) + m.group(2) + m.group(3)
        if d not in digits and (m.group(2) + m.group(3)) not in digits:
            flags.append({"type": "phone", "token": m.group(0), "why": "number not in context"})
    for m in EMAIL_RX.finditer(a):
        if m.group(0).lower().rstrip(".") not in corpus_l:
            flags.append({"type": "email", "token": m.group(0), "why": "email not in context"})
    for m in URL_RX.finditer(a):
        u = m.group(0).rstrip(".,;:").rstrip("/")
        if u.lower() not in corpus_l and u.lower().replace("https://", "").replace("http://", "").replace("www.", "") not in corpus_l.replace("https://", "").replace("http://", "").replace("www.", ""):
            flags.append({"type": "url", "token": m.group(0), "why": "url not in context"})
    for m in ROOM_RX.finditer(a):
        if m.group(1).lower() not in corpus_l:
            flags.append({"type": "room", "token": m.group(0), "why": "room not in context"})
    for m in MONEY_RX.finditer(a):
        if m.group(1).replace(",", "") not in corpus.replace(",", ""):
            flags.append({"type": "money", "token": m.group(0), "why": "amount not in context"})
    durs = _durations(corpus)
    a_nodate = DATE_ONE.sub(" ", DATE_RANGE.sub(" ", a))
    for m in SCORE_RX.finditer(a_nodate):
        tok = m.group(0)
        lo, hi = int(m.group(1)), int(m.group(2))
        before = a_nodate[max(0, m.start() - 16):m.start()].lower()
        if re.search(r"(grades?|gr\.?|blocks?|years?|ages?|days?|weeks?|pages?|rooms?|levels?|sections?|steps?|items?|chapters?)\s*$", before) and lo < hi <= 12:
            continue                      # a range of grades/blocks, not a score
        if re.sub(r"\s", "", tok) not in re.sub(r"\s", "", corpus):
            if lo < hi <= 12 and re.search(rf"\b{lo}(st|nd|rd|th)?\s*[-to]+\s*{hi}(st|nd|rd|th)?\b", corpus):
                continue                  # "10th - 12th" in the context
            flags.append({"type": "score", "token": tok, "why": "score/range not in context"})
    cleaned = TIME_RX.sub(" ", PHONE_RX.sub(" ", URL_RX.sub(" ", EMAIL_RX.sub(" ", MONEY_RX.sub(" ", a_nodate)))))
    cleaned = SCORE_RX.sub(" ", DATE_NUM.sub(" ", DATE_ISO.sub(" ", cleaned)))
    for m in BIGNUM_RX.finditer(cleaned):
        n = m.group(1).replace(",", "")
        if n.isdigit() and (int(n) in range(1990, 2036) or int(n) <= 12 or int(n) in durs):
            continue
        if m.group(1) not in corpus and n not in corpus.replace(",", ""):
            after = cleaned[m.end():m.end() + 12].lower()
            if re.match(r"\s*(min|minute|hour|day|week|word|item|club|class)", after) and int(n) <= 400:
                continue          # a count/duration the model may have computed: soft, not flagged
            flags.append({"type": "number", "token": m.group(0), "why": "number not in context"})
    if vocab is not None:
        seen = set()
        for sent in re.split(r"(?<=[.!?\n])\s+", a):
            words = CAP_WORD.findall(sent)
            for i, w in enumerate(words):
                lw = w.lower()
                if lw in SAFE_CAPS or lw in seen or lw in corpus_l or lw in vocab:
                    continue
                seen.add(lw)
                flags.append({"type": "name", "token": w, "why": "word appears nowhere in the index"})
    return flags


# ------------------------------------------------------------------------------------------------ grading
def expect_check(answer: str, expect: list, forbid: list) -> tuple[list[str], list[str]]:
    a = normalize(answer)
    missing, hits = [], []
    for e in expect:
        alts = e if isinstance(e, list) else [e]
        if not any(pattern(x).search(a) for x in alts):
            missing.append(" | ".join(alts)[:80])
    for f in forbid:
        m = pattern(f).search(a)
        if m:
            hits.append(m.group(0)[:60])
    return missing, hits


def grade(item: dict, answer: str, context: str, today: date, expect=None, forbid=None, vocab=None, extra: str = "") -> dict:
    """-> {label, reasons[], missing[], forbid_hits[], hedge{}, flags[]}. Exactly one label."""
    beh = item["behavior"]
    expect = item["expect"] if expect is None else expect
    forbid = item["forbid"] if forbid is None else forbid
    missing, fhits = expect_check(answer, expect, forbid)
    hedge = hedge_info(answer)
    flags = grounding(answer, context, item["q"], item.get("history") or [], today, vocab, extra)
    hard_flags = [f for f in flags if f["type"] != "name"]
    name_flags = [f for f in flags if f["type"] == "name"]
    reasons = []
    n_exp = len(expect)
    got = n_exp - len(missing)
    facts_ok = not missing
    a = normalize(answer)
    res = lambda label: {"label": label, "reasons": reasons, "missing": missing, "forbid_hits": fhits, "hedge": hedge, "flags": flags}
    if not a.strip():
        reasons.append("empty answer")
        return res("wrong")
    if hard_flags or name_flags:
        reasons.append("ungrounded: " + ", ".join(f"{f['type']}:{f['token']}" for f in (hard_flags + name_flags)[:6]))
        return res("hallucinated")
    if fhits:
        reasons.append("forbidden: " + ", ".join(fhits[:3]))
        return res("wrong")
    refusalish = hedge["refusal_in_head"] or (hedge["n_refusal"] > 0 and not facts_ok)
    if beh == "safety":
        if facts_ok:
            return res("correct")
        reasons.append("missing safe-decline: " + "; ".join(missing[:2]))
        return res("wrong" if not hedge["n_refusal"] and not DECLINE.search(a) else "partial")
    if beh == "redirect_ok":
        if facts_ok or hedge["precise_pointer"] or hedge["n_redirect"]:
            return res("correct")
        reasons.append("no precise redirect")
        return res("partial")
    if beh == "no_such_thing":
        if hedge["refusal_in_head"] or (not hedge["confident_no_head"] and hedge["n_refusal"]):
            reasons.append("answered 'I don't know' instead of a confident no")
            return res("unnecessary_refusal")
        if not hedge["confident_no_head"] and not CONFIDENT_NO.search(a):
            reasons.append("no confident 'no'")
            return res("wrong")
        if not facts_ok:
            reasons.append("confident no but missing nearest true thing: " + "; ".join(missing[:2]))
            return res("partial")
        return res("correct_overhedged" if hedge["n_refusal"] else "correct")
    if beh == "past_event":
        if UPCOMING.search(a) and not PAST_MARK.search(a):
            reasons.append("presents a past event as upcoming")
            return res("wrong")
        if not facts_ok:
            if refusalish:
                reasons.append("refused instead of giving the date")
                return res("unnecessary_refusal")
            reasons.append("missing: " + "; ".join(missing[:2]))
            return res("partial" if got else "wrong")
        if not PAST_MARK.search(a):
            reasons.append("facts present but does not say it already happened")
            return res("partial")
        return res("correct_overhedged" if hedge["n_refusal"] else "correct")
    # answer / ambiguous / partial
    if not facts_ok:
        if refusalish or (hedge["n_refusal"] and got == 0):
            reasons.append("said it doesn't know / redirected; expected fact absent: " + "; ".join(missing[:3]))
            return res("unnecessary_refusal")
        reasons.append(("missing: " if got else "all facts missing: ") + "; ".join(missing[:3]))
        return res("partial" if got else "wrong")
    if beh == "partial":
        return res("correct" if LIMIT_STATEMENT.search(a) or hedge["n_redirect"] or hedge["n_refusal"] else "correct")
    if hedge["n_refusal"] or hedge["n_redirect"]:
        reasons.append("hedge: " + "; ".join((hedge["refusal"] + hedge["redirect"])[:3]))
        return res("correct_overhedged" if beh != "ambiguous" else "correct")
    return res("correct")


# ------------------------------------------------------------------------------------------------ dynamic resolvers
def _md(d: date) -> str:
    return rf"\b{d.strftime('%b')}[a-z]*\.?\s+{d.day}\b"


def resolve_dynamic(item: dict, today: date, sb) -> tuple[list | None, str]:
    """-> (expect list or None, note). Recomputes expectations from the live index for today's date."""
    dyn = item.get("dynamic") or {}
    r = dyn.get("resolver")
    if not r:
        return None, ""
    kind = r["type"]
    if kind == "day_type":
        d = today + timedelta(days=r.get("offset", 0))
        rows = sb.table("document_chunks_v2").select("content,metadata").eq("metadata->>source", "school_day").eq("metadata->>event_date", d.isoformat()).execute().data
        if not rows:
            return None, f"no day record for {d}"
        c = rows[0]["content"]
        if re.search(r"No classes", c):
            return [r"no (school|classes)|not in session|closed|off"], f"{d}: no classes"
        m = re.search(r"- (A'|B'|A|B) day", c)
        if m:
            lab = m.group(1)
            return ([r"\b%s['\u2019]\s*day|%s prime|%s'" % (lab[0], lab[0], lab[0])] if "'" in lab else [r"\b%s day\b|\b%s-day" % (lab, lab)]), f"{d}: {lab} day"
        if re.search(r"Full-day special event", c):
            ev = re.search(r"special event: ([^.]+)", c)
            return [re.escape(ev.group(1).split(" ")[0]) if ev else "event"], f"{d}: full-day event"
        return None, f"{d}: unparsed day record"
    if kind == "lunch":
        d = today + timedelta(days=r.get("offset", 0))
        rows = sb.table("document_chunks_v2").select("content,metadata").eq("metadata->>source", "lunch").eq("metadata->>campus", r["campus"]).eq("metadata->>event_date", d.isoformat()).execute().data
        if not rows:
            return None, f"no lunch row for {r['campus']} {d}"
        items = (rows[0].get("metadata") or {}).get("items") or re.findall(r"^- (.+)$", rows[0]["content"], re.M)
        if not items:
            return [r"no lunch|closed|weekend|not (posted|served)"], f"{d}: closed"
        dish = re.sub(r"^[^:]+:\s*", "", items[0])
        words = sorted(re.findall(r"[A-Za-z]{4,}", dish)[:4], key=len, reverse=True)[:1]
        return [re.escape(w) for w in words] or None, f"{d}: first dish {dish[:40]}"
    if kind == "next_event":
        q = sb.table("document_chunks_v2").select("content,metadata").in_("metadata->>source", ["calendar", "calendar_sheet"]).gte("metadata->>end_date", today.isoformat()).limit(1000)
        if r.get("kind"):
            q = q.eq("metadata->>kind", r["kind"])
        if r.get("category"):
            q = q.eq("metadata->>category", r["category"])
        rows = [x for x in q.execute().data if re.search(r["match"], (x["metadata"] or {}).get("summary", ""), re.I)]
        rows.sort(key=lambda x: ((x["metadata"] or {}).get("event_date", ""), (x["metadata"] or {}).get("start_time") or ""))
        if not rows:
            return None, "no upcoming event"
        m = rows[0]["metadata"]
        d = date.fromisoformat(m["event_date"])
        exp = [_md(d)] if d != today else [_md(d) + r"|today"]
        opp = re.search(r"Game\s+(.+?)\s+-\s+(Home|Away)\s*$", m.get("summary", ""))
        if opp:
            exp.append(re.escape(opp.group(1).split(" ")[0]))
        return exp, f"{m['event_date']} {m.get('summary', '')[:60]}"
    return None, "unknown resolver"


# ------------------------------------------------------------------------------------------------ context + answers
class Env:
    def __init__(self):
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env")
        import query_server as qs
        self.qs = qs

    def request(self, item: dict, n: int):
        qs = self.qs
        return qs.QueryRequest(query=item["q"], history=[qs.Turn(role=t["role"], content=t["content"]) for t in item.get("history", [])],
                               campus=item.get("campus"), anon_id=f"test-quality-{n}", session_id="test-quality-run")

    def prepare(self, item: dict, n: int) -> dict:
        """-> {kind: canned|rag|error, answer?, msgs?, context, system, result, sources}"""
        qs = self.qs
        req = self.request(item, n)
        if (canned := qs.smalltalk(req.query)) is not None:
            return {"kind": "canned", "answer": canned, "context": "", "system": "", "msgs": [], "sources": [], "result": {}}
        try:
            msgs, sources, result, now = qs._prepare(req)
        except Exception as e:
            return {"kind": "error", "error": f"{type(e).__name__}: {str(e)[:120]}", "context": "", "system": "", "msgs": [], "sources": [], "result": {}}
        system = msgs[0]["content"]
        context = system.split("CONTEXT:\n", 1)[1] if "CONTEXT:\n" in system else system
        return {"kind": "rag", "msgs": msgs, "context": context, "system": system, "result": result, "sources": sources, "req": req}


def cache_key(item: dict, prep: dict, model: str, today: date) -> str:
    system = prep["system"]
    if not item.get("dynamic"):
        system = re.sub(r"Today is [^\n]*?\(Pacific time\)\.", "Today is <DATE> (Pacific time).", system)    # static questions: a new day alone does not invalidate
    hist = json.dumps(item.get("history", []), sort_keys=True)
    return hashlib.sha256("\x1f".join([system, item["q"], hist, model]).encode()).hexdigest()


def cache_get(key: str):
    p = CACHE_DIR / f"{key}.json"
    return json.load(open(p)) if p.exists() else None


def cache_put(key: str, rec: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    json.dump(rec, open(CACHE_DIR / f"{key}.json", "w"), ensure_ascii=False)


class Quota(Exception):
    pass


class Pacer:
    """Keeps tokens/minute under the free-tier cap (8k TPM, shared with the live site) and counts tokens spent."""
    def __init__(self, tpm: int, budget: int):
        self.tpm, self.budget, self.spent, self.window = tpm, budget, 0, []

    def wait(self, est: int):
        while True:
            now = time.time()
            self.window = [(t, n) for t, n in self.window if now - t < 62]
            used = sum(n for _, n in self.window)
            if used + est <= self.tpm or not self.window:
                return
            time.sleep(max(1.0, 62 - (now - self.window[0][0])))

    def add(self, n: int):
        self.spent += n
        self.window.append((time.time(), n))


def llm_answer(env: Env, prep: dict, model: str, pacer: Pacer, log) -> dict:
    """One Groq call on the exact production prompt. Retries per-minute 429s; raises Quota on a daily limit or an exhausted budget."""
    from groq import APIConnectionError, APIStatusError
    qs = env.qs
    est = int(len(json.dumps(prep["msgs"])) / 3.4) + 700
    if pacer.budget and pacer.spent + est > pacer.budget:
        raise Quota("token budget reached")
    for attempt in range(5):
        pacer.wait(est)
        t0 = time.time()
        try:
            c = qs.groq_client.with_options(timeout=60).chat.completions.create(messages=prep["msgs"], stream=False, **qs._llm_kwargs(model))
            ans = c.choices[0].message.content or ""
            u = getattr(c, "usage", None)
            tok = (u.prompt_tokens + u.completion_tokens) if u else est
            if not ans.strip():          # same as production (_generate): one retry with a larger budget before an empty answer is reported
                pacer.add(tok)
                c = qs.groq_client.with_options(timeout=60).chat.completions.create(messages=prep["msgs"], stream=False, **qs._llm_kwargs(model, True))
                ans = c.choices[0].message.content or ""
                u = getattr(c, "usage", None)
                tok += (u.prompt_tokens + u.completion_tokens) if u else est
            pacer.add(tok)
            return {"answer": ans, "model": model, "tokens": tok, "pt": getattr(u, "prompt_tokens", None), "ct": getattr(u, "completion_tokens", None), "ms": int((time.time() - t0) * 1000)}
        except APIStatusError as e:
            txt = str(e)
            if e.status_code == 429:
                if re.search(r"per day|TPD|RPD|daily", txt, re.I):
                    raise Quota(f"daily quota: {txt[:140]}")
                wait = qs._retry_after(txt) or 20
                log(f"    429 per-minute limit; sleeping {min(wait + 1, 65):.0f}s")
                pacer.window.append((time.time(), pacer.tpm))
                time.sleep(min(wait + 1, 65))
                continue
            if e.status_code in (500, 502, 503, 504, 529):
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except APIConnectionError:
            time.sleep(5)
    raise RuntimeError("LLM unavailable after retries")


def backend_answer(base: str, item: dict, n: int) -> dict:
    import urllib.request
    body = {"query": item["q"], "history": item.get("history", []), "anon_id": f"test-quality-{n}", "session_id": "test-quality-run"}
    if item.get("campus"):
        body["campus"] = item["campus"]
    req = urllib.request.Request(base.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-nuevai-test": "1"})
    d = json.loads(urllib.request.urlopen(req, timeout=120).read())
    return {"answer": d.get("answer", ""), "model": d.get("model_used", ""), "tokens": 0}


# ------------------------------------------------------------------------------------------------ selection
def select(items: list[dict], a) -> list[dict]:
    if a.verified_only:
        items = [i for i in items if i.get("verified")]
    if a.category:
        items = [i for i in items if i["category"] in set(a.category.split(","))]
    if a.persona:
        items = [i for i in items if i["persona"] in set(a.persona.split(","))]
    if a.ids:
        want = a.ids.split(",")
        items = [i for i in items if i["id"] in want]
    if a.only_failed:
        rep = json.load(open(a.only_failed))
        bad = {r["id"] for r in rep["results"] if r.get("label") not in ("correct", "correct_overhedged")}
        items = [i for i in items if i["id"] in bad]
    if a.sample and a.sample < len(items):
        rng = random.Random(a.seed)
        pref = {c: k for k, c in enumerate((a.prefer or "").split(","))}
        by = collections.defaultdict(list)
        for i in items:
            by[i["category"]].append(i)
        for v in by.values():
            rng.shuffle(v)
        order = sorted(by, key=lambda c: (pref.get(c, 99), c))
        out = []
        while len(out) < a.sample and any(by.values()):
            for c in order:
                if by[c] and len(out) < a.sample:
                    out.append(by[c].pop())
        items = out
    return items


# ------------------------------------------------------------------------------------------------ reporting
def pct(n, d):
    return f"{100 * n / d:5.1f}%" if d else "  n/a "


def summarize(results: list[dict], mode: str, model: str) -> dict:
    graded = [r for r in results if r["label"] in LABELS]
    cnt = collections.Counter(r["label"] for r in graded)
    n = len(graded)
    # unnecessary refusals only make sense where the data covers the question (all behaviors except redirect_ok / safety)
    cover = [r for r in graded if r["behavior"] not in ("redirect_ok", "safety")]
    answered_ok = cnt["correct"] + cnt["correct_overhedged"]
    out = {"mode": mode, "model": model, "primary_model": model == PRIMARY_MODEL, "n_graded": n, "counts": dict(cnt),
           "not_graded": dict(collections.Counter(r["label"] for r in results if r["label"] in NOT_GRADED)),
           "correct_rate": answered_ok / n if n else None, "strict_correct_rate": cnt["correct"] / n if n else None,
           "overhedge_rate": cnt["correct_overhedged"] / answered_ok if answered_ok else None,
           "unnecessary_refusal_rate": (sum(1 for r in cover if r["label"] == "unnecessary_refusal") / len(cover)) if cover else None,
           "hallucination_rate": cnt["hallucinated"] / n if n else None}
    for dim in ("category", "persona", "phrasing", "behavior"):
        d = collections.defaultdict(collections.Counter)
        for r in graded:
            d[r[dim]][r["label"]] += 1
        out["by_" + dim] = {k: {"n": sum(v.values()), "correct": v["correct"] + v["correct_overhedged"], **{l: v[l] for l in LABELS}} for k, v in sorted(d.items(), key=lambda kv: str(kv[0]))}
    bar = {"correct_ge_95": (out["correct_rate"] or 0) >= BAR["correct"], "hallucinated_zero": cnt["hallucinated"] == 0,
           "unnecessary_refusal_lt_3": (out["unnecessary_refusal_rate"] if out["unnecessary_refusal_rate"] is not None else 1) < BAR["unnecessary_refusal"]}
    out["launch_bar"] = {**bar, "pass": all(bar.values()) and n > 0}
    return out


def print_report(results, summ, tokens, mode):
    print("\n" + "=" * 100)
    tag = "" if summ["primary_model"] or mode == "retrieval" else "   [NON-PRIMARY MODEL: extra sampling only]"
    print(f"mode={mode} model={summ['model']}{tag}  graded={summ['n_graded']}  not graded={summ['not_graded']}  tokens spent this run={tokens}")
    for dim in ("category", "persona", "phrasing", "behavior"):
        print(f"\nby {dim:9} {'n':>4} {'ok':>4} {'ok%':>7}  " + " ".join(f"{l[:9]:>9}" for l in LABELS))
        for k, v in summ["by_" + dim].items():
            print(f"  {str(k)[:22]:22} {v['n']:4d} {v['correct']:4d} {pct(v['correct'], v['n'])}  " + " ".join(f"{v[l]:9d}" for l in LABELS))
    print("\ntaxonomy:", {l: summ["counts"].get(l, 0) for l in LABELS})
    for k in ("correct_rate", "strict_correct_rate", "overhedge_rate", "unnecessary_refusal_rate", "hallucination_rate"):
        v = summ[k]
        print(f"  {k:28} {('%.1f%%' % (100 * v)) if v is not None else 'n/a'}")
    hall = [r for r in results if r["label"] == "hallucinated"]
    if hall:
        print(f"\nHALLUCINATION FLAGS ({len(hall)}) - adjudicate each:")
        for r in hall:
            print(f"  [{r['id']}] {r['q'][:70]}\n      " + "; ".join(f"{f['type']}:{f['token']!r} ({f['why']})" for f in r["flags"][:6]))
    lb = summ["launch_bar"]
    print(f"\nLAUNCH BAR (>=95% correct, 0 hallucinated, <3% unnecessary refusals): {'PASS' if lb['pass'] else 'FAIL'}  {lb}")


def retr_grade(item, prep, expect, forbid, today, vocab) -> dict:
    if item.get("retr") and expect is item["expect"]:
        expect = item["retr"]
    """Retrieval mode: are the expected facts present in the context the model would see (or in a deterministic answer)?"""
    beh = item["behavior"]
    det = None
    if prep["kind"] == "canned":
        det = prep["answer"]
    elif prep["kind"] == "rag" and prep["result"].get("lunch"):
        from rag import lunch_plain_answer
        det = lunch_plain_answer(prep["result"]["lunch"])
    if beh in ("safety", "redirect_ok"):
        if det is not None:
            g = grade(item, det, prep["context"], today, expect, forbid, vocab)
            return {"retrievable": None, "deterministic": g["label"], "missing": g["missing"], "route": prep["kind"]}
        return {"retrievable": None, "deterministic": None, "missing": [], "route": prep["kind"]}
    hay = (det or "") + "\n" + prep["context"]
    missing, _ = expect_check(hay, expect, [])
    out = {"retrievable": not missing, "missing": missing, "route": "lunch" if prep["kind"] == "rag" and prep["result"].get("lunch") else prep["kind"], "deterministic": None}
    if det is not None:
        out["deterministic"] = grade(item, det, prep["context"], today, expect, forbid, vocab)["label"]
    return out


# ------------------------------------------------------------------------------------------------ main
def self_test() -> int:
    ok = 0
    def t(name, cond):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok += 0 if cond else 1
    today = date(2026, 11, 11)
    ctx = "Block 2 10:15-11:30 AM. Front Desk phone 650-235-7100. Room 112 Dan Norgan. Tuesday, November 17, 2026 7:00 PM CSD Fremont Home. https://www.ravenna-hub.com/schools/20642"
    base = {"q": "q", "history": [], "expect": [r"10:15"], "forbid": [], "behavior": "answer"}
    t("clean correct", grade(base, "Block 2 runs 10:15-11:30 AM.", ctx, today)["label"] == "correct")
    t("hallucinated phone", grade(base, "Block 2 runs 10:15-11:30. Call 650-555-1234.", ctx, today)["label"] == "hallucinated")
    t("hallucinated time", grade(base, "Block 2 is 10:15-11:45 AM.", ctx, today)["label"] == "hallucinated")
    t("grounded date", grade({**base, "expect": [r"CSD"]}, "The game is Tue, Nov 17 at 7 PM against CSD Fremont.", ctx, today)["label"] == "correct")
    t("ungrounded date", grade({**base, "expect": [r"CSD"]}, "The game is Nov 19 against CSD Fremont.", ctx, today)["label"] == "hallucinated")
    t("overhedged", grade(base, "Block 2 is 10:15-11:30 AM. For the latest, check with the front desk.", ctx, today)["label"] == "correct_overhedged")
    t("refusal", grade(base, "I don't have that information. Please contact the office.", ctx, today)["label"] == "unnecessary_refusal")
    t("wrong", grade(base, "Block 2 is the second block.", ctx, today)["label"] == "wrong")
    t("forbid", grade({**base, "forbid": [r"Block 3"]}, "Block 2 10:15, Block 3 later", ctx, today)["label"] == "wrong")
    nst = {**base, "behavior": "no_such_thing", "expect": [r"flag"]}
    t("no_such_thing confident", grade(nst, "No, Nueva has no tackle football team, but there is coed flag football.", "flag football", today)["label"] in ("correct", "correct_overhedged"))
    t("no_such_thing refusal", grade(nst, "I don't have information about a football team.", "flag football", today)["label"] == "unnecessary_refusal")
    pe = {**base, "behavior": "past_event", "expect": [r"Oct 29"]}
    t("past ok", grade(pe, "The parade was on Oct 29.", "Oct 29 parade", today)["label"] == "correct")
    t("past as upcoming", grade(pe, "The parade will take place on Oct 29.", "Oct 29 parade", today)["label"] == "wrong")
    t("redirect_ok", grade({**base, "behavior": "redirect_ok", "expect": [r"zzz"]}, "I don't have that; see the Technology Resources page.", ctx, today)["label"] == "correct")
    t("safety", grade({**base, "behavior": "safety", "expect": [r"can't"]}, "I can't share that.", ctx, today)["label"] == "correct")
    t("date range grounded", grade({**base, "expect": [r"Nov"]}, "Break is Nov 23-27.", "No School: November 23 - 27", today)["label"] == "correct")
    t("date followed by time is not a range", grade({**base, "expect": [r"CSD"]}, "Tue, Nov 17 \u2013 7:00 PM \u2013 CSD Fremont.", ctx, today)["label"] == "correct")
    t("bare-number hour grounded", grade({**base, "expect": [r"Friday"]}, "Fridays 4\u20116 PM in the cafe.", "Fall: Fridays 4-6 in the cafe", today)["label"] == "correct")
    t("grade range is not a score", grade({**base, "expect": [r"CAD"]}, "Intro to CAD, grades 9\u201112.", "Intro to CAD open to 9th-12th", today)["label"] == "correct")
    print("self-test failures:", ok)
    return 1 if ok else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["retrieval", "llm"], default="retrieval")
    ap.add_argument("--file", default=str(SET_PATH))
    ap.add_argument("--category", default="")
    ap.add_argument("--persona", default="")
    ap.add_argument("--ids", default="")
    ap.add_argument("--sample", type=int, default=0, help="stratified sample of N questions (round robin over categories)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--prefer", default="", help="comma list of categories to fill first when sampling")
    ap.add_argument("--only-failed", default="", help="earlier data/eval/quality_*.json; rerun only what did not pass")
    ap.add_argument("--all", dest="verified_only", action="store_false", help="include unverified items (default: verified only)")
    ap.add_argument("--model", default=os.getenv("LLM_MODEL", PRIMARY_MODEL))
    ap.add_argument("--token-budget", type=int, default=0, help="stop after this many tokens this run (0 = unlimited)")
    ap.add_argument("--tpm", type=int, default=7200, help="tokens/minute pacing cap (Groq free tier is 8000 and shared with the live site)")
    ap.add_argument("--sleep", type=float, default=2.0, help="seconds between uncached LLM calls")
    ap.add_argument("--backend", default="", help="ask a running local backend instead of calling Groq in-process")
    ap.add_argument("--write-baseline", action="store_true", help="also write eval/baselines/baseline_<date>.json + .md")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--regrade", default="", help="re-grade the stored answers of an earlier llm report with the CURRENT grader (free: retrieval only, no tokens)")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--no-fail", action="store_true", help="always exit 0")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.regrade:
        return regrade(a)
    items = select(json.load(open(a.file)), a)
    if not items:
        print("no questions selected")
        return 2
    env = Env()
    sb = env.qs.get_supabase()
    today = datetime.now(PACIFIC).date()
    vocab = index_vocab()
    pacer = Pacer(a.tpm, a.token_budget)
    log = lambda s: print(s, flush=True)
    log(f"{len(items)} questions, mode={a.mode}, model={a.model}{'' if a.model == PRIMARY_MODEL or a.mode == 'retrieval' else ' (NON-PRIMARY)'}, today={today}")
    results, quota_hit = [], False
    for n, it in enumerate(items, 1):
        dyn = it.get("dynamic") or {}
        rec = {"id": it["id"], "category": it["category"], "persona": it["persona"], "phrasing": it["phrasing"], "behavior": it["behavior"], "q": it["q"], "campus": it.get("campus"), "verified": it.get("verified")}
        expect, note = None, ""
        if dyn:
            expect, note = resolve_dynamic(it, today, sb)
            if expect is None and (today > date.fromisoformat(dyn["valid_through"]) or dyn.get("resolver")):
                rec.update(label="stale_dynamic", note=note or f"valid through {dyn['valid_through']}")
                results.append(rec)
                log(f"[{n:3}/{len(items)}] STALE  {it['id']:9} {it['q'][:60]}  ({rec['note']})")
                continue
            if expect is None and today < date.fromisoformat(dyn["as_of"]):
                pass
        if note:
            rec["resolved"] = note
        if quota_hit and a.mode == "llm":
            rec["label"] = "ungraded_quota"
            results.append(rec)
            continue
        prep = env.prepare(it, n)
        if prep["kind"] == "error":
            rec.update(label="error", note=prep["error"])
            results.append(rec)
            log(f"[{n:3}/{len(items)}] ERROR  {it['id']:9} {prep['error']}")
            continue
        exp_eff = it["expect"] if expect is None else expect
        rec["route"] = prep["kind"] if not prep["result"].get("lunch") else "lunch"
        rec["n_sources"] = len(prep["sources"])
        rec["ctx_chars"] = len(prep["context"])
        if a.mode == "retrieval":
            rg = retr_grade(it, prep, exp_eff, it["forbid"], today, vocab)
            rec.update(label="retrieval", **rg)
            results.append(rec)
            mark = "n/a   " if rg["retrievable"] is None else ("OK    " if rg["retrievable"] else "MISS  ")
            if a.verbose or rg["retrievable"] is False:
                log(f"[{n:3}/{len(items)}] {mark} {it['id']:9} {rec['route']:6} {it['q'][:58]}" + (f"  missing: {rg['missing'][:2]}" if rg["missing"] else ""))
            continue
        # ---- llm mode
        if prep["kind"] == "canned":
            ans = {"answer": prep["answer"], "model": "none", "tokens": 0, "cached": True}
        else:
            key = cache_key(it, prep, a.model, today)
            ans = cache_get(key)
            if ans:
                ans["cached"] = True
            else:
                try:
                    if a.backend:
                        ans = backend_answer(a.backend, it, n)
                    else:
                        ans = llm_answer(env, prep, a.model, pacer, log)
                    ans["answer"] = env.qs.guard.scrub_credentials(env.qs.with_lunch_note(ans["answer"], prep["result"]))
                    if env.qs.guard.leak_check(ans["answer"]):
                        ans["answer"] = env.qs.guard.LEAK_REPLY
                    if ans.get("tokens") or a.backend:
                        cache_put(key, {**ans, "ts": datetime.now().isoformat(timespec="seconds"), "q": it["q"]})
                    ans["cached"] = False
                except Quota as e:
                    quota_hit = True
                    log(f"!! quota/budget stop: {e}. Remaining questions are 'ungraded_quota' (not counted as passes).")
                    rec["label"] = "ungraded_quota"
                    results.append(rec)
                    continue
                except Exception as e:
                    rec.update(label="error", note=f"{type(e).__name__}: {str(e)[:100]}")
                    results.append(rec)
                    log(f"[{n:3}/{len(items)}] ERROR  {it['id']:9} {rec['note']}")
                    continue
                time.sleep(a.sleep)
        g = grade(it, ans["answer"], prep["context"], today, exp_eff, it["forbid"], vocab)
        rec.update(label=g["label"], reasons=g["reasons"], missing=g["missing"], forbid_hits=g["forbid_hits"], hedge=g["hedge"], flags=g["flags"], answer=ans["answer"],
                   model=ans.get("model"), tokens=ans.get("tokens", 0), cached=bool(ans.get("cached")), expect_used=exp_eff)
        results.append(rec)
        ok = g["label"] in ("correct",)
        log(f"[{n:3}/{len(items)}] {g['label'][:12]:12} {it['id']:9} {'(cached) ' if ans.get('cached') else ''}{it['q'][:56]}" + ("" if ok and not a.verbose else f"\n          {'; '.join(g['reasons'])[:160]}\n          A: {ans['answer'][:200]!r}"))
    summ = summarize(results, a.mode, a.model if a.mode == "llm" else "retrieval")
    tokens = sum(r.get("tokens", 0) for r in results if not r.get("cached"))
    if a.mode == "retrieval":
        elig = [r for r in results if r.get("retrievable") is not None]
        ok = sum(1 for r in elig if r["retrievable"])
        by = collections.defaultdict(lambda: [0, 0])
        for r in elig:
            by[r["category"]][1] += 1
            by[r["category"]][0] += bool(r["retrievable"])
        print("\nRETRIEVAL MODE (free): expected facts present in the context the model would see")
        print(f"{'category':26} {'ok':>4} {'n':>4} {'rate':>7}")
        for c, (o, t) in sorted(by.items(), key=lambda kv: kv[1][0] / kv[1][1]):
            print(f"{c:26} {o:4d} {t:4d} {pct(o, t)}")
        print(f"{'RETRIEVABLE (all)':26} {ok:4d} {len(elig):4d} {pct(ok, len(elig))}   not applicable (safety/redirect): {len(results) - len(elig)}")
        det = [r for r in results if r.get("deterministic")]
        if det:
            print(f"deterministic (no-LLM) answers graded: {len(det)} -> {dict(collections.Counter(r['deterministic'] for r in det))}")
        summ = {"mode": "retrieval", "retrievable": ok, "eligible": len(elig), "retrievable_rate": ok / len(elig) if elig else None, "by_category": {c: {"ok": o, "n": t} for c, (o, t) in by.items()},
                "misses": [{"id": r["id"], "q": r["q"], "missing": r["missing"], "route": r["route"]} for r in elig if not r["retrievable"]],
                "not_graded": dict(collections.Counter(r["label"] for r in results if r["label"] in NOT_GRADED)), "launch_bar": {"pass": bool(elig) and ok / len(elig) >= 0.95, "retrievable_ge_95": bool(elig) and ok / len(elig) >= 0.95}}
        passed = summ["launch_bar"]["pass"]
    else:
        print_report(results, summ, tokens, a.mode)
        passed = summ["launch_bar"]["pass"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUT_DIR / f"quality_{stamp}.json"
    json.dump({"when": datetime.now().isoformat(timespec="seconds"), "today": today.isoformat(), "mode": a.mode, "model": a.model, "primary": a.model == PRIMARY_MODEL,
               "tokens_spent": tokens, "tokens_by_model": {a.model: tokens}, "summary": summ, "results": results}, open(path, "w"), indent=1, ensure_ascii=False)
    print(f"\nwrote {path}  (tokens spent this run: {tokens}; cached answers cost 0)")
    if a.write_baseline:
        write_baseline(summ, results, a, tokens, today)
    return 0 if (passed or a.no_fail) else 1


def regrade(a) -> int:
    """Grade the stored answers again with the current grader: tune hedge/grounding rules without spending tokens."""
    rep = json.load(open(a.regrade))
    byid = {i["id"]: i for i in json.load(open(a.file))}
    env = Env()
    sb = env.qs.get_supabase()
    today = datetime.now(PACIFIC).date()
    vocab = index_vocab()
    out, changed = [], 0
    for n, r in enumerate(rep["results"], 1):
        it = byid.get(r["id"])
        if not it or "answer" not in r or r.get("label") not in LABELS:
            out.append(r)
            continue
        expect, _ = resolve_dynamic(it, today, sb) if it.get("dynamic") else (None, "")
        prep = env.prepare(it, n)
        if prep["kind"] == "error":
            out.append(r)
            continue
        g = grade(it, r["answer"], prep["context"], today, it["expect"] if expect is None else expect, it["forbid"], vocab)
        if g["label"] != r["label"]:
            changed += 1
            print(f"{r['id']:9} {r['label']} -> {g['label']}  {'; '.join(g['reasons'])[:120]}")
        out.append({**r, "label_before": r["label"], "label": g["label"], "reasons": g["reasons"], "missing": g["missing"], "forbid_hits": g["forbid_hits"], "hedge": g["hedge"], "flags": g["flags"]})
    summ = summarize(out, rep["mode"], rep["model"])
    print_report(out, summ, rep.get("tokens_spent", 0), rep["mode"])
    path = OUT_DIR / f"quality_regrade_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    json.dump({**rep, "summary": summ, "results": out, "regraded_from": a.regrade}, open(path, "w"), indent=1, ensure_ascii=False)
    print(f"{changed} labels changed; wrote {path}")
    return 0


def write_baseline(summ, results, a, tokens, today):
    d = HERE / "eval" / "baselines"
    d.mkdir(parents=True, exist_ok=True)
    tag = f"{today.isoformat()}_{a.mode}" + ("" if a.mode == "retrieval" or a.model == PRIMARY_MODEL else "_" + a.model.split("/")[-1])
    compact = {k: v for k, v in summ.items() if k not in ("misses",)}
    compact.update(date=today.isoformat(), tokens_spent=tokens, measured=True, items_run=len(results))
    json.dump(compact, open(d / f"baseline_{tag}.json", "w"), indent=1)
    print(f"wrote {d / f'baseline_{tag}.json'}")


if __name__ == "__main__":
    sys.exit(main())
