"""Offline tests for the calendar refresh: iCal edge cases (all-day, multi-day, time zones, DST, recurring, cancelled, moved, duplicates),
the history window, which calendar rows the index stage may remove, team-sheet dedupe, the audit's CI mode and the job summary.
No network, no database.

    python test_calendar.py
"""
import contextlib
import io
import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

from pipeline import calendar_feed as cf, config, index, sheets

FAILS = []


def check(name, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {name}{'' if cond else '  ' + str(detail)}")
    if not cond:
        FAILS.append(name)


def feed(*events: str) -> str:
    return "BEGIN:VCALENDAR\nVERSION:2.0\n" + "\n".join(events) + "\nEND:VCALENDAR\n"


def ev(uid, summary, start, end=None, extra=""):
    return f"BEGIN:VEVENT\nUID:{uid}\nSUMMARY:{summary}\n{start}\n" + (f"{end}\n" if end else "") + (extra + "\n" if extra else "") + "END:VEVENT"


def one(text):
    evs = cf.parse_ics(text)
    return evs[0] if evs else None


# --- time zones and DST --------------------------------------------------------------------------------------------
e = one(feed(ev("a", "Pacific", "DTSTART;TZID=America/Los_Angeles:20261010T150000", "DTEND;TZID=America/Los_Angeles:20261010T160000")))
check("tz: a Pacific TZID time is kept as written", e["start"] == datetime(2026, 10, 10, 15, 0), e["start"])
e = one(feed(ev("a", "NY", "DTSTART;TZID=America/New_York:20261010T150000", "DTEND;TZID=America/New_York:20261010T160000")))
check("tz: a New York TZID time is converted to Pacific (3 pm ET = noon PT)", e["start"] == datetime(2026, 10, 10, 12, 0) and e["end"] == datetime(2026, 10, 10, 13, 0), e["start"])
e = one(feed(ev("a", "UTC summer", "DTSTART:20261031T200000Z")))
check("dst: 20:00 UTC on Oct 31 is 1 pm PDT", e["start"] == datetime(2026, 10, 31, 13, 0), e["start"])
e = one(feed(ev("a", "UTC winter", "DTSTART:20261101T200000Z")))
check("dst: 20:00 UTC on Nov 1 (the day DST ends) is noon PST", e["start"] == datetime(2026, 11, 1, 12, 0), e["start"])
e = one(feed(ev("a", "Floating", "DTSTART:20261010T090000")))
check("tz: a floating time is taken as Pacific", e["start"] == datetime(2026, 10, 10, 9, 0) and not e["all_day"])
e = one(feed(ev("a", "Explicit datetime", "DTSTART;VALUE=DATE-TIME:20261010T090000")))
check("tz: VALUE=DATE-TIME is a timed event, not an all-day one", not e["all_day"] and e["start"] == datetime(2026, 10, 10, 9, 0), e)
e = one(feed(ev("a", "Unknown zone", "DTSTART;TZID=Pacific Standard Time:20261010T090000")))
check("tz: an unknown zone name keeps the wall time instead of crashing", e["start"] == datetime(2026, 10, 10, 9, 0), e["start"])

# --- all-day and multi-day -----------------------------------------------------------------------------------------
e = one(feed(ev("a", "Thanksgiving Break", "DTSTART;VALUE=DATE:20261123", "DTEND;VALUE=DATE:20261128")))
d = cf.describe(e)
check("all-day: an exclusive DTEND means the last day is the day before", d["date"] == date(2026, 11, 23) and d["end_date"] == date(2026, 11, 27), d)
check("all-day: the text names the whole range", "through Friday, November 27, 2026" in d["when"], d["when"])
e = one(feed(ev("a", "One day", "DTSTART;VALUE=DATE:20261012", "DTEND;VALUE=DATE:20261013")))
check("all-day: a single day ends the same day", cf.describe(e)["end_date"] == date(2026, 10, 12))
e = one(feed(ev("a", "Overnight", "DTSTART;TZID=America/Los_Angeles:20261016T190000", "DTEND;TZID=America/Los_Angeles:20261017T080000")))
d = cf.describe(e)
check("timed multi-day: ends on the next day and shows only the start time", d["end_date"] == date(2026, 10, 17) and d["time"] == "7 PM", d)

# --- recurring events ---------------------------------------------------------------------------------------------
weekly = ev("w1", "Chess Club", "DTSTART;TZID=America/Los_Angeles:20261005T153000", "DTEND;TZID=America/Los_Angeles:20261005T163000",
            "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261021T000000Z\nEXDATE;TZID=America/Los_Angeles:20261012T153000")
moved = ev("w1", "Chess Club (moved)", "DTSTART;TZID=America/Los_Angeles:20261014T170000", "DTEND;TZID=America/Los_Angeles:20261014T180000",
           "RECURRENCE-ID;TZID=America/Los_Angeles:20261014T153000")
cancelled_inst = ev("w1", "Chess Club", "DTSTART;TZID=America/Los_Angeles:20261019T153000", "DTEND;TZID=America/Los_Angeles:20261019T163000",
                    "RECURRENCE-ID;TZID=America/Los_Angeles:20261019T153000\nSTATUS:CANCELLED")
evs = cf.parse_ics(feed(weekly, moved, cancelled_inst))
out, st = cf.expand_recurring(evs, date(2026, 8, 1), date(2027, 8, 1))
got = [(o["start"].strftime("%m-%d %H:%M"), o["summary"]) for o in sorted(out, key=lambda o: o["start"]) if o.get("status", "").upper() != "CANCELLED"]
check("rrule: weekly MO,WE expands, honours EXDATE, UNTIL and a moved instance, and a cancelled instance disappears",
      got == [("10-05 15:30", "Chess Club"), ("10-07 15:30", "Chess Club"), ("10-14 17:00", "Chess Club (moved)")], got)
check("rrule: stats count one expanded rule", st["expanded"] == 1 and st["unsupported"] == 0, st)
dst_weekly = ev("w2", "Practice", "DTSTART;TZID=America/Los_Angeles:20261026T153000", "DTEND;TZID=America/Los_Angeles:20261026T170000", "RRULE:FREQ=WEEKLY;COUNT=3")
out, _ = cf.expand_recurring(cf.parse_ics(feed(dst_weekly)), date(2026, 8, 1), date(2027, 8, 1))
check("rrule: the wall-clock time survives the DST change on Nov 1", [o["start"].strftime("%m-%d %H:%M") for o in out] == ["10-26 15:30", "11-02 15:30", "11-09 15:30"]
      and out[1]["end"].strftime("%H:%M") == "17:00", [o["start"] for o in out])
monthly = ev("m1", "Board meeting", "DTSTART;TZID=America/Los_Angeles:20261001T180000", "DTEND;TZID=America/Los_Angeles:20261001T190000", "RRULE:FREQ=MONTHLY;BYDAY=1TH;COUNT=3")
out, _ = cf.expand_recurring(cf.parse_ics(feed(monthly)), date(2026, 8, 1), date(2027, 8, 1))
check("rrule: monthly first Thursday", [o["start"].strftime("%m-%d") for o in out] == ["10-01", "11-05", "12-03"], [o["start"] for o in out])
odd = ev("u1", "Odd", "DTSTART:20261005T153000", "DTEND:20261005T163000", "RRULE:FREQ=MONTHLY;BYSETPOS=2;BYDAY=MO")
out, st = cf.expand_recurring(cf.parse_ics(feed(odd)), date(2026, 8, 1), date(2027, 8, 1))
check("rrule: an unsupported rule keeps the first instance only and is reported", len(out) == 1 and st["unsupported"] == 1, (out, st))

# --- build(): window, history, cancelled, audience duplicates, passcodes -----------------------------------------
tmp = Path(tempfile.mkdtemp())
config.DATA_DIR, config.CALENDAR_FILE = tmp, tmp / "calendar.json"
TODAY = date(2026, 11, 12)
text = feed(
    ev("1", "Back to School Night", "DTSTART;TZID=America/Los_Angeles:20260825T180000", "DTEND;TZID=America/Los_Angeles:20260825T200000"),   # earlier this school year
    ev("2", "Graduation 2026", "DTSTART;TZID=America/Los_Angeles:20260605T100000", "DTEND;TZID=America/Los_Angeles:20260605T120000"),         # last school year
    ev("3", "Fall Concert", "DTSTART;TZID=America/Los_Angeles:20261120T190000", "DTEND;TZID=America/Los_Angeles:20261120T203000", "LOCATION:Theater"),
    ev("4", "Fall Concert", "DTSTART;TZID=America/Los_Angeles:20261120T190000", "DTEND;TZID=America/Los_Angeles:20261120T203000", "LOCATION:Theater"),   # same event, another audience
    ev("5", "Cancelled Talk", "DTSTART;TZID=America/Los_Angeles:20261121T190000", "DTEND;TZID=America/Los_Angeles:20261121T200000", "STATUS:CANCELLED"),
    ev("6", "Parent Coffee", "DTSTART;TZID=America/Los_Angeles:20261124T083000", "DTEND;TZID=America/Los_Angeles:20261124T093000",
       "LOCATION:https://zoom.us/j/123?pwd=SECRETSECRET"),
    ev("7", "Thanksgiving Break", "DTSTART;VALUE=DATE:20261123", "DTEND;VALUE=DATE:20261128"),
    ev("8", "Far future", "DTSTART;VALUE=DATE:20290101", "DTEND;VALUE=DATE:20290102"),
)
stats = cf.build(cf.parse_ics(text), log=lambda *a: None, today=TODAY)
chunks = json.loads(config.CALENDAR_FILE.read_text())
titles = {c["metadata"]["summary"] for c in chunks if c["metadata"]["source"] == "calendar"}
check("window: an event from earlier this school year stays (answerable as 'already happened')", "Back to School Night" in titles, titles)
check("window: last school year's event is dropped", "Graduation 2026" not in titles)
check("window: events more than ~14 months ahead are dropped", "Far future" not in titles)
check("cancelled events are not indexed", "Cancelled Talk" not in titles and stats["cancelled"] == 1, stats)
check("audience duplicates are merged into one row", sum(1 for c in chunks if c["metadata"].get("summary") == "Fall Concert") == 1 and stats["duplicates_removed"] == 1, stats)
check("meeting passcodes are stripped from locations", "SECRETSECRET" not in json.dumps(chunks), "pwd leaked")
brk = next(c for c in chunks if c["metadata"].get("summary") == "Thanksgiving Break")
check("multi-day break: category no_school, ends Nov 27, shown in each day's digest",
      brk["metadata"]["category"] == "no_school" and brk["metadata"]["end_date"] == "2026-11-27"
      and {f"cal-day-202611{d}-0" for d in (23, 24, 25, 26, 27)} <= {c["chunk_id"] for c in chunks}, brk["metadata"])
ids1 = {c["chunk_id"] for c in chunks}
cf.build(cf.parse_ics(text), log=lambda *a: None, today=TODAY)
check("chunk ids are stable between runs", ids1 == {c["chunk_id"] for c in json.loads(config.CALENDAR_FILE.read_text())})
# the same event moved by an hour: a new id (the old row must be removed by the index stage)
moved_text = text.replace("20261120T190000", "20261120T180000").replace("20261120T203000", "20261120T193000")
cf.build(cf.parse_ics(moved_text), log=lambda *a: None, today=TODAY)
ids2 = {c["chunk_id"] for c in json.loads(config.CALENDAR_FILE.read_text())}
check("a moved event gets a new chunk id", ids1 != ids2)
# school year rollover
check("rollover: in June the whole school year is still kept", cf.window_floor(date(2027, 6, 10)) == date(2026, 8, 1))
check("rollover: in September the old year is gone", cf.window_floor(date(2027, 9, 10)) == date(2027, 8, 1))
check("rollover: in early August the last two weeks stay", cf.window_floor(date(2027, 8, 5)) == date(2027, 7, 22))

# --- which rows the index stage may remove --------------------------------------------------------------------------
def row(i, source, d, summary="x", end=None, url="school-calendar"):
    return {"id": i, "source": source, "date": d, "end": end or d, "url": url, "summary": summary}


def chunk(i, source, d, summary="x", url="school-calendar"):
    return {"chunk_id": i, "metadata": {"source": source, "event_date": d, "summary": summary, "source_url": url}}


SHEET = "https://docs.google.com/spreadsheets/d/S/edit#gid=1"
plan_chunks = [chunk("cal-keep", "calendar", "2026-12-01", "Keep"), chunk("cal-day-20261201-0", "calendar_day", "2026-12-01"),
               chunk("cal-edit-new", "calendar", "2026-10-20", "Diwali"), chunk("sheet-ok", "calendar_sheet", "2026-12-05", "Team - Game A - Home", SHEET)]
db = [row("cal-keep", "calendar", "2026-12-01", "Keep"),
      row("cal-future-gone", "calendar", "2026-12-02", "Cancelled future"),                                         # removed: cancelled or moved
      row("cal-today", "calendar", "2026-11-12", "Happening today, gone from feed"),                              # removed: today counts as not yet history
      row("cal-past-gone", "calendar", "2026-10-02", "Happened, left the feed"),                                   # kept: history
      row("cal-edit-old", "calendar", "2026-10-20", "Diwali"),                                                      # removed: same day and title edited
      row("cal-old-year", "calendar", "2026-06-05", "Graduation 2026"),                                             # removed: before the school year
      row("cal-multi-running", "calendar", "2026-11-09", "Running break", end="2026-11-13"),                        # removed: still running, left the feed
      row("cal-day-20261201-1", "calendar_day", "2026-12-01"),                                                       # old continuation row of a rewritten day
      row("cal-day-20261202-0", "calendar_day", "2026-12-02"),                                                       # future digest with no digest in the plan
      row("cal-day-20261005-0", "calendar_day", "2026-10-05"),                                                       # past digest: kept
      row("sheet-ok", "calendar_sheet", "2026-12-05", "Team - Game A - Home", url=SHEET),
      row("sheet-gone", "calendar_sheet", "2026-12-06", "Team - Game B - Away", url=SHEET),                          # removed: dropped from the sheet this run
      row("sheet-past", "calendar_sheet", "2026-11-01", "Team - Game C - Home", url=SHEET),                          # kept: history
      row("sheet-other-team", "calendar_sheet", "2026-12-07", "Other - Game D", url="https://docs.google.com/spreadsheets/d/T/edit#gid=2"),  # kept: that sheet was not read this run
      row("fact-1", "school_facts", "2026-12-07"), row("email-1", "email", "2026-12-07"), row("lunch-x", "lunch", "2026-12-07")]
plan = index.plan_calendar_removals(db, plan_chunks, TODAY)
rm = set(plan["remove"])
check("index: cancelled/moved future events, today's vanished event, edited duplicates and last year's events are removed",
      {"cal-future-gone", "cal-today", "cal-edit-old", "cal-old-year", "cal-multi-running"} <= rm, rm)
check("index: a past event of this school year that left the feed is kept as history", "cal-past-gone" not in rm and "cal-day-20261005-0" not in rm and "sheet-past" not in rm)
check("index: a team-sheet game dropped from a sheet read this run is removed; other sheets and past games are not", "sheet-gone" in rm and "sheet-other-team" not in rm and "sheet-ok" not in rm, rm)
check("index: a rewritten day's old continuation row goes, a future digest the plan no longer has goes", "cal-day-20261201-1" in rm and "cal-day-20261202-0" in rm, rm)
check("index: rows of the plan, other sources and email rows are never removed", not (rm & {"cal-keep", "fact-1", "email-1", "lunch-x", "cal-day-20261201-0"}), rm)
check("index: reasons are reported", plan["by_reason"].get("sheet_row_gone_from_sheet") == 1 and plan["history_kept"] >= 3, plan)
only_feed = index.plan_calendar_removals(db, [c for c in plan_chunks if c["metadata"]["source"] != "calendar_sheet"], TODAY)
check("index: without sheet chunks in the run (sheet fetch failed) no sheet row is touched", not any(i.startswith("sheet-") for i in only_feed["remove"]), only_feed)
check("index: nothing is removed from sources the run did not produce (lunch-only run)", index.plan_calendar_removals(db, [chunk("lunch-y", "lunch", "2026-12-01")], TODAY)["remove"] == [])
check("index: rows older than the school year are listed apart (removed by date alone, never held back by the refusal rule)", plan["dated"] == ["cal-old-year"], plan["dated"])
check("index: a plan that would remove a quarter of the calendar is refused", index.removal_refused(300, 900) and not index.removal_refused(8, 900) and not index.removal_refused(20, 60))

# --- team sheets: duplicate rows (the JV table lists some games twice, once without a time) ----------------------------
csv_text = "DATE,HOME TEAM,AWAY TEAM,TIME,LOCATION\n1/13/2027,Nueva,KIPP San Jose Collegiate,,Nueva\n1/13/2027,Nueva,KIPP San Jose Collegiate,4:00 PM,Nueva Gym\n1/14/2027,Mt. View Academy,Nueva,3:30 PM,Away\n1/14/2027,Mt. View Academy,Nueva,,Away\n"
evs = sheets.sports_events({"title": "US Boys Basketball JV", "url": SHEET, "text": csv_text}, date(2026, 1, 1), date(2028, 1, 1))
by = {e["date"].isoformat(): e for e in evs}
check("sheet: the duplicate row WITH a time wins, whichever comes first", len(evs) == 2 and by["2027-01-13"]["start"] == "16:00" and by["2027-01-14"]["start"] == "15:30", evs)
check("sheet: the winning copy brings its own location", by["2027-01-13"]["location"] == "Nueva Gym", by["2027-01-13"])

# --- audit CI mode ---------------------------------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import audit_data as ad     # noqa: E402

A = ad.Audit({}, ci=True)
A.check("g.conflict", False, "two sources disagree", "conflict")
A.check("g.orphan", False, "orphan row", "orphan")
A.check("g.mismatch", False, "index differs")
A.check("g.ok", True, "")
check("audit ci: conflicts and orphans only warn, a mismatch fails", A.status("g.conflict") == "warn" and A.status("g.orphan") == "warn" and A.status("g.mismatch") == "FAIL" and A.status("g.ok") == "ok")
B = ad.Audit({}, ci=False)
B.check("g.conflict", False, "two sources disagree", "conflict")
check("audit local: a conflict still fails unless accepted in audit_known.json", B.status("g.conflict") == "FAIL")
check("audit ci: only groups that work on a bare runner are selected", set(ad.CI_GROUPS) == {"bell", "teams", "ical", "fresh", "email", "share", "lunch"}
      and set(ad.CI_SKIPPED) == {"days", "courses", "clubs", "index", "canon"})

calls = []
names = ("days", "bell", "courses", "clubs", "teams", "ical", "fresh", "orphans", "email", "lunch", "canon")
saved = {n: getattr(ad, f"audit_{n}") for n in names}
for n in names:
    setattr(ad, f"audit_{n}", (lambda n: lambda A, S: calls.append(n))(n))
dump = tmp / "db.json"
dump.write_text("[]")
argv = sys.argv
try:
    sys.argv = ["audit_data.py", "--ci", "--db-json", str(dump), "--json", str(tmp / "audit.json")]
    with contextlib.redirect_stdout(io.StringIO()):
        rc = ad.main()
finally:
    sys.argv = argv
    for n in names:
        setattr(ad, f"audit_{n}", saved[n])
res = json.loads((tmp / "audit.json").read_text())
check("audit ci: runs bell, teams, ical, fresh, email, lunch and nothing else", sorted(calls) == ["bell", "email", "fresh", "ical", "lunch", "teams"], calls)
check("audit ci: skipped groups are listed with a 'skipped in CI' note", all(res[g]["skip"].startswith("skipped in CI") for g in ("days.sheet", "courses.index", "clubs.index", "index.orphan_rows", "canon.*")), res)
check("audit ci: exit 0 when nothing failed", rc == 0, rc)

import os    # noqa: E402
import types   # noqa: E402

os.environ["NUEVAI_CALENDAR_URL"] = ""
fake = types.SimpleNamespace(args=types.SimpleNamespace(offline=False, ci=True), db=None, today=date(2026, 11, 12), db_err="")
A = ad.Audit({}, ci=True)
ad.audit_ical(A, fake)
check("audit ci: a missing calendar secret FAILS the unattended job (it must not pass without reading the calendar)", A.status("ical.feed") == "FAIL", A.g)
fake.args.ci = False
A = ad.Audit({}, ci=False)
ad.audit_ical(A, fake)
check("audit local: a missing calendar URL is just a skipped group", A.status("ical.feed") == "SKIP")

# the compare_team dedupe (one copy without a time) is information, two different times is a conflict, a removed future row is a mismatch, a past one is history
A = ad.Audit({}, ci=True)
sheet_evs = [{"date": date(2027, 1, 13), "opp": "kipp", "ha": "home", "start": None, "loc": "", "game": True},
             {"date": date(2027, 1, 13), "opp": "kipp", "ha": "home", "start": "16:00", "loc": "", "game": True},
             {"date": date(2027, 1, 14), "opp": "menlo", "ha": "away", "start": "15:00", "loc": "", "game": True},
             {"date": date(2027, 1, 14), "opp": "menlo", "ha": "away", "start": "17:00", "loc": "", "game": True}]
rows = [{"metadata": {"event_date": "2027-01-13", "summary": "JV - Game KIPP - Home", "home_away": "home", "start_time": "16:00", "location": ""}},
        {"metadata": {"event_date": "2027-01-14", "summary": "JV - Game Menlo - Away", "home_away": "away", "start_time": "15:00", "location": ""}},
        {"metadata": {"event_date": "2027-02-02", "summary": "JV - Game Gone - Away", "home_away": "away", "start_time": "15:00", "location": ""}},
        {"metadata": {"event_date": "2026-11-05", "summary": "JV - Game Past - Away", "home_away": "away", "start_time": "15:00", "location": ""}}]
ad.compare_team(A, "t", "JV", sheet_evs, rows, date(2026, 11, 12))
kinds = sorted(k for k, _ in A.g["t"]["bad"])
check("audit teams: dup with one time = info; two times = conflict; removed future row = mismatch; past row = history info",
      kinds == ["conflict", "mismatch"] and any("kept as history" in i for i in A.g["t"]["info"]) and any("uses the copy with the time" in i for i in A.g["t"]["info"]), (kinds, A.g["t"]))

# --- CI summary ------------------------------------------------------------------------------------------------------
import importlib.util   # noqa: E402
spec = importlib.util.spec_from_file_location("ci_summary", Path(__file__).resolve().parent / "scripts" / "ci_summary.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)
d = Path(tempfile.mkdtemp())
(d / "pipeline").mkdir()
cs.DATA, cs.PIPE = d, d / "pipeline"
check("summary: tolerates a run that wrote nothing", "no report was written" in cs.build() and "No audit report" in cs.build())
(d / "index_count_before.txt").write_text("5000")
(d / "index_count_after.txt").write_text("5010")
(d / "pipeline" / "report.json").write_text(json.dumps({"stages": {"calendar": {"events_kept": 669, "events_in_feed": 921, "duplicates_removed": 252, "cancelled": 0,
                                                                                  "range": "2026-09-11 .. 2027-07-23", "chunks": 912},
                                                        "index": {"total_chunks": 1050, "to_embed": 16, "unchanged": 1034, "expired_calendar": 5,
                                                                  "calendar_removals_by_reason": {"sheet_row_gone_from_sheet": 5}, "calendar_history_kept": 54}}}))
(d / "pipeline" / "audit.json").write_text(json.dumps({"ical.index": {"status": "ok", "checks": 10, "problems": [], "skip": None, "info": []},
                                                       "teams.live_tabs": {"status": "FAIL", "checks": 5, "problems": ["mismatch: x"], "skip": None, "info": []},
                                                       "days.sheet": {"status": "SKIP", "checks": 0, "problems": [], "skip": "skipped in CI: workbook", "info": []}}))
md = cs.build()
check("summary: shows row counts, calendar events and window, removals and the failed audit group",
      "5000 before, 5010 after" in md and "669" in md and "2026-09-11 .. 2027-07-23" in md and "sheet_row_gone_from_sheet" in md and "FAILED" in md and "`teams.live_tabs`" in md, md)
(d / "pipeline" / "report.json").write_text(json.dumps({"stages": {}, "error": {"stage_failed": "calendar", "type": "HTTPError"}}))
check("summary: a failed pipeline names the stage", "FAILED in stage `calendar`" in cs.build())

# --- titles: division, classes cancelled, category, placeholders ------------------------------------------------------
import calendar_meta as cm   # noqa: E402

for title, want in [("MS Parent Ed: Digital Literacy Night", ["ms"]), ("LS/MS Parent Workshop: Demystifying ERBs (Grades 3-8)", ["ls", "ms"]),
                    ("US P/T Conferences", ["us"]), ("Admissions Financial Assistance Workshop", []), ("11th Grade PSAT", ["us"]),
                    ("Class of 2027 School Day ACT", ["us"]), ("PreK Parent Social", ["ls"]), ("4th grade: Rising 5th Grade Parent Coffee", ["ls", "ms"]),
                    ("MS Junior Varsity (JV) 5th-6th Flag Football - Practice -", ["ms"]), ("US Boys Basketball Varsity - Game Menlo - Home", ["us"]),
                    ("Upper School Homecoming Dance", ["us"]), ("HB Campus Book Fair", ["ls", "ms"]), ("Nueva Mid-Autumn Festival Celebration", [])]:
    got = cm.divisions_of(title)
    check(f"division: {title!r} -> {want}", got == want, got)
for title, want in [("No LS & MS Classes: LS & MS Parent/Teacher Conferences", ["ls", "ms"]), ("No US Classes: US Parent-teacher Conferences", ["us"]),
                    ("No Classes for LS/MS Students ONLY: LS & MS Admissions Event (All Day)", ["ls", "ms"]), ("No School: Presidents' Day", ["all"]),
                    ("No Classes: Faculty Work Day", ["all"]), ("No Classes: Winter Break", ["all"]), ("School Closed: Juneteenth (Observed)", ["all"]),
                    ("LS Parent-Teacher Conferences (LS Classes in Session)", None), ("US Fall Play: Lend Me a Tenor", None)]:
    got = cm.no_classes_for(title)
    check(f"no classes: {title!r} -> {want}", got == want, got)
check("campus: a San Mateo viewer sees US and whole-school events, not LS/MS-only ones", cm.applies_to(["us"], "san_mateo") and cm.applies_to([], "san_mateo")
      and not cm.applies_to(["ls", "ms"], "san_mateo") and cm.applies_to(["ls", "ms"], "hillsborough") and not cm.applies_to(["us"], "hillsborough") and cm.applies_to(["ms"], None))
for title, cat in [("No Classes: Faculty Work Day", "no_school"), ("No LS/MS Classes: LS & MS Parent-Teacher Conferences", "no_school"), ("No US Classes: US Parent-teacher Conferences", "no_school"),
                   ("No Classes:Professional Learning Day", "no_school"), ("New Year’s Day: Campus and Offices Closed", "no_school"), ("No School : November Break", "no_school"),
                   ("US: Winter Formal", "community"), ("Evening of the Arts", "arts"), ("School Resumes (All Divisions)", "academics"),
                   ("US Boys Basketball Varsity - Game Menlo - Home", "athletics"), ("Jazz Concert on the Quad", "arts"), ("Halloween", "other")]:
    check(f"category: {title!r} -> {cat}", cf.categorize(title) == cat, cf.categorize(title))
check("placeholders: '(MOVED TO OCT 8)' and 'CANCELED:' are dropped, '[Moved from Nov 3]' (the new date) is kept",
      bool(cf.CALLED_OFF.search("(MOVED TO OCT 8) NPA Latine Heritage Month Celebration")) and bool(cf.CALLED_OFF.search("CANCELED: Parent Coffee"))
      and bool(cf.CALLED_OFF.search("Cancelled - Chess")) and not cf.CALLED_OFF.search("US Winter Sports Athlete/Parent Meeting [Moved from Nov 3]")
      and not cf.CALLED_OFF.search("Fall Concert"))
cf.build(cf.parse_ics(feed(ev("c1", "No LS/MS Classes: LS & MS Parent-Teacher Conferences", "DTSTART;VALUE=DATE:20261130", "DTEND;VALUE=DATE:20261201"),
                           ev("c2", "(MOVED TO OCT 8) NPA Celebration", "DTSTART;VALUE=DATE:20261201", "DTEND;VALUE=DATE:20261202"))), log=lambda *a: None, today=TODAY)
evs = [c["metadata"] for c in json.loads(config.CALENDAR_FILE.read_text()) if c["metadata"]["source"] == "calendar"]
check("build: division, no_classes and category are stored; the placeholder is not indexed",
      len(evs) == 1 and evs[0]["division"] == ["ls", "ms"] and evs[0]["no_classes"] == ["ls", "ms"] and evs[0]["category"] == "no_school", evs)

# --- master schedule: special bell schedules and events -------------------------------------------------------------------
import openpyxl   # noqa: E402
from pipeline import workbooks   # noqa: E402

wbk = openpyxl.Workbook()
ws = wbk.active
ws.title = "Calendar AB + Special Schedule"
ws.append(["Week", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
ws.append([None, datetime(2026, 11, 1)])
ws.append([1, 9, 10, 11, 12, 13, None, "Event", "Point Person(s)", "Notes + Links"])
ws.append([None, "Advisory", "Dept Meetings", "Community (40')", "Advisory", "Community (90')", None, "Community (40') / Diwali (Nov 8)", "Someone", "N/A"])
ws.append([None, "A", "B", "B", "A'", "B'"])
ws.append([2, 16, 17, 18, 19, 20, None, "Event", "Point Person(s)", "Notes + Links"])
ws.append([None, None, None, "Full-Day Event", None, None, None, "Full-Day / CSL Day", "Someone", "Bring a jacket"])
ws.append([None, "A", "B", "CSL Day", "A'", "B'"])
ws.append([None] * 11 + ["Community Time [40 Minutes]\n[65-Minute Classes]\n\nBlock 1: 8:55 - 10:00\nBlock 2: 10:05 - 11:10\n\nAssembly -or- Activity: 11:20 - 12:00\n"
                         "Lunch: 12:00 - 12:50\n\nBlock 3: 12:55 - 2:00\nBlock 4: 2:05 - 3:10"])
ws.append([None] * 11 + ["Full-Day Event\n[No Classes]\n\nAM Activities: 8:55 - 11:30\nLunch: 11:30 - 12:30\nPM Activities: 12:30 - 3:10"])
sp = workbooks.parse_special_schedules(wbk)
check("special: the 40-minute and full-day configurations are read from the cells (a 90-minute one is absent here)", set(sp) == {"community_40", "full_day"} and sp["community_40"]["minutes"] == 65
      and sp["community_40"]["lines"][2] == ("Assembly -or- Activity", "11:20 AM-12:00 PM") and sp["full_day"]["lines"][0] == ("AM Activities", "8:55-11:30 AM"), sp)
recs = {c["metadata"]["event_date"]: c for c in workbooks.day_chunks(wbk, "t.xlsx")}
wed = recs["2026-11-11"]["content"]
check("special: a B day with Community (40') shows its own times, in its own block numbers, and the event name",
      "Block 5 8:55-10:00 AM; Block 6 10:05-11:10 AM; Assembly -or- Activity 11:20 AM-12:00 PM; Lunch 12:00-12:50 PM; Block 7 12:55-2:00 PM; Block 8 2:05-3:10 PM" in wed
      and "Event: Diwali (Nov 8)" in wed and "65-minute classes" in wed and recs["2026-11-11"]["metadata"]["schedule"] == "community_40", wed)
check("special: a regular A' day keeps the standard bell times", "Times: Block 1 8:55-10:10 AM; Block 2 10:15-11:30 AM; Lunch 11:30 AM-12:00/12:30 PM; Block 4 12:35-1:50 PM; Block 3 1:55-3:10 PM" in recs["2026-11-12"]["content"]
      and "schedule" not in recs["2026-11-12"]["metadata"])
fri = recs["2026-11-13"]["content"]
check("special: Community (90') with no configuration on the sheet falls back to the standard times, never an invented schedule", "SPECIAL" not in fri and "Block 5 8:55-10:10 AM" in fri, fri)
full = recs["2026-11-18"]["content"]
check("special: a full-day event shows its AM/PM activity times, the event and the note", "AM Activities 8:55-11:30 AM; Lunch 11:30 AM-12:30 PM; PM Activities 12:30-3:10 PM" in full
      and "Event: CSL Day" in full and "Notes: Bring a jacket" in full and "do not meet" in full, full)

print(f"\n{'all passed' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
