"""Calendar stage: the whole-school iCal feed (sports, events, closures).

The feed URL carries a private access token, so it is read from the
NUEVAI_CALENDAR_URL environment variable (.env locally, a GitHub secret in CI),
never from code. No login is involved. webcal:// is accepted and treated as https://.

Each event becomes a retrievable chunk with an unambiguous date, and every day
that has events also gets a digest chunk ("Schedule for Tuesday, October 28,
2026: ...") so questions like "what's happening Oct 28" retrieve the whole day.
Events from before the start of the current school year (window_floor) are dropped, and the indexer
removes expired calendar chunks from Supabase automatically.
"""
import hashlib
import json
import os
import re
from collections import defaultdict
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))     # freshness.py lives at the repo root
import calendar_meta  # noqa: E402
import freshness      # noqa: E402
from . import safety  # noqa: E402

import requests

from . import config

PACIFIC = "America/Los_Angeles"
KEEP_PAST_DAYS = 14          # minimum history; the real floor is window_floor() (the start of the school year)
KEEP_FUTURE_DAYS = 430


def today_pacific() -> date:
    """"Today" at the school (Pacific time), not the runner's UTC date: a run at 6 pm Pacific is already the next day in UTC."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(PACIFIC)).date()


def window_floor(today: date) -> date:
    """Oldest event day kept. The feed itself carries history (it starts in September for 2026-27), so events of the CURRENT school
    year (from August 1) stay answerable as "that already happened on <date>"; never less than KEEP_PAST_DAYS. At the start of the
    next school year the floor moves and the old year expires through the normal expired-calendar cleanup."""
    start = date(freshness.current_start(today), 8, 1)
    return min(today - timedelta(days=KEEP_PAST_DAYS), start)
DIGEST_MAX_CHARS = 1500

CATEGORY_RULES = [
    ("no_school", re.compile(r"\bno school\b|\bno classes\b|\bbreak\b|\bholiday\b|\bcampus closed\b|\bschool closed\b|\bearly (release|dismissal)\b", re.I)),
    ("athletics", re.compile(r"-\s*(game|match|meet|scrimmage)\b|\b(practice|tournament|invitational|athlet\w*|basketball|soccer|swim\w*|tennis|"
                             r"volleyball|baseball|softball|golf|cross country|track|lacrosse|badminton|water polo|ultimate|fencing|"
                             r"sports?|varsity|jv)\b", re.I)),
    ("community", re.compile(r"\b(parent|coffee|social|night|celebration|festival|carnival|assembly|fair|fundrais\w*|gala|brunch|picnic|"
                             r"volunteer|alumni|admissions|open house|formal|coffeehouse|spirit day|reunion|pack the (field|house))\b", re.I)),
    ("academics", re.compile(r"\b(exam|finals?|midterm|testing|erb|report cards?|conferences?|registration|semester|trimester|"
                             r"intersession|deadline|last day|first day|orientation|course|class of|college|advisory|schedule|resum\w+)\b", re.I)),
    ("arts", re.compile(r"\b(concert|play|theat(er|re)|musical|recital|performance|exhibit|art show|open stage|dance|choir|orchestra|band|"
                        r"ensemble|jamboree|evening of the arts|rehearsals?)\b", re.I)),
]
# titles of events that were moved or called off but still sit in the feed as a placeholder: "(MOVED TO OCT 8) NPA ...", "CANCELED: ...".
# ("[Moved from Nov 3]" is the NEW date of a moved event and is kept.)
CALLED_OFF = re.compile(r"^\W*(?:cancel+ed|postponed)\b|\(\s*(?:moved to|cancel+ed|postponed)\b|\[\s*(?:moved to|cancel+ed|postponed)\b|\bcancel+ed\s*[:\-]", re.I)


def categorize(summary: str) -> str:
    if calendar_meta.no_classes_for(summary) is not None:
        return "no_school"
    for name, rx in CATEGORY_RULES:
        if rx.search(summary):
            return name
    return "other"


def event_kind(summary: str) -> str:
    s = summary.lower()
    if re.search(r"\bpractice\b", s):
        return "practice"
    if re.search(r"-\s*(game|match|meet|scrimmage)\b|\b(tournament|invitational)\b", s):
        return "game"
    return "event"


def _unfold(text: str):
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(v: str) -> str:
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v).strip()


def _parse_dt(value: str, params: str):
    """-> (datetime|date, is_all_day). Times are returned as naive Pacific wall-clock time: a trailing Z (UTC) and an explicit
    TZID other than Pacific are converted; a floating time (no zone) is taken as Pacific."""
    v = value.strip()
    if re.fullmatch(r"\d{8}", v) or ("VALUE=DATE" in params.upper() and "VALUE=DATE-TIME" not in params.upper()):
        return datetime.strptime(v[:8], "%Y%m%d").date(), True
    from zoneinfo import ZoneInfo
    dt = datetime.strptime(v.rstrip("Z"), "%Y%m%dT%H%M%S")
    if v.endswith("Z"):
        dt = dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(PACIFIC)).replace(tzinfo=None)
    else:
        m = re.search(r"TZID=\"?([^;:\"]+)", params)
        if m and m.group(1) != PACIFIC:
            try:
                dt = dt.replace(tzinfo=ZoneInfo(m.group(1))).astimezone(ZoneInfo(PACIFIC)).replace(tzinfo=None)
            except Exception:           # an unknown zone name (Outlook "Pacific Standard Time"): keep the wall time
                pass
    return dt, False


def parse_ics(text: str) -> list[dict]:
    events, cur, in_event = [], None, False
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur, in_event = {}, True
        elif line == "END:VEVENT":
            if cur and "start" in cur and cur.get("summary"):
                events.append(cur)
            cur, in_event = None, False
        elif in_event and ":" in line:
            head, _, value = line.partition(":")
            name, _, params = head.partition(";")
            name = name.upper()
            try:
                if name == "DTSTART":
                    cur["start"], cur["all_day"] = _parse_dt(value, params)
                elif name == "DTEND":
                    cur["end"], cur["end_all_day"] = _parse_dt(value, params)
                elif name == "SUMMARY":
                    cur["summary"] = _unescape(value)
                elif name == "LOCATION":
                    cur["location"] = _unescape(value)
                elif name == "DESCRIPTION":
                    cur["description"] = _unescape(value)
                elif name == "UID":
                    cur["uid"] = value.strip()
                elif name == "STATUS":
                    cur["status"] = value.strip()
                elif name == "RRULE":
                    cur["recurring"] = True
                    cur["rrule"] = value.strip()
                elif name == "EXDATE":
                    cur.setdefault("exdates", []).extend(_parse_dt(x, params)[0] for x in value.split(",") if x.strip())
                elif name == "RECURRENCE-ID":
                    cur["recurrence_id"] = _parse_dt(value, params)[0]
            except ValueError:
                pass
    return events


WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
MAX_OCCURRENCES = 1200


def _rrule_parts(rule: str) -> dict:
    return {k.upper(): v for k, _, v in (p.partition("=") for p in rule.split(";") if "=" in p)}


def _add_months(d: date, n: int) -> date | None:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    try:
        return d.replace(year=y, month=m + 1)
    except ValueError:                      # the 31st of a 30-day month: the rule skips that month
        return None


def occurrences(e: dict, lo: date, hi: date) -> list[dict]:
    """Expand one recurring event (RRULE) into dated copies inside [lo, hi]. Supported: DAILY, WEEKLY (BYDAY), MONTHLY (day of month, or
    BYDAY with an ordinal such as 1MO / -1FR) and YEARLY, with INTERVAL, COUNT, UNTIL and EXDATE. The wall-clock time of the first
    instance is kept on every date (so a 3:30 pm practice stays 3:30 pm across a DST change). Anything else returns only the first
    instance and is flagged `rrule_unsupported`."""
    start, end = e["start"], e.get("end")
    all_day = e["all_day"]
    first = _day(start)
    span = (end - start) if end is not None and isinstance(end, datetime) == isinstance(start, datetime) else (timedelta(days=1) if all_day else timedelta(0))
    r = _rrule_parts(e.get("rrule", ""))
    freq, interval = r.get("FREQ", ""), max(int(r.get("INTERVAL", "1") or 1), 1)
    count = int(r["COUNT"]) if r.get("COUNT", "").isdigit() else None
    until = None
    if r.get("UNTIL"):
        try:
            until = _day(_parse_dt(r["UNTIL"], "")[0])
        except ValueError:
            pass
    byday = [x for x in r.get("BYDAY", "").split(",") if x]
    ordinal_ok = all(re.fullmatch(r"-?\d?(MO|TU|WE|TH|FR|SA|SU)", x) for x in byday)
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY") or not ordinal_ok or (r.get("BYSETPOS") or r.get("BYWEEKNO") or r.get("BYYEARDAY")):
        return [{**e, "rrule_unsupported": True}]
    ex = {_day(x) for x in e.get("exdates", [])}
    days: list[date] = []
    if freq == "DAILY":
        d = first
        while len(days) < MAX_OCCURRENCES and d <= hi:
            days.append(d)
            d += timedelta(days=interval)
    elif freq == "WEEKLY":
        wanted = sorted(WEEKDAYS.index(x[-2:]) for x in byday) or [first.weekday()]
        monday = first - timedelta(days=first.weekday())
        while len(days) < MAX_OCCURRENCES and monday <= hi:
            for wd in wanted:
                d = monday + timedelta(days=wd)
                if d >= first:
                    days.append(d)
            monday += timedelta(weeks=interval)
    elif freq == "MONTHLY":
        n = 0
        while len(days) < MAX_OCCURRENCES:
            base = _add_months(first.replace(day=1), n * interval)
            if base is None or base > hi:
                break
            if byday:
                for tok in byday:
                    wd, nth = WEEKDAYS.index(tok[-2:]), int(tok[:-2]) if tok[:-2] else 0
                    month = [base + timedelta(days=i) for i in range(31) if (base + timedelta(days=i)).month == base.month and (base + timedelta(days=i)).weekday() == wd]
                    pick = month if not nth else [month[nth - 1] if nth > 0 and len(month) >= nth else month[nth] if nth < 0 and len(month) >= -nth else None]
                    days += [d for d in pick if d and d >= first]
            else:
                bmd = [int(x) for x in r.get("BYMONTHDAY", "").split(",") if x.lstrip("-").isdigit()] or [first.day]
                for md in bmd:
                    try:
                        d = base.replace(day=md) if md > 0 else None
                    except ValueError:
                        d = None
                    if d and d >= first:
                        days.append(d)
            n += 1
        days.sort()
    else:  # YEARLY
        n = 0
        while len(days) < MAX_OCCURRENCES:
            try:
                d = first.replace(year=first.year + n * interval)
            except ValueError:
                d = None
            if d and d > hi or first.year + n * interval > hi.year:
                break
            if d:
                days.append(d)
            n += 1
    out = []
    for i, d in enumerate(days):
        if count is not None and i >= count:
            break
        if until is not None and d > until:
            break
        if d in ex or d + (span if all_day else timedelta(0)) < lo or d > hi:
            continue
        shift = d - first
        ns = start + shift
        out.append({**e, "start": ns, "end": (ns + span) if end is not None else None, "occurrence_of": e.get("uid")})
    return out


def expand_recurring(events: list[dict], lo: date, hi: date) -> tuple[list[dict], dict]:
    """Replace each RRULE event by its occurrences; RECURRENCE-ID overrides (an instance moved or edited in the feed) replace the
    generated instance of the same day. -> (events, {"expanded": n, "unsupported": n})"""
    overrides = {(e.get("uid"), _day(e["recurrence_id"])) for e in events if e.get("recurrence_id") is not None}
    out, stats = [], {"expanded": 0, "unsupported": 0}
    for e in events:
        if not e.get("rrule"):
            out.append(e)
            continue
        occ = occurrences(e, lo, hi)
        if occ and occ[0].get("rrule_unsupported"):
            stats["unsupported"] += 1
            out.append(e)
            continue
        stats["expanded"] += 1
        out += [o for o in occ if (e.get("uid"), _day(o["start"])) not in overrides]
    return out, stats


def _day(d) -> date:
    return d.date() if isinstance(d, datetime) else d


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p").replace(":00 ", " ")


def describe(e: dict) -> dict:
    start, end = e["start"], e.get("end")
    d0 = _day(start)
    if e["all_day"]:
        # an all-day DTEND is exclusive (the day after the last day); a few feed events pair an all-day start with a timed
        # end on the same day ("Daylight Saving Time Ends": DTEND 01:00 on the start date), which is inclusive
        if end and e.get("end_all_day", True):
            last = _day(end) - timedelta(days=1)
        else:
            last = _day(end) if end else d0
        last = max(last, d0)
        when = d0.strftime("%A, %B %-d, %Y") + (f" through {last.strftime('%A, %B %-d, %Y')}" if last > d0 else "")
        time = "all day"
    else:
        when = d0.strftime("%A, %B %-d, %Y")
        time = f"{_fmt_time(start)} to {_fmt_time(end)}" if end and _day(end) == d0 else _fmt_time(start)
    return {"when": when, "time": time, "date": d0, "end_date": (last if e["all_day"] else _day(end) if end else d0)}


def fetch(url: str) -> str:
    r = requests.get(url.replace("webcal://", "https://", 1), timeout=60, headers={"User-Agent": config.USER_AGENT})
    r.raise_for_status()
    if "BEGIN:VCALENDAR" not in r.text[:200]:
        raise RuntimeError("calendar URL did not return an iCal feed (token expired?)")
    return r.text


def _meta(e: dict, source: str, title: str, content: str, extra: dict) -> dict:
    return {"source": source, "source_url": "school-calendar", "title": title, "event_date": e["date"].isoformat(), **extra,
            "content_hash": hashlib.sha1((content + json.dumps(extra, sort_keys=True, default=str)).encode()).hexdigest()}


def run(log=print) -> dict:
    url = os.getenv("NUEVAI_CALENDAR_URL", "").strip().strip("\"'")
    if not url:
        msg = "skipped: NUEVAI_CALENDAR_URL not set"
        log(f"calendar: {msg}")
        return {"status": msg}
    events = parse_ics(fetch(url))
    return build(events, log=log, today=today_pacific())


def build(events: list[dict], log=print, today: date | None = None) -> dict:
    """Parsed feed events -> calendar chunks (written to config.CALENDAR_FILE) and a stats dict. Split from run() so the offline tests can
    feed it a fake feed."""
    today = today or today_pacific()
    lo, hi = window_floor(today), today + timedelta(days=KEEP_FUTURE_DAYS)
    events, rec_stats = expand_recurring(events, lo, hi)
    kept, cancelled, called_off, recurring = [], 0, 0, rec_stats["unsupported"]
    for e in events:
        if e.get("status", "").upper() == "CANCELLED":      # a cancelled RECURRENCE-ID override also removes the generated instance (expand_recurring)
            cancelled += 1
            continue
        if CALLED_OFF.search(e["summary"]):                    # a placeholder left in the feed under a "(MOVED TO ...)" / "CANCELED" title
            called_off += 1
            continue
        d = describe(e)
        if d["end_date"] < lo or d["date"] > hi:
            continue
        e = {**e, "description": safety.scrub(e.get("description", ""))[0], "location": safety.scrub(e.get("location", ""))[0]}   # meeting passcodes in invite links
        kept.append({**e, **{k: d[k] for k in ("when", "time", "date", "end_date")}})
    kept.sort(key=lambda e: (e["date"], e["start"] if isinstance(e["start"], datetime) else datetime.min))
    seen, unique = set(), []
    for e in kept:   # the feed repeats an event once per audience; keep one copy
        key = (e["summary"], str(e["start"]), str(e.get("end")), e.get("location", ""))
        if key not in seen:
            seen.add(key)
            unique.append(e)
    duplicates, kept = len(kept) - len(unique), unique

    chunks, by_day = [], defaultdict(list)
    for e in kept:
        lines = [f"Calendar event: {e['summary']}", f"Date: {e['when']}",
                 f"Time: {e['time']} (Pacific)" if not e["all_day"] else "Time: all day"]
        if e.get("location"):
            lines.append(f"Location: {e['location']}")
        if e.get("description"):
            lines.append(f"Details: {e['description'][:600]}")
        content = f"Title: {e['summary']}\nSource: school calendar\n\n" + "\n".join(lines)
        # id from the event's own content, not the feed's UID: the feed repeats events with different UIDs
        # and which copy is kept can change between fetches
        uid = hashlib.sha1(f"{e['summary']}|{e['start']}|{e.get('end')}|{e.get('location', '')}".encode()).hexdigest()[:12]
        extra = {
            "end_date": e["end_date"].isoformat(), "summary": e["summary"], "when": e["when"], "time": e["time"],
            "all_day": e["all_day"],
            "start_time": None if e["all_day"] else e["start"].strftime("%H:%M"),
            "end_time": None if e["all_day"] or not e.get("end") else e["end"].strftime("%H:%M"),
            "location": e.get("location", ""), "description": e.get("description", "")[:300],
            "category": categorize(e["summary"]), "kind": event_kind(e["summary"]),
            "division": calendar_meta.divisions_of(e["summary"]), "no_classes": calendar_meta.no_classes_for(e["summary"]),
            "home_away": "home" if re.search(r"\bhome\b", e["summary"], re.I) else "away" if re.search(r"\baway\b", e["summary"], re.I) else "",
        }
        chunks.append({"chunk_id": f"cal-{uid}", "content": content, "source_file": "school-calendar",
                       "metadata": _meta(e, "calendar", e["summary"], content, extra)})
        d = e["date"]
        while d <= e["end_date"] and (d - e["date"]).days < 14:   # multi-day events show on each day (capped)
            by_day[d].append(e)
            d += timedelta(days=1)

    for d, evs in sorted(by_day.items()):
        head = f"Schedule for {d.strftime('%A, %B %-d, %Y')}"
        parts, cur = [], [head + ":"]
        for e in evs:
            line = f"- {e['summary']} ({'all day' if e['all_day'] else e['time']})" + (f" at {e['location']}" if e.get("location") else "")
            if sum(len(x) + 1 for x in cur) + len(line) > DIGEST_MAX_CHARS:
                parts.append(cur)
                cur = [head + " (continued):"]
            cur.append(line)
        parts.append(cur)
        for n, cur in enumerate(parts):
            content = f"Title: {head}\nSource: school calendar\n\n" + "\n".join(cur)
            fake = {"date": d}
            chunks.append({"chunk_id": f"cal-day-{d:%Y%m%d}-{n}", "content": content, "source_file": "school-calendar",
                           "metadata": _meta(fake, "calendar_day", head, content, {})})

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.CALENDAR_FILE.write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    stats = {"events_in_feed": len(events), "events_kept": len(kept), "duplicates_removed": duplicates, "cancelled": cancelled, "called_off_in_title": called_off,
             "recurring_expanded": rec_stats["expanded"], "recurring_not_expanded": recurring, "chunks": len(chunks),
             "range": f"{kept[0]['date']} .. {kept[-1]['date']}" if kept else None}
    log(f"calendar done: {stats}")
    return stats
