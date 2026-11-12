#!/usr/bin/env python
"""Markdown health summary of one 'Refresh calendar' run, for the GitHub job summary page.

    python scripts/ci_summary.py >> "$GITHUB_STEP_SUMMARY"

Reads what the run left in data/ (pipeline report.json, audit.json, the index_guard counts) and prints row counts, calendar events indexed and
the window they cover, what the index stage removed or kept, and the audit result. It never fails the job (always exit 0) and tolerates missing
files (a run that died early still gets a summary that says where). Nothing secret is read or printed.
"""
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
PIPE = DATA / "pipeline"


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def count(path: Path):
    try:
        return int(path.read_text().strip())
    except Exception:
        return None


def build() -> str:
    rep, aud = load(PIPE / "report.json") or {}, load(PIPE / "audit.json")
    st = rep.get("stages", {})
    out = ["## Refresh calendar: health", ""]
    err = rep.get("error")
    if not rep:
        out += ["**Pipeline: no report was written** (the job died before or inside the first stage; see the step log).", ""]
    elif err:
        out += [f"**Pipeline FAILED in stage `{err.get('stage_failed')}`** ({err.get('type')}). The index was not changed by later stages.", ""]
    else:
        out += ["**Pipeline: all stages finished.**", ""]

    before, after = count(DATA / "index_count_before.txt"), count(DATA / "index_count_after.txt")
    rows = []
    if before is not None:
        change = f" ({(after - before) / before:+.1%})" if after is not None and before else ""
        rows.append(("Index rows (pipeline-owned, emails excluded)", f"{before} before, {after if after is not None else '?'} after{change}"))
    cal = st.get("calendar") or {}
    if cal:
        if cal.get("status"):
            rows.append(("Calendar feed", cal["status"]))
        else:
            rows += [("Calendar events indexed", f"{cal.get('events_kept')} (feed has {cal.get('events_in_feed')} events; {cal.get('duplicates_removed')} audience duplicates merged, "
                                                  f"{cal.get('cancelled')} cancelled, {cal.get('recurring_expanded', 0)} recurring expanded, {cal.get('recurring_not_expanded', 0)} unsupported)"),
                     ("Calendar window (event dates)", cal.get("range") or "none"), ("Calendar chunks written", cal.get("chunks"))]
    lunch = st.get("lunch") or {}
    if lunch:
        months = lunch.get("months_available") or []
        rows.append(("Lunch menus", f"{lunch.get('chunks')} rows from {len(lunch.get('menus_parsed') or [])} menus; months available: {', '.join(map(str, months)) or 'none'}"
                                    + (f"; problems: {len(lunch.get('problems') or [])}" if lunch.get("problems") else "")))
    live = st.get("live") or {}
    if live:
        rows.append(("Live team sheets", f"{len(live.get('fetched') or [])} read ({', '.join(live.get('fetched') or [])})" + (f"; PROBLEMS: {live.get('problems')}" if live.get("problems") else "")))
    idx = st.get("index") or {}
    if idx:
        rows.append(("Index plan", f"{idx.get('total_chunks')} chunks, {idx.get('to_embed')} new or changed embedded, {idx.get('unchanged')} unchanged"))
        rows.append(("Calendar rows removed", f"{idx.get('expired_calendar', 0)} {idx.get('calendar_removals_by_reason') or ''}".strip()))
        rows.append(("Calendar history kept", f"{idx.get('calendar_history_kept', 0)} past rows of this school year that left their source"))
        if idx.get("calendar_removal_refused"):
            rows.append(("WARNING", f"{idx.get('calendar_removals_held_back')} calendar removals were REFUSED (more than 25% of the calendar rows would go: truncated feed or sheet?). Only rows older than the school year were removed."))
        if idx.get("orphans_to_flag"):
            rows.append(("Workbook rows flagged superseded", idx["orphans_to_flag"]))
    if rows:
        out += ["| What | Value |", "|---|---|"] + [f"| {a} | {b} |" for a, b in rows] + [""]

    out.append("### Audit (CI mode)")
    if aud is None:
        out += ["No audit report (the audit step did not run or crashed).", ""]
    else:
        n = lambda s: sum(1 for d in aud.values() if d.get("status") == s)
        fails = {k: d for k, d in aud.items() if d.get("status") == "FAIL"}
        warns = {k: d for k, d in aud.items() if d.get("status") == "warn"}
        out += [f"**{'FAILED' if fails else 'passed'}**: {len(aud)} groups, {sum(d.get('checks', 0) for d in aud.values())} checks; "
                f"{n('ok')} ok, {n('known')} accepted, {n('warn')} warnings, {n('FAIL')} failed, {n('SKIP')} skipped (skipped in CI: workbooks, clubs, courses, orphans, canonical lists are checked by the owner's local run).", ""]
        for title, grp in (("Failed", fails), ("Warnings (need a person, do not fail the job)", warns)):
            for k, d in grp.items():
                out += [f"- {title}: `{k}`: {len(d['problems'])} problem(s)"] + [f"  - {p[:220]}" for p in d["problems"][:5]]
        notes = [(k, i) for k, d in aud.items() for i in d.get("info", []) if "list" in i.lower() and "twice" in i.lower() or "kept as history" in i]
        for k, i in notes[:6]:
            out.append(f"- info `{k}`: {i[:220]}")
        skipped_other = {k: d["skip"] for k, d in aud.items() if d.get("status") == "SKIP" and "skipped in CI" not in (d.get("skip") or "")}
        for k, why in skipped_other.items():
            out.append(f"- skipped `{k}`: {why}")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    try:
        print(build())
    except Exception as e:                      # a summary problem must never fail the refresh
        print(f"(summary could not be built: {type(e).__name__})")
    sys.exit(0)
