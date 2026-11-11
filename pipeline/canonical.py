"""Canonical lists derived from the source files on every pipeline run (nothing here is typed by hand).

Each builder turns a source the pipeline already reads into one enumerated, plainly titled record set that vector AND keyword
search can find ("All 2026-27 clubs that meet on Tuesday", "Departments and courses", "Teams with schedules on the calendar"):

  clubs_directory      Clubs Catalog 2026-27 workbook      -> clubs by meeting day/time with room and advisor (no student names)
  courses_by_dept      2026-27 Course Catalog (Google Doc) -> departments and every course in each
  us_no_class_days     "The Sheet" master schedule         -> every Upper School no-class day and break, merged into ranges
  teams_with_schedules calendar feed + team sheets         -> every team/level with scheduled games or practices (date range, counts)
  links_directory      the link registry                   -> forms / documents / booking / payment / portal links by category

The curated, source-quoted lists (all sports, leadership, contacts, admissions...) are in knowledge/*.md (pipeline/facts.py).
Records carry source "school_canonical" (not a workbook source, so a partial run such as the CI calendar job never flags them superseded) with source_url facts://canonical/<name>, an as_of date (the date of the underlying source)
and the school year they apply to, in the text and in metadata, like every other fact record.
"""
import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config, safety

MAX_CHARS = 1150            # a record is shown to the model in full only up to roughly this size: split longer lists


def _day_of(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()


def record(name: str, head: str, doc_title: str, body: str, as_of: str, source: str, year: str = "2026-27") -> dict:
    content = (f"Title: {head}\nSection: {doc_title}\nSource: {source}\nLast updated: {as_of}\nApplies to: the {year} school year\n\n"
               f"{head}\n{body}")
    content, _ = safety.scrub(content)
    return {"chunk_id": "canon-" + hashlib.sha1(f"{name}|{head}".encode()).hexdigest()[:14], "content": content,
            "source_file": f"facts://canonical/{name}",
            "metadata": {"source": "school_canonical", "source_url": f"facts://canonical/{name}", "title": head, "kind": "canonical",
                         "list": name, "as_of": as_of, "content_hash": hashlib.sha1(content.encode()).hexdigest()}}


def split_lines(lines: list[str], limit: int = MAX_CHARS) -> list[list[str]]:
    parts, cur, size = [], [], 0
    for ln in lines:
        if cur and size + len(ln) + 1 > limit:
            parts.append(cur)
            cur, size = [], 0
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        parts.append(cur)
    return parts


def numbered(head: str, i: int, n: int) -> str:
    return head if n == 1 else f"{head} (part {i + 1} of {n})"


# --------------------------------------------------------------------------- clubs
def clubs_rows(path: Path) -> list[dict]:
    import openpyxl
    from .workbooks import cell, grid
    ws = openpyxl.load_workbook(str(path), data_only=True).worksheets[0]
    rows = grid(ws)
    head = [cell(c).lower() for c in rows[0]]
    idx = {n: head.index(n) for n in ("clubs", "description", "leaders", "advisor", "day", "time", "room") if n in head}
    out = []
    for r in rows[1:]:
        r = r + [None] * 8
        g = lambda k: cell(r[idx[k]]) if k in idx else ""
        if g("clubs"):
            room = g("room")
            room = room[:-2] if room.endswith(".0") else room
            out.append({"name": g("clubs"), "day": g("day"), "time": g("time"), "advisor": g("advisor"), "room": room})
    return out


def _day_key(day: str) -> tuple:
    order = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    low = day.lower()
    for i, d in enumerate(order):
        if d in low and not low.startswith(("every other", "first")):
            return (0, i, low)
    for i, d in enumerate(order):
        if d in low:
            return (1, i, low)
    return (2, 0, low)


def clubs_directory(log=print) -> list[dict]:
    from .workbooks import find_workbooks
    found = find_workbooks().get("clubs") or []
    if not found:
        return []
    path = found[0]
    clubs = clubs_rows(path)
    as_of = _day_of(path)
    src = f"Clubs Catalog 2026-27 ({path.name}), file dated {as_of}"
    doc = "Clubs and affinity groups directory 2026-27"
    out = []
    groups = defaultdict(list)
    for c in clubs:
        groups[(c["day"] or "TBD", c["time"] or "TBD")].append(c)

    def line(c):
        bits = [x for x in (f"room {c['room']}" if c["room"] and c["room"] not in ("N/A", "TBD") else "", f"advisor {c['advisor']}" if c["advisor"] else "") if x]
        return f"- {c['name']}" + (f" ({'; '.join(bits)})" if bits else "")

    lines = []
    for (day, time), cs in sorted(groups.items(), key=lambda kv: (_day_key(kv[0][0]), kv[0][1])):
        lines.append(f"{day}, {time} ({len(cs)} clubs):")
        lines += [line(c) for c in sorted(cs, key=lambda c: c["name"].lower())]
    parts = split_lines(lines)
    intro = (f"There are {len(clubs)} clubs and affinity groups for 2026-27 (Upper School). Grouped here by when they meet "
             "(day, then lunch or after school), with room and faculty advisor. The catalog has no category column.")
    for i, p in enumerate(parts):
        out.append(record("clubs", numbered("All 2026-27 clubs and affinity groups by meeting day, room and advisor", i, len(parts)), doc,
                          (intro + "\n" if i == 0 else "") + "\n".join(p), as_of, src))
    alpha = sorted(c["name"] for c in clubs)
    for i, p in enumerate(split_lines([f"- {n}" for n in alpha])):
        n = len(split_lines([f"- {x}" for x in alpha]))
        out.append(record("clubs", numbered("Alphabetical list of every 2026-27 club and affinity group (names only)", i, n), doc,
                          f"{len(clubs)} clubs and affinity groups in total:\n" + "\n".join(p), as_of, src))
    log(f"  canonical clubs: {len(clubs)} clubs -> {len(out)} records")
    return out


# --------------------------------------------------------------------------- courses by department
def courses_by_dept(pages: list[dict], log=print) -> list[dict]:
    from . import chunker
    page = next((p for p in pages if (p.get("title") or "").strip() == "2026-27 Course Catalog" and p.get("source") == "google_doc"), None) \
        or next((p for p in pages if (p.get("title") or "").strip() == "2026-27 Course Catalog"), None)
    if not page:
        return []
    text = chunker.promote_record_titles(chunker.clean_text(page["text"]))
    depts, cur = [], None
    skip = re.compile(r"table of contents|newly approved|graduation requirements|course load|navigating", re.I)
    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not m:
            continue
        level, title = len(m.group(1)), re.sub(r"\s+", " ", m.group(2)).strip()
        if level == 1:
            cur = None if skip.search(title) else {"name": title, "courses": []}
            if cur:
                depts.append(cur)
        elif level == 4 and cur is not None and title not in cur["courses"]:
            cur["courses"].append(title)
    depts = [d for d in depts if d["courses"]]
    as_of = (page.get("scraped_at") or "")[:10] or date.today().isoformat()
    src = f"2026-27 Course Catalog (Google Doc, captured {as_of}), department headings and course titles"
    doc = "Upper School departments and courses 2026-27"
    out = []
    total = sum(len(d["courses"]) for d in depts)
    out.append(record("courses", "What departments does the Upper School have? Every department in the 2026-27 course catalog", doc,
                      f"The 2026-27 Upper School catalog has {len(depts)} departments with courses: "
                      + "; ".join(f"{d['name']} ({len(d['courses'])})" for d in depts)
                      + f". {total} course titles in all. Chinese, Japanese and Spanish are the world languages. "
                        "The department record for each lists its courses.", as_of, src))
    for d in depts:
        lines = [f"- {c}" for c in d["courses"]]
        parts = split_lines(lines)
        for i, p in enumerate(parts):
            out.append(record("courses", numbered(f"{d['name']} department courses 2026-27: every course offered", i, len(parts)), doc,
                              f"The 2026-27 {d['name']} department lists {len(d['courses'])} courses:\n" + "\n".join(p), as_of, src))
    log(f"  canonical courses: {len(depts)} departments, {total} courses -> {len(out)} records")
    return out


# --------------------------------------------------------------------------- Upper School no-class days
def us_no_class_days(log=print) -> list[dict]:
    import openpyxl
    from . import workbooks
    found = workbooks.find_workbooks().get("master") or []
    if not found:
        return []
    path = found[0]
    days = workbooks.parse_days(openpyxl.load_workbook(str(path), data_only=True))
    off = {d: v for d, v in days.items() if any("no class" in l.lower() for l in v["labels"])}
    if not off:
        return []
    ranges, cur = [], None
    for d in sorted(off):
        reason = off[d]["type"] or "No classes"
        nxt_ok = cur and cur["reason"] == reason and all((cur["end"] + timedelta(days=k)).weekday() >= 5 or (cur["end"] + timedelta(days=k)) in off
                                                         for k in range(1, (d - cur["end"]).days)) and (d - cur["end"]).days <= 4
        if nxt_ok:
            cur["end"] = d
        else:
            cur = {"start": d, "end": d, "reason": reason}
            ranges.append(cur)
    as_of = _day_of(path)
    fmt = lambda d: d.strftime("%a %b %-d, %Y")
    lines = [f"- {fmt(r['start'])}" + (f" to {fmt(r['end'])}" if r["end"] != r["start"] else "") + f": {r['reason']}" for r in ranges]
    first = min(days)
    doc = "Upper School no-class days and breaks 2026-27"
    out = []
    parts = split_lines(lines)
    for i, p in enumerate(parts):
        body = ("Upper School (San Mateo campus) days with no classes in 2026-27, from the master schedule ('The Sheet', Calendar AB + "
                f"Special Schedule tab; the first listed day of the sheet is {fmt(first)}). Winter, February and spring-break weeks and holidays "
                "are included; Lower and Middle School parent-teacher conference days differ and are in the year-at-a-glance record.\n" if i == 0 else "") + "\n".join(p)
        out.append(record("us_no_class_days", numbered("Upper School breaks, holidays and no-class days 2026-27 (every date with no classes)", i, len(parts)), doc,
                          body, as_of, f"The Sheet - Master Schedule 2026-27 ({path.name}), file dated {as_of}"))
    log(f"  canonical no-class days: {len(ranges)} ranges -> {len(out)} records")
    return out


# --------------------------------------------------------------------------- teams that have a schedule
TEAM_RX = re.compile(r"^(?P<team>(?:US|MS)\b[^-]*?(?:\([^)]*\))?[^-]*?)\s*(?:-\s*(?P<what>.*))?$")


def team_name(summary: str) -> str:
    s = re.split(r"\s+(?:vs\.?|@|at)\s+", summary, maxsplit=1, flags=re.I)[0]
    s = re.sub(r"\s*-\s*(game|match|meet|practice|scrimmage|first day|ccs|cif|psal|1st round|thanksgiving|winter break|february break|friendship).*$", "",
               s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def teams_with_schedules(chunks: list[dict], log=print) -> list[dict]:
    teams = defaultdict(lambda: {"games": 0, "practices": 0, "first": None, "last": None, "src": set()})
    for c in chunks:
        m = c["metadata"]
        if m.get("source") not in ("calendar", "calendar_sheet") or m.get("kind") not in ("game", "practice"):
            continue
        name = team_name(m.get("summary") or m.get("title") or "")
        if not re.match(r"^(US|MS)\b", name):
            continue
        t = teams[name]
        t["games" if m["kind"] == "game" else "practices"] += 1
        d = m.get("event_date")
        if d:
            t["first"], t["last"] = min(filter(None, [t["first"], d])), max(filter(None, [t["last"], d]))
        t["src"].add("team schedule sheet" if m.get("source") == "calendar_sheet" else "school calendar feed")
    if not teams:
        return []
    as_of = date.today().isoformat()
    lines = []
    for name in sorted(teams):
        t = teams[name]
        bits = []
        if t["games"]:
            bits.append(f"{t['games']} game" + ("s" if t["games"] != 1 else ""))
        if t["practices"]:
            bits.append(f"{t['practices']} practices")
        lines.append(f"- {name}: {', '.join(bits)} scheduled, {t['first']} to {t['last']} ({' and '.join(sorted(t['src']))})")
    doc = "Teams with schedules on the athletics calendar"
    out = []
    parts = split_lines(lines)
    for i, p in enumerate(parts):
        body = ("Teams that have games or practices on the school calendar or the team schedule sheets right now (US = Upper School, MS = Middle "
                "School; levels such as Varsity, JV, 7th/8th VA, 5th/6th JVA are part of the team name). This is the list of teams WITH A SCHEDULE: "
                "a sport can be offered by Nueva and not appear here until its season is on the calendar. All sports the school lists are in the "
                "athletics offerings record.\n" if i == 0 else "") + "\n".join(p)
        out.append(record("teams", numbered("Teams with games or practices on the schedule (by level and gender)", i, len(parts)), doc, body, as_of,
                          "school calendar feed and team schedule sheets, read on the pipeline run date"))
    log(f"  canonical teams: {len(teams)} teams with a schedule -> {len(out)} records")
    return out


# --------------------------------------------------------------------------- forms / links directory
def links_directory(links: list[dict], log=print) -> list[dict]:
    if not links:
        return []
    as_of = date.today().isoformat()
    names = {"form": "Forms", "document": "Documents and policies", "booking": "Booking and sign-up links", "payment": "Payment, store and donation links",
             "portal": "Portals and logins (addresses only)", "calendar": "Calendars"}
    out = []
    for cat in ("form", "booking", "payment", "portal", "calendar", "document"):
        rs = [r for r in links if r["category"] == cat and not config.STALE_URL.search(r["url"])]
        if not rs:
            continue
        seen, lines = set(), []
        for r in sorted(rs, key=lambda r: r["label"].lower()):
            label = re.sub(r"\s+", " ", r["label"]).strip()[:90]
            if (label.lower(), r["url"]) in seen or not label:
                continue
            seen.add((label.lower(), r["url"]))
            lines.append(f"- {label}: {r['url']}")
        parts = split_lines(lines, 1150)
        for i, p in enumerate(parts):
            out.append(record("links", numbered(f"Directory of school {names[cat].lower()}: where to find each one", i, len(parts)),
                              "Forms and links directory", f"{len(lines)} {names[cat].lower()} the school's pages link to (label: address):\n" + "\n".join(p),
                              as_of, "link registry built from the school's pages by this pipeline (pipeline/links.py)", year="2026-27"))
    log(f"  canonical links directory: {len(out)} records")
    return out


def build_chunks(existing_chunks: list[dict], pages: list[dict], links: list[dict], log=print) -> list[dict]:
    out = []
    for fn in (lambda: clubs_directory(log), lambda: courses_by_dept(pages, log), lambda: us_no_class_days(log),
               lambda: teams_with_schedules(existing_chunks, log), lambda: links_directory(links, log)):
        try:
            out += fn()
        except Exception as e:                  # one missing source must not stop the whole pipeline
            log(f"  canonical list skipped: {type(e).__name__}: {e}")
    return out
