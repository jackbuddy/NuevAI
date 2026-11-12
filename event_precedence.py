"""Which copy of a team event wins when the same game / practice is listed more than once.

Team schedules reach the index from several places: the daily pipeline's public team tabs (rows `sheet-*`, `source_date` = the day the tab was
last downloaded), Google Sheets shared with the ingest account (`share-*`, `modified_time` = Drive's last-modified time) and school emails
(`email-*`, `source_date` = the day it was sent). The same game can appear in more than one of them.

Rule (documented in README > Auto-ingestion): events are the same when they have the same team (the words of the team label without division /
season / 'schedule' noise), the same date, the same kind and the same opponent (or note). Of a group the NEWEST source wins, compared by day:
  * shared sheet:  its Drive last-modified day
  * email:         the day it was sent
  * public tab:    the day the pipeline last downloaded it
On a tie the shared sheet or email wins over the public tab (it was handed over deliberately); between two shared files the later full
modifiedTime wins. Rows flagged superseded (a thread that was updated, a file that is no longer shared) never show.

Used at read time by rag.py so no row of another feature is ever modified or deleted to resolve a duplicate.
"""
import re

_DROP = {"us", "ms", "ls", "nueva", "school", "upper", "middle", "lower", "schedule", "schedules", "season", "team", "the", "game", "games",
         "practice", "practices", "vs", "at", "and", "of", "co", "ed", "tab", "sheet", "copy"}


def team_tokens(summary: str) -> frozenset:
    team = re.split(r"\s+-\s+(?:Game|Practice)\b", summary or "", maxsplit=1)[0]
    team = team.split(" - ")[0]
    return frozenset(t for t in re.findall(r"[a-z0-9]+", team.lower()) if t not in _DROP and not t.isdigit())


def what(summary: str, kind: str) -> str:
    s = summary or ""
    if kind == "game":
        m = re.search(r"-\s+Game\s+(.*?)(?:\s+-\s+(?:Home|Away))?\s*$", s)
        s = m.group(1) if m else s
    elif kind == "practice":
        return "practice"
    else:
        s = s.split(" - ", 1)[1] if " - " in s else s
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _origin(row: dict) -> str:
    cid = str(row.get("chunk_id") or "")
    return "share" if cid.startswith("share-") else "email" if cid.startswith("email-") else "other"


def _stamp(row: dict) -> tuple:
    m = row.get("metadata") or {}
    o = _origin(row)
    full = str(m.get("modified_time") or m.get("source_date") or "")
    return (str(m.get("source_date") or full[:10])[:10], 1 if o in ("share", "email") else 0, full)


def dedupe_events(rows: list[dict]) -> list[dict]:
    """Drop superseded rows and, among calendar_sheet rows that describe the same event, every copy but the winner. Order is preserved."""
    rows = [r for r in rows if not (r.get("metadata") or {}).get("superseded")]
    best: dict[tuple, dict] = {}
    for r in rows:
        m = r.get("metadata") or {}
        if m.get("source") != "calendar_sheet" or not m.get("event_date"):
            continue
        toks = team_tokens(m.get("summary", ""))
        if not toks:
            continue
        k = (toks, m["event_date"], m.get("kind", ""), what(m.get("summary", ""), m.get("kind", "")))
        cur = best.get(k)
        if cur is None or _stamp(r) > _stamp(cur):
            best[k] = r
    out = []
    for r in rows:
        m = r.get("metadata") or {}
        if m.get("source") == "calendar_sheet" and m.get("event_date"):
            toks = team_tokens(m.get("summary", ""))
            if toks and best.get((toks, m["event_date"], m.get("kind", ""), what(m.get("summary", ""), m.get("kind", "")))) is not r:
                continue
        out.append(r)
    return out
