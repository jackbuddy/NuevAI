"""Freshness of indexed text: which school year it was written for and whether it is out of date.

Shared by the ingestion pipeline (writes `school_year`, `stale`, `source_date`, `indexed_at` into chunk metadata)
and by retrieval (rag.py falls back to classifying the chunk text when a row has no metadata yet).

A document is STALE when it clearly IS a version written for an earlier school year than the current one (Aug-Jul). Only the
head of the document (its title and opening lines, where a version label lives) is read:
  * it names school years ("2025-26 Course Catalog", "25-26") and none of them is the current year, or
  * it carries a full calendar date ("Updated May 8, 2025", "4/9/2025") and the latest one is more than a year old.
Passing mentions deeper in a page ("In the 2024-25 school year the library..."), month-and-year dates and bare years never make a
page stale: they are history, not versions.
Structured sources built from this year's workbooks, the live calendar and the confirmed facts are current by construction.
"""
import os
import re
from datetime import date, datetime, timedelta

CURRENT_BY_CONSTRUCTION = {"school_day", "school_courses", "school_clubs", "school_advisory", "school_facts",
                           "calendar", "calendar_day", "calendar_sheet", "link_registry"}

YEAR_RANGE = re.compile(r"(?<!\d)(20\d{2})\s*[-–—/]\s*(?:20)?(\d{2})(?![\d:])")
SHORT_RANGE = re.compile(r"(?<![\d:.$/])(2[3-9])\s*[-–]\s*(2[4-9])(?![\d:%])")
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
MONTH_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(20\d{2})\b", re.I)
HEAD_CHARS = 700            # title + opening lines
DATE_GRACE_DAYS = 365
NUM_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(20\d{2})(?![\d/])")


# Documents known to be replaced by a structured 2026-27 source that carries the same facts (and more): demoted like prior-year ones.
SUPERSEDED = [
    (re.compile(r"clubs\s*(&|and)\s*affinity groups catalog", re.I),
     "an earlier draft of the clubs list; the 2026-27 Clubs Catalog (leaders, advisors, times, rooms) is the current one"),
]


def current_start(today: date | None = None) -> int:
    """Calendar year in which the current school year (Aug-Jul) began. SCHOOL_YEAR_START overrides it."""
    env = os.getenv("SCHOOL_YEAR_START", "").strip()
    if env.isdigit():
        return int(env)
    today = today or date.today()
    return today.year if today.month >= 8 else today.year - 1


def label(start: int) -> str:
    return f"{start}-{(start + 1) % 100:02d}"


def school_years_in(text: str) -> set[int]:
    out = set()
    for a, b in YEAR_RANGE.findall(text):
        if (int(a) + 1) % 100 == int(b):
            out.add(int(a))
    for a, b in SHORT_RANGE.findall(text):
        if int(a) + 1 == int(b):
            out.add(2000 + int(a))
    return out


def full_dates_in(text: str) -> list[date]:
    out = []
    for mon, d, y in MONTH_DATE.findall(text):
        try:
            out.append(date(int(y), MONTHS[mon.lower()[:3]], int(d)))
        except ValueError:
            pass
    for m, d, y in NUM_DATE.findall(text):
        try:
            out.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    return out


def classify(text: str, source: str = "", today: date | None = None, whole_page: bool = False) -> dict:
    """-> {"school_year": "2026-27"|None, "stale": bool, "stale_reason": str}

    whole_page=True reads the entire text (public website pages are evergreen, so a "2025-26" anywhere on one usually marks a
    year-specific statement such as a deadline or a physical-exam date), otherwise only the head (version label) is read."""
    cur = current_start(today)
    if source in CURRENT_BY_CONSTRUCTION:
        return {"school_year": label(cur), "stale": False, "stale_reason": ""}
    text = text if whole_page else text[:HEAD_CHARS]
    for rx, why in SUPERSEDED:
        if rx.search(text):
            return {"school_year": None, "stale": True, "stale_reason": f"superseded: {why}"}
    years = school_years_in(text)
    if years:
        newest = max(years)
        if newest < cur:
            return {"school_year": label(newest), "stale": True, "stale_reason": f"names only the {label(newest)} school year"}
        return {"school_year": label(newest), "stale": False, "stale_reason": ""}
    dates = full_dates_in(text)
    if dates and max(dates) < min(date(cur, 8, 1), (today or date.today()) - timedelta(days=DATE_GRACE_DAYS)):
        latest = max(dates)
        return {"school_year": None, "stale": True, "stale_reason": f"its latest date is {latest.isoformat()}, over a year ago"}
    return {"school_year": None, "stale": False, "stale_reason": ""}


def meta_of(row: dict) -> dict:
    """Freshness of a retrieved row: stored metadata when present, otherwise classified from its text."""
    m = row.get("metadata") or {}
    if "stale" in m:
        return {"school_year": m.get("school_year"), "stale": bool(m.get("stale")), "source_date": m.get("source_date"),
                "reason": m.get("stale_reason", "")}
    c = classify(row.get("content", ""), m.get("source", ""))
    return {"school_year": c["school_year"], "stale": c["stale"], "source_date": m.get("source_date"), "reason": c["stale_reason"]}


def is_public_site(url: str) -> bool:
    """The school's public website (including copies imported through Nexus): evergreen pages, read whole."""
    return "nuevaschool.org" in url and "myschoolapp" not in url


def title_key(title: str) -> str:
    """Title with years removed, so '2025-26 Course Catalog' and '2026-27 Course Catalog' compare equal."""
    t = YEAR_RANGE.sub(" ", title or "")
    t = SHORT_RANGE.sub(" ", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z]+", " ", re.sub(r"(?<!\d)20\d{2}(?!\d)", " ", t.lower()))).strip()


def parse_day(value) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None
