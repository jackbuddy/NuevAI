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

ACRONYM_SKIP = {"US", "MS", "LS", "HS", "JV", "PE", "AM", "PM", "OK", "AI", "IT", "TV", "ID", "SM", "HB", "FAQ", "PDF", "USA", "ASAP", "RSVP", "PSAL", "WBAL", "BTSN", "CS", "ELA", "A", "B"}
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
have has had having am pm what's whats that's there's happening going anything something events kind type sort"""
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
    dayinfo: bool = False           # asking about A/B day type or which blocks meet
    holidays: list[str] = field(default_factory=list)   # named holidays in the question (thanksgiving, labor day...)
    holiday_dates: list[date] = field(default_factory=list)   # this school year's date of each named holiday ("day after Thanksgiving" -> Nov 27)

    def short_range(self) -> bool:
        return (self.end - self.start).days <= 3


HOLIDAY_RX = re.compile(r"\b(thanksgiving|christmas|labor day|memorial day|martin luther king|mlk|veterans day|indigenous|columbus|presidents.? day|juneteenth|new year|halloween|diwali|lunar new year|easter|good friday|winter break|spring break|independence day|winter solstice)\b", re.I)


COMPETITION_BEFORE = re.compile(r"\b(amc|aime|sat|act|ap|psat|usaco|usamo|mathcounts|olympiad|exam|test|contest|competition)\s*#?\s*$")


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th (1-based; -1 = last) given weekday of a month."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year, month, _cal.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


# US holidays the school marks, by rule (month, weekday, n) or fixed (month, day): the date inside the current school year
HOLIDAY_RULES = {"labor day": ("n", 9, 0, 1), "thanksgiving": ("n", 11, 3, 4), "veterans day": ("f", 11, 11), "halloween": ("f", 10, 31),
                 "christmas": ("f", 12, 25), "new year": ("f", 1, 1), "martin luther king": ("n", 1, 0, 3), "mlk": ("n", 1, 0, 3),
                 "presidents": ("n", 2, 0, 3), "memorial day": ("n", 5, 0, -1), "juneteenth": ("f", 6, 19), "independence day": ("f", 7, 4),
                 "indigenous": ("n", 10, 0, 2), "columbus": ("n", 10, 0, 2)}


def holiday_date(name: str, today: date) -> date | None:
    rule = next((v for k, v in HOLIDAY_RULES.items() if k in name.lower()), None)
    if not rule:
        return None
    sy = today.year if today.month >= 8 else today.year - 1          # school year starts in August
    month = rule[1]
    year = sy if month >= 8 else sy + 1
    return _nth_weekday(year, month, rule[2], rule[3]) if rule[0] == "n" else date(year, month, rule[2])


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
    # A date means the one inside the current school year (August to July): "August 20" in October is two months ago,
    # not next year. Only dates past the end of the school year roll over.
    sy_start = date(today.year if today.month >= 8 else today.year - 1, 8, 1)
    year = sy_start.year if month >= 8 else sy_start.year + 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


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
    if m and COMPETITION_BEFORE.search(s[:m.start()]):
        m = None            # "AMC 10/12" is two contests (AMC 10 and AMC 12), not October 12
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
    if category == "athletics" and re.search(r"\b(meetings?|banquet|orientation|tryouts?|physicals?|parent)\b", q):
        category = None      # "Spring Sports Athlete/Parent Meeting" is a named event, not a team-list question
    if not sport and not category and HOLIDAY_RX.search(q):
        category = "no_school"      # a named holiday ("Thanksgiving") is looked up as a break / no-school event
    acr = [a.lower() for a in re.findall(r"\b[A-Z]{2,6}\b", question) if a not in ACRONYM_SKIP]
    if acr and sum(c.isupper() for c in question) > 0.5 * max(1, sum(c.isalpha() for c in question)):
        acr = []                    # shouting ("WHAT TIME IS LUNCH") is not a list of acronyms
    rng = parse_range(q, today)
    if not rng and previous_user_msg and re.search(r"\b(the )?(next|following) day\b|\bday after( that)?\b|\b(the )?(previous|day before)\b", q):
        prev_rng = parse_range(previous_user_msg.lower(), today)
        if prev_rng:
            step = -1 if re.search(r"\b(previous|day before)\b", q) else 1
            d = prev_rng[1 if step > 0 else 0] + timedelta(days=step)
            rng = (d, d, d.strftime("%B %-d"))
    has_cal_word = bool(re.search(CAL_WORDS, q))
    dayinfo = bool(re.search(r"\b[AB]['\u2019]?[ -]?day\b", question) or re.search(r"\b(a|b) day (or|and) (a|b) day\b|\bwhich blocks?\b|\bwhat blocks?\b|\bday type\b|\b(kind|type|sort) of day\b|\bblock schedule\b|\bwhat day (is|are) (it|we|today|tomorrow)\b|\bblocks?\s*\d\b|\bbl[oa]c?ks?\b|\bblock\b|\bbell schedule\b|\bwhen does (school|class|classes) (start|end)\b|\bwhat time (does|do) (school|classes|lunch)\b", q))

    # short follow-ups ("what about tomorrow?") inherit the topic of the previous question
    if previous_user_msg and not sport and not category and len(q.split()) <= 8 and rng:
        prev = parse_intent(previous_user_msg, today)
        if prev and (prev.sport or prev.category or prev.keywords):
            prev.start, prev.end, prev.date_given, prev.label = rng[0], rng[1], True, rng[2]
            return prev

    advisory = bool(re.search(r"\badvisory\b", q))
    dayinfo = dayinfo or advisory
    if not (sport or kind or category or rng or has_cal_word or dayinfo or acr):
        return None

    gender = "girls" if re.search(r"\b(girls?|women'?s?|female)\b", q) else "boys" if re.search(r"\b(boys?|men'?s?|male)\b", q) else None
    division = "MS" if re.search(r"\b(middle school|ms)\b", q) else "US" if (re.search(r"\b(upper school|high school|varsity|jv|junior varsity)\b", q) or re.search(r"\bUS\b", question)) \
        else "LS" if re.search(r"\b(lower school|ls)\b", q) else None
    home_away = "home" if re.search(r"\bhome\b", q) else "away" if re.search(r"\baway\b", q) else None

    words = re.findall(r"[a-z][a-z'-]{3,}", q)
    taken = set(SPORTS) | set(MONTHS) | set(WEEKDAYS) | {"games", "game", "practice", "practices", "match", "matches", "boys", "girls",
                                                              "middle", "school", "upper", "lower", "home", "away", "varsity", "playing"}
    keywords = [w for w in words if w not in STOP and w not in taken][:3]
    if sport or kind or category or dayinfo:
        keywords = []   # structured filters are enough; extra words only add noise
    elif acr:
        keywords = acr[:2]      # "the SAT", "AMC 10/12": a named test or contest is the thing to find on the calendar

    if rng:
        start, end, label = rng
        date_given = True
    elif dayinfo:
        start, end = today, today + timedelta(days=14 if advisory else 0)
        date_given, label = True, "the next two weeks" if advisory else "today"
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
    if not (sport or kind or category or keywords or rng or dayinfo):
        return None
    hol = [x.lower() for x in HOLIDAY_RX.findall(q)]
    hol_dates = []
    for h in hol:
        d = holiday_date(h, today)
        if d:
            off = 1 if re.search(rf"\b(day|school|class\w*) after {re.escape(h)}|\bafter {re.escape(h)}\b", q) else -1 if re.search(rf"\b(day|school|class\w*) before {re.escape(h)}|\bbefore {re.escape(h)}\b", q) else 0
            hol_dates.append(d + timedelta(days=off))
    return CalendarIntent(start, end, sport, kind, category, gender, division, home_away, keywords, date_given, next_only, label, dayinfo, hol, hol_dates)


# ---------------------------------------------------------------------------------------------- lunch (the cafe's monthly menus)
# A menu signal on its own: the word "lunch" alone is NOT one ("which clubs meet at lunch", "and lunch?" after a bell question).
LUNCH_STRONG = re.compile(r"\b(menus?|cafeteria|cafe|caf[eé])\b|\bfor (?:lunch|eating)\b|\blunch(?:es)? (?:menu|options?|specials?|items?)\b|"
                          r"\bwhat(?:'s|s| is| are)? (?:on )?(?:the |today'?s |tomorrow'?s )?(?:lunch|soups?)\b|\bwhat(?:'s|s| is| are)? to eat\b|"
                          r"\bwhat (?:can|do|should) (?:i|we) eat\b|\bsoups? of the day\b|\bsandwich favorite\b|\bsuperfood\b|\bearthly selections?\b|\bplant inspired\b", re.I)
LUNCH_BARE = re.compile(r"\blunch(?:es)?\b", re.I)
DATE_HINT = re.compile(r"\b(today|tonight|tomorrow|day after|next week|this week|weekend|mon(?:day)?|tues?(?:day)?|wed(?:nesday)?|thu(?:rs)?(?:day)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|"
                       r"jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)\b|\b\d{1,2}/\d{1,2}\b", re.I)
NOT_MENU = re.compile(r"\b(meets?|meeting|meetings|clubs?|advisory|office hours|tutorial|appointments?|practice|rehearsal|class(?:es)?|block)\b", re.I)
DIET_WORD = re.compile(r"\b(vegetarian|veggie|vegan|plant[- ]based|gluten[- ]?free|dairy[- ]?free|nut[- ]?free|halal|kosher|pescatarian|allerg\w+)\b", re.I)
DIET_CONTEXT = re.compile(r"\b(options?|available|eat|food|offer(?:ed|s)?|serv\w+|school|campus|cafe|caf[eé]|cafeteria|meals?|today|tomorrow)\b", re.I)
LUNCH_NOT = re.compile(r"\blunch (?:and learn|club|duty|detention|period|block|time|bell|schedule)\b|\b(?:advisory|bell) (?:and )?lunch\b", re.I)
TIME_WORD = re.compile(r"\b(what time|when|how long|how many minutes|schedule|bell|starts?|ends?|begins?|duration)\b", re.I)
MENU_CUE = re.compile(r"\b(menu|what'?s|whats|what is|what are|eat|eating|options?|vegetarian|vegan|gluten|dairy|nut|soup|sandwich|salad|serving today|for lunch)\b", re.I)
HOURS_Q = re.compile(r"\b(cafe|caf[eé]|cafeteria)\b.*\b(hours?|open|opens|close|closes)\b|\b(hours?|open|opens)\b.*\b(cafe|caf[eé]|cafeteria)\b", re.I)
PREK_WORD = re.compile(r"\bpre[\s-]?k\b|\bprek\b|\bkindergarten\b", re.I)
LUNCH_GENERIC = set("""soup soups sandwich sandwiches salad salads entree entrees main dish dishes item items special specials food foods meal meals option options choice choices
available serving served serve lunch lunches menu menus cafe cafeteria campus school anything something there offer offered offers today's todays vegetarian vegan
veggie gluten dairy free nuts eat eating hillsborough mateo upper lower middle high san week weekend month hours hour open opens close closes""".split())


@dataclass
class LunchIntent:
    days: list[date]
    label: str
    date_given: bool
    campuses: list[str]                 # one campus when known, else both
    campus_from: str                    # "text" | "setting" | "none"
    diet: str | None = None
    keywords: list[str] = field(default_factory=list)       # dish words for "when is pizza day" (only when no date was given)
    hours: bool = False
    prek: bool = False
    wide: bool = False                  # the asked range was longer than a week (a month, "this month")
    hours_only: bool = False            # "cafe hours": no menu is wanted, only the opening hours


def campus_in_text(question: str) -> list[str]:
    """Campuses the question names itself: Hillsborough / HB / middle or lower school / MS / LS -> hillsborough;
    San Mateo / SM / upper school / high school / US -> san_mateo. 'MS', 'LS', 'US', 'SM', 'HB' count in capitals anywhere and in
    lowercase only right before lunch/menu/cafe (so "tell us", "Ms. Lee" and "ls" do not)."""
    q = question
    hb = bool(re.search(r"\bhillsborough\b|\bmiddle school\b|\blower school\b", q, re.I) or re.search(r"\b(?:HB|MS|LS)\b", q)
              or re.search(r"\b(?:ms|ls|hb)\b(?=\W+(?:lunch|menu|cafe|caf[eé]|food))", q, re.I))
    sm = bool(re.search(r"\bsan mateo\b|\bupper school\b|\bhigh school\b", q, re.I) or re.search(r"\b(?:SM|US)\b", q)
              or re.search(r"\b(?:us|sm|hs)\b(?=\W+(?:lunch|menu|cafe|caf[eé]|food))", q, re.I))
    return (["san_mateo"] if sm else []) + (["hillsborough"] if hb else [])


# A policy / logistics question that merely contains "lunch" ("Can freshmen leave campus for lunch?") is a handbook question, not a menu lookup.
LUNCH_POLICY = re.compile(r"\b(leave|leaving|off[- ]campus|open campus|closed campus|allowed|permitted|permission|polic(?:y|ies)|rules?|may i|can (?:i|we|freshm[ae]n|sophomores?|juniors?|seniors?|students?|kids?|my (?:kid|child|son|daughter))|are (?:we|students|freshm[ae]n|seniors) allowed|"
                          r"late|skip(?:ping)?|bring|outside|drive|driving|walk(?:ing)?|detention|sign(?:ed)? out|go (?:out|off|home)|order|deliver\w*|doordash|uber ?eats)\b", re.I)
MENU_EXPLICIT = re.compile(r"\b(menus?|what'?s (?:for|on)|whats (?:for|on)|what (?:is|are) (?:for|on|being served|served)|serving|to eat|what (?:can|do|should) (?:i|we) eat|soups? of the day|today'?s lunch|lunch today|lunch tomorrow)\b", re.I)
FOOD_NOUN = re.compile(r"\b(pizza|pasta|burgers?|tacos?|burritos?|sushi|noodles?|fries|chicken|nuggets?|rice|salad|soup|sandwich(?:es)?|quesadillas?|nachos?|bagels?|pancakes?|waffles?|dessert|cookies?|fruit|curry|ramen|dumplings?|hot dogs?|mac and cheese|mac ?& ?cheese)\b", re.I)
FOOD_ASK = re.compile(r"\b(is|are) there\b|\bdoes (?:the )?(?:cafe|caf[e\u00e9]|school|cafeteria|nueva)\b|\bserv\w+\b|\bon the menu\b|\bwhen is\b|\bwhen do (?:they|we)\b", re.I)
HOLIDAY_WORD = re.compile(r"\b(thanksgiving|christmas|labor day|memorial day|veterans day|mlk|martin luther king|indigenous|columbus|presidents.? day|juneteenth|winter break|spring break|fall break|february break|halloween|new year)\b", re.I)


def is_lunch_question(question: str) -> bool:
    """True for 'what's for lunch', 'cafe menu', 'lunch tomorrow', 'Hillsborough lunch', 'any vegan options at the cafeteria', 'cafe hours';
    false for bell-time questions ('what time is lunch', 'lunch schedule'), club or meeting times ('which clubs meet at lunch'), bare 'lunch?'
    follow-ups, and unrelated uses of the word ('lunch and learn', 'lunch duty')."""
    q = question
    if LUNCH_NOT.search(q):
        return False
    if HOURS_Q.search(q):
        return True
    if LUNCH_POLICY.search(q) and not MENU_EXPLICIT.search(q) and not DIET_WORD.search(q) and not re.search(r"\b(cafe|caf[e\u00e9]|cafeteria|menus?|options?)\b", q, re.I):
        return False                       # policy / logistics wording and no menu wording: the handbook answers this
    if HOLIDAY_WORD.search(q) and not MENU_EXPLICIT.search(q):
        return False                       # "is there lunch on Thanksgiving": the school calendar answers it (no school), not the menu
    if FOOD_NOUN.search(q) and FOOD_ASK.search(q) and (DATE_HINT.search(q) or campus_in_text(q)):
        return True                        # "Is there pizza at the middle school on November 12?"
    if LUNCH_STRONG.search(q):
        return not (TIME_WORD.search(q) and not MENU_CUE.search(q)) and not (NOT_MENU.search(q) and not re.search(r"\b(menu|for lunch|what'?s on|eat|serving)\b", q, re.I))
    if LUNCH_BARE.search(q):
        if NOT_MENU.search(q) or (TIME_WORD.search(q) and not MENU_CUE.search(q)):
            return False
        return bool(DATE_HINT.search(q) or campus_in_text(q))
    return bool(DIET_WORD.search(q) and DIET_CONTEXT.search(q) and re.search(r"\b(options?|available|eat|food|offer\w*|serv\w+|meals?)\b", q, re.I))


def parse_lunch(question: str, today: date, campus: str | None = None, previous_user_msg: str = "") -> LunchIntent | None:
    """-> LunchIntent or None. `campus` is the optional saved setting ('san_mateo' | 'hillsborough'); a campus named in the question wins."""
    q = question.lower()
    rng = parse_range(q, today)
    if not is_lunch_question(question):
        # "and the day after?" / "what about the day before?" right after a lunch question: same question, neighbouring day
        rel = re.search(r"\b(the )?(next|following) day\b|\bday after( that)?\b|\b(the )?(previous|day before)\b", q)
        if not (rel and previous_user_msg and is_lunch_question(previous_user_msg) and len(q.split()) <= 8):
            return None
        prev = parse_range(previous_user_msg.lower(), today) or (today, today, "today")
        step = -1 if re.search(r"\b(previous|day before)\b", q) else 1
        d = prev[1 if step > 0 else 0] + timedelta(days=step)
        base = parse_lunch(previous_user_msg, today, campus)
        if not base:
            return None
        base.days, base.label, base.date_given, base.wide, base.keywords = [d], d.strftime("%B %-d"), True, False, []
        return base
    date_given = rng is not None
    if rng:
        start, end, label = rng
    else:
        start = end = today
        label = "today"
    wide = (end - start).days > 6
    n = min((end - start).days + 1, 31)
    days = [start + timedelta(days=i) for i in range(n)]
    if len(days) > 1:
        days = [d for d in days if d.weekday() < 5]            # a week or a month: school days only
    if wide:
        days = [d for d in days if d >= today][:5] or days[:5]
    named = campus_in_text(question)
    saved = campus if campus in ("san_mateo", "hillsborough") else None
    if named:
        campuses, src = named, "text"
    elif saved:
        campuses, src = [saved], "setting"
    else:
        campuses, src = ["san_mateo", "hillsborough"], "none"
    diet = (DIET_WORD.search(question) or [None, None])[0] if DIET_WORD.search(question) else None
    kws = []
    if not date_given:
        words = re.findall(r"[a-z][a-z'-]{3,}", q)
        taken = set(MONTHS) | set(WEEKDAYS) | STOP | LUNCH_GENERIC
        kws = [w.rstrip("s") if w.endswith("s") and len(w) > 4 else w for w in words if w not in taken and not DIET_WORD.fullmatch(w)][:3]
    hours = bool(HOURS_Q.search(q))
    hours_only = hours and not date_given and not re.search(r"\bmenu\b|what'?s|whats|what is|for lunch|\beat\b|serving|options?|vegetarian|vegan", q)
    return LunchIntent(days, label, date_given, campuses, src, diet, kws, hours, bool(PREK_WORD.search(q)), wide, hours_only)
