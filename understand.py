"""Query understanding: how a casually worded question is read before any lookup runs. Deterministic, no network, no LLM.

People do not type "When is the first Upper School varsity boys basketball game?". They type "when's boys bball first game", "only
varsity", "who's the AD", "is it an a day". The knowledge is in the index; this module makes the wording reach it:

  normalize(q)        shorthand -> the words the lookups and the index use ("when's" -> "when is", "vball" -> "volleyball",
                      "the AD" -> "the athletic director", "apps due" -> "applications due", "b7" -> "block 7", "an a day" -> "an A day")
  clean_query(q)      the same question without greetings / filler / politeness, used as a SECOND search query next to the original
  interpret(...)      -> Interp: question type, team facets (sport, gender, level, division) with DEFAULTS and the alternatives they
                      hide, first-vs-next, vague schedule requests, confidence
  apply_to_intent()   turns the interpretation into the structured calendar lookup (season opener for "first", Upper School unless the
                      asker says otherwise or their saved campus is Hillsborough, a games lookup for "the varsity schedule")
  split_division() / filter_level() / lead_games()   post-filters on calendar rows, driven by the rows' own metadata
  line()              the INTERPRETATION line the model receives (how the question was understood, defaults applied, alternatives)
  props()             compact fields for the chat_answered analytics event

Nothing here answers a question or invents an entity: every facet comes from the question, the conversation, the saved campus, or the
index rows themselves.
"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from intent import SPORTS, CalendarIntent

# ---------------------------------------------------------------------------------------------- shorthand
# (pattern, replacement). Applied in order to the typo-fixed question. Only wording is changed, never meaning.
SHORTHAND = [
    (re.compile(r"\b(when|where|who|how)['\u2019]?s\b(?!\s+(?:the\s+)?(?:that|this)\b)", re.I), lambda m: f"{m.group(1)} is"),
    (re.compile(r"\bv[- ]?ball\b", re.I), "volleyball"),
    (re.compile(r"\bhoops?\b(?!\s+(?:earrings?|skirt))", re.I), "basketball"),
    (re.compile(r"\bcross[- ]country\b", re.I), "cross country"),
    (re.compile(r"(?-i:\bAD\b)|(?<=\bthe )ad\b(?!\s+(?:for|about|on|in)\b)", re.I), "athletic director"),
    (re.compile(r"\bapps?\b(?=\s+(?:are\s+|is\s+)?(?:due|deadline|open|close))|(?<=\bwhen are )apps?\b|(?<=\bwhen is the )app\b", re.I), "admissions applications"),
    (re.compile(r"(?-i:\bPT\b) conferences|\bpt conferences\b|\bparent[- ]teacher conf\w*", re.I), "parent-teacher conferences"),
    (re.compile(r"(?-i:\bFA\b)(?=\s+(?:app\w*|deadline|workshop|form))|\bfin(?:ancial)? aid\b", re.I), "financial assistance"),
    (re.compile(r"\bd[- ]?tech\b", re.I), "Design Tech"),
    (re.compile(r"\bb([1-8])\b(?=\s+(?:time|start|end|today|tomorrow|on|class|room)|\s*\??$)", re.I), lambda m: f"block {m.group(1)}"),
    (re.compile(r"\bms\b(?=\s+(?:boys|girls|soccer|basketball|volleyball|sports?|teams?|games?|musical|clubs?|lunch|trips?|dance|parents?|students?|conferences)\b)|(?<=\babout )ms\b|(?<=\bfor )ms\b"), "MS"),
    (re.compile(r"\bhs\b(?=\s+(?:boys|girls|soccer|basketball|volleyball|sports?|teams?|games?|musical|clubs?|lunch|trips?|dance))"), "upper school"),
    (re.compile(r"\b(fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth)[- ](grade(?:rs?)?)\b", re.I),
     lambda m: f"{ {'fifth': '5th', 'sixth': '6th', 'seventh': '7th', 'eighth': '8th', 'ninth': '9th', 'tenth': '10th', 'eleventh': '11th', 'twelfth': '12th'}[m.group(1).lower()]} {m.group(2)}"),
    (re.compile(r"\bmerch\b", re.I), "merchandise spirit wear"),
    (re.compile(r"\bafter ?care\b", re.I), "after-school care aftercare"),
    (re.compile(r"\bteams? up against\b", re.I), "team playing"),
    # day type written in lowercase: "is it an a day", "a or b day today", "tomorrow a day or b day", "b day blocks"
    (re.compile(r"\b(an|is it|it is|its|it's|be|today|tomorrow|tonight)\s+a[- ]day\b(?!\s+(?:off|of|trip|early|late|for|to|when|where|that|school))", re.I), lambda m: f"{m.group(1)} A day"),
    (re.compile(r"\b(?:an?\s+)?a\s+or\s+(?:an?\s+)?b(?:\s+day)?\b", re.I), "an A day or B day"),
    (re.compile(r"\ba[- ]day\s+or\s+(?:a\s+)?b[- ]day\b", re.I), "A day or B day"),
    (re.compile(r"(?<![\w'])(?:a\s+|on\s+a\s+|on\s+)?b[- ]day\b(?!\s*(?:party|cake|present|gift))", re.I), lambda m: m.group(0).replace("b day", "B day").replace("b-day", "B day")),
    (re.compile(r"\ba[- ]prime\b", re.I), "A'"),
    (re.compile(r"\bb[- ]prime\b", re.I), "B'"),
]


def normalize(q: str) -> str:
    """Shorthand and casual forms -> the wording the lookups key on. Idempotent; an already direct question is returned unchanged."""
    out = q
    for rx, rep in SHORTHAND:
        out = rx.sub(rep, out)
    return re.sub(r"\s{2,}", " ", out).strip()


# ---------------------------------------------------------------------------------------------- filler
_FILLER_LEAD = re.compile(
    r"^\s*(?:(?:hi|hello|hey|yo|ok|okay|so|um|uh|well|also|and|quick question|question|sorry|excuse me)\b[\s,!.:-]*)*"
    r"(?:(?:can|could|would|will) (?:you|u|someone|anyone) (?:please |pls |kindly )?(?:tell|show|give|help|let) me(?: (?:know|with|out|find))?\b[\s,:]*|"
    r"(?:do|did) (?:you|u|ya|y'?all) (?:happen to )?(?:know|have)(?: (?:if|whether))?\b[\s,:]*(?=\w)|"
    r"(?:i|we) (?:was|were|am|are|'m|'re) (?:just )?(?:wondering|curious|trying to (?:find|figure) out|looking for)(?: (?:if|whether|about))?\b[\s,:]*|"
    r"(?:i|we)(?:'d| would) like to know\b[\s,:]*|(?:i|we) (?:need|want)(?: to (?:know|find|see|get))?\b[\s,:]*|(?:looking for|need|wondering)\b[\s,:]*|"
    r"(?:please|pls|plz)\b[\s,:]*|(?:tell|show|give) me\b[\s,:]*|(?:any idea|do you know|does anyone know|anyone know)\b[\s,:]*)*", re.I)
_FILLER_TAIL = re.compile(r"[\s,]*(?:\b(?:please|pls|plz|thanks|thank you|thx|ty|asap|lol|btw|tho|though|anyone|do you know|if you know|by any chance|or what|again)\b[\s,!.?]*)+$", re.I)
_FILLER_MID = re.compile(r"\b(?:just|actually|basically|like,|kinda|kind of like|really|even|exactly|by any chance|or something|or whatever|this year|at nueva|at school|here)\b", re.I)


def clean_query(q: str) -> str:
    """The question without greetings, politeness and filler ("hey quick question, do you know when ... is, thanks"). Used as a second
    search query: the embedding of a short, on-topic sentence lands closer to the right chunks. Never empty: falls back to `q`."""
    s = _FILLER_LEAD.sub("", q.strip())
    s = _FILLER_TAIL.sub("", s)
    s = _FILLER_MID.sub(" ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,.-")
    return s if len(re.findall(r"[A-Za-z0-9]{2,}", s)) >= 2 else q.strip()


# ---------------------------------------------------------------------------------------------- question type
_QT = [
    ("howmuch", re.compile(r"^\W*(?:how much|how many|how long|how old|how often)\b|\bhow (?:much|many)\b", re.I)),
    ("when", re.compile(r"^\W*(?:when|what (?:time|day|date|days|dates)|which (?:day|days|date)|how soon)\b|\bwhat time\b|\bwhen (?:is|are|do|does|did|was|were|will)\b", re.I)),
    ("where", re.compile(r"^\W*(?:where|which (?:room|campus|building|gym|field)|what (?:room|address))\b|\bwhere (?:is|are|do|does|can)\b", re.I)),
    ("who", re.compile(r"^\W*(?:who|whose|whom|which (?:teacher|coach|advisor|dean))\b|\bwho (?:is|are|do|does|teaches|runs|leads|coaches)\b", re.I)),
    ("how", re.compile(r"^\W*how\b|\bhow (?:do|can|does|should|to)\b", re.I)),
    ("yesno", re.compile(r"^\W*(?:is|are|was|were|do|does|did|can|could|will|would|should|may|am|has|have|any)\b", re.I)),
    ("list", re.compile(r"^\W*(?:what|which|list|show|name)\b.*\b(?:are there|offered|available|options|all|every|list)\b|^\W*(?:list|show|name)\b|\bwhat (?:sports|clubs|courses|classes|languages|electives|teams|departments)\b", re.I)),
    ("what", re.compile(r"^\W*(?:what|which|why)\b", re.I)),
]


def qtype(q: str) -> str:
    """when | where | who | how | howmuch | yesno | list | what | statement (no question form: 'need the bell schedule')."""
    for name, rx in _QT:
        if rx.search(q):
            return name
    return "statement"


# ---------------------------------------------------------------------------------------------- interpretation
LEVEL_RX = re.compile(r"\b(junior varsity|jv|varsity)\b", re.I)
ONLY_RX = re.compile(r"\b(only|just|specifically|not (?:the )?(?:jv|junior varsity|varsity|middle school|ms|boys|girls))\b", re.I)
FIRST_RX = re.compile(r"\b(first|opening|opener|season opener|1st)\b", re.I)
NEXT_RX = re.compile(r"\b(next|upcoming|soonest|coming up)\b", re.I)
GAME_RX = re.compile(r"\b(games?|matches|match|meets?|scrimmages?|tournaments?|vs\.?|versus|playing|plays?|against)\b", re.I)
SCHEDULE_RX = re.compile(r"\b(schedules?|games?|matches|playing|plays?|fixtures?|season)\b", re.I)
NOTE_RX = re.compile(r"\b(practices?|tryouts?|playoffs?|ccs|championships?|scrimmages?|first day|start(?:s|ing)?|begins?|season (?:start|begin|end)\w*|break)\b", re.I)
MS_RX = re.compile(r"\b(middle school|hillsborough|(?:5th|6th|7th|8th|fifth|sixth|seventh|eighth)[- ]grade(?:rs?)?)\b|(?-i:\bMS\b)", re.I)
US_RX = re.compile(r"\b(upper school|high school|highschool|san mateo|varsity|jv|junior varsity|freshm[ae]n|sophomores?|juniors?|seniors?|(?:9th|10th|11th|12th)[- ]grade(?:rs?)?)\b|(?-i:\bUS\b)", re.I)
DIV_NAME = {"US": "Upper School", "MS": "Middle School", "LS": "Lower School"}
# "do we have hockey", "can i play baseball here", "what season do the girls play golf": asks WHETHER / WHEN IN THE YEAR a sport exists
# (answered by the offerings lists), not for a game schedule. The sport list includes sports the school may not offer.
SPORT_ANY = re.compile(r"\b(" + "|".join(sorted(set(SPORTS) | {"football", "hockey", "ice hockey", "field hockey", "cheer", "cheerleading", "rugby", "gymnastics", "crew", "rowing", "sailing", "squash",
                                                                "skiing", "surfing", "bowling", "archery", "esports", "table tennis", "ping pong", "martial arts", "karate", "flag football",
                                                                "beach volleyball", "diving", "swimming", "softball", "baseball"}, key=len, reverse=True)) + r")\b", re.I)
OFFER_RX = re.compile(r"\b(?:do|does|did) (?:we|you|u|ya|yall|y'all|nueva|the school|they|the (?:upper|middle) school)(?: even| guys)? (?:have|offer|got|do)\b|\b(?:is|are) there\b|"
                      r"\bcan (?:i|you|we|girls|boys|students|freshm[ae]n|my (?:kid|son|daughter|child)|\w+ graders) (?:play|do|join|run|swim|try out for|sign up for)\b|"
                      r"\b(?:what|which) season\b|\b(?:have|has|offer|offers) an? (?:\w+ ){1,3}team\b|\bis (?:\w+ ){1,2}(?:a thing|offered|a sport)\b", re.I)
SCHED_SIGNAL = re.compile(r"\b(next|first|schedule|games?|score|scores|when|what time|where|who|tonight|today|tomorrow|this week|against|vs\.?|practices?|coach\w*|league|tryouts?|"
                          r"jan\w*|feb\w*|mar\w*|apr\w*|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*|mon(?:day)?|tue\w*|wed\w*|thu\w*|fri(?:day)?|sat\w*|sun(?:day)?)\b|\d{1,2}/\d{1,2}", re.I)
OFFER_WORDS = "varsity offerings by season athletics teams sports offered no-cut limited-roster teams Fall Winter Spring"


@dataclass
class Interp:
    text: str                               # the question the lookups see (typo-fixed, shorthand expanded, follow-up resolved)
    qtype: str = "what"
    sport: str | None = None
    gender: str | None = None
    level: str | None = None                # "varsity" | "jv" (only when the question says so)
    only_level: bool = False                # "only varsity", "just the JV": the other level is filtered out, not merely listed second
    division: str | None = None             # explicit in the question
    default_division: str | None = None     # applied because the question did not say (US, or MS when the saved campus is Hillsborough)
    first: bool = False                     # "first game" = the season opener (even if already played)
    next: bool = False
    game: bool = False
    vague_schedule: bool = False            # "do you have the varsity schedule": a level / games request with no sport
    offering: bool = False                  # "do we have hockey", "can i play baseball here": does the sport exist, not when is the game
    expand: list[str] = field(default_factory=list)         # index wording added to the search text only (never shown)
    followup: str | None = None             # refine | correct | swap | pronoun | date | None
    defaults: list[str] = field(default_factory=list)       # human-readable defaults that were applied
    alternatives: list[str] = field(default_factory=list)   # what the default hides ("Middle School", "JV", "Alexa Hart")
    notes: list[str] = field(default_factory=list)          # extra reading notes for the model
    confidence: float = 1.0
    rewrite: str = ""                       # "" | "shorthand" | "followup" | "llm"

    def low(self) -> bool:
        return self.confidence < 0.5


def interpret(text: str, campus: str | None = None, grade: int | None = None, followup: str | None = None, original: str = "") -> Interp:
    """Reads the (already normalised, follow-up-resolved) question. Cheap: a few regexes."""
    q = text
    ql = q.lower()
    ip = Interp(text=q, qtype=qtype(q), followup=followup)
    ip.sport = next((SPORTS[k] for k in sorted(SPORTS, key=len, reverse=True) if re.search(rf"\b{re.escape(k)}\b", ql)), None)
    if ip.sport == "dance" and not re.search(r"\bdance (team|game|meet|competition)\b", ql):
        ip.sport = None
    ip.gender = "girls" if re.search(r"\b(girls?|women'?s?|female)\b", ql) else "boys" if re.search(r"\b(boys?|men'?s?|male)\b", ql) else None
    lv = LEVEL_RX.findall(q)
    if lv:
        kinds = {"jv" if x.lower() in ("jv", "junior varsity") else "varsity" for x in lv}
        if len(kinds) == 1:
            ip.level = kinds.pop()
            ip.only_level = True                 # naming one level asks for that level
    ms, us = bool(MS_RX.search(q)), bool(US_RX.search(q))
    ip.division = "MS" if ms and not us else "US" if us and not ms else None
    ip.first = bool(FIRST_RX.search(q)) and not re.search(r"\bfirst (day|week|semester|period|block|place|round|incident|time i|thing)\b", ql)
    ip.next = bool(NEXT_RX.search(q)) and not ip.first
    ip.game = bool(GAME_RX.search(q))
    athletic_word = bool(ip.sport or ip.level or re.search(r"\b(teams?|athletics|sports?)\b", ql))
    ip.vague_schedule = bool(not ip.sport and ip.level and SCHEDULE_RX.search(q) and not re.search(r"\b(sports?|offer\w*|teams? (?:are|does|do)|tryouts?|what is|difference)\b", ql))
    ip.offering = bool(SPORT_ANY.search(q) and OFFER_RX.search(q) and not SCHED_SIGNAL.search(q))
    if ip.offering:
        ip.expand.append(OFFER_WORDS)
        ip.vague_schedule = False
    elif (ip.sport or ip.vague_schedule or (ip.game and athletic_word)) and not ip.division:
        ip.default_division = "MS" if campus == "hillsborough" else "US"
    words = re.findall(r"[a-z0-9']+", ql)
    conf = 1.0
    if len(words) <= 2:
        conf -= 0.4                          # "varsity?", "lunch", "schedule": too little to be sure
    if ip.qtype == "statement":
        conf -= 0.15
    if followup in ("pronoun", "refine") and len(words) <= 4:
        conf -= 0.1
    if original and original.strip().lower() != q.strip().lower():
        ip.rewrite = "followup" if followup else "shorthand"
    ip.confidence = max(0.0, round(conf, 2))
    return ip


# Everyday phrases for things the school calendar files under another title -> the title words to look up (any of them matches).
CAL_ALIASES = [
    (re.compile(r"\b(?:next|a|the|school|any|upcoming|winter|spring) dance\b|\bdances\b", re.I), ["dance", "formal", "prom"]),
    (re.compile(r"\b(?:accept\w*|say(?:ing)? yes|respond\w*|reply\w*|commit\w*|decide)\b.{0,40}\b(?:offer|admission\w*|acceptance|spot)\b|\bafter getting in\b|\b(?:offer|acceptance)\b.{0,30}\b(?:deadline|due|respond|reply)\b", re.I), ["decision responses"]),
    (re.compile(r"\b(?:hear back|find out|notified)\b.{0,40}\b(?:admission\w*|appl\w+|accept\w+|got in|get in)\b|\b(?:admissions?|acceptance) (?:decisions?|letters?|results?)\b.{0,20}\b(?:come|out|sent|released|arrive)\b", re.I), ["decisions released"]),
    (re.compile(r"\b(?:get|gets|getting|let|lets) out for (?:the )?summer\b|\bwhen does summer (?:start|begin)\b|\bsummer (?:break|vacation) (?:start|begin)\w*\b", re.I), ["last day of school"]),
]


def calendar_alias(q: str) -> list[str]:
    for rx, kws in CAL_ALIASES:
        if rx.search(q):
            return list(kws)
    return []


def school_year_start(today: date) -> date:
    return date(today.year if today.month >= 8 else today.year - 1, 8, 1)


def apply_to_intent(ip: Interp, intent: CalendarIntent | None, today: date) -> CalendarIntent | None:
    """Adjusts (or creates) the structured calendar lookup from the interpretation. Returns the intent to use."""
    if ip.offering and intent is not None and not intent.date_given and not intent.holidays and intent.category in (None, "arts", "athletics") and not intent.dayinfo:
        return None                         # "can i play baseball here": the offerings documents answer it, a game lookup would only add noise
    if ip.vague_schedule and (intent is None or not (intent.sport or intent.category or intent.date_given and not intent.kind)):
        start, end, label, given = today, today + timedelta(days=45), "upcoming games", False
        if intent is not None and intent.date_given:
            start, end, label, given = intent.start, intent.end, intent.label, True
        intent = CalendarIntent(start, end, kind="game", division=ip.division, label=label, date_given=given)
        ip.notes.append("no sport was named: the next games of each team at that level are listed; offer the full schedule of one team")
        ip.confidence = min(ip.confidence, 0.6)
    if intent is None:
        return None
    team_q = bool(intent.sport or intent.kind == "game") and intent.kind != "practice" and intent.category is None
    if team_q and ip.first and not intent.date_given:
        # "first game" = the season opener, even when it has been played: look from the start of the school year, not from today
        intent.start = school_year_start(today)
        intent.end = max(intent.end, today + timedelta(days=150))
        intent.label = "this season, from the first game"
        intent.next_only = True
        if not intent.kind:
            intent.kind = "game"
    if team_q and ip.default_division and intent.division is None:
        ip.defaults.append(DIV_NAME[ip.default_division])
    return intent


def row_division(r: dict) -> str | None:
    m = r.get("metadata") or {}
    d = m.get("division")
    if d in ("US", "MS", "LS"):
        return d
    s = (m.get("summary") or "").strip()
    mm = re.match(r"(US|MS|LS)\b", s)
    return mm.group(1) if mm else None


def row_is_jv(r: dict) -> bool:
    m = r.get("metadata") or {}
    return str(m.get("level") or "").lower() in ("jv", "junior varsity") or bool(re.search(r"\bJV\b|\bJunior Varsity\b", m.get("summary") or ""))


def split_division(rows: list[dict], ip: Interp) -> tuple[list[dict], list[dict]]:
    """Default division: -> (rows of the assumed division, rows of the other divisions). When the assumed division has no row (a sport
    only the Middle School plays), nothing is assumed: all rows are kept and the default is withdrawn."""
    d = ip.default_division
    if not d or DIV_NAME[d] not in ip.defaults:
        return rows, []
    mine = [r for r in rows if row_division(r) in (d, None)]
    if not any(row_division(r) == d for r in rows):
        ip.defaults.remove(DIV_NAME[d])
        return rows, []
    other = [r for r in rows if row_division(r) not in (d, None)]
    if other:
        ip.alternatives.append(DIV_NAME[row_division(other[0])])
    return mine, other


def filter_level(rows: list[dict], ip: Interp) -> list[dict]:
    """'only varsity' / 'JV': keep that level among Upper School rows (a team with no JV row is its varsity). Never returns nothing."""
    if not ip.level:
        return rows
    want_jv = ip.level == "jv"
    kept = [r for r in rows if row_division(r) == "MS" or row_is_jv(r) == want_jv]
    return kept or rows


def team_of(r: dict) -> str:
    m = r.get("metadata") or {}
    return m.get("team") or re.sub(r"\s*-\s*(Game|Practice)\b.*$", "", m.get("summary") or "", flags=re.I).strip(" -")


def lead_games(events: list[dict], ip: Interp | None, today: date, per_team: int = 1, limit: int = 5) -> list[dict]:
    """'First / next game' and 'the varsity schedule': one line per team (its opener or next game; with per_team=2 the one after it),
    varsity teams before JV, then by date. When the opener has been played the team's next upcoming game follows it."""
    games = [e for e in events if (e.get("metadata") or {}).get("kind") == "game"] or events
    by_team: dict[str, list[dict]] = {}
    for e in games:
        by_team.setdefault(team_of(e), []).append(e)
    picked = []
    tday = today.isoformat()
    for team, rows in by_team.items():
        rows.sort(key=lambda r: ((r["metadata"] or {}).get("event_date", ""), (r["metadata"] or {}).get("start_time") or ""))
        take = rows[:per_team]
        if ip is not None and ip.first and take and (take[0]["metadata"] or {}).get("event_date", "") < tday:
            nxt = next((r for r in rows if (r["metadata"] or {}).get("event_date", "") >= tday), None)
            if nxt is not None and nxt not in take:
                take = take[:1] + [nxt]
        picked.append((row_is_jv(rows[0]), (take[0]["metadata"] or {}).get("event_date", ""), (take[0]["metadata"] or {}).get("start_time") or "", take))
    picked.sort(key=lambda x: x[:3])
    out = []
    for _, _, _, take in picked[:limit]:
        out += take
    if ip is not None and len(by_team) > 1 and not ip.level:
        jv = [t for t in by_team if row_is_jv(by_team[t][0])]
        if jv and "JV" not in ip.alternatives:
            ip.alternatives.append("JV")
            if "varsity first" not in ip.defaults:
                ip.defaults.append("varsity first")
    return out


def line(ip: Interp | None) -> str:
    """The INTERPRETATION line for the prompt, or '' when the question was read literally with nothing assumed."""
    if ip is None:
        return ""
    parts = []
    if ip.defaults:
        d = ", ".join(ip.defaults)
        parts.append(f"the asker did not say which division or level, so this is read as {d}")
    if ip.first and (ip.sport or ip.game):
        parts.append("'first game' means the season opener (if it is before today, say it already happened and give the next game too)")
    if ip.alternatives:
        parts.append("also relevant: " + ", ".join(ip.alternatives))
    parts += ip.notes
    if not parts:
        return ""
    tail = " Answer for that reading directly, say the assumption in a few words (for example \"For Upper School varsity: ...\"), and name the alternatives in one short line instead of asking which was meant."
    return "INTERPRETATION: " + "; ".join(parts) + "." + tail


def props(ip: Interp | None, intent=None) -> dict:
    """Compact, PII-free analytics fields (a few dozen bytes): how the question was understood."""
    if ip is None:
        return {}
    out = {"qt": ip.qtype, "conf": ip.confidence}
    if ip.rewrite:
        out["rw"] = ip.rewrite
    if ip.followup:
        out["fu"] = ip.followup
    if ip.defaults:
        out["dflt"] = [d[:16] for d in ip.defaults[:3]]
    if ip.alternatives:
        out["alt"] = len(ip.alternatives)
    it = "lunch" if intent == "lunch" else None
    if it is None and intent is not None:
        it = "game" if getattr(intent, "kind", None) == "game" else "practice" if getattr(intent, "kind", None) == "practice" else \
            "daytype" if getattr(intent, "dayinfo", False) else (getattr(intent, "category", None) or ("sport" if getattr(intent, "sport", None) else "date" if getattr(intent, "date_given", False) else "calendar"))
    if it:
        out["it"] = it
    return out
