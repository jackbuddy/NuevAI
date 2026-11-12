"""The school's public Upper School athletics calendar (nuevaschool.org/school-life/athletics/athletics-calendar) -> team events.

This is an OFFICIAL source: the same Finalsite calendar the athletics office publishes for parents (calendar id 375). It covers every
Upper School team that has a schedule (the private whole-school iCal feed only carries Middle School games), including tennis, golf,
cross-country and soccer which have no team Google Sheet. The month grid is server-rendered HTML, so no login, no script and no
private token is involved. Fetching is polite: robots.txt is read first, one GET per month, >= 5 s apart (the site's Crawl-delay),
a clear User-Agent, and a 20 h cache in data/pipeline/athletics_calendar/.

    sports stage (pipeline/sports.py)  ->  fetch_months()  ->  parse_month()  ->  events()  ->  merged with the team sheets

The page lists no venue ("Click for Location" opens a pop-up) and no opponent address; the home venue comes from the Upper School
Athletics Handbook (pipeline/sports.py OFFICIAL_TEAMS).
"""
import html as htmllib
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib import robotparser

import requests

from . import config

BASE = "https://www.nuevaschool.org"
PAGE = BASE + "/school-life/athletics/athletics-calendar"
ATHLETICS_HOME = BASE + "/school-life/athletics"
UA = "NuevAI-bot (school info assistant)"
DELAY = 5.5
CACHE_HOURS = 20
CACHE_DIR = config.DATA_DIR / "athletics_calendar"

SPORTS = [  # (regex on the title, canonical sport)
    (r"beach volleyball", "Beach Volleyball"), (r"volleyball", "Volleyball"), (r"basketball", "Basketball"), (r"soccer", "Soccer"),
    (r"tennis", "Tennis"), (r"golf", "Golf"), (r"cross[- ]country", "Cross Country"), (r"track", "Track & Field"),
    (r"swim|dive|diving", "Swimming & Diving"), (r"flag football", "Flag Football"), (r"ultimate", "Ultimate Frisbee"),
]
POSTSEASON = re.compile(r"\b(CCS|CIF|PSAL|WBAL|NCS|Invitational|Classic|Tournament|Championships?|Playoffs?|Qualifier|Meet|Preview|Semi-?finals?|"
                        r"Finals?|Quarterfinals?|Showcase|Festival|Memorial)\b", re.I)


# --------------------------------------------------------------------------- fetch
def month_starts(first: date, last: date) -> list[date]:
    out, d = [], date(first.year, first.month, 1)
    while d <= last:
        out.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def school_year_months(today: date) -> list[date]:
    start_year = today.year if today.month >= 8 else today.year - 1
    return month_starts(date(start_year, 8, 1), date(start_year + 1, 6, 1))


def _robots_ok(url: str) -> bool:
    rp = robotparser.RobotFileParser()
    try:
        r = requests.get(BASE + "/robots.txt", timeout=30, headers={"User-Agent": UA})
        rp.parse(r.text.splitlines())
    except requests.RequestException:
        return False          # when robots.txt cannot be read, do not crawl
    return rp.can_fetch(UA, url)


def fetch_month(month: date, session=None, force: bool = False) -> str | None:
    """HTML of one month of the athletics calendar (cached 20 h). None when it could not be fetched."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / f"{month:%Y-%m}.html"
    if f.exists() and not force and time.time() - f.stat().st_mtime < CACHE_HOURS * 3600:
        return f.read_text(encoding="utf-8")
    url = f"{PAGE}?cal_date={month:%Y-%m-01}"
    s = session or requests
    try:
        r = s.get(url, timeout=40, headers={"User-Agent": UA})
    except requests.RequestException:
        return f.read_text(encoding="utf-8") if f.exists() else None
    if r.status_code != 200 or "fsCalendar" not in r.text:
        return f.read_text(encoding="utf-8") if f.exists() else None
    f.write_text(r.text, encoding="utf-8")
    return r.text


def fetch_months(months: list[date], log=print) -> dict[date, str]:
    if not _robots_ok(PAGE):
        log("athletics calendar: robots.txt disallows or could not be read; not fetching")
        return {}
    out, session, last = {}, requests.Session(), 0.0
    for m in months:
        f = CACHE_DIR / f"{m:%Y-%m}.html"
        fresh = f.exists() and time.time() - f.stat().st_mtime < CACHE_HOURS * 3600
        if not fresh:
            time.sleep(max(0.0, DELAY - (time.time() - last)))
            last = time.time()
        h = fetch_month(m, session)
        if h:
            out[m] = h
    return out


def fetch_sports_offered(session=None) -> str | None:
    """The 'Sports Offered' block of the athletics landing page (the list of Middle and Upper School teams by season), as plain text."""
    f = CACHE_DIR / "athletics-home.html"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if f.exists() and time.time() - f.stat().st_mtime < CACHE_HOURS * 3600:
        h = f.read_text(encoding="utf-8")
    else:
        if not _robots_ok(ATHLETICS_HOME):
            return None
        try:
            r = (session or requests).get(ATHLETICS_HOME, timeout=40, headers={"User-Agent": UA})
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        h = r.text
        f.write_text(h, encoding="utf-8")
    return sports_offered_text(h)


def sports_offered_text(h: str) -> str | None:
    h = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", h)
    t = htmllib.unescape(re.sub(r"(?s)<[^>]+>", "\n", h))
    t = re.sub(r"[ \t]+", " ", t)
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    try:
        a = next(i for i, l in enumerate(lines) if l == "Sports Offered")
        b = next(i for i, l in enumerate(lines) if i > a and l.startswith("Upcoming Upper School Competitions"))
    except StopIteration:
        return None
    return "Sports Offered (nuevaschool.org/school-life/athletics)\n" + "\n".join(lines[a + 1:b])


# --------------------------------------------------------------------------- parse
def parse_month(h: str) -> list[dict]:
    """-> [{date, title, start 'HH:MM' | None, end 'HH:MM' | None}] for every event in one month grid."""
    out = []
    for box in re.split(r'<div class="fsCalendarDaybox', h)[1:]:
        d = re.search(r'data-day="(\d+)" data-year="(\d+)" data-month="(\d+)"', box)
        mon = re.search(r'fsCalendarMonth">(\w+)</span>', box)
        if not d or not mon:
            continue
        try:
            day = datetime.strptime(f"{d.group(2)} {mon.group(1)} {d.group(1)}", "%Y %B %d").date()
        except ValueError:
            continue
        for ev in re.split(r"fsCalendarEventTitle", box)[1:]:
            t = re.search(r'title="([^"]+)"', ev)
            if not t:
                continue
            st = re.search(r'<time datetime="([^"]+)" class="fsStartTime"', ev)
            en = re.search(r'<time datetime="([^"]+)" class="fsEndTime"', ev)
            out.append({"date": day, "title": re.sub(r"\s+", " ", htmllib.unescape(t.group(1))).strip(),
                        "start": st.group(1)[11:16] if st else None, "end": en.group(1)[11:16] if en else None})
    seen, uniq = set(), []
    for e in out:
        k = (e["date"], e["title"], e["start"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq


def _sport_of(text: str) -> str | None:
    t = text.lower()
    return next((name for rx, name in SPORTS if re.search(rx, t)), None)


def parse_title(title: str) -> dict | None:
    """'US Girls Varsity Tennis VS. Mercy' -> {gender, level, sport, home_away, opponent, event, scrimmage, tournament}. None when the
    title is not a team event (a parent meeting, an unprefixed event)."""
    m = re.match(r"^US\s+(?P<rest>.+)$", title.strip())
    if not m:
        return None
    rest = m.group("rest")
    parts = re.split(r"\s+(VS\.?|V\.S\.?|@)\s+", rest, maxsplit=1, flags=re.I)
    head = parts[0]
    sep = parts[1].upper().rstrip(".") if len(parts) > 2 else ""
    tail = parts[2].strip() if len(parts) > 2 else ""
    venue = ""
    if re.search(r"\s&\s", head):                      # a golf tri-match: "US Girls Golf & Notre Dame San Jose @ Crystal Springs"
        head, partner = re.split(r"\s&\s", head, maxsplit=1)
        head, tail, venue, sep = head, partner.strip(), tail, "&"
    sport = _sport_of(head)
    if not sport:
        return None
    low = head.lower()
    gender = "girls" if re.search(r"\bgirls?\b", low) else "boys" if re.search(r"\bboys?\b", low) else "coed"
    level = "JV" if re.search(r"\bjv\b|junior varsity", low) else "Varsity" if re.search(r"\bvarsity\b", low) else ""
    scrim = bool(re.search(r"\(\s*scrimmage\s*\)", tail, re.I))
    friendship = bool(re.search(r"friendship game", tail, re.I))
    opp = re.sub(r"\s*\((scrimmage|friendship game)\)\s*", "", tail, flags=re.I).strip()
    tournament = sep == "@" and bool(POSTSEASON.search(tail))
    home_away = "" if tournament or sep == "&" else ("home" if sep in ("VS", "V.S") else "away" if sep == "@" else "")
    if not sep:          # "US Cross Country (JV Only)" and the like carry no opponent
        opp = ""
    return {"sport": sport, "gender": gender, "level": level, "home_away": home_away, "opponent": opp, "tournament": tournament,
            "scrimmage": scrim, "friendship": friendship, "venue": venue, "jv_only": bool(re.search(r"jv only", head, re.I))}


def team_label(p: dict) -> str:
    g = {"girls": "Girls ", "boys": "Boys ", "coed": ""}[p["gender"]]
    return f"US {g}{p['sport']}" + (f" {p['level']}" if p["level"] else "")


def events(raw: list[dict], url: str = PAGE) -> list[dict]:
    """Calendar entries -> event dicts in the shape pipeline/sheets.event_chunk() takes (plus 'meta' for the structured fields)."""
    out = []
    for e in raw:
        p = parse_title(e["title"])
        start = e["start"]
        t_label = None
        if start:
            h, mi = int(start[:2]), start[3:5]
            t_label = f"{h % 12 or 12}:{mi} {'PM' if h >= 12 else 'AM'}" + (f" to {int(e['end'][:2]) % 12 or 12}:{e['end'][3:5]} {'PM' if int(e['end'][:2]) >= 12 else 'AM'}" if e["end"] else "")
        if not p:        # a team-less athletics entry (a parent meeting, "Clovis Invitational"): keep as an athletics event
            if not re.search(r"athlet|invitational|sports|meeting", e["title"], re.I):
                continue
            out.append({"date": e["date"], "summary": e["title"], "kind": "event", "home_away": "", "start": start, "time": t_label or "all day",
                        "location": "", "url": url, "opp": "", "source_rank": 2,
                        "meta": {"provenance": "school athletics calendar page", "official": True}})
            continue
        label = team_label(p)
        if p["opponent"] and not p["tournament"]:
            ha = p["home_away"]
            extra = " (scrimmage)" if p["scrimmage"] else " (Friendship Game)" if p["friendship"] else ""
            summary = f"{label} - Game {p['opponent']}{extra} - {ha.title()}" if ha else f"{label} - Game {p['opponent']}{extra}"
            kind = "game"
        elif p["tournament"] or p["opponent"]:
            summary, kind = f"{label} - {p['opponent']}", "game"          # tournament / postseason / invitational
        else:
            summary, kind = f"{label} - {e['title'][3:]}", "event"
        out.append({"date": e["date"], "summary": summary, "kind": kind, "home_away": p["home_away"], "start": start,
                    "time": t_label or ("all day" if kind != "game" or p["tournament"] else "time to be announced"), "location": p["venue"], "url": url,
                    "opp": "" if p["tournament"] else p["opponent"], "source_rank": 2,
                    "meta": {"sport": p["sport"], "gender": p["gender"], "level": p["level"].lower(), "provenance": "school athletics calendar page",
                             "official": True, "scrimmage": p["scrimmage"], "tournament": p["tournament"], 
                             "title_on_calendar": e["title"]}})
    return out


def events_from_cache(months: list[date] | None = None) -> list[dict]:
    """Parse whatever month pages are cached (the chunk stage reads this; it never touches the network)."""
    if not CACHE_DIR.exists():
        return []
    out = []
    for f in sorted(CACHE_DIR.glob("20??-??.html")):
        if months and date(int(f.stem[:4]), int(f.stem[5:7]), 1) not in months:
            continue
        out += events(parse_month(f.read_text(encoding="utf-8")))
    seen, uniq = set(), []
    for e in out:                      # a month grid also shows the neighbouring months' edge days: the same entry appears twice
        k = (e["date"], e["summary"], e["start"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq


def as_of() -> str | None:
    if not CACHE_DIR.exists():
        return None
    fs = list(CACHE_DIR.glob("20??-??.html"))
    return datetime.fromtimestamp(max(f.stat().st_mtime for f in fs), timezone.utc).date().isoformat() if fs else None
