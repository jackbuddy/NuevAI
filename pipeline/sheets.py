"""Google Sheets -> structured knowledge.

* is_sensitive(): sheets that hold people's data (student names, sign-ups, rosters) are never indexed.
* sports_events(): team schedule sheets (DATE / HOME TEAM / AWAY TEAM / TIME) become dated calendar events,
  so "when is the next Upper School basketball game?" is answered from the same structured lookup as the
  school calendar.
* club_outline(): the clubs sheet becomes a one-glance list of club names and what they do (no leader names).
"""
import csv
import hashlib
import io
import re
from datetime import date, datetime, timedelta

SENSITIVE_HEADER = re.compile(r"student\s*(last|first)|first\s*name|last\s*name|e-?mail|phone|guardian|birth|home address|ssn", re.I)
# Any source (Google, Nexus, manual): titles that are people-data by nature.
STRICT_TITLE = re.compile(r"diploma|rsvp|roster|transcript|salary|payroll|medical record", re.I)
# Spreadsheets only (a page called "Grades 1-4" is fine; a sheet called "Grades" is not).
SHEET_TITLE = re.compile(r"sign[- ]?up|responses|attendance|grades?\b|submissions", re.I)


def rows_of(page: dict) -> list[list[str]]:
    try:
        return list(csv.reader(io.StringIO(page["text"])))
    except csv.Error:
        return []


def is_sensitive(page: dict) -> bool:
    title = page.get("title", "") or ""
    if STRICT_TITLE.search(title):
        return True
    if page.get("source") == "google_sheet" or "/spreadsheets/" in page.get("url", ""):
        header = page["text"].split("\n", 1)[0]
        return bool(SHEET_TITLE.search(title) or SENSITIVE_HEADER.search(header))
    return False


def _cols(header: list[str]) -> dict:
    low = [h.strip().lower() for h in header]
    pick = lambda *names: next((i for i, h in enumerate(low) if h in names), None)
    return {"date": pick("date"), "home": pick("home team", "home"), "away": pick("away team", "away"),
            "time": pick("time", "time "), "loc": pick("location", "venue")}


def _date(cell: str) -> date | None:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", cell or "")
    if not m:
        return None
    y = int(m.group(3))
    try:
        return date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _time(cell: str):
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([AP]M)", (cell or "").upper())
    if not m:
        return None
    h, mi = int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0), int(m.group(2) or 0)
    return f"{h:02d}:{mi:02d}"


def sports_events(page: dict, keep_from: date, keep_to: date) -> list[dict]:
    rows = rows_of(page)
    hdr = next((i for i, r in enumerate(rows[:6]) if any(c.strip().lower() == "date" for c in r)
                and any("home" in c.strip().lower() for c in r)), None)
    if hdr is None:
        return []
    c = _cols(rows[hdr])
    if c["date"] is None or c["home"] is None:
        return []
    out, seen = [], set()
    get = lambda r, k: (r[c[k]].strip() if c[k] is not None and c[k] < len(r) else "")
    for r in rows[hdr + 1:]:
        d = _date(get(r, "date"))
        home, away = get(r, "home"), get(r, "away")
        if not d or not (keep_from <= d <= keep_to) or not (home or away):
            continue
        if away and _date(home) and not _date(away):        # a date pasted into the Home cell ("Tue 9/8/26 | Nueva"): the opponent is the location
            home = get(r, "loc") or home
        key = (d, (home + "|" + away).lower())
        if key in seen:                                      # sheets repeat the home games in a second "Home schedule" table
            continue
        seen.add(key)
        title = page["title"]
        if not away:                                    # a note row such as "First Day of Practice"
            summary, kind, ha, opp = f"{title} - {home.title() if home.isupper() else home}", "event", "", ""
        else:
            nueva_home = home.lower().startswith("nueva")
            opp = away if nueva_home else home
            ha = "home" if nueva_home else "away"
            summary, kind = f"{title} - Game {opp} - {ha.title()}", "game"
        start = _time(get(r, "time"))
        t_label = get(r, "time") or ("time to be announced" if kind == "game" else "all day")
        out.append({"date": d, "summary": summary, "kind": kind, "home_away": ha, "start": start, "time": t_label,
                    "location": get(r, "loc"), "url": page["url"], "opp": opp})
    return out


def event_chunk(e: dict) -> dict:
    when = e["date"].strftime("%A, %B %-d, %Y")
    lines = [f"Calendar event: {e['summary']}", f"Date: {when}", f"Time: {e['time']}" + (" (Pacific)" if e["start"] else "")]
    if e["location"]:
        lines.append(f"Location: {e['location']}")
    content = f"Title: {e['summary']}\nSource: team schedule sheet\n\n" + "\n".join(lines)
    extra = {"end_date": e["date"].isoformat(), "summary": e["summary"], "when": when, "time": e["time"], "all_day": not e["start"],
             "start_time": e["start"], "end_time": None, "location": e["location"], "description": "", "category": "athletics",
             "kind": e["kind"], "home_away": e["home_away"]}
    cid = "sheet-" + hashlib.sha1(f"{e['url']}|{e['date']}|{e['summary']}".encode()).hexdigest()[:12]
    return {"chunk_id": cid, "content": content, "source_file": e["url"],
            "metadata": {"source": "calendar_sheet", "source_url": e["url"], "title": e["summary"], "event_date": e["date"].isoformat(),
                         **extra, "content_hash": hashlib.sha1((content + str(sorted(extra.items(), key=lambda kv: kv[0]))).encode()).hexdigest()}}


def club_outline(page: dict):
    """Yield (title, body) outline parts for a clubs sheet: 'Clubs, Description, Leaders, ...'."""
    rows = rows_of(page)
    if not rows or not rows[0] or rows[0][0].strip().lower() != "clubs":
        return
    items = []
    for r in rows[1:]:
        name = r[0].strip() if r else ""
        desc = r[1].strip() if len(r) > 1 else ""
        if name and (desc or len(name) > 3) and name.lower() not in ("clubs",):
            short = re.sub(r"\s+", " ", desc)[:110]
            items.append(f"- {name}" + (f": {short}" if short else ""))
    items = list(dict.fromkeys(items))
    for i in range(0, len(items), 25):
        yield page["title"], f"{page['title']} - clubs and affinity groups ({len(items)} listed):\n" + "\n".join(items[i:i + 25])
