"""Pre-LLM safety layer: input cleanup, canned refusals, indirect-injection scrubbing and output leak checks.

Everything here runs without the LLM, so it keeps working (and stays testable) when Groq is rate limited.
    sanitize(text)        normalise unicode, drop invisible/control characters
    check(query)          -> (category, canned reply) or None
    scrub_context(text)   remove instruction-like lines from retrieved chunks (indirect prompt injection)
    leak_check(answer)    True when a model answer reveals the system prompt or a secret
"""
import re
import unicodedata

# ---------------------------------------------------------------- input cleanup
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤⁦-⁩﻿­᠎]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# common Cyrillic/Greek look-alikes -> Latin (used only for matching, never shown)
_CONFUSABLE = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ѕ": "s", "ј": "j", "һ": "h", "ԁ": "d",
    "ɡ": "g", "ո": "n", "ν": "v", "ο": "o", "ι": "i", "α": "a", "ε": "e", "ρ": "p", "τ": "t", "κ": "k", "μ": "m", "η": "n",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "І": "I",
})


def sanitize(text: str) -> str:
    """NFKC-normalise (turns fullwidth/styled letters into plain ones), strip zero-width, bidi and control characters."""
    t = unicodedata.normalize("NFKC", text or "")
    t = _INVISIBLE.sub("", t)
    t = _CONTROL.sub("", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def _skeleton(text: str) -> str:
    t = sanitize(text).translate(_CONFUSABLE)
    t = t.replace("’", "'").replace("`", "'")
    return t


# ---------------------------------------------------------------- replies
OFFER = "I can help with Nueva's courses, the school calendar and game schedules, handbook policies, and how to book, register or pay for things."

REPLIES = {
    "self_harm": (
        "I'm really sorry you're going through this, and I'm glad you said something. You don't have to handle it alone. "
        "If you or anyone else is in immediate danger, call 911. You can call or text 988 (the Suicide & Crisis Lifeline) any time, day or night. "
        "At school, please talk to a counselor or a trusted adult as soon as you can: a counselor, your advisor or any teacher can help right away. "
        "If you're worried about a friend, tell a counselor or another adult you trust. I'm an AI and can't replace that support, "
        "but I can help you find who to contact at Nueva whenever you're ready."),
    "threat": (
        "I can't help with that. If you know of a threat to anyone's safety, call 911 and tell a school adult right away. "
        "If you're struggling yourself, you can call or text 988 at any time."),
    "student_privacy": (
        "I can't share private or personal information about students or families, such as names, rosters, advisee lists, contact details, "
        "grades or records. " + OFFER),
    "staff_privacy": (
        "I can't share private information about staff, such as salaries or personal details. " + OFFER),
    "injection": (
        "I can't share or change how I work, and I don't have access to any keys or settings. " + OFFER),
    "homework": (
        "I don't write essays or do homework or assignments for students; that work should be yours, and Nueva's academic integrity policy covers it. "
        "For help with an assignment, ask your teacher or go to a tutorial. " + OFFER),
    "harmful": (
        "I can't help with that. " + OFFER),
}

# ---------------------------------------------------------------- patterns
SELF_HARM = re.compile(
    r"\b(kill(ing)? (my ?self|them ?selves|him ?self|her ?self)|suicid\w*|want(ed)? to die|wanna die|end(ing)? (my|his|her|their) (own )?li(fe|ves)|"
    r"end it all|take my (own )?life|hurt(ing)? my ?self|harm(ing)? my ?self|self[- ]?harm\w*|cut(ting)? my ?self|"
    r"(don'?t|do not|no longer) want to (be alive|live|be here|exist)|no reason to (live|go on|be alive)|better off (dead|without me)|"
    r"wish i (was|were) (dead|never born)|can'?t (go on|do this anymore)|not worth living|life isn'?t worth|"
    r"(friend|someone|classmate|he|she|they|brother|sister).{0,30}(wants?|going|plans?|thinking|threatening) (to )?(kill|hurt|harm|end).{0,15}(them ?selves|him ?self|her ?self|their life|his life|her life))\b", re.I)

THREAT = re.compile(
    r"\b(shoot(ing)? up (the|my|our) school|bring (a|the) (gun|knife|weapon)s? to school|bomb threat|(going|gonna|plan(ning)?) to (shoot|stab|kill|bomb|hurt) .{0,30}(school|teacher|class|students?|everyone|people)|"
    r"(kill|hurt|stab|shoot) (my|a|the|some) (teacher|classmates?|students?|principal|someone))\b", re.I)

HARMFUL = re.compile(
    r"\b(how (do|can|to|would) (i |you |we )?(make|build|create|synthesi[sz]e|cook|get) .{0,25}\b(bombs?|explosives?|meth\w*|poisons?|napalm|weapons?|guns?|fentanyl|cocaine|malware|ransomware|virus(es)?)\b|"
    r"hack(ing)? (into|the school|nexus|the wifi|someone|her|his|their)|ddos|phishing (page|email|site|kit)|steal (a |an |someone'?s? |the )?(password|account|identity|exam|test|answers)s?|"
    r"(buy|get|find|where (can i|to) (buy|get)) .{0,15}\b(weed|vapes?|drugs?|alcohol|fake ids?|adderall|xanax)\b|"
    r"cheat(ing)? (on|in|at) (a |an |the |my )?(tests?|exams?|quizz?es|midterms?|finals?|assignments?)|get away with (cheating|plagiari\w*)|"
    r"bypass (the )?(school'?s? )?(filter|firewall|proctor\w*|lockdown|gptzero|turnitin|ai detect\w*)|dox+(ing)?|"
    r"porn\w*|nsfw|\bnudes?\b|sex(ual)? (story|stories|roleplay|chat)|erotic\w*)\b", re.I)

HOMEWORK = re.compile(
    r"(\b(write|draft|compose|generate|produce|finish|complete|ghostwrite)\b[^.?!\n]{0,30}\b(essays?|papers?|reports?|poems?|stor(y|ies)|speech(es)?|thesis|lab report|personal statement|college essay|"
    r"(python |java |javascript |c\+\+ |some |the |my )?(code|program|script|function)s?|(book|chapter|article) summar(y|ies))\b|"
    r"\bdo (my|the|this|our|his|her) (homework|assignment|hw|problem set|pset|lab|project|worksheet|essay|math)\b|"
    r"\b(solve|answer) (this|these|the (following|next)|my) (equation|problem|question|math|calculus|physics|chemistry|quiz|test|exam|homework|worksheet)s?\b|"
    r"\bwhat('s| is) the answer to (question|problem|number|#)|"
    r"\bwrite (me )?(a|an|my) .{0,30}\b(essay|paper|report|poem|story|speech)\b|"
    r"\bhelp me (cheat|plagiari[sz]e)\b|\b(rewrite|paraphrase) (this|my|the) (essay|paper|text|paragraph) (so|to) .{0,30}(detect|caught|turnitin|plagiar)\w*)", re.I)

STUDENT_PRIVACY = re.compile(
    r"((?<!limited-)(?<!limited )\brosters?\b(?! (limit|size|spots?|cap|policy|cuts?))|\b(advisees?|advisee list|advisory (list|roster|members?|names?)|class (list|roster)|team (roster|list|members)|(basketball|soccer|volleyball|tennis|swim|track|baseball|softball|lacrosse|golf|cross country) (roster|players list|player list|team list))\b|"
    r"\b(list|names?|show|give|tell|send|share|get|find|who (is|are|was|were)|who'?s)\b[^.?!\n]{0,40}\b(students?|kids|pupils|advisees?|freshm[ae]n|sophomores?|juniors?|seniors?|(9|10|11|12)(th)? graders?)\b[^.?!\n]{0,30}\b(in|of|from|at|on|names?|list|attend\w*|enrolled)\b|"
    r"\b(names?|list|roster)s? of (?:(?:all|the|every|our|my) )*(students?|kids|seniors?|juniors?|sophomores?|freshm[ae]n|(9|10|11|12)(th)? graders?|advisees?|class of)\b|"
    r"\b(list|names?)\b[^.?!\n]{0,20}\b(?:(?:the|all|every|of) )+(students|kids|pupils|advisees)\b|"
    r"\bclass of 20\d\d (list|roster|names|students)\b|"
    r"\bwho('s| is| are) in (my|his|her|their|the|\w+'s) (advisory|class|section|homeroom|grade|block|group)\b|"
    r"\b(student|students|parent|parents|guardian|guardians|mom|dad|mother|father|kid|sibling|classmate)s?'?s? (personal |private |home |cell |mobile )?(e-?mails?|e-?mail addresses|phone( numbers?)?|numbers?|addresses|contact( info(rmation)?| details)?|cell|whatsapp|instagram|snapchat|socials?)\b|"
    r"\b(e-?mails?|e-?mail addresses|phone numbers?|addresses|contact (info|details)|numbers?) (of|for) (?:(?:the|a|my|his|her|all|every) )*(9th graders|10th graders|11th graders|12th graders|students?|parents?|guardians?|classmates?|kids|seniors?|juniors?|sophomores?|freshm[ae]n)\b|"
    r"\b(phone|e-?mail|address|number|contact|cell)\b[^\n]{0,60}\b(she|he|they)('s| is| are) (an? )?(junior|senior|sophomore|freshm[ae]n|student|(9|10|11|12)th grader)\b|"
    r"\b(parent|student|family) (directory|contact list|email list|phone list)\b|"
    r"\b(?!(?:school|college|student|teacher|department|class|course)'s)(?-i:[A-Z])[a-z]+'s (grades?|gpa|test scores?|sat scores?|transcript|report card|disciplinary (record|file|history)|attendance record|iep|medical (record|file|history)|diagnosis|detentions?|suspension)\b|"
    r"\b(grades?|gpa|test scores?|transcript|report card|disciplinary record|medical records?|attendance record)\b (of|for) (student|a student|(?-i:[A-Z])[a-z]+ (?-i:[A-Z])[a-z]+)\b|"
    r"\bhome address\b|\bwhere does [A-Za-z]+( [A-Za-z]+)? live\b|\bwhich advisory is (?-i:[A-Z])[a-z]+ (?-i:[A-Z])[a-z]+ in\b|\bwho is (?-i:[A-Z])[a-z]+ (?-i:[A-Z])[a-z]+'s advisor\b|"
    r"\bwho (got|has|made|failed|is failing|was suspended|got suspended|got expelled|is on probation)\b[^.?!\n]{0,40}\b(honor roll|suspended|expelled|probation|detention|the lowest|the highest|a (c|d|f)|failing)\b)", re.I)

STAFF_PRIVACY = re.compile(
    r"\b(salary matrix|payroll|how much (does|do|is|are|did|will) [^?!\n]{1,40}(make|earn|get paid|paid)\b|"
    r"(faculty|teacher|staff|head of school|principal|coach|employee|admin\w*)s?'?s? (salar\w*|pay ?scale|wages?|income|compensation)|salar(y|ies) (of|for) (the |a |mr|mrs|ms|dr)\b|\w+'s salary|compensation (of|for) (the )?(head|principal|teacher))\b", re.I)

INJECTION = re.compile(
    r"(\bignore (all |any |the |your |my |every )?(of )?(the )?(previous|prior|above|earlier|preceding|system|safety|these|those|other)\b|"
    r"\bignore (all |any |the |your |my )?(instructions|rules|guidelines|restrictions|programming|directives)\b|"
    r"\bdisregard (all |any |the |your |every )?(previous|prior|above|earlier|instructions|rules|guidelines|system|safety)\b|"
    r"\b(forget|drop|override|bypass|overwrite|erase) (all |any |the |your |everything |every )?(of )?(previous |prior |your |the |above )?(instructions|rules|guidelines|programming|restrictions|training|safety|system prompt)\b|"
    r"\bsystem[ _-]?prompt\b|\bsystem (message|instructions?)\b|\b(initial|original|hidden|secret|internal|full|exact) (prompt|instructions|rules|configuration)\b|"
    r"\b(reveal|show|print|repeat|display|output|leak|dump|recite|copy|paste|tell me|give me|share|expose|echo|translate) (me )?(all |the |your |ur |its )?(entire |full |whole |exact |initial |original |above |hidden |internal |system |secret )*(prompt|instructions|rules|guidelines|configuration|context|directives|programming)\b|"
    r"\bwhat (are|were|is) your (instructions|rules|guidelines|directives|prompt|programming|initial)\b|"
    r"\b(repeat|print|echo|output|say|write) (everything|all|the (text|words|content|message)s?) (above|before|prior|from the (start|beginning))\b|"
    r"\bdeveloper mode\b|\bdan mode\b|\bjailbreak\w*|\bdo anything now\b|\bgod mode\b|\bsudo (mode|su|rm)\b|\bunrestricted mode\b|\bno (restrictions|rules|limits|filters) mode\b|"
    r"\byou are (now |no longer )(a |an |in |free|unrestricted|dan|my|not|an? ai)|\bfrom now on,? (you|act|ignore|respond|answer|pretend)\b|"
    r"\b(pretend|imagine|act|behave|roleplay|role-play|respond) (as )?(like )?(that )?(you (are|were|have|had)|to be|as|an? (unrestricted|evil|uncensored|unfiltered|different))\b[^.?!\n]{0,40}\b(no (rules|restrictions|limits|filters|guidelines)|unrestricted|uncensored|unfiltered|evil|different ai|another ai|without (rules|restrictions|filters))\b|"
    r"\bwithout (any )?(rules|restrictions|filters|limits|guardrails|censorship)\b|\bunfiltered\b|\buncensored\b|"
    r"\b(api[ _-]?keys?|secret keys?|service[ _-]?role|groq[ _-]?(api )?keys?|supabase[ _-]?(key|url|secret)|access tokens?|bearer token|metrics[ _-]?token|environment variables?|env vars?|\.env\b|process\.env|os\.environ|credentials|private keys?)\b|"
    r"(?<!\w)\.env\b|\b(your|the server'?s?|the database'?s?|the admin'?s?|the backend'?s?) (password|passcode|secrets?)\b|"
    r"\bwhat (model|llm) are you (running|based|using)\b[^.?!\n]{0,30}\b(key|token|secret)\b|"
    r"(<\|?\s*(system|im_start|endoftext)\s*\|?>|\[/?(system|inst)\]|<<\s*/?sys\s*>>|^\s*#{1,3}\s*system\b|\bBEGIN (SYSTEM|NEW) (PROMPT|INSTRUCTIONS)\b|\bEND OF (CONTEXT|PROMPT|INSTRUCTIONS)\b|\bnew (instructions|rules|directive)s?:)|"
    r"\b(you must|you will|you should) (now )?(obey|comply|follow my)\b|\bi am (your )?(developer|creator|admin(istrator)?|owner|the (head|principal)|an? (openai|anthropic|groq) (employee|engineer))\b[^.?!\n]{0,60}\b(override|unlock|disable|ignore|reveal|show|enable|tell)\b|"
    r"\b(admin|developer|root|debug|maintenance) (override|access|command|mode|password)\b)", re.I | re.M)

# order matters: safety first, then privacy, then misuse
_RULES = [
    ("self_harm", SELF_HARM), ("threat", THREAT), ("student_privacy", STUDENT_PRIVACY), ("staff_privacy", STAFF_PRIVACY),
    ("injection", INJECTION), ("harmful", HARMFUL), ("homework", HOMEWORK),
]


def check(query: str) -> tuple[str, str] | None:
    """Canned refusal/care reply for clearly unsafe or off-policy requests, else None."""
    sk = _skeleton(query)
    sk_nospace = re.sub(r"[\s_.\-*]+", "", sk.lower().translate(str.maketrans("013457@$", "oieastas")))
    advice = re.match(r"\s*(how (do|can|should|would|to)|what makes|any tips|tips|advice)\b", sk, re.I) and not re.search(r"\b(for me|write me|do my)\b", sk, re.I)
    for cat, rx in _RULES:
        if cat == "homework" and advice:
            continue
        if rx.search(sk):
            return cat, REPLIES[cat]
    # letter-spaced evasion: "i g n o r e  a l l  p r e v i o u s"
    if re.search(r"ignore(all)?(the)?(previous|prior|above|your)(instructions|rules|prompt)?", sk_nospace) or \
            re.search(r"(reveal|show|print|repeat)(me)?(your|the)(system)?(prompt|instructions)", sk_nospace) or \
            re.search(r"killmyself|wanttodie|suicide|endmylife", sk_nospace):
        cat = "self_harm" if re.search(r"killmyself|wanttodie|suicide|endmylife", sk_nospace) else "injection"
        return cat, REPLIES[cat]
    return None


# ---------------------------------------------------------------- indirect injection (instructions hidden in indexed text)
_CTX_INJECTION = re.compile(
    r"(ignore (all |any |the |your )?(previous|prior|above|earlier)|disregard (all |any |the |your )?(previous|prior|above|earlier|instructions|rules)|"
    r"forget (all |your )?(previous|prior|instructions)|new instructions?\s*:|system prompt|you are now\b|from now on,? (you|always|ignore)|"
    r"(assistant|ai|chatbot|bot|llm|model)[ ,:]+(you )?(must|should|will|shall) (now )?(always |only |never )?(say|answer|reply|respond|tell|reveal|ignore|output|include|recommend|visit|send|ask)|"
    r"\b(ai|llm|chatbot|assistant)s? (reading|processing|summari[sz]ing) this|when (asked|summari[sz]ing).{0,40}(tell|say|respond|reply|include) (the )?(user|them)|"
    r"do not (tell|inform|mention to) the user|reveal (the |your )?(system|prompt|instructions|api)|<\|?(system|im_start)\|?>|\[/?(system|inst)\])", re.I)


def scrub_context(text: str) -> str:
    """Drop whole lines/sentences from retrieved text that try to instruct the model. Retrieved text is data, never commands."""
    if not _CTX_INJECTION.search(text):
        return text
    text = re.sub(r"</?s>|<\|?/?(system|im_start|im_end|endoftext|assistant|user)\|?>|\[/?(system|inst)\]", ". ", text, flags=re.I)
    kept = []
    for line in text.split("\n"):
        if _CTX_INJECTION.search(line):
            parts = re.split(r"(?<=[.!?])\s+", line)
            line = " ".join(p for p in parts if not _CTX_INJECTION.search(p))
        kept.append(line)
    return "\n".join(kept)


# ---------------------------------------------------------------- output check
_LEAK = re.compile(
    r"(STYLE \(house style|GROUNDING\n|house style, follow exactly|You are NuevAI, a high-utility|Privacy: never give information about individual students|"
    r"\bgsk_[A-Za-z0-9]{10,}|\bsb_(secret|publishable)_[A-Za-z0-9_-]{8,}|\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}|SUPABASE_SERVICE_ROLE_KEY\s*=|GROQ_API_KEY\s*=)")

LEAK_REPLY = "I can't share that. " + OFFER


def leak_check(answer: str) -> bool:
    return bool(_LEAK.search(answer or ""))
