"""Lunch stage: the school cafe's monthly menu (one PDF per campus) -> one structured record per campus per day.

The cafe (Epicurean Group, https://nuevaschoolcafe.com) publishes a PDF calendar grid per campus each month:
  san_mateo     "Nueva School - San Mateo US and Faculty Menu"   (Upper School campus)
  hillsborough  "Hillsborough Nueva School-LS, MS"               (Lower and Middle School campus)
The PDFs are Google Sheets exports: every wrapped line of text is its own text run, so the grid is rebuilt from the run order and
positions of the characters (pdfplumber), never from extract_text(), which interleaves the columns.

Where the PDFs come from, in order of precedence (per campus and month):
  1. manual drop   data/pipeline/manual_docs/lunch/<campus>_<YYYY>-<MM>.pdf   (campus = san_mateo | hillsborough), e.g. san_mateo_2026-11.pdf
  2. discovery     the cafe site embeds a public Google Calendar whose month events carry the PDFs as attachments; its public iCal feed is
                   read (one GET) and each attachment is downloaded from Google Drive (one GET per file). Fails soft: nothing found -> the
                   rows already indexed stay in place and the app says the month "isn't posted yet".

Each day becomes one chunk `lunch-<campus>-<YYYYMMDD>` (source "lunch"): the dishes exactly as listed, grouped under the cafe's own
section names ("Earthly Selections", "Plant Inspired", ...). Days whose cell holds a picture instead of a menu (no school, professional
day) become records with closed=true. NO dietary tag is ever inferred: the PDF's key (vegetarian, vegan, gluten free...) is a row of icons
that is not attached to individual dishes, so only wording that is in a dish's own name is kept.

PDF contents are untrusted data: they are only read as text, never executed, and every line passes guard.scrub_context at query time.
"""
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import freshness      # noqa: E402

CAFE_URL = "https://nuevaschoolcafe.com/#our-menu"
CAFE_ICAL = "https://calendar.google.com/calendar/ical/egnhnsunit%40gmail.com/public/basic.ics"   # the Google Calendar embedded on CAFE_URL
DRIVE_DOWNLOAD = "https://drive.google.com/uc?export=download&id={id}"
DRIVE_VIEW = "https://drive.google.com/file/d/{id}/view"
CAMPUSES = ("san_mateo", "hillsborough")
CAMPUS_NAME = {"san_mateo": "San Mateo (Upper School)", "hillsborough": "Hillsborough (Lower and Middle School)"}
NOTE = "The menu can change; check with the cafe for ingredients or allergens."
MANUAL_DIR = config.DATA_DIR / "manual_docs" / "lunch"
AUTO_DIR = config.DATA_DIR / "lunch" / "downloads"
PARSED_DIR = config.DATA_DIR / "lunch" / "parsed"
LUNCH_FILE = config.LUNCH_FILE
MAX_PDF_BYTES = 6_000_000
MIN_DAYS = 8                 # a month with fewer day cells than this is a broken parse, not a menu
MONTHS = {m: i + 1 for i, m in enumerate("january february march april may june july august september october november december".split())}

# Section names the cafe prints in bold inside a day's cell. Anything else in bold is the day's theme ("Street Food", "Global Adventure").
SECTIONS = re.compile(r"^(earthly selections?|plant inspired|sandwich favou?rites?|superfood tossed|something different|it'?s a wrap|"
                      r"soup|salad bar|dessert|sides?)$", re.I)
FOOTER = re.compile(r"epicurean group|allergen containing|menu key|general manager|executive chef|@epicurean|menu here", re.I)
PAGE_H = 1000.0          # vertical offset per page, so a week that continues on the next page keeps its order


# --------------------------------------------------------------------------- PDF -> grid
def _segments(page, page_idx: int, edges: list[float]) -> list[dict]:
    """Text runs of one page in content-stream order: [{text, x, y, bold}]. A run ends at a line change, a jump to another column
    or a gap in x (the stream puts the same line of neighbouring cells one after another)."""
    def col(x):
        for i in range(5):
            if edges[i] - 1 <= x < edges[i + 1] + 1:
                return i
        return -1

    segs, cur = [], []

    def flush():
        nonlocal cur
        if cur:
            text = re.sub(r"\s+", " ", "".join(c["text"] for c in cur)).strip()
            if text:
                body = [c for c in cur if c["text"].strip()]
                segs.append({"text": text, "x": cur[0]["x0"], "y": cur[0]["top"] + page_idx * PAGE_H, "page": page_idx,
                             "bold": all("bold" in c["fontname"].lower() for c in body) if body else False,
                             "size": cur[0]["size"]})
            cur = []

    prev = None
    for c in page.chars:
        if prev is not None:
            gap = c["x0"] - prev["x1"]
            if abs(c["top"] - prev["top"]) > 1.5 or gap > 9 or c["x0"] < prev["x0"] - 3 or (col(c["x0"]) != col(prev["x0"]) and gap > 1):
                flush()
        cur.append(c)
        prev = c
    flush()
    return segs


def _join_lines(lines: list[str]) -> str:
    out = ""
    for ln in lines:
        out = out + ln if out.endswith("-") else (out + " " + ln if out else ln)
    return re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s+", " ", out)).strip()


def clean_label(label: str) -> str:
    label = re.sub(r"[\s.…]+$", "", label.strip())
    if re.fullmatch(r"earthly selections?", label, re.I):
        return "Earthly Selections"
    return label


def clean_text(text: str) -> str:
    return re.sub(r"[\s,]+$", "", text).strip()          # a few cells end in a dangling comma in the PDF itself


def _structure(cell: list[dict]) -> list[dict]:
    """The runs of one day's cell -> [{label, text}]. A cell reads: [soup] / bold theme / main dish / bold 'Earthly Selections' / dish /
    bold 'Plant Inspired' / dish / bold 'Sandwich Favorite' / dish / bold 'Superfood Tossed' / dish (San Mateo has the soup line, Hillsborough
    starts at the theme). Bold lines are labels, everything else is the text of the label above it."""
    lead, items, label, buf = [], [], None, []
    for s in cell:
        if s["bold"]:
            if label is None and buf:
                lead.append(_join_lines(buf))
                buf = []
            elif label is not None and buf:
                items.append({"label": clean_label(label), "text": clean_text(_join_lines(buf))})
                buf = []
            elif label is not None:
                label = f"{label} {s['text']}"          # a label wrapped over two lines
                continue
            label = s["text"]
        else:
            buf.append(s["text"])
    if label is not None and buf:
        items.append({"label": clean_label(label), "text": clean_text(_join_lines(buf))})
    elif label is None and buf:
        lead.append(_join_lines(buf))
    out = [{"label": "Soup", "text": clean_text(t)} for t in lead if t]
    return out + [i for i in items if i["text"]]


def parse_pdf(path) -> dict:
    """-> {campus, month 'YYYY-MM', title, hours, days: [{date, weekday, closed, items:[{label, text}]}], warnings:[...]}.
    Raises ValueError when the file is not a recognisable cafe menu. `warnings` is empty for a clean parse."""
    import pdfplumber
    warnings = []
    with pdfplumber.open(str(path)) as pdf:
        pages = list(pdf.pages)
        if not pages:
            raise ValueError("empty PDF")
        p0 = pages[0]
        words0 = p0.extract_words()
        mon_w = [w for w in words0 if w["text"] == "Monday"]
        if not mon_w:
            raise ValueError("no weekday header row")
        top = mon_w[0]["top"]
        head_text = " ".join(w["text"] for w in sorted(words0, key=lambda w: (round(w["top"]), w["x0"])) if w["top"] < top - 2)
        low = head_text.lower()
        campus = "san_mateo" if "san mateo" in low else "hillsborough" if "hillsborough" in low else None
        m = re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d{2})\b", low)
        if not campus or not m:
            raise ValueError(f"header not recognised: {head_text[:80]!r}")
        year, mon = int(m.group(2)), MONTHS[m.group(1)]
        month = f"{year}-{mon:02d}"
        hm = re.search(r"(\d{1,2}:\d{2}\s*[ap]m)\s*-\s*(\d{1,2}:\d{2}\s*[ap]m)", low)
        hours = f"{hm.group(1).replace(' ', '')}-{hm.group(2).replace(' ', '')}" if hm else None
        # column edges: the thin vertical rules of the header row (Monday ... Friday)
        xs = sorted({round(r["x0"], 0) for r in p0.rects if r["x1"] - r["x0"] <= 2 and r["top"] <= top + 1 and r["bottom"] >= top + 3})
        edges: list[float] = []
        for x in xs:
            if not edges or x - edges[-1] > 20:
                edges.append(float(x))
        if len(edges) != 6:
            raise ValueError(f"expected 6 column edges, found {len(edges)}")
        segs, images = [], []
        for i, pg in enumerate(pages):
            segs += _segments(pg, i, edges)
            images += [{"x0": im["x0"], "x1": im["x1"], "y0": im["top"] + i * PAGE_H, "y1": im["bottom"] + i * PAGE_H} for im in pg.images
                       if im["x1"] - im["x0"] > 40 and im["bottom"] - im["top"] > 40]

    def col_of(x):
        for i in range(5):
            if edges[i] - 2 <= x < edges[i + 1]:
                return i
        return None

    foot = [s["y"] for s in segs if FOOTER.search(s["text"])]       # logo, manager, allergen and menu key: below the last week
    foot_y = min(foot) - 2 if foot else float("inf")
    nums = [s for s in segs if re.fullmatch(r"\d{1,2}", s["text"]) and top + 3 < s["y"] < foot_y and 1 <= int(s["text"]) <= 31 and col_of(s["x"]) is not None]
    nums.sort(key=lambda s: (s["y"], s["x"]))
    weeks: list[list[dict]] = []
    for n in nums:
        if weeks and abs(weeks[-1][0]["y"] - n["y"]) < 3:
            weeks[-1].append(n)
        else:
            weeks.append([n])
    days = []
    for wi, wk in enumerate(weeks):
        y0 = wk[0]["y"] - 1
        y1 = weeks[wi + 1][0]["y"] - 1 if wi + 1 < len(weeks) else foot_y
        for n in wk:
            c = next((i for i in range(5) if edges[i] < n["x"] + 6 <= edges[i + 1] + 3), None)        # the number is right-aligned in its cell
            if c is None:
                warnings.append(f"date number {n['text']} is not inside a column")
                continue
            try:
                d = date(year, mon, int(n["text"]))
            except ValueError:
                warnings.append(f"impossible date {year}-{mon:02d}-{n['text']}")
                continue
            if d.weekday() != c:
                warnings.append(f"{d} is a {d.strftime('%A')} but sits in column {c + 1}")
                continue
            cell = [s for s in segs if y0 + 4 < s["y"] < y1 and col_of(s["x"]) == c and s is not n]
            cell.sort(key=lambda s: (s["page"], s["y"], s["x"]))
            # a picture over the cell (no school, professional day, holiday): the cell shows no menu, whatever text lies hidden under it
            probe_y, probe_x = n["y"] + 18, (edges[c] + edges[c + 1]) / 2
            covered = any(im["x0"] <= probe_x <= im["x1"] and im["y0"] <= probe_y <= im["y1"] and im["y1"] - im["y0"] > 60 for im in images)
            items = [] if covered else _structure(cell)
            if not covered and not items:
                warnings.append(f"{d}: no readable menu text and no picture")
            days.append({"date": d.isoformat(), "weekday": d.strftime("%A"), "closed": covered or not items, "items": items})
    seen = set()
    for dd in days:
        if dd["date"] in seen:
            warnings.append(f"{dd['date']} appears twice")
        seen.add(dd["date"])
    days.sort(key=lambda x: x["date"])
    return {"campus": campus, "month": month, "title": head_text.strip(), "hours": hours, "days": days, "warnings": warnings}


# --------------------------------------------------------------------------- parsed menu -> records
def month_label(month: str) -> str:
    y, m = month.split("-")
    return f"{[k for k, v in MONTHS.items() if v == int(m)][0].capitalize()} {y}"


def long_date(d: date) -> str:
    return d.strftime("%A, %B ") + str(d.day) + d.strftime(", %Y")


def item_strings(items: list[dict]) -> list[str]:
    return [f"{i['label']}: {i['text']}" if i.get("label") else i["text"] for i in items]


def build_chunks(doc: dict, source_url: str, pdf_name: str, fetched_on: str) -> list[dict]:
    campus, month = doc["campus"], doc["month"]
    name, label = CAMPUS_NAME[campus], month_label(month)
    hours = f" (cafe hours {doc['hours']})" if doc.get("hours") else ""
    chunks = []
    for dd in doc["days"]:
        d = date.fromisoformat(dd["date"])
        title = f"Lunch menu - {name} - {long_date(d)}"
        if dd["closed"]:
            body = (f"No lunch menu is listed for {name} on {long_date(d)}: the cafe's {label} menu shows a notice (such as no school or a "
                    f"professional day) instead of a menu.")
            items, kind = [], "no_menu_day"
        else:
            items = item_strings(dd["items"])
            body = f"Lunch menu for {name}, {long_date(d)}{hours}:\n" + "\n".join(f"- {x}" for x in items) + f"\nNote: {NOTE}"
            kind = "menu_day"
        content = f"Title: {title}\nSource: Nueva School cafe menu, {label}\n\n{body}"
        extra = {"source": "lunch", "source_type": "lunch", "kind": kind, "campus": campus, "event_date": d.isoformat(), "end_date": d.isoformat(),
                 "weekday": dd["weekday"], "month": month, "month_label": label, "closed": bool(dd["closed"]), "items": items,
                 "pdf_name": pdf_name, "source_url": source_url, "title": title, "fetched_on": fetched_on, "hours": doc.get("hours")}
        h = hashlib.sha1((content + json.dumps({k: extra[k] for k in ("campus", "event_date", "closed", "items", "source_url")}, sort_keys=True)).encode()).hexdigest()
        chunks.append({"chunk_id": f"lunch-{campus}-{d:%Y%m%d}", "content": content, "source_file": source_url,
                       "metadata": {**extra, "content_hash": h}})
    return chunks


# --------------------------------------------------------------------------- finding and downloading the PDFs
def _get(url: str, **kw):
    import requests
    return requests.get(url, timeout=30, headers={"User-Agent": config.USER_AGENT}, **kw)


def classify_event(summary: str) -> str | None:
    s = summary.lower()
    if re.search(r"pre[\s-]*k|kindergarten", s):
        return None                          # the PreK-K menu is a separate PDF that this stage does not read
    if "san mateo" in s:
        return "san_mateo"
    if "hillsborough" in s:
        return "hillsborough"
    return None


def drive_id(url: str) -> str | None:
    m = re.search(r"[?&]id=([\w-]{20,})", url) or re.search(r"/d/([\w-]{20,})", url)
    return m.group(1) if m else None


def parse_ical_menus(text: str) -> list[dict]:
    """The cafe's public calendar: one all-day event per campus per month, the menu PDF attached.
    -> [{campus, month, summary, filename, file_id}]"""
    from .calendar_feed import _unfold
    out, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur and cur.get("start") and cur.get("summary") and cur.get("attach"):
                campus = classify_event(cur["summary"])
                fid = drive_id(cur["attach"])
                if campus and fid:
                    out.append({"campus": campus, "month": cur["start"][:7], "summary": cur["summary"], "filename": cur.get("filename", ""), "file_id": fid})
            cur = None
        elif cur is not None and ":" in line:
            head, _, value = line.partition(":")
            name, _, params = head.partition(";")
            name = name.upper()
            if name == "DTSTART" and re.match(r"\d{8}", value):
                cur["start"] = f"{value[:4]}-{value[4:6]}-{value[6:8]}"
            elif name == "SUMMARY":
                cur["summary"] = value.strip()
            elif name == "ATTACH":
                cur["attach"] = value.strip()
                fm = re.search(r"FILENAME=([^;]+)", params)
                cur["filename"] = fm.group(1).strip() if fm else ""
    return out


def target_months(today: date) -> list[str]:
    nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    return [today.strftime("%Y-%m"), nxt.strftime("%Y-%m")]


def discover(today: date, log=print) -> tuple[list[dict], list[str]]:
    """-> (found menus for this and next month, problems). One GET for the feed. Never raises."""
    problems = []
    try:
        r = _get(CAFE_ICAL)
        r.raise_for_status()
        if "BEGIN:VCALENDAR" not in r.text[:300]:
            raise RuntimeError("not an iCal feed")
        menus = parse_ical_menus(r.text)
    except Exception as e:
        return [], [f"cafe calendar unreachable ({type(e).__name__}: {str(e)[:80]})"]
    want = set(target_months(today))
    best: dict[tuple, dict] = {}
    for m in menus:
        if m["month"] in want:
            best.setdefault((m["campus"], m["month"]), m)        # the first attachment of the month wins (events are not edited in place)
    found = list(best.values())
    for cm in [(c, mo) for c in CAMPUSES for mo in sorted(want)]:
        if cm not in best:
            problems.append(f"no {cm[0]} menu for {cm[1]} on the cafe calendar")
    log(f"  lunch discovery: {len(found)} menu link(s) for {sorted(want)}")
    return found, problems


def download(menu: dict, log=print) -> Path | None:
    """One GET of a Drive file into AUTO_DIR. Skips the GET when the same Drive file was already fetched. Returns the path or None."""
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    path = AUTO_DIR / f"{menu['campus']}_{menu['month']}.pdf"
    side = path.with_suffix(".id")
    if path.exists() and side.exists() and side.read_text().strip() == menu["file_id"]:
        return path
    try:
        r = _get(DRIVE_DOWNLOAD.format(id=menu["file_id"]))
        data = r.content
        if r.status_code != 200 or not data.startswith(b"%PDF") or len(data) > MAX_PDF_BYTES:
            raise RuntimeError(f"HTTP {r.status_code}, {len(data)} bytes, not a PDF")
    except Exception as e:
        log(f"  could not download {menu['campus']} {menu['month']}: {type(e).__name__}: {str(e)[:80]}")
        return None
    path.write_bytes(data)
    side.write_text(menu["file_id"])
    return path


MANUAL_NAME = re.compile(r"^(san_mateo|hillsborough)_(\d{4})-(\d{2})\.pdf$", re.I)


def manual_files() -> tuple[list[dict], list[str]]:
    found, problems = [], []
    if MANUAL_DIR.exists():
        for p in sorted(MANUAL_DIR.iterdir()):
            if p.is_dir() or p.name.startswith("."):
                continue
            m = MANUAL_NAME.match(p.name)
            if not m:
                problems.append(f"manual lunch file {p.name!r} ignored: name it <san_mateo|hillsborough>_<YYYY>-<MM>.pdf")
                continue
            found.append({"campus": m.group(1).lower(), "month": f"{m.group(2)}-{m.group(3)}", "path": p, "pdf_name": p.name, "source_url": CAFE_URL})
    return found, problems


def load_previous() -> list[dict]:
    if LUNCH_FILE.exists():
        try:
            return json.loads(LUNCH_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def run_soft(log=print, **kw) -> dict:
    """run() that can never stop the rest of the pipeline: an unexpected error is reported and the earlier menus stay."""
    try:
        return run(log=log, **kw)
    except Exception as e:
        msg = f"failed softly: {type(e).__name__}: {str(e)[:120]}"
        log(f"lunch: {msg}")
        return {"status": msg, "problems": [msg]}


def run(log=print, today: date | None = None, discover_online: bool = True) -> dict:
    """Stage: find the current (and next) month's menus, parse them and write data/pipeline/lunch_chunks.json for the chunk stage.
    Fails soft: anything that cannot be fetched or parsed is reported and the previous lunch_chunks.json (and the indexed rows) stay."""
    from zoneinfo import ZoneInfo
    today = today or datetime.now(ZoneInfo("America/Los_Angeles")).date()
    today_s = today.isoformat()
    problems: list[str] = []
    jobs: dict[tuple, dict] = {}
    if discover_online:
        found, probs = discover(today, log)
        problems += probs
        for m in found:
            p = download(m, log)
            if p:
                jobs[(m["campus"], m["month"])] = {"path": p, "pdf_name": m["filename"] or p.name, "source_url": DRIVE_VIEW.format(id=m["file_id"]), "via": "discovery"}
            else:
                problems.append(f"download failed for {m['campus']} {m['month']}")
    manual, probs = manual_files()
    problems += probs
    for m in manual:                                           # a file the owner dropped in wins over the downloaded one
        jobs[(m["campus"], m["month"])] = {"path": m["path"], "pdf_name": m["pdf_name"], "source_url": m["source_url"], "via": "manual"}

    chunks, parsed, skipped = [], [], []
    for (campus, month), j in sorted(jobs.items()):
        try:
            doc = parse_pdf(j["path"])
        except Exception as e:
            skipped.append(f"{campus} {month}: unreadable ({type(e).__name__}: {str(e)[:100]})")
            continue
        if doc["campus"] != campus or doc["month"] != month:
            skipped.append(f"{campus} {month}: the file's own header says {doc['campus']} {doc['month']}; refused (a wrong campus or month would give wrong answers)")
            continue
        if doc["warnings"] or len(doc["days"]) < MIN_DAYS:
            skipped.append(f"{campus} {month}: parse warnings {doc['warnings'][:3]} ({len(doc['days'])} days); refused")
            continue
        chunks += build_chunks(doc, j["source_url"], j["pdf_name"], today_s)
        parsed.append({"campus": campus, "month": month, "days": len(doc["days"]), "open_days": sum(1 for d in doc["days"] if not d["closed"]), "via": j["via"]})
        PARSED_DIR.mkdir(parents=True, exist_ok=True)
        (PARSED_DIR / f"{campus}_{month}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    problems += skipped
    # keep what an earlier run produced for any campus/month that this run could not refresh (the owner can drop a file to replace it)
    fresh_keys = {(c["metadata"]["campus"], c["metadata"]["month"]) for c in chunks}
    kept = [c for c in load_previous() if (c["metadata"].get("campus"), c["metadata"].get("month")) not in fresh_keys]
    out = chunks + kept
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if out:
        LUNCH_FILE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    stats = {"menus_parsed": parsed, "chunks": len(chunks), "chunks_kept_from_earlier_run": len(kept), "problems": problems,
             "months_available": [f"{c} {m}" for c, m in sorted({(c["metadata"]["campus"], c["metadata"]["month"]) for c in out})]}
    log(f"lunch done: {stats}")
    return stats


