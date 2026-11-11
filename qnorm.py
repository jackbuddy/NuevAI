"""Query normalisation for retrieval: typos, slang, and word rarity, driven by the index vocabulary.

`vocab_freq.json` (built by scripts/build_vocab.py from the live index) says how many chunks contain each word. That gives us:
  - typo repair: a word that appears nowhere in the school's text ("tommrow", "skool") and is one or two letters away from a
    word that appears often is replaced by it. Words that exist in the index (or are common English question words) are never touched.
  - rarity: `rare_terms` picks the words that carry the question ("guild", "priory", "hoco"), which the keyword (FTS) search and the
    calendar keyword lookup use instead of the common words every chunk contains.
Everything is deterministic and free. The vocabulary file is optional: without it only the explicit slang table applies.
"""
import difflib
import functools
import json
import math
import re
from pathlib import Path

_VOCAB_PATH = Path(__file__).with_name("vocab_freq.json")
_vocab: dict[str, int] | None = None

# Slang and misspellings that students actually type -> the word the index uses. Exact word matches only.
SLANG = {
    "tommrow": "tomorrow", "tomorow": "tomorrow", "tomorro": "tomorrow", "tommorow": "tomorrow", "tommorrow": "tomorrow", "tmrw": "tomorrow",
    "tmr": "tomorrow", "tmw": "tomorrow", "2morrow": "tomorrow", "tomm": "tomorrow", "tonite": "tonight", "yday": "yesterday",
    "wen": "when", "whn": "when", "wehn": "when", "wat": "what", "wut": "what", "wht": "what", "whats": "what's", "wats": "what's", "wich": "which",
    "whos": "who's", "skool": "school", "scool": "school", "schol": "school", "shcool": "school", "shedule": "schedule", "schedual": "schedule",
    "scedule": "schedule", "schdule": "schedule", "sched": "schedule", "calender": "calendar", "calandar": "calendar",
    "brake": "break", "braek": "break", "bball": "basketball", "b-ball": "basketball", "bskball": "basketball", "hw": "homework",
    "teh": "the", "taht": "that", "recieve": "receive", "wensday": "wednesday", "wednsday": "wednesday", "wendsday": "wednesday",
    "thrusday": "thursday", "thurday": "thursday", "tuesday's": "tuesday's", "fri": "friday", "mon": "monday", "tues": "tuesday",
    "thurs": "thursday", "freshman": "freshmen", "frosh": "freshmen", "soph": "sophomores", "plz": "please", "pls": "please", "thx": "thanks",
    "btw": "by the way", "blok": "block", "blck": "block", "tournement": "tournament", "tounament": "tournament", "practise": "practice", "pratice": "practice", "practic": "practice",
    "voleyball": "volleyball", "volleybal": "volleyball", "basketbal": "basketball", "baskteball": "basketball", "soccor": "soccer",
    "chemestry": "chemistry", "calc": "calculus", "philsophy": "philosophy", "consioucness": "consciousness", "thanksgivng": "thanksgiving",
    "thanksgivin": "thanksgiving", "xmas": "christmas", "lunchtime": "lunch time", "cafateria": "cafeteria", "cafetaria": "cafeteria",
    "advisery": "advisory", "advisary": "advisory", "tutorail": "tutorial", "turorial": "tutorial", "cource": "course", "corse": "course",
    "enroll": "enroll", "grade's": "grade's", "gpa's": "gpa's",
}
SLANG_RX = re.compile(r"(?<![\w'-])(" + "|".join(sorted((re.escape(k) for k in SLANG), key=len, reverse=True)) + r")(?![\w'-])", re.I)
# "b ball" / "b-ball" written as two tokens
BBALL_RX = re.compile(r"\bb[\s-]?ball\b", re.I)

# Words that are fine as they are even though they may be absent from the school's text (common English / question words).
COMMON = set("""a about above after again against all also am an and any are around as at be because been before being below between both but by can could did do does doing
down during each few for from further had has have having he her here hers him his how i if in into is it its just let like me more most my no nor not now of off on once only or other
our out over own same she should so some such than that the their them then there these they this those through to too under until up us very was we were what when where which while
who whom why will with would you your today tomorrow tonight yesterday tell show give list find get need want know does please thanks hello hi hey ok okay yes sure""".split())

MIN_DF = 8          # a target word must appear in at least this many chunks


def vocab() -> dict[str, int]:
    global _vocab
    if _vocab is None:
        try:
            _vocab = json.loads(_VOCAB_PATH.read_text())
        except Exception:
            _vocab = {}
    return _vocab


def set_vocab(v: dict[str, int] | None):
    """Tests: inject a vocabulary (None reloads the file)."""
    global _vocab
    _vocab = v
    _nearest.cache_clear()


def known(word: str) -> bool:
    """True when the word, or a simple inflection of it, exists in the index or is common English."""
    lw = word.lower()
    v = vocab()
    return lw in v or lw in COMMON or any(x in v or x in COMMON for x in _forms(lw))


def df(word: str) -> int:
    return vocab().get(word.lower(), 0)


@functools.lru_cache(maxsize=4096)
def _nearest(word: str) -> str | None:
    """The most frequent indexed word within edit distance 1 (2 for long words) that starts with the same letter, or None."""
    v = vocab()
    if not v:
        return None
    n = len(word)
    maxd = 1 if n <= 7 else 2
    best, best_df = None, MIN_DF - 1
    for w, c in v.items():
        if c <= best_df or abs(len(w) - n) > maxd or w[0] != word[0] or w == word:
            continue
        if _edit_distance(word, w, maxd) <= maxd:
            best, best_df = w, c
    return best


def _edit_distance(a: str, b: str, limit: int) -> int:
    """Damerau-Levenshtein (adjacent swaps count 1), early exit above `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def fix_typos(q: str, extra_known: set[str] | frozenset = frozenset()) -> str:
    """Slang table first, then vocabulary snapping for unknown 5+ letter lowercase words. Proper nouns (capitalised words that are
    not at the start of the question), words with digits and anything the index or COMMON already knows are left alone."""
    q = BBALL_RX.sub("basketball", q)
    q = SLANG_RX.sub(lambda m: SLANG[m.group(1).lower()], q)
    v = vocab()
    if not v:
        return q

    def fix(m):
        w = m.group(0)
        lw = w.lower()
        if len(lw) < 5 or lw in COMMON or lw in v or lw in extra_known or w[0].isupper() and m.start() > 0 or not w.islower() and not w.istitle():
            return w
        if any(x in v or x in COMMON for x in _forms(lw)):
            return w                # an inflection of a word the index has ("advisor" / "advisors", "advises" / "advise")
        near = _nearest(lw)
        if near and len(lw) == 4 and v.get(near, 0) < 50:
            return w
        return near if near and (w.islower() or m.start() == 0) else w
    return re.sub(r"[A-Za-z]+", fix, q)


def _forms(w: str) -> list[str]:
    """Plausible base / inflected forms of `w`, so 'advisor' is recognised through 'advisors' and 'advises' through 'advise'. Only suffix
    logic: a misspelling such as 'labr' must not be 'known' just because its prefix ('lab') is a word."""
    out = []
    if len(w) >= 5:
        out += [w + "s", w + "es", w + "ed", w + "ing", w + "er", w + "ly"]
    for suf in ("ing", "ers", "ors", "ies", "es", "ed", "er", "ly", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            stem = w[:-len(suf)]
            out += [stem, stem + "e", stem + "y"]
            if len(stem) > 3 and stem[-1] == stem[-2]:
                out.append(stem[:-1])
    return [x for x in out if len(x) >= 3]


def stem(t: str) -> str:
    """Very light stemmer (qualifies/qualify, advisors/advisor, courses/course, offered/offer) so lexical overlap does not miss plurals
    and tenses. Applied identically to both sides of a comparison, so it only has to be consistent."""
    if len(t) <= 4 or not t.isalpha():
        return t
    if t.endswith("ies"):
        return t[:-3] + "y"
    for suf in ("ing", "ed"):
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            t = t[:-len(suf)]
            return t[:-1] if t[-1] == t[-2] and t[-1] not in "lsz" else t
    if t.endswith(("sses", "xes", "zes", "ches", "shes")):
        return t[:-2]
    if t.endswith("s") and not t.endswith(("ss", "us", "is")):
        return t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:[.'-][a-z0-9]+)*", text.lower())


def rare_terms(query: str, stop: set[str], max_df: int = 60, limit: int = 4) -> list[str]:
    """The question's most distinctive words, rarest first: indexed words that appear in at most `max_df` chunks, plus words the
    index has never seen (a new name or acronym) when they are at least 3 letters. Common question words never qualify."""
    v = vocab()
    out = []
    for t in dict.fromkeys(tokenize(query)):
        if t in stop or t in COMMON or len(t) < 3 or not re.search(r"[a-z]", t) or re.fullmatch(r"[\d-]+(st|nd|rd|th)?", t):
            continue
        c = v.get(t)
        if c is None:
            if v and len(t) >= 4:
                out.append((0, t))          # unseen in the whole index: not a typo we could snap, so look for it literally
        elif c <= max_df:
            out.append((c, t))
    out.sort()
    return [t for _, t in out[:limit]]


def idf(term: str, n_docs: int = 5400) -> float:
    c = vocab().get(term.lower(), 0)
    return math.log(1 + n_docs / (1 + c))
