"""Turns a question into a structured calendar lookup.

Vector search is poor at "when is the next girls soccer game" because the answer
depends on today's date and on exact filters (sport, gender, home/away). This
module parses those out of the question so the server can query calendar events
directly, then hand the model a short, correctly ordered list.
"""
import calendar as _cal
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m.lower(): i for i, m in enumerate(_cal.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(_cal.month_abbr) if m})
MONTHS["sept"] = 9

SPORTS = {
    "basketball": "basketball", "soccer": "soccer", "swim": "swim", "swimming": "swim", "tennis": "tennis",
    "volleyball": "volleyball", "baseball": "baseball", "softball": "softball", "golf": "golf",
    "cross country": "cross country", "xc": "cross country", "track": "track", "lacrosse": "lacrosse",
    "badminton": "badminton", "water polo": "water polo", "ultimate": "ultimate", "frisbee": "ultimate",
    "fencing": "fencing", "wrestling": "wrestling", "pickleball": "pickleball", "dance": "dance",
}
GAME_WORDS = r"\b(games?|matches|match|meets?|scrimmages?|tournaments?|invitationals?|vs\.?|versus|playing|play)\b"
PRACTICE_WORDS = r"\b(practices?|practicing|training)\b"
NO_SCHOOL_WORDS = r"\b(no school|break|breaks|holidays?|vacation|closed|closure|day off|days off|off school)\b"
ARTS_WORDS = r"\b(concerts?|performances?|recitals?|musicals?|plays?|theat(?:er|re)|exhibits?|art show)\b"
CAL_WORDS = r"\b(calendar|events?|deadlines?|when is|when are|when does|when do|what time|what day|what date|upcoming)\b"

STOP = set("""a an the is are was were be been do does did can could will would should what when where which who whom
how why of for on in at to from with about into over under this that these those it its my our your their me us we you
i there here any some all more most next last new today tonight tomorrow yesterday week weekend month year day days
time times date dates schedule scheduled upcoming calendar event events please tell show give list find get
have has had having am pm what's whats that's there's happening going anything something events"""
    .split())


@dataclass
class CalendarIntent:
    start: date
    end: date
    sport: str | None = None
    kind: str | None = None          # game | practice
    category: str | None = None      # no_school | arts | ...
    gender: str | None = None        # boys | girls
    division: str | None = None      # MS | US | LS
    home_away: str | None = None
    keywords: list[str] = field(default_factory=list)
    date_given: bool = False
    next_only: bool = False
    label: str = ""

    def short_range(self) -> bool:
        return (self.end - self.start).days <= 3


def _next_weekday(today: date, idx: int, strictly_after: bool = False) -> date:
    delta = (idx - today.weekday()) % 7
    if strictly_after and delta == 0:
        delta = 7
    return today + timedelta(days=delta)


def _school_year_date(today: date, month: int, day: int) -> date | None:
    try:
        d = date(today.year, month, day)
    except ValueError:
        return None
    # past dates more than ~2 months back are assumed to mean next year
    return d if d >= today - timedelta(days=60) else date(today.year + 1, month, day)


def parse_range(q: str, today: date):
    """-> (start, end, label) or None"""
    s = q.lower()
    if re.search(r"\b(today|tonight)\b", s):
        return today, today, "today"
    if re.search(r"\btomorrow\b", s):
        return today + timedelta(days=1), today + timedelta(days=1), "tomorrow"
    if re.search(r"\bday after tomorrow\b", s):
        return today + timedelta(days=2), today + timedelta(days=2), "day after tomorrow"
    m = re.search(r"\bin (\d{1,2}) (day|days|week|weeks)\b", s)
    if m:
        n = int(m.group(1)) * (7 if m.group(2).startswith("week") else 1)
        return today + timedelta(days=n), today + timedelta(days=n), f"in {m.group(1)} {m.group(2)}"
    if re.search(r"\bnext week\b", s):
        mon = _next_weekday(today, 0, strictly_after=True)
        return mon, mon + timedelta(days=6), "next week"
    if re.search(r"\bthis week\b", s):
        return today, today + timedelta(days=6 - today.weekday()), "this week"
    if re.search(r"\bnext weekend\b", s):
        sat = _next_weekday(today, 5, strictly_after=True) + timedelta(days=7 if today.weekday() < 5 else 0)
        return sat, sat + timedelta(days=1), "next weekend"
    if re.search(r"\b(this )?weekend\b", s):
        sat = _next_weekday(today, 5)
        if today.weekday() == 6:
            sat = today - timedelta(days=1)
        return max(sat, today), sat + timedelta(days=1), "this weekend"
    if re.search(r"\bnext month\b", s):
        y, mth = (today.year + (today.month == 12), today.month % 12 + 1)
        return date(y, mth, 1), date(y, mth, _cal.monthrange(y, mth)[1]), "next month"
    if re.search(r"\bthis month\b", s):
        return today, date(today.year, today.month, _cal.monthrange(today.year, today.month)[1]), "this month"
    # "november 12", "nov 12th", "12 november"
    mon_rx = "|".join(sorted(MONTHS, key=len, reverse=True))
    m = re.search(rf"\b({mon_rx})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", s) or None
    if m:
        d = _school_year_date(today, MONTHS[m.group(1)], int(m.group(2)))
        if d:
            return d, d, d.strftime("%B %-d")
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({mon_rx})\b", s)
    if m:
        d = _school_year_date(today, MONTHS[m.group(2)], int(m.group(1)))
        if d:
            return d, d, d.strftime("%B %-d")
    m = re.search(r"\b(\d{1,2})/(\d{1,2})\b", s)
    if m and 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31:
        d = _school_year_date(today, int(m.group(1)), int(m.group(2)))
        if d:
            return d, d, d.strftime("%B %-d")
    m = re.search(rf"\b({mon_rx})\b", s)
    if m and m.group(1) not in ("may",) or (m and re.search(r"\b(in|during|of|for) may\b", s)):
        mth = MONTHS[m.group(1)]
        y = today.year if mth >= today.month or today.month - mth < 2 else today.year + 1
        return date(y, mth, 1), date(y, mth, _cal.monthrange(y, mth)[1]), _cal.month_name[mth]
    for i, wd in enumerate(WEEKDAYS):
        if re.search(rf"\b{wd}s?\b", s):
            strictly = bool(re.search(rf"\bnext {wd}\b", s))
            d = _next_weekday(today, i, strictly_after=strictly)
            return d, d, wd.capitalize()
    return None


def parse_intent(question: str, today: date, previous_user_msg: str = "") -> CalendarIntent | None:
    q = question.lower()
    sport = next((SPORTS[k] for k in sorted(SPORTS, key=len, reverse=True) if re.search(rf"\b{re.escape(k)}\b", q)), None)
    kind = "game" if re.search(GAME_WORDS, q) and sport else "practice" if re.search(PRACTICE_WORDS, q) else None
    if re.search(GAME_WORDS, q) and not sport and re.search(r"\b(games?|matches|scrimmages?)\b", q):
        kind = "game"
    category = "no_school" if re.search(NO_SCHOOL_WORDS, q) else "arts" if re.search(ARTS_WORDS, q) and not sport else None
    if not sport and not category and re.search(r"\b(sports?|teams?|athletics?|athletes?)\b", q):
        category = "athletics"
    rng = parse_range(q, today)
    has_cal_word = bool(re.search(CAL_WORDS, q))

    # short follow-ups ("what about tomorrow?") inherit the topic of the previous question
    if previous_user_msg and not sport and not category and len(q.split()) <= 8 and rng:
        prev = parse_intent(previous_user_msg, today)
        if prev and (prev.sport or prev.category or prev.keywords):
            prev.start, prev.end, prev.date_given, prev.label = rng[0], rng[1], True, rng[2]
            return prev

    if not (sport or kind or category or rng or has_cal_word):
        return None

    gender = "girls" if re.search(r"\b(girls?|women'?s?|female)\b", q) else "boys" if re.search(r"\b(boys?|men'?s?|male)\b", q) else None
    division = "MS" if re.search(r"\b(middle school|ms)\b", q) else "US" if (re.search(r"\b(upper school|high school|varsity)\b", q) or re.search(r"\bUS\b", question)) \
        else "LS" if re.search(r"\b(lower school|ls)\b", q) else None
    home_away = "home" if re.search(r"\bhome\b", q) else "away" if re.search(r"\baway\b", q) else None

    words = re.findall(r"[a-z][a-z'-]{3,}", q)
    taken = set(SPORTS) | set(MONTHS) | set(WEEKDAYS) | {"games", "game", "practice", "practices", "match", "matches", "boys", "girls",
                                                              "middle", "school", "upper", "lower", "home", "away", "varsity", "playing"}
    keywords = [w for w in words if w not in STOP and w not in taken][:3]
    if sport or kind or category:
        keywords = []   # structured filters are enough; extra words only add noise

    if rng:
        start, end, label = rng
        date_given = True
    else:
        date_given = False
        label = "upcoming"
        start = today
        # season-level questions need a longer window than "what's next"
        if (sport or category == "athletics") and re.search(r"\b(season|schedule|all|playing|lineup)\b", q):
            span = 150
        elif sport or kind:
            span = 60
        else:
            span = 300      # concerts, breaks, parent events and named events can be months away
        end = today + timedelta(days=span)
    next_only = bool(re.search(r"\b(next|upcoming|soonest|first)\b", q)) and not rng
    if not (sport or kind or category or keywords or rng):
        return None
    return CalendarIntent(start, end, sport, kind, category, gender, division, home_away, keywords, date_given, next_only, label)
