"""Read side of the cafe lunch menu: exact lookups of the day records written by pipeline/lunch.py. No LLM, no embeddings.

Used by rag.py (the chat answer's context) and by GET /lunch (query_server.py). A day record is a row of the chunk table with
metadata.source = "lunch", metadata.campus in {san_mateo, hillsborough}, metadata.event_date = the day.

Honesty rules baked in here:
  * a month with no rows is "not posted yet" (month_available False), never a different month's menu
  * a weekend, or a weekday the cafe's calendar shows a notice instead of a menu for, has available=False and a closed_reason
  * the dishes are returned exactly as the cafe lists them; no dietary tag is ever added
"""
import re
from datetime import date, datetime, timedelta

import settings
from cache import TTLCache

CAMPUSES = ("san_mateo", "hillsborough")
CAMPUS_NAME = {"san_mateo": "San Mateo (Upper School)", "hillsborough": "Hillsborough (Lower and Middle School)"}
NOTE = "The menu can change; check with the cafe for ingredients or allergens."
CAFE_URL = "https://nuevaschoolcafe.com/#our-menu"
GENERIC_CLOSED = "No lunch menu is posted for this day (no school or a professional day)."

_cache = TTLCache(max_items=256, ttl=300)


def valid_campus(c) -> str | None:
    """'san_mateo' | 'hillsborough' | None. Anything else (wrong case, other strings, other types) is ignored, never an error."""
    return c if isinstance(c, str) and c in CAMPUSES else None


def month_label(d: date) -> str:
    return d.strftime("%B %Y")


def load_month(sb, campus: str, month: str) -> dict[str, dict]:
    """event_date -> {items, closed, source_url, month_label, pdf_name} for one campus and one 'YYYY-MM' (cached 5 min)."""
    key = ("month", campus, month)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    rows = sb.table(settings.CHUNKS_TABLE).select("chunk_id,metadata").eq("metadata->>source", "lunch").eq("metadata->>campus", campus) \
        .eq("metadata->>month", month).limit(100).execute().data or []
    out = {}
    for r in rows:
        m = r.get("metadata") or {}
        if m.get("superseded") or not m.get("event_date"):
            continue
        out[m["event_date"]] = {"items": list(m.get("items") or []), "closed": bool(m.get("closed")), "source_url": m.get("source_url") or CAFE_URL,
                                "month_label": m.get("month_label") or "", "pdf_name": m.get("pdf_name") or "", "hours": m.get("hours")}
    _cache.set(key, out)
    return out


def cafe_hours(sb, campus: str, day: date) -> str | None:
    """'11:00am-1:00pm' as printed in that month's menu header, or None."""
    for rec in load_month(sb, campus, day.strftime("%Y-%m")).values():
        if rec.get("hours"):
            return rec["hours"]
    return None


def available_months(sb, campus: str) -> list[str]:
    """Months (YYYY-MM) that have a posted menu for the campus, oldest first (cached 5 min)."""
    key = ("months", campus)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    rows = sb.table(settings.CHUNKS_TABLE).select("m:metadata->>month").eq("metadata->>source", "lunch").eq("metadata->>campus", campus).limit(1000).execute().data or []
    months = sorted({r["m"] for r in rows if r.get("m")})
    _cache.set(key, months)
    return months


NO_CLASS_LINE = re.compile(r"\bno (?:school|classes|class)\b|\bno (?:ls|ms|us)\b", re.I)


def closure_reason(sb, campus: str, day: date) -> str | None:
    """Why the cafe has no menu for a day, from the school calendar data already in the index (never invented):
    San Mateo: the master schedule's day record ("No classes (Faculty Work Day)"); both campuses: a 'No school / No classes' calendar line
    that names the division (or none)."""
    key = ("why", campus, day.isoformat())
    hit = _cache.get(key)
    if hit is not None:
        return hit or None
    why = ""
    try:
        rows = sb.table(settings.CHUNKS_TABLE).select("content,metadata").in_("metadata->>source", ["school_day", "calendar_day"]) \
            .eq("metadata->>event_date", day.isoformat()).limit(10).execute().data or []
        div = {"san_mateo": r"\bUS\b|upper school|San Mateo|^No (School|Classes)", "hillsborough": r"\bLS\b|\bMS\b|lower school|middle school|^No (School|Classes)"}[campus]
        for r in rows:
            m, text = r.get("metadata") or {}, r["content"].split("\n\n", 1)[-1]
            if m.get("source") == "school_day" and campus == "san_mateo" and m.get("kind") == "no_classes":
                mm = re.search(r"No classes(?: \(([^)]+)\))?", text)
                why = f"No classes ({mm.group(1)})" if mm and mm.group(1) else "No classes"
                break
            if m.get("source") == "calendar_day":
                for ln in text.splitlines():
                    ln = ln.lstrip("- ").strip()
                    if NO_CLASS_LINE.search(ln) and re.search(div, ln, re.I | re.M):
                        why = re.sub(r"\s*\((?:all day|\d.*?)\)\s*(?:at .*)?$", "", ln).strip()
                        break
            if why:
                break
    except Exception:
        why = ""
    _cache.set(key, why, ttl=1800)
    return why or None


def day_info(sb, campus: str, day: date) -> dict:
    """The /lunch response for one campus and one day:
    {date, campus, items, note, available, month_available, month_label, source_url, closed_reason}"""
    rows = load_month(sb, campus, day.strftime("%Y-%m"))
    month_ok = bool(rows)
    rec = rows.get(day.isoformat())
    out = {"date": day.isoformat(), "campus": campus, "items": [], "note": None, "available": False, "month_available": month_ok,
           "month_label": month_label(day), "source_url": (rec or next(iter(rows.values()), {})).get("source_url") if month_ok else CAFE_URL,
           "closed_reason": None}
    if out["source_url"] is None:
        out["source_url"] = CAFE_URL
    if not month_ok:
        out["closed_reason"] = f"The {out['month_label'].split()[0]} menu isn't posted yet."
        return out
    if day.weekday() >= 5:
        out["closed_reason"] = "Weekend: no lunch service."
        return out
    if rec and rec["items"] and not rec["closed"]:
        out.update(items=rec["items"], note=NOTE, available=True)
        return out
    out["closed_reason"] = (closure_reason(sb, campus, day) if rec and rec["closed"] else None) or GENERIC_CLOSED
    return out


def search_days(sb, campus: str, words: list[str], start: date, limit: int = 8) -> list[tuple[str, dict]]:
    """Days from `start` on (in the posted months) whose dishes contain every word: 'when is pizza day' -> [(date, record), ...]."""
    out = []
    for month in available_months(sb, campus):
        if month < start.strftime("%Y-%m"):
            continue
        for dkey, rec in sorted(load_month(sb, campus, month).items()):
            if dkey < start.isoformat() or rec["closed"]:
                continue
            blob = " ".join(rec["items"]).lower()
            if all(w in blob for w in words):
                out.append((dkey, rec))
    return out[:limit]


def today_pacific() -> date:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Los_Angeles")).date()
