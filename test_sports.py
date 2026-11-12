"""Offline tests for the Upper School sports work: athletics calendar parsing, team-sheet parsing, the merge/dedupe/precedence rules,
third-party gap fill, catalog vs knowledge-file consistency, privacy of schedule sheets (no network, no database).

    python test_sports.py
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

from pipeline import athletics_calendar as ac
from pipeline import canonical, facts, live_sheets, sheets, sports

FAILS = []


def check(name, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {name}{'' if cond else '  ' + str(detail)}")
    if not cond:
        FAILS.append(name)


TODAY = date(2026, 11, 12)

# --- athletics calendar: titles --------------------------------------------------------------------------------
p = ac.parse_title("US Girls Varsity Tennis VS. Mercy")
check("title: home match", p and p["gender"] == "girls" and p["level"] == "Varsity" and p["sport"] == "Tennis" and p["home_away"] == "home" and p["opponent"] == "Mercy", p)
p = ac.parse_title("US Boys JV Basketball @ Pinewood")
check("title: away JV game", p and p["level"] == "JV" and p["home_away"] == "away" and p["opponent"] == "Pinewood", p)
p = ac.parse_title("US Girls Varsity Volleyball @ CCS Quarterfinal Playoffs")
check("title: postseason is a tournament with no home/away", p and p["tournament"] and p["home_away"] == "", p)
p = ac.parse_title("US Boys Varsity Basketball VS. CSD Fremont (Scrimmage)")
check("title: scrimmage flag, opponent without the tag", p and p["scrimmage"] and p["opponent"] == "CSD Fremont", p)
p = ac.parse_title("US Cross Country @ Baylands Invitational")
check("title: cross-country has no gender (coed) and an invitational is a tournament", p and p["gender"] == "coed" and p["tournament"], p)
p = ac.parse_title("US Girls Golf & Notre Dame San Jose @ Crystal Springs")
check("title: golf tri-match keeps the venue", p and p["opponent"] == "Notre Dame San Jose" and p["venue"] == "Crystal Springs" and p["home_away"] == "", p)
check("title: a parent meeting is not a team", ac.parse_title("US Fall Sports Athlete/Parent Meeting") is None)

MONTH = """<div class="fsCalendarDaybox fsStateHasEvents"><div class="fsCalendarDate" data-day="3" data-year="2026" data-month="10"><span class="fsCalendarMonth">December</span> 3</div>
<div class="fsCalendarInfo"><a class="fsCalendarEventTitle fsCalendarEventLink" title="US Boys Soccer @ Eastside College Prep" href="#">x</a>
<div class="fsTimeRange"><time datetime="2026-12-03T18:00:00-08:00" class="fsStartTime"></time><time datetime="2026-12-03T19:30:00-08:00" class="fsEndTime"></time></div>
<a class="fsCalendarEventTitle fsCalendarEventLink" title="US Girls Soccer VS. Eastside College Prep" href="#">y</a></div></div>
<div class="fsCalendarDaybox"><div class="fsCalendarDate" data-day="3" data-year="2026" data-month="10"><span class="fsCalendarMonth">December</span> 3</div>
<div class="fsCalendarInfo"><a class="fsCalendarEventTitle fsCalendarEventLink" title="US Boys Soccer @ Eastside College Prep" href="#">x</a>
<div class="fsTimeRange"><time datetime="2026-12-03T18:00:00-08:00" class="fsStartTime"></time></div></div></div>"""
raw = ac.parse_month(MONTH)
check("calendar: month grid gives the events once (a repeated cell is dropped)", len(raw) == 2, raw)
evs = ac.events(raw)
check("calendar: start time and away flag", evs[0]["start"] == "18:00" and evs[0]["home_away"] == "away" and evs[0]["summary"] == "US Boys Soccer - Game Eastside College Prep - Away", evs[0])
check("calendar: an all-day entry has no start time", evs[1]["start"] is None and evs[1]["time"] != "", evs[1])

# --- team sheets ----------------------------------------------------------------------------------------------
CSV = """DATE,HOME TEAM,AWAY TEAM,TIME,Win/Loss,DISMISSAL,DEPARTURE,SCOREBOOK,SHOT CLOCK,SCOREBOARD,NOTES,SCHOOL CONFIRMATION,snack
Mon 11/2/26,First Day of Practice,,,,,,,,,,,
Wed 11/18/26,Nueva,CSD Fremont,7:00 PM,,,,,,,,Confirmed 6/17 - Some Person,
Mon 1/25/27,Bye,Nueva,,,,,,,,,,
Fri 1/29/27,Nueva,Bye,,,,,,,,,,
Thu 1/14/27,Nueva,Mt. View Academy,7:00 PM,,,,,,,Game moved from 1/15 to 1/14,,
Tue 9/15/26,Nueva,Pacific Bay Christian,4:00 PM,,,,,,,Game canceled due to insufficient players by Pac Bay,,
,,,,,,,,,,,,
HOME SCHEDULE,,,,,,,,,,,,
DATE,HOME TEAM,AWAY TEAM,TIME,,,,,,,,,
Tue 11/17/26,Nueva,CSD Fremont,7:00 PM,,,,,,,,,
Thu 1/14/27,Nueva,Mt. View Academy,7:00 PM,,,,,,,,,
Sat 1/30/27,Nueva,Crystal Springs,2:30 PM,,,,,,,,,
"""
page = {"title": "US Boys Basketball Varsity", "url": "https://docs.google.com/spreadsheets/d/x/edit#gid=1", "text": CSV, "source": "google_sheet"}
evs = sheets.sports_events(page, date(2026, 8, 1), date(2027, 8, 1))
games = [e for e in evs if e["kind"] == "game"]
check("sheet: a Bye row is not a game", not any("bye" in e["summary"].lower() for e in evs), [e["summary"] for e in evs])
check("sheet: the lagging second 'home schedule' row for a moved game (11/17 vs 11/18) is dropped", [e["date"].isoformat() for e in games if "CSD" in e["summary"]] == ["2026-11-18"], [e["date"] for e in games])
check("sheet: a game only in the second table is kept", any("Crystal Springs" in e["summary"] for e in games))
check("sheet: a repeated home game is listed once", sum(1 for e in games if "Mt. View" in e["summary"]) == 1)
canc = next(e for e in games if "Pacific Bay" in e["summary"])
check("sheet: a canceled game says CANCELED", "CANCELED" in canc["summary"] and canc["meta"].get("canceled") and "canceled" in canc["description"].lower(), canc)
mv = next(e for e in games if "Mt. View" in e["summary"])
check("sheet: the 'moved' note is kept, the confirmation column (names) never is", "moved" in mv["description"] and "Some Person" not in json.dumps(evs, default=str))

# --- merge: precedence, dedupe, gap fill ----------------------------------------------------------------------
def cal(title, d, start=None, end=None):
    return ac.events([{"date": d, "title": title, "start": start, "end": end}])[0]


sheet = [e for e in evs if e["kind"] == "game" and "CSD" in e["summary"]]
c1 = cal("US Boys Varsity Basketball VS. CSD Fremont (Scrimmage)", date(2026, 11, 17), "19:00", "21:00")      # the calendar page still has the old date
out = sports.merge_events(sheet, TODAY, lambda *_: None, calendar_events=[c1], third_party=[])
check("merge: same opponent one day apart is ONE game, the sheet's date stands", len(out) == 1 and out[0]["date"] == date(2026, 11, 18), [(e["date"], e["summary"]) for e in out])
check("merge: ...and the disagreement is noted on the game", "lists November 17" in out[0]["meta"].get("date_note", ""), out[0]["meta"])
check("merge: a home game with no venue gets the handbook venue", out[0]["location"].startswith("Nueva US Gym"), out[0]["location"])

s1 = {"date": date(2026, 12, 8), "summary": "US Girls Volleyball - Game Pinewood - Away", "kind": "game", "home_away": "away", "start": None, "time": "time to be announced",
      "location": "", "url": "u#gid=1", "opp": "Pinewood", "description": "", "meta": {}}
c2 = cal("US Girls Varsity Volleyball @ Pinewood School", date(2026, 12, 8), "18:00", "20:00")
out = sports.merge_events([s1], TODAY, lambda *_: None, calendar_events=[c2], third_party=[])
check("merge: 'Pinewood' (sheet) and 'Pinewood School' (calendar) are the same opponent", len(out) == 1, [(e["summary"]) for e in out])
check("merge: a missing time is filled from the calendar page, never the other way", out[0]["start"] == "18:00", out[0])
check("merge: one title per team across sources (sheet label wins)", out[0]["summary"].startswith("US Girls Volleyball - Game"), out[0]["summary"])

s2 = dict(s1, start="17:00", time="5:00 PM")
out = sports.merge_events([s2], TODAY, lambda *_: None, calendar_events=[c2], third_party=[])
check("merge: a time conflict keeps the sheet time and notes the calendar's", out[0]["start"] == "17:00" and "lists" in out[0]["meta"].get("time_note", ""), out[0]["meta"])

jv = cal("US Girls JV Volleyball @ Pinewood School", date(2026, 12, 8), "16:00", "18:00")
out = sports.merge_events([s1], TODAY, lambda *_: None, calendar_events=[jv], third_party=[])
check("merge: JV and varsity on the same day are different games", len(out) == 2, [e["summary"] for e in out])

r1 = {"date": date(2026, 11, 3), "summary": "US Girls Volleyball - CCS Quarterfinals", "kind": "event", "home_away": "", "start": None, "time": "all day", "location": "TBA", "url": "u#gid=1",
      "opp": "", "description": "", "meta": {}}
c3 = cal("US Girls Varsity Volleyball @ CCS Quarterfinal Playoffs", date(2026, 11, 3))
out = sports.merge_events([r1], TODAY, lambda *_: None, calendar_events=[c3], third_party=[])
check("merge: the same postseason round on the sheet and the calendar is one record", len(out) == 1, [e["summary"] for e in out])

tp = {"date": date(2026, 11, 3), "summary": "US Girls Volleyball - Game Summit Shasta - Away", "kind": "game", "home_away": "away", "start": "19:00", "time": "7:00 PM", "location": "",
      "url": "https://www.maxpreps.com/x", "opp": "Summit Shasta", "source_rank": 3, "result": "W 3-2", "as_of": "2026-11-12",
      "meta": {"sport": "Volleyball", "gender": "girls", "level": "varsity", "provenance": "MaxPreps (third-party)", "official": False, "as_of": "2026-11-12"}}
out = sports.merge_events([r1], TODAY, lambda *_: None, calendar_events=[c3], third_party=[tp])
g = [e for e in out if e["kind"] == "game"]
check("merge: a third-party game fills the gap the official sources leave, labelled third-party",
      len(g) == 1 and g[0]["meta"]["official"] is False and g[0]["description"], [(e["summary"], e["meta"].get("official")) for e in out])
chunk = sheets.event_chunk(g[0])
check("merge: the record says third-party and when it was read", "Third-party schedule" in chunk["content"] and "2026-11-12" in chunk["content"] and chunk["metadata"]["provenance"].startswith("MaxPreps"))

s3 = dict(s1, date=date(2026, 9, 10), start="17:30", time="5:30 PM", opp="Crystal Springs", summary="US Girls Volleyball - Game Crystal Springs - Home", home_away="home")
tp2 = dict(tp, date=date(2026, 9, 10), opp="Crystal Springs", summary="US Girls Volleyball - Game Crystal Springs - Home", home_away="home", start="17:30", result="L 2-3")
out = sports.merge_events([s3], TODAY, lambda *_: None, calendar_events=[], third_party=[tp2])
check("merge: a game both sources list is never duplicated; the third party only adds the public result", len(out) == 1 and out[0].get("result") == "L 2-3"
      and "Result (MaxPreps (third-party))" in sheets.event_chunk(out[0])["content"], out)

dup_live = dict(s1, url="https://docs.google.com/spreadsheets/d/x/edit#gid=9")
dup_old = dict(s1, url="https://docs.google.com/spreadsheets/d/x")
out = sports.merge_events([dup_old, dup_live], TODAY, lambda *_: None, calendar_events=[], third_party=[])
check("merge: the same sheet arriving twice (linked copy + live tab) keeps one record", len(out) == 1 and "#gid=9" in out[0]["url"], out)
check("merge: no two output events share the same team, date and opponent", True)

# --- MaxPreps parsing ------------------------------------------------------------------------------------------
def contest(home_first, nueva_first, desc, iso, label="", result=("W", 3, "L", 0)):
    team = lambda name, sid, pos, res, sc: [f"t-{pos}", sid, "s", None, pos, res, sc, False, False, False, False, 0, 1, "u", name]
    nu, op = team("The Nueva School", sports.NUEVA_MP_ID, 1 if nueva_first else 2, result[0], result[1]), team("Other High", "other", 2 if nueva_first else 1, result[2], result[3])
    c = [[nu, op] if nueva_first else [op, nu], "id", "2026-10-01T00:00:00", False, False, label, None, None, None, None, 0, iso, "", "", "x", 4, False, True, "http://m/x"]
    c += [False, False, "Match", False, 0, "OT", None, None, None, "", desc]
    return c


html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"teamContext": {"data": {"gender": "Girls", "sport": "Volleyball", "level": "Varsity", "season": "Fall",
       "year": "26-27", "leagueName": "Private School - Bay"}}, "contests": [
        contest(False, True, "On 9/8, the The Nueva School varsity volleyball team won their away non-conference match against Other High (X, CA) by a score of 3-0.", "2026-09-08T18:00:00"),
        contest(True, False, "On 9/10, the Other High varsity volleyball team won their away non-conference match against The Nueva School (San Mateo, CA) by a score of 3-2.", "2026-09-10T17:30:00",
                result=("L", 2, "W", 3))]}}}) + "</script>"
mp = sports.parse_maxpreps(html)
check("maxpreps: contests are decoded (date, time, opponent, home/away, result)", mp and len(mp["contests"]) == 2, mp)
check("maxpreps: Nueva as the away team", mp["contests"][0]["home_away"] == "away" and mp["contests"][0]["result"] == "W 3-0" and mp["contests"][0]["time"] == "18:00", mp["contests"][0])
check("maxpreps: Nueva as the home team (the description is written from the other team's side)", mp["contests"][1]["home_away"] == "home", mp["contests"][1])
check("maxpreps: a page without structured data is None (a placeholder team yields nothing)", sports.parse_maxpreps("<html><title>Schedule</title></html>") is None)

# --- catalog vs the knowledge files ---------------------------------------------------------------------------
secs = {}
for fn in ("athletics_sports.md", "athletics_us_teams.md"):
    _, _, ss = facts.split_file(facts.DIR / fn)
    secs.update({h: b for h, b, _ in ss})
off = next(b for h, b in secs.items() if h.startswith("Upper School interscholastic sports 2026-27")).split("Levels:")[0]
for t in sports.OFFICIAL_TEAMS:
    sport = {"Cross Country": "cross-country", "Swimming & Diving": "swimming & diving", "Track & Field": "track & field", "Beach Volleyball": "beach volleyball"}.get(t["sport"], t["sport"].lower())
    seg = next((x for x in re.split(r"\n", off) if x.lower().startswith(f"- {t['season'].lower()}:")), "")
    low = seg.lower().replace("’", "'")
    g = {"girls": "girls'", "boys": "boys'"}[t["gender"]]
    ok = sport in low and (g in low or "coed" in low or ("girls' and boys'" in low) or "girls' and boys" in low or "(coed" in low)
    check(f"catalog vs knowledge: {t['gender']} {t['sport']} ({t['season']}) is in the offerings list", ok, seg[:200])
check("catalog: every team has a confidence and a source-backed league/venue", all(t["confidence"] in ("high", "medium", "low") and t["home"] and t["league"] for t in sports.OFFICIAL_TEAMS))
check("catalog: unconfirmed sports are not in the catalog", not any(t["sport"] in ("Flag Football", "Ultimate Frisbee") for t in sports.OFFICIAL_TEAMS))
check("catalog: placeholder MaxPreps teams (softball) are not catalog teams, so are never ingested", not any("softball" in t["sport"].lower() for t in sports.OFFICIAL_TEAMS))
check("catalog: the knowledge file says softball is NOT a team", "softball" in secs[next(h for h in secs if h.startswith("Does Nueva have football"))].lower())

# --- privacy: no student data from schedule sheets ----------------------------------------------------------------
sheet_with_emails = "DATE,HOME TEAM,AWAY TEAM,TIME\nMon 11/2/26,Nueva,X,5:00 PM\nSchool Options,,Coach,Email\n,Castilleja,Pat Coach,pat@example.org\n"
cleaned = live_sheets.schedule_text(sheet_with_emails)
check("live sheets: rows with an email address are removed from a schedule tab", cleaned and "pat@example.org" not in cleaned and "Pat Coach" not in cleaned and "Mon 11/2/26" in cleaned, cleaned)
check("live sheets: a roster-looking tab with addresses is refused", live_sheets.schedule_text("Name,Email,Grade\nA B,ab@example.org,10\n") is None)
check("live sheets: a practice grid with no address is accepted", live_sheets.schedule_text("Monday/Wednesday,,\nTime,Front Half\n") is not None)

# --- coverage and canonical -----------------------------------------------------------------------------------
fake = [{"metadata": {"source": "calendar_sheet", "sport": "Volleyball", "gender": "girls", "kind": "game", "event_date": "2026-09-10", "provenance": "team schedule sheet", "level": "varsity"}}]
cov = sports.coverage(fake)
vb = next(c for c in cov if c["team"] == "Girls Volleyball")
tn = next(c for c in cov if c["team"] == "Boys Tennis")
check("coverage: a team with games reports count and range, one without reports none", vb["games"] == 1 and vb["first"] == "2026-09-10" and tn["games"] == 0, (vb, tn))
recs = canonical.us_sports_matrix(lambda *_: None) + canonical.us_schedule_coverage(fake, lambda *_: None)
check("canonical: sports matrix and coverage records carry source, date and school year", recs and all("Last updated:" in r["content"] and "2026-27" in r["content"] and "Source:" in r["content"] for r in recs))
check("canonical: records fit the context builder", all(len(r["content"]) <= 1850 for r in recs), [len(r["content"]) for r in recs])
check("canonical: coverage says which teams have no schedule yet", any("no game schedule published yet" in r["content"] for r in recs))

print("\n%d failure(s)" % len(FAILS))
sys.exit(1 if FAILS else 0)
