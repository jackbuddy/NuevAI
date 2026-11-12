"""Schedule parsing shared by the email ingest, the Google Sheets ingest and Google Docs.

Everything here is pure (no network, no I/O) so every rule is testable offline:

  dates / times     find_dates, parse_cell_date, parse_time ('Tue 11/17', '11/17/26', 'Nov 17', serial numbers like 46342, '3:45 PM',
                    '4:00-5:30 PM', 'TBD')
  tables            map_columns, schedule_events: header + rows -> game / practice / event dicts (HOME TEAM / AWAY TEAM layout of the
                    school's own team sheets, and OPPONENT + H/A layouts)
  free text         find_practice_slots, find_games, schedule_summary: 'Practice: Tue/Thu 4:00-5:30 PM at <place>' out of an announcement
  records           derive_context (team/sport/gender/level/season) and event_chunk (the SAME record schema pipeline/sheets.event_chunk
                    writes, so rag.py's 'next game' lookups, the Calendar tab and the Happening cards read them unchanged)
"""
import hashlib
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # guard.py / intent.py / freshness.py live at the repo root

# --------------------------------------------------------------------------------------------------------------------- dates
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
MONTH_RX = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
WD_RX = r"(?:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SERIAL_EPOCH = date(1899, 12, 30)          # Google Sheets / Excel serial day 0

_D_ISO = r"(?P<iy>20\d{2})-(?P<im>\d{1,2})-(?P<id>\d{1,2})"
_D_NUM = r"(?<![\d/.:])(?P<nm>\d{1,2})/(?P<nd>\d{1,2})(?:/(?P<ny>\d{4}|\d{2}))?(?![\d/:])"
_D_MDY = rf"\b(?P<wm>{MONTH_RX})\.?\s+(?P<wd>\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(?P<wy>20\d{{2}}))?\b"
_D_DMY = rf"\b(?P<dd>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<dm>{MONTH_RX})\b\.?(?:\s*,?\s*(?P<dy>20\d{{2}}))?"
DATE_RX = re.compile(rf"(?:\b(?P<wk>{WD_RX})\.?,?\s+)?(?:{_D_ISO}|{_D_NUM}|{_D_MDY}|{_D_DMY})", re.I)


def school_year_start(ref: date) -> int:
    return ref.year if ref.month >= 8 else ref.year - 1


def infer_year(month: int, day: int, ref: date, weekday: int | None = None) -> date | None:
    """A date written without a year means the one inside the school year (Aug-Jul) that `ref` (the file's last-modified day) is in.
    When the writer also gave the weekday ('Tue 11/17') and that disagrees, the neighbouring years are tried."""
    sy = school_year_start(ref)
    first = date_or_none(sy if month >= 8 else sy + 1, month, day)
    if first is not None and (weekday is None or first.weekday() == weekday):
        return first
    for dy in (1, -1, 2, -2):
        alt = date_or_none((sy if month >= 8 else sy + 1) + dy, month, day)
        if alt is not None and weekday is not None and alt.weekday() == weekday:
            return alt
    return first


def date_or_none(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def serial_to_date(v: float) -> date | None:
    """Google Sheets serial day number -> date (None outside 1982..2064, so an ordinary number is never mistaken for a date)."""
    if 30000 <= v <= 60000:
        return SERIAL_EPOCH + timedelta(days=int(v))
    return None


def _wk(m: re.Match) -> int | None:
    w = (m.group("wk") or "")[:3].lower()
    return [x.lower() for x in DAY_ABBR].index(w) if w else None


def find_dates(text: str, ref: date) -> list[tuple[date, tuple[int, int]]]:
    """Every date written in the text -> [(date, (start, end) span)]. Year-less dates use the school year of `ref`."""
    out = []
    for m in DATE_RX.finditer(text or ""):
        d = None
        wk = _wk(m)
        if m.group("iy"):
            d = date_or_none(int(m.group("iy")), int(m.group("im")), int(m.group("id")))
        elif m.group("nm"):
            mo, da, y = int(m.group("nm")), int(m.group("nd")), m.group("ny")
            if not (1 <= mo <= 12 and 1 <= da <= 31):
                continue
            if y:
                yy = int(y)
                d = date_or_none(yy + 2000 if yy < 100 else yy, mo, da)
            else:
                d = infer_year(mo, da, ref, wk)
        elif m.group("wm"):
            mo = MONTHS[m.group("wm").lower()[:3]]
            d = date_or_none(int(m.group("wy")), mo, int(m.group("wd"))) if m.group("wy") else infer_year(mo, int(m.group("wd")), ref, wk)
        elif m.group("dm"):
            mo = MONTHS[m.group("dm").lower()[:3]]
            d = date_or_none(int(m.group("dy")), mo, int(m.group("dd"))) if m.group("dy") else infer_year(mo, int(m.group("dd")), ref, wk)
        if d:
            out.append((d, m.span()))
    return out


def parse_cell_date(cell: str, ref: date) -> tuple[date, date | None] | None:
    """A date cell ('Mon 11/16/26', '11/17', 'Nov 17', 46342, 'Mon 11/23/26 - Fri 11/27/26') -> (start, end or None)."""
    cell = (cell or "").strip()
    if not cell:
        return None
    if re.fullmatch(r"\d{5}(?:\.\d+)?", cell):
        d = serial_to_date(float(cell))
        return (d, None) if d else None
    found = find_dates(cell, ref)
    if not found:
        return None
    start = found[0][0]
    end = found[1][0] if len(found) > 1 and found[1][0] >= start else None
    return start, end


# --------------------------------------------------------------------------------------------------------------------- times
TBD = re.compile(r"^\s*(tbd|tba|tbc|to be (?:determined|announced|confirmed)|t\.b\.[ad]\.?)\s*\.?\s*$", re.I)
_T = r"(\d{1,2})(?::(\d{2}))?"
_AP = r"([ap])\.?m\.?"
TIME_RANGE = re.compile(rf"(?<![\d:/.]){_T}\s*(?:{_AP})?\s*(?:-|–|—|to|until|till)\s*{_T}\s*{_AP}(?![\w])", re.I)
TIME_ONE = re.compile(rf"(?<![\d:/.]){_T}\s*{_AP}(?![\w])", re.I)
TIME_24 = re.compile(r"(?<![\d:/.])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
NOON = re.compile(r"\bnoon\b", re.I)


def _hm(h: int, m: int, ap: str | None) -> tuple[int, int]:
    if ap:
        h = h % 12 + (12 if ap.lower() == "p" else 0)
    return h, m


def fmt_time(h: int, m: int) -> str:
    return f"{(h % 12) or 12}:{m:02d} {'PM' if h >= 12 else 'AM'}"


def parse_time(text: str) -> tuple[str | None, str | None, str]:
    """Free text or a cell -> (start 'HH:MM' or None, end 'HH:MM' or None, label). The label is how the time is shown ('3:45 PM',
    '4:00-5:30 PM', 'time to be announced'); an unreadable cell gives (None, None, '')."""
    text = (text or "").strip()
    if not text:
        return None, None, ""
    if TBD.match(text):
        return None, None, "time to be announced"
    if re.fullmatch(r"0?\.\d+|0|1", text) and 0 < float(text) < 1:                      # a time cell given as a day fraction (serial time)
        mins = round(float(text) * 24 * 60)
        h, m = divmod(mins, 60)
        return f"{h:02d}:{m:02d}", None, fmt_time(h, m)
    m = TIME_RANGE.search(text)
    if m:
        h1, m1, a1, h2, m2, a2 = int(m.group(1)), int(m.group(2) or 0), m.group(3), int(m.group(4)), int(m.group(5) or 0), m.group(6)
        eh, em = _hm(h2, m2, a2)
        sh, sm = _hm(h1, m1, a1 or a2)
        if not a1 and (sh, sm) >= (eh, em):                    # '11-1 PM': the start is in the morning
            sh, sm = _hm(h1, m1, "a" if a2.lower() == "p" else "p")
        if (sh, sm) < (eh, em) and 0 <= sh < 24:
            same = (sh >= 12) == (eh >= 12)
            label = f"{(sh % 12) or 12}:{sm:02d}-{fmt_time(eh, em)}" if same else f"{fmt_time(sh, sm)}-{fmt_time(eh, em)}"
            return f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}", label
    m = TIME_ONE.search(text)
    if m:
        h, mi = _hm(int(m.group(1)), int(m.group(2) or 0), m.group(3))
        if h < 24 and mi < 60:
            return f"{h:02d}:{mi:02d}", None, fmt_time(h, mi)
    if NOON.search(text):
        return "12:00", None, "12:00 PM"
    m = TIME_24.search(text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return f"{h:02d}:{mi:02d}", None, fmt_time(h, mi)
    return None, None, ""


# --------------------------------------------------------------------------------------------------------------------- weekdays
_DAYWORD = re.compile(rf"\b({WD_RX})s?\b\.?", re.I)
_RANGE_DAYS = re.compile(rf"\b({WD_RX})s?\.?\s*(?:-|–|—|to|through|thru)\s*({WD_RX})s?\b", re.I)
_LETTERS = re.compile(r"(?<![\w/])((?:Th|Tu|M|T|W|R|F)(?:\s*[/&,]\s*(?:Th|Tu|M|T|W|R|F))+)(?![\w/])")
_LETTER_MAP = {"m": 0, "t": 1, "tu": 1, "w": 2, "th": 3, "r": 3, "f": 4}


def _wd_index(word: str) -> int:
    return [x.lower() for x in DAY_ABBR].index(word[:3].lower())


def parse_days(text: str) -> list[int]:
    """'Tue/Thu', 'Tuesdays and Thursdays', 'Mon-Fri', 'weekdays', 'M/W/F' -> sorted weekday indexes (Mon = 0)."""
    days: set[int] = set()
    t = text or ""
    if re.search(r"\bweekdays\b", t, re.I):
        days |= {0, 1, 2, 3, 4}
    for m in _RANGE_DAYS.finditer(t):
        a, b = _wd_index(m.group(1)), _wd_index(m.group(2))
        days |= set(range(a, b + 1)) if a <= b else {a, b}
    t2 = _RANGE_DAYS.sub(" ", t)
    days |= {_wd_index(m.group(1)) for m in _DAYWORD.finditer(t2)}
    for m in _LETTERS.finditer(t2):
        days |= {_LETTER_MAP[x.lower()] for x in re.findall(r"Th|Tu|M|T|W|R|F", m.group(1))}
    return sorted(days)


def fmt_days(days: list[int]) -> str:
    if not days:
        return ""
    if len(days) >= 3 and days == list(range(days[0], days[-1] + 1)):
        return f"{DAY_ABBR[days[0]]}-{DAY_ABBR[days[-1]]}"
    return "/".join(DAY_ABBR[d] for d in days)


# --------------------------------------------------------------------------------------------------------------------- table columns
COLS = [
    ("date", r"^(game )?date$|^day$|^when$|^dates?\b"),
    ("time", r"^(game |start |tip[- ]?off |first pitch )?time$|^start$|^tip|^kick|^first pitch"),
    ("home", r"^home( team)?$"),
    ("away", r"^(away|visitor|visiting)( team)?s?$"),
    ("opp", r"^opp(onent)?s?$|^vs\.?$|^versus$|^against$|^(opposing )?(team|school)$|^game$|^matchup$"),
    ("loc", r"^(location|venue|site|field|gym|court|where|place|address)\b"),
    ("ha", r"^h\s*/\s*a$|^home\s*(/|or)\s*away$|^h-a$|^home/away$"),
    ("type", r"^(type|event|kind|activity)$"),
    ("note", r"^(notes?|comments?|details?|info|information|description)$"),
    ("dow", r"^(weekday|day of (the )?week)$"),
]


def map_columns(header: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, h in enumerate(header):
        low = re.sub(r"\s+", " ", (h or "").strip().lower().strip(":"))
        if not low:
            continue
        for key, rx in COLS:
            if key not in out and re.search(rx, low):
                out[key] = i
                break
    return out


def is_schedule_header(cols: dict) -> bool:
    return "date" in cols and ("opp" in cols or ("home" in cols and "away" in cols) or "type" in cols or "home" in cols)


_HA = {"h": "home", "home": "home", "a": "away", "away": "away", "@": "away", "vs": "home"}


def _clean_opp(s: str) -> tuple[str, str]:
    """'@ Menlo (Away)' -> ('Menlo', 'away'); 'vs. Menlo' -> ('Menlo', '')."""
    s = re.sub(r"\s+", " ", s or "").strip()
    ha = ""
    m = re.search(r"\((home|away)\)\s*$", s, re.I)
    if m:
        ha, s = m.group(1).lower(), s[:m.start()].strip()
    m = re.match(r"^(@|at\b)\s*(.+)$", s, re.I)
    if m:
        ha, s = ha or "away", m.group(2).strip()
    else:
        m = re.match(r"^(vs\.?|v\.|versus)\s*(.+)$", s, re.I)
        if m:
            s = m.group(2).strip()
    return s.strip(" -,"), ha


def schedule_events(header: list[str], rows: list[list[str]], ref: date, title_hint: str = "", our_names: tuple = ("nueva",), seen: set | None = None) -> list[dict]:
    """Table rows -> events. Each event: date, end_date, start, end, time, kind (game|practice|event), opp, home_away, location, note.
    Layouts: DATE | HOME TEAM | AWAY TEAM | TIME (the school's own sheets: the side starting with 'Nueva' is us) and DATE | OPPONENT | H/A | TIME | LOCATION.
    Rows without a readable date are skipped (never guessed); duplicate rows (the same game listed in a second 'home schedule' table) collapse."""
    cols = map_columns(header)
    if not is_schedule_header(cols):
        return []
    get = lambda r, k: (r[cols[k]].strip() if k in cols and cols[k] < len(r) else "")
    practice_table = bool(re.search(r"\bpractices?\b|\btraining\b", title_hint, re.I))
    out = []
    seen = set() if seen is None else seen          # shared across the tables of one file: sheets repeat the home games in a second table
    for r in rows:
        parsed = parse_cell_date(get(r, "date"), ref)
        if not parsed:
            continue
        d, end = parsed
        home, away, opp_c = get(r, "home"), get(r, "away"), get(r, "opp")
        kind, opp, ha, note = "event", "", "", ""
        typ = get(r, "type")
        if "home" in cols and "away" in cols:
            if away and _parse_is_date(home, ref) and not _parse_is_date(away, ref) and get(r, "loc"):
                home = get(r, "loc")                       # a date pasted into the Home cell: the opponent is the location column
            if away and home:
                ours = home.lower().startswith(our_names)
                opp, ha, kind = (away if ours else home), ("home" if ours else "away"), "game"
                if re.fullmatch(r"(?i)\s*bye\s*", opp):
                    continue                               # "Bye" is a week without a game (the team-sheet parser drops it too)
            else:
                note = home or away
        elif "opp" in cols:
            if opp_c and not re.fullmatch(r"(?i)bye|n/?a|-+", opp_c.strip()):
                opp, ha = _clean_opp(opp_c)
                kind = "game"
                ha = _HA.get(get(r, "ha").lower().strip(". "), ha) if get(r, "ha") else ha
            else:
                note = opp_c
        else:                                               # DATE | EVENT/TYPE | ... : a list of events
            note = typ or home
        if typ and re.search(r"practice|training|workout|conditioning", typ, re.I):
            kind, opp, ha = "practice", "", ""
        elif typ and re.search(r"game|match|scrimmage|tournament|meet\b|vs", typ, re.I) and opp:
            kind = "game"
        elif practice_table and kind == "event" and not note.strip():
            kind = "practice"
        elif practice_table and kind != "game":
            kind = "practice" if not re.search(r"first day|tryout|break|no practice|cancel", note, re.I) else "event"
        if kind == "event" and not note and not typ:
            continue                                        # nothing but a date
        if kind == "event" and not note:
            note = typ
        start, tend, label = parse_time(get(r, "time"))
        if not label and not get(r, "time") and kind != "event":
            label = ""
        loc = re.sub(r"\s+", " ", get(r, "loc")).strip()
        extra = re.sub(r"\s+", " ", get(r, "note")).strip()
        key = (d, kind, (opp or note or home).lower().strip())
        if key in seen:
            continue
        seen.add(key)
        out.append({"date": d, "end_date": end, "start": start, "end": tend, "time": label, "kind": kind, "opp": opp, "home_away": ha,
                    "location": loc, "note": (note if kind == "event" else extra)[:200]})
    return out


def _parse_is_date(cell: str, ref: date) -> bool:
    return bool(cell) and parse_cell_date(cell, ref) is not None


# --------------------------------------------------------------------------------------------------------------------- free text (emails, docs)
PRACTICE_KW = re.compile(r"\b(practices?|practicing|training|workouts?|conditioning|open gym|scrimmages?|meets?|meetings?|sessions?|rehearsals?)\b", re.I)
GAME_KW = re.compile(r"\b(games?|match(?:es)?|vs\.?|versus|against|tournament|invitational|scrimmage|opponent)\b|(?<!\w)@\s*[A-Z]", re.I)
LOC_LABEL = re.compile(r"^\s*(?:location|where|place|venue|field|court|gym|meet(?:ing)? (?:at|place|location))\s*:\s*(.+?)\s*$", re.I)
_AT = re.compile(r"(?:\bat|@|\bin)\s+(?:the\s+)?([^,.;()\n]{2,60})", re.I)
_PLACE_STOP = re.compile(r"\s+(?:and|with|from|starting|beginning|begins|through|until|unless|if|for|bring|please|weather|on|every|each|then)\b", re.I)
LIMIT_END = re.compile(r"\b(?:through|thru|until|till|ending|ends?)\s+", re.I)
LIMIT_START = re.compile(r"\b(?:starting|beginning|begins|starts?|from)\s+", re.I)


def _place_in(line: str, after: int = 0) -> str:
    for m in _AT.finditer(line, after):
        cand = _PLACE_STOP.split(m.group(1))[0].strip(" -:")
        if not cand or re.match(r"^(?:\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?|noon|\d)", cand, re.I) or _DAYWORD.search(cand) or re.match(MONTH_RX + r"\b", cand, re.I):
            continue
        if len(cand) >= 2:
            return cand[:60]
    return ""


def _location_default(text: str) -> str:
    for line in (text or "").splitlines():
        m = LOC_LABEL.match(line)
        if m and 2 <= len(m.group(1)) <= 80:
            return m.group(1).strip()
    return ""


def find_practice_slots(text: str, ref: date) -> list[dict]:
    """Practice / meeting times written in an announcement -> [{kind, days, start, end, time, location, start_date, end_date, date}]. A slot
    has either `days` (a weekly pattern, optionally bounded by start_date/end_date: 'through Dec 10') or one `date`. Lines about games are left
    to find_games. Slots with the same time and place are merged ('Tue' + 'Thu' -> 'Tue/Thu')."""
    lines = [re.sub(r"\s+", " ", l).strip(" -*•\t") for l in (text or "").splitlines()]
    default_loc, slots, ctx = _location_default(text), [], 0
    for i, line in enumerate(lines):
        if not line:
            ctx = 0
            continue
        kw = PRACTICE_KW.search(line)
        cells = split_cells(line)
        if kw and not parse_days(line):
            ctx = 4                                          # 'Practice schedule:' above a list of 'Tuesday 4-5:30 PM - Field' lines
        has_game = bool(GAME_KW.search(line)) and not kw
        if has_game or (cells and len(cells) >= 3):
            ctx = 0
            continue
        days = parse_days(line)
        start, end, label = parse_time(line)
        dates = find_dates(line, ref)
        in_ctx = ctx > 0 and not dates
        ctx = max(0, ctx - 1)
        if not label or not (kw or in_ctx):
            continue
        if re.search(r"\b(game|vs\.?|versus)\b", line, re.I) and not kw:
            continue
        kind = "Meeting" if kw and re.match(r"(?i)meet", kw.group(1)) and not re.search(r"practice|training|workout", line, re.I) else "Practice"
        lim_end = lim_start = one = None
        if dates:
            after_end = LIMIT_END.search(line)
            after_start = LIMIT_START.search(line)
            for d, (a, b) in dates:
                if after_end and after_end.end() <= a and a - after_end.end() <= 3:
                    lim_end = d
                elif after_start and after_start.end() <= a and a - after_start.end() <= 3:
                    lim_start = d
                elif re.search(r"(?:-|–|—|to)\s*$", line[:a]) and lim_start is None and len(dates) > 1:
                    lim_end = d
                elif not days and one is None:
                    one = d
                elif days and lim_start is None and len(dates) >= 2 and d == dates[0][0]:
                    lim_start = d
        if not days and not one:
            continue
        place = _place_in(line, kw.end() if kw else 0) or default_place(lines, i) or default_loc
        slots.append({"kind": kind, "days": days, "start": start, "end": end, "time": label, "location": place, "start_date": lim_start,
                      "end_date": lim_end, "date": one})
    return merge_slots(slots)


def default_place(lines: list[str], i: int) -> str:
    """A 'Location:' / 'Where:' line right above or below a slot line."""
    for j in (i - 1, i + 1):
        if 0 <= j < len(lines):
            m = LOC_LABEL.match(lines[j])
            if m:
                return m.group(1).strip()
    return ""


def merge_slots(slots: list[dict]) -> list[dict]:
    out: dict[tuple, dict] = {}
    for s in slots:
        if s["date"]:
            out[("d", s["date"], s["start"], s["kind"], s["location"])] = s
            continue
        k = ("w", s["kind"], s["start"], s["end"], s["location"].lower(), s["start_date"], s["end_date"])
        if k in out:
            out[k]["days"] = sorted(set(out[k]["days"]) | set(s["days"]))
        else:
            out[k] = dict(s)
    return list(out.values())


def slot_line(s: dict) -> str:
    when = s["date"].strftime("%a %b %-d") if s["date"] else fmt_days(s["days"])
    span = ""
    if s["start_date"] and s["end_date"]:
        span = f" ({s['start_date'].strftime('%b %-d')} to {s['end_date'].strftime('%b %-d')})"
    elif s["end_date"]:
        span = f" (through {s['end_date'].strftime('%b %-d')})"
    elif s["start_date"]:
        span = f" (starting {s['start_date'].strftime('%b %-d')})"
    return f"{s['kind']}: {when} {s['time']}" + (f" at {s['location']}" if s["location"] else "") + span


_SPLIT_BAR = re.compile(r"\s*\|\s*")


def split_cells(line: str) -> list[str] | None:
    if "|" in line:
        cells = [c.strip() for c in _SPLIT_BAR.split(line.strip().strip("|"))]
    elif "\t" in line:
        cells = [c.strip() for c in line.split("\t")]
    elif re.search(r"\S\s{3,}\S", line):
        cells = [c.strip() for c in re.split(r"\s{3,}", line.strip())]
    else:
        return None
    return cells if len([c for c in cells if c]) >= 2 else None


def find_games(text: str, ref: date, title_hint: str = "") -> list[dict]:
    """Games written in an announcement: a table (pipe/tab/aligned columns with a Date and Opponent header) or lines such as
    'Sat 11/21 vs Menlo 10:00 AM (Home)' / '11/25 @ Priory, 4:30 PM'. Returns the schedule_events() dict shape."""
    lines = (text or "").splitlines()
    games: list[dict] = []
    i = 0
    used: set[int] = set()
    while i < len(lines):
        cells = split_cells(lines[i])
        if cells and is_schedule_header(map_columns(cells)):
            rows, j = [], i + 1
            while j < len(lines):
                c = split_cells(lines[j])
                if not c:
                    if lines[j].strip():
                        break
                    j += 1
                    if j < len(lines) and not split_cells(lines[j]):
                        break
                    continue
                rows.append(c)
                used.add(j)
                j += 1
            used.add(i)
            games += schedule_events(cells, rows, ref, title_hint)
            i = j
            continue
        i += 1
    for k, line in enumerate(lines):
        if k in used or split_cells(line) and len(split_cells(line)) >= 3:
            continue
        g = game_from_line(line, ref)
        if g:
            games.append(g)
    seen, out = set(), []
    for g in games:
        key = (g["date"], g["opp"].lower(), g["start"], g["kind"], g["note"].lower())
        if key not in seen:
            seen.add(key)
            out.append(g)
    return sorted(out, key=lambda g: (g["date"], g["start"] or ""))


_OPP = re.compile(r"(?:\b(?:vs\.?|v\.|versus|against)\s+|(?<!\w)@\s*)(?P<o>[^,;()|\n]+)", re.I)


def game_from_line(line: str, ref: date) -> dict | None:
    line = re.sub(r"\s+", " ", line).strip(" -*•\t")
    dates = find_dates(line, ref)
    if not dates or not GAME_KW.search(line) or PRACTICE_KW.search(line) and not re.search(r"\b(game|vs\.?|versus|against)\b|@", line, re.I):
        return None
    d, (da, db) = dates[0]
    m = _OPP.search(line, db)
    if not m:
        m = _OPP.search(line)
    if not m:
        return None
    opp_raw = re.split(r"\s+(?:at|@|on|from|starting|\d{1,2}(?::\d{2})?\s*[ap]\.?m)\b|\s+-\s+|\s+[–—]\s+", m.group("o"), maxsplit=1, flags=re.I)[0]
    opp_end = m.start("o") + len(opp_raw)
    opp, ha = _clean_opp(("@ " if m.group(0).lstrip().startswith("@") else "") + opp_raw)
    if not opp or len(opp) > 50 or len(opp) < 2 or PRACTICE_KW.fullmatch(opp):
        return None
    rest = line[:da] + " " + line[db:]
    if re.search(r"\(home\)|\bhome game\b|\bhome\b(?!\s*(?:team|court|field)\b)", rest, re.I) and not ha:
        ha = "home"
    elif re.search(r"\(away\)|\baway\b", rest, re.I) and not ha:
        ha = "away"
    start, end, label = parse_time(line)
    loc = _place_in(line, opp_end) or ""
    if loc and loc.lower().startswith(opp.lower()):
        loc = ""
    return {"date": d, "end_date": None, "start": start, "end": end, "time": label, "kind": "game", "opp": opp, "home_away": ha,
            "location": loc, "note": ""}


def schedule_summary(slots: list[dict], games: list[dict]) -> str:
    """A short clean block to put on top of the cleaned text: 'Practice: Tue/Thu 4:00-5:30 PM at Field 2' and one line per game."""
    lines = [slot_line(s) for s in slots]
    for g in games[:40]:
        when = g["date"].strftime("%a %b %-d")
        who = f"vs {g['opp']}" if g["kind"] == "game" else g["note"] or g["kind"]
        lines.append(f"{'Game' if g['kind'] == 'game' else 'Event'}: {when} {g['time'] or 'time TBA'} {who}"
                     + (f" ({g['home_away'].title()})" if g["home_away"] else "") + (f" at {g['location']}" if g["location"] else ""))
    return ("Schedule summary:\n" + "\n".join(f"- {l}" for l in lines)) if lines else ""


def practice_events(slots: list[dict], horizon_days: int = 120, max_events: int = 60) -> list[dict]:
    """Dated practice occurrences, but ONLY where the announcement pins them down: a single dated practice, or a weekly pattern bounded
    by an explicit end date ('through Dec 10', 'Nov 3 - Dec 12'). An open-ended 'Tue/Thu 4:00' stays a text summary: inventing a
    season's worth of future events would outlive the season."""
    out = []
    for s in slots:
        base = {"end_date": None, "start": s["start"], "end": s["end"], "time": s["time"], "kind": "practice" if s["kind"] == "Practice" else "event",
                "opp": "", "home_away": "", "location": s["location"], "note": s["kind"] if s["kind"] != "Practice" else ""}
        if s["date"]:
            out.append({**base, "date": s["date"]})
        elif s["days"] and s["end_date"]:
            d = s["start_date"] or (s["end_date"] - timedelta(days=min(horizon_days, 84)))
            while d <= s["end_date"] and len(out) < max_events:
                if d.weekday() in s["days"]:
                    out.append({**base, "date": d})
                d += timedelta(days=1)
    return out


# --------------------------------------------------------------------------------------------------------------------- team context
SPORT_LABEL = {"ultimate": "Ultimate Frisbee", "swim": "Swim", "cross country": "Cross Country", "water polo": "Water Polo", "track": "Track and Field",
               "tennis": "Tennis", "pickleball": "Pickleball"}
GENERIC_TAB = re.compile(r"^(sheet\s*\d*|schedule|schedules|games?|calendar|master|main|all|data|summary|tab\s*\d*|copy of .*)$", re.I)
NOISE_WORDS = re.compile(r"\b(schedules?|calendar|season|master|copy of|20\d{2}\s*[-–/]\s*(?:20)?\d{2}|\d{2}\s*[-–]\s*\d{2}|20\d{2}|games?|team|the)\b", re.I)


def derive_context(title: str, tab: str = "", extra: str = "", ref: date | None = None) -> dict:
    """File title + tab (+ any header text) -> {team, sport, gender, level, division, season, school_year, category}.
    The team label is built the way the school's own sheets name theirs ('US Boys Basketball Varsity'), so rag.py's structured lookups
    (sport word, boys/girls, 'US ' prefix for varsity/JV) find it."""
    from intent import SPORTS            # root-level module, shared with the retrieval code
    import freshness
    ref = ref or date.today()
    blob = " ".join(x for x in (title, tab, extra) if x)
    low = blob.lower()
    sport = next((SPORTS[k] for k in sorted(SPORTS, key=len, reverse=True) if re.search(rf"\b{re.escape(k)}\b", low)), None)
    gender = "Girls" if re.search(r"\b(girls?|women'?s?|female)\b", low) else "Boys" if re.search(r"\b(boys?|men'?s?|male)\b", low) else \
        "Co-ed" if re.search(r"\bco-?ed\b|\bmixed\b", low) else ""
    level = "Varsity" if re.search(r"\bvarsity\b|\bvar\b", low) else "JV" if re.search(r"\bjv\b|junior varsity", low) else \
        "Frosh" if re.search(r"\b(frosh|freshman|freshmen)\b", low) else ""
    division = "MS" if re.search(r"\bmiddle school\b|\bms\b", low) else "US" if (level or re.search(r"\b(upper school|high school)\b|\bus\b", low)) else \
        "LS" if re.search(r"\blower school\b|\bls\b", low) else ""
    season = next((s.title() for s in ("fall", "winter", "spring") if re.search(rf"\b{s}\b", low)), "")
    sy = freshness.school_years_in(blob)
    school_year = freshness.label(min(sy)) if sy else freshness.label(school_year_start(ref))
    if sport:
        s_label = SPORT_LABEL.get(sport, sport.title())
        team = " ".join(x for x in (division, gender if gender != "Co-ed" else "Co-ed", s_label, level) if x)
    else:
        team = ""
        names = [t for t in (title, tab) if t and not GENERIC_TAB.match(t.strip())]
        raw = " ".join(names) if names else title or ""
        team = re.sub(r"\s+", " ", NOISE_WORDS.sub(" ", raw)).strip(" -–:|")[:80] or (title or "")[:80]
    category = "athletics" if sport or re.search(r"\b(varsity|jv|athletics|games?|opponent|practice)\b", low) else \
        "clubs" if re.search(r"\bclubs?\b|\baffinity\b", low) else "general"
    return {"team": team, "sport": sport or "", "gender": gender, "level": level, "division": division, "season": season,
            "school_year": school_year, "category": category}


# --------------------------------------------------------------------------------------------------------------------- event records
def event_summary(e: dict, team: str) -> str:
    if e["kind"] == "game":
        return f"{team} - Game {e['opp']}".rstrip() + (f" - {e['home_away'].title()}" if e["home_away"] else "")
    if e["kind"] == "practice":
        return f"{team} - Practice"
    note = e["note"].title() if e["note"].isupper() else e["note"]
    return f"{team} - {note}" if note else team


def event_chunk(e: dict, ctx: dict, *, id_prefix: str, source_url: str, source_label: str, extra_meta: dict, used_ids: set | None = None) -> dict:
    """One dated event in the schema pipeline/sheets.event_chunk writes (source=calendar_sheet, kind game|practice|event, event_date, end_date,
    summary, when, time, all_day, start_time, location, category, home_away). `id_prefix` is the owning feature's id prefix."""
    import guard
    e = {**e, **{k: guard.redact_contacts(e.get(k) or "")[0] for k in ("note", "location", "opp")}}        # no non-staff address or number in a record
    summary = event_summary(e, ctx["team"] or ctx.get("title", "Schedule"))
    d = e["date"]
    when = d.strftime("%A, %B %-d, %Y")
    t_label = e["time"] or ("time to be announced" if e["kind"] == "game" else "all day")
    lines = [f"Calendar event: {summary}", f"Date: {when}", f"Time: {t_label}" + (" (Pacific)" if e["start"] else "")]
    if e["location"]:
        lines.append(f"Location: {e['location']}")
    if e["note"]:
        lines.append(f"Note: {e['note']}")
    content = f"Title: {summary}\nSource: {source_label}\n\n" + "\n".join(lines)
    extra = {"end_date": (e.get("end_date") or d).isoformat(), "summary": summary, "when": when, "time": t_label, "all_day": not e["start"],
             "start_time": e["start"], "end_time": e["end"], "location": e["location"], "description": e["note"], "category": ctx["category"],
             "kind": e["kind"], "home_away": e["home_away"]}
    cid = id_prefix + hashlib.sha1(f"{d}|{summary}|{e['start']}|{e['location']}".encode()).hexdigest()[:8]
    if used_ids is not None:
        n = 1
        base = cid
        while cid in used_ids:
            n += 1
            cid = f"{base}{n}"
        used_ids.add(cid)
    meta = {"source": "calendar_sheet", "source_url": source_url, "title": summary, "event_date": d.isoformat(), **extra,
            "team": ctx["team"], "sport": ctx["sport"], "gender": ctx["gender"], "level": ctx["level"], "division": ctx["division"],
            "season": ctx["season"], **extra_meta}
    meta["content_hash"] = hashlib.sha1((content + str(sorted(extra.items(), key=lambda kv: kv[0]))).encode()).hexdigest()
    return {"chunk_id": cid, "content": content, "source_file": source_url, "metadata": meta}
