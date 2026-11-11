"""One source of truth for "did the answer refuse / redirect / hedge?".

Imported by the server (analytics, cache policy, rescue chips) and by eval_quality.py (grading), so the dashboard's refusal rate and
the evaluation's refusal rate are measured by the same code. Pure regexes, no network.

The answer prompt asks the model to use ONE canonical phrase when the school's data really does not cover a question:
it starts with "I don't have that" (see CANONICAL). `canonical_refusal` detects exactly that, `detect` also catches the older
phrasings (cached answers, other models) and redirects ("check with the athletics office").
"""
import re

CANONICAL = "I don't have that"
CANONICAL_RX = re.compile(r"^\s*(?:\*\*)?(?:sorry,?\s*)?I (?:don't|do not|dont) have (?:that|this|any|the|a|an|information|details)\b", re.I)

# REFUSAL: "I don't know" family (the data was apparently missing as far as the model could tell).
REFUSAL = re.compile(
    r"\bI (do not|don't|dont) (have|know|see)\b|\bI (could not|couldn't|can't|cannot|can not|am unable to|'m unable to|am not able to|'m not able to|wasn't able to) (find|locate|confirm|verify|say|tell|determine|see|share|access|provide)"
    r"|\bnot (in|part of) (the|my) (information|data|context|documents|knowledge|calendar)\b|\b(isn't|is not|aren't|are not) (listed|available|specified|included|posted|published|provided|shown)\b(?: (in|on|for|yet|anywhere|here))?"
    r"|\b(doesn't|does not|don't|do not) (list|say|specify|include|mention|show|provide|give|have) (any|a|the|that|this|those|specific|an)?\b.{0,40}\b(information|details|info|schedule|date|time|number|dates|times|listing)"
    r"|\bno (information|details|info|data|record|listing)\b|\bnot sure\b|\b(cannot|can't|can not) (be )?(confirmed|verified|determined)\b|\bI lack\b|\bunable to (find|locate|confirm|verify|provide)\b|\bwithout (more|further|specific) (information|details)\b"
    r"|\bdon't have (access|that|this|any|the)\b|\bnot something I (have|know)\b|\bnot (currently )?(on|in) (the )?(school )?calendar\b|\b(nothing|none) (is |are )?(listed|scheduled|posted|on the)\b", re.I)
# REDIRECT: sends the user elsewhere.
REDIRECT = re.compile(
    r"\b(check|checking|confirm|verify|ask|contact|call|email|reach out to|consult|refer to|see|visit|look at|look on|review|go to|get in touch with|speak with|talk to)\b[^.\n]{0,50}\b"
    r"(office|front desk|athletic\w*|nexus|handbook|website|school site|coach|coaches|admissions|dean|counsel\w*|cafe|teacher|advisor|department|registrar|resource board|calendar tab|resources tab|school)\b"
    r"|\bfor (the )?(latest|most up[- ]to[- ]date|current|official|exact|final|updated) (information|details|info|schedule|times?|dates?|updates?)\b|\bplease (check|confirm|verify|contact)\b|\byou may want to (check|contact|confirm)\b"
    r"|\bconfirm (the )?(details|times?|dates?|schedule)\b|\bsubject to change\b|\bcan (change|vary)\b", re.I)
# a closing pointer after a complete answer ("For more details, see the FAQ") is recorded but is not an over-hedge
TAIL_POINTER = re.compile(r"\bfor (more|additional|further|full) (information|details|info|steps|instructions)\b", re.I)
PRECISE_POINTER = re.compile(r"https?://\S+|\[[^\]]+\]\([^)]+\)|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b|[\w.+-]+@[\w-]+\.[\w.-]+|\b(front desk|main office|athletics office|admissions office|business office|health office|nurse|dean of students|Nexus|resource board|technology resources|help desk|college counseling)\b", re.I)
CONFIDENT_NO = re.compile(r"\b(no|not|doesn't|does not|don't|do not|isn't|is not|aren't|are not|there is no|there are no|never|none)\b", re.I)

LUNCH_NOTE_RX = re.compile(r"The menu can change; check with the cafe for ingredients or allergens\.?", re.I)


def normalize(text: str) -> str:
    """Models emit no-break spaces, curly quotes and en/em dashes; match on plain text."""
    for bad, good in (("\u00a0", " "), ("\u202f", " "), ("\u2009", " "), ("\u2019", "'"), ("\u2018", "'"),
                      ("\u201c", '"'), ("\u201d", '"'), ("\u2013", "-"), ("\u2014", "-"), ("\u2011", "-")):
        text = text.replace(bad, good)
    return text


def hedge_info(answer: str) -> dict:
    a = LUNCH_NOTE_RX.sub("", normalize(answer))      # the mandatory cafe note is product copy, not a hedge
    first = re.split(r"(?<=[.!?])\s+", a.strip())[:2]
    head = " ".join(first)
    ref = [m.group(0) for m in REFUSAL.finditer(a)]
    red = [m.group(0) for m in REDIRECT.finditer(a)]
    return {"refusal": ref[:3], "redirect": red[:3], "refusal_in_head": bool(REFUSAL.search(head)), "n_refusal": len(ref), "n_redirect": len(red),
            "precise_pointer": bool(PRECISE_POINTER.search(a)), "tail_pointer": bool(TAIL_POINTER.search(a)), "confident_no_head": bool(CONFIDENT_NO.search(head)) and not REFUSAL.search(head)}


def canonical_refusal(answer: str) -> bool:
    """True when the answer opens with the canonical 'I don't have that' (the one genuine 'not in my data' phrase)."""
    return bool(CANONICAL_RX.match(normalize(answer or "")))


def detect(answer: str) -> dict:
    """Booleans only (safe to log): refusal (any 'I don't know' family wording), canonical (the exact opening phrase), redirect (sends the
    user elsewhere), hedged (refusal or redirect in the first two sentences, i.e. the answer leads with a limit instead of a fact)."""
    h = hedge_info(answer or "")
    a = LUNCH_NOTE_RX.sub("", normalize(answer or ""))
    head = " ".join(re.split(r"(?<=[.!?])\s+", a.strip())[:2])
    canon = canonical_refusal(answer)
    return {"refusal": bool(h["n_refusal"]) or canon, "canonical": canon, "redirect": bool(h["n_redirect"]),
            "hedged": h["refusal_in_head"] or canon or bool(REDIRECT.search(head)), "n_refusal": h["n_refusal"], "n_redirect": h["n_redirect"]}


def is_limited(answer: str) -> bool:
    """The answer states a limit of the data (refused, or leads with a hedge). Such answers are not cached and get rescue chips."""
    d = detect(answer)
    return d["refusal"] or d["hedged"]
