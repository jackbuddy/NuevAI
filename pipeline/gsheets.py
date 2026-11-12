"""Google Sheets API v4 (spreadsheets.get with grid data) -> clean table text, chunks and event records.

A plain CSV export loses everything that matters in a hand-built schedule workbook, so this works on the structured response:

  cells            the FORMATTED value of every cell (what a person sees: 'Mon 11/16/26', '6:00 PM'); when Google sends only the raw number of a
                   date/time-formatted cell it is converted from the serial day number / day fraction
  merged cells     a vertical merge (one date spanning two rows) is filled down; a horizontal merge over a header row is a band
                   ('Home' over 'Team | Score') and is folded into the column labels
  tables           blank rows separate stacked tables on one tab; a caption row, a repeated header row and a second table under the same header
                   are recognised; side-by-side tables are split on blank columns when both sides carry their own Date header
  hidden           rows hidden by hand or by a filter and hidden columns are left out and counted in the text ('3 hidden rows not shown')
  text format      the same CSV-style table text the pipeline's existing Google Sheets path produces (pipeline/sheets.py reads it unchanged),
                   preceded by Title / Section / Source lines like every other chunk
  chunking         WHOLE ROWS only: a table is cut BETWEEN rows, never inside one, and the header row is repeated in every chunk
  events           schedule tables also become dated calendar_sheet event records (the schema pipeline/sheets.event_chunk writes)
  privacy          roster-like tabs (student names with grades/positions/contacts, sign-up and response sheets) are skipped, never indexed; people's
                   contact details are redacted; columns of volunteers / sign-ups / parents are dropped

Pure functions: no network, no database. pipeline/share_ingest.py does the I/O.
"""
import csv
import hashlib
import io
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import freshness   # noqa: E402
import guard       # noqa: E402

from . import schedule_text as st   # noqa: E402

MAX_TABS = 25
MAX_ROWS = 2000                 # rows read per tab
MAX_COLS = 40                   # columns read per tab (A..AN)
MAX_CELL_CHARS = 600
MAX_CHUNKS_PER_FILE = 150
CHUNK_TARGET = 1500             # a whole table up to this size stays in ONE chunk; longer tables are cut between rows
CHUNK_HARD = 1900               # a single row longer than this is kept whole anyway (it is never cut)

STRICT_TITLE = re.compile(r"roster|rsvp|diploma|transcript|salary|payroll|medical|attendance|sign[- ]?up|responses|submissions|\bgrades?\b|\bgpa\b|"
                          r"emergency contact|contact (?:list|sheet|info)|phone (?:tree|list)|directory", re.I)
STUDENT_HEADER = re.compile(r"student|first\s*name|last\s*name|full\s*name|guardian|\bparents?\b|birth|\bdob\b|home address|\bssn\b|\bgpa\b|"
                            r"emergency|allerg|medical|\bid\s*(?:#|number|no)?$|insurance", re.I)
PEOPLE_COLUMN = re.compile(r"sign[- ]?up|volunteer|snack|\bparents?\b|guardian|\bfamily\b|families|bringing|\bdriver|carpool|chaperone", re.I)
NAMEISH_HEADER = re.compile(r"^(?:player|athlete|name|students?|member|participant|swimmer|runner)s?\b", re.I)
ROSTER_SIBLING = re.compile(r"grade|class of|\byear\b|gpa|position|jersey|^#$|number|parent|guardian|advis|e-?mail|phone|cell|dob|birth|height|weight|size|"
                            r"\bpos\b|status|eligib|forms?|physical", re.I)
FIRST_LAST = re.compile(r"^[A-Z][a-z]+(?:[-'’][A-Z][a-z]+)?(?: [A-Z]\.?)? [A-Z][a-z]+(?:[-'’][A-Z][a-z]+)?$")


# ----------------------------------------------------------------------------------------------------------------- grid
class Grid:
    def __init__(self, title: str, sheet_id, hidden: bool = False):
        self.title, self.sheet_id, self.hidden = title, sheet_id, hidden
        self.rows: list[list[str]] = []          # visible rows only
        self.row_nums: list[int] = []            # their 1-based sheet row numbers
        self.bands: dict[int, list[tuple[int, int, str]]] = {}      # index into rows -> [(first col, last col, text)] of horizontal merges
        self.hidden_rows = 0
        self.hidden_cols = 0
        self.notes: list[str] = []


def cell_text(cell: dict | None, ref: date) -> str:
    """The text a person sees in a cell. Falls back to the raw value (with serial dates/times converted) when no formatted value was sent."""
    if not cell:
        return ""
    fv = cell.get("formattedValue")
    if fv is not None and str(fv).strip() != "":
        txt = str(fv)
    else:
        ev = cell.get("effectiveValue") or {}
        if "numberValue" in ev:
            n = ev["numberValue"]
            kind = ((cell.get("effectiveFormat") or cell.get("userEnteredFormat") or {}).get("numberFormat") or {}).get("type", "")
            if kind in ("DATE", "DATE_TIME") and (d := st.serial_to_date(float(n))):
                txt = d.strftime("%a %-m/%-d/%y")
                frac = float(n) - int(float(n))
                if kind == "DATE_TIME" and frac:
                    h, m = divmod(round(frac * 1440), 60)
                    txt += " " + st.fmt_time(h % 24, m)
            elif kind == "TIME":
                h, m = divmod(round((float(n) % 1) * 1440), 60)
                txt = st.fmt_time(h % 24, m)
            else:
                txt = ("%d" % n) if float(n).is_integer() else repr(n)
        elif "stringValue" in ev:
            txt = str(ev["stringValue"])
        elif "boolValue" in ev:
            txt = "TRUE" if ev["boolValue"] else "FALSE"
        else:
            txt = ""
    txt = re.sub(r"\s*\n\s*", " / ", txt.replace("\r", "")).replace("\xa0", " ")
    txt = re.sub(r"[ \t]+", " ", txt).strip()
    if len(txt) > MAX_CELL_CHARS:
        txt = txt[:MAX_CELL_CHARS].rsplit(" ", 1)[0] + " ..."
    return txt


def grid_from_sheet(sheet: dict, ref: date) -> Grid:
    """One entry of spreadsheets.get()['sheets'] (with includeGridData) -> Grid."""
    props = sheet.get("properties") or {}
    g = Grid(str(props.get("title") or "Sheet"), props.get("sheetId"), bool(props.get("hidden")))
    cells: dict[tuple[int, int], str] = {}
    row_hidden: dict[int, bool] = {}
    col_hidden: set[int] = set()
    for block in sheet.get("data") or []:
        r0, c0 = block.get("startRow", 0) or 0, block.get("startColumn", 0) or 0
        for i, rd in enumerate(block.get("rowData") or []):
            for j, cell in enumerate((rd or {}).get("values") or []):
                t = cell_text(cell, ref)
                if t:
                    cells[(r0 + i, c0 + j)] = t
        for i, rm in enumerate(block.get("rowMetadata") or []):
            if rm and (rm.get("hiddenByUser") or rm.get("hiddenByFilter")):
                row_hidden[r0 + i] = True
        for j, cm in enumerate(block.get("columnMetadata") or []):
            if cm and cm.get("hiddenByUser"):
                col_hidden.add(c0 + j)
    bands: dict[int, list[tuple[int, int, str]]] = {}
    for mg in sheet.get("merges") or []:
        a, b = mg.get("startRowIndex", 0), mg.get("endRowIndex", 0)
        c, d = mg.get("startColumnIndex", 0), mg.get("endColumnIndex", 0)
        top = cells.get((a, c), "")
        if not top:
            continue
        if b - a > 1:                                       # a date (or note) spanning several rows: it belongs to each of them
            for r in range(a + 1, b):
                cells.setdefault((r, c), top)
        if d - c > 1 and b - a == 1:                        # a header band: remembered, not repeated into the cells
            bands.setdefault(a, []).append((c, d - 1, top))
    if not cells:
        return g
    max_r = max(r for r, _ in cells)
    max_c = min(max(c for _, c in cells), MAX_COLS - 1)
    cols = [c for c in range(max_c + 1) if c not in col_hidden]
    g.hidden_cols = len(col_hidden & set(range(max_c + 1)))
    pos = {c: k for k, c in enumerate(cols)}
    for r in range(max_r + 1):
        if row_hidden.get(r):
            g.hidden_rows += 1
            continue
        g.rows.append([cells.get((r, c), "") for c in cols])
        g.row_nums.append(r + 1)
        if r in bands:
            g.bands[len(g.rows) - 1] = [(pos.get(c, 0), pos.get(d, pos.get(c, 0)), t) for c, d, t in bands[r] if c in pos]
    return g


# ----------------------------------------------------------------------------------------------------------------- tables
class Table:
    def __init__(self, tab: str, title: str = "", header: list[str] | None = None):
        self.tab, self.title, self.header = tab, title, header or []
        self.rows: list[list[str]] = []
        self.row_nums: list[int] = []
        self.dropped_columns: list[str] = []
        self.repeated_headers = 0

    @property
    def first_row(self):
        return self.row_nums[0] if self.row_nums else None

    @property
    def last_row(self):
        return self.row_nums[-1] if self.row_nums else None


def _nonempty(cells) -> int:
    return sum(1 for c in cells if c.strip())


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def header_like(cells: list[str], ref: date) -> bool:
    ne = [c.strip() for c in cells if c.strip()]
    if len(ne) < 2:
        return False
    texty = sum(1 for c in ne if not re.fullmatch(r"[\d.,:/$%\- ]+", c) and st.parse_cell_date(c, ref) is None and not st.parse_time(c)[2])
    return texty / len(ne) >= 0.7


def _same_header(a: list[str], b: list[str]) -> bool:
    la, lb = [x.strip().lower() for x in a], [x.strip().lower() for x in b]
    ne = [(x, y) for x, y in zip(la, lb) if x or y]
    return bool(ne) and sum(1 for x, y in ne if x == y) / len(ne) >= 0.6


def _col_groups(rows: list[list[str]]) -> list[tuple[int, int]]:
    width = max((len(r) for r in rows), default=0)
    used = [any(c < len(r) and r[c].strip() for r in rows) for c in range(width)]
    groups, start = [], None
    for c, u in enumerate(used + [False]):
        if u and start is None:
            start = c
        elif not u and start is not None:
            groups.append((start, c - 1))
            start = None
    return groups


def _group_header(rows: list[list[str]], lo: int, hi: int, ref: date) -> list[str]:
    for r in rows:
        seg = (r + [""] * (hi + 1))[lo:hi + 1]
        if _nonempty(seg) >= 2 and header_like(seg, ref):
            return [x.strip().lower() for x in seg]
    return []


def _should_split(rows, g1, g2, ref) -> bool:
    h1, h2 = _group_header(rows, *g1, ref), _group_header(rows, *g2, ref)
    if not h1 or not h2:
        return False
    both_dated = "date" in st.map_columns(h1) and "date" in st.map_columns(h2)
    overlap = len(set(h1) & set(h2) - {""}) / max(1, len(set(h2) - {""}))
    return both_dated or overlap >= 0.5


def split_tables(grid: Grid, ref: date) -> list[Table]:
    """Stacked and side-by-side tables of one tab -> Tables (a table's rows keep their sheet row numbers)."""
    blocks, cur = [], []
    for i, r in enumerate(grid.rows):
        if _nonempty(r):
            cur.append(i)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    tables: list[Table] = []
    caption: list[str] = []                     # a lone title row separated from its table by blank rows ('Varsity Boys Soccer 2026-27')
    for bi, idxs in enumerate(blocks):
        if len(idxs) == 1 and _nonempty(grid.rows[idxs[0]]) == 1 and bi + 1 < len(blocks):
            caption.append(next(c.strip() for c in grid.rows[idxs[0]] if c.strip()))
            continue
        rows = [grid.rows[i] for i in idxs]
        groups = _col_groups(rows)
        parts = [(0, max(len(r) for r in rows) - 1)] if len(groups) <= 1 else None
        if parts is None:
            merged, cur_g = [], groups[0]
            for g in groups[1:]:
                if _should_split(rows, cur_g, g, ref):
                    merged.append(cur_g)
                    cur_g = g
                else:
                    cur_g = (cur_g[0], g[1])
            merged.append(cur_g)
            parts = merged
        for lo, hi in parts:
            sub = [(i, (grid.rows[i] + [""] * (hi + 1))[lo:hi + 1]) for i in idxs]
            sub = [(i, c) for i, c in sub if _nonempty(c)]
            if sub:
                tables += _build(grid, sub, lo, ref, caption)
        caption = []
    return tables


def _build(grid: Grid, sub: list[tuple[int, list[str]]], lo: int, ref: date, pre_titles: list[str] | None = None) -> list[Table]:
    out: list[Table] = []
    titles: list[str] = list(pre_titles or [])
    k = 0
    while k < len(sub) and _nonempty(sub[k][1]) == 1 and k + 1 < len(sub) and sub[k][0] not in grid.bands:         # caption rows above the header
        titles.append(next(c.strip() for c in sub[k][1] if c.strip()))
        k += 1
    hidx = next((j for j in range(k, len(sub)) if header_like(sub[j][1], ref)), None)
    if hidx is None or (hidx - k > 2):
        hidx = None
    cur = Table(grid.title, " - ".join(titles))
    if hidx is not None:
        hdr = [c.strip() for c in sub[hidx][1]]
        band_row = grid.bands.get(sub[hidx - 1][0]) if hidx > 0 and sub[hidx - 1][0] == sub[hidx][0] - 1 else None
        if band_row:                                                                 # 'Home' over 'Team | Score' -> 'Home Team', 'Home Score'
            for c0, c1, text in band_row:
                for c in range(max(c0 - lo, 0), min(c1 - lo, len(hdr) - 1) + 1):
                    if hdr[c] and text.lower() not in hdr[c].lower():
                        hdr[c] = f"{text} {hdr[c]}"
        for j in range(k, hidx):                                                     # a non-caption row above the header: note it in the title
            if not (band_row and j == hidx - 1) and _nonempty(sub[j][1]):
                cur.title = (cur.title + " - " if cur.title else "") + " ".join(c.strip() for c in sub[j][1] if c.strip())
        cur.header = [h if h else (f"Col {_col_letter(lo + c)}" if any(r[1][c].strip() for r in sub[hidx + 1:] if c < len(r[1])) else "") for c, h in enumerate(hdr)]
        data = sub[hidx + 1:]
    else:
        data = sub[k:]
        if titles:
            cur.title = " - ".join(titles)
    out.append(cur)
    for j, (i, cells) in enumerate(data):
        if cur.header and _same_header(cells, cur.header):                           # the header repeated further down
            cur.repeated_headers += 1
            continue
        if cur.header and _nonempty(cells) == 1 and j + 1 < len(data) and _same_header(data[j + 1][1], cur.header):      # 'Home games' caption + header again
            cur = Table(grid.title, next(c.strip() for c in cells if c.strip()), cur.header)
            out.append(cur)
            continue
        cur.rows.append(cells)
        cur.row_nums.append(grid.row_nums[i])
    return [t for t in out if t.rows or t.title]


def _drop_empty_columns(t: Table) -> None:
    width = max([len(t.header)] + [len(r) for r in t.rows]) if (t.header or t.rows) else 0
    keep = [c for c in range(width) if (c < len(t.header) and t.header[c].strip()) or any(c < len(r) and r[c].strip() for r in t.rows)]
    t.header = [t.header[c] if c < len(t.header) else "" for c in keep] if t.header else []
    t.rows = [[r[c] if c < len(r) else "" for c in keep] for r in t.rows]


def _drop_people_columns(t: Table) -> None:
    """Sign-up / volunteer / parent / 'Name' columns hold people's names; they never leave this module."""
    if not t.header:
        return
    drop = [c for c, h in enumerate(t.header) if PEOPLE_COLUMN.search(h) and "date" not in h.lower() and not re.search(r"team|school|opponent|event", h, re.I)]
    if not drop:
        return
    t.dropped_columns = [t.header[c] for c in drop]
    keep = [c for c in range(len(t.header)) if c not in drop]
    t.header = [t.header[c] for c in keep]
    t.rows = [[r[c] if c < len(r) else "" for c in keep] for r in t.rows]


def sensitive_reason(tab: str, t: Table, ref: date) -> str | None:
    """Why a table must not be indexed (student-level data), else None."""
    label = f"{tab} {t.title}"
    if STRICT_TITLE.search(label):
        return f"tab or table title looks like people data ('{(t.title or tab)[:40]}')"
    hdr = [h.strip() for h in t.header if h.strip()]
    if any(STUDENT_HEADER.search(h) and not re.search(r"team|school|opponent|event", h, re.I) for h in hdr):
        return "header has student/guardian/birth/ID columns"
    cols = st.map_columns(t.header)
    sched = st.is_schedule_header(cols)
    if not sched and any(NAMEISH_HEADER.search(h) for h in hdr) and any(ROSTER_SIBLING.search(h) for h in hdr):
        return "looks like a roster (name column with grade/position/contact columns)"
    if not sched and len(t.rows) >= 6:
        for c in range(len(t.header) or 1):
            vals = [r[c].strip() for r in t.rows if c < len(r) and r[c].strip()]
            if len(vals) >= 6 and sum(1 for v in vals if FIRST_LAST.match(v)) / len(vals) >= 0.7:
                return "a column of personal names"
    text = table_csv(t)
    return guard.student_data_reason(text)


# ----------------------------------------------------------------------------------------------------------------- text
def _csv_line(cells: list[str]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(cells)
    return buf.getvalue()


def row_lines(t: Table) -> list[str]:
    out = []
    for r in t.rows:
        cells = [guard.redact_contacts(c)[0] for c in (r + [""] * (len(t.header) - len(r)))] if t.header else [guard.redact_contacts(c)[0] for c in r]
        out.append(_csv_line(cells))
    return out


def table_csv(t: Table) -> str:
    lines = ([_csv_line(t.header)] if t.header else []) + row_lines(t)
    return "\n".join(lines)


def weekly_summary(t: Table) -> list[str]:
    """A Day | Time | Location table (no dates) -> 'Practice: Tue/Thu 4:00-5:30 PM at Field 2' lines."""
    cols = st.map_columns(t.header)
    dcol = next((i for i, h in enumerate(t.header) if re.fullmatch(r"(?i)\s*(days?|weekday|day of (the )?week)\s*", h or "")), None)
    if dcol is None or "time" not in cols:
        return []
    kind_hint = f"{t.tab} {t.title}"
    out = []
    for r in t.rows:
        days = st.parse_days(r[dcol]) if dcol < len(r) else []
        _, _, label = st.parse_time(r[cols["time"]] if cols["time"] < len(r) else "")
        if not days or not label:
            continue
        loc = r[cols["loc"]].strip() if "loc" in cols and cols["loc"] < len(r) else ""
        kind = "Meeting" if re.search(r"meeting|club", kind_hint, re.I) and not re.search(r"practice", kind_hint, re.I) else "Practice"
        out.append(f"{kind}: {st.fmt_days(days)} {label}" + (f" at {loc}" if loc else ""))
    return out


# ----------------------------------------------------------------------------------------------------------------- row-aligned chunking
def chunk_rows(preamble_len: int, header_line: str, lines: list[str], target: int = CHUNK_TARGET) -> list[tuple[int, int]]:
    """Index ranges [a, b) over `lines`: greedy packing by WHOLE rows. A row is never cut, never repeated and never dropped; the ranges
    partition the rows in order. The header (and preamble) are repeated in every chunk, so they count toward the size. A single row bigger
    than the target gets a chunk of its own."""
    fixed = preamble_len + (len(header_line) + 1 if header_line else 0)
    out, a, size = [], 0, fixed
    for i, line in enumerate(lines):
        add = len(line) + 1
        if i > a and size + add > target:
            out.append((a, i))
            a, size = i, fixed
        size += add
    if a < len(lines):
        out.append((a, len(lines)))
    return out


def ordinal_day(d: str | date | None) -> str:
    return freshness.day_label(d if isinstance(d, str) else (d.isoformat() if d else ""))


class Analysis:
    def __init__(self):
        self.status = "indexed"           # indexed | needs_review | skipped
        self.reason = ""
        self.chunks: list[dict] = []
        self.events = 0
        self.tables = 0
        self.skipped_tabs: list[str] = []
        self.notes: list[str] = []
        self.team = ""
        self.truncated = False

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)


def base_meta(meta: dict, fh: str, now: datetime, ctx: dict, modified_day: str, source: str, stype: str) -> dict:
    return {"source": source, "source_type": stype, "source_url": f"share://{fh}", "share_file_hash": fh, "title": meta["name"][:200], "file_title": meta["name"][:200],
            "modified_time": meta.get("modified_time", ""), "source_date": modified_day, "indexed_at": now.replace(microsecond=0).isoformat(),
            "school_year": ctx["school_year"], "stale": False, "category": ctx["category"], "team": ctx["team"], "sport": ctx["sport"],
            "gender": ctx["gender"], "level": ctx["level"], "division": ctx["division"], "season": ctx["season"],
            "owner_domain": meta.get("owner_domain", ""), "drive_version": str(meta.get("version", ""))}


def file_hash(file_id: str) -> str:
    return hashlib.sha1(file_id.encode()).hexdigest()[:12]


def analyze_workbook(book: dict, meta: dict, now: datetime | None = None, event_window: tuple[date, date] | None = None) -> Analysis:
    """spreadsheets.get() response (+ file meta {file_id, name, modified_time, version, owner_domain}) -> Analysis with the chunks to index.
    Never raises on odd structure: a tab that cannot be parsed is noted and skipped."""
    now = now or datetime.now().astimezone()
    a = Analysis()
    fid, name = meta["file_id"], meta.get("name") or (book.get("properties") or {}).get("title") or "Untitled sheet"
    meta = {**meta, "name": name}
    fh = file_hash(fid)
    md = freshness.parse_day(meta.get("modified_time"))
    ref = date.fromisoformat(md) if md else now.date()
    modified_day = ref.isoformat()
    if STRICT_TITLE.search(name):
        a.status, a.reason = "needs_review", f"file name looks like people data ('{name[:40]}'): not indexed"
        return a
    win_lo, win_hi = event_window or (now.date() - timedelta(days=45), now.date() + timedelta(days=430))
    label = f'shared Google Sheet "{name[:80]}", updated {freshness.day_label(modified_day)}'
    used_ids: set[str] = set()
    seen_events: dict[str, set] = {}
    sheets = book.get("sheets") or []
    for sh in sheets[:MAX_TABS]:
        title = str((sh.get("properties") or {}).get("title") or "Sheet")
        try:
            grid = grid_from_sheet(sh, ref)
            if grid.hidden:
                a.notes.append(f"hidden tab '{title}' not read")
                continue
            tables = split_tables(grid, ref)
        except Exception as e:                                   # oddly structured tab: skip it, keep the rest
            a.notes.append(f"tab '{title}' could not be parsed ({type(e).__name__})")
            continue
        if len(sheets) > MAX_TABS:
            a.truncated = True
        tab_kept = 0
        for t in tables:
            _drop_people_columns(t)
            _drop_empty_columns(t)
            why = sensitive_reason(title, t, ref)
            if why:
                a.skipped_tabs.append(title)
                a.notes.append(f"table in tab '{title[:30]}' skipped as private ({why})")
                continue
            if not t.rows and not t.title:
                continue
            tab_kept += 1
            a.tables += 1
            ctx = st.derive_context(name, title, t.title, ref)
            a.team = a.team or ctx["team"]
            ctx["title"] = name
            events = st.schedule_events(t.header, t.rows, ref, f"{title} {t.title}", seen=seen_events.setdefault(title, set()))
            if ctx["category"] == "general" and events:
                ctx["category"] = "athletics" if any(e["kind"] in ("game", "practice") for e in events) else ctx["category"]
            if not ctx["season"] and events:
                m = events[0]["date"].month
                ctx["season"] = "Fall" if 8 <= m <= 10 else "Winter" if m in (11, 12, 1, 2) else "Spring"      # inferred from the first event
            bm = base_meta(meta, fh, now, ctx, modified_day, "share_sheet", "sheet")
            bm.update({"tab": title, "table": t.title, "kind": "table"})
            sect = " > ".join(x for x in (title, t.title) if x and x != name)
            ctx_line = " | ".join(f"{k}: {v}" for k, v in (("Team", ctx["team"] if ctx["sport"] else ""), ("Level", ctx["level"]), ("Gender", ctx["gender"]),
                                                          ("Sport", ctx["sport"]), ("Season", ctx["season"]), ("School year", ctx["school_year"])) if v)
            notes = []
            if grid.hidden_rows:
                notes.append(f"Note: {grid.hidden_rows} hidden or filtered-out row(s) on this tab are not shown.")
            if t.repeated_headers:
                notes.append("Note: repeated header rows were removed.")
            weekly = weekly_summary(t)
            lines = row_lines(t)
            header_line = _csv_line(t.header) if t.header else ""
            head = f"Title: {name}\n" + (f"Section: {sect}\n" if sect else "") + f"Source: {label}\n"
            pre = head + (ctx_line + "\n" if ctx_line else "") + "\n"
            first_extra = ("Schedule summary:\n" + "\n".join(f"- {w}" for w in weekly) + "\n") if weekly else ""
            ranges = chunk_rows(len(pre) + len(first_extra) + sum(len(n) + 1 for n in notes), header_line, lines) or [(0, 0)]
            for n, (lo, hi) in enumerate(ranges):
                body = ""
                if len(ranges) > 1:
                    body += f"(table rows {t.row_nums[lo]}-{t.row_nums[hi - 1]} of {t.row_nums[0]}-{t.row_nums[-1]}; the header row is repeated in each part)\n"
                if n == 0:
                    body += first_extra
                body += "".join(x + "\n" for x in notes) + (header_line + "\n" if header_line else "") + "\n".join(lines[lo:hi])
                content = pre + body.rstrip()
                cid = f"{freshness.SHARE_ID_PREFIX}{fh}-s{(grid.sheet_id if grid.sheet_id is not None else 0)}t{len(a.chunks)}-{n:03d}"
                a.chunks.append({"chunk_id": cid, "content": content, "source_file": f"share://{fh}",
                                 "metadata": {**bm, "row_first": t.row_nums[lo] if t.rows else None, "row_last": t.row_nums[hi - 1] if t.rows else None,
                                              "content_hash": hashlib.sha1(content.encode()).hexdigest()}})
            for e in events:
                if not (win_lo <= e["date"] <= win_hi):
                    continue
                ev_meta = {k: v for k, v in bm.items() if k not in ("source", "source_url", "title", "kind", "table")}
                ev_meta.update({"source_type": "sheet", "share_event": True})
                rec = st.event_chunk(e, ctx, id_prefix=f"{freshness.SHARE_ID_PREFIX}{fh}-e", source_url=f"share://{fh}",
                                     source_label=label, extra_meta=ev_meta, used_ids=used_ids)
                a.chunks.append(rec)
                a.events += 1
        if not tab_kept and not any(x.startswith(f"table in tab '{title[:30]}'") for x in a.notes):
            a.notes.append(f"tab '{title[:30]}' is empty")
    if len(a.chunks) > MAX_CHUNKS_PER_FILE:
        a.truncated = True
        keep = [c for c in a.chunks if c["metadata"].get("kind") != "table"][:MAX_CHUNKS_PER_FILE // 2]
        keep += [c for c in a.chunks if c["metadata"].get("kind") == "table"][:MAX_CHUNKS_PER_FILE - len(keep)]
        a.chunks = keep
        a.notes.append(f"truncated to {len(keep)} chunks (cap {MAX_CHUNKS_PER_FILE})")
    if not a.chunks:
        if a.skipped_tabs:
            a.status, a.reason = "needs_review", "every table looks like people data (roster, sign-ups, student columns): nothing indexed. " + "; ".join(a.notes[:3])
        else:
            a.status, a.reason = "skipped", "no readable table content" + (": " + "; ".join(a.notes[:3]) if a.notes else "")
        return a
    bits = [f"{a.tables} table(s)", f"{a.events} event record(s)"] + a.notes[:4]
    a.reason = "; ".join(bits)
    return a


def reproduce_csv(table: Table) -> str:
    """The table as CSV text exactly like the pipeline's anonymous CSV export path would give it (header row first)."""
    return table_csv(table)
