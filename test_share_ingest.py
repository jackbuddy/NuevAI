"""Offline tests for the Drive/Gmail auto-ingest (Sheets + Docs shared with the ingest account, schedule emails).

No network, no credentials, no real files or addresses: Google's APIs are a faithful fake (share_fixtures.FakeGoogle, answering at the HTTP level so the
REAL pipeline/gapi.py client runs), Supabase is fake_supabase.FakeSB, spreadsheets are built in code.

    python test_share_ingest.py
"""
import csv
import io
import json
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import event_precedence
import fake_supabase as FS
import freshness
import guard
import intent as intent_mod
import rag
import settings
import share_fixtures as F
from pipeline import email_ingest as ei, gapi, gsheets, index, schedule_text as st, share_ingest as si
from pipeline import sheets as old_sheets

FAILS = []
NOW = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 12)
CH = settings.CHUNKS_TABLE
HERE = Path(__file__).resolve().parent


def check(name, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {name}{'' if cond else '  ' + str(detail)[:500]}")
    if not cond:
        FAILS.append(name)


# =============================================================================================================== dates, times, days
REF = date(2026, 11, 10)
check("date: serial number 46342 -> Mon 2026-11-16", st.parse_cell_date("46342", REF) == (date(2026, 11, 16), None))
check("date: 'Tue 11/17' (no year) -> the school year of the file, weekday-consistent", st.parse_cell_date("Tue 11/17", REF) == (date(2026, 11, 17), None))
check("date: 'Mon 11/16/26', 'Nov 17', 'November 17, 2026', ISO", [st.parse_cell_date(x, REF)[0] for x in ("Mon 11/16/26", "Nov 17", "November 17, 2026", "2026-11-17")]
      == [date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 17), date(2026, 11, 17)])
check("date: January belongs to the second half of the school year", st.parse_cell_date("Tue 1/5", REF)[0] == date(2027, 1, 5))
check("date: a range gives start and end", st.parse_cell_date("Mon 11/23/26 - Fri 11/27/26", REF) == (date(2026, 11, 23), date(2026, 11, 27)))
check("date: not a date -> None (TBD, text, impossible day)", all(st.parse_cell_date(x, REF) is None for x in ("TBD", "Nueva", "13/45", "")))
check("time: '3:45 PM', '6:00PM', '3:45 pm'", [st.parse_time(x)[2] for x in ("3:45 PM", "6:00PM", "3:45 pm")] == ["3:45 PM", "6:00 PM", "3:45 PM"])
check("time: ranges '4:00-5:30 PM', '4-5:30pm', '11-1 PM'", [st.parse_time(x) for x in ("4:00-5:30 PM", "4-5:30pm", "11-1 PM")]
      == [("16:00", "17:30", "4:00-5:30 PM"), ("16:00", "17:30", "4:00-5:30 PM"), ("11:00", "13:00", "11:00 AM-1:00 PM")])
check("time: TBD / TBA -> time to be announced, no start", st.parse_time("TBD") == (None, None, "time to be announced") and st.parse_time("tba")[0] is None)
check("time: a serial time fraction 0.625 -> 3:00 PM", st.parse_time("0.625") == ("15:00", None, "3:00 PM"))
check("days: Tue/Thu, Tuesdays and Thursdays, Mon-Fri, M/W/F", [st.fmt_days(st.parse_days(x)) for x in ("Tue/Thu", "Tuesdays and Thursdays", "Mon-Fri", "M/W/F")]
      == ["Tue/Thu", "Tue/Thu", "Mon-Fri", "Mon/Wed/Fri"], [st.fmt_days(st.parse_days(x)) for x in ("Tue/Thu", "Tuesdays and Thursdays", "Mon-Fri", "M/W/F")])

# =============================================================================================================== sheets: structure
M = lambda name, mt="2026-11-10T18:00:00.000Z": {"file_id": "driveid-" + name, "name": name, "modified_time": mt, "version": "7", "owner_domain": "example.org"}
SERIAL_DATE = F.cell(number=46342, fmt="DATE")
SERIAL_TIME = F.cell(number=0.625, fmt="TIME")
HEAD = ["DATE", "OPPONENT", "H/A", "TIME", "LOCATION", "Score", "W/L", "Bus notes"]
sched_rows = [
    ["Varsity Boys Soccer 2026-27", None, None, None, None, None, None, None],                                  # 0 merged title band
    [None] * 8,                                                                                              # 1
    [None, None, None, None, None, "Results", None, None],                                                    # 2 header band over Score / W/L
    HEAD,                                                                                                    # 3
    [SERIAL_DATE, "Menlo", "H", SERIAL_TIME, "Nueva Field", None, None, "leave 1:00"],                         # 4 serial date + serial time
    ["Wed 11/18", "Hidden Opp", "H", "4:00 PM", "Nueva Field", None, None, None],                             # 5 hidden row
    ["Tue 11/17", "Priory", "A", "3:45 PM", "Priory Gym", None, None, "bus 2:30"],                            # 6 year-less text date
    ["Fri 11/20", "TBD", None, "TBD", None, None, None, None],                                                # 7
    ["Sat 11/21", "Crystal Springs (G1)", "A", "10:00 AM", "CSUS", None, None, None],                         # 8 \ date merged over two rows
    [None, "Crystal Springs (G2)", "A", "12:00 PM", "CSUS", None, None, None],                                # 9 /
    [None] * 8,                                                                                              # 10
    [None] * 8,                                                                                              # 11
    ["Practice schedule"] + [None] * 7,                                                                      # 12 caption of a stacked table
    ["DAY", "TIME", "LOCATION"] + [None] * 5,                                                                # 13
    ["Tue/Thu", "4:00-5:30 PM", "Main Field"] + [None] * 5,                                                  # 14
    ["Mon", "3:30 PM-5:00 PM", "Gym"] + [None] * 5,                                                          # 15
    [None] * 8,                                                                                              # 16
    ["Home games"] + [None] * 7,                                                                              # 17 caption + repeated header + a repeated game
    HEAD,
    ["Mon 11/16/26", "Menlo", "H", "3:00 PM", "Nueva Field", None, None, None],
]
complex_tab = F.tab("Schedule", sched_rows, sheet_id=11, merges=[(0, 1, 0, 5), (2, 3, 5, 7), (8, 10, 0, 1)], hidden_rows={5}, hidden_cols={7})
roster_tab = F.tab("Roster", [["#", "Name", "Grade", "Position"], ["1", "Mia Chen", "10", "GK"], ["2", "Leo Park", "11", "FW"], ["3", "Ana Ruiz", "9", "MF"]], sheet_id=12)
hidden_tab = F.tab("Old drafts", [["DATE", "OPPONENT"], ["Mon 9/1", "Old"]], sheet_id=13, hidden=True)
book_complex = F.book(complex_tab, roster_tab, hidden_tab, title="Varsity Boys Soccer 2026-27")
A1 = gsheets.analyze_workbook(book_complex, M("Varsity Boys Soccer 2026-27"), NOW)
text_chunks = [c for c in A1.chunks if c["metadata"].get("kind") == "table"]
ev1 = [c for c in A1.chunks if c["metadata"].get("source") == "calendar_sheet"]
body = "\n".join(c["content"] for c in A1.chunks)
check("sheet: indexed; the Roster tab was skipped, the hidden tab was not read", A1.status == "indexed" and "Roster" in A1.skipped_tabs and any("hidden tab" in n for n in A1.notes), (A1.status, A1.notes))
check("sheet: stacked tables on one tab become separate tables (schedule with its caption, practice, home games)", {c["metadata"]["table"] for c in text_chunks} >= {"Varsity Boys Soccer 2026-27", "Practice schedule", "Home games"},
      [c["metadata"]["table"] for c in text_chunks])
check("sheet: merged header band folded into the column labels ('Results Score')", "Results Score" in body and "Results W/L" in body, body[:700])
check("sheet: hidden row, hidden column and student roster never reach the text", "Hidden Opp" not in body and "Bus notes" not in body and "leave 1:00" not in body and "Mia Chen" not in body, body[:300])
check("sheet: hidden rows are noted in the text", "hidden or filtered-out row" in body)
check("sheet: date serial and time fraction cells are shown as formatted text", "Mon 11/16/26" in body and "3:00 PM" in body and "46342" not in body and "0.625" not in body, body[:900])
check("sheet: weekly practice table summarised ('Practice: Tue/Thu 4:00-5:30 PM at Main Field')", "Practice: Tue/Thu 4:00-5:30 PM at Main Field" in body and "Practice: Mon 3:30-5:00 PM at Gym" in body, body[-700:])
games = {c["metadata"]["summary"]: c["metadata"] for c in ev1}
check("events: Menlo (serial date + serial time) home 15:00", any("Menlo" in k and v["event_date"] == "2026-11-16" and v["start_time"] == "15:00" and v["home_away"] == "home" for k, v in games.items()), list(games))
check("events: 'Tue 11/17' year-less -> 2026-11-17, away, 15:45, location", any("Priory" in k and v["event_date"] == "2026-11-17" and v["home_away"] == "away" and v["start_time"] == "15:45" and v["location"] == "Priory Gym" for k, v in games.items()))
check("events: TBD opponent/time kept with 'time to be announced'", any("Game TBD" in k and v["time"] == "time to be announced" and v["all_day"] for k, v in games.items()))
check("events: vertically merged date applies to both rows (two games on 11/21)", sum(1 for v in games.values() if v["event_date"] == "2026-11-21") == 2)
check("events: the game repeated in a second 'Home games' table is not duplicated", sum(1 for v in games.values() if "Menlo" in v["summary"]) == 1, list(games))
check("events: hidden row produced no event; practice table (no dates) produced none", not any("Hidden" in k for k in games) and not any(v["kind"] == "practice" for v in games.values()))
m0 = ev1[0]["metadata"]
OLD_EV = old_sheets.event_chunk({"date": date(2026, 11, 17), "summary": "x", "time": "t", "start": None, "location": "", "kind": "game", "home_away": "home", "url": "u", "opp": "o"})["metadata"]
check("events: same record schema as pipeline/sheets.event_chunk", set(OLD_EV) <= set(m0), set(OLD_EV) - set(m0))
check("events: source calendar_sheet, category athletics, own id prefix, team label rag can filter on",
      m0["source"] == "calendar_sheet" and m0["category"] == "athletics" and all(c["chunk_id"].startswith("share-") for c in A1.chunks)
      and m0["summary"].startswith("US Boys Soccer Varsity - Game"), (m0["source"], m0["summary"]))
tm = text_chunks[0]["metadata"]
check("metadata: source_type, category, team, level, gender, sport, season, school year, title/tab, modified, ingested_at", tm["source_type"] == "sheet" and tm["category"] == "athletics"
      and tm["team"] == "US Boys Soccer Varsity" and tm["level"] == "Varsity" and tm["gender"] == "Boys" and tm["sport"] == "soccer" and tm["school_year"] == "2026-27"
      and tm["title"].startswith("Varsity Boys Soccer") and tm["tab"] == "Schedule" and tm["modified_time"].startswith("2026-11-10") and tm["indexed_at"] and tm["season"] == "Winter",
      {k: tm.get(k) for k in ("source_type", "category", "team", "level", "gender", "sport", "season", "school_year", "tab", "modified_time")})
check("metadata: no Drive file id, owner address or link to the file in any chunk", not any("driveid-" in json.dumps(c) or "docs.google.com" in json.dumps(c) or "@" in json.dumps(c["metadata"]) for c in A1.chunks))
FH_X = gsheets.file_hash("driveid-Varsity Boys Soccer 2026-27")
check("ids: stable across runs and always under share-<file hash>-", len({c["chunk_id"] for c in A1.chunks}) == len(A1.chunks)
      and {c["chunk_id"] for c in A1.chunks} == {c["chunk_id"] for c in gsheets.analyze_workbook(book_complex, M("Varsity Boys Soccer 2026-27"), NOW).chunks}
      and all(c["chunk_id"].startswith(f"share-{FH_X}-") for c in A1.chunks))

# --- the same rows the existing ingestion gets, from real and from synthetic team sheets ----------------------------------------------
SYN_CSV = """DATE,HOME TEAM,AWAY TEAM,TIME,Win/Loss,NOTES,Home game snack signup
Mon 11/2/26,First Day of Practice,,,,,
Mon 11/16/26,Nueva,Alpha Prep,6:00 PM,,,Sample Family
,Friendship Game,,,,,
Thu 11/19/26,Nueva,Beta Academy,5:30 PM,,,
Mon 11/23/26 - Fri 11/27/26,THANKSGIVING BREAK,,,,,
Fri 12/4/26,Gamma High - Town,Nueva,5:30 PM,,Game moved from 12/3,
Thu 12/10/26,Delta School,Nueva,6:00PM,,,
Tue 1/19/27,,,,,,
Thu 1/21/27,Epsilon,Nueva,,,,
Fri 1/29/27,Nueva,Zeta Christian,TBA,,,
DATE,HOME TEAM,AWAY TEAM,TIME,Win/Loss,NOTES,Home game snack signup
Mon 11/16/26,Nueva,Alpha Prep,5:00 PM,,,
Thu 11/19/26,Nueva,Beta Academy,7:00 PM,,,
"""
WIDE = (date(2000, 1, 1), date(2100, 1, 1))


def compare_with_existing(csv_text, title):
    page = {"title": title, "url": "https://example.org/sheet", "text": csv_text, "source": "google_sheet"}
    old = old_sheets.sports_events(page, *WIDE)
    a = gsheets.analyze_workbook(F.book(F.csv_tab(csv_text, "Schedule"), title=title), M(title), NOW, event_window=WIDE)
    new = [c["metadata"] for c in a.chunks if c["metadata"].get("source") == "calendar_sheet"]
    ok_k = sorted((e["date"].isoformat(), e["kind"], e["opp"].lower(), e["home_away"], e["start"] or "", e["location"]) for e in old)
    nw_k = sorted((m["event_date"], m["kind"], (m["summary"].split("Game ", 1)[1].rsplit(" - ", 1)[0].lower() if m["kind"] == "game" else ""), m["home_away"], m["start_time"] or "", m["location"]) for m in new)
    return ok_k, nw_k, a


o, n, a_syn = compare_with_existing(SYN_CSV, "US Girls Basketball")
check("parity: synthetic team sheet gives the SAME events (dates, kinds, opponents, home/away, times) as pipeline/sheets.py", o == n and len(o) >= 7, (set(o) ^ set(n)))
check("parity: the snack sign-up column (people's names) is dropped", "Sample Family" not in "\n".join(c["content"] for c in a_syn.chunks))
REAL = next((p for p in (HERE.parent.parent.parent / "data" / "pipeline" / "pages", Path.home() / "Developer" / "NuevAI" / "data" / "pipeline" / "pages") if p.is_dir()), None)
real_checked = 0
if REAL:
    for f in sorted(REAL.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if d.get("source") != "google_sheet" or not old_sheets.sports_events({"title": d.get("title", ""), "url": d.get("url", ""), "text": d.get("text", ""), "source": "google_sheet"}, *WIDE):
            continue
        o, n, _ = compare_with_existing(d["text"], d["title"])
        check(f"parity (REAL public team sheet, {len(o)} events): the Sheets-structure parser reproduces the existing ingestion's rows", o == n and len(o) > 5, set(o) ^ set(n))
        real_checked += 1
print(f"     (real public team sheets compared: {real_checked}{'' if real_checked else ' - none on disk here, only the synthetic copy ran'})")

# --- opponent/location/home-away layouts -----------------------------------------------------------------------------------------------------
lay = F.book(F.tab("Games", [["Date", "Opponent", "Home/Away", "Time", "Venue", "Notes"], ["Sat 11/21", "@ Menlo School", "", "10:00 AM", "Menlo Gym", ""],
                             ["Sun 11/22", "vs. Priory", "Home", "1:00 PM", "Nueva Gym", "Senior day"], ["Mon 11/23", "BYE", "", "", "", ""],
                             ["Wed 11/25", "Crystal Springs (Away)", "", "noon", "", ""]]), title="JV Girls Volleyball")
al = gsheets.analyze_workbook(lay, M("JV Girls Volleyball"), NOW)
evs = {c["metadata"]["event_date"]: c["metadata"] for c in al.chunks if c["metadata"].get("source") == "calendar_sheet"}
check("layout OPPONENT/H-A: '@ X' is away, 'vs. X' + Home is home, BYE is a note not a game, '(Away)' suffix, 'noon'",
      evs["2026-11-21"]["home_away"] == "away" and "Menlo School" in evs["2026-11-21"]["summary"] and evs["2026-11-22"]["home_away"] == "home" and evs["2026-11-22"]["description"] == "Senior day"
      and evs["2026-11-23"]["kind"] == "event" and evs["2026-11-25"]["home_away"] == "away" and evs["2026-11-25"]["start_time"] == "12:00", evs)
check("layout: JV girls volleyball -> 'US Girls Volleyball JV' with level and division", evs["2026-11-21"]["summary"].startswith("US Girls Volleyball JV - Game") and evs["2026-11-21"]["level"] == "JV" and evs["2026-11-21"]["division"] == "US")

# --- chunking: whole rows only ----------------------------------------------------------------------------------------------------------------
rows_long = [["DATE", "OPPONENT", "TIME", "LOCATION", "NOTES"]] + [[f"Mon {1 + (i % 28)}/{1 + (i // 28) % 12}/27", f"Opponent number {i} Academy of Sports", "4:30 PM", f"Field {i % 9}", f"Note for game {i} " + "x" * (1 + i % 40)] for i in range(420)]
long_book = F.book(F.tab("Long", rows_long, sheet_id=3), title="Varsity Boys Lacrosse 2026-27")
al = gsheets.analyze_workbook(long_book, M("Long table"), NOW, event_window=WIDE)
tcs = [c for c in al.chunks if c["metadata"].get("kind") == "table"]
exp_lines = []
for r_ in rows_long[1:]:
    b = io.StringIO()
    csv.writer(b, lineterminator="").writerow(r_)
    exp_lines.append(b.getvalue())
header_line = ",".join(rows_long[0])
got_lines, hdr_ok = [], True
for c in tcs:
    lines = c["content"].split("\n")
    i = lines.index(header_line)
    hdr_ok &= lines[i] == header_line
    got_lines += [l for l in lines[i + 1:] if l]
check("chunking: a 420-row table is cut into several chunks", len(tcs) >= 8, len(tcs))
check("chunking: every row appears exactly once, whole, in order (no row split across two chunks)", got_lines == exp_lines, (len(got_lines), len(exp_lines), [x for x in got_lines if x not in exp_lines][:2]))
check("chunking: the header row is repeated at the top of EVERY chunk", hdr_ok and all(header_line in c["content"] for c in tcs))
check("chunking: chunks stay within the size limit", max(len(c["content"]) for c in tcs) <= gsheets.CHUNK_HARD, max(len(c["content"]) for c in tcs))
check("chunking: chunk boundaries fall between rows (each chunk's rows are a contiguous slice)", all(c["metadata"]["row_first"] <= c["metadata"]["row_last"] for c in tcs)
      and [c["metadata"]["row_first"] for c in tcs][1:] == [c["metadata"]["row_last"] + 1 for c in tcs][:-1])
rg = gsheets.chunk_rows(100, "H", ["a" * 50] * 10, target=300)
check("chunk_rows: ranges partition the rows with no overlap or gap", [x for a, b in rg for x in range(a, b)] == list(range(10)) and len(rg) > 1, rg)
big = "w" * 590
huge = F.book(F.tab("T", [["DATE", "OPPONENT", "TIME", "LOCATION", "NOTES"], ["Mon 11/16/26", big, big, big, big], ["Tue 11/17/26", "Menlo", "4:00 PM", "Gym", "short"]], sheet_id=4), title="Varsity Boys Lacrosse")
ah = gsheets.analyze_workbook(huge, M("Huge row"), NOW)
big_chunks = [c for c in ah.chunks if c["metadata"].get("kind") == "table"]
check("chunking: a single over-long row is kept whole in a chunk of its own (never cut)", any(c["content"].count(big) >= 4 for c in big_chunks) and len(big_chunks) == 2, [len(c["content"]) for c in big_chunks])
side = [["DATE", "OPPONENT", "", "DATE", "OPPONENT"], ["Mon 11/16/26", "Alpha", "", "Sat 11/21/26", "Beta"], ["Tue 11/17/26", "Gamma", "", "Sun 11/22/26", "Delta"]]
as_ = gsheets.analyze_workbook(F.book(F.tab("Side", side), title="Varsity Boys Tennis"), M("side"), NOW)
check("tables: two tables side by side (each with its own Date header) are split on the blank column", sum(1 for c in as_.chunks if c["metadata"].get("kind") == "table") == 2
      and sum(1 for c in as_.chunks if c["metadata"].get("source") == "calendar_sheet") == 4)

# --- privacy -------------------------------------------------------------------------------------------------------------------------------------
rost = F.book(F.tab("Team", [["Player", "Grade", "Position"], ["Mia Chen", "10", "GK"], ["Leo Park", "11", "FW"], ["Ana Ruiz", "9", "MF"], ["Sam Lee", "12", "DF"]]), title="Varsity Soccer")
ar = gsheets.analyze_workbook(rost, M("Varsity Soccer"), NOW)
check("privacy: a roster-looking sheet is skipped as needs_review and nothing is indexed", ar.status == "needs_review" and not ar.chunks and "Mia" not in repr(ar.__dict__), (ar.status, ar.reason))
ar2 = gsheets.analyze_workbook(F.book(F.tab("Sheet1", [["Student last name", "Student first name", "Grade"], ["Chen", "Mia", "10"], ["Park", "Leo", "11"]]), title="Practice list"), M("Practice list"), NOW)
check("privacy: student name/grade columns -> needs_review", ar2.status == "needs_review" and not ar2.chunks)
ar3 = gsheets.analyze_workbook(F.book(F.tab("Sheet1", [["Date", "Opponent"], ["Mon 11/16", "Menlo"]]), title="Spring Sports Sign-up responses"), M("Spring Sports Sign-up responses"), NOW)
check("privacy: a file named like sign-ups/responses/roster -> needs_review without reading it", ar3.status == "needs_review" and not ar3.chunks)
ar4 = gsheets.analyze_workbook(F.book(F.tab("Team", [["Team member", "Shirt size"]] + [[n_, "M"] for n_ in ("Mia Chen", "Leo Park", "Ana Ruiz", "Sam Lee", "Eva Gil", "Tom Fox", "Zoe Ray")]), title="Varsity Frisbee"), M("Varsity Frisbee"), NOW)
check("privacy: a name column with shirt sizes is a roster", ar4.status == "needs_review" and not ar4.chunks, (ar4.status, ar4.reason))
mixed = F.book(complex_tab, F.tab("Contacts", [["Name", "Phone", "Email"], ["Coach A", "415-555-0123", "coach@example.com"]], sheet_id=20), title="Varsity Boys Soccer 2026-27")
am = gsheets.analyze_workbook(mixed, M("mixed"), NOW)
check("privacy: a schedule tab is still indexed when another tab is skipped", am.status == "indexed" and "Contacts" in am.skipped_tabs and "415-555" not in "\n".join(c["content"] for c in am.chunks))
red = F.book(F.tab("S", [["DATE", "OPPONENT", "TIME", "NOTES"], ["Mon 11/16/26", "Menlo", "4 PM", "call 415-555-0123 or coach@example.com"]]), title="Varsity Boys Tennis")
arr = gsheets.analyze_workbook(red, M("red"), NOW)
check("privacy: non-staff phone numbers and addresses in cells (and in event records) are redacted", all("415-555" not in c["content"] and "coach@example.com" not in c["content"] and "coach@example.com" not in json.dumps(c["metadata"]) for c in arr.chunks)
      and any("[phone removed]" in c["content"] for c in arr.chunks), [c["content"][-120:] for c in arr.chunks])
check("privacy: the guard.py pattern is the one used (student_data_reason on the table text)", guard.student_data_reason("Student ID: 123456") is not None)

# --- malformed / oddly structured ---------------------------------------------------------------------------------------------------------------
for label, bk in (("empty response", {}), ("no sheets key", {"properties": {}}), ("junk cells", {"sheets": [{"properties": {"title": "x"}, "data": [{"rowData": [{"values": [None, 5, "str", {"formattedValue": None}]}]}]}]}),
                  ("only blanks", F.book(F.tab("t", [[None, None], [None, None]]))), ("one lone cell", F.book(F.tab("t", [["hello"]]))),
                  ("merge over nothing", {"sheets": [{"properties": {"title": "t"}, "merges": [{"startRowIndex": 5, "endRowIndex": 9, "startColumnIndex": 0, "endColumnIndex": 3}], "data": []}]})):
    try:
        r_ = gsheets.analyze_workbook(bk, M("odd"), NOW)
        check(f"malformed sheet ({label}): no exception, status {r_.status}", r_.status in ("skipped", "needs_review", "indexed"))
    except Exception as e:
        check(f"malformed sheet ({label}): no exception", False, repr(e))

# =============================================================================================================== docs
DOC = ("<h1>Ultimate Frisbee season</h1><p>Practice: Tuesdays and Thursdays 4:00-5:30 PM at Field 2.</p><h2>Games</h2>"
       "<table><tr><th>Date</th><th>Opponent</th><th>Time</th><th>Location</th></tr><tr><td>Sat 11/21</td><td>Menlo</td><td>10:00 AM</td><td>Home field</td></tr>"
       "<tr><td>Sat 12/5</td><td>Crystal Springs</td><td>1:00 PM</td><td>CSUS</td></tr></table><p>Contact coach@example.com or 415-555-0199.</p>")
ad = si.analyze_doc(F.DOC_HTML.format(DOC), M("Ultimate Frisbee season"), NOW)
dt = "\n".join(c["content"] for c in ad.chunks)
check("doc: indexed through the plain-text path with a schedule summary on top", ad.status == "indexed" and "Schedule summary:" in ad.chunks[0]["content"] and "Practice: Tue/Thu 4:00-5:30 PM at Field 2" in ad.chunks[0]["content"], ad.chunks[0]["content"][:400])
check("doc: game table gives event records (ultimate, game, dates)", sorted(c["metadata"]["event_date"] for c in ad.chunks if c["metadata"].get("kind") == "game") == ["2026-11-21", "2026-12-05"] and ad.events == 2)
check("doc: contact details redacted, source_type doc, own prefix", "coach@example.com" not in dt and "415-555-0199" not in dt and all(c["chunk_id"].startswith("share-") for c in ad.chunks) and ad.chunks[0]["metadata"]["source_type"] == "doc")
ad2 = si.analyze_doc(F.DOC_HTML.format("<p>Class roster</p><p>Mia Chen, 3.0</p><p>Leo Park, 3.1</p><p>Ana Ruiz, 3.2</p><p>padding padding padding padding padding</p>"), M("Notes"), NOW)
check("doc: student-level data -> needs_review", ad2.status == "needs_review" and not ad2.chunks)
tbl = ["| Date | Opponent | Time |"] + [f"| Mon {1 + i % 28}/1/27 | Team {i} | 4:00 PM |" for i in range(200)]
pk = si._pack_section("T", "\n".join(tbl))
rows_back = [l for t, p in pk for l in p.split("\n") if l != tbl[0]]
check("doc: a long pipe table is cut between rows with the header repeated, no row lost or split", len(pk) > 2 and rows_back == tbl[1:] and all(p.startswith(tbl[0]) for t, p in pk))

# =============================================================================================================== Google client
CFG = gapi.load_config({"GOOGLE_OAUTH_CLIENT_ID": "cid", "GOOGLE_OAUTH_CLIENT_SECRET": "csecret", "GOOGLE_OAUTH_REFRESH_TOKEN": "rtoken"})
check("config: missing secrets -> None (feature not configured)", gapi.load_config({}) is None and gapi.load_config({"GOOGLE_OAUTH_CLIENT_ID": "x"}) is None and "not configured" in gapi.NOT_CONFIGURED)
check("config: repr never shows a secret", "csecret" not in repr(CFG) and "rtoken" not in repr(CFG))


def files_fixture():
    return {
        "fSoccer": F.sheet_file("Varsity Boys Soccer 2026-27", book_complex, modified="2026-11-10T18:00:00.000Z", owner="coach@example.org"),
        "fDoc": F.doc_file("Ultimate Frisbee season", DOC, modified="2026-11-09T18:00:00.000Z"),
        "fFolder": F.folder_file("Spring sports", shared=True),
        "fKid": F.sheet_file("Spring Tennis", F.book(F.tab("S", [["DATE", "OPPONENT", "TIME"], ["Sat 3/6/27", "Menlo", "3 PM"]]), title="Spring Tennis"), modified="2026-11-08T10:00:00.000Z",
                             shared=False, parents=["fFolder"]),
        "fSub": F.folder_file("Archive", shared=False, parents=["fFolder"]),
        "fDeep": F.doc_file("Deep doc", "<p>Practice Mondays 3-4 PM at the gym for the chess club meetings.</p>", modified="2026-11-01T10:00:00.000Z", shared=False, parents=["fSub"]),
        "fTrash": F.sheet_file("Trashed", F.book(F.tab("S", [["a", "b"]])), trashed=True),
        "fPdf": {"name": "x.pdf", "mimeType": "application/pdf", "modifiedTime": "2026-11-01T00:00:00Z", "shared": True},
    }


fg = F.FakeGoogle(files_fixture(), page_size=2)
gc = gapi.GoogleClient(CFG, session=fg, sleep=lambda s: None)
found, warns = gc.discover()
names_found = sorted(f["name"] for f in found)
check("discovery: shared sheets/docs, files inside shared folders and sub-folders; trashed, non-Google and folder objects excluded; pagination followed",
      names_found == ["Deep doc", "Spring Tennis", "Ultimate Frisbee season", "Varsity Boys Soccer 2026-27"] and not warns, names_found)
check("discovery: queries ask for sharedWithMe, not trashed, and read-only scopes are declared", any("sharedWithMe = true" in p.get("q", "") and "trashed = false" in p.get("q", "") for _, _, p in fg.requests)
      and all("readonly" in s for s in gapi.SCOPES))
check("discovery: fields requested include id,name,mimeType,modifiedTime,version,owners,parents", all(x in gapi.GoogleClient.FIELDS for x in ("id", "name", "mimeType", "modifiedTime", "version", "owners", "parents")))
check("auth: one token request served all calls (cached until expiry)", fg.tokens_issued == 1, fg.tokens_issued)
t = [1000.0]
fg2 = F.FakeGoogle(files_fixture())
gc2 = gapi.GoogleClient(CFG, session=fg2, sleep=lambda s: None, clock=lambda: t[0])
gc2.discover()
t[0] += 4000
gc2.discover()
check("auth: the access token is refreshed in-process after it expires", fg2.tokens_issued == 2, fg2.tokens_issued)
fg3 = F.FakeGoogle(files_fixture())
gc3 = gapi.GoogleClient(CFG, session=fg3, sleep=lambda s: None)
gc3.access_token()
fg3.valid.clear()                      # the server forgets the token: 401 -> refresh -> retry
check("auth: a 401 triggers a refresh and the call succeeds", gc3.get_file("fDoc")["name"] == "Ultimate Frisbee season" and fg3.tokens_issued == 2)
fg4 = F.FakeGoogle(files_fixture())
fg4.refresh_fail = "invalid_grant"
try:
    gapi.GoogleClient(CFG, session=fg4, sleep=lambda s: None).discover()
    check("auth failure: invalid_grant raises GoogleAuthError", False)
except gapi.GoogleAuthError as e:
    msg_ = str(e)
    check("auth failure: invalid_grant -> clear re-authorize message (script name, 7-day Testing gotcha), no secrets", "google_auth_setup.py" in msg_ and "In production" in msg_ and "invalid_grant" in msg_
          and "rtoken" not in msg_ and "csecret" not in msg_, msg_)
sleeps = []
fg5 = F.FakeGoogle(files_fixture())
fg5.fail_next = [("/drive/v3/files", F.err(429, "rateLimitExceeded")), ("/drive/v3/files", F.Resp(503, {"error": {"errors": [{"reason": "backendError"}]}}))]
gc5 = gapi.GoogleClient(CFG, session=fg5, sleep=sleeps.append)
found5, _ = gc5.discover()
check("rate limits: 429 and 503 are retried with backoff and the listing still completes", len(found5) == 4 and len(sleeps) == 2 and sleeps[1] >= sleeps[0], sleeps)
fg6 = F.FakeGoogle(files_fixture())
fg6.fail_next = [("/drive/v3/files", F.err(429, "rateLimitExceeded"))] * 6
slp6 = []
try:
    gapi.GoogleClient(CFG, session=fg6, sleep=slp6.append).discover()
    check("rate limits: gives up after bounded tries", False)
except gapi.GoogleApiError as e:
    check("rate limits: gives up after bounded tries with a GoogleApiError", e.status == 429 and len(slp6) == gapi.MAX_TRIES - 1, (e.status, slp6))
fg7 = F.FakeGoogle(files_fixture())
fg7.fail_next = [("/drive/v3/files", F.err(403, "accessNotConfigured", "Drive API has not been used"))]
try:
    gapi.GoogleClient(CFG, session=fg7, sleep=lambda s: None).discover()
    check("API disabled: systemic auth error with instructions", False)
except gapi.GoogleAuthError as e:
    check("API disabled: systemic auth error with instructions", "Drive API and the Sheets API" in str(e))
sp, warn = gapi.GoogleClient(CFG, session=F.FakeGoogle(files_fixture()), sleep=lambda s: None).get_spreadsheet("fSoccer")
check("sheets API: one metadata call + one grid call per tab, same shape as spreadsheets.get; hidden tab not fetched",
      [s["properties"]["title"] for s in sp["sheets"]] == ["Schedule", "Roster", "Old drafts"] and "data" in sp["sheets"][0] and "data" not in sp["sheets"][2])
reqs = F.FakeGoogle(files_fixture())
gapi.GoogleClient(CFG, session=reqs, sleep=lambda s: None).get_spreadsheet("fSoccer")
check("sheets API: grid requests use includeGridData with an explicit capped range and a fields mask", any(p.get("includeGridData") == "true" and "!A1:" in p.get("ranges", "") and "merges" in p.get("fields", "") and "hiddenByUser" in p.get("fields", "")
                                                                                                    for _, _, p in reqs.requests))
rows_big = F.tab("Big", [["DATE", "OPPONENT"]], sheet_id=1)
rows_big["properties"]["gridProperties"] = {"rowCount": 9000, "columnCount": 60}
_, warn = gapi.GoogleClient(CFG, session=F.FakeGoogle({"b": F.sheet_file("Big", F.book(rows_big))}), sleep=lambda s: None).get_spreadsheet("b")
check("huge sheets: capped rows/columns with a clear logged truncation", any("truncated" in w and "9000" in w for w in warn), warn)

# =============================================================================================================== the run loop (manifest, replace, failures)
LOG = []


def new_world(files=None, missing=(), page_size=None):
    db_ = FS.FakeSB(missing=missing)
    g_ = F.FakeGoogle(files if files is not None else files_fixture(), page_size=page_size)
    store_ = si.ShareStore(sb=db_, embed_upsert=FS.fake_embed_upsert(CH), out=LOG.append, now=lambda: NOW)
    cl_ = gapi.GoogleClient(CFG, session=g_, sleep=lambda s: None)
    return db_, g_, store_, cl_


def go(db_, g_, store_, cl_, **kw):
    LOG.clear()
    return si.run(CFG, store_, client=cl_, now=NOW, out=LOG.append, **kw)


def manifest_of(db_):
    return {r["file_hash"]: r for r in db_.tables.get("share_ingest_manifest", [])}


db, g, store, cl = new_world()
code = go(db, g, store, cl)
rows = db.tables[CH]
fh_soccer, fh_doc = gsheets.file_hash("fSoccer"), gsheets.file_hash("fDoc")
check("run 1: new files are indexed (sheet, doc, files inside shared folders), exit 0", code == 0 and {r["metadata"]["share_file_hash"] for r in rows} >= {fh_soccer, fh_doc, gsheets.file_hash("fKid"), gsheets.file_hash("fDeep")}, LOG)
check("run 1: every row is under share-<hash>- and nothing else was written to the chunk table", all(r["chunk_id"].startswith("share-") for r in rows) and db.deleted == [])
man = manifest_of(db)
check("manifest: one row per file with modifiedTime, version, status indexed, counts, owner DOMAIN only", len(man) == 4 and man[fh_soccer]["status"] == "indexed" and str(man[fh_soccer]["modified_time"]).startswith("2026-11-10")
      and man[fh_soccer]["owner_domain"] == "example.org" and man[fh_soccer]["chunk_count"] > 0 and "coach@" not in json.dumps(db.tables["share_ingest_manifest"]) and "fSoccer" not in json.dumps(db.tables["share_ingest_manifest"]))
lg = db.tables["share_ingest_log"]
check("log rows: statuses, counts, reasons; no ids, bodies or addresses", {l["status"] for l in lg} == {"indexed"} and len(lg) == 4 and "fSoccer" not in json.dumps(lg) and "@" not in json.dumps(lg)
      and "Hidden Opp" not in json.dumps(lg), lg[:1])
check("stdout: no file ids, titles or addresses without --show-names", not any("fSoccer" in x or "Soccer" in x or "@" in x for x in LOG), LOG)
n_rows = len(rows)
snap = json.dumps(sorted((r["chunk_id"], r["metadata"]["content_hash"]) for r in rows))
g.requests.clear()
code = go(db, g, store, cl)
fetches = [p for _, p, _ in g.requests if p.startswith("/v4/") or p.endswith("/export")]
check("run 2 (nothing changed): no file is fetched, nothing written, nothing duplicated", code == 0 and not fetches and len(db.tables[CH]) == n_rows
      and json.dumps(sorted((r["chunk_id"], r["metadata"]["content_hash"]) for r in db.tables[CH])) == snap and any("unchanged=4" in x for x in LOG), (fetches, LOG))
embeds = []
store._embed_upsert = lambda sb, b: (embeds.extend(c["chunk_id"] for c in b), FS.fake_embed_upsert(CH)(sb, b))[1]
code = go(db, g, store, cl, file_id="fSoccer")
check("--file-id forces ONE file through even though unchanged; unchanged chunks are not re-embedded; still no duplicates", code == 0 and any("indexed" in x for x in LOG) and not embeds and len(db.tables[CH]) == n_rows, (embeds, LOG))
code = go(db, g, store, cl, force=True)
check("--force re-reads every file (and still duplicates nothing)", code == 0 and len(db.tables[CH]) == n_rows and sum(1 for x in LOG if x.strip().startswith("indexed")) == 4, LOG)
db.tables[CH].append({"chunk_id": "doc-keep-0001", "content": "x", "metadata": {"source": "public_site", "source_url": "https://x"}})
db.tables[CH].append({"chunk_id": "email-aaaaaaaaaaaaaaaa-000", "content": "e", "metadata": {"source": "email", "source_url": "email://a"}})
db.tables[CH].append({"chunk_id": "sheet-abc123abc123", "content": "live", "metadata": {"source": "calendar_sheet", "source_url": "https://live", "event_date": "2026-11-20", "summary": "x"}})
# --- an edited sheet: rows removed, rows changed, rows added -> everything is replaced, nothing stale remains
before_ids = {r["chunk_id"] for r in db.tables[CH] if r["chunk_id"].startswith(f"share-{fh_soccer}-")}
edited_rows = [list(r_) for r_ in sched_rows if not (isinstance(r_[1], str) and "Priory" in r_[1])]            # the Priory game is removed
edited_rows[4][3] = "4:15 PM"                                                                         # Menlo moved
edited_rows.insert(8, ["Sun 11/29", "New Opponent", "H", "2:00 PM", "Nueva Field", None, None, None])
ed_tab = F.tab("Schedule", edited_rows, sheet_id=11, merges=[(0, 1, 0, 5), (2, 3, 5, 7)], hidden_rows={5}, hidden_cols={7})
g.files["fSoccer"] = F.sheet_file("Varsity Boys Soccer 2026-27", F.book(ed_tab, roster_tab, hidden_tab), modified="2026-11-11T19:30:00.000Z", owner="coach@example.org", version=8)
embeds.clear()
code = go(db, g, store, cl)
cur = [r for r in db.tables[CH] if r["chunk_id"].startswith(f"share-{fh_soccer}-")]
cur_ev = [r["metadata"] for r in cur if r["metadata"].get("source") == "calendar_sheet"]
cur_txt = "\n".join(r["content"] for r in cur)
check("update: re-run picks up the changed file only (newer modifiedTime)", code == 0 and any("indexed" in x for x in LOG) and any("unchanged=3" in x for x in LOG), LOG)
check("update: the removed Priory row disappeared from text AND events; changed time and new game are there", "Priory" not in cur_txt and not any("Priory" in m["summary"] for m in cur_ev)
      and any("New Opponent" in m["summary"] for m in cur_ev) and any("Menlo" in m["summary"] and m["start_time"] == "16:15" for m in cur_ev), [m["summary"] for m in cur_ev])
check("update: no stale duplicate alongside the new data (one Menlo event, ids unique, old ids that vanished were deleted)", sum(1 for m in cur_ev if "Menlo" in m["summary"]) == 1
      and len({r["chunk_id"] for r in cur}) == len(cur) and not [i for i in db.deleted if not i.startswith(f"share-{fh_soccer}-")] and any(i in db.deleted for i in before_ids - {r["chunk_id"] for r in cur}), db.deleted)
left = {r["chunk_id"] for r in db.tables[CH]}
check("update: rows of other features and other files are untouched", {"doc-keep-0001", "email-aaaaaaaaaaaaaaaa-000", "sheet-abc123abc123"} <= left and any(r["chunk_id"].startswith(f"share-{fh_doc}-") for r in db.tables[CH]))
man = manifest_of(db)
check("update: manifest holds the new modifiedTime and version", str(man[fh_soccer]["modified_time"]).startswith("2026-11-11") and str(man[fh_soccer]["version"]) == "8")
# --- re-sharing the SAME file again (new share, same Drive file id, same modifiedTime) is a no-op
n_before = len(db.tables[CH])
g.files["fSoccer"]["shared"] = False
go(db, g, store, cl)
g.files["fSoccer"]["shared"] = True
go(db, g, store, cl)
check("re-share: sharing the same file again never duplicates its data (stable Drive-id key)", len([r for r in db.tables[CH] if r["chunk_id"].startswith(f"share-{fh_soccer}-")]) == len(cur), len(db.tables[CH]) - n_before)
# --- unshared: needs two complete runs, flagged not deleted; re-share restores
del_before = list(db.deleted)
g.files["fSoccer"]["shared"] = False
go(db, g, store, cl)
flagged1 = [r for r in db.tables[CH] if r["chunk_id"].startswith(f"share-{fh_soccer}-") and (r["metadata"] or {}).get("superseded")]
g_missing1 = manifest_of(db)[fh_soccer]["missing_runs"]
go(db, g, store, cl)
flagged2 = [r for r in db.tables[CH] if r["chunk_id"].startswith(f"share-{fh_soccer}-") and (r["metadata"] or {}).get("superseded")]
mrow = manifest_of(db)[fh_soccer]
check("unshared: one missing run only counts; the second flags chunks superseded (kept), manifest says unshared, log row written, nothing deleted", not flagged1 and g_missing1 == 1 and len(flagged2) == len(cur)
      and mrow["status"] == "unshared" and db.deleted == del_before and any(l["status"] == "unshared" for l in db.tables["share_ingest_log"]), (len(flagged1), g_missing1, len(flagged2), len(cur), mrow["status"]))
check("unshared: flagged rows are hidden from the event lookup (dedupe drops superseded)", not event_precedence.dedupe_events([r for r in flagged2 if r["metadata"].get("source") == "calendar_sheet"]))
g.files["fSoccer"]["shared"] = True
go(db, g, store, cl)
check("re-shared after being unshared: flags cleared, data back", not [r for r in db.tables[CH] if r["chunk_id"].startswith(f"share-{fh_soccer}-") and (r["metadata"] or {}).get("superseded")]
      and manifest_of(db)[fh_soccer]["status"] == "indexed", (manifest_of(db)[fh_soccer]["status"], LOG))
# --- a listing that comes back empty / incomplete never unshares anything
g_empty = F.FakeGoogle({})
go(db, g_empty, store, gapi.GoogleClient(CFG, session=g_empty, sleep=lambda s: None))
check("an empty listing does not unshare everything", not [r for r in db.tables[CH] if (r["metadata"] or {}).get("superseded")] and any("came back empty" in x for x in LOG), LOG)

# --- failures: malformed / odd file logged and skipped, run continues, bounded retries
files = files_fixture()
files["fBad"] = F.sheet_file("Weird sheet", {"properties": {}}, modified="2026-11-11T20:00:00.000Z")
files["fBroken"] = F.sheet_file("Broken sheet", F.book(F.tab("t", [["a"]])), modified="2026-11-11T21:00:00.000Z")
db, g, store, cl = new_world(files)
orig_get = g.request


def flaky(method, url, params=None, data=None, headers=None, timeout=None):
    if "fBroken" in url and params and "ranges" in params:
        return F.err(500, "backendError")
    return orig_get(method, url, params=params, data=data, headers=headers, timeout=timeout)


g.request = flaky
codes = [go(db, g, store, cl) for _ in range(3)]
mb = manifest_of(db)[gsheets.file_hash("fBroken")]
good = {r["metadata"]["share_file_hash"] for r in db.tables[CH] if r["chunk_id"].startswith("share-")}
check("failure: a file whose API call keeps failing never fails the run (exit 0) and everything else is indexed", codes == [0, 0, 0] and {fh_soccer, fh_doc} <= good, (codes, LOG))
statuses_b = [l["status"] for l in db.tables["share_ingest_log"] if l["file_hash"] == gsheets.file_hash("fBroken")]
check("failure: retried a bounded number of times (3) then marked failed", mb["status"] == "failed" and mb["attempts"] == 3 and statuses_b == ["error", "error", "failed"], (mb["status"], mb["attempts"], statuses_b))
g.requests.clear()
go(db, g, store, cl)
check("failure: a failed file is not retried forever (no fetch of it on the 4th run)", not any("fBroken" in p for _, p, _ in g.requests), g.requests[-3:])
g.files["fBroken"]["modifiedTime"] = "2026-11-12T01:00:00.000Z"
g.request = orig_get
go(db, g, store, cl)
check("failure: once the file changes it is tried again (and now succeeds)", manifest_of(db)[gsheets.file_hash("fBroken")]["status"] in ("indexed", "skipped"), (manifest_of(db)[gsheets.file_hash("fBroken")], LOG))
mw = manifest_of(db)[gsheets.file_hash("fBad")]
check("malformed sheet logged with a reason and skipped without breaking the run", mw["status"] in ("skipped", "needs_review", "error", "failed") and any(l["file_hash"] == gsheets.file_hash("fBad") and l["reason"] for l in db.tables["share_ingest_log"]), (mw, db.tables["share_ingest_log"][-3:]))
# roster file: needs_review, logged once, not retried
files = files_fixture()
files["fRoster"] = F.sheet_file("Varsity Soccer Team", rost, modified="2026-11-11T20:00:00.000Z")
db, g, store, cl = new_world(files)
go(db, g, store, cl)
g.requests.clear()
go(db, g, store, cl)
rl = [l for l in db.tables["share_ingest_log"] if l["file_hash"] == gsheets.file_hash("fRoster")]
check("roster sheet: logged needs_review ONCE with a reason, never indexed, not re-read on later runs", len(rl) == 1 and rl[0]["status"] == "needs_review" and "roster" in rl[0]["reason"].lower()
      and not any(r["metadata"].get("share_file_hash") == gsheets.file_hash("fRoster") for r in db.tables[CH]) and not any("fRoster" in p for _, p, _ in g.requests) and "Mia" not in json.dumps(db.tables), rl)
# --- systemic failures turn the run red
dbx, gx, sx, clx = new_world()
gx.refresh_fail = "invalid_grant"
check("auth failure: exit code 1 and the message tells how to re-authorize", go(dbx, gx, sx, clx) == 1 and any("GOOGLE AUTH FAILURE" in x and "google_auth_setup.py" in x for x in LOG), LOG)
dby, gy, sy, cly = new_world()
gy.fail_next = [("/drive/v3/files", F.Resp(500, {"error": {"errors": [{"reason": "backendError"}]}}))] * 6
check("Drive listing down: exit code 1, nothing processed, nothing flagged", go(dby, gy, sy, cly) == 1 and not dby.tables.get(CH), LOG)
dbz, gz, sz, clz = new_world()


def boom(sb, batch):
    raise RuntimeError("embedding service unavailable")


sz._embed_upsert = boom
check("database/embedding failure is systemic: exit code 1", go(dbz, gz, sz, clz) == 1)
# --- manifest table missing: still incremental via the chunks' own metadata
dbm, gm, sm, clm = new_world(missing={"share_ingest_manifest", "share_ingest_log"})
go(dbm, gm, sm, clm)
gm.requests.clear()
code = go(dbm, gm, sm, clm)
check("manifest/log tables missing: ingest still works, falls back to chunk metadata (second run fetches nothing) and logs to stdout", code == 0 and dbm.tables[CH] and not [p for _, p, _ in gm.requests if p.startswith("/v4/") or p.endswith("/export")]
      and any("not available" in x for x in LOG), LOG)
# --- dry run, budget, ordering, limit
dbd, gd, sd, cld = new_world()
go(dbd, gd, sd, cld, dry_run=True)
check("dry run: nothing is written (no chunks, no manifest, no log)", not dbd.tables.get(CH) and not dbd.tables.get("share_ingest_manifest") and not dbd.writes and any("[dry run]" in x for x in LOG), LOG)
dbb, gb, sb_, clb = new_world()
tick = [0.0]


def clock():
    tick[0] += 100.0
    return tick[0]


order = []
orig_pf = si.process_file


def spy(client, store_, f, now, dry, prev):
    order.append(f["name"])
    return orig_pf(client, store_, f, now, dry, prev)


si.process_file = spy
LOG.clear()
code = si.run(CFG, sb_, client=clb, now=NOW, out=LOG.append, budget=250, clock=clock)
si.process_file = orig_pf
check("time box: newest-changed first; the run stops gracefully at the budget and says what waits for the next run", order[0] == "Varsity Boys Soccer 2026-27" and 0 < len(order) < 4 and any("time budget" in x and "wait for the next run" in x for x in LOG) and code == 0, (order, LOG))
LOG.clear()
si.run(CFG, sb_, client=clb, now=NOW, out=LOG.append)
check("time box: the next run picks up the files that were deferred (resumable)", len({r["metadata"]["share_file_hash"] for r in dbb.tables[CH]}) == 4, LOG)
dbl, gl, sl, cll = new_world()
go(dbl, gl, sl, cll, limit=1)
check("--limit processes at most N files", len({r["metadata"]["share_file_hash"] for r in dbl.tables[CH]}) == 1)

# =============================================================================================================== removal / list tools
sys.path.insert(0, str(HERE / "scripts"))
import share_remove
import share_list
db, g, store, cl = new_world()
go(db, g, store, cl)
db.tables[CH].append({"chunk_id": "doc-keep-0002", "content": "x", "metadata": {"title": "Varsity handbook"}})
db.tables[CH].append({"chunk_id": "email-bbbbbbbbbbbbbbbb-000", "content": "x", "metadata": {"title": "Varsity Boys Soccer email"}})
out = []
before = len(db.tables[CH])
code = share_remove.execute(db, "Varsity Boys Soccer", delete=False, out=out.append)
check("share_remove: dry run is the default: lists the file's chunks, deletes nothing", code == 0 and len(db.tables[CH]) == before and any("DRY RUN" in x for x in out) and db.deleted == [], out)
out = []
code = share_remove.execute(db, "Varsity Boys Soccer", delete=True, out=out.append)
left = {r["chunk_id"] for r in db.tables[CH]}
check("share_remove --yes: deletes only that file's share-* rows (not emails, docs or other files); manifest becomes 'removed'", code == 0 and db.deleted and all(i.startswith(f"share-{fh_soccer}-") for i in db.deleted)
      and {"doc-keep-0002", "email-bbbbbbbbbbbbbbbb-000"} <= left and any(i.startswith(f"share-{fh_doc}-") for i in left)
      and not any(i.startswith(f"share-{fh_soccer}-") for i in left)
      and manifest_of(db)[fh_soccer]["status"] == "removed", (out, db.deleted[:3]))
go(db, g, store, cl)
not_back = not any(r["chunk_id"].startswith(f"share-{fh_soccer}-") for r in db.tables[CH])
go(db, g, store, cl, file_id="fSoccer")
check("share_remove: a removed file is NOT re-ingested while unchanged, but --file-id brings it back", not_back and any(r["chunk_id"].startswith(f"share-{fh_soccer}-") for r in db.tables[CH]), LOG)
check("share_remove: the Drive file id is hashed locally to match", share_remove.match(share_remove.share_rows(db), "driveidwithmorethantwentychars") == [] and share_remove.match(share_remove.share_rows(db), fh_soccer))
check("share_remove only ever touches the share- prefix", share_remove.PREFIX == "share-")
db.tables["share_ingest_log"].append({"processed_at": "2026-11-12T10:00:00", "source_type": "sheet", "file_hash": "abc", "name": "x", "status": "error", "chunk_count": 0, "event_count": 0, "reason": "r", "attempts": 1})
check("share_list: log, manifest and chunk summary render", "error" in share_list.fmt_log(share_list.fetch_log(db, 5, "error")) and share_list.fetch_manifest(db) and share_list.fetch_chunk_summary(db))
import google_auth_setup as gas
verifier, challenge = gas.pkce_pair()
url = gas.build_auth_url("cid", "http://127.0.0.1:5000", "st", challenge)
check("google_auth_setup: read-only scopes, offline access, PKCE S256, loopback redirect", "drive.readonly" in url and "spreadsheets.readonly" in url and "access_type=offline" in url and "code_challenge_method=S256" in url and "127.0.0.1" in url and "prompt=consent" in url)


class Sess:
    def __init__(self, body, status=200): self.body, self.status = body, status

    def post(self, url, data=None, timeout=None):
        self.data = data
        return F.Resp(self.status, self.body)


ok_body = {"access_token": "AT-SECRET", "refresh_token": "RT-123", "scope": " ".join(gapi.SCOPES)}
check("google_auth_setup: code exchange returns the refresh token (PKCE verifier sent)", gas.exchange_code("cid", "sec", "code", "http://127.0.0.1:1", verifier, Sess(ok_body)) == "RT-123")
for label, body_ in (("no refresh token", {"access_token": "AT-SECRET", "scope": " ".join(gapi.SCOPES)}), ("missing scope", {**ok_body, "scope": gapi.SCOPES[0]})):
    try:
        gas.exchange_code("cid", "sec", "code", "http://127.0.0.1:1", verifier, Sess(body_))
        check(f"google_auth_setup: {label} is refused", False)
    except SystemExit as e:
        check(f"google_auth_setup: {label} is refused with a plain message (no tokens)", "AT-SECRET" not in str(e) and "RT-123" not in str(e))

# =============================================================================================================== emails
CFGE = ei.load_config({"EMAIL_INGEST_USER": "bot@example.net", "EMAIL_INGEST_APP_PASSWORD": "abcd efgh ijkl mnop", "EMAIL_ALLOWED_SENDERS": "teacher@example.org, admin@example.org"})
AUTH_OK = "mx.google.com; dkim=pass header.i=@example.org header.s=s1 header.b=abc; spf=pass (google.com: domain of teacher@example.org designates 1.2.3.4 as permitted sender) smtp.mailfrom=teacher@example.org; dmarc=pass"


def msg(body, subject="Fwd: Practice schedule", sender="Teacher <teacher@example.org>", auth=AUTH_OK, mid="<m1@example.org>", date_h="Wed, 28 Oct 2026 10:00:00 -0700"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = sender, "bot@example.net", subject, date_h
    if mid:
        m["Message-ID"] = mid
    if auth:
        m["Authentication-Results"] = auth
    m.set_content(body)
    return m.as_bytes()


PRACTICE = """FYI, forwarding

---------- Forwarded message ---------
From: Coach T <coach@example.org>
Date: Mon, Nov 9, 2026 at 5:02 PM
Subject: Ultimate Frisbee practice and game schedule
To: Team <team@example.org>

Hi everyone,

Ultimate Frisbee season starts next week! Here is the schedule:

[image: team logo]
Practice: Tuesdays and Thursdays 4:00-5:30 PM at Field 2
Optional Saturday practice 9-10:30am at the turf field, Nov 14 through Dec 12
Location: Hillsdale Park

Games:
Date | Opponent | Time | Location
Sat 11/21 | Menlo | 10:00 AM | Home field
Sat 12/5 | Crystal Springs | 1:00 PM | CSUS Field

Docs
docs.google.com
Docs
Bring water and a light and dark shirt.
Thanks,
Coach T

Sent from my iPhone
To unsubscribe from this list click here: https://list-manage.com/unsubscribe?u=abc
"""
r = ei.analyze(msg(PRACTICE), CFGE, NOW)
first = r.chunks[0]["content"]
body_e = "\n".join(c["content"] for c in r.chunks if c["metadata"].get("source") == "email")
check("email: indexed, forwarded block parsed (original subject and date)", r.status == "indexed" and r.subject == "Ultimate Frisbee practice and game schedule" and r.source_date == "2026-11-09", (r.status, r.reason))
check("email: a clean 'Practice: Tue/Thu 4:00-5:30 PM at <place>' summary sits on top of the cleaned text", "Schedule summary:\n- Practice: Tue/Thu 4:00-5:30 PM at Field 2" in first.split("\n\n", 1)[1][:200]
      and "- Practice: Sat 9:00-10:30 AM at turf field (Nov 14 to Dec 12)" in first, first[:700])
check("email: game table parsed into the summary", "- Game: Sat Nov 21 10:00 AM vs Menlo at Home field" in body_e and "Crystal Springs" in body_e)
check("email: junk stripped (signature, unsubscribe footer, [image: ...], link-preview clutter, tracking link)", all(x not in body_e for x in ("Sent from my iPhone", "unsubscribe", "[image:", "list-manage", "docs.google.com")) and body_e.count("Bring water") == 1, body_e[-500:])
em = [c["metadata"] for c in r.chunks if c["metadata"].get("source") == "calendar_sheet"]
check("email events: 2 dated games + the bounded Saturday practice run; the open-ended Tue/Thu pattern is NOT expanded into invented dates",
      sum(1 for m in em if m["kind"] == "game") == 2 and sum(1 for m in em if m["kind"] == "practice") == 5 and {m["event_date"] for m in em if m["kind"] == "practice"} == {"2026-11-14", "2026-11-21", "2026-11-28", "2026-12-05", "2026-12-12"}, [(m["kind"], m["event_date"]) for m in em])
SCHEMA = set(OLD_EV)
check("email events: same schema as team-sheet events, ultimate/athletics, id email-<key>-e<hash>, source_type email", SCHEMA <= set(em[0]) and em[0]["source_type"] == "email" and em[0]["category"] == "athletics" and em[0]["summary"].startswith("Ultimate Frisbee - ")
      and all(re.fullmatch(r"email-[0-9a-f]{16}-(\d{3}|e[0-9a-f]{8}\d*)", c["chunk_id"]) for c in r.chunks), em[0])
tm = r.chunks[0]["metadata"]
check("email metadata: source_type email, category, sport, school year, sent date, thread key, no addresses", tm["source_type"] == "email" and tm["category"] == "athletics" and tm["sport"] == "ultimate" and tm["school_year"] == "2026-27"
      and tm["source_date"] == "2026-11-09" and len(tm["thread_key"]) == 16 and "@" not in json.dumps(tm))
check("email: no '@' addresses anywhere in the indexed text", all("@" not in c["content"] for c in r.chunks))
LINES = "Varsity boys soccer schedule update:\nSat 11/21 vs Menlo 10:00 AM (Home)\n11/25 @ Priory, 4:30 PM\nFri 12/4 vs Crystal Springs 3:30 PM at Nueva Field\nGo team!\n"
rl_ = ei.analyze(msg(LINES, subject="Soccer schedule"), CFGE, NOW)
gm_ = {m["event_date"]: m for m in (c["metadata"] for c in rl_.chunks) if m.get("source") == "calendar_sheet"}
check("email: game lines ('Sat 11/21 vs Menlo 10:00 AM (Home)', '11/25 @ Priory, 4:30 PM') -> game events with home/away and time", gm_["2026-11-21"]["home_away"] == "home" and gm_["2026-11-21"]["start_time"] == "10:00"
      and gm_["2026-11-25"]["home_away"] == "away" and "Priory" in gm_["2026-11-25"]["summary"] and gm_["2026-12-04"]["location"] == "Nueva Field", {k: (v["home_away"], v["start_time"], v["location"]) for k, v in gm_.items()})
quiet = ei.analyze(msg("The picnic is on the lawn. Bring a hat."), CFGE, NOW)
check("email: an email with no schedule gets no summary and no events", not [c for c in quiet.chunks if c["metadata"].get("source") == "calendar_sheet"] and "Schedule summary" not in quiet.chunks[0]["content"])
junk = """---------- Forwarded message ---------
From: Coach T <coach@example.org>
Date: Mon, Nov 9, 2026 at 5:02 PM
Subject: Update
To: Team <team@example.org>

Practice moves to the gym on Friday at 3:30 PM.
[cid:image001.png@01D]
CAUTION: This email originated from outside the organization. Do not click links.
Attachments: schedule.pdf
Reply | Forward
Google Docs
Google Docs
Open in Drive
Sent from Gmail Mobile
"""
cl_ = ei.clean_body(junk)
check("cleaner: [cid:], external-sender banner, Attachments:, Reply|Forward, link-preview titles and 'Open in Drive' are removed; the content stays", cl_.text.strip() == "Practice moves to the gym on Friday at 3:30 PM.", repr(cl_.text))
check("cleaner: a Google Drive share notification is ignored (the Drive step reads the file)", ei.analyze(msg("x shared a spreadsheet with you", sender="Someone <drive-shares-dm-noreply@google.com>", auth=None, mid="<n1@google.com>"), CFGE, NOW).status == "ignore")

# --- updates supersede, re-forwards are no-ops, other threads are untouched
dbe = FS.FakeSB()
ste = ei.SupabaseStore(sb=dbe, embed_upsert=FS.fake_embed_upsert(CH), out=lambda *_: None)
V1 = PRACTICE
V2 = PRACTICE.replace("Tuesdays and Thursdays 4:00-5:30 PM at Field 2", "Tuesdays and Thursdays 4:30-6:00 PM at Field 3").replace("Mon, Nov 9, 2026 at 5:02 PM", "Thu, Nov 12, 2026 at 8:00 AM")
rr1 = ei.handle(msg(V1, mid="<a@example.org>"), CFGE, ste, False, NOW)
rows1 = {r_["chunk_id"] for r_ in dbe.tables[CH]}
rr2 = ei.handle(msg(V1, mid="<b@example.org>"), CFGE, ste, False, NOW)
check("email: re-forwarding identical text is a no-op (duplicate)", rr1.status == "indexed" and rr2.status == "duplicate" and {r_["chunk_id"] for r_ in dbe.tables[CH]} == rows1)
rr3 = ei.handle(msg(V2, mid="<c@example.org>"), CFGE, ste, False, NOW)
sup = [r_ for r_ in dbe.tables[CH] if r_["metadata"].get("superseded")]
live = [r_ for r_ in dbe.tables[CH] if not r_["metadata"].get("superseded")]
check("email: an UPDATED announcement (same subject + original sender, corrected times, new forward) supersedes the older version", rr3.status == "indexed" and rr1.thread_key == rr3.thread_key and rr1.content_key != rr3.content_key
      and sup and all(r_["metadata"]["content_key"] == rr1.content_key and r_["metadata"]["superseded_by"] == rr3.content_key for r_ in sup) and all(r_["metadata"]["content_key"] == rr3.content_key for r_ in live), (len(sup), len(live), rr3.reason))
check("email: after the update only the new times are current ('4:30-6:00 PM'); the old ones live only in superseded rows", any("4:30-6:00 PM" in r_["content"] for r_ in live) and all("4:00-5:30 PM" not in r_["content"] for r_ in live), [r_["chunk_id"] for r_ in live])
rr4 = ei.handle(msg(V1, mid="<d@example.org>"), CFGE, ste, False, NOW)
check("email: a stale re-forward of the OLD text later stays a duplicate and does not resurrect it", rr4.status == "duplicate" and all(r_["metadata"].get("superseded") for r_ in dbe.tables[CH] if r_["metadata"]["content_key"] == rr1.content_key))
other = ei.handle(msg(V1.replace("Coach T <coach@example.org>", "Other Coach <other@example.org>").replace("at Field 2", "at Field 4"), mid="<e@example.org>"), CFGE, ste, False, NOW)
check("email: same subject from a DIFFERENT original sender is another thread (nothing superseded)", other.status == "indexed" and other.thread_key != rr1.thread_key
      and not [r_ for r_ in dbe.tables[CH] if r_["metadata"]["content_key"] == other.content_key and r_["metadata"].get("superseded")], (other.status, other.reason, other.thread_key, rr1.thread_key))
dbo = FS.FakeSB()
sto = ei.SupabaseStore(sb=dbo, embed_upsert=FS.fake_embed_upsert(CH), out=lambda *_: None)
newer = ei.handle(msg(V2, mid="<n@example.org>"), CFGE, sto, False, NOW)
older = ei.handle(msg(V1, mid="<o@example.org>"), CFGE, sto, False, NOW)
check("email: an OLDER version that arrives after a newer one is the one flagged superseded", older.status == "indexed" and all(r_["metadata"].get("superseded") for r_ in dbo.tables[CH] if r_["metadata"]["content_key"] == older.content_key)
      and not any(r_["metadata"].get("superseded") for r_ in dbo.tables[CH] if r_["metadata"]["content_key"] == newer.content_key))
check("email: the thread key normalises Re:/Fwd: in the subject", ei.analyze(msg(V1, subject="RE: Fwd: Ultimate Frisbee practice and game schedule", mid="<z@example.org>"), CFGE, NOW).thread_key == rr1.thread_key)


class FakeIMAP:
    def __init__(self, messages): self.messages, self.flags = dict(messages), []
    def login(self, u, p): return "OK", []
    def select(self, f): return "OK", [b"1"]
    def close(self): pass
    def logout(self): pass

    def uid(self, cmd, *a):
        if cmd == "SEARCH":
            return "OK", [b" ".join(str(u).encode() for u in self.messages)]
        if cmd == "FETCH":
            return "OK", [(b"hdr", self.messages[int(a[0])]), b")"]
        if cmd == "STORE":
            self.flags.append((int(a[0]), a[1], a[2]))
            return "OK", []


dbr = FS.FakeSB()
str_ = ei.SupabaseStore(sb=dbr, embed_upsert=FS.fake_embed_upsert(CH), out=lambda *_: None)
bad = b"\x00\xff garbage that is not an email at all"
imap1 = FakeIMAP({1: msg(PRACTICE, mid="<ok1@example.org>"), 2: bad, 3: msg(LINES, subject="Soccer", mid="<ok2@example.org>")})
lines_out = []
code = ei.run(CFGE, str_, imap=imap1, out=lines_out.append)
st_ = [x["status"] for x in dbr.tables["email_ingest_log"]]
check("email run: a malformed message is logged and skipped, the others are indexed, the run exits 0", code == 0 and st_.count("indexed") == 2 and len(st_) == 3 and not any("garbage" in repr(x) for x in dbr.tables["email_ingest_log"]), (code, st_, lines_out))
check("email run: logs and stdout hold no bodies or addresses", not any("@" in x for x in lines_out) and all("@" not in repr(l) for l in dbr.tables["email_ingest_log"]))
for _ in range(3):
    imap1 = FakeIMAP({2: bad})
    c2 = ei.run(CFGE, str_, imap=imap1, out=lambda *_: None)
check("email run: after 3 tries the malformed message is marked read so it is not retried forever (and the run never failed)", c2 == 0 and any(u == 2 for u, _, f in imap1.flags))
dbs = FS.FakeSB()
code = ei.run(CFGE, ei.SupabaseStore(sb=dbs, embed_upsert=boom, out=lambda *_: None), imap=FakeIMAP({1: msg(PRACTICE)}), out=lambda *_: None)
check("email run: storage/embedding down is systemic -> exit 1 (left unread)", code == 1)

# =============================================================================================================== prune safety
check("freshness: share rows are recognised by id prefix, source and file hash; email rows are not share rows", freshness.is_share_row("share-abc123abc123-s0t0-000") and freshness.is_share_row("x", "share_doc") and freshness.is_share_row("x", None, {"share_file_hash": "a"})
      and not freshness.is_share_row("email-aaaa-000") and freshness.is_ingested_row("email-aaaa-000") and freshness.is_ingested_row("share-aaaa-000") and not freshness.is_ingested_row("doc-1"))
tmp = Path(tempfile.mkdtemp())
(tmp / "chunks.json").write_text(json.dumps([{"chunk_id": "plan-0001", "content": "Title: t\n\nplanned", "source_file": "https://x/plan",
                                              "metadata": {"source_url": "https://x/plan", "title": "t", "source": "public_site", "content_hash": "h1", "source_date": "2026-10-01",
                                                           "school_year": "2026-27", "stale": False, "indexed_at": "2026-10-01"}}]))
index.config.CHUNKS_FILE = tmp / "chunks.json"
dbp = FS.FakeSB()
share_rows = [dict(r_) for r_ in db.tables[CH] if r_["chunk_id"].startswith("share-")]
dbp.tables[CH] = share_rows + [
    {"chunk_id": "old-0001", "content": "gone", "metadata": {"source_url": "https://x/old", "source": "public_site", "content_hash": "o"}},
    {"chunk_id": "cal-1", "content": "gone", "metadata": {"source_url": "https://x/c", "source": "calendar", "content_hash": "o"}},
    {"chunk_id": "legacy-1", "content": "gone", "metadata": {"source": "public_site"}},
    {"chunk_id": "wb-1", "content": "x", "metadata": {"source_url": "workbook://a", "source": "school_courses", "content_hash": "o"}},
    {"chunk_id": "share-aaaaaaaaaaaa-s0t0-000", "content": "x", "metadata": {"source_url": "share://aaaaaaaaaaaa", "source": "share_sheet", "content_hash": "z", "share_file_hash": "aaaaaaaaaaaa"}},
    {"chunk_id": "share-bbbbbbbbbbbb-eabcd1234", "content": "x", "metadata": {"source_url": "share://bbbbbbbbbbbb", "source": "calendar_sheet", "content_hash": "z", "event_date": "2026-11-20", "share_file_hash": "bbbbbbbbbbbb"}}]
saved_up = index._embed_and_upsert
index._embed_and_upsert = lambda sb, b: len(b)
index.client = lambda: dbp
stats = index.run(prune=True, prune_legacy=True, dry_run=True, log=lambda *_: None)
check("prune plan counts only non-share rows (stale: old, cal, wb; legacy: 1)", stats["legacy_in_db"] == 1 and stats["stale_in_db"] == 3, stats)
index.run(prune=True, prune_legacy=True, dry_run=False, log=lambda *_: None)
left = {r_["chunk_id"] for r_ in dbp.tables[CH]}
check("share rows (text and event records) survive --prune --prune-legacy; stale non-share rows are pruned", {r_["chunk_id"] for r_ in share_rows} <= left and "share-aaaaaaaaaaaa-s0t0-000" in left and "share-bbbbbbbbbbbb-eabcd1234" in left and "old-0001" not in left, left)
check("no share row was flagged superseded by the pipeline", not any((r_.get("metadata") or {}).get("superseded") for r_ in dbp.tables[CH] if r_["chunk_id"].startswith("share-")))
dd = FS.FakeSB()
dd.tables[CH] = [{"chunk_id": "share-x-000", "metadata": {}}, {"chunk_id": "a", "metadata": {}}]
index.delete_ids(dd, ["share-x-000", "a"], lambda *_: None)
check("index.delete_ids refuses share ids even if asked", dd.deleted == ["a"], dd.deleted)
dm = FS.FakeSB()
dm.tables[CH] = [{"chunk_id": "share-x-000", "metadata": {"a": 1}}, {"chunk_id": "wb-2", "metadata": {"a": 1}}]
index.mark_superseded(dm, ["share-x-000", "wb-2"], lambda *_: None)
check("index.mark_superseded never flags a share row", not dm.tables[CH][0]["metadata"].get("superseded") and dm.tables[CH][1]["metadata"].get("superseded"))
try:
    si.replace_file(si.ShareStore(sb=FS.FakeSB(), embed_upsert=lambda *a: 0, out=lambda *_: None), "aaaaaaaaaaaa", [{"chunk_id": "doc-1", "content": "", "metadata": {"content_hash": "h"}}])
    check("replace_file refuses a chunk id outside the file's prefix", False)
except ValueError:
    check("replace_file refuses a chunk id outside the file's prefix", True)
dd2 = FS.FakeSB()
st_del = si.ShareStore(sb=dd2, embed_upsert=lambda *a: 0, out=lambda *_: None)
dd2.tables[CH] = [{"chunk_id": "share-aaaaaaaaaaaa-x", "metadata": {}}, {"chunk_id": "share-bbbbbbbbbbbb-x", "metadata": {}}, {"chunk_id": "email-x", "metadata": {}}]
st_del.delete_ids(["share-aaaaaaaaaaaa-x", "share-bbbbbbbbbbbb-x", "email-x"], "aaaaaaaaaaaa")
check("ShareStore.delete_ids can only delete ids under the given file's own prefix", dd2.deleted == ["share-aaaaaaaaaaaa-x"], dd2.deleted)


# =============================================================================================================== retrieval: labels, ordering, precedence, 'next game'
def row(cid, title, body_, sd, source="share_sheet", stype="sheet", sim=0.8, **m):
    meta = {"title": title, "source": source, "source_type": stype, "source_url": "share://x", "indexed_at": "2026-11-01", "source_date": sd, "school_year": "2026-27", "stale": False, **m}
    return {"chunk_id": cid, "content": f"Title: {title}\nSource: shared Google Sheet \"{title}\", updated {sd}\n\n{body_}", "metadata": meta, "similarity": sim}


R = rag.Retriever(lambda: None)
new_s, old_s = row("share-aaaaaaaaaaaa-s0t0-000", "Varsity schedule", "Practice is at 4:00 PM", "2026-11-10"), row("share-bbbbbbbbbbbb-s0t0-000", "Varsity schedule", "Practice is at 3:00 PM", "2026-09-01")
ranked = R.rerank("when is varsity practice", [old_s, new_s], TODAY)
check("retrieval: a newer shared file outranks an older one on the same topic (newest wins)", ranked[0]["chunk_id"] == new_s["chunk_id"], [r_["chunk_id"] for r_ in ranked])
ctx, sources = rag.build_context({"events": [], "intent": None, "docs": [new_s, row("share-cccccccccccc-d-000", "Frisbee season", "Practice Tue/Thu", "2026-11-09", source="share_doc", stype="doc")], "pinned": [], "entities": []})
check("retrieval: context labels shared files '[Shared sheet: <title>, updated Nov 10, 2026]' / '[Shared doc: ...]'", "[Shared sheet: Varsity schedule, updated Nov 10, 2026]" in ctx and "[Shared doc: Frisbee season, updated Nov 9, 2026]" in ctx, ctx[:400])
check("retrieval: sources carry the title only: no owner, address or link to the private file", all("@" not in repr(s) and not s.get("url") for s in sources), sources)
email_row = row("email-1-000", "Early dismissal", "x", "2026-10-27", source="email", stype="email")
ctx2, _ = rag.build_context({"events": [], "intent": None, "docs": [email_row], "pinned": [], "entities": []})
check("retrieval: the school-email label is unchanged", "[School email, sent Oct 27, 2026]" in ctx2)
import query_server
check("prompt: the freshness/source rules cover shared sheets and docs (newest wins, name the file and its update date, no owner/address)", "[Shared sheet:" in query_server.SYSTEM_PERSONA and "updated Nov 10" in query_server.SYSTEM_PERSONA
      and "never an owner or address" in query_server.SYSTEM_PERSONA)


def ev(cid, summary, day, kind="game", src_date="2026-11-01", modified=None, superseded=False):
    m = {"source": "calendar_sheet", "event_date": day, "summary": summary, "kind": kind, "source_date": src_date, "end_date": day}
    if modified:
        m["modified_time"] = modified
    if superseded:
        m["superseded"] = True
    return {"chunk_id": cid, "content": "x", "metadata": m}


live = ev("sheet-111111111111", "US Boys Basketball Varsity - Game Menlo - Home", "2026-11-20", src_date="2026-11-12")
shared_new = ev("share-aaaaaaaaaaaa-e11111111", "US Boys Basketball Varsity - Game Menlo - Home", "2026-11-20", src_date="2026-11-12", modified="2026-11-12T18:00:00Z")
shared_old = ev("share-bbbbbbbbbbbb-e22222222", "Varsity Boys Basketball 26-27 - Game Menlo", "2026-11-20", src_date="2026-11-05", modified="2026-11-05T18:00:00Z")
other_game = ev("sheet-222222222222", "US Boys Basketball Varsity - Game Priory - Away", "2026-11-22", src_date="2026-11-12")
res = event_precedence.dedupe_events([live, shared_new, shared_old, other_game])
check("precedence: same team/date/opponent from the public tab and shared sheets -> ONE row; on a same-day tie the shared sheet wins", [r_["chunk_id"] for r_ in res] == ["share-aaaaaaaaaaaa-e11111111", "sheet-222222222222"], [r_["chunk_id"] for r_ in res])
res2 = event_precedence.dedupe_events([ev("sheet-111111111111", "US Boys Basketball Varsity - Game Menlo - Home", "2026-11-20", src_date="2026-11-12"), shared_old])
check("precedence: a shared sheet last modified BEFORE the public tab was last downloaded loses to the tab", [r_["chunk_id"] for r_ in res2] == ["sheet-111111111111"], res2)
res3 = event_precedence.dedupe_events([ev("share-aaaaaaaaaaaa-e1", "US Boys Basketball Varsity - Game Menlo - Home", "2026-11-20", modified="2026-11-10T10:00:00Z", src_date="2026-11-10"),
                                       ev("email-aaaaaaaaaaaaaaaa-e1", "US Boys Basketball Varsity - Game Menlo", "2026-11-20", src_date="2026-11-11")])
check("precedence: an email sent after the shared sheet's last edit wins", [r_["chunk_id"] for r_ in res3] == ["email-aaaaaaaaaaaaaaaa-e1"])
check("precedence: different days, opponents or teams are never merged; superseded rows never show", len(event_precedence.dedupe_events([live, ev("sheet-3", "US Girls Basketball Varsity - Game Menlo - Home", "2026-11-20"), ev("sheet-4", "US Boys Basketball Varsity - Game Menlo - Home", "2026-11-21")])) == 3
      and not event_precedence.dedupe_events([ev("share-x", "A - Game B", "2026-11-20", superseded=True)]))
check("precedence: calendar-feed rows (source calendar) are never touched", len(event_precedence.dedupe_events([{"chunk_id": "cal-1", "metadata": {"source": "calendar", "event_date": "2026-11-20", "summary": "Thanksgiving"}}] * 2)) == 2)

dbq = FS.FakeSB()
dbq.tables[CH] = [dict(c) for c in A1.chunks if c["metadata"].get("source") == "calendar_sheet"] + [dict(c) for c in r.chunks if c["metadata"].get("source") == "calendar_sheet"]
live_row = {"chunk_id": "sheet-live000001", "content": "x", "metadata": {"source": "calendar_sheet", "summary": "US Boys Soccer Varsity - Game Menlo - Home", "event_date": "2026-11-16", "end_date": "2026-11-16", "source_date": "2026-11-01",
                                                                       "when": "Monday, November 16, 2026", "time": "6:00 PM", "category": "athletics", "kind": "game", "home_away": "home", "start_time": "18:00", "location": "", "all_day": False}}
dbq.tables[CH].append(live_row)
Rq = rag.Retriever(lambda: dbq)
q1 = "when is the next varsity game"
it1 = intent_mod.parse_intent(q1, TODAY)
evs1 = Rq.calendar_events(it1)
chosen = rag.select_events(evs1, it1, q1)
check("next game: 'when is the next varsity game' returns the shared-sheet games (the real rag lookup), soonest first", chosen and chosen[0]["metadata"]["event_date"] == "2026-11-16" and chosen[0]["metadata"]["kind"] == "game"
      and all(e["metadata"]["kind"] == "game" for e in chosen), [(e["metadata"]["event_date"], e["metadata"]["summary"]) for e in chosen])
check("next game: the same game listed by the public tab and the shared sheet is shown ONCE (precedence by last-modified)", sum(1 for e in evs1 if e["metadata"]["event_date"] == "2026-11-16" and "Menlo" in e["metadata"]["summary"]) == 1
      and [e["chunk_id"] for e in evs1 if e["metadata"]["event_date"] == "2026-11-16"][0].startswith("share-"), [(e["chunk_id"], e["metadata"]["summary"]) for e in evs1])
cx, _ = rag.build_context({"events": evs1, "intent": it1, "docs": [], "pinned": [], "entities": []}, query=q1)
check("next game: the calendar block in the model's context lists date, time, opponent and location from the shared sheet", "Monday, November 16, 2026" in cx and "Menlo" in cx and "3:00 PM" in cx, cx[:500])
q2 = "when does Ultimate Frisbee practice"
it2 = intent_mod.parse_intent(q2, TODAY)
evs2 = Rq.calendar_events(it2)
check("practice: 'when does Ultimate Frisbee practice' finds the practice dates parsed from the email", bool(evs2) and all(e["metadata"]["kind"] == "practice" for e in evs2) and {e["metadata"]["event_date"] for e in evs2} >= {"2026-11-14", "2026-11-21"}
      and "turf field" in rag.practice_overview(evs2), rag.practice_overview(evs2) if evs2 else "no events")
evs3 = Rq.calendar_events(intent_mod.parse_intent("when is the next Ultimate Frisbee game", TODAY))
check("next game (email): 'next Ultimate Frisbee game' -> Menlo on Sat Nov 21 from the email's game table", bool(evs3) and evs3[0]["metadata"]["kind"] == "game" and "Menlo" in evs3[0]["metadata"]["summary"] and evs3[0]["metadata"]["event_date"] == "2026-11-21")
dbq.tables[CH] = [x for x in dbq.tables[CH] if not x["chunk_id"].startswith("share-")]
check("next game: with the shared file removed/unshared the public tab row answers instead (no leftovers)", rag.Retriever(lambda: dbq).calendar_events(it1)[0]["chunk_id"] == "sheet-live000001")
dbq.tables[CH] = [dict(c, metadata={**c["metadata"], "superseded": True}) for c in r.chunks if c["metadata"].get("source") == "calendar_sheet"]
check("superseded email events (an updated announcement) never reach the calendar block", not rag.Retriever(lambda: dbq).calendar_events(it2))

# =============================================================================================================== audit, CLI, workflow
import audit_data


class Args:
    offline = False
    db_json = None
    today = date(2026, 11, 12)


dump = tmp / "db.json"
good_rows = [{"chunk_id": c["chunk_id"], "content": c["content"], "metadata": c["metadata"]} for c in A1.chunks]
dump.write_text(json.dumps(good_rows + [{"chunk_id": "x-1", "content": "c", "metadata": {"source": "public_site", "source_url": "u"}}]))
Args.db_json = str(dump)
S = audit_data.Sources(Args)
Aud = audit_data.Audit({})
audit_data.audit_share(Aud, S)
check("audit: clean share rows pass the share groups and are kept out of S.db", not any(d["bad"] for d in Aud.g.values()) and all(not r_["chunk_id"].startswith("share-") for r_ in S.db), {k: v["bad"][:2] for k, v in Aud.g.items() if v["bad"]})
bad_row = dict(good_rows[0], content=good_rows[0]["content"] + "\nCall 415-555-0123", metadata={**good_rows[0]["metadata"], "source_url": "https://docs.google.com/spreadsheets/d/secret"})
dump.write_text(json.dumps([bad_row]))
S = audit_data.Sources(Args)
Aud = audit_data.Audit({})
audit_data.audit_share(Aud, S)
check("audit: a share row with a private phone or a link to the file is flagged", Aud.g["share.privacy"]["bad"] and Aud.g["share.rows"]["bad"])
env_clean = {"PATH": "/usr/bin:/bin"}
script = str(HERE / "scripts" / "email_ingest.py")
p = subprocess.run([sys.executable, script, "--only", "google"], capture_output=True, text=True, env=env_clean, cwd=str(tmp))
check("CLI: without the Google secrets it says 'Google ingestion not configured' and exits 0", p.returncode == 0 and "Google ingestion not configured" in p.stdout, (p.returncode, p.stdout, p.stderr[-300:]))
p = subprocess.run([sys.executable, script, "--once"], capture_output=True, text=True, env=env_clean, cwd=str(tmp))
check("CLI: with nothing configured both parts say so and exit 0 (the email part is independent)", p.returncode == 0 and "not configured" in p.stdout and "Google ingestion not configured" in p.stdout, p.stdout)
sj = tmp / "book.json"
sj.write_text(json.dumps(book_complex))
p = subprocess.run([sys.executable, script, "--sheet-json", str(sj)], capture_output=True, text=True, env=env_clean, cwd=str(tmp))
check("CLI: --sheet-json analyses a saved spreadsheets.get offline", p.returncode == 0 and "indexed" in p.stdout and "events=" in p.stdout, p.stdout + p.stderr[-300:])
wf = (HERE / ".github" / "workflows" / "email-ingest.yml").read_text()
check("workflow: Google secrets reach only the Drive step as env; timeout and concurrency kept; secrets never echoed", "GOOGLE_OAUTH_REFRESH_TOKEN: ${{ secrets.GOOGLE_OAUTH_REFRESH_TOKEN }}" in wf and wf.count("GOOGLE_OAUTH_CLIENT_ID:") == 1 and "timeout-minutes: 5" in wf
      and "cancel-in-progress: false" in wf and "run: echo" not in wf and "echo $" not in wf and "--only google" in wf and "--only email" in wf and 'cron: "*/30 * * * *"' in wf and "workflow_dispatch" in wf)
check("CI runs this test file", "python test_share_ingest.py" in (HERE / ".github" / "workflows" / "ci.yml").read_text())


# --- a repeated "Home schedule" table on the same tab lags behind the main table: its near-duplicate game must not become a second game
DUP_CSV = """Date,Home Team,Away Team,Time,Location
Wed 11/18/26,Nueva,CSD Fremont,7:00 PM,Nueva Gym
Thu 11/19/26,Nueva,Design Tech,7:00 PM,Nueva Gym

Date,Home Team,Away Team,Time,Location
Tue 11/17/26,Nueva,CSD Fremont,7:00 PM,Nueva Gym
Thu 11/19/26,Nueva,Design Tech,7:00 PM,Nueva Gym
Tue 12/01/26,Nueva,Menlo,7:00 PM,Nueva Gym
"""
ad = gsheets.analyze_workbook(F.book(F.csv_tab(DUP_CSV, "Varsity"), title="US Boys Basketball Varsity"), M("US Boys Basketball Varsity"), NOW, event_window=WIDE)
dg = sorted((c["metadata"]["event_date"], c["metadata"]["summary"]) for c in ad.chunks if c["metadata"].get("source") == "calendar_sheet" and c["metadata"].get("kind") == "game")
check("repeated home table: a near-duplicate game (Nov 17 vs the main table's Nov 18) is dropped, an exact duplicate collapses, a genuinely new home game is kept",
      [d for d, _ in dg] == ["2026-11-18", "2026-11-19", "2026-12-01"], dg)

index._embed_and_upsert = saved_up
print(f"\n{'all passed' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
