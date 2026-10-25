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
VARIANT_GROUPS = [("jv", "varsity"), ("junior varsity", "varsity"), ("boys", "girls"), ("home", "away"), ("fall", "spring")]
VARIANT_WORDS = {"jv": "JV", "junior varsity": "JV", "varsity": "varsity", "boys": "boys", "girls": "girls", "home": "home", "away": "away", "fall": "fall", "spring": "spring"}
DATE_PHRASE = re.compile(
    r"\b(today|tonight|tomorrow|yesterday|this week|next week|this weekend|next weekend|this (?:mon|tues|wednes|thurs|fri|satur|sun)day|next (?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.? \d{1,2}(?:st|nd|rd|th)?|\d{1,2}/\d{1,2})\b", re.I)
RELDAY = re.compile(r"\b(the )?(next|following) day\b|\bday after( that)?\b|\b(the )?(previous|day before)\b", re.I)
ATTR_ONLY = re.compile(r"^\s*(?:what|which|where|when|who)(?:'s| is| are)?(?: the)?\s*(?:room|block|blocks|time|day|days|date|advisor|advisors|leader|leaders|teacher|teachers|prereqs?|prerequisites?|cost|deadline)?\s*(?:is it|is that|then)?\s*\??\s*$", re.I)


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


def _variant_swap(q: str, prev: str) -> str | None:
    """'what about JV?' after 'when is the next varsity game' -> 'when is the next JV game'."""
    ql = re.sub(r"[?.!]", "", q.lower()).strip()
    ql = LEAD_FOLLOW.sub("", ql).strip()
    ql = re.sub(r"^the\s+", "", ql)
    ql = re.sub(r"\s+(?:team|game|games|schedule|one|ones|instead)$", "", ql).strip()
    if ql not in VARIANT_WORDS:
        return None
    new = VARIANT_WORDS[ql]
    for grp in VARIANT_GROUPS:
        if ql in grp:
            for old in grp:
                if old != ql and re.search(rf"\b{old}\b", prev, re.I):
                    return re.sub(rf"\b{old}\b", new, prev, count=1, flags=re.I)
    return f"{prev.rstrip('?. ')} ({new})"


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
    users = [m.get("content", "") for m in (history or []) if m.get("role") == "user" and m.get("content")]
    if not users:
        return query
    prev = users[-1]
    if len(users) >= 2 and len(_words(prev)) <= 6 and (not SELF_CONTAINED.match(prev) or PRONOUN.search(prev)):
        prev = rewrite_followup(prev, [{"role": "user", "content": u} for u in users[:-1]])     # chain of follow-ups
    q = query.strip()
    words = _words(q)
    if not words or len(words) > 9 or RELDAY.search(q):
        return query          # "the next day" is resolved against the previous date in intent.py

    if out := _variant_swap(q, prev):
        return out
    if out := _date_swap(q, prev):
        return out

    lead = LEAD_FOLLOW.match(q)
    body = q[lead.end():] if lead else q
    body_s = body.strip()
    subj = subject_of(prev)
    stand_alone = SELF_CONTAINED.match(body_s) and not PRONOUN.search(body_s) and not ATTR_ONLY.match(body_s)
    if stand_alone:
        return query

    # pronoun reference: "who teaches it?" / "what room is that in?" / "when do they meet?"
    if PRONOUN.search(body_s):
        repl = subj
        out = re.sub(r"\b(?:it|that|this|them|they|those|these|there)\b", repl, body_s, count=1, flags=re.I)
        out = re.sub(r"\b(?:its|his|her)\b", repl + "'s", out, count=1, flags=re.I) if out == body_s else out
        out = re.sub(r"\b(?:he|she|him)\b", repl, out, count=1, flags=re.I) if out == body_s else out
        return out if out != body_s else f"{body_s} ({subj})"

    # attribute only: "what room?", "who is the advisor?"
    if ATTR_ONLY.match(body_s):
        return f"{subj}: {body_s}"

    # "and Block 4?" / "what about Intro to Film?": same question, new subject
    if lead and body_s and len(_words(body_s)) <= 6 and not re.match(r"the (one|next|previous|other|last)\b", body_s, re.I):
        fm = FRAME.match(prev.strip())
        if fm and fm.end() < len(prev.strip().rstrip("?. ")):
            tail = TRAIL_VERB.search(prev.strip().rstrip("?. !"))
            suffix = tail.group(0) if tail else ""
            return f"{prev.strip()[:fm.end()]}{body_s.rstrip('?. !')}{suffix}?".replace("  ", " ")
        return f"{body_s.rstrip('?')} ({subj})"
    return query


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
