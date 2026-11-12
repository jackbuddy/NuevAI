#!/usr/bin/env python
"""Source-of-truth audit: re-derive facts from the source files and compare them with what the index (and bot) holds.

    python scripts/audit_data.py                     # everything; reads Supabase (read-only SELECTs) and the iCal feed
    python scripts/audit_data.py --offline           # source files only (no database, feed or sheet fetch)
    python scripts/audit_data.py --db-json dump.json # use a saved dump of the chunk table instead of Supabase
    python scripts/audit_data.py --today 2026-10-25  # pin "today" (calendar window, freshness) for a repeatable run
    python scripts/audit_data.py -v                  # print every mismatch, not the first few

What is checked (each line of output is a group of checks):
  days.*        A/B/A'/B' day and special events for every school date: two tabs of the master sheet against each other,
                against the pipeline parser, and against the indexed day records; advisory times by grade
  bell.*        bell times and A'/B' order: knowledge file vs pipeline constants vs system prompt vs index vs day records
  courses.*     teacher-course-block-room records from the Roles tabs vs the index; Rooms tab vs Roles tab; Course Offerings
                workbook vs Roles; offerings vs index; live copy of the offerings workbook vs the local copy
  clubs.*       every club (leaders, advisor, day, time, room) vs the index; clubs lists in other documents
  teams.*       team schedule sheets (US basketball ...) vs the index; live tabs vs saved copies (online)
  ical.*        the private iCal feed vs the index: counts, dates, timezone, all-day handling, duplicates, digests
  fresh.*       freshness metadata present and correct, prior-year documents flagged, same document in two years,
                contradicting facts between sources
  lunch.*       the cafe's monthly lunch rows (chunk_id "lunch-<campus>-<date>"): metadata and freshness present, valid campus, no duplicate
                dates, every weekday of each posted month present per campus, record text consistent with its items, index vs the parsed PDF;
                lunch served on a no-classes day is listed as information only
  canon.*       canonical lists (knowledge/*.md, pipeline/canonical.py): every fact verified against its stored source page, source/as-of/school
                year on every record, sports offerings vs the teams that have schedules (and the not-offered list vs both), clubs and courses
                directories vs their workbook/catalog, no-class days vs the master schedule, contacts (business addresses only), and index
                hygiene (no unflagged credential, duplicate copy or missing refreshed public page)
  share.*       rows written by the Drive ingest (chunk_id "share-..."): metadata sanity, no file links, privacy
  email.*       rows written by the email ingest (chunk_id "email-..."): metadata sanity (source_type, dates, hashes, no raw sender address)
                and no non-staff email address or phone number in the text. They are kept out of every other group (they are not part of
                the pipeline's plan, so they are never orphans or "missing freshness")

--ci runs only the groups that are meaningful on a GitHub runner (no local workbooks, saved pages or chunk files: bell.*, teams.* against the
live sheets, ical.*, fresh.*, email.*, lunch.*) and skips the rest with a "skipped in CI" note. In CI mode only a "mismatch" (the index disagrees
with its source) or a crash fails the run; a "conflict" (two sources disagree, needs a person) or an "orphan" is a warning annotation; a database
that cannot be read fails; a source that cannot be fetched (feed, sheet: a network flake) is a warning.

Exit status 1 when any group has a mismatch beyond what scripts/audit_known.json accepts; 2 on a crash. A "conflict" is two
sources disagreeing with each other (needs a person to decide); a "mismatch" is the index disagreeing with its source (fixable).
Nothing is written anywhere except the optional --json report. The feed URL is read from NUEVAI_CALENDAR_URL and never printed.
"""
import argparse
import collections
import csv
import hashlib
import io
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openpyxl                     # noqa: E402
import freshness                    # noqa: E402
from pipeline import calendar_feed, config, workbooks   # noqa: E402

KNOWN_FILE = ROOT / "scripts" / "audit_known.json"
CAL_SOURCES = ("calendar", "calendar_day", "calendar_sheet")
STRUCTURED = ("school_day", "school_courses", "school_clubs", "school_advisory", "school_facts", "school_canonical", "link_registry") + CAL_SOURCES


# --------------------------------------------------------------------------- reporting
class Audit:
    WARN_KINDS = ("conflict", "orphan", "info")      # in CI mode: need a person (or a local run), never fail the unattended job

    def __init__(self, known: dict, verbose=False, ci=False):
        self.known, self.verbose, self.ci = known, verbose, ci
        self.g: dict[str, dict] = collections.OrderedDict()

    def grp(self, name):
        return self.g.setdefault(name, {"n": 0, "bad": [], "skip": None, "info": []})

    def check(self, g, cond, msg, kind="mismatch"):
        d = self.grp(g)
        d["n"] += 1
        if not cond:
            d["bad"].append((kind, msg))
        return cond

    def passed(self, g, n=1):
        self.grp(g)["n"] += n

    def skip(self, g, why):
        self.grp(g)["skip"] = why

    def info(self, g, msg):
        self.grp(g)["info"].append(msg)

    def status(self, name):
        d = self.g[name]
        if d["skip"]:
            return "SKIP"
        if not d["bad"]:
            return "ok"
        if self.ci:
            hard = [b for b in d["bad"] if b[0] not in self.WARN_KINDS]
            if not hard:
                return "warn"
            return "known" if len(hard) <= int(self.known.get(name, {}).get("max", 0)) else "FAIL"
        return "known" if len(d["bad"]) <= int(self.known.get(name, {}).get("max", 0)) else "FAIL"

    def report(self, out=print):
        total = bad = 0
        for name, d in self.g.items():
            st = self.status(name)
            total += d["n"]
            n_bad = len(d["bad"])
            bad += n_bad if st == "FAIL" else 0
            if self.ci and os.getenv("GITHUB_ACTIONS"):          # annotations show on the run page
                for kind, msg in d["bad"][:5]:
                    lvl = "error" if st == "FAIL" and kind not in self.WARN_KINDS else "warning"
                    out(f"::{lvl} title=audit {name} ({kind})::{msg[:300]}".replace("\n", " "))
                if d["skip"]:
                    out(f"::notice title=audit {name} skipped::{d['skip']}")
            kinds = collections.Counter(k for k, _ in d["bad"])
            extra = f"  [{', '.join(f'{v} {k}' for k, v in kinds.items())}]" if kinds else ""
            why = f"  ({d['skip']})" if d["skip"] else ""
            out(f"{st:5} {name:34} checks={d['n']:<6} problems={n_bad}{extra}{why}")
            if st == "known":
                out(f"        accepted: {self.known[name].get('reason', '')}")
            for kind, msg in (d["bad"] if self.verbose or st == "FAIL" and n_bad <= 12 else d["bad"][:6]):
                out(f"        - {kind}: {msg}")
            if n_bad > (len(d["bad"]) if self.verbose else 6) and not (st == "FAIL" and n_bad <= 12):
                out(f"        ... {n_bad - 6} more (-v to list)")
            for m in d["info"][: (50 if self.verbose else 3)]:
                out(f"        info: {m}")
        fails = [n for n in self.g if self.status(n) == "FAIL"]
        out(f"\n{len(self.g)} groups, {total} checks, {sum(len(d['bad']) for d in self.g.values())} problems "
            f"({sum(len(self.g[n]['bad']) for n in fails)} unaccepted in {len(fails)} groups), "
            f"{sum(1 for n in self.g if self.status(n) == 'SKIP')} skipped")
        return not fails

    def as_json(self):
        return {n: {"status": self.status(n), "checks": d["n"], "problems": [f"{k}: {m}" for k, m in d["bad"]],
                    "skip": d["skip"], "info": d["info"]} for n, d in self.g.items()}


# --------------------------------------------------------------------------- helpers
def cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return re.sub(r"[ \t]+", " ", str(v).replace("\xa0", " ")).strip()


def grid(ws):
    return [list(r) for r in ws.iter_rows(values_only=True)]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower().replace("&", " and ").replace("+", " and "))).strip()


def flat(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'")).strip()


def fix_name(n: str) -> str:
    return {"Jeremy Jaquot": "Jeremy Jacquot"}.get(n, n)      # spelling confirmed by the school (see HANDOFF)


def edit1(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) < 5 or len(b) < 5 or a[0] != b[0] or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    s, l = (a, b) if len(a) < len(b) else (b, a)
    i = j = diff = 0
    while i < len(s) and j < len(l):
        if s[i] == l[j]:
            i += 1
        else:
            diff += 1
            if diff > 1:
                return False
        j += 1
    return True


def last_of(name: str) -> str:
    return norm(name).split(" ")[-1] if name else ""


def load_wb(path: Path):
    return openpyxl.load_workbook(str(path), data_only=True)


# --------------------------------------------------------------------------- sources
class Sources:
    def __init__(self, args):
        self.args, self.today = args, args.today
        found = workbooks.find_workbooks()
        self.master_path = (found.get("master") or [None])[0]
        self.off_path = (found.get("offerings") or [None])[0]
        self.clubs_path = (found.get("clubs") or [None])[0]
        self.master = load_wb(self.master_path) if self.master_path else None
        self.off = load_wb(self.off_path) if self.off_path else None
        self.clubs = load_wb(self.clubs_path) if self.clubs_path else None
        self.db: list[dict] | None = None
        self.db_err = ""
        if args.db_json:
            self.db = json.loads(Path(args.db_json).read_text())
        elif not args.offline:
            try:
                self.db = fetch_db()
            except Exception as e:                  # no network / no key: the database groups are skipped
                self.db_err = f"{type(e).__name__}: {str(e)[:100]}"
        self.bell = parse_bell_md()
        try:
            self.local_chunks = json.loads(config.CHUNKS_FILE.read_text(encoding="utf-8")) if config.CHUNKS_FILE.exists() else []
        except Exception:
            self.local_chunks = []
        # rows of forwarded emails are audited by audit_email only; the pipeline-vs-index groups must not see them (they are not in its plan)
        self.email_db = [r for r in (self.db or []) if freshness.is_email_row(r["chunk_id"], None, r.get("metadata"))]
        self.share_db = [r for r in (self.db or []) if freshness.is_share_row(r["chunk_id"], None, r.get("metadata"))]
        if self.db is not None:
            self.db = [r for r in self.db if not freshness.is_ingested_row(r["chunk_id"], None, r.get("metadata"))]
        self.db_all = self.db
        if self.db is not None:                 # rows flagged by scripts/flag_rows.py are ignored by retrieval, so the content groups ignore them too
            self.db = [r for r in self.db if not (r["metadata"] or {}).get("flagged_at")]

    def current(self):
        """Rows the current pipeline output still contains. Rows it no longer produces (superseded copies, renamed records) are
        orphans: they wait for a prune, which this audit never performs."""
        ids = {c["chunk_id"] for c in self.local_chunks}
        return [r for r in (self.db or []) if not (r["metadata"] or {}).get("flagged_at")           # flagged by scripts/flag_rows.py: retrieval ignores them
                and (not ids or r["chunk_id"] in ids or (r["metadata"] or {}).get("source") in CAL_SOURCES)]

    def orphans(self):
        ids = {c["chunk_id"] for c in self.local_chunks}
        return [] if not ids else [r for r in (self.db or []) if r["chunk_id"] not in ids and (r["metadata"] or {}).get("source") not in CAL_SOURCES]

    def db_by(self, source=None, kind=None):
        return [r for r in self.current() if (source is None or (r["metadata"] or {}).get("source") == source)
                and (kind is None or (r["metadata"] or {}).get("kind") == kind)]


def fetch_db() -> list[dict]:
    from dotenv import load_dotenv
    from supabase import create_client
    load_dotenv(ROOT / ".env")
    url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
    sb = create_client(url.strip().strip("\"'"), key.strip().strip("\"'"))
    rows, start = [], 0
    while True:                                    # read-only SELECTs, without the embedding column
        r = sb.table("document_chunks_v2").select("chunk_id,content,source_file,metadata").range(start, start + 999).execute().data
        rows += r
        if len(r) < 1000:
            return rows
        start += 1000


def parse_bell_md() -> dict:
    p = ROOT / "knowledge" / "bell_schedule.md"
    out = {"blocks": {}, "lunch": None, "orders": {}}
    if not p.exists():
        return out
    t = p.read_text(encoding="utf-8")
    for a, b, times in re.findall(r"^- Blocks (\d) and (\d): ([\d:]+-[\d:]+ [AP]M)", t, re.M):
        out["blocks"][int(a)] = out["blocks"][int(b)] = times
    m = re.search(r"starts at ([\d:]+ [AP]M) and ends at ([\d:]+) or ([\d:]+ [AP]M)", t)
    if m:
        out["lunch"] = (m.group(1), m.group(2), m.group(3))
    for d, seq in re.findall(r"^- (A'?|B'?) day order: (.+)$", t, re.M):
        out["orders"][d] = [int(x) for x in re.findall(r"Block (\d)", seq)]
    return out


# --------------------------------------------------------------------------- days
def read_day_grid(ws) -> dict[date, dict]:
    """date -> {"type": 'A', "label": 'Community (90')'} from a week-grid tab (3 rows per week: numbers, event, type)."""
    rows = [r + [None] * 8 for r in grid(ws)]
    out, ym = {}, None
    for i, r in enumerate(rows):
        if isinstance(r[1], datetime) and not any(cell(r[j]) for j in range(2, 6)):
            ym = (r[1].year, r[1].month)
            continue
        nums = {j: int(r[j]) for j in range(1, 6) if isinstance(r[j], (int, float)) and r[j] == int(r[j]) and 1 <= r[j] <= 31}
        if nums:                  # a date cell with text typed into it ("24 Community (90')") is a string: still that day number
            for j in range(1, 6):
                m = re.match(r"\s*(\d{1,2})\b", r[j]) if isinstance(r[j], str) else None
                if m and j not in nums and 1 <= int(m.group(1)) <= 31:
                    nums[j] = int(m.group(1))
        if not (nums and ym) or i + 2 >= len(rows):
            continue
        for j, dn in nums.items():
            try:
                d = date(ym[0], ym[1], dn)
            except ValueError:
                continue
            if d.weekday() != j - 1:
                continue
            out[d] = {"label": flat(cell(rows[i + 1][j])).replace("′", "'"), "type": cell(rows[i + 2][j]).replace("’", "'").replace("′", "'")}
    return out


NO_CLASS = re.compile(r"no class", re.I)
FULL_DAY = re.compile(r"^full-day", re.I)
LAG_MAX = 12
CI_GROUPS = ("bell", "teams", "ical", "fresh", "email", "lunch")
CI_SKIPPED = {          # groups that need files only the owner's machine has (workbooks, saved pages, chunks.json); run `python scripts/audit_data.py` locally for them
    "days": [("days.sheet", "needs the master-schedule workbook, which is downloaded by hand")],
    "courses": [("courses.index", "needs the master-schedule and course-offerings workbooks")],
    "clubs": [("clubs.index", "needs the clubs workbook")],
    "index": [("index.orphan_rows", "compares the table with a full local pipeline run; CI only builds the calendar, lunch, live-sheet and knowledge-file part")],
    "canon": [("canon.*", "needs the saved source pages and the full local chunk list")],
}
SHORT_DAY = re.compile(r"dismissal|early release", re.I)


def audit_days(A: Audit, S: Sources):
    g = "days.sheet"
    if S.master is None:
        return A.skip(g, "master schedule workbook not found in data/pipeline")
    tab1 = read_day_grid(S.master["Calendar AB + Special Schedule"])
    tab2 = read_day_grid(S.master["Master Calendar"]) if "Master Calendar" in S.master.sheetnames else {}
    A.check(g, len(tab1) > 150, f"only {len(tab1)} dates read from the calendar tab")
    for d in sorted(set(tab1) | set(tab2)):
        a, b = tab1.get(d), tab2.get(d)
        A.check(g, a is not None and b is not None, f"{d} present in only one calendar tab ({'AB+Special' if a else 'Master Calendar'})", "conflict")
        if a and b:
            A.check(g, norm(a["type"]).replace(" ", "") == norm(b["type"]).replace(" ", ""), f"{d}: 'Calendar AB + Special Schedule' says {a['type']!r}, 'Master Calendar' says {b['type']!r}", "conflict")
    for d, v in tab1.items():
        A.check(g, v["type"] in workbooks.DAY_TYPES or v["type"] == "" or NO_CLASS.search(v["label"]) or FULL_DAY.search(v["label"]) or SHORT_DAY.search(v["type"] + v["label"]),
                f"{d}: day type {v['type']!r} / label {v['label']!r} is neither an A/B day, a no-classes day, a full-day event nor an early dismissal")
    types = collections.Counter(v["type"] for v in tab1.values())
    A.info(g, f"{len(tab1)} dates; day types: {dict(types)}")
    # every weekday inside the year has a record (class day, no-classes day or a labelled event)
    adv = workbooks.parse_advisory(S.master)
    first, last = min(tab1), max(tab1)
    gaps = [d for d in (first + timedelta(n) for n in range((last - first).days + 1)) if d.weekday() < 5 and d not in tab1 and d not in adv]
    for d in gaps:
        A.check(g, False, f"{d} ({d:%a}) is a weekday between {first} and {last} with no entry in the calendar", "conflict")
    A.passed(g, (last - first).days)

    # pipeline parser vs the independent reading
    g = "days.pipeline_parser"
    par = workbooks.parse_days(S.master)
    for d in sorted(set(par) | set(tab1)):
        a, b = tab1.get(d), par.get(d)
        A.check(g, (a is None) == (b is None), f"{d}: parsed by only one reader")
        if a and b:
            A.check(g, a["type"] == b["type"], f"{d}: audit reads {a['type']!r}, pipeline parser reads {b['type']!r}")
            A.check(g, flat(" / ".join(b["labels"])) == flat(a["label"]).replace(" / ", " / ") or norm(" ".join(b["labels"])) == norm(a["label"]),
                    f"{d}: event label differs ({a['label']!r} vs {b['labels']!r})")

    audit_yag(A, S, tab1)

    # index
    g = "days.index"
    if S.db is None:
        return A.skip(g, f"database not read ({S.db_err or 'offline'})")
    rows = S.db_by("school_day")
    by = collections.defaultdict(list)
    for r in rows:
        by[r["metadata"].get("event_date")].append(r)
    order = {k: v for k, v in S.bell["orders"].items()}
    blocks = S.bell["blocks"]
    special = {}          # "40" / "90" -> [(start, end), ...] read straight from the "Special Schedule Configurations" cells next to the calendar
    for row in S.master["Calendar AB + Special Schedule"].iter_rows(values_only=True):
        for v in row[10:14]:
            m = re.match(r"Community Time \[(\d+) Minutes\]", v.strip()) if isinstance(v, str) else None
            if m:
                special[m.group(1)] = re.findall(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", v)
    A.check(g, set(special) == {"40", "90"}, f"expected special schedules for Community 40 and 90 minutes, found {sorted(special)}")
    for d, v in sorted(tab1.items()):
        rs = by.get(d.isoformat(), [])
        t, label = v["type"], v["label"]
        if not t and not label and d not in adv:
            continue
        if not A.check(g, len(rs) == 1, f"{d}: {len(rs)} school_day rows in the index (expected 1)"):
            continue
        m, text = rs[0]["metadata"], flat(rs[0]["content"].split("\n\n", 1)[-1])
        if t in order:
            seq = order[t]
            A.check(g, m.get("day_type") == t and m.get("kind") == "class_day", f"{d}: index says day_type={m.get('day_type')!r}/{m.get('kind')!r}, sheet says {t}")
            want = (f"{t} day: Blocks {seq[0]}-{max(seq)} meet, in the order {', '.join(map(str, seq))}. Times: "
                    f"Block {seq[0]} {blocks[seq[0]].replace('-', '-')}; Block {seq[1]} {blocks[seq[1]]}; Lunch 11:30 AM-12:00/12:30 PM; "
                    f"Block {seq[2]} {blocks[3]}; Block {seq[3]} {blocks[4]}")
            sk = re.search(r"Community \((40|90)'\)", label)
            if sk and special.get(sk.group(1)):
                # a Community-time day runs on the sheet's special schedule: every time in that configuration cell must be in the index text,
                # and the day's own block order must be applied to it (Block n = this day's n-th block)
                flat_text = re.sub(r"\s*(AM|PM)\b", "", text)
                missing = [f"{a}-{b}" for a, b in special[sk.group(1)] if f"{a}-{b}" not in flat_text]
                A.check(g, not missing and "SPECIAL" in text, f"{d} ({t}, Community ({sk.group(1)}')): special-schedule times missing from the index: {missing[:3]} {text[:120]!r}")
                A.check(g, f"Block {seq[2]} " in text and text.index(f"Block {seq[2]} ") < text.index(f"Block {seq[3]} "), f"{d} ({t}): special schedule does not follow the day's block order {seq}")
            else:
                A.check(g, want in text, f"{d} ({t}): block order/times in the index differ from knowledge/bell_schedule.md: {text[:150]!r}")
        elif SHORT_DAY.search(t + " " + label):
            A.check(g, m.get("kind") == "short_day" and "early dismissal" in text and "No classes" not in text, f"{d}: sheet says {t!r} ({label!r}), a short day, but the index says: {text[:120]!r}")
        elif FULL_DAY.search(label) and not NO_CLASS.search(label):
            A.check(g, m.get("kind") == "full_day_event" and (t or label) in text and "No classes" not in text, f"{d}: sheet says full-day event {t!r}, index says: {text[:120]!r}")
        elif t or NO_CLASS.search(label):
            A.check(g, m.get("kind") == "no_classes" and text.split(" - ", 1)[-1].startswith("No classes"), f"{d}: sheet type {t!r} ({label!r}) is a no-classes day but index says {m.get('kind')!r}: {text[:100]!r}")
        else:
            A.check(g, m.get("day_type", "") == "", f"{d}: sheet has no day type but index has {m.get('day_type')!r}")
        for lab in [] if t and t not in order and not NO_CLASS.search(label) else [x.strip() for x in re.split(r"\s*/\s*|\n", label) if x.strip()]:
            A.check(g, norm(lab) in norm(text), f"{d}: sheet event {lab!r} missing from index text")
    sheet_dates = {d.isoformat() for d in set(tab1) | set(adv)}
    for k, rs in by.items():
        A.check(g, k in sheet_dates, f"index has a school_day record for {k}, which is not in the sheet (stale)")
        A.check(g, len(rs) == 1 or k not in sheet_dates, f"{k}: {len(rs)} duplicate school_day rows")
    # no school_day text may contain a different day type than its metadata
    for r in rows:
        m = re.search(r"- ((?:A|B)'?) day: Blocks", r["content"])
        if m:
            A.check(g, m.group(1) == r["metadata"].get("day_type"), f"{r['metadata'].get('event_date')}: text says {m.group(1)} but metadata {r['metadata'].get('day_type')}")

    # advisory by grade
    g = "days.advisory"
    grades = ["Freshmen", "Sophomores", "Juniors", "Seniors"]
    ws = S.master["Advisory Calendar"]
    arows = grid(ws)
    usual = {gr: cell(arows[1][3 + k]) for k, gr in enumerate(grades)}
    for r in arows[2:]:
        r = r + [None] * 8
        if not isinstance(r[1], datetime):
            continue
        d = r[1].date()
        rs = by.get(d.isoformat(), [])
        text = flat(rs[0]["content"]) if rs else ""
        for k, gr in enumerate(grades):
            v = flat(re.sub(r"\s*\n\s*", " ", cell(r[3 + k])))
            if not v:
                continue
            if not A.check(g, bool(rs), f"{d}: advisory entry for {gr} but no school_day record"):
                continue
            ok = norm(v) in norm(text)
            if ok and v.lower() == "advisory" and usual[gr]:
                ok = norm(usual[gr]) in norm(text)
            A.check(g, ok, f"{d} {gr}: advisory {v!r} (usual {usual[gr]!r}) not in index text")


MON = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
MON3 = {m[:3].title(): i + 1 for i, m in enumerate(MON)}
NOSCHOOL_LINE = re.compile(r"(?:Mon|Tue|Tues|Wed|Thu|Thur|Thurs|Fri|Sat|Sun)\.?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})"
                           r"(?:\s*[\u2013-]\s*(?:(?:Mon|Tue|Tues|Wed|Thu|Thur|Thurs|Fri)\.?\s+)?(?:(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+)?(\d{1,2}))?"
                           r"\s*:\s*((?:(?!\b(?:Mon|Tue|Tues|Wed|Thu|Thur|Thurs|Fri|Sat|Sun)\.?\s+[A-Z][a-z]{2}\b)[^()|]){0,90}?)\(\s*(No school|No classes|School closed)", re.I)


def parse_yag(text: str, start_year: int):
    """Month class-day counts and no-school date ranges from the Year-at-a-Glance calendar document."""
    counts = {}
    for m in re.finditer(r"\b(" + "|".join(MON) + r")\s+(2\s?0\s?\d\s?\d)\s*\[\s*(\d+)\s*d\s?a\s?y\s?s?\s*\]", text):
        counts.setdefault((int(m.group(2).replace(" ", "")), MON.index(m.group(1)) + 1), int(m.group(3)))
    ranges = []
    for m in NOSCHOOL_LINE.finditer(text):
        m1, d1, m2, d2, what, kind = m.groups()
        a = MON3[m1.title()]
        b = MON3[m2.title()] if m2 else a
        ya = start_year if a >= 8 else start_year + 1
        yb = start_year if b >= 8 else start_year + 1
        try:
            lo = date(ya, a, int(d1))
            hi = date(yb, b, int(d2)) if d2 else lo
        except ValueError:
            continue
        ranges.append((lo, hi, what.strip(" :"), kind))
    return counts, ranges


def audit_yag(A: Audit, S: Sources, tab1):
    g = "days.year_at_a_glance"
    docs = []
    for p in sorted(config.PAGES_DIR.glob("*.json")) if config.PAGES_DIR.exists() else []:
        d = json.loads(p.read_text(encoding="utf-8"))
        if re.search(r"year-at-a-glance", d.get("title") or "", re.I) and "2026" in (d.get("title") or ""):
            docs.append(d)
    if not docs:
        return A.skip(g, "no 2026-27 Year-at-a-Glance document saved locally")
    for d in docs[:1]:
        counts, ranges = parse_yag(d["text"], 2026)
        A.check(g, len(counts) >= 11, f"only {len(counts)} month headers found in {d['title'][:50]!r}")
        diffs = []
        for (y, mth), n in sorted(counts.items()):       # whole-school counts (LS/MS/US): informational, the Upper School has its own retreats and intersession
            have = sum(1 for dt, v in tab1.items() if (dt.year, dt.month) == (y, mth) and v["type"] in workbooks.DAY_TYPES)
            A.passed(g)
            if have != n:
                diffs.append(f"{MON[mth - 1][:3].title()} {y}: {n} school-wide vs {have} US A/B days")
        A.info(g, "class days per month, Year-at-a-Glance (all divisions) vs master sheet (Upper School A/B days): " + "; ".join(diffs[:12]))
        totals = re.findall(r"S[12]: \d+ class days", d["text"])[:2]
        before = sum(1 for dt, v in tab1.items() if v["type"] in workbooks.DAY_TYPES and dt < date(2027, 1, 1))
        after = sum(1 for dt, v in tab1.items() if v["type"] in workbooks.DAY_TYPES and dt >= date(2027, 1, 1))
        A.info(g, f"semester totals in the document: {totals}; the master sheet has {before} A/B days before 2027 and {after} from 2027")
        for lo, hi, what, kind in ranges:
            if re.search(r"\b(LS|MS)\b", what) and not re.search(r"\bUS\b", what):
                continue                                  # Lower/Middle School only
            k = lo
            while k <= hi:
                if k.weekday() < 5:
                    v = tab1.get(k)
                    A.check(g, v is None or v["type"] not in workbooks.DAY_TYPES, f"{k}: the Year-at-a-Glance says {what!r} ({kind}) but the master sheet has an {v['type'] if v else ''} class day", "conflict")
                k += timedelta(days=1)
        for lo, label in ((date(2026, 8, 19), "First Day of Classes"), (date(2027, 6, 4), "Last Day of School")):
            A.check(g, label.lower() in re.sub(r"\s+", " ", d["text"]).lower() and re.search(rf"{lo.strftime('%b')}\w* {lo.day}: {label}", d["text"], re.I) is not None,
                    f"Year-at-a-Glance does not list {label!r} on {lo}", "conflict")
            v = tab1.get(lo)
            A.check(g, v is not None and (v["type"] in workbooks.DAY_TYPES or SHORT_DAY.search(v["type"] + v["label"])), f"master sheet has no class day / short day on {lo} ({label})", "conflict")


def audit_bell(A: Audit, S: Sources):
    g = "bell.sources"
    b = S.bell
    A.check(g, set(b["blocks"]) == set(range(1, 9)), "knowledge/bell_schedule.md does not list all 8 blocks")
    want = {1: "8:55-10:10 AM", 2: "10:15-11:30 AM", 3: "12:35-1:50 PM", 4: "1:55-3:10 PM"}      # confirmed by the school (HANDOFF section 4)
    for k, t in want.items():
        A.check(g, b["blocks"].get(k) == t and b["blocks"].get(k + 4) == t, f"knowledge file: block {k}/{k + 4} is {b['blocks'].get(k)!r}, confirmed time is {t!r}")
    A.check(g, b["orders"] == {"A": [1, 2, 3, 4], "A'": [1, 2, 4, 3], "B": [5, 6, 7, 8], "B'": [5, 6, 8, 7]}, f"knowledge file orders {b['orders']}")
    A.check(g, workbooks.SLOT_TIMES == [want[1], want[2], want[3], want[4]], f"pipeline SLOT_TIMES {workbooks.SLOT_TIMES} differ from the confirmed times")
    A.check(g, workbooks.ORDER == b["orders"], "pipeline ORDER differs from the knowledge file")
    A.check(g, b["lunch"] == ("11:30 AM", "12:00", "12:30 PM"), f"knowledge file lunch {b['lunch']}")
    qs = (ROOT / "query_server.py").read_text()
    prompt = qs[qs.index("SYSTEM_PERSONA"): qs.index("def _norm")]
    for k in (1, 2, 3, 4):
        a = want[k].split("-")
        A.check(g, f"{k}/{k + 4} {a[0]}-{a[1].split(' ')[0]}" in prompt, f"system prompt block time for Blocks {k}/{k + 4} does not match {want[k]}")
    A.check(g, "3:10 PM" in prompt and "12:00 or 12:30" in prompt, "system prompt lunch/end of day differ")
    for f in ("intent.py", "rag.py", "followup.py"):
        src = (ROOT / f).read_text()
        for t in re.findall(r"\b(?:8:(?!55)\d\d|9:\d\d|10:(?!10|15)\d\d)\s*(?:-|to)\s*\d+:\d\d\b", src):
            A.check(g, False, f"{f} hard-codes a time range {t!r} that is not a confirmed bell time")
    if S.db is None:
        return
    g = "bell.index"
    facts = S.db_by("school_facts")
    ftext = " ".join(flat(r["content"]) for r in facts)
    for k, t in want.items():
        A.check(g, f"Blocks {k} and {k + 4}: {t}" in ftext, f"index fact for Blocks {k}/{k + 4} missing or different (confirmed {t})")
    A.check(g, "A' day order: Block 1, Block 2, Block 4, Block 3" in ftext and "B' day order: Block 5, Block 6, Block 8, Block 7" in ftext, "index fact for A'/B' order missing")
    # other documents that state block times differently
    clock = re.compile(r"block\s*\d[^.\n]{0,40}?(\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)?\s*[-–to ]+\s*\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)?)", re.I)
    good = {x.replace(" AM", "").replace(" PM", "") for x in want.values()} | {"8:55-10:10", "10:15-11:30", "12:35-1:50", "1:55-3:10"}
    for r in S.current():
        m = r["metadata"]
        if m.get("source") in ("school_facts", "school_day") or m.get("stale"):
            continue
        for t in clock.findall(r["content"]):
            tt = re.sub(r"\s*(a\.?m\.?|p\.?m\.?)", "", t, flags=re.I)
            tt = re.sub(r"\s*(-|–|to)\s*", "-", tt).replace(" ", "")
            A.check(g, tt in good, f"document {m.get('title', '')[:50]!r} ({m.get('source')}) states block time {t.strip()!r}", "conflict")


# --------------------------------------------------------------------------- courses
ROOM_RE = re.compile(r"\d{2,4}[A-Za-z]?|Portable")
ASSIGN_LINE = re.compile(r"^- Block (\d) \([^)]*\): (.*?)(?:, room (\S+))?$")


def read_roles(ws):
    rows = [r + [None] * 14 for r in grid(ws)]
    hdr = next((i for i, r in enumerate(rows[:6]) if "Block 1" in [cell(c) for c in r]), None)
    if hdr is None:
        return [], []
    cols = {int(m.group(1)): j for j, c in enumerate(rows[hdr]) if (m := re.fullmatch(r"Block (\d)", cell(c)))}
    recs, noassign = [], []
    for r in rows[hdr + 1:]:
        name = fix_name(cell(r[0]))
        if not name or name.lower() in ("type", "room"):
            continue
        cells = {b: str(r[j]) for b, j in cols.items() if r[j] is not None and cell(r[j]) not in ("", "/", "|")}
        if not cells:
            noassign.append(name)
            continue
        if any(re.search(r"\bDays$", cell(v)) for v in cells.values()):
            continue
        for b, raw in cells.items():
            lines = [l.strip() for l in raw.split("\n") if l.strip()]
            room = ""
            if len(lines) > 1 and ROOM_RE.fullmatch(lines[-1]):
                room, lines = lines[-1], lines[:-1]
            course = cell(" ".join(lines))
            if course:
                recs.append({"teacher": name, "block": b, "course": course, "room": room})
    return recs, noassign


def read_rooms(ws):
    rows = [r + [None] * 14 for r in grid(ws)]
    hdr = next((i for i, r in enumerate(rows[:6]) if "Block 1" in [cell(c) for c in r]), None)
    if hdr is None:
        return []
    cols = {int(m.group(1)): j for j, c in enumerate(rows[hdr]) if (m := re.fullmatch(r"Block (\d)", cell(c)))}
    out = []
    for r in rows[hdr + 1:]:
        room = cell(r[1])
        if not room or not ROOM_RE.fullmatch(room):
            continue
        for b, j in cols.items():
            raw = str(r[j] or "")
            lines = [l.strip() for l in raw.split("\n") if l.strip()]
            if lines:
                out.append({"room": room, "block": b, "course": cell(lines[0]), "who": cell(lines[1]) if len(lines) > 1 else ""})
    return out


def read_offerings(wb):
    ws = wb["Sheet1"] if "Sheet1" in wb.sheetnames else wb.worksheets[0]
    rows = grid(ws)
    hdr = next((i for i, r in enumerate(rows[:4]) if "Block 1" in [cell(c) for c in r]), 0)
    cols = {int(m.group(1)): j for j, c in enumerate(rows[hdr]) if (m := re.fullmatch(r"Block (\d)", cell(c)))}
    out = []
    for r in rows[hdr + 1:]:
        r = r + [None] * 12
        for b, j in cols.items():
            lines = [l.strip() for l in str(r[j] or "").split("\n") if l.strip()]
            if lines and cell(lines[0]) not in ("", "/"):
                out.append({"course": cell(lines[0]), "block": b, "last": cell(lines[1]) if len(lines) > 1 else ""})
    return out


def who_matches(teacher: str, last: str) -> bool:
    """Is the offerings sheet's last name ('Mendez-Ortiz', 'Navarro Rajoy', 'Jacquot') this full name?"""
    tn, ln = norm(teacher), norm(last)
    return bool(ln) and (tn == ln or tn.endswith(" " + ln) or edit1(tn.split(" ")[-1], ln.split(" ")[-1]))


def split_who(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"\s*\+\s*|\s*,\s*|\s+and\s+", s) if x.strip()]


def audit_courses(A: Audit, S: Sources):
    if S.master is None:
        return A.skip("courses.index", "master schedule workbook not found")
    tabs = {"Fall Roles": "Fall", "Spring Roles": "Spring"}
    parsed = {sem: read_roles(S.master[t]) for t, sem in tabs.items() if t in S.master.sheetnames}
    roles = {sem: v[0] for sem, v in parsed.items()}
    offers = read_offerings(S.off) if S.off else []
    A.info("courses.index", f"roles: {{{', '.join(f'{k}: {len(v)} records' for k, v in roles.items())}}}; offerings sheet: {len(offers)} entries")
    fall = roles.get("Fall", [])
    teachers = {r["teacher"] for r in fall}
    teachers |= {n for n in parsed.get("Fall", ([], []))[1] if len(n.split()) >= 2}      # named teachers with no assignments entered yet
    # offerings entries resolved against the Roles teachers: agree / contradict (other course in that block) / roles empty / unknown teacher
    by_tb = {(r["teacher"], r["block"]): norm(r["course"]) for r in fall}
    verdict = []                                           # (offer, teacher or None, status)
    for o in offers:
        for w in (split_who(o["last"]) or [""]):
            ts = [t for t in teachers if who_matches(t, w)]
            if not w:
                verdict.append((o, w, "no_teacher"))
                continue
            if len(ts) != 1:
                verdict.append((o, w, "unknown_teacher"))
                continue
            c = by_tb.get((ts[0], o["block"]))
            verdict.append((o, ts[0], "agree" if c == norm(o["course"]) else "contradict" if c else "roles_empty"))

    # Rooms tab vs Roles tab (two views of the same workbook)
    g = "courses.rooms_vs_roles"
    for t, sem in (("Fall Rooms", "Fall"), ("Spring Rooms", "Spring")):
        if t not in S.master.sheetnames or sem not in roles:
            continue
        rr = roles[sem]
        for x in read_rooms(S.master[t]):
            who = re.sub(r"\([^)]*\)", "", x["who"])
            firsts = [norm(p).split(" ")[0] for p in re.split(r"\s*\+\s*|,", who) if norm(p)]
            hits = [r for r in rr if r["block"] == x["block"] and norm(r["course"]) == norm(x["course"])
                    and (not firsts or norm(r["teacher"]).split(" ")[0] in firsts)]
            if not A.check(g, bool(hits), f"{t}: room {x['room']} Block {x['block']} {x['course']!r} ({x['who']}) has no matching record in {sem} Roles", "conflict"):
                continue
            same = lambda a, b: a == b or (a and b and (a.startswith(b) or b.startswith(a)))
            A.check(g, any(same(h["room"], x["room"]) for h in hits), f"{t} vs {sem} Roles: {x['course']!r} Block {x['block']} ({x['who']}): Rooms tab says room {x['room']}, Roles says {sorted({h['room'] or '(none)' for h in hits})}", "conflict")

    # Roles vs the Course Offerings workbook (Fall 2026)
    if S.off and fall:
        g = "courses.offerings_vs_roles"
        for o, w, st in verdict:
            if st == "contradict":
                A.check(g, False, f"offerings: {o['course']!r} Block {o['block']} ({o['last']}) but Roles has {w} on {by_tb[(w, o['block'])]!r} in that block (index keeps the Roles entry)", "conflict")
            elif st == "roles_empty":
                A.check(g, False, f"offerings: {o['course']!r} Block {o['block']} ({w}); Roles has no entry for {w} in that block (index keeps it, marked unconfirmed)", "conflict")
            elif st == "agree":
                A.passed(g)
        n_roles_only = sum(1 for r in fall if (norm(r["course"]), r["block"]) not in {(norm(o["course"]), o["block"]) for o in offers})
        A.info(g, f"{sum(1 for v in verdict if v[2] == 'agree')} of {len(verdict)} offerings entries agree with Roles; {n_roles_only} of {len(fall)} Roles entries are not on the offerings sheet at all")
        g = "courses.offerings_teachers_not_in_roles"
        blanks = sum(1 for o, w, st in verdict if st == "no_teacher")
        if blanks:
            A.info(g, f"{blanks} offerings entries have no teacher listed")
        names = collections.Counter(w for o, w, st in verdict if st == "unknown_teacher")
        for w, n in sorted(names.items()):
            A.check(g, False, f"offerings sheet lists {w!r} ({n} entries) but no such teacher is in the Fall Roles tab", "conflict")
    else:
        A.skip("courses.offerings_vs_roles", "need both the Roles tab and the offerings workbook")

    # course names in the Roles tab vs the 2026-27 Course Catalog document
    g = "courses.catalog_names"
    cat = None
    for pth in sorted(config.PAGES_DIR.glob("*.json")) if config.PAGES_DIR.exists() else []:
        d = json.loads(pth.read_text(encoding="utf-8"))
        if (d.get("title") or "").strip() == "2026-27 Course Catalog" and d.get("source") == "google_doc":
            cat = norm(d["text"])
    if cat is None or not fall:
        A.skip(g, "no saved 2026-27 Course Catalog document")
    else:
        for c in sorted({r["course"] for r in fall}):
            if norm(c) in ("academic council", "deans meeting"):
                continue
            A.check(g, norm(c) in cat, f"Roles course name {c!r} does not appear in the 2026-27 Course Catalog (abbreviation or typo in the sheet; lookups still match it fuzzily)", "conflict")

    # live copy of the offerings workbook vs the local one
    g = "courses.live_offerings_fresh"
    lp = config.DATA_DIR / "live" / "Course Offerings by Block (live).xlsx"
    if S.args.offline:
        A.skip(g, "offline")
    elif not lp.exists():
        A.skip(g, "no local live copy")
    else:
        try:
            import requests
            spec = next(s for s in config.LIVE_SHEETS if s["kind"] == "workbook")
            r = requests.get(f"https://docs.google.com/spreadsheets/d/{spec['id']}/export?format=xlsx", timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200 or r.content[:2] != b"PK":
                A.skip(g, f"could not download the live workbook (HTTP {r.status_code})")
            else:
                new = openpyxl.load_workbook(io.BytesIO(r.content), data_only=True)
                old = load_wb(lp)
                A.check(g, new.sheetnames == old.sheetnames, f"tab names changed: {old.sheetnames} -> {new.sheetnames}", "conflict")
                for n in set(new.sheetnames) & set(old.sheetnames):
                    A.check(g, grid(new[n]) == grid(old[n]), f"live tab {n!r} differs from the local copy in data/pipeline/live (re-run the live stage)")
        except Exception as e:
            A.skip(g, f"fetch failed: {type(e).__name__}")

    g = "courses.index"
    if S.db is None:
        return A.skip(g, f"database not read ({S.db_err or 'offline'})")
    teach = [r for r in S.db_by("school_courses", "teacher")]
    dbt = collections.defaultdict(dict)                 # (sem, teacher) -> {(block, course norm): (room, unconfirmed)}
    for r in teach:
        m = r["metadata"]
        sem = "Fall" if m["semester"].startswith("Fall") else "Spring"
        for line in r["content"].split("\n\n", 1)[-1].split("\n"):
            line = line.strip()
            unconf = "listed only on the course offerings sheet" in line
            line = re.sub(r" \(listed only on the course offerings sheet[^)]*\)$", "", line)
            mm = ASSIGN_LINE.match(line)
            if mm:
                dbt[(sem, m["teacher"])][(int(mm.group(1)), norm(mm.group(2)))] = (mm.group(3) or "", unconf)
    for sem, recs in roles.items():
        for r in recs:
            got = dbt.get((sem, r["teacher"]), {})
            key = (r["block"], norm(r["course"]))
            if not A.check(g, key in got, f"{sem} Roles: {r['teacher']} Block {r['block']} {r['course']!r} missing from the index"):
                continue
            A.check(g, got[key][0] == r["room"], f"{sem} Roles: {r['teacher']} Block {r['block']} {r['course']!r}: sheet room {r['room'] or '(none)'}, index room {got[key][0] or '(none)'}")
            A.check(g, not got[key][1], f"{sem} Roles: {r['teacher']} Block {r['block']} {r['course']!r} is marked unconfirmed in the index although Roles lists it")
    other_sem = {r["teacher"] for sem, recs in roles.items() if sem != "Fall" for r in recs}
    ok_off = {(o["course"], o["block"], t) for o, t, st in verdict if st in ("agree", "roles_empty")}
    for o, w, st in verdict:                              # a teacher only the Spring Roles tab names: the pipeline attaches the full name
        ts = [t for t in other_sem if who_matches(t, w)] if st == "unknown_teacher" else []
        if len(ts) == 1:
            ok_off.add((o["course"], o["block"], ts[0]))
    ok_off = {(norm(c), b, t) for c, b, t in ok_off}
    for (sem, t), lines in dbt.items():
        exp = {(r["block"], norm(r["course"])) for r in roles.get(sem, []) if r["teacher"] == t}
        for key, (room, unconf) in lines.items():
            if key in exp:
                continue
            # not in Roles: allowed only as an unconfirmed offerings entry for a block Roles leaves empty (never a second course in a taken block)
            A.check(g, sem == "Fall" and unconf and (key[1], key[0], t) in ok_off,
                    f"index: {sem} {t} Block {key[0]} {key[1]!r} is not backed by the Roles tab or by a non-contradicting offerings entry (phantom or double-booked block)")
    names = {t for (_, t) in dbt}
    for sem, recs in roles.items():
        for t in {r["teacher"] for r in recs}:
            A.check(g, t in names, f"teacher {t!r} has no teacher record in the index")
    for n in names:
        A.check(g, "+" not in n and "Jaquot" not in n, f"teacher record {n!r} in the index is a joint entry or the misspelling 'Jaquot' (confirmed spelling: Jacquot)")

    g = "courses.index_offerings"
    crs = collections.defaultdict(list)
    for r in S.db_by("school_courses", "course"):
        m = r["metadata"]
        if m["semester"].startswith("Fall"):
            crs[m["course_key"]].append(r["content"])
    for o, w, st in verdict:
        if st == "contradict":
            continue                                    # intentionally dropped in favour of Roles (reported above)
        if st == "no_teacher":
            w = ""
        txt = " ".join(crs.get(norm(o["course"]), []))
        if not A.check(g, bool(txt), f"offerings: course {o['course']!r} has no course record in the index"):
            continue
        lines = [l for l in txt.split("\n") if l.startswith(f"- Block {o['block']} ")]
        want = norm(w)
        A.check(g, bool(lines) and (not want or any(who_matches(l.split("): ", 1)[-1].split(",")[0], w) or want in norm(l) for l in lines)),
                f"offerings: {o['course']!r} Block {o['block']} ({w}) not in the index course record: {lines[:2]}")


# --------------------------------------------------------------------------- clubs
def audit_clubs(A: Audit, S: Sources):
    g = "clubs.index"
    if S.clubs is None:
        return A.skip(g, "clubs workbook not found")
    rows = grid(S.clubs.worksheets[0])
    head = [cell(c).lower() for c in rows[0]]
    idx = {n: head.index(n) for n in ("clubs", "description", "leaders", "advisor", "day", "time", "room") if n in head}
    raw = {}
    for r in rows[1:]:
        r = r + [None] * 8
        name = cell(r[idx["clubs"]])
        if name:
            raw[name] = {k: flat(cell(r[i])) for k, i in idx.items()}
    names = [cell((r + [None])[0]) for r in rows[1:] if cell((r + [None])[0])]
    A.check(g, len(names) == len(set(names)), f"duplicate club names in the workbook: {[n for n, c in collections.Counter(names).items() if c > 1]}", "conflict")
    if S.db is None:
        return A.skip(g, f"database not read ({S.db_err or 'offline'})")
    db = {r["metadata"]["club"]: r for r in S.db_by("school_clubs", "club")}
    for name, v in raw.items():
        if not A.check(g, name in db, f"club {name!r} is in the workbook but not in the index"):
            continue
        t = flat(db[name]["content"])
        if v.get("leaders"):
            A.check(g, f"Student leaders: {v['leaders']}" in t, f"{name}: leaders {v['leaders']!r} not in index")
        else:
            A.check(g, "Student leaders" not in t, f"{name}: index lists leaders but the workbook has none")
        if v.get("advisor"):
            A.check(g, f"Faculty advisor: {v['advisor']}" in t, f"{name}: advisor {v['advisor']!r} not in index")
        meets = ", ".join(x for x in (v.get("day", ""), v.get("time", "")) if x)
        if meets:
            want = f"Meets: {meets}" + (f" in room {v['room']}" if v.get("room") else "")
            A.check(g, want in t, f"{name}: meeting time/room {want!r} not in index")
        if v.get("description"):
            A.check(g, flat(v["description"])[:80] in t, f"{name}: description differs")
    for name in db:
        A.check(g, name in raw, f"index has club {name!r}, which is not in the 2026-27 workbook (stale)")
    out_rows = S.db_by("school_clubs", "outline")
    listed = sum(int(m.group(1)) for r in out_rows for m in [re.search(r"(\d+) listed", r["content"])][:1] if m)
    listed = int(re.search(r"(\d+) listed", out_rows[0]["content"]).group(1)) if out_rows else 0
    A.check(g, listed == len(raw), f"index outline says {listed} clubs, workbook has {len(raw)}")

    g = "clubs.other_documents"
    squash = lambda n: re.sub(r"\b(club|clubs|group|the|and|of)\b", " ", norm(n)).replace(" ", "")
    cur = {squash(n) for n in raw}
    flagged = collections.Counter()
    for r in S.current():
        m = r["metadata"]
        if m.get("source") == "school_clubs" or "club" not in (m.get("title", "") + m.get("kind", "")).lower() or m.get("kind") != "outline":
            continue
        for item in re.findall(r"^- ([^:\n(]+)", r["content"], re.M):
            k = squash(item)
            if len(k) > 3:
                ok = k in cur or any(k in c or c in k for c in cur)
                if not ok and (m.get("stale") or freshness.meta_of(r)["stale"]):
                    flagged[m.get("title", "")[:45]] += 1
                    A.passed(g)
                    continue
                A.check(g, ok, f"{m.get('title', '')[:45]!r} ({m.get('source')}) lists club {item.strip()!r}, not in the 2026-27 workbook", "conflict")
    if flagged:
        A.info(g, f"{sum(flagged.values())} club names only in superseded club lists, which are flagged stale: {dict(flagged)}")


# --------------------------------------------------------------------------- team schedule sheets
def rd_schedule(text: str):
    rows = list(csv.reader(io.StringIO(text)))
    hdr = next((i for i, r in enumerate(rows[:6]) if any(c.strip().lower() == "date" for c in r) and any("home" in c.lower() for c in r)), None)
    if hdr is None:
        return []
    low = [c.strip().lower() for c in rows[hdr]]
    ix = lambda *n: next((i for i, h in enumerate(low) if h in n), None)
    cd, ch, ca, ct, cl = ix("date"), ix("home team", "home"), ix("away team", "away"), ix("time", "time "), ix("location", "venue")
    out = []
    for r in rows[hdr + 1:]:
        g = lambda i: r[i].strip() if i is not None and i < len(r) else ""
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", g(cd))
        if not m or not (g(ch) or g(ca)):
            continue
        y = int(m.group(3))
        try:
            d = date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        tm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([AP]M)", g(ct).upper())
        st = f"{int(tm.group(1)) % 12 + (12 if tm.group(3) == 'PM' else 0):02d}:{int(tm.group(2) or 0):02d}" if tm else None
        home, away = g(ch), g(ca)
        if away:
            nh = home.lower().startswith("nueva")
            out.append({"date": d, "opp": (away if nh else home).lower(), "ha": "home" if nh else "away", "start": st, "loc": g(cl), "game": True})
        else:
            out.append({"date": d, "opp": home.lower(), "ha": "", "start": st, "loc": g(cl), "game": False})
    return out


def compare_team(A: Audit, g: str, label: str, evs: list[dict], db_rows: list[dict], today: date):
    lo, hi = today - timedelta(days=14), today + timedelta(days=430)
    exp, firsts = [], {}
    for e in evs:                  # sheets repeat the home games in a second table; the copy WITH a time is the one the pipeline keeps
        if not (lo <= e["date"] <= hi):
            continue
        k = (e["date"], e["opp"], e["ha"])
        if k in firsts:
            f = firsts[k]
            if f["start"] == e["start"]:
                A.passed(g)
            elif f["start"] is None or e["start"] is None:
                chosen = e if f["start"] is None else f
                A.passed(g)
                A.info(g, f"{label}: {e['date']} vs {e['opp']} is listed twice in the sheet, once without a time; the pipeline uses the copy with the time ({chosen['start']})")
                if f["start"] is None:
                    exp[exp.index(f)] = e
                    firsts[k] = e
            else:
                A.check(g, False, f"{label}: {e['date']} vs {e['opp']} appears twice in the sheet with different times {f['start']} and {e['start']} (the first is used; fix the sheet)", "conflict")
            continue
        firsts[k] = e
        exp.append(e)
    got = [r["metadata"] for r in db_rows if lo.isoformat() <= (r["metadata"].get("event_date") or "") <= hi.isoformat()]
    used = set()
    for e in exp:
        cands = [i for i, m in enumerate(got) if i not in used and m.get("event_date") == e["date"].isoformat()
                 and norm(e["opp"]) in norm(m.get("summary", ""))]
        if not A.check(g, bool(cands), f"{label}: {e['date']} {'game vs' if e['game'] else 'note'} {e['opp']} is in the sheet but not in the index"):
            continue
        i = cands[0]
        used.add(i)
        m = got[i]
        if e["game"]:
            A.check(g, m.get("home_away") == e["ha"], f"{label} {e['date']} vs {e['opp']}: sheet {e['ha']}, index {m.get('home_away')!r}")
            A.check(g, m.get("start_time") == e["start"], f"{label} {e['date']} vs {e['opp']}: sheet time {e['start']}, index {m.get('start_time')}")
            if e["loc"]:
                A.check(g, flat(m.get("location", "")) == flat(e["loc"]), f"{label} {e['date']} vs {e['opp']}: sheet location {e['loc']!r}, index {m.get('location')!r}")
    for i, m in enumerate(got):
        if i not in used:
            if (m.get("event_date") or "") < today.isoformat():
                A.info(g, f"{label}: past row {m.get('summary')!r} on {m.get('event_date')} is no longer on the sheet (kept as history)")
            else:
                A.check(g, False, f"{label}: index has {m.get('summary')!r} on {m.get('event_date')}, not in the sheet (stale or removed; the next refresh removes it)")


def audit_teams(A: Audit, S: Sources):
    g = "teams.saved_pages"
    pages = []
    for p in sorted((config.PAGES_DIR).glob("*.json")) if config.PAGES_DIR.exists() else []:
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("source") == "google_sheet" and rd_schedule(d.get("text", "")):
            pages.append(d)
    if S.db is None:
        return A.skip(g, f"database not read ({S.db_err or 'offline'})")
    if not pages:
        return A.skip(g, "no team schedule pages saved locally")
    cal = S.db_by("calendar_sheet")
    for pg in pages:
        rows = [r for r in cal if r["metadata"].get("source_url") == pg["url"]]
        compare_team(A, g, pg["title"], rd_schedule(pg["text"]), rows, S.today)
    A.info(g, f"{len(pages)} schedule pages: {', '.join(p['title'] for p in pages)}")

    g = "teams.live_tabs"
    if S.args.offline:
        return A.skip(g, "offline")
    try:
        import requests
        H = {"User-Agent": "Mozilla/5.0"}
        for spec in (s for s in config.LIVE_SHEETS if s["kind"] == "team_schedule"):
            html = requests.get(f"https://docs.google.com/spreadsheets/d/{spec['id']}/htmlview", headers=H, timeout=45).text
            tabs = {n.strip(): gid for n, gid in re.findall(r'items\.push\(\{name: "([^"]+)".*?gid: "(\d+)"', html)}
            for tab, title in spec["tabs"].items():
                gid = tabs.get(tab)
                if not A.check(g, bool(gid), f"live sheet tab {tab!r} not found (renamed?)"):
                    continue
                r = requests.get(f"https://docs.google.com/spreadsheets/d/{spec['id']}/export?format=csv&gid={gid}", headers=H, timeout=45)
                if r.status_code != 200 or "@" in r.text:
                    A.check(g, False, f"{title}: tab not readable or looks like a roster (HTTP {r.status_code}); not compared")
                    continue
                url = f"https://docs.google.com/spreadsheets/d/{spec['id']}/edit#gid={gid}"
                rows = [x for x in cal if x["metadata"].get("source_url") == url]
                compare_team(A, g, title, [{**e, "title": title} for e in rd_schedule(r.text)], rows, S.today)
    except Exception as e:
        A.skip(g, f"fetch failed: {type(e).__name__}: {str(e)[:80]}")


# --------------------------------------------------------------------------- iCal feed
def parse_feed(text: str) -> tuple[list[dict], dict]:
    """A second, independent reading of the feed (regex based): list of events with local wall times."""
    from zoneinfo import ZoneInfo
    LA = ZoneInfo("America/Los_Angeles")
    t = re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n"))
    stats = {"vevent": 0, "utc_times": 0, "floating_times": 0, "tzids": collections.Counter(), "recurring": 0, "cancelled": 0}
    out = []
    for blk in re.findall(r"BEGIN:VEVENT\n(.*?)\nEND:VEVENT", t, re.S):
        stats["vevent"] += 1
        props = {}
        for ln in blk.split("\n"):
            if ":" not in ln:
                continue
            head, _, val = ln.partition(":")
            name, _, params = head.partition(";")
            props.setdefault(name.upper(), (params, val))
        if "RRULE" in props:
            stats["recurring"] += 1
        if props.get("STATUS", ("", ""))[1].strip().upper() == "CANCELLED":
            stats["cancelled"] += 1
            continue

        def when(key):
            if key not in props:
                return None, None
            params, val = props[key]
            val = val.strip()
            if "VALUE=DATE" in params or re.fullmatch(r"\d{8}", val):
                return datetime.strptime(val[:8], "%Y%m%d"), True
            dt = datetime.strptime(val.rstrip("Z"), "%Y%m%dT%H%M%S")
            if val.endswith("Z"):
                stats["utc_times"] += 1
                dt = dt.replace(tzinfo=timezone.utc).astimezone(LA).replace(tzinfo=None)
            else:
                m = re.search(r"TZID=([^;:]+)", params)
                if m:
                    stats["tzids"][m.group(1)] += 1
                    if m.group(1) != "America/Los_Angeles":
                        dt = dt.replace(tzinfo=ZoneInfo(m.group(1))).astimezone(LA).replace(tzinfo=None)
                else:
                    stats["floating_times"] += 1
            return dt, False
        s, allday = when("DTSTART")
        e, e_all = when("DTEND")
        summ = re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), props.get("SUMMARY", ("", ""))[1]).strip()
        if s is None or not summ:
            continue
        loc = re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), props.get("LOCATION", ("", ""))[1]).strip()
        from pipeline import safety
        loc = safety.scrub(loc)[0]            # the pipeline strips meeting passcodes (?pwd=) from invite links before indexing
        d0 = s.date()
        if allday:                 # all-day DTEND is exclusive; a timed DTEND on an all-day event is inclusive
            end_d = (e.date() - timedelta(days=1) if e_all else e.date()) if e else d0
            end_d = max(end_d, d0)
        else:
            end_d = e.date() if e else d0
        out.append({"summary": summ, "date": d0, "end_date": end_d, "all_day": allday, "location": loc,
                    "start": None if allday else s.strftime("%H:%M"), "end": None if allday or e is None else e.strftime("%H:%M"),
                    "raw_start": str(s), "raw_end": str(e)})
    return out, stats


def audit_ical(A: Audit, S: Sources):
    g = "ical.feed"
    if S.args.offline:
        return A.skip(g, "offline")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    url = os.getenv("NUEVAI_CALENDAR_URL", "").strip().strip("\"'")
    if not url:
        if S.args.ci:       # the unattended job must not pass without reading the calendar: the secret is missing or empty
            return A.check(g, False, "NUEVAI_CALENDAR_URL is not set: the daily refresh could not read the calendar (repository secret missing or empty)")
        return A.skip(g, "NUEVAI_CALENDAR_URL not set")
    try:
        import requests
        r = requests.get(url.replace("webcal://", "https://", 1), timeout=60, headers={"User-Agent": config.USER_AGENT})
        r.raise_for_status()
        text = r.text
    except Exception as e:
        return A.skip(g, f"feed not fetched ({type(e).__name__})")            # never include the exception text: it can echo the URL
    A.check(g, "BEGIN:VCALENDAR" in text[:200], "feed does not look like iCal (token expired?)")
    ev, st = parse_feed(text)
    A.info(g, f"{st['vevent']} VEVENTs, {st['recurring']} with RRULE, {st['cancelled']} cancelled, tz {dict(st['tzids'])}, "
              f"{st['utc_times']} UTC times, {st['floating_times']} floating times")
    if st["recurring"]:
        A.check(g, False, f"{st['recurring']} recurring events (RRULE) in the feed: the pipeline expands them (pipeline/calendar_feed.py occurrences) but this independent reader does not, so ical.index may differ for them", "conflict")
    A.check(g, set(st["tzids"]) <= {"America/Los_Angeles"}, f"feed uses time zones other than America/Los_Angeles: {dict(st['tzids'])}")
    A.check(g, st["floating_times"] == 0, f"{st['floating_times']} timed events have no time zone (assumed Pacific by the pipeline)", "conflict")
    A.check(g, st["vevent"] == len(ev) + st["cancelled"], f"{st['vevent'] - len(ev) - st['cancelled']} events could not be read (no start or summary)")
    mixed = [e for e in ev if e["all_day"] and e["raw_end"] != "None" and ":" in e["raw_end"].split(" ")[-1] and e["raw_end"].split(" ")[-1] != "00:00:00"]
    if mixed:
        A.info(g, f"{len(mixed)} all-day events have a timed DTEND (e.g. {mixed[0]['summary'].strip()!r}); read as ending that day")
    for e in ev:
        A.check(g, e["end_date"] >= e["date"], f"{e['summary']!r} ends before it starts ({e['raw_start']} .. {e['raw_end']})")
        A.check(g, e["all_day"] or e["end"] is None or e["end_date"] > e["date"] or e["end"] >= e["start"], f"{e['summary']!r} {e['date']}: end time {e['end']} before start {e['start']}")

    lo, hi = calendar_feed.window_floor(S.today), S.today + timedelta(days=430)
    seen, uniq = set(), []
    for e in sorted(ev, key=lambda x: (x["date"], x["start"] or "", x["summary"])):
        if e["end_date"] < lo or e["date"] > hi or calendar_feed.CALLED_OFF.search(e["summary"]):     # "(MOVED TO OCT 8) ..." placeholders are not indexed
            continue
        k = (e["summary"], e["raw_start"], e["raw_end"], e["location"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    A.info(g, f"{len(uniq)} distinct events in the window {lo}..{hi}")
    if S.db is None:
        return A.skip("ical.index", f"database not read ({S.db_err or 'offline'})")

    g = "ical.index"
    rows = S.db_by("calendar")
    key = lambda summary, d, s, e, ad, ed, loc: (flat(summary), str(d), s or "", e or "", bool(ad), str(ed), flat(loc))
    exp = collections.Counter(key(e["summary"], e["date"], e["start"], e["end"], e["all_day"], e["end_date"], e["location"]) for e in uniq)
    got = collections.Counter()
    lag = []              # differences the daily refresh (13:30 UTC) removes by itself: tolerated up to a day's churn
    for r in rows:
        m = r["metadata"]
        if m.get("event_date") and m["event_date"] < lo.isoformat():
            lag.append(f"expired event {m.get('summary')!r} on {m['event_date']} not yet removed")
            continue
        got[key(m.get("summary", ""), m.get("event_date"), m.get("start_time"), m.get("end_time"), m.get("all_day"), m.get("end_date"), m.get("location", ""))] += 1
    A.passed(g, len(exp))
    for k, n in (exp - got).items():
        lag.append(f"feed event not in the index (new or edited since the last refresh): {k[0]!r} {k[1]} {k[2] or 'all day'}-{k[3]} end {k[5]} loc {k[6]!r}")
    history = 0
    for k, n in (got - exp).items():
        if k[5] < S.today.isoformat():          # a past event of this school year that has left the feed: kept on purpose ("when was X" stays answerable)
            history += n
            continue
        lag.append(f"index event not in the feed (moved, edited or removed since the last refresh): {k[0]!r} {k[1]} {k[2] or 'all day'}-{k[3]} end {k[5]}")
    if history:
        A.info(g, f"{history} past calendar rows of this school year are no longer in the feed and are kept as history")
    A.check(g, len(lag) <= LAG_MAX, f"{len(lag)} calendar differences between the feed and the index (more than a day's churn of {LAG_MAX}: is the daily refresh failing?)")
    for x in lag[:LAG_MAX + 1]:
        A.info(g, f"awaiting the daily refresh: {x}")
    A.info(g, f"feed distinct events {sum(exp.values())}, index calendar rows {len(rows)}")
    dup = collections.Counter((flat(r['metadata'].get('summary', '')), r['metadata'].get('event_date'), r['metadata'].get('start_time'), flat(r['metadata'].get('location', ''))) for r in rows)
    for k, n in dup.items():
        A.check(g, n == 1, f"duplicate in the index ({n}x): {k[0]!r} on {k[1]} {k[2] or 'all day'}")
    ids = collections.Counter(r["chunk_id"] for r in rows)
    A.check(g, all(n == 1 for n in ids.values()), "duplicate chunk ids")
    for r in rows:        # internal consistency: the spoken date matches the stored date, times match the label
        m = r["metadata"]
        try:
            w = datetime.strptime(m["when"].split(" through ")[0], "%A, %B %d, %Y").date()
            A.check(g, w.isoformat() == m["event_date"], f"{m['summary']!r}: text date {m['when']!r} but event_date {m['event_date']}")
        except (KeyError, ValueError):
            A.check(g, False, f"{m.get('summary')!r}: unreadable date text {m.get('when')!r}")
        if m.get("all_day") is False and m.get("start_time"):
            h, mi = map(int, m["start_time"].split(":"))
            lab = f"{(h % 12) or 12}:{mi:02d} {'AM' if h < 12 else 'PM'}".replace(":00 ", " ")
            A.check(g, m.get("time", "").startswith(lab), f"{m['summary']!r} {m['event_date']}: time label {m.get('time')!r} vs start {m['start_time']}")

    g = "ical.day_digests"
    digest = collections.defaultdict(set)
    for r in S.db_by("calendar_day"):
        for ln in r["content"].split("\n"):
            if ln.startswith("- "):
                digest[r["metadata"]["event_date"]].add(flat(re.sub(r" \((all day|[^()]*(?:AM|PM)[^()]*)\)( at .*)?$", "", ln[2:])))
    expd = collections.defaultdict(set)
    for e in uniq:
        d = e["date"]
        while d <= e["end_date"] and (d - e["date"]).days < 14:
            if d >= lo:
                expd[d.isoformat()].add(flat(e["summary"]))
            d += timedelta(days=1)
    differing = [d for d in sorted(set(expd) | set(digest)) if expd.get(d, set()) != digest.get(d, set()) and d >= S.today.isoformat()]   # past days are kept as history
    A.passed(g, len(set(expd) | set(digest)) - len(differing))
    A.check(g, len(differing) <= 6, f"{len(differing)} day digests differ from the feed (a day's churn is tolerated; more means the refresh is failing): {differing[:8]}")
    for d in differing[:6]:
        A.info(g, f"awaiting the daily refresh: {d}: missing {sorted(expd.get(d, set()) - digest.get(d, set()))[:2]}, extra {sorted(digest.get(d, set()) - expd.get(d, set()))[:2]}")

    # feed vs the master schedule's no-class days and the team schedule sheets
    g = "ical.vs_master_sheet"
    if S.master is not None:
        days = read_day_grid(S.master["Calendar AB + Special Schedule"])
        nos = re.compile(r"\bno school\b|\bschool closed\b|\bcampus closed\b|\bholiday\b|\bbreak\b", re.I)
        gaps_feed = []
        for e in uniq:
            if not nos.search(e["summary"]) or "practice" in e["summary"].lower() or "game" in e["summary"].lower():
                continue
            d = e["date"]
            while d <= e["end_date"] and (d - e["date"]).days < 14:
                v = days.get(d)
                if d.weekday() < 5 and v is not None and v["type"] in workbooks.DAY_TYPES:
                    A.check(g, False, f"{d}: feed says {e['summary']!r} but the master sheet has an {v['type']} class day", "conflict")
                else:
                    A.passed(g)
                d += timedelta(days=1)
        for d, v in days.items():
            if d >= lo and d.weekday() < 5 and v["type"] not in workbooks.DAY_TYPES and v["type"]:
                has = any(nos.search(e["summary"]) and e["date"] <= d <= e["end_date"] for e in uniq)
                if not has and "no class" in (v["type"] + v["label"]).lower():
                    gaps_feed.append(f"{d} {v['type']}")
                A.passed(g)

        A.info(g, f"{len(gaps_feed)} no-classes days on the master sheet have no no-school event in the feed (the day records in the index cover them): {', '.join(gaps_feed[:8])}")
    g = "ical.vs_team_sheets"
    games = [r["metadata"] for r in S.db_by("calendar_sheet") if r["metadata"].get("kind") == "game"]
    for m in games:
        sport = re.sub(r"\s*-\s*Game.*$", "", m["summary"]).strip()
        same = [e for e in uniq if e["date"].isoformat() == m["event_date"] and norm(sport) in norm(e["summary"]) and "practice" not in e["summary"].lower()]
        for e in same:
            A.check(g, e["start"] == m.get("start_time") or e["all_day"] or not m.get("start_time"),
                    f"{m['event_date']} {sport}: sheet time {m.get('start_time')}, feed {e['summary']!r} at {e['start']}", "conflict")
            if m.get("home_away"):
                ha = "home" if re.search(r"\bhome\b", e["summary"], re.I) else "away" if re.search(r"\baway\b", e["summary"], re.I) else ""
                A.check(g, not ha or ha == m["home_away"], f"{m['event_date']} {sport}: sheet says {m['home_away']}, feed {e['summary']!r}", "conflict")


# --------------------------------------------------------------------------- freshness and contradictions
def audit_fresh(A: Audit, S: Sources):
    g = "fresh.metadata"
    if S.db is None:
        return A.skip(g, f"database not read ({S.db_err or 'offline'})")
    cur = {c["chunk_id"] for c in S.local_chunks}
    miss = [r for r in S.db if "source_date" not in (r["metadata"] or {}) or "indexed_at" not in (r["metadata"] or {})]
    pending = [r for r in miss if r["chunk_id"] not in cur]
    for r in miss:
        if r["chunk_id"] in cur:
            A.check(g, False, f"{r['chunk_id']} ({r['metadata'].get('source')}: {r['metadata'].get('title', '')[:40]!r}) has no indexed_at/source_date")
    A.passed(g, len(S.db) - len(miss))
    if pending:
        A.info(g, f"{len(pending)} older rows without freshness metadata are not produced by the current pipeline (superseded copies; prune candidates, see NEEDS_USER.md)")
    for r in S.db:
        m = r["metadata"]
        if "source_date" in m:
            A.check(g, bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(m["source_date"]))), f"{r['chunk_id']}: bad source_date {m['source_date']!r}")
            A.check(g, str(m["source_date"]) <= (S.today + timedelta(days=1)).isoformat(), f"{r['chunk_id']}: source_date {m['source_date']} is in the future")

    g = "fresh.prior_year_flagged"
    docs = collections.defaultdict(list)
    for r in S.db:
        m = r["metadata"]
        if m.get("source") not in STRUCTURED:
            docs[m.get("source_url")].append(r)
    nstale = unverifiable = 0
    pages, pages_by_prefix = {}, {}
    for pth in config.PAGES_DIR.glob("*.json") if config.PAGES_DIR.exists() else []:
        d = json.loads(pth.read_text(encoding="utf-8"))
        pages[d["url"]] = d
        pages_by_prefix[hashlib.sha1(d.get("id_seed", d["url"]).encode()).hexdigest()[:12]] = d      # a refreshed copy has its own chunk-id namespace
    for url, rs in docs.items():
        page = pages_by_prefix.get(rs[0]["chunk_id"][:12]) or pages.get(url)
        if page is not None:             # the classifier reads the saved page (a short first line such as "Last updated: ..." never becomes a chunk)
            text = (page.get("title") or "") + "\n" + (page.get("text") or "")
        else:
            body = "\n".join(r["content"].split("\n\n", 1)[-1] for r in sorted(rs, key=lambda r: r["chunk_id"]))
            text = (rs[0]["metadata"].get("title", "")) + "\n" + body
        c = freshness.classify(text, rs[0]["metadata"].get("source", ""), S.today, whole_page=freshness.is_public_site(url or ""))
        flags = {bool(r["metadata"].get("stale")) for r in rs if "stale" in r["metadata"]}
        if not flags:
            continue
        if page is None and flags == {True} and not c["stale"]:
            # no saved copy of the page here (CI): a short first line such as "Last updated: 5/09/2023" never becomes a chunk, so the chunk text
            # alone cannot show why the page was flagged stale. Only the other direction (flagged current, text says old) is checkable.
            A.passed(g)
            unverifiable += 1
            continue
        nstale += c["stale"]
        title = rs[0]["metadata"].get("title", "")[:50]
        # chunk-level: a chunk of a stale document must itself be flagged (every chunk carries the page's verdict)
        A.check(g, flags == {c["stale"]} or (not c["stale"] and flags == {True} and len(rs) == 1),
                f"{title!r} ({rs[0]['metadata'].get('source')}): flagged stale={sorted(flags)} but its text says stale={c['stale']} ({c['stale_reason']})")
    A.info(g, f"{nstale} documents classified as written for an earlier school year")
    if unverifiable:
        A.info(g, f"{unverifiable} documents flagged stale could not be re-derived here (their saved page is not on this machine); checked by local runs")
    for src in ("public_site",):
        old = [rs[0]["metadata"].get("title", "")[:50] for rs in docs.values() if rs[0]["metadata"].get("source") == src and any(r["metadata"].get("stale") for r in rs)]
        A.info(g, f"{src}: {len(old)} pages flagged stale: {old[:6]}")

    g = "fresh.same_document_two_years"
    byt = collections.defaultdict(dict)
    for url, rs in docs.items():
        m = rs[0]["metadata"]
        if m.get("source") == "public_site":
            continue                # distinct public pages share titles ("Lower School: Visit and Apply" is four pages): not one document in two years
        k = freshness.title_key(m.get("title", ""))
        sy = m.get("school_year")
        if k and sy:
            byt[k].setdefault(sy, []).append(rs)
    for k, ys in byt.items():
        if len(ys) > 1:
            newest = max(ys)
            for y, lst in ys.items():
                if y != newest:
                    A.check(g, all(r["metadata"].get("stale") for rs in lst for r in rs), f"{k!r}: {y} copy is not flagged stale although a {newest} copy exists", "conflict")
    A.info(g, f"{sum(1 for ys in byt.values() if len(ys) > 1)} titles exist in more than one school year")

    g = "fresh.expired_or_last_year_rows"
    old_cal = []
    for r in S.current():
        m = r["metadata"]
        if m.get("source") in ("school_day", "calendar", "calendar_sheet", "calendar_day"):
            d = m.get("event_date")
            if m.get("source") != "school_day":
                floor = calendar_feed.window_floor(S.today)
                if m.get("source") != "calendar_sheet" and d and d < floor.isoformat():      # team-sheet rows of past games are kept as history
                    old_cal.append(f"{m.get('source')} row {r['chunk_id']} dated {d}")
                A.passed(g)
            else:
                A.check(g, d is not None and "2026-08-01" <= d <= "2027-07-31", f"school_day record dated {d} is outside the 2026-27 school year")
    A.check(g, len(old_cal) <= LAG_MAX, f"{len(old_cal)} calendar rows are long past (the daily refresh removes expired rows; more than a day's worth means it is failing): {old_cal[:4]}")
    for x in old_cal[:LAG_MAX]:
        A.info(g, f"awaiting the daily refresh: {x}")
    for r in S.db_by("school_courses"):
        A.check(g, r["metadata"].get("semester", "").split(" ")[1][:4] in ("2026", "2027") if len(r["metadata"].get("semester", "").split(" ")) > 1 else False,
                f"course record {r['chunk_id']} has semester {r['metadata'].get('semester')!r}")

    g = "fresh.conflicting_facts"
    # key facts stated differently in another document: campus phone/address, A' meaning handled in bell.index
    facts = " ".join(r["content"] for r in S.db_by("school_facts"))
    phones = set(re.findall(r"\b650-\d{3}-\d{4}\b", facts))
    for r in S.db:
        m = r["metadata"]
        if m.get("source") in ("school_facts",) or m.get("stale"):
            continue
        t = r["content"]
        for ph in re.findall(r"San Mateo (?:Campus )?(?:Front Desk|Main Office)(?: Phone)?:?\s*(650[-. ]\d{3}[-. ]\d{4})", t, re.I):
            A.check(g, ph.replace(".", "-").replace(" ", "-") in phones, f"{m.get('title', '')[:45]!r}: San Mateo front desk phone {ph} differs from the confirmed facts", "conflict")
        for addr in re.findall(r"\b\d{2,4} [A-Z][\w.]*(?: [A-Z][\w.]*)? (?:Avenue|Ave\.?|Street|St\.?|Boulevard|Blvd\.?),? San Mateo", t):
            A.check(g, "131 E 28th" in addr or "131 East 28th" in addr, f"{m.get('title', '')[:45]!r}: San Mateo address {addr!r} differs from 131 E 28th Ave", "conflict")


def audit_orphans(A: Audit, S: Sources):
    g = "index.orphan_rows"
    if S.db is None:
        return A.skip(g, f"database not read ({S.db_err or 'offline'})")
    if not S.local_chunks:
        return A.skip(g, "no local chunks.json to compare with (run the chunk stage)")
    orph = S.orphans()
    by_src = collections.Counter((r["metadata"] or {}).get("source") for r in orph)
    A.info(g, f"{len(orph)} rows are no longer produced by the pipeline: {dict(by_src)}")
    for r in orph:
        m = r["metadata"] or {}
        if m.get("source") in STRUCTURED:
            A.check(g, bool(m.get("superseded")), f"{m.get('source')} row {r['chunk_id']} ({m.get('title', '')[:50]!r}) is no longer produced by the pipeline and not flagged superseded (run the index stage; delete it later, see NEEDS_USER.md)", "orphan")
        elif "source_url" not in m:
            A.check(g, False, f"row {r['chunk_id']} has no source_url (old pipeline)", "orphan")
    A.passed(g, len(S.db) - len(orph))


SPORT_WORDS = [("beach volleyball", "beach volleyball"), ("volleyball", "volleyball"), ("basketball", "basketball"), ("soccer", "soccer"), ("tennis", "tennis"),
               ("golf", "golf"), ("swim", "swim"), ("track", "track"), ("cross", "cross-country"), ("flag football", "flag football"), ("ultimate", "ultimate frisbee")]


def sport_of(text: str) -> str | None:
    t = text.lower()
    for key, name in SPORT_WORDS:
        if key in t:
            return name
    return None


def sports_in(text: str) -> set[str]:
    t = text.lower()
    return {name for key, name in SPORT_WORDS if key in t}


def md_sections(name: str) -> dict[str, str]:
    from pipeline import facts as F
    _, _, secs = F.split_file(F.DIR / name)
    return {h: b for h, b, _ in secs}


def audit_canon(A: Audit, S: Sources):
    """Canonical lists (knowledge/*.md and pipeline/canonical.py): sourced, dated, consistent with each other and with the schedules."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_facts
    from pipeline import facts as F, safety
    g = "canon.verify"
    pages_ok = any(config.PAGES_DIR.glob("*.json")) if config.PAGES_DIR.exists() else False
    if not pages_ok:
        A.skip(g, "no stored source pages (data/pipeline/pages) to verify the fact files against")
    else:
        n, problems = check_facts.check()
        for p in problems:
            A.check(g, False, p)
        A.passed(g, n)

    # --- every record of the fact files and canonical lists carries its source, as-of date and school year
    g = "canon.freshness"
    recs = [c for c in S.local_chunks if (c["metadata"] or {}).get("source") in ("school_canonical",) or
            ((c["metadata"] or {}).get("source") == "school_facts" and (c["metadata"] or {}).get("as_of"))]
    A.check(g, len(recs) >= 30, f"only {len(recs)} canonical/dated fact records in chunks.json (run the chunk stage)") if S.local_chunks else A.skip(g, "no chunks.json")
    for c in recs:
        m, t = c["metadata"], c["content"]
        A.check(g, bool(re.search(r"^Source: .{8,}", t, re.M)) and bool(re.search(r"^Last updated: \d{4}-\d{2}-\d{2}", t, re.M)), f"{m.get('title', '')[:60]!r}: no Source/Last updated lines")
        A.check(g, bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(m.get("as_of", "")))) and m.get("as_of") <= S.today.isoformat(), f"{m.get('title', '')[:60]!r}: bad as_of {m.get('as_of')!r}")
        A.check(g, bool(re.search(r"20\d\d.\d\d|school year", t)), f"{m.get('title', '')[:60]!r}: names no school year")
        A.check(g, m.get("stale") in (False, None) and m.get("indexed_at") is not None, f"{m.get('title', '')[:60]!r}: stale or not annotated")
        A.check(g, len(t) <= 1900, f"{m.get('title', '')[:60]!r}: record is {len(t)} chars (shown cut by the context builder)")
    if S.db is not None:
        have = {r["chunk_id"] for r in S.db}
        miss = [c["metadata"].get("title", "")[:50] for c in recs if c["chunk_id"] not in have]
        A.check(g, not miss, f"{len(miss)} canonical records are not in the index yet: {miss[:3]} (python run_pipeline.py --stages chunk,index --additive-only ...)", "orphan")

    # --- sports: the offerings list vs the teams that actually have schedules, and the negative list vs everything
    g = "canon.sports"
    secs = md_sections("athletics_sports.md")
    off = next((b for h, b in secs.items() if h.startswith("Upper School interscholastic sports 2026-27")), "")
    ms = next((b for h, b in secs.items() if h.startswith("Middle School and Lower School")), "")
    neg = next((b for h, b in secs.items() if h.startswith("Does Nueva have football")), "")
    if not off or not ms:
        A.check(g, False, "athletics_sports.md lost its offerings sections")
    else:
        us_sports, ms_sports = sports_in(off.split("Levels:")[0]), sports_in(ms)
        for c in S.local_chunks:
            m = c["metadata"]
            if m.get("source") in ("calendar", "calendar_sheet") and m.get("kind") in ("game", "practice"):
                name = (m.get("summary") or "")
                sp = sport_of(name)
                lvl = "US" if name.startswith("US") else "MS" if name.startswith("MS") else None
                if lvl and sp:
                    A.check(g, sp in (us_sports if lvl == "US" else ms_sports), f"{lvl} team on the calendar with a sport missing from the offerings list: {name[:70]!r}")
        # gender must agree with the list for the US teams that have schedules
        for c in S.local_chunks:
            m = c["metadata"]
            if m.get("source") == "calendar_sheet" and m.get("kind") == "game" and str(m.get("summary", "")).startswith("US"):
                name = m["summary"].lower()
                sp = sport_of(name)
                gender = "girls" if "girls" in name else "boys" if "boys" in name else None
                segs = [x.lower() for x in re.split(r"[;\n]", off) if sp and sp in sports_in(x)]
                A.check(g, not gender or any(gender in x or "coed" in x or ("girls" in x and "boys" in x) for x in segs), f"{m['summary'][:60]!r}: {gender} {sp} is not in the offerings list")
        negs = [w.strip() for w in re.findall(r"\b(tackle football|ice hockey|field hockey|baseball|softball|lacrosse|rugby|wrestling|water polo)\b", neg)]
        for w in negs:
            A.check(g, w not in off.lower() and w not in ms.lower(), f"{w!r} is listed as NOT offered but appears in an offerings list")
        if S.db is not None:
            for r in S.db:
                m = r["metadata"] or {}
                if m.get("source") in ("calendar", "calendar_sheet") and m.get("kind") in ("game", "practice") and not m.get("superseded"):
                    for w in negs:
                        A.check(g, w not in (m.get("summary") or "").lower(), f"a {w!r} team is scheduled on the calendar ({m.get('summary')!r}) but listed as not offered")
        A.info(g, f"US sports in the offerings list: {sorted(us_sports)}; MS: {sorted(ms_sports)}")

    # --- clubs directory vs the workbook, courses directory vs the catalog, no-class days vs the day records
    from pipeline import canonical
    g = "canon.clubs"
    if S.clubs_path:
        clubs = canonical.clubs_rows(S.clubs_path)
        dirs = [c for c in S.local_chunks if (c["metadata"] or {}).get("list") == "clubs"]
        if dirs:
            body = "\n".join(c["content"] for c in dirs if "by meeting day" in c["metadata"]["title"])
            alpha = "\n".join(c["content"] for c in dirs if "Alphabetical" in c["metadata"]["title"])
            A.check(g, len(clubs) == len({c["name"] for c in clubs}), "duplicate club names in the workbook")
            for c in clubs:
                A.check(g, f"- {c['name']}" in body and f"- {c['name']}" in alpha, f"club {c['name']!r} missing from the directory")
            A.check(g, f"{len(clubs)} clubs and affinity groups" in body, f"directory total does not say {len(clubs)} clubs")
        else:
            A.skip(g, "no clubs directory in chunks.json")
    else:
        A.skip(g, "no clubs workbook")
    g = "canon.courses"
    cat = next((p for p in (json.loads(x.read_text(encoding="utf-8")) for x in config.PAGES_DIR.glob("*.json")) if p.get("title", "").strip() == "2026-27 Course Catalog"), None) if config.PAGES_DIR.exists() else None
    cdir = [c for c in S.local_chunks if (c["metadata"] or {}).get("list") == "courses"]
    if cat and cdir:
        text = flat(cat["text"])
        n = 0
        for c in cdir:
            for ln in c["content"].splitlines():
                if ln.startswith("- "):
                    n += 1
                    A.check(g, flat(ln[2:]) in text, f"course {ln[2:60]!r} is not in the 2026-27 catalog text")
        A.check(g, n >= 100, f"only {n} courses listed")
    else:
        A.skip(g, "no catalog page or courses directory")
    g = "canon.calendar"
    nc = [c for c in S.local_chunks if (c["metadata"] or {}).get("list") == "us_no_class_days"]
    if S.master is not None and nc:
        days = workbooks.parse_days(S.master)
        off_days = sorted(d for d, v in days.items() if any("no class" in l.lower() for l in v["labels"]))
        body = "\n".join(c["content"] for c in nc)
        # every listed range must start and end on a no-class day of the sheet
        for a, b in re.findall(r"- (\w{3} \w{3} \d+, \d{4})(?: to (\w{3} \w{3} \d+, \d{4}))?:", body):
            for x in filter(None, (a, b)):
                d = datetime.strptime(x, "%a %b %d, %Y").date()
                A.check(g, d in off_days, f"{x} is listed as a no-class day but the master schedule does not say so")
        listed = sum(1 for ln in body.splitlines() if ln.startswith("- "))
        A.check(g, listed >= 15, f"only {listed} no-class ranges listed")
        ce = " ".join(md_sections("calendar_essentials.md").values())
        for label, a, b in (("Fall Break", date(2026, 11, 23), date(2026, 11, 27)), ("Winter Break", date(2026, 12, 21), date(2027, 1, 1)),
                            ("February Break", date(2027, 2, 15), date(2027, 2, 19)), ("Spring Break", date(2027, 3, 29), date(2027, 4, 2))):
            labelled = [d for d in off_days if (days[d]["type"] or "").lower() == label.lower()]
            A.check(g, bool(labelled) and min(labelled) == a and max(labelled) == b, f"{label}: master schedule says {min(labelled) if labelled else None}..{max(labelled) if labelled else None}, essentials say {a}..{b}")
            A.check(g, bool(re.search(re.escape(a.strftime("%b %-d")) + r"(, \d{4})? to " + re.escape(b.strftime("%a %b %-d")), ce)), f"{label}: calendar_essentials.md does not state {a:%b %-d} to {b:%a %b %-d}")
        first = min(days)
        A.check(g, f"{first.strftime('%B %-d, %Y')}" in ce and "First day of classes" in ce, f"first class day {first} (master schedule) is not what calendar_essentials.md says")
    else:
        A.skip(g, "no master schedule or no-class list")

    # --- contacts: business addresses only, no secrets in the fact files, no conflicting phone numbers for the same office
    g = "canon.contacts"
    allfacts = {}
    for p in sorted(F.DIR.glob("*.md")):
        allfacts[p.name] = p.read_text(encoding="utf-8")
    for name, t in allfacts.items():
        A.check(g, not safety.find(t), f"{name}: contains a credential/personal-number pattern {safety.find(t)}")
        for em in set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]*\w", t)):
            A.check(g, em.lower().endswith("@nuevaschool.org") or em.lower() == "extendedday@nuevaschool.org", f"{name}: non-school address {em}")
    phones = collections.defaultdict(set)
    for name, t in allfacts.items():
        for ln in t.splitlines():
            for ph in re.findall(r"\b\d{3}-\d{3}-\d{4}\b", ln):
                m = re.search(r"(front desk|main office|main line|business|admissions|financial assistance)", ln, re.I)
                if m:
                    phones[(m.group(1).lower(), ph)].add(name)
    by_office = collections.defaultdict(set)
    for (office, ph), _ in phones.items():
        by_office[office].add(ph)
    A.info(g, f"office numbers stated: { {k: sorted(v) for k, v in by_office.items()} }")

    # --- the index: nothing sensitive left unflagged, no unflagged duplicate copies, refreshed public pages present
    g = "canon.index_hygiene"
    if S.db is None:
        return A.skip(g, f"database not read ({S.db_err or 'offline'})")
    import flag_rows
    live = [r for r in S.db if not (r["metadata"] or {}).get("superseded")]
    for r in live:
        m = r["metadata"] or {}
        if m.get("source") in ("calendar", "calendar_day", "calendar_sheet", "lunch"):
            if safety.find(r["content"]):
                A.info(g, f"{r['chunk_id']}: calendar text with a meeting passcode awaits the next daily refresh (the calendar stage now scrubs it)")
            continue
        A.check(g, not safety.find(r["content"]), f"{r['chunk_id']} ({m.get('title', '')[:40]!r}): unflagged sensitive content {safety.find(r['content'])}", "sensitive")
    pending = flag_rows.plan(S.db_all)
    A.check(g, not pending, f"{len(pending)} rows still match a flag rule (python scripts/flag_rows.py --apply)")
    for u in config.PUBLIC_PAGES:
        pref = hashlib.sha1((u + "#public").encode()).hexdigest()[:12]
        A.check(g, any(r["chunk_id"].startswith(pref) for r in S.db), f"refreshed public page {u} is not in the index (run --stages public_pages,chunk,index --additive-only)", "orphan")
    flagged = [r for r in S.db_all if (r["metadata"] or {}).get("flagged_at")]
    A.info(g, f"{len(flagged)} rows are flagged superseded by scripts/flag_rows.py and waiting for the owner's delete (NEEDS_USER.md)")


def audit_email(A: Audit, S: Sources):
    """Rows written by scripts/email_ingest.py: metadata sanity and privacy of the stored text."""
    import guard
    g = "email.rows"
    if S.db is None:
        return A.skip(g, f"database not read ({S.db_err or 'offline'})")
    rows = S.email_db
    A.info(g, f"{len(rows)} email chunks from {len({(r['metadata'] or {}).get('content_key') for r in rows})} emails")
    needed = ("source", "source_type", "title", "source_url", "source_date", "indexed_at", "school_year", "message_id_hash", "content_key", "sender_domain")
    for r in rows:
        m, cid = r["metadata"] or {}, r["chunk_id"]
        A.check(g, all(m.get(k) not in (None, "") for k in needed), f"{cid}: missing metadata {[k for k in needed if m.get(k) in (None, '')]}")
        A.check(g, m.get("source_type") == "email" and m.get("source") in ("email", "calendar_sheet"), f"{cid}: source/source_type are not 'email'")
        A.check(g, bool(re.fullmatch(r"email-[0-9a-f]{16}-(?:\d{3}|e[0-9a-f]{8}\d*)", cid)) and cid.startswith(f"email-{m.get('content_key')}-"), f"{cid}: chunk_id does not match its content_key")
        d = str(m.get("source_date", ""))
        A.check(g, bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)) and d <= (S.today + timedelta(days=1)).isoformat(), f"{cid}: bad or future source_date {d!r}")
        A.check(g, m.get("stale") is False, f"{cid}: an email must never be flagged stale")
        A.check(g, not m.get("superseded") or bool(m.get("superseded_by")), f"{cid}: an email row is only ever superseded by a newer email of the same thread (superseded_by)")
        A.check(g, bool(re.fullmatch(r"[0-9a-f]{16}", str(m.get("message_id_hash", "")))), f"{cid}: message_id_hash is not a 16-hex hash")
        A.check(g, "@" not in str(m.get("sender_domain", "")), f"{cid}: sender_domain holds an address, not a domain")
        meta_text = json.dumps(m)
        leaks = [x for x in guard.find_contacts(meta_text)]
        A.check(g, not leaks, f"{cid}: metadata contains a private contact detail ({len(leaks)})")
        A.check(g, "Source: school email" in r["content"], f"{cid}: content lacks its 'Source: school email, <date>' header")
    g = "email.privacy"
    for r in rows:
        bad = guard.find_contacts(r["content"])
        A.check(g, not bad, f"{r['chunk_id']}: contains {len(bad)} non-staff email address(es) or phone number(s) (re-ingest or remove it with scripts/email_remove.py)")
        why = guard.student_data_reason(r["content"])
        A.check(g, why is None, f"{r['chunk_id']}: looks like student-level data ({why}); remove it with scripts/email_remove.py")


def audit_share(A: Audit, S: Sources):
    """Rows written by the Drive ingest (chunk_id "share-<file hash>-..."): metadata sanity, no file ids or owner addresses, privacy of the text."""
    import guard
    g = "share.rows"
    if S.db is None:
        return A.skip(g, f"database not read ({S.db_err or 'offline'})")
    rows = S.share_db
    A.info(g, f"{len(rows)} shared-file chunks from {len({(r['metadata'] or {}).get('share_file_hash') for r in rows})} files")
    needed = ("source", "source_type", "title", "source_url", "source_date", "indexed_at", "school_year", "share_file_hash", "modified_time", "category")
    for r in rows:
        m, cid = r["metadata"] or {}, r["chunk_id"]
        A.check(g, all(m.get(k) not in (None, "") for k in needed), f"{cid}: missing metadata {[k for k in needed if m.get(k) in (None, '')]}")
        A.check(g, m.get("source_type") in ("sheet", "doc") and m.get("source") in ("share_sheet", "share_doc", "calendar_sheet"), f"{cid}: bad source/source_type")
        A.check(g, bool(re.fullmatch(r"share-[0-9a-f]{12}-[a-z0-9-]+", cid)) and cid.startswith(f"share-{m.get('share_file_hash')}-"), f"{cid}: chunk_id does not match its file hash")
        A.check(g, str(m.get("source_url", "")).startswith("share://"), f"{cid}: source_url must not be a link to the private file")
        A.check(g, m.get("stale") is False, f"{cid}: a shared-file row must not be stale")
        A.check(g, not guard.find_contacts(json.dumps(m)), f"{cid}: metadata contains a private contact detail")
        A.check(g, "Source: shared Google" in r["content"] or m.get("source") == "calendar_sheet", f"{cid}: content lacks its 'Source: shared Google Sheet/Doc' header")
    g = "share.privacy"
    for r in rows:
        bad = guard.find_contacts(r["content"])
        A.check(g, not bad, f"{r['chunk_id']}: contains {len(bad)} non-staff email address(es) or phone number(s) (remove with scripts/share_remove.py)")
        why = guard.student_data_reason(r["content"])
        A.check(g, why is None, f"{r['chunk_id']}: looks like student-level data ({why}); remove it with scripts/share_remove.py")


def audit_lunch(A: Audit, S: Sources):
    """The cafe's lunch rows (chunk_id "lunch-<campus>-<date>", written by pipeline/lunch.py): internal consistency of the indexed records.
    They are kept out of the pipeline-vs-index groups (a past month simply ages out when the owner prunes)."""
    from pipeline import lunch as L
    g = "lunch.rows"
    if S.db is None:
        return A.skip(g, f"database not read ({S.db_err or 'offline'})")
    rows = [r for r in S.db if freshness.is_lunch_row(r["chunk_id"], (r.get("metadata") or {}).get("source"))]
    if not rows:
        return A.skip(g, "no lunch rows indexed yet")
    A.info(g, f"{len(rows)} lunch rows: " + ", ".join(f"{c} {m} ({n})" for (c, m), n in sorted(collections.Counter(
        ((r['metadata'] or {}).get('campus'), (r['metadata'] or {}).get('month')) for r in rows).items(), key=str)))
    need = ("source", "campus", "event_date", "month", "month_label", "source_url", "pdf_name", "indexed_at", "source_date", "school_year")
    seen = collections.Counter()
    by_month = collections.defaultdict(dict)
    for r in rows:
        m, cid = r["metadata"] or {}, r["chunk_id"]
        absent = [k for k in need if m.get(k) in (None, "")] + [k for k in ("items", "closed") if k not in m]
        A.check(g, not absent, f"{cid}: missing metadata {absent}")
        A.check(g, m.get("campus") in L.CAMPUSES, f"{cid}: campus {m.get('campus')!r} is not san_mateo or hillsborough")
        d = str(m.get("event_date", ""))
        ok_date = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d))
        A.check(g, ok_date and cid == f"lunch-{m.get('campus')}-{d.replace('-', '')}", f"{cid}: chunk_id does not match its campus and date ({m.get('campus')}, {d})")
        if ok_date:
            dd = date.fromisoformat(d)
            A.check(g, dd.weekday() < 5 and m.get("weekday") == dd.strftime("%A"), f"{cid}: {d} is not a school weekday or its weekday label {m.get('weekday')!r} is wrong")
            A.check(g, m.get("month") == d[:7] and m.get("month_label") == L.month_label(d[:7]), f"{cid}: month {m.get('month')!r}/{m.get('month_label')!r} does not match {d}")
            by_month[(m.get("campus"), m.get("month"))][d] = r
        seen[(m.get("campus"), d)] += 1
        items = m.get("items") or []
        text = r["content"]
        if m.get("closed"):
            A.check(g, not items and "No lunch menu is listed" in text, f"{cid}: closed day with dishes or without the 'no menu' wording")
        else:
            A.check(g, bool(items) and all(x in text for x in items) and L.NOTE in text, f"{cid}: its items are not all in its text, or the cafe note is missing")
        A.check(g, str(m.get("source_url", "")).startswith("http"), f"{cid}: source_url is not a link")
        A.check(g, bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(m.get("source_date", "")))) and m.get("stale") is False and not m.get("superseded"),
                f"{cid}: freshness metadata wrong (source_date {m.get('source_date')!r}, stale {m.get('stale')!r})")
    for (campus, d), n in seen.items():
        A.check(g, n == 1, f"{campus} {d}: {n} duplicate rows for one day")
    g = "lunch.coverage"
    for (campus, month), recs in sorted(by_month.items(), key=str):
        y, mo = map(int, month.split("-"))
        d, missing = date(y, mo, 1), []
        while d.month == mo:
            if d.weekday() < 5 and d.isoformat() not in recs:
                missing.append(d.isoformat())
            d += timedelta(days=1)
        A.check(g, not missing, f"{campus} {month}: weekdays with no record: {missing}")
    g = "lunch.vs_parse"
    n_cmp = 0
    for (campus, month), recs in sorted(by_month.items(), key=str):
        p = L.PARSED_DIR / f"{campus}_{month}.json"
        if not p.exists():
            continue
        parsed = json.loads(p.read_text(encoding="utf-8"))
        n_cmp += 1
        want = {x["date"]: x for x in parsed["days"]}
        A.check(g, set(want) == set(recs), f"{campus} {month}: dates that differ between the parsed PDF and the index: {sorted(set(want) ^ set(recs))[:4]}")
        for dkey, x in want.items():
            r = recs.get(dkey)
            if r:
                A.check(g, bool((r["metadata"] or {}).get("closed")) == bool(x["closed"]) and list((r["metadata"] or {}).get("items") or []) == L.item_strings(x["items"]),
                        f"{campus} {dkey}: indexed record differs from the parsed PDF")
    if not n_cmp:
        A.skip(g, "no parsed menu files (data/pipeline/lunch/parsed) to compare with")
    # information only: a menu served on a day the school calendar says has no classes (two sources written by different people)
    g = "lunch.vs_calendar"
    nocls = {}
    for r in S.db:
        m = r["metadata"] or {}
        if m.get("source") == "school_day" and m.get("kind") == "no_classes":
            nocls.setdefault(m.get("event_date"), set()).add("san_mateo")
        if m.get("source") == "calendar_day":
            for ln in r["content"].splitlines():
                if re.search(r"\bno (?:ls|ms)\b.*classes|\bno (?:school|classes)\b", ln, re.I) and not re.search(r"\bno us\b", ln, re.I):
                    nocls.setdefault(m.get("event_date"), set()).add("hillsborough")
    for (campus, month), recs in sorted(by_month.items(), key=str):
        for dkey, r in recs.items():
            if not (r["metadata"] or {}).get("closed") and campus in nocls.get(dkey, ()):
                A.info(g, f"{campus} {dkey}: the cafe menu lists lunch but the school calendar shows no classes that day (check with the cafe)")
    A.passed(g)


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="no network: source files only")
    ap.add_argument("--db-json", help="saved dump of the chunk table (list of {chunk_id, content, metadata})")
    ap.add_argument("--today", type=date.fromisoformat, default=date.today())
    ap.add_argument("--json", help="write the results here")
    ap.add_argument("--strict", action="store_true", help="a skipped group counts as a failure")
    ap.add_argument("--ci", action="store_true", help="unattended CI mode: only the groups that make sense without local workbooks/pages; see the docstring")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    known = json.loads(KNOWN_FILE.read_text()) if KNOWN_FILE.exists() else {}
    A = Audit(known, args.verbose, ci=args.ci)
    S = Sources(args)
    if S.db is None and not args.offline:
        print(f"note: database not read ({S.db_err}); database groups are skipped\n")
        if args.ci:
            A.check("ci.database", False, f"the index could not be read ({S.db_err}); nothing could be verified")
    for name, fn in (("days", audit_days), ("bell", audit_bell), ("courses", audit_courses), ("clubs", audit_clubs),
                     ("teams", audit_teams), ("ical", audit_ical), ("fresh", audit_fresh), ("index", audit_orphans), ("email", audit_email), ("share", audit_share), ("lunch", audit_lunch), ("canon", audit_canon)):
        if args.ci and name not in CI_GROUPS:
            for grp_name, why in CI_SKIPPED.get(name, []):
                A.skip(grp_name, f"skipped in CI: {why}")
            continue
        try:
            fn(A, S)
        except Exception as e:
            import traceback
            A.check(f"{name}.crash", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")
    ok = A.report()
    if args.json:
        Path(args.json).write_text(json.dumps(A.as_json(), indent=1))
    if args.strict and any(d["skip"] for d in A.g.values()):
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
