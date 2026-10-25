"""Calendar stage: the whole-school iCal feed (sports, events, closures).

The feed URL carries a private access token, so it is read from the
NUEVAI_CALENDAR_URL environment variable (.env locally, a GitHub secret in CI),
never from code. No login is involved. webcal:// is accepted and treated as https://.

Each event becomes a retrievable chunk with an unambiguous date, and every day
that has events also gets a digest chunk ("Schedule for Tuesday, October 28,
2026: ...") so questions like "what's happening Oct 28" retrieve the whole day.
Events from before CALENDAR_KEEP_PAST_DAYS ago are dropped, and the indexer
removes expired calendar chunks from Supabase automatically.
"""
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

from . import config

KEEP_PAST_DAYS = 14
KEEP_FUTURE_DAYS = 430
DIGEST_MAX_CHARS = 1500

CATEGORY_RULES = [
    ("no_school", re.compile(r"\bno school\b|\bbreak\b|\bholiday\b|\bcampus closed\b|\bschool closed\b|\bearly (release|dismissal)\b", re.I)),
    ("athletics", re.compile(r"-\s*(game|match|meet|scrimmage)\b|\b(practice|tournament|invitational|athlet\w*|basketball|soccer|swim\w*|tennis|"
                             r"volleyball|baseball|softball|golf|cross country|track|lacrosse|badminton|water polo|ultimate|fencing|"
                             r"sports?|varsity|jv)\b", re.I)),
    ("community", re.compile(r"\b(parent|coffee|social|night|celebration|festival|carnival|assembly|fair|fundrais\w*|gala|brunch|picnic|"
                             r"volunteer|alumni|admissions|open house)\b", re.I)),
    ("academics", re.compile(r"\b(exam|finals?|midterm|testing|erb|report cards?|conferences?|registration|semester|trimester|"
                             r"intersession|deadline|last day|first day|orientation|course|class of|college|advisory|schedule)\b", re.I)),
    ("arts", re.compile(r"\b(concert|play|theat(er|re)|musical|recital|performance|exhibit|art show|open stage|dance|choir|orchestra|band)\b", re.I)),
]


def categorize(summary: str) -> str:
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
    """-> (datetime|date, is_all_day). Times are returned as naive Pacific time."""
    v = value.strip()
    if "VALUE=DATE" in params or re.fullmatch(r"\d{8}", v):
        return datetime.strptime(v[:8], "%Y%m%d").date(), True
    dt = datetime.strptime(v.rstrip("Z"), "%Y%m%dT%H%M%S")
    if v.endswith("Z"):
        from zoneinfo import ZoneInfo
        dt = dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Los_Angeles")).replace(tzinfo=None)
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
                    cur["recurring"] = True      # not expanded; reported
            except ValueError:
                pass
    return events


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
    today = date.today()
    lo, hi = today - timedelta(days=KEEP_PAST_DAYS), today + timedelta(days=KEEP_FUTURE_DAYS)
    kept, cancelled, recurring = [], 0, 0
    for e in events:
        if e.get("status", "").upper() == "CANCELLED":
            cancelled += 1
            continue
        recurring += bool(e.get("recurring"))
        d = describe(e)
        if d["end_date"] < lo or d["date"] > hi:
            continue
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
    stats = {"events_in_feed": len(events), "events_kept": len(kept), "duplicates_removed": duplicates, "cancelled": cancelled,
             "recurring_not_expanded": recurring, "chunks": len(chunks),
             "range": f"{kept[0]['date']} .. {kept[-1]['date']}" if kept else None}
    log(f"calendar done: {stats}")
    return stats
