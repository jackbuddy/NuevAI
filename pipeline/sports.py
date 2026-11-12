"""Upper School sports: the verified team catalog, the schedule merge (sheets + official athletics calendar + third-party gap fill) and
the 'sports' pipeline stage.

SOURCE HIERARCHY (owner's rule; every fact says where it came from)
  1. official school sources: team schedule Google Sheets (rank 1), the public athletics calendar page (rank 2, pipeline/athletics_calendar.py),
     the Upper School Athletics Handbook, Student & Family Handbook, PE page, athletics pages, school emails
  2. league pages (PSAL / WBAL / CCS): only to confirm what (1) says
  3. MaxPreps (rank 3) and other third-party sites: SUPPORTING evidence only. Never overrides (1). Ingested only for teams that
     OFFICIAL_TEAMS confirms (MaxPreps lists placeholder teams, e.g. Girls Softball, that Nueva does not have), only to fill a game the
     official sources lack or to attach a public result, always labelled "third-party" with its fetch date.

MERGE RULE (one game, one record): events are matched on (sport, gender, varsity/JV, date, opponent); the lowest rank wins every field
it has, higher ranks only fill what is missing (time, venue), a conflict is kept as a note ('time_note'), and a third-party game is
added only when no official source lists that team on that date.

The stage (`python run_pipeline.py --stages sports`) fetches the month pages of the athletics calendar and the MaxPreps schedule pages,
politely (robots.txt, >= 5 s apart, clear User-Agent, 20 h cache). The chunk stage reads only the caches (no network).
"""
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib import robotparser

import requests

from . import athletics_calendar as ac
from . import config
from .common import save_page

NUEVA_MAXPREPS = "https://www.maxpreps.com/ca/san-mateo/the-nueva-school-mavericks"
NUEVA_MP_ID = "32102f3d-6d77-4ed8-abad-27f4e864d72e"
MP_DIR = config.DATA_DIR / "thirdparty" / "maxpreps"
UA = ac.UA
GYM = "Nueva US Gym (San Mateo)"

# --------------------------------------------------------------------------- the verified catalog (2026-27 Upper School)
# One row per sport/gender team group. `levels` are the levels with games on an official schedule ("JV" only when an official source lists
# JV games; handbook: a JV team is created where there is interest). `confidence` high = two or more official sources agree AND a game/practice
# is on an official schedule; medium = official lists agree, no schedule yet; low = a single official list or sources conflict.
OFFICIAL_TEAMS = [
    dict(sport="Cross Country", gender="girls", season="Fall", levels=["Varsity"], league="PSAL (Bay Division); CCS and CIF championships",
         home="Hallmark Park and the Nueva Hillsborough campus (handbook); senior-night race at Crystal Springs Cross Country Course (email)",
         practice="Various Bay Area locations; Bay Meadows Park", contact="Robert Lopez, Head Coach of Cross Country and Track & Field",
         confidence="high", mp="cross-country/girls", mp_has_data=False),
    dict(sport="Cross Country", gender="boys", season="Fall", levels=["Varsity", "JV"], league="PSAL (Bay Division); CCS and CIF championships",
         home="Hallmark Park and the Nueva Hillsborough campus (handbook); senior-night race at Crystal Springs Cross Country Course (email)",
         practice="Various Bay Area locations; Bay Meadows Park", contact="Robert Lopez, Head Coach of Cross Country and Track & Field",
         confidence="high", mp="cross-country", mp_has_data=False),
    dict(sport="Volleyball", gender="girls", season="Fall", levels=["Varsity", "JV"], league="PSAL", home=GYM, practice=GYM,
         contact="coach not published (athletics@nuevaschool.org)", confidence="high", mp="volleyball", mp_has_data=True),
    dict(sport="Tennis", gender="girls", season="Fall", levels=["Varsity", "JV"], league="WBAL (supplemental member); WBAL/CCS playoffs",
         home="Cañada College (handbook)", practice="Cañada College", contact="coach not published (athletics@nuevaschool.org)",
         confidence="high", mp="tennis/girls", mp_has_data=False),
    dict(sport="Golf", gender="girls", season="Fall", levels=["Varsity"], league="WBAL (supplemental member); CCS Team & Individual Championships",
         home="Crystal Springs Golf Course (handbook venue table, listed for Boys Golf); a senior-night match was at Baylands Golf Course (email)",
         practice="Stanford Golf Course (handbook)", contact="coach not published (athletics@nuevaschool.org)", confidence="high", mp=None, mp_has_data=False),
    dict(sport="Basketball", gender="boys", season="Winter", levels=["Varsity", "JV"], league="PSAL; CCS, NorCal and CIF playoffs", home=GYM,
         practice=GYM + " (shared gym grid, see practice record)", contact="coach not published (athletics@nuevaschool.org)", confidence="high",
         mp="basketball", mp_has_data=True),
    dict(sport="Basketball", gender="girls", season="Winter", levels=["Varsity"], league="PSAL; CCS playoffs", home=GYM,
         practice=GYM + " (shared gym grid, see practice record)", contact="coach not published (athletics@nuevaschool.org)", confidence="high",
         mp="basketball/girls", mp_has_data=True),
    dict(sport="Soccer", gender="boys", season="Winter", levels=["Varsity"], league="PSAL; CCS playoffs", home="Skyline College (handbook)",
         practice="Skyline College; Nueva MS campus (Hillsborough)", contact="coach not published (athletics@nuevaschool.org)", confidence="high",
         mp="soccer/winter", mp_has_data=True),
    dict(sport="Soccer", gender="girls", season="Winter", levels=["Varsity"], league="PSAL; CCS playoffs", home="Skyline College (handbook)",
         practice="Skyline College; Nueva MS campus (Hillsborough)", contact="coach not published (athletics@nuevaschool.org)", confidence="high",
         mp="soccer/girls/winter", mp_has_data=True),
    dict(sport="Volleyball", gender="boys", season="Spring", levels=["Varsity"], league="PSAL", home=GYM, practice=GYM,
         contact="coach not published (athletics@nuevaschool.org)", confidence="medium", mp="volleyball/boys", mp_has_data=True),
    dict(sport="Beach Volleyball", gender="girls", season="Spring", levels=["Varsity"], league="not stated in any official source",
         home="Notre Dame Belmont (handbook)", practice="Sports House (handbook)", contact="coach not published (athletics@nuevaschool.org)",
         confidence="medium", mp=None, mp_has_data=False),
    dict(sport="Tennis", gender="boys", season="Spring", levels=["Varsity"], league="WBAL (supplemental member)", home="Skyline College (handbook)",
         practice="Skyline College", contact="coach not published (athletics@nuevaschool.org)", confidence="medium", mp="tennis", mp_has_data=False),
    dict(sport="Golf", gender="boys", season="Spring", levels=["Varsity"], league="WBAL (supplemental member)",
         home="Crystal Springs Golf Course (handbook)", practice="Stanford Golf Course (handbook)",
         contact="coach not published (athletics@nuevaschool.org)", confidence="medium", mp="golf/spring", mp_has_data=False),
    dict(sport="Swimming & Diving", gender="girls", season="Spring", levels=["Varsity"], league="WBAL (supplemental member)",
         home="various Bay Area locations", practice="Bay Club", contact="coach not published (athletics@nuevaschool.org)", confidence="medium",
         mp="swimming/girls/spring", mp_has_data=False),
    dict(sport="Swimming & Diving", gender="boys", season="Spring", levels=["Varsity"], league="WBAL (supplemental member)",
         home="various Bay Area locations", practice="Bay Club", contact="coach not published (athletics@nuevaschool.org)", confidence="medium",
         mp="swimming", mp_has_data=False),
    dict(sport="Track & Field", gender="girls", season="Spring", levels=["Varsity"], league="PSAL (athletics handbook) or WBAL (Student & Family Handbook): sources differ",
         home="various Bay Area locations", practice="College of San Mateo; Skyline College", contact="Robert Lopez, Head Coach of Cross Country and Track & Field",
         confidence="medium", mp="track-field/girls", mp_has_data=False),
    dict(sport="Track & Field", gender="boys", season="Spring", levels=["Varsity"], league="PSAL (athletics handbook) or WBAL (Student & Family Handbook): sources differ",
         home="various Bay Area locations", practice="College of San Mateo; Skyline College", contact="Robert Lopez, Head Coach of Cross Country and Track & Field",
         confidence="medium", mp="track-field", mp_has_data=False),
]

# Listed by exactly one official page and with no schedule or other source: NOT treated as confirmed teams.
UNCONFIRMED = [
    "Boys' Flag Football (Fall): only on the US P.E. requirement page; no game on the athletics calendar, not on the athletics page or handbooks",
    "Girls' and Boys' Ultimate Frisbee (Fall and Spring): the PE page lists it as interscholastic, the handbook and Nexus post list it as a PE class; no game on the athletics calendar",
]
# MaxPreps entries for Nueva that no official source supports (placeholders or stale)
MAXPREPS_NOT_REAL = [
    "Girls Softball (Spring 26-27, varsity)", "Boys Basketball Freshman (24-25)", "Girls Soccer JV (Winter 26-27)",
    "Boys Tennis JV and Boys Volleyball JV (Spring 26-27): no official source lists them yet",
]

VENUE = {}
for _t in OFFICIAL_TEAMS:
    VENUE[(_t["sport"], _t["gender"])] = _t["home"]
VENUE[("Volleyball", "girls")] = "Nueva - San Mateo Campus (US Gym)"
HOME_VENUE_SPORTS = {("Basketball", "boys"), ("Basketball", "girls"), ("Volleyball", "girls"), ("Volleyball", "boys"), ("Soccer", "boys"),
                     ("Soccer", "girls"), ("Tennis", "girls"), ("Tennis", "boys")}


# --------------------------------------------------------------------------- team keys
def parse_team_title(title: str) -> dict:
    """'US Boys Basketball Varsity' / 'US Girls Volleyball' -> {sport, gender, level}"""
    t = title.lower()
    sport = ac._sport_of(t) or ""
    gender = "girls" if re.search(r"\bgirls?\b", t) else "boys" if re.search(r"\bboys?\b", t) else "coed"
    level = "jv" if re.search(r"\bjv\b|junior varsity", t) else "varsity" if "varsity" in t else ""
    return {"sport": sport, "gender": gender, "level": level}


def team_key(ev: dict) -> tuple:
    m = ev.get("meta") or {}
    if m.get("sport"):
        sport, gender, level = m["sport"], m["gender"], m.get("level", "")
    else:
        p = parse_team_title(re.split(r"\s+-\s+", ev["summary"], maxsplit=1)[0])
        sport, gender, level = p["sport"], p["gender"], p["level"]
    return (sport, gender, "jv" if level == "jv" else "varsity")


_GENERIC = {"school", "high", "academy", "prep", "preparatory", "college", "independent", "the", "of", "and", "jewish", "community", "christian",
            "schools", "hs", "uplands", "collegiate", "san", "jose", "silicon", "valley"}
_ALIAS = {"mt": "mountain", "bay": "bay", "basis": "basis", "kehillah": "kehillah", "tm": "thomas"}


def opp_tokens(name: str) -> set:
    n = re.sub(r"\(.*?\)", " ", (name or "").lower()).replace("california school for the deaf", "csd")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    toks = {_ALIAS.get(w, w) for w in n.split() if w}
    core = {w for w in toks if w not in _GENERIC}
    return core or toks


def same_opponent(a: str, b: str) -> bool:
    if not a or not b:
        return not a and not b
    ta, tb = opp_tokens(a), opp_tokens(b)
    if ta and tb and (ta <= tb or tb <= ta or ta & tb):
        return True
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.75


def opp_key(e: dict) -> str:
    """Stable text for 'the same game' inside one source: the opponent, or the whole title for notes such as 'First Day of Practice'."""
    return " ".join(sorted(opp_tokens(e["opp"]))) if e.get("opp") else re.sub(r"\W+", " ", e["summary"].split(" - ", 1)[-1].lower()).strip()


# --------------------------------------------------------------------------- MaxPreps (third party)
def _robots_ok(url: str) -> bool:
    rp = robotparser.RobotFileParser()
    try:
        r = requests.get("https://www.maxpreps.com/robots.txt", timeout=30, headers={"User-Agent": UA})
        rp.parse(r.text.splitlines())
    except requests.RequestException:
        return False
    return rp.can_fetch(UA, url)


def parse_maxpreps(html: str) -> dict | None:
    """Schedule page -> {'team': {...}, 'contests': [...]} or None when the page has no structured schedule (placeholder / empty team)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(1))["props"]["pageProps"]
        td = d["teamContext"]["data"]
    except (KeyError, ValueError):
        return None
    out = []
    for c in d.get("contests") or []:
        try:
            teams, iso = c[0], c[11]
            desc = c[29] if len(c) > 29 and isinstance(c[29], str) else ""
            nu = next((t for t in teams if t[1] == NUEVA_MP_ID), None)
            op = next((t for t in teams if t[1] != NUEVA_MP_ID), None)
            if not nu or not op:
                continue
            subject_is_nueva = bool(re.match(r"^(On \S+, )?the (The )?Nueva", desc, re.I)) or desc.lower().startswith("the the nueva")
            w = re.search(r"\b(home|away|neutral)\b", desc, re.I)
            ha = ""
            if w:
                ha = w.group(1).lower()
                if not subject_is_nueva and ha in ("home", "away"):
                    ha = "away" if ha == "home" else "home"
                if ha == "neutral":
                    ha = ""
            timed = bool(re.search(r"@\s*\d", desc)) or (len(c) > 28 and False) or not iso.endswith("T00:00:00")
            res = None
            if nu[5] in ("W", "L", "T") and nu[6] is not None and op[6] is not None:
                res = f"{nu[5]} {nu[6]}-{op[6]}"
            out.append({"date": iso[:10], "time": iso[11:16] if timed and not iso.endswith("T00:00:00") else None, "opponent": op[14],
                        "home_away": ha, "label": c[5] if isinstance(c[5], str) else "", "result": res,
                        "url": c[18] if len(c) > 18 and isinstance(c[18], str) else "", "tournament": bool(re.search(r"tournament|classic|invitational", desc, re.I)),
                        "league": bool(re.search(r"\bconference\b", desc, re.I) and not re.search(r"non-conference", desc, re.I))})
        except (IndexError, TypeError, ValueError):
            continue
    return {"team": {k: td.get(k) for k in ("gender", "sport", "level", "season", "year", "leagueName")}, "contests": out}


def fetch_maxpreps(log=print, force: bool = False) -> dict:
    """One polite GET per confirmed official team page (never for teams the catalog does not confirm)."""
    MP_DIR.mkdir(parents=True, exist_ok=True)
    got, skipped, last = [], [], 0.0
    s = requests.Session()
    for t in OFFICIAL_TEAMS:
        if not t.get("mp"):
            continue
        slug = t["mp"].replace("/", "_")
        f = MP_DIR / f"{slug}.json"
        if f.exists() and not force and time.time() - f.stat().st_mtime < ac.CACHE_HOURS * 3600:
            continue
        url = f"{NUEVA_MAXPREPS}/{t['mp']}/schedule/"
        if not _robots_ok(url):
            skipped.append(f"{t['mp']}: robots")
            continue
        time.sleep(max(0.0, 6.0 - (time.time() - last)))
        last = time.time()
        try:
            r = s.get(url, timeout=40, headers={"User-Agent": UA})
        except requests.RequestException:
            skipped.append(f"{t['mp']}: network")
            continue
        if r.status_code != 200:
            skipped.append(f"{t['mp']}: HTTP {r.status_code}")      # a block or challenge is never worked around
            continue
        parsed = parse_maxpreps(r.text)
        f.write_text(json.dumps({"url": url, "fetched": datetime.now(timezone.utc).date().isoformat(), "data": parsed}), encoding="utf-8")
        got.append(f"{t['mp']}: {len((parsed or {}).get('contests', []))} games")
    log(f"maxpreps (third party): fetched {got}; skipped {skipped}")
    return {"fetched": got, "skipped": skipped}


def maxpreps_events() -> list[dict]:
    """Cached MaxPreps games as event dicts (rank 3), for confirmed official teams only."""
    out = []
    for t in OFFICIAL_TEAMS:
        if not t.get("mp"):
            continue
        f = MP_DIR / (t["mp"].replace("/", "_") + ".json")
        if not f.exists():
            continue
        j = json.loads(f.read_text(encoding="utf-8"))
        data = j.get("data")
        if not data:
            continue
        for c in data["contests"]:
            d = date.fromisoformat(c["date"])
            gender = t["gender"]
            label = f"US {'Girls ' if gender == 'girls' else 'Boys ' if gender == 'boys' else ''}{t['sport']}"
            summary = (f"{label} - Game {c['opponent']}" + (f" - {c['home_away'].title()}" if c["home_away"] else "")) if not c["label"] or c["opponent"] else f"{label} - {c['label']}"
            tl = None
            if c["time"]:
                h = int(c["time"][:2])
                tl = f"{h % 12 or 12}:{c['time'][3:5]} {'PM' if h >= 12 else 'AM'}"
            out.append({"date": d, "summary": summary, "kind": "game", "home_away": c["home_away"], "start": c["time"], "time": tl or "time to be announced",
                        "location": "", "url": j["url"], "opp": c["opponent"], "source_rank": 3, "result": c["result"], "as_of": j["fetched"],
                        "meta": {"sport": t["sport"], "gender": gender, "level": "varsity", "provenance": "MaxPreps (third-party)", "official": False,
                                 "as_of": j["fetched"], "third_party_url": c["url"] or j["url"]}})
    return out


# --------------------------------------------------------------------------- merge
def _bye(e: dict) -> bool:
    return bool(re.search(r"\bbye\b", e.get("opp", "") or "", re.I)) or bool(re.search(r"\bgame bye\b", e["summary"], re.I))


def merge_events(sheet_events: list[dict], today: date | None = None, log=print, calendar_events: list[dict] | None = None,
                 third_party: list[dict] | None = None, window: tuple[date, date] | None = None) -> list[dict]:
    """Team-sheet events (rank 1) + athletics-calendar events (rank 2) + third-party games (rank 3) -> one event per game.
    calendar_events / third_party default to the caches; tests pass fixtures."""
    today = today or date.today()
    lo, hi = window or (date(today.year if today.month >= 8 else today.year - 1, 8, 1), today + timedelta(days=430))
    cal = ac.events_from_cache() if calendar_events is None else calendar_events
    tp = maxpreps_events() if third_party is None else third_party
    stats = {"sheet": 0, "calendar_added": 0, "calendar_merged": 0, "third_party_added": 0, "third_party_result": 0, "byes_dropped": 0, "conflicts": 0}
    final: list[dict] = []
    best: dict[tuple, dict] = {}
    for e in sheet_events:       # the same team sheet can arrive twice (a Google-linked copy and the live tab): keep the live (#gid=) copy
        k = (parse_team_title(e["summary"].split(" - ")[0]).get("sport"), parse_team_title(e["summary"].split(" - ")[0]).get("gender"),
             parse_team_title(e["summary"].split(" - ")[0]).get("level"), e["date"], e["kind"], opp_key(e))
        if k not in best or ("#gid=" in e.get("url", "") and "#gid=" not in best[k].get("url", "")):
            best[k] = e
    sheet_events = [e for e in sheet_events if best.get((parse_team_title(e["summary"].split(" - ")[0]).get("sport"), parse_team_title(e["summary"].split(" - ")[0]).get("gender"),
                                                         parse_team_title(e["summary"].split(" - ")[0]).get("level"), e["date"], e["kind"], opp_key(e))) is e]
    for e in sheet_events:
        e = {**e, "source_rank": 1, "meta": {**(e.get("meta") or {}), "provenance": "team schedule sheet", "official": True}}
        e["meta"].update(zip(("sport", "gender", "level"), (lambda p: (p["sport"], p["gender"], p["level"]))(parse_team_title(e["summary"].split(" - ")[0]))))
        if _bye(e):
            stats["byes_dropped"] += 1
            continue
        stats["sheet"] += 1
        final.append(e)

    ROUND = re.compile(r"\b(CCS|CIF|PSAL|WBAL|NCS|first round|second round|quarterfinals?|semi-?finals?|finals?|1st round|playoffs?|championships?)\b", re.I)

    def is_round(x) -> bool:
        return x["kind"] in ("game", "event") and not x.get("opp") and bool(ROUND.search(x["summary"].split(" - ", 1)[-1]))

    def find(e):
        k = team_key(e)
        mine = [x for x in final if team_key(x) == k and x["kind"] == e["kind"]]
        same_day = [x for x in mine if x["date"] == e["date"]]
        for x in same_day:
            if same_opponent(x.get("opp", ""), e.get("opp", "")):
                return x
        if e["kind"] == "game" and e.get("opp") and len(same_day) == 1 and not same_day[0].get("opp") and not is_round(same_day[0]):
            return same_day[0]
        if e["kind"] == "game" and e.get("opp") and len(same_day) == 1 and e["source_rank"] > 1:
            if SequenceMatcher(None, same_day[0].get("opp", "").lower(), e["opp"].lower()).ratio() > 0.5:
                return same_day[0]
        for x in [y for y in final if team_key(y) == k and y["date"] == e["date"]]:    # a postseason / tournament entry: same title, or both are round labels
            if is_round(x) and is_round(e):
                return x
            if x["summary"].split(" - ", 1)[-1].lower() == e["summary"].split(" - ", 1)[-1].lower():
                return x
        if e["kind"] == "game" and e.get("opp"):         # the same opponent a few days apart: a game that was moved; the sheet's date stands
            near = [x for x in mine if x.get("opp") and 0 < abs((x["date"] - e["date"]).days) <= 3 and same_opponent(x["opp"], e["opp"])
                    and x["source_rank"] < e["source_rank"]]
            if len(near) == 1:
                return near[0]
        return None

    def absorb(base: dict, e: dict) -> None:
        label = "the athletics calendar page" if e["source_rank"] == 2 else "MaxPreps (third-party)"
        if e["date"] != base["date"]:
            base["meta"]["date_note"] = f"{label} lists {e['date']:%B %-d}; the team sheet's date is used"
            stats["conflicts"] += 1
        elif not base.get("start") and e.get("start"):          # fill, never override
            base["start"], base["time"] = e["start"], e["time"]
        elif base.get("start") and e.get("start") and base["start"] != e["start"] and e["source_rank"] == 2:
            base["meta"]["time_note"] = f"{label} lists {e['time']}"
            stats["conflicts"] += 1
        if not base.get("location") and e.get("location"):
            base["location"] = e["location"]
        if e.get("result") and not base.get("result") and e["date"] == base["date"]:
            base["result"] = e["result"]
            base["meta"]["result_source"] = "MaxPreps (third-party)"
            stats["third_party_result"] += 1

    for e in cal:
        if not (lo <= e["date"] <= hi) or _bye(e):
            continue
        hit = find(e)
        if hit:
            absorb(hit, e)
            hit["meta"]["also_on"] = "school athletics calendar page"
            stats["calendar_merged"] += 1
        else:
            final.append(e)
            stats["calendar_added"] += 1
    for e in tp:
        if not (lo <= e["date"] <= hi):
            continue
        hit = find(e)
        if hit:
            absorb(hit, e)
        else:
            final.append(e)
            stats["third_party_added"] += 1
    # one title per team: sources spell the same team differently ("US Girls Volleyball" vs "US Girls Volleyball Varsity")
    label_of: dict[tuple, str] = {}
    for e in final:
        if e["source_rank"] == 1:
            label_of.setdefault(team_key(e), e["summary"].split(" - ")[0])
    for e in final:
        k = team_key(e)
        if e["source_rank"] > 1 and k in label_of and e["summary"].split(" - ")[0] != label_of[k]:
            e["summary"] = label_of[k] + " - " + e["summary"].split(" - ", 1)[-1]
    # a round label ("CCS Quarterfinals", TBA) on the same day as a game with a known opponent becomes that game's description
    for e in list(final):
        if is_round(e):
            games = [x for x in final if x is not e and x["date"] == e["date"] and team_key(x) == team_key(e) and x["kind"] == "game" and x.get("opp")]
            if len(games) == 1:
                games[0]["description"] = (games[0].get("description") or "") or e["summary"].split(" - ", 1)[-1]
                final.remove(e)
    for e in final:               # home venue from the athletics handbook where the source gives none (home games only)
        k = (e["meta"].get("sport"), e["meta"].get("gender"))
        if e["kind"] == "game" and e["home_away"] == "home" and not e.get("location") and k in HOME_VENUE_SPORTS:
            e["location"] = VENUE[k].split(" (handbook")[0]
            e["meta"]["location_source"] = "Upper School Athletics Handbook (home venue table)"
    log(f"  sports merge: {stats}")
    return final


# --------------------------------------------------------------------------- coverage (derived from the chunks)
def coverage(chunks: list[dict]) -> list[dict]:
    """Per catalog team: games, practices, date range, sources, from the calendar_sheet chunks."""
    out = []
    for t in OFFICIAL_TEAMS:
        gl = "Girls" if t["gender"] == "girls" else "Boys"
        rows = [c["metadata"] for c in chunks if c["metadata"].get("source") == "calendar_sheet" and c["metadata"].get("sport") == t["sport"]
                and c["metadata"].get("gender") == t["gender"] and c["metadata"].get("kind") in ("game", "practice")]
        games = [r for r in rows if r["kind"] == "game"]
        ds = sorted(r["event_date"] for r in rows)
        srcs = sorted({r.get("provenance", "") for r in rows})
        out.append({"team": f"{gl} {t['sport']}", "season": t["season"], "games": len(games), "first": ds[0] if ds else None, "last": ds[-1] if ds else None,
                    "sources": srcs, "levels": sorted({(r.get("level") or "varsity") for r in rows})})
    return out


# --------------------------------------------------------------------------- stage
def run(log=print) -> dict:
    """Fetch the official athletics calendar months, the 'Sports Offered' block, and the MaxPreps pages of the confirmed teams."""
    months = ac.school_year_months(date.today())
    got = ac.fetch_months(months, log)
    offered = ac.fetch_sports_offered()
    if offered:       # stored as a page so scripts/check_facts.py can verify the knowledge file against it; the title keeps it out of the index
        save_page({"url": ac.ATHLETICS_HOME + "#sports-offered", "title": "Athletics sports offered (source data)", "source": "public_site", "text": offered,
                   "links": [], "scraped_at": datetime.now(timezone.utc).isoformat()})
    raw = ac.events_from_cache()
    if raw:
        lines = [f"{e['date']} | {e['summary']} | {e['time']}" for e in sorted(raw, key=lambda e: (e["date"], e["summary"]))]
        save_page({"url": ac.PAGE + "#source-data", "title": "US Athletics Calendar (source data)", "source": "public_site", "text": "\n".join(lines),
                   "links": [], "scraped_at": datetime.now(timezone.utc).isoformat()})
    mp = fetch_maxpreps(log)
    stats = {"calendar_months_fetched": len(got), "calendar_events": len(raw), "sports_offered_saved": bool(offered), "maxpreps": mp}
    log(f"sports stage done: {stats}")
    return stats
