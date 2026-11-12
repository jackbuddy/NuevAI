"""Live Google Sheets stage: public sheets that change during the year are re-read on every run.

  team_schedule  only the configured tabs are fetched (by tab id, so trailing spaces in tab names, and the other tabs
                 in the same workbook - rosters, guest lists, form responses - are never touched)
  workbook       the whole workbook is exported as .xlsx into data/pipeline/live/ for pipeline/workbooks.py

Set LIVE_KINDS=team_schedule to refresh only schedules (the daily GitHub job does this).
"""
import csv
import io
import os
import re
from datetime import datetime, timezone

from . import config
from .common import Fetcher, save_page

LIVE_DIR = config.DATA_DIR / "live"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def tab_ids(f: Fetcher, sheet_id: str) -> dict[str, str]:
    """tab name (stripped) -> gid, from the sheet's public html view."""
    r = f.get(f"https://docs.google.com/spreadsheets/d/{sheet_id}/htmlview", headers=HEADERS)
    if r is None or r.status_code != 200:
        return {}
    return {n.strip(): g for n, g in re.findall(r'items\.push\(\{name: "([^"]+)".*?gid: "(\d+)"', r.text)}


EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def schedule_text(text: str) -> str | None:
    """CSV text of a configured team-schedule tab, or None when it looks like a roster. A schedule tab may carry a side list of other schools'
    coaches/athletic directors with email addresses ("School Options": scheduling notes): every row with an email address is dropped so no
    address or name from it is stored or indexed. A tab whose header is not DATE / HOME TEAM (a roster, a guest list) is refused outright
    when it holds any address."""
    rows = list(csv.reader(io.StringIO(text)))
    head = next((r for r in rows[:6] if any(c.strip().lower() == "date" for c in r)), None)
    if head is None or not any("home" in c.lower() for c in head):
        return None if EMAIL.search(text) else text      # a practice grid or similar: fine only when it holds no address at all
    keep = [r for r in rows if not any(EMAIL.search(c) for c in r)]
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(keep)
    return out.getvalue()


def run(log=print) -> dict:
    kinds = {k.strip() for k in os.getenv("LIVE_KINDS", "team_schedule,workbook").split(",")}
    f = Fetcher(min_delay=1.0)
    saved, problems = [], []
    for spec in config.LIVE_SHEETS:
        if spec["kind"] not in kinds:
            continue
        sid = spec["id"]
        if spec["kind"] == "team_schedule":
            tabs = tab_ids(f, sid)
            for tab, title in spec["tabs"].items():
                gid = tabs.get(tab)
                if not gid:
                    problems.append(f"{title}: tab '{tab}' not found (renamed?)")
                    continue
                r = f.get(f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}", headers=HEADERS)
                text = schedule_text(r.text) if r is not None and r.status_code == 200 else None
                if text is None:                                               # a roster or an unreadable tab: never index it
                    problems.append(f"{title}: not readable or looks like a roster (HTTP {getattr(r, 'status_code', '?')})")
                    continue
                save_page({"url": f"https://docs.google.com/spreadsheets/d/{sid}/edit#gid={gid}", "title": title,
                           "source": "google_sheet", "text": text, "links": [],
                           "scraped_at": datetime.now(timezone.utc).isoformat()})
                saved.append(title)
        elif spec["kind"] == "workbook":
            r = f.get(f"https://docs.google.com/spreadsheets/d/{sid}/export?format=xlsx", headers=HEADERS)
            if r is None or r.status_code != 200 or r.content[:2] != b"PK":
                problems.append(f"{spec['name']}: could not download (is it shared with 'anyone with the link'?)")
                continue
            LIVE_DIR.mkdir(parents=True, exist_ok=True)
            (LIVE_DIR / f"{spec['name']}.xlsx").write_bytes(r.content)
            saved.append(spec["name"])
    stats = {"fetched": saved, "problems": problems}
    log(f"live sheets done: {stats}")
    return stats
