"""Deterministic follow-up resolution: no LLM, no network.

A short follow-up ("what about JV?", "and tomorrow?", "who teaches it?") is rewritten into a standalone question
using the previous user turns, so retrieval (entity lookup, clubs, calendar, vector search) sees the real topic.
The model still receives the real conversation; the rewrite is only used for retrieval.

Also holds the optional `grade` helpers. The grade (9-12) lives in the browser's localStorage and is sent with a
request only so the answer can be tailored. It is never logged, cached or stored.
"""
import re

WH = r"(?:who|whom|whose|what|whats|when|where|which|why|how|can|could|is|are|do|does|did|will|would|should|may|tell|show|give|list|find)"
# question frames whose remainder is the subject: "who teaches <X>", "what time is <X>", ...
FRAME = re.compile(
    r"^\s*(?:(?:hi|hey|ok|okay|so|and|also|please|um)\s+)*"
    r"(?:who(?:'s| is| are)? (?:the )?(?:teaching|teaches|teacher of|teachers of|instructor of|advisor of|advisors of|leader of|leaders of|leads|runs|advises)|who (?:teaches|leads|advises|runs|is teaching)|"
    r"what (?:room|block|blocks|time|day|days) (?:is|are|does)|which (?:room|block|blocks|time|day|days) (?:is|are|does)|"
    r"what(?:'s| is| are) (?:the )?(?:schedule|time|times|room|rooms|block|blocks|date|dates|deadline|cost|price) (?:for|of)|"
    r"when (?:is|are|does|do|did)|where (?:is|are|does|do)|how (?:long|much|many) (?:is|are|does|do)|"
    r"what (?:is|are|does|do)|tell me (?:more )?about|(?:can you )?(?:tell|show|give) me|how do i (?:join|sign up for|apply to|find)|"
    r"who(?:'s| is| are)|(?:is|are|does|do)(?: (?:there|the|a|an))?)\s+", re.I)
TRAIL_VERB = re.compile(r"\s+(?:meet|meets|start|starts|end|ends|play|plays|run|runs|happen|happens|open|opens|close|closes|take place|offered|located|in|at|on|taught|about|like|for)\s*$", re.I)
PRONOUN = re.compile(r"\b(it|its|that|this|them|they|those|these|he|she|him|her|his|there)\b", re.I)
LEAD_FOLLOW = re.compile(r"^\s*(?:and|also|then|so|or|but|what about|how about|what if|what of)\b[\s,]*", re.I)
SELF_CONTAINED = re.compile(rf"^\s*{WH}\b", re.I)
VARIANT_GROUPS = [("jv", "varsity"), ("junior varsity", "varsity"), ("boys", "girls"), ("home", "away"), ("fall", "spring"),
                  ("san mateo", "hillsborough"), ("upper school", "middle school", "lower school")]
VARIANT_WORDS = {"jv": "JV", "junior varsity": "JV", "varsity": "varsity", "boys": "boys", "girls": "girls", "home": "home", "away": "away", "fall": "fall", "spring": "spring",
                 "san mateo": "San Mateo", "hillsborough": "Hillsborough", "upper school": "upper school", "middle school": "middle school", "lower school": "lower school"}
DATE_PHRASE = re.compile(
    r"\b(today|tonight|tomorrow|yesterday|this week|next week|this weekend|next weekend|this (?:mon|tues|wednes|thurs|fri|satur|sun)day|next (?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.? \d{1,2}(?:st|nd|rd|th)?|\d{1,2}/\d{1,2})\b", re.I)
RELDAY = re.compile(r"\b(the )?(next|following) day\b|\bday after( that)?\b|\b(the )?(previous|day before)\b", re.I)
_ATTR = r"(?:room|rooms|block|blocks|time|times|day|days|date|dates|advisor|advisors|leader|leaders|teacher|teachers|prereqs?|prerequisites?|cost|deadline|location|place)"
# "what room?", "who is the advisor?", and pairs: "which block and room?", "what block and where", "when and where"
ATTR_ONLY = re.compile(rf"^\s*(?:what|which|where|when|who)(?:'s| is| are)?(?: the)?\s*{_ATTR}?(?:\s*(?:and|&|,)\s*(?:(?:what|which|where|when|who)(?:'s| is| are)?(?: the)?\s*)?{_ATTR}?)*\s*(?:is it|is that|then)?\s*\??\s*$", re.I)


def _words(q: str) -> list[str]:
    return re.findall(r"[a-z0-9'+]+", q.lower())


def subject_of(prev: str) -> str:
    """'Who teaches Software Engineering?' -> 'Software Engineering'. Falls back to the whole question."""
    s = prev.strip().rstrip("?.! ")
    m = FRAME.match(s)
    if m and m.end() < len(s):
        s = s[m.end():]
    s = re.sub(r"^(?:the|a|an)\s+", "", s, flags=re.I)
    s = TRAIL_VERB.sub("", s).strip()
    s = re.sub(r"\s*,?\s*(?:which|what)\s+(?:blocks?|rooms?)(?:\s+and\s+(?:blocks?|rooms?))?\s*$", "", s, flags=re.I).strip(" -,")
    return s or prev.strip().rstrip("?.! ")


FILLER = {"the", "team", "teams", "game", "games", "schedule", "one", "ones", "instead", "only", "what", "about", "how", "and", "also", "then", "for", "just", "level", "side", "version",
          "no", "nah", "nope", "not", "i", "mean", "meant", "actually", "sorry", "wait", "ok", "okay", "please", "pls", "show", "me", "but", "is", "are", "its", "it", "s", "now", "though", "specifically"}
# what people type for a variant -> the canonical variant word ('US boys' = upper school boys in a short refinement)
VARIANT_ALIAS = {"us": "upper school", "hs": "upper school", "high school": "upper school", "ms": "middle school", "ls": "lower school", "junior varsity": "jv",
                 "guys": "boys", "mens": "boys", "men's": "boys", "womens": "girls", "women's": "girls", "sm": "san mateo", "hb": "hillsborough"}
CORRECTION = re.compile(r"^\s*(?:(?:no|nah|nope|not that|sorry|wait|oops)\b[\s,.!]*)?(?:(?:i|we) (?:mean|meant|was asking about|am asking about|was talking about)|i'?m (?:asking|talking) about|as in)\b[\s,:]*(?P<body>.+)$"
                        r"|^\s*(?:no|nah|nope|not that)\b[\s,.!]+(?P<body2>.+)$", re.I)
OTHER_ONE = re.compile(r"^\s*(?:and |what about |how about |no,? )?(?:the )?other (?:one|ones|team|teams|campus|level|side|division)\s*\??\s*$", re.I)
BINARY = [("boys", "girls"), ("varsity", "jv"), ("home", "away"), ("san mateo", "hillsborough"), ("upper school", "middle school"), ("fall", "spring")]


def _other_one(prev: str) -> str | None:
    """'the other one' after a question that names exactly one side of a pair (boys/girls, varsity/JV, a campus): the other side."""
    hits = [(a, b) for a, b in BINARY if bool(re.search(rf"\b{a}\b", prev, re.I)) != bool(re.search(rf"\b{b}\b", prev, re.I))]
    if len(hits) != 1:
        return None
    a, b = hits[0]
    old, new = (a, b) if re.search(rf"\b{a}\b", prev, re.I) else (b, a)
    return re.sub(rf"\b{old}\b", VARIANT_WORDS.get(new, new), prev, count=1, flags=re.I)


def _variant_swap(q: str, prev: str) -> str | None:
    """'what about JV?' after 'when is the next varsity game' -> 'when is the next JV game'. Several at once work too:
    'and the varsity girls?' after a boys' JV question swaps both level and gender; 'only varsity US boys' narrows the previous
    question to those variants (the ones it already has stay, the missing ones are added)."""
    ql = re.sub(r"[?.!,]", " ", q.lower())
    ql = re.sub(r"\s+", " ", ql).strip()
    found = []
    for key in sorted(set(VARIANT_WORDS) | set(VARIANT_ALIAS), key=len, reverse=True):          # longest first: 'junior varsity' before 'varsity'
        if re.search(rf"\b{re.escape(key)}\b", ql):
            canon = VARIANT_ALIAS.get(key, key)
            if canon not in found:
                found.append(canon)
            ql = re.sub(rf"\b{re.escape(key)}\b", " ", ql)
    rest = [w for w in re.findall(r"[a-z0-9']+", ql) if w not in FILLER]
    if not found or rest:
        return None
    out = prev
    for ql_key in found:
        new = VARIANT_WORDS[ql_key]
        if re.search(rf"\b{re.escape(new)}\b", out, re.I):
            continue                                                  # the previous question already has it
        swapped = False
        for grp in VARIANT_GROUPS:
            if ql_key in grp:
                for old_w in grp:
                    if old_w != ql_key and re.search(rf"\b{old_w}\b", out, re.I):
                        out = re.sub(rf"\b{old_w}\b", new, out, count=1, flags=re.I)
                        swapped = True
                        break
            if swapped:
                break
        if not swapped:
            out = f"{out.rstrip('?. ')} ({new})"
    return out


def _date_swap(q: str, prev: str) -> str | None:
    m = DATE_PHRASE.search(q)
    if not m or len(_words(q)) > 6:
        return None
    rest = DATE_PHRASE.sub("", q).strip(" ?.!,")
    rest_w = [w for w in _words(rest) if w not in ("and", "what", "about", "how", "then", "also", "the", "is", "its", "it's", "on", "for")]
    if rest_w:
        return None          # has its own content ("when is the winter concert tomorrow"): not a bare date follow-up
    if DATE_PHRASE.search(prev):
        return DATE_PHRASE.sub(m.group(0), prev, count=1)
    return f"{prev.rstrip('?. ')} {m.group(0)}"


def rewrite_followup(query: str, history: list[dict] | None) -> str:
    """Returns a standalone version of `query`, or `query` unchanged when it already stands on its own."""
    return resolve(query, history)[0]


def resolve(query: str, history: list[dict] | None) -> tuple[str, str | None]:
    """-> (standalone question, kind). kind: refine ('only varsity', 'and the girls?'), correct ('no i mean the teachers'), swap ('the
    other one'), date ('and tomorrow?'), pronoun ('who teaches it?'), attr ('what room?'), subject ('and Block 4?'), or None when the
    message already stands on its own."""
    users = [m.get("content", "") for m in (history or []) if m.get("role") == "user" and m.get("content")]
    if not users:
        return query, None
    prev = users[-1]
    if len(users) >= 2 and (len(_words(prev)) <= 6 and (not SELF_CONTAINED.match(prev) or PRONOUN.search(prev)) or OTHER_ONE.match(prev)
                            or (CORRECTION.match(prev) and len(_words(prev)) <= 9)):
        prev = resolve(prev, [{"role": "user", "content": u} for u in users[:-1]])[0]     # chain of follow-ups
    q = query.strip()
    words = _words(q)
    if not words or len(words) > 9 or RELDAY.search(q):
        return query, None          # "the next day" is resolved against the previous date in intent.py

    if out := _variant_swap(q, prev):
        return out, "refine"
    if OTHER_ONE.match(q) and (out := _other_one(prev)):
        return out, "swap"
    if out := _date_swap(q, prev):
        return out, "date"
    cm = CORRECTION.match(q)
    if cm:
        body = (cm.group("body") or cm.group("body2") or "").strip(" ?.!")
        if body and len(_words(body)) <= 7 and not SELF_CONTAINED.match(body):
            # a correction re-reads the previous question with the missing piece: "why is history so terrible" + "no i mean the teachers"
            return f"{prev.rstrip('?. ')} ({body})", "correct"

    lead = LEAD_FOLLOW.match(q)
    body = q[lead.end():] if lead else q
    body_s = body.strip()
    subj = subject_of(prev)
    stand_alone = SELF_CONTAINED.match(body_s) and not PRONOUN.search(body_s) and not ATTR_ONLY.match(body_s)
    if stand_alone:
        return query, None

    # pronoun reference: "who teaches it?" / "what room is that in?" / "when do they meet?"
    if PRONOUN.search(body_s):
        repl = subj
        out = re.sub(r"\b(?:it|that|this|them|they|those|these|there)\b", repl, body_s, count=1, flags=re.I)
        out = re.sub(r"\b(?:its|his|her)\b", repl + "'s", out, count=1, flags=re.I) if out == body_s else out
        out = re.sub(r"\b(?:he|she|him)\b", repl, out, count=1, flags=re.I) if out == body_s else out
        return (out if out != body_s else f"{body_s} ({subj})"), "pronoun"

    # attribute only: "what room?", "who is the advisor?"
    if ATTR_ONLY.match(body_s):
        return f"{subj}: {body_s}", "attr"

    # "and Block 4?" / "what about Intro to Film?": same question, new subject
    if lead and body_s and len(_words(body_s)) <= 6 and not re.match(r"the (one|next|previous|other|last)\b", body_s, re.I):
        fm = FRAME.match(prev.strip())
        if fm and fm.end() < len(prev.strip().rstrip("?. ")):
            tail = TRAIL_VERB.search(prev.strip().rstrip("?. !"))
            suffix = tail.group(0) if tail else ""
            return f"{prev.strip()[:fm.end()]}{body_s.rstrip('?. !')}{suffix}?".replace("  ", " "), "subject"
        return f"{body_s.rstrip('?')} ({subj})", "subject"
    return query, None


# ------------------------------------------------------------------ grade (client-side preference)
GRADE_WORD = {9: "freshmen", 10: "sophomores", 11: "juniors", 12: "seniors"}
GRADE_SENSITIVE = re.compile(r"\b(courses?|classes|class|electives?|take|taking|recommend\w*|prereq\w*|prerequisites?|requirements?|graduat\w*|advisory|advisor|schedule|credits?|sign up|register|offered|should i)\b", re.I)
EXPLICIT_GRADE = re.compile(r"\b(9|10|11|12)(?:th)?\s*(?:grade|graders?)\b|\bgrade\s*(?:9|10|11|12)\b|\b(freshm[ae]n|sophomores?|juniors?|seniors?)\b", re.I)


def valid_grade(g) -> int | None:
    try:
        g = int(g)
    except (TypeError, ValueError):
        return None
    return g if 9 <= g <= 12 else None


def grade_relevant(query: str, grade: int | None) -> bool:
    """True when knowing the grade changes the answer, so such answers must not be shared through the cache."""
    return bool(grade) and bool(GRADE_SENSITIVE.search(query)) and not EXPLICIT_GRADE.search(query)


def apply_grade(query: str, grade: int | None) -> str:
    """Adds the student's grade to the retrieval text for grade-sensitive questions ('my advisory' -> 'my advisory for juniors')."""
    if not grade_relevant(query, grade):
        return query
    if re.search(r"\badvisory\b", query, re.I) and not re.search(r"\badvisory\s*\d", query, re.I):
        return f"{query.rstrip('?. ')} for {GRADE_WORD[grade]}"
    return f"{query.rstrip('?. ')} grade {grade}"
