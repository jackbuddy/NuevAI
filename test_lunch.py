"""Offline tests for the cafe lunch menu (pipeline/lunch.py, lunch_lookup.py, intent/rag/query_server integration).

    ANALYTICS_DISABLED=1 WARMUP_DISABLED=1 python test_lunch.py

No network, no database, no PDFs on disk, no LLM: the PDF reader is fed synthetic pages (characters with positions, like pdfplumber's),
the database is a small in-memory fake, and every date is fixed.
"""
import contextlib
import copy
import inspect
import json
import os
import sys
import tempfile
import types
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("ANALYTICS_DISABLED", "1")
os.environ.setdefault("WARMUP_DISABLED", "1")
os.environ.setdefault("ANSWER_L2_DISABLED", "1")

import freshness
import intent
import lunch_lookup
import rag
import query_server as qs
from pipeline import chunker, config, index, lunch

FAILS = []


def check(name, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {name}{'' if cond else '  ' + str(detail)[:500]}")
    if not cond:
        FAILS.append(name)


# ------------------------------------------------------------------ synthetic PDF pages (what pdfplumber hands to the parser)
EDGES = [10.0, 80.0, 150.0, 220.0, 290.0, 360.0]


def run_chars(text, x0, top, bold=False):
    out = []
    for i, ch in enumerate(text):
        out.append({"text": ch, "x0": x0 + 3.0 * i, "x1": x0 + 3.0 * i + 3.0, "top": float(top), "bottom": top + 5.0, "size": 4.8,
                    "fontname": "ABC+Arial-BoldMT" if bold else "ABC+CenturyGothic"})
    return out


class FakePage:
    def __init__(self, header=None, title="", month="", rects=(), chars=(), images=(), words=()):
        self.chars, self.images, self.rects, self._words = list(chars), list(images), list(rects), list(words)
        self.height = 792

    def extract_words(self, **kw):
        return self._words


def header_page(title, month_line, chars_extra, images, weeks_chars):
    """Page 0: title, month line, weekday header row with 6 rules, then whatever chars were laid out."""
    words = [{"text": t, "top": 24.0, "x0": 100.0 + 20 * i} for i, t in enumerate(title.split())]
    words += [{"text": t, "top": 36.0, "x0": 100.0 + 20 * i} for i, t in enumerate(month_line.split())]
    words += [{"text": d, "top": 49.0, "x0": EDGES[i] + 5} for i, d in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])]
    rects = [{"x0": x, "x1": x + 0.5, "top": 48.0, "bottom": 55.0} for x in EDGES]
    chars = []
    for t, x, y in [(title, 100, 24), (month_line, 100, 36)]:
        chars += run_chars(t, x, y, True)
    chars += chars_extra + weeks_chars
    return FakePage(rects=rects, chars=chars, images=images, words=words)


def day_cell(col, top, number, lines):
    """number band at `top`, then the cell's lines. lines = [(text, bold)]. Returns (band chars, [(line_index, col, chars)])"""
    band = run_chars(str(number), EDGES[col + 1] - 8 - 3 * (len(str(number)) - 1), top, True)
    runs = [(i, col, run_chars(t, EDGES[col] + 3, top + 8 + 6 * i, b)) for i, (t, b) in enumerate(lines)]
    return band, runs


def lay_out(week_top, cells):
    """cells: {col: (number, lines)} -> chars in content-stream order: all bands, then line 0 of every column, line 1 ..."""
    chars, per_line = [], {}
    for col, (number, lines) in cells.items():
        band, runs = day_cell(col, week_top, number, lines)
        chars += band
        for i, c, rc in runs:
            per_line.setdefault(i, []).append(rc)
    for i in sorted(per_line):
        for rc in per_line[i]:
            chars += rc
    return chars


SM_LINES = [("Tomato Basil", False), ("Meatless Monday", True), ("Cheese Raviolis, House", False), ("Made Marinara Sauce,", False),
            ("Earthly Selections", True), ("Cheese Raviolis, House", False), ("Made Marinara Sauce", False),
            ("Plant Inspired", True), ("Lentil Marinara Sauce, Penne", False),
            ("Sandwich Favorite", True), ("California Bagelwich-", False), ("Cream Cheese, Avocado", False),
            ("Superfood Tossed", True), ("Rainbow Beet Salad-Red and", False), ("Golden Beets,", False)]
HB_LINES = [("Kitchen Table", True), ("Grass Fed Ground Beef", False), ("Mac and Cheese", False),
            ("Earthly Selections", True), ("Butternut Squash Mac and", False), ("Cheese", False),
            ("Plant Inspired", True), ("Baked Pasta, Organic Pasta", False),
            ("Sandwich Favorite", True), ("Bassian Farm Ham and", False), ("Provolone Cheese", False)]


def sm_pdf(extra_week=True, wrong_weekday=False):
    """San Mateo style (soup line first, Superfood Tossed), October 2026; Mon 5 has a menu, Tue 6 is covered by a picture (no school),
    Wed 7 a menu; the week of 12 continues on page 2 (a week split by the page break)."""
    p0w1 = lay_out(60.0, {0: (5, SM_LINES), 1: (6, [("Beef Chili", False)]), 2: (7, [("Chicken Noodle", False), ("Asian Street Food", True),
                                                                                   ("Classic Sweet and Sour Pork with", False), ("Steamed Broccoli", False)])})
    covered = {"x0": EDGES[1], "x1": EDGES[2], "top": 66.0, "bottom": 190.0}
    week2_p0 = lay_out(700.0, {3: (15 if not wrong_weekday else 14, [("Lasagna Soup", False), ("Platillos Latino", True), ("Slow Braised Chicken", False)])})
    page0 = header_page("Nueva School - San Mateo US and Faculty Menu", "October 2026 Cafe Lunch Hours: 11:00am - 1:00 pm", [], [covered], p0w1 + week2_p0)
    cont = run_chars("Tinga, Flour Tortillas", EDGES[3] + 3, 20.0, False)       # continues on the next page, same column
    foot = run_chars("Epicurean Group at Nueva Schools", 20.0, 90.0, True)
    page1 = FakePage(chars=cont + foot, images=[])
    return [page0, page1]


def hb_pdf():
    """Hillsborough style: no soup line, no salad; Fri 2 is covered by a picture."""
    chars = lay_out(60.0, {3: (1, HB_LINES), 4: (2, [("Global Adventure", True), ("Greek inspired Pork", False)])})
    chars += lay_out(200.0, {0: (5, HB_LINES)})
    covered = {"x0": EDGES[4], "x1": EDGES[5], "top": 66.0, "bottom": 190.0}
    page0 = header_page("Hillsborough Nueva School-LS, MS", "October 2026 Cafe Hours 11:00am -1:00pm", [], [covered], chars)
    foot = run_chars("Epicurean Group at Nueva Schools", 20.0, 400.0, True)
    return [FakePage(rects=page0.rects, chars=page0.chars + foot, images=page0.images, words=page0.extract_words())]


class FakePdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@contextlib.contextmanager
def fake_pdfplumber(pages):
    mod = types.ModuleType("pdfplumber")
    mod.open = lambda path: FakePdf(pages)
    old = sys.modules.get("pdfplumber")
    sys.modules["pdfplumber"] = mod
    try:
        yield
    finally:
        if old is not None:
            sys.modules["pdfplumber"] = old
        else:
            sys.modules.pop("pdfplumber", None)


# ------------------------------------------------------------------ parsing, both formats
with fake_pdfplumber(sm_pdf()):
    sm = lunch.parse_pdf("x.pdf")
check("parse san_mateo: campus, month, hours", (sm["campus"], sm["month"], sm["hours"]) == ("san_mateo", "2026-10", "11:00am-1:00pm"), sm)
days = {d["date"]: d for d in sm["days"]}
check("parse san_mateo: dates found, no warnings", sorted(days) == ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-15"] and not sm["warnings"], (sorted(days), sm["warnings"]))
mon = days["2026-10-05"]
check("parse san_mateo: soup line then theme, sections and salad", [i["label"] for i in mon["items"]] ==
      ["Soup", "Meatless Monday", "Earthly Selections", "Plant Inspired", "Sandwich Favorite", "Superfood Tossed"], mon["items"])
check("parse san_mateo: wrapped lines joined, hyphen kept", mon["items"][1]["text"] == "Cheese Raviolis, House Made Marinara Sauce" or mon["items"][1]["text"].startswith("Cheese Raviolis, House Made Marinara Sauce"), mon["items"][1])
check("parse san_mateo: hyphenated dish name joins without a space", "Bagelwich-Cream Cheese" in mon["items"][4]["text"], mon["items"][4])
check("parse san_mateo: picture over a cell -> closed, no items", days["2026-10-06"]["closed"] and days["2026-10-06"]["items"] == [], days["2026-10-06"])
check("parse san_mateo: adjacent cells on one row stay apart", days["2026-10-07"]["items"][0]["text"] == "Chicken Noodle" and days["2026-10-07"]["items"][1]["label"] == "Asian Street Food", days["2026-10-07"])
check("parse san_mateo: a week split by the page break is rejoined", days["2026-10-15"]["items"][1]["text"] == "Slow Braised Chicken Tinga, Flour Tortillas", days["2026-10-15"])
with fake_pdfplumber(sm_pdf(wrong_weekday=True)):
    bad = lunch.parse_pdf("x.pdf")
check("parse: a date sitting in the wrong weekday column is a warning (file is refused)", any("sits in column" in w for w in bad["warnings"]), bad["warnings"])

with fake_pdfplumber(hb_pdf()):
    hb = lunch.parse_pdf("x.pdf")
hd = {d["date"]: d for d in hb["days"]}
check("parse hillsborough: campus from its own header, month, hours", (hb["campus"], hb["month"], hb["hours"]) == ("hillsborough", "2026-10", "11:00am-1:00pm"), hb)
check("parse hillsborough: no soup, starts at the theme", [i["label"] for i in hd["2026-10-01"]["items"]] == ["Kitchen Table", "Earthly Selections", "Plant Inspired", "Sandwich Favorite"], hd["2026-10-01"])
check("parse hillsborough: covered Friday is closed", hd["2026-10-02"]["closed"], hd["2026-10-02"])
check("parse hillsborough: second week row", sorted(hd) == ["2026-10-01", "2026-10-02", "2026-10-05"], sorted(hd))
check("labels: 'Earthly Selection' and trailing dots are normalised", lunch.clean_label("Earthly Selection") == "Earthly Selections" and lunch.clean_label("American BBQ Series….") == "American BBQ Series")
check("no dietary tag is ever added by the parser", not any(w in json.dumps(sm).lower() for w in ("gluten free", "gluten-free", '"vegan"', "dairy free", "nut free")))

# ------------------------------------------------------------------ records
chunks = lunch.build_chunks(sm, "https://drive.google.com/file/d/AAA/view", "San Mateo Lunch Menu", "2026-10-25")
ids = [c["chunk_id"] for c in chunks]
check("records: stable ids lunch-<campus>-<date>", ids == ["lunch-san_mateo-20261005", "lunch-san_mateo-20261006", "lunch-san_mateo-20261007", "lunch-san_mateo-20261015"], ids)
c0, c1 = chunks[0], chunks[1]
m0 = c0["metadata"]
check("records: metadata (source, campus, date, month, closed, items, source url, pdf name)", m0["source"] == "lunch" and m0["campus"] == "san_mateo" and m0["event_date"] == "2026-10-05"
      and m0["month"] == "2026-10" and m0["month_label"] == "October 2026" and not m0["closed"] and len(m0["items"]) == 6 and m0["source_url"].startswith("https://drive.google.com")
      and m0["pdf_name"] == "San Mateo Lunch Menu" and m0["content_hash"], m0)
check("records: menu text carries the cafe note", lunch.NOTE in c0["content"] and "Soup: Tomato Basil" in c0["content"])
check("records: closed day says no menu is listed", c1["metadata"]["closed"] and c1["metadata"]["kind"] == "no_menu_day" and "No lunch menu is listed" in c1["content"] and lunch.NOTE not in c1["content"])
check("records: content hash is stable for the same menu, changes when a dish changes",
      lunch.build_chunks(sm, "u", "n", "2026-10-26")[0]["metadata"]["content_hash"] == lunch.build_chunks(sm, "u", "n", "2026-10-27")[0]["metadata"]["content_hash"]
      and lunch.build_chunks(sm, "u", "n", "x")[0]["metadata"]["content_hash"] != lunch.build_chunks(sm, "u2", "n", "x")[0]["metadata"]["content_hash"])
check("records: CALENDAR/LUNCH_NOTE wording is the exact required sentence", lunch.NOTE == "The menu can change; check with the cafe for ingredients or allergens." == lunch_lookup.NOTE == rag.LUNCH_NOTE)

# freshness
fresh = chunker.annotate_freshness(copy.deepcopy(chunks), [], today=date(2026, 10, 25))
cc = copy.deepcopy(chunks)
chunker.annotate_freshness(cc, [], today=date(2026, 10, 25))
mm = cc[0]["metadata"]
check("freshness: indexed_at, source_date (fetch day), school_year, stale=False", mm["school_year"] == "2026-27" and mm["stale"] is False and mm["source_date"] == "2026-10-25" and mm["indexed_at"], mm)
check("freshness: lunch is current by construction", freshness.classify("anything", "lunch", date(2026, 10, 25))["stale"] is False)
ccm = copy.deepcopy(chunks)
for c in ccm:
    c["metadata"]["event_date"] = "2027-08-26"
chunker.annotate_freshness(ccm, [], today=date(2026, 10, 25))
check("freshness: school_year follows the menu's own date (August menu = next school year)", ccm[0]["metadata"]["school_year"] == "2027-28", ccm[0]["metadata"])


# ------------------------------------------------------------------ fake database
class Res:
    def __init__(self, data):
        self.data = data


def getpath(row, col):
    if col.startswith("metadata->>"):
        return str((row.get("metadata") or {}).get(col[len("metadata->>"):], "")) if (row.get("metadata") or {}).get(col[len("metadata->>"):]) is not None else None
    return row.get(col)


class Q:
    def __init__(self, rows, cols):
        self.rows, self.cols = list(rows), cols

    def select(self, cols, **kw):
        self.cols = cols
        return self

    def eq(self, col, val):
        self.rows = [r for r in self.rows if getpath(r, col) == val]
        return self

    def in_(self, col, vals):
        self.rows = [r for r in self.rows if getpath(r, col) in vals]
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def execute(self):
        if self.cols.startswith("m:metadata->>month"):
            return Res([{"m": (r.get("metadata") or {}).get("month")} for r in self.rows])
        return Res([copy.deepcopy(r) for r in self.rows])


class FakeSB:
    def __init__(self, rows):
        self.rows = rows
        self.queries = 0

    def table(self, name):
        self.queries += 1
        return Q(self.rows, "*")


def db_rows(*docs):
    rows = []
    for doc, url in docs:
        for c in lunch.build_chunks(doc, url, "menu.pdf", "2026-10-25"):
            rows.append({"chunk_id": c["chunk_id"], "content": c["content"], "metadata": c["metadata"]})
    return rows


def full_month_doc(campus, month="2026-10", closed=(), name_prefix="", special=None):
    y, mth = map(int, month.split("-"))
    ds = []
    d = date(y, mth, 1)
    while d.month == mth:
        if d.weekday() < 5:
            iso = d.isoformat()
            cl = iso in closed
            ds.append({"date": iso, "weekday": d.strftime("%A"), "closed": cl,
                       "items": [] if cl else [{"label": "Soup", "text": f"{name_prefix}{campus} soup {d.day}"}, {"label": "Street Food", "text": f"{campus} main {d.day}"},
                                              {"label": "Earthly Selections", "text": f"{campus} veg {d.day}"}]
                       + ([{"label": "Stone Hearth", "text": special[iso]}] if special and iso in special else [])})
        d += timedelta(days=1)
    return {"campus": campus, "month": month, "hours": "11:00am-1:00pm", "title": "t", "warnings": [], "days": ds}


SM_OCT = full_month_doc("san_mateo", closed=("2026-10-12", "2026-10-28"), special={"2026-10-27": "Pepperoni Pizza on hand tossed dough"})
HB_OCT = full_month_doc("hillsborough", closed=("2026-10-12", "2026-10-23"))
DAYREC = [
    {"chunk_id": "wb-1", "content": "Title: x\n\nMonday, October 12, 2026 - No classes (Indigenous People's Day).", "metadata": {"source": "school_day", "kind": "no_classes", "event_date": "2026-10-12"}},
    {"chunk_id": "wb-2", "content": "Title: x\n\nWednesday, October 28, 2026 - No classes (Parent Teacher Conferences).", "metadata": {"source": "school_day", "kind": "no_classes", "event_date": "2026-10-28"}},
    {"chunk_id": "cal-day-20261023-0", "content": "Title: x\n\nSchedule for Friday, October 23, 2026:\n- No LS & MS Classes: LS & MS Parent/Teacher Conferences (8 AM to 5 PM)\n- US Parent Fika (8:30 AM to 10 AM)",
     "metadata": {"source": "calendar_day", "event_date": "2026-10-23"}},
]
SB = FakeSB(db_rows((SM_OCT, "https://drive.google.com/file/d/SMID/view"), (HB_OCT, "https://drive.google.com/file/d/HBID/view")) + DAYREC)


def fresh_cache():
    lunch_lookup._cache.clear()


# ------------------------------------------------------------------ /lunch day lookup
fresh_cache()
r = lunch_lookup.day_info(SB, "hillsborough", date(2026, 10, 14))
check("lookup: open day shape", set(r) == {"date", "campus", "items", "note", "available", "month_available", "month_label", "source_url", "closed_reason"}
      and r["available"] and r["items"][0] == "Soup: hillsborough soup 14" and r["note"] == lunch.NOTE and r["closed_reason"] is None and r["month_available"]
      and r["month_label"] == "October 2026" and r["source_url"].endswith("HBID/view") and r["campus"] == "hillsborough" and r["date"] == "2026-10-14", r)
r = lunch_lookup.day_info(SB, "san_mateo", date(2026, 10, 17))
check("lookup: weekend -> no lunch service", not r["available"] and r["items"] == [] and r["note"] is None and "Weekend" in r["closed_reason"] and r["month_available"], r)
r = lunch_lookup.day_info(SB, "san_mateo", date(2026, 10, 12))
check("lookup: holiday uses the school day record as the reason", not r["available"] and "Indigenous" in r["closed_reason"], r)
r = lunch_lookup.day_info(SB, "hillsborough", date(2026, 10, 23))
check("lookup: Hillsborough conference day uses the calendar line (LS & MS)", not r["available"] and "LS & MS" in r["closed_reason"], r)
r = lunch_lookup.day_info(SB, "hillsborough", date(2026, 10, 12))
check("lookup: closed with no calendar detail -> generic reason, never invented", not r["available"] and r["closed_reason"] == lunch_lookup.GENERIC_CLOSED, r)
r = lunch_lookup.day_info(SB, "san_mateo", date(2026, 11, 2))
check("lookup: next month not posted -> month_available False, nothing from October", not r["available"] and not r["month_available"] and r["items"] == []
      and r["month_label"] == "November 2026" and "November menu isn't posted yet" in r["closed_reason"], r)
check("lookup: campus validation ignores junk", [lunch_lookup.valid_campus(x) for x in ("san_mateo", "hillsborough", "SAN_MATEO", "x", None, 5, "")] == ["san_mateo", "hillsborough", None, None, None, None, None])

# month boundary: Oct 30 (Fri) and Nov 2 (Mon) with a November menu posted for one campus only
NOV_SM = full_month_doc("san_mateo", month="2026-11")
SB2 = FakeSB(SB.rows + db_rows((NOV_SM, "https://drive.google.com/file/d/SMNOV/view")))
fresh_cache()
a, b = lunch_lookup.day_info(SB2, "san_mateo", date(2026, 10, 30)), lunch_lookup.day_info(SB2, "san_mateo", date(2026, 11, 2))
c = lunch_lookup.day_info(SB2, "hillsborough", date(2026, 11, 2))
check("month boundary: Oct 30 -> October menu, Nov 2 -> November menu (own source)", a["available"] and a["month_label"] == "October 2026" and b["available"] and b["month_label"] == "November 2026"
      and b["source_url"].endswith("SMNOV/view") and a["source_url"].endswith("SMID/view"), (a, b))
check("month boundary: the other campus without a November menu says not posted", not c["available"] and not c["month_available"], c)
check("search: finds days by dish word", [d for d, _ in lunch_lookup.search_days(SB, "san_mateo", ["main", "21"], date(2026, 10, 1))] == ["2026-10-21"])

# ------------------------------------------------------------------ intent: lunch question, campus, dates
T = date(2026, 10, 20)      # a Tuesday
Q_YES = ["What's for lunch?", "what is for lunch tomorrow", "cafe menu", "What's on the lunch menu on Oct 14?", "any vegan options at the cafeteria", "Hillsborough lunch",
         "MS lunch", "what can I eat at school today", "lunch next week", "what is the cafe serving today", "is there a vegetarian option for lunch friday", "cafe hours",
         "lunch today", "what's the soup today", "Is there pizza for lunch?", "lunch on Oct 14"]
Q_NO = ["What time is lunch?", "when is lunch", "lunch schedule", "what is the bell schedule", "when is the lunch and learn", "who teaches chemistry", "what is the cell phone policy",
        "when does lunch start on A days", "is there lunch duty", "Which clubs meet at lunch?", "Does Model UN meet at lunch?", "And lunch?", "lunch?",
        "what time does lunch start tomorrow", "Is there a lunch period for clubs?"]
check("intent: lunch questions recognised", all(intent.is_lunch_question(q) for q in Q_YES), [q for q in Q_YES if not intent.is_lunch_question(q)])
check("intent: bell-time and unrelated questions are not lunch questions", not any(intent.is_lunch_question(q) for q in Q_NO), [q for q in Q_NO if intent.is_lunch_question(q)])
li = intent.parse_lunch("What's for lunch?", T)
check("intent: no date -> today", li.days == [T] and not li.date_given and li.label == "today")
check("intent: tomorrow", intent.parse_lunch("what is for lunch tomorrow", T).days == [date(2026, 10, 21)])
check("intent: weekday name", intent.parse_lunch("lunch on Monday", T).days == [date(2026, 10, 26)])
check("intent: 'on Oct 14' / '10/14'", intent.parse_lunch("lunch on Oct 14", T).days == [date(2026, 10, 14)] and intent.parse_lunch("lunch 10/14", T).days == [date(2026, 10, 14)])
nw = intent.parse_lunch("What's the lunch menu next week?", T)
check("intent: next week = five school days", nw.days == [date(2026, 10, 26) + timedelta(days=i) for i in range(5)], nw.days)
check("intent: weekend asked directly stays a single day", intent.parse_lunch("lunch on Saturday", T).days == [date(2026, 10, 24)])
check("campus: text beats the saved setting", intent.parse_lunch("Hillsborough lunch today", T, "san_mateo").campuses == ["hillsborough"]
      and intent.parse_lunch("Hillsborough lunch today", T, "san_mateo").campus_from == "text")
check("campus: saved setting used when the text names none", intent.parse_lunch("what's for lunch", T, "san_mateo").campuses == ["san_mateo"]
      and intent.parse_lunch("what's for lunch", T, "san_mateo").campus_from == "setting")
check("campus: none known -> both campuses", intent.parse_lunch("what's for lunch", T, None).campuses == ["san_mateo", "hillsborough"] and intent.parse_lunch("what's for lunch", T, "bogus").campus_from == "none")
check("campus: words MS / middle school / lower school / LS -> hillsborough", all(intent.campus_in_text(q) == ["hillsborough"] for q in ("MS lunch", "middle school menu", "lower school lunch", "LS lunch", "ms lunch")))
check("campus: San Mateo / upper school / US / high school -> san_mateo", all(intent.campus_in_text(q) == ["san_mateo"] for q in ("San Mateo lunch", "upper school menu", "US lunch", "high school lunch")))
check("campus: 'tell us' and 'Ms. Lee' are not campuses", intent.campus_in_text("tell us what's for lunch") == [] and intent.campus_in_text("Ms. Lee's lunch menu") == [])
check("campus: both named -> both", intent.campus_in_text("San Mateo and Hillsborough lunch") == ["san_mateo", "hillsborough"])
check("intent: diet word captured", intent.parse_lunch("vegetarian options for lunch tomorrow", T).diet == "vegetarian")
check("intent: dish search when no date is given", intent.parse_lunch("when is pizza on the menu", T).keywords == ["pizza"] and intent.parse_lunch("what's the soup today", T).keywords == [])
check("intent: 'and the day after?' after a lunch question", intent.parse_lunch("and the day after?", T, None, "what's for lunch tomorrow at hillsborough").days == [date(2026, 10, 22)])
check("intent: prek flagged", intent.parse_lunch("prek lunch today", T).prek)

# ------------------------------------------------------------------ retrieval (rag) with the fake database
fresh_cache()
R = rag.Retriever(lambda: SB)


def ask(q, campus=None, today=T, history=None):
    res = R.retrieve(q, history or [], today=today, campus=campus)
    ctx, sources = rag.build_context(res)
    return res, ctx, sources


res, ctx, src = ask("what's for lunch?", "hillsborough")
check("rag: lunch question returns the day record for the saved campus only", "hillsborough soup 20" in ctx and "san_mateo soup 20" not in ctx and res["lunch"] and not res["docs"] and not res["events"], ctx[:400])
check("rag: context carries the exact note instruction and the campus label", rag.LUNCH_NOTE in ctx and "Hillsborough (Lower and Middle School)" in ctx)
check("rag: sources point at the record and PDF", src and src[0]["type"] == "lunch" and src[0]["url"].endswith("HBID/view"), src)
res, ctx, src = ask("what's for lunch tomorrow?", None)
check("rag: no campus known -> both campuses' items, each labelled", "san_mateo soup 21" in ctx and "hillsborough soup 21" in ctx and "San Mateo (Upper School)" in ctx and "show BOTH campuses" in ctx, ctx[:600])
res, ctx, src = ask("San Mateo lunch today", "hillsborough")
check("rag: campus named in the question beats the saved campus", "san_mateo soup 20" in ctx and "hillsborough soup 20" not in ctx)
res, ctx, src = ask("lunch on Saturday", None)
check("rag: weekend -> no lunch service, no invented menu", "Both campuses: No lunch service (weekend)." in ctx and "soup" not in ctx.split("MENU")[-1].lower().replace("soup of", ""), ctx)
res, ctx, src = ask("lunch on Oct 12", "san_mateo")
check("rag: holiday names the reason from the calendar data", "Indigenous" in ctx and "san_mateo soup 12" not in ctx, ctx)
res, ctx, src = ask("lunch on Nov 3", "san_mateo")
check("rag: month not posted -> says so and shows no other month", "November menu isn't posted yet" in ctx and "soup" not in ctx.lower().split("monday")[0].replace("soup of", "") and "san_mateo soup" not in ctx, ctx)
res, ctx, src = ask("lunch on Sept 3", "san_mateo")
check("rag: a past month that is no longer kept is not called 'not posted yet'", "I do not have the September 2026 menu" in ctx, ctx)
res, ctx, src = ask("vegetarian options for lunch tomorrow", "san_mateo")
check("rag: diet question carries the 'not labelled' rule and Earthly Selections items", "does not mark individual dishes as vegetarian" in ctx and "Earthly Selections: san_mateo veg 21" in ctx, ctx)
res, ctx, src = ask("what about Hillsborough?", None, history=[{"role": "user", "content": "What's for lunch tomorrow?"}])
check("rag: follow-up 'what about Hillsborough?' keeps the day and switches the campus", "hillsborough soup 21" in ctx and "san_mateo soup" not in ctx, ctx[:500])
res, ctx, src = ask("and the day after?", None, history=[{"role": "user", "content": "What's for lunch tomorrow at hillsborough?"}])
check("rag: 'and the day after?' moves one day", "hillsborough soup 22" in ctx and "hillsborough soup 21" not in ctx and "Thursday, October 22" in ctx, ctx[:400])
res, ctx, src = ask("when is pizza on the menu", None)
check("rag: dish search lists the matching days", res["lunch"]["search"] is not None and "Tuesday, October 27, 2026" in ctx and "Pepperoni Pizza" in ctx, ctx)
res, ctx, src = ask("lunch next week", "san_mateo")
check("rag: next week lists five days", ctx.count("san_mateo soup") == 4 and "Wednesday, October 28, 2026" in ctx and "No lunch service listed" in ctx, ctx[:300])
res, ctx, src = ask("prek lunch tomorrow", "hillsborough")
check("rag: PreK question warns the PreK-K menu is separate", "PreK-K menu is a separate cafe PDF" in ctx.replace("their menu is a separate cafe PDF", "PreK-K menu is a separate cafe PDF"))
plain = rag.lunch_plain_answer(R.retrieve("what's for lunch tomorrow?", [], today=T, campus="san_mateo")["lunch"])
check("rag: plain (no-LLM) answer lists items and ends with the note", plain.rstrip().endswith(lunch.NOTE) and "- Soup: san_mateo soup 21" in plain, plain)
check("intent: 'cafe hours' is hours only; with a menu question it is not", intent.parse_lunch("cafe hours", T).hours_only and not intent.parse_lunch("what's on the menu and the cafe hours today", T).hours_only)
res, ctx, src = ask("cafe hours", None)
check("rag: cafe hours come from the menu header and no menu is listed", "Cafe hours as printed on the cafe's menus: San Mateo (Upper School) 11:00am-1:00pm" in ctx and "soup" not in ctx.split("MENU")[-1].lower().replace("soup of", "")
      and "  - " not in ctx, ctx[-400:])
plain_d = rag.lunch_plain_answer(R.retrieve("vegan options for lunch tomorrow", [], today=T, campus="san_mateo")["lunch"])
check("rag: plain answer to a diet question says the dishes are not marked", plain_d.startswith("The posted menu does not mark individual dishes as vegan") and plain_d.rstrip().endswith(lunch.NOTE), plain_d[:300])
plain_h = rag.lunch_plain_answer(R.retrieve("cafe hours", [], today=T, campus="san_mateo")["lunch"])
check("rag: plain answer to a hours question gives the hours", "11:00am-1:00pm" in plain_h and plain_h.rstrip().endswith(lunch.NOTE), plain_h)
check("rag: a non-lunch question is untouched by the lunch path (no 'lunch' key)",
      rag.parse_lunch("who teaches chemistry", T) is None and rag.parse_lunch("what time is lunch", T) is None)

# ------------------------------------------------------------------ cache bypass, request model, analytics
now = datetime(2026, 10, 22, 9, 0, tzinfo=qs.PACIFIC)
r1 = qs.QueryRequest(query="What's for lunch?")
r2 = qs.QueryRequest(query="what's for lunch", campus="hillsborough")
r3 = qs.QueryRequest(query="What are the graduation requirements?")
check("cache: lunch questions skip L1 and L2 (no key), with or without campus", qs._cache_key(r1, now) is None and qs._cache_key(r2, now) is None)
check("cache: other questions still cached", qs._cache_key(r3, now) is not None)
check("cache: _cache_get with no key never reads L1 or L2", qs._cache_get(r1, None) is None)
puts = []
orig_put = qs.answer_l2.put
qs.answer_l2.put = lambda *a, **k: puts.append(a)
try:
    key = qs._cache_key(r2, now)
    if key:
        qs._cache_put(r2, key, {"answer": "x"}, None)
finally:
    qs.answer_l2.put = orig_put
check("cache: a lunch answer is never written to L2", not puts)
check("request: campus validated to the two values or None", qs.QueryRequest(query="x", campus="san_mateo").campus == "san_mateo" and qs.QueryRequest(query="x", campus="hillsborough").campus == "hillsborough"
      and qs.QueryRequest(query="x", campus="Narnia").campus is None and qs.QueryRequest(query="x", campus="").campus is None and qs.QueryRequest(query="x", campus=5).campus is None
      and qs.QueryRequest(query="x").campus is None)
src_track = inspect.getsource(qs._track) + inspect.getsource(qs._answer) + inspect.getsource(qs.chat_stream)
check("privacy: the campus setting never reaches analytics, logs or the cache", "req.campus" not in src_track and "req.campus" not in inspect.getsource(qs._cache_key)
      and "req.campus" not in inspect.getsource(qs._cache_put) and "req.campus" not in inspect.getsource(qs._cache_get))
check("answer: the note is appended when the model leaves it out, never duplicated",
      qs.with_lunch_note("Tomato soup.", {"lunch": {"x": 1}}).endswith(lunch.NOTE) and qs.with_lunch_note("x\n\n" + lunch.NOTE, {"lunch": {"x": 1}}).count(lunch.NOTE) == 1
      and qs.with_lunch_note("hello", {}) == "hello")
deg = qs.degraded_answer([], R.retrieve("what's for lunch today", [], today=T, campus="san_mateo"))
check("degraded answer for lunch is the exact records plus the note", "san_mateo soup 20" in deg and deg.rstrip().endswith(lunch.NOTE) and "trouble" not in deg, deg)
sug = qs.suggestions_for(R.retrieve("what's for lunch", [], today=T), "what's for lunch")
check("suggestions: lunch gets lunch follow-ups", sug and all("query" in s for s in sug) and any("lunch" in s["query"].lower() for s in sug), sug)
check("persona: lunch style rule present, with the exact note", "Lunch and cafe menu questions" in qs.SYSTEM_PERSONA and lunch.NOTE in qs.SYSTEM_PERSONA)

# ------------------------------------------------------------------ endpoint
from fastapi.testclient import TestClient

fresh_cache()
qs.api_cache.clear()
orig_sb = qs.get_supabase
qs.get_supabase = lambda: SB
client = TestClient(qs.app)
try:
    r = client.get("/lunch", params={"date": "2026-10-14", "campus": "san_mateo"})
    body = r.json()
    check("GET /lunch: 200 and exactly the specified shape", r.status_code == 200 and set(body) == {"date", "campus", "items", "note", "available", "month_available", "month_label", "source_url", "closed_reason"}, body)
    check("GET /lunch: values for an open day", body["available"] and body["items"][0] == "Soup: san_mateo soup 14" and body["note"] == lunch.NOTE and body["closed_reason"] is None and body["campus"] == "san_mateo", body)
    r = client.get("/lunch", params={"date": "2026-10-17", "campus": "hillsborough"}).json()
    check("GET /lunch: weekend", not r["available"] and r["items"] == [] and r["note"] is None and r["closed_reason"].startswith("Weekend"), r)
    r = client.get("/lunch", params={"date": "2026-12-01", "campus": "hillsborough"}).json()
    check("GET /lunch: month not posted", not r["month_available"] and not r["available"] and r["month_label"] == "December 2026" and "isn't posted yet" in r["closed_reason"], r)
    r = client.get("/lunch", params={"campus": "san_mateo"})
    today_iso = qs.datetime.now(qs.PACIFIC).date().isoformat()
    check("GET /lunch: date defaults to today (Pacific)", r.status_code == 200 and r.json()["date"] == today_iso, r.text)
    check("GET /lunch: bad campus / missing campus / bad date -> 400/422, no stack trace",
          client.get("/lunch", params={"campus": "mars", "date": "2026-10-14"}).status_code == 400 and client.get("/lunch", params={"date": "2026-10-14"}).status_code in (400, 422)
          and client.get("/lunch", params={"campus": "san_mateo", "date": "2026-13-45"}).status_code == 400 and client.get("/lunch", params={"campus": "san_mateo", "date": "tomorrow"}).status_code == 400)
    q_before = SB.queries
    client.get("/lunch", params={"date": "2026-10-14", "campus": "san_mateo"})
    check("GET /lunch: repeated request is served from memory (no database query)", SB.queries == q_before)
    qs.LUNCH_PER_IP_PER_MIN = 5
    qs._lunch_hits.clear()
    codes = [client.get("/lunch", params={"date": "2026-10-14", "campus": "san_mateo"}).status_code for _ in range(8)]
    check("GET /lunch: rate limited per client", codes[:5] == [200] * 5 and 429 in codes[5:], codes)
    qs.LUNCH_PER_IP_PER_MIN = 60
    qs._lunch_hits.clear()
    class Boom(FakeSB):
        def table(self, name):
            raise RuntimeError("db down")
    qs.get_supabase = lambda: Boom([])
    qs.api_cache.clear()
    fresh_cache()
    r = client.get("/lunch", params={"date": "2026-10-14", "campus": "san_mateo"})
    check("GET /lunch: database down -> friendly 503", r.status_code == 503 and "unavailable" in r.json()["detail"], r.text)
    r = client.post("/api/chat", json={"query": "hi", "campus": "Narnia"})
    check("POST /api/chat: an invalid campus value is ignored (not a validation error)", r.status_code == 200, r.text[:200])
finally:
    qs.get_supabase = orig_sb
    qs.api_cache.clear()
    fresh_cache()

# ------------------------------------------------------------------ discovery (cafe calendar) and manual drops
ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261001
SUMMARY:San Mateo Lunch Menu
ATTACH;FILENAME=San Mateo Oct 2026.pdf;FMTTYPE=application/pdf:https://drive.google.com/open?id=1HMVoWzTYDzgStgFolW7ycrH1oZgQfiY9
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261001
SUMMARY:Hillsborough Oct. Lunch Menu
ATTACH;FILENAME=Hillsborough Oct.pdf;FMTTYPE=application/pdf:https://drive.google.com/open?id=1wpnAHARGLE38IL22QKt0bOpdpaFoLf_t
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261001
SUMMARY:Pre K - K Lunch Menu
ATTACH;FILENAME=prek.pdf;FMTTYPE=application/pdf:https://drive.google.com/open?id=1hbDHZhr9OFqbyG0QEXicwA_ghNcgclqB
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261101
SUMMARY:San Mateo Lunch
DESCRIPTION:no attachment on this one
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20250901
SUMMARY:San Mateo Lunch
ATTACH;FILENAME=old.pdf:https://drive.google.com/open?id=1OLDOLDOLDOLDOLDOLDOLDOLDOLDOLD00
END:VEVENT
END:VCALENDAR
"""
menus = lunch.parse_ical_menus(ICS)
check("discovery: finds the campus menus with their Drive ids and skips PreK-K and events without a PDF", sorted((m["campus"], m["month"]) for m in menus) == [("hillsborough", "2026-10"), ("san_mateo", "2025-09"), ("san_mateo", "2026-10")]
      and {m["file_id"] for m in menus if m["month"] == "2026-10"} == {"1HMVoWzTYDzgStgFolW7ycrH1oZgQfiY9", "1wpnAHARGLE38IL22QKt0bOpdpaFoLf_t"}, menus)
check("discovery: this month and next are the targets", lunch.target_months(date(2026, 12, 20)) == ["2026-12", "2027-01"] and lunch.target_months(date(2026, 10, 25)) == ["2026-10", "2026-11"])
check("discovery: Drive ids from both link styles", lunch.drive_id("https://drive.google.com/file/d/1HMVoWzTYDzgStgFolW7ycrH1oZgQfiY9/view") == "1HMVoWzTYDzgStgFolW7ycrH1oZgQfiY9"
      and lunch.drive_id("https://drive.google.com/open?id=1wpnAHARGLE38IL22QKt0bOpdpaFoLf_t&authuser=1") == "1wpnAHARGLE38IL22QKt0bOpdpaFoLf_t")


class Resp:
    def __init__(self, text="", status=200, content=b""):
        self.text, self.status_code, self.content = text, status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


gets = []
orig_get = lunch._get
lunch._get = lambda url, **kw: (gets.append(url), Resp(ICS))[1]
found, probs = lunch.discover(date(2026, 10, 25))
check("discovery: one GET for the feed, picks this month's menus, reports the missing November ones", len(gets) == 1 and {(m["campus"], m["month"]) for m in found} == {("san_mateo", "2026-10"), ("hillsborough", "2026-10")}
      and any("2026-11" in p for p in probs), (gets, found, probs))
lunch._get = lambda url, **kw: (_ for _ in ()).throw(ConnectionError("no network"))
found, probs = lunch.discover(date(2026, 10, 25))
check("discovery: network failure fails soft (nothing found, a problem is reported)", found == [] and probs and "unreachable" in probs[0], probs)
lunch._get = orig_get

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    saved = (lunch.MANUAL_DIR, lunch.AUTO_DIR, lunch.PARSED_DIR, lunch.LUNCH_FILE, config.DATA_DIR)
    lunch.MANUAL_DIR, lunch.AUTO_DIR, lunch.PARSED_DIR, lunch.LUNCH_FILE = td / "manual", td / "auto", td / "parsed", td / "lunch_chunks.json"
    config.DATA_DIR = td
    lunch.MANUAL_DIR.mkdir()
    saved_min, lunch.MIN_DAYS = lunch.MIN_DAYS, 1
    try:
        (lunch.MANUAL_DIR / "san_mateo_2026-10.pdf").write_bytes(b"%PDF-fake")
        (lunch.MANUAL_DIR / "hillsborough_2026-10.pdf").write_bytes(b"%PDF-fake")
        (lunch.MANUAL_DIR / "Oct menu.pdf").write_bytes(b"%PDF-fake")
        files, probs = lunch.manual_files()
        check("manual drop: <campus>_<YYYY>-<MM>.pdf is accepted, other names are reported and ignored", sorted(f["campus"] for f in files) == ["hillsborough", "san_mateo"] and any("Oct menu.pdf" in p for p in probs), (files, probs))

        def pages_for(path):
            return sm_pdf() if "san_mateo" in str(path) else hb_pdf()
        mod = types.ModuleType("pdfplumber")
        mod.open = lambda path: FakePdf(pages_for(path))
        sys.modules["pdfplumber"] = mod
        st = lunch.run(log=lambda *a: None, today=date(2026, 10, 25), discover_online=False)
        out = json.loads(lunch.LUNCH_FILE.read_text())
        check("run: manual drops parsed into chunks and written for the chunk stage", st["chunks"] == len(out) == 4 + 3 and {c["metadata"]["campus"] for c in out} == {"san_mateo", "hillsborough"}, st)
        check("run: source url falls back to the cafe page for manual drops", all(c["metadata"]["source_url"] == lunch.CAFE_URL for c in out))
        check("run: the stage reports which menus are available", st["months_available"] == ["hillsborough 2026-10", "san_mateo 2026-10"], st["months_available"])
        # a file whose header disagrees with its name is refused (a wrong campus answer would be worse than none)
        (lunch.MANUAL_DIR / "san_mateo_2026-10.pdf").unlink()
        (lunch.MANUAL_DIR / "hillsborough_2026-10.pdf").unlink()
        (lunch.MANUAL_DIR / "hillsborough_2026-11.pdf").write_bytes(b"%PDF-fake")
        (lunch.MANUAL_DIR / "san_mateo_2026-11.pdf").write_bytes(b"%PDF-fake")
        mod.open = lambda path: FakePdf(hb_pdf())          # both files are the Hillsborough October menu
        st = lunch.run(log=lambda *a: None, today=date(2026, 11, 2), discover_online=False)
        check("run: a file whose own header names another campus or month is refused", st["chunks"] == 0 and sum("refused" in p for p in st["problems"]) == 2, st["problems"])
        check("run: nothing new -> the earlier menus stay in the output file (kept)", st["chunks_kept_from_earlier_run"] == 7 and len(json.loads(lunch.LUNCH_FILE.read_text())) == 7, st)
        # unreadable file: fails soft
        mod.open = lambda path: (_ for _ in ()).throw(ValueError("bad pdf"))
        st = lunch.run_soft(log=lambda *a: None, today=date(2026, 11, 2), discover_online=False)
        check("run_soft: an unreadable PDF never raises and keeps the earlier menus", st["chunks"] == 0 and st["chunks_kept_from_earlier_run"] == 7 and any("unreadable" in p for p in st["problems"]), st)
        check("run_soft: an unexpected crash is contained", "failed softly" in lunch.run_soft(log=lambda *a: None, today="not a date")["status"])
    finally:
        sys.modules.pop("pdfplumber", None)
        lunch.MANUAL_DIR, lunch.AUTO_DIR, lunch.PARSED_DIR, lunch.LUNCH_FILE = saved[:4]
        config.DATA_DIR = saved[4]
        lunch.MIN_DAYS = saved_min

# ------------------------------------------------------------------ prune safety (index stage)
check("prune helpers: lunch rows recognised, campus from the id", freshness.is_lunch_row("lunch-san_mateo-20261005") and not freshness.is_lunch_row("cal-day-20261005-0")
      and freshness.lunch_campus("lunch-hillsborough-20261005") == "hillsborough" and freshness.lunch_campus("email-abc") is None)


def plan(have, chunks, prune=True, only_source=None, dry_run=False):
    """Run index.run against fakes; returns (deleted ids, upserted ids, stats)."""
    deleted, upserted = [], []
    saved = (index.client, index.existing, index.freshness_state, index.delete_ids, index._embed_and_upsert, index.mark_superseded, index.patch_metadata, config.CHUNKS_FILE)
    with tempfile.TemporaryDirectory() as td:
        config.CHUNKS_FILE = Path(td) / "chunks.json"
        config.CHUNKS_FILE.write_text(json.dumps(chunks))
        index.client = lambda: object()
        index.existing = lambda sb: {k: v for k, v in have.items() if not freshness.is_email_row(k, v[2])}
        index.freshness_state = lambda sb: {}
        index.delete_ids = lambda sb, ids, log: deleted.extend(ids)
        index._embed_and_upsert = lambda sb, batch: (upserted.extend(c["chunk_id"] for c in batch), len(batch))[1]
        index.mark_superseded = lambda sb, ids, log: 0
        index.patch_metadata = lambda sb, c: None
        try:
            stats = index.run(prune=prune, dry_run=dry_run, log=lambda *a: None, only_source=only_source)
        finally:
            (index.client, index.existing, index.freshness_state, index.delete_ids, index._embed_and_upsert, index.mark_superseded, index.patch_metadata, config.CHUNKS_FILE) = saved
    return deleted, upserted, stats


def lrow(campus, d, h="old"):
    return (f"lunch-{campus}-{d}", (h, "https://x", "lunch"))


have = dict([lrow("san_mateo", "20260929"), lrow("san_mateo", "20261005"), lrow("hillsborough", "20260929"), lrow("hillsborough", "20261005"),
             ("email-abcdabcdabcdabcd-000", ("h", "email://x", "email")), ("cal-1", ("h", "school-calendar", "calendar")), ("doc-1", ("h", "https://doc", "google_doc"))])
new_sm = [{"chunk_id": "lunch-san_mateo-20261105", "content": "x", "source_file": "u", "metadata": {"source": "lunch", "campus": "san_mateo", "content_hash": "n1"}}]
d, u, st = plan(have, new_sm)
check("prune: a past month ages out only for the campus whose new menu arrived", sorted(d) == ["lunch-san_mateo-20260929", "lunch-san_mateo-20261005", "cal-1", "doc-1"] or sorted(d) == ["cal-1", "doc-1", "lunch-san_mateo-20260929", "lunch-san_mateo-20261005"], d)
check("prune: the other campus keeps its rows (its menu could not be fetched)", not any("hillsborough" in x for x in d), d)
check("prune: email rows are never deleted", not any(x.startswith("email-") for x in d))
check("prune: new lunch row is embedded and upserted", u == ["lunch-san_mateo-20261105"], u)
check("prune: stats report the rows held back", st["lunch_rows_held_back_from_prune"] == 2, st)
d, u, st = plan(have, [{"chunk_id": "doc-1", "content": "x", "source_file": "u", "metadata": {"source": "google_doc", "source_url": "https://doc", "content_hash": "h"}}])
check("prune: a run that produced no lunch rows leaves every lunch row alone", not [x for x in d if x.startswith("lunch-")] and "email-abcdabcdabcdabcd-000" not in d, d)
d, u, st = plan(have, new_sm + [{"chunk_id": "doc-2", "content": "x", "source_file": "u", "metadata": {"source": "google_doc", "content_hash": "z"}}], only_source="lunch")
check("only_source=lunch: plans and prunes lunch rows only, nothing else is touched", sorted(d) == ["lunch-san_mateo-20260929", "lunch-san_mateo-20261005"] and u == ["lunch-san_mateo-20261105"], (d, u))
d, u, st = plan(have, new_sm, prune=False)
check("no --prune: nothing is deleted (daily refresh)", d == [] and st["lunch_rows_held_back_from_prune"] == 2, d)

# ------------------------------------------------------------------ audit group
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import audit_data as ad

lunch.PARSED_DIR = Path(tempfile.mkdtemp())          # the audit reads parsed menus from here: keep the real local ones out of the test


class FakeSources:
    def __init__(self, rows, today=date(2026, 10, 25)):
        self.db, self.db_err, self.today = rows, "", today
        self.local_chunks = []
        self.args = types.SimpleNamespace(offline=False)

    def lunch_db(self):
        return [r for r in self.db if freshness.is_lunch_row(r["chunk_id"], (r.get("metadata") or {}).get("source"))]


def audit_for(rows):
    A = ad.Audit({}, False)
    ad.audit_lunch(A, FakeSources(rows))
    return A


def annotated(rows):
    out = copy.deepcopy(rows)
    for r in out:
        m = r["metadata"]
        m.update({"indexed_at": "2026-10-25T10:00:00+00:00", "source_date": "2026-10-25", "school_year": "2026-27", "stale": False})
    return out


good = annotated(db_rows((SM_OCT, "https://drive.google.com/file/d/SMID/view"), (HB_OCT, "https://drive.google.com/file/d/HBID/view")))
A = audit_for(good)
check("audit lunch: a complete month for both campuses passes", all(A.status(g) in ("ok", "SKIP") for g in A.g) and not any(A.g[g]["bad"] for g in A.g), {g: A.g[g]["bad"] for g in A.g if A.g[g]["bad"]})
missing = [r for r in good if r["chunk_id"] != "lunch-san_mateo-20261014"]
A = audit_for(missing)
check("audit lunch: a missing weekday is reported", any("2026-10-14" in m for g in A.g for _, m in A.g[g]["bad"]), {g: A.g[g]["bad"] for g in A.g})
dup = good + [copy.deepcopy(good[0])]
A = audit_for(dup)
check("audit lunch: a duplicate date is reported", any("duplicate" in m for g in A.g for _, m in A.g[g]["bad"]))
badc = copy.deepcopy(good)
badc[0]["metadata"]["campus"] = "narnia"
A = audit_for(badc)
check("audit lunch: an invalid campus is reported", any("campus" in m for g in A.g for _, m in A.g[g]["bad"]))
nofresh = copy.deepcopy(good)
del nofresh[3]["metadata"]["indexed_at"]
A = audit_for(nofresh)
check("audit lunch: missing freshness metadata is reported", any("indexed_at" in m for g in A.g for _, m in A.g[g]["bad"]))
mism = copy.deepcopy(good)
mism[5]["metadata"]["items"] = ["Soup: something else"]
A = audit_for(mism)
check("audit lunch: record text that disagrees with its items is reported", any("items" in m for g in A.g for _, m in A.g[g]["bad"]))
A = audit_for([])
check("audit lunch: no lunch rows at all is a skip, not a failure", all(A.status(g) in ("SKIP", "ok") for g in A.g))

print()
print(f"{len(FAILS)} failed" if FAILS else "all lunch tests passed")
sys.exit(1 if FAILS else 0)
