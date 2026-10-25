#!/usr/bin/env python
"""Print a markdown usage digest from the analytics table, to paste into a chat. Read-only; sends nothing.

    python scripts/weekly_digest.py            # last 7 days
    python scripts/weekly_digest.py --days 14

Reads analytics_events with SELECTs through the service-role key from .env / the environment (never printed).
Events from the golden-set grader (anon_id starting "golden-") are excluded. Anonymous ids are only counted, never shown;
question and feedback text is cut short and emails / phone numbers are masked before printing.
"""
import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


def scrub(text: str, n: int = 110) -> str:
    t = PHONE.sub("[number]", EMAIL.sub("[email]", str(text))).replace("\n", " ").replace("|", "/").strip()
    return t[:n] + ("..." if len(t) > n else "")


def norm(q: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", q.lower()).strip()[:160]


def load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def fetch(sb, since: str, cap: int = 50000) -> list[dict]:
    rows, start = [], 0
    while len(rows) < cap:
        page = sb.table("analytics_events").select("ts,anon_id,session_id,event,props") \
            .gte("ts", since).order("ts").range(start, start + 999).execute().data
        rows += page
        if len(page) < 1000:
            break
        start += 1000
    return rows


def pct(vals, p):
    if not vals:
        return "n/a"
    v = sorted(vals)
    return f"{v[min(len(v) - 1, int(len(v) * p))] / 1000:.1f}s"


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def digest(rows: list[dict], days: int) -> str:
    rows = [r for r in rows if not str(r.get("anon_id", "")).startswith("golden-")]
    users, sessions = set(), set()
    per_day = defaultdict(lambda: [set(), 0])
    first_day: dict[str, str] = {}
    prompts, unanswered, down = Counter(), Counter(), Counter()
    chats = cached = degraded = errors = 0
    lat = []
    thumbs, tabs, fb_text, err_msgs = Counter(), Counter(), [], Counter()
    for r in rows:
        e, p, day = r["event"], r.get("props") or {}, r["ts"][:10]
        users.add(r["anon_id"])
        if r.get("session_id"):
            sessions.add(r["session_id"])
        per_day[day][0].add(r["anon_id"])
        first_day.setdefault(r["anon_id"], day)
        if e == "chat_answered":
            chats += 1
            per_day[day][1] += 1
            cached += bool(p.get("cached"))
            degraded += bool(p.get("degraded"))
            if p.get("total_ms") and not p.get("cached"):
                lat.append(p["total_ms"])
            q = norm(p.get("query", ""))
            if q:
                prompts[q] += 1
                if p.get("no_context") or p.get("fallback") or p.get("degraded"):
                    unanswered[q] += 1
        elif e == "chat_error":
            errors += 1
            err_msgs[re.sub(r"\d+", "N", str(p.get("error", "?")))[:80]] += 1
        elif e == "feedback":
            thumbs[p.get("value", "?")] += 1
            if p.get("value") == "down" and p.get("query"):
                down[norm(p["query"])] += 1
        elif e == "feedback_text":
            fb_text.append((r["ts"][:10], p.get("kind", ""), p.get("text", "")))
        elif e == "tab_view":
            tabs[p.get("tab", "?")] += 1
    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=days)).isoformat()
    new = sum(1 for d in first_day.values() if d >= start)
    L = [f"# NuevAI digest: last {days} days (to {today})", ""]
    L += ["## Usage", f"- Visitors: {len(users)} ({new} first seen in the period), sessions: {len(sessions)}",
          f"- Questions answered: {chats} ({cached} from cache, {degraded} degraded fallbacks)",
          f"- Server-side chat errors: {errors}",
          f"- Latency of uncached answers: p50 {pct(lat, .5)}, p95 {pct(lat, .95)}", ""]
    if per_day:
        L += [table(["day", "visitors", "questions"], [(d, len(v[0]), v[1]) for d, v in sorted(per_day.items())]), ""]
    if tabs:
        L += ["Tab views: " + ", ".join(f"{k} {v}" for k, v in tabs.most_common()), ""]
    L += ["## Top questions", table(["#", "count", "question"], [(i, n, scrub(q)) for i, (q, n) in enumerate(prompts.most_common(15), 1)]) if prompts else "none", ""]
    L += ["## Failures", f"- Questions the bot could not answer or answered with a fallback: {sum(unanswered.values())}"]
    L += [f"  - {n}x {scrub(q)}" for q, n in unanswered.most_common(10)]
    if err_msgs:
        L += ["- Error kinds: " + "; ".join(f"{n}x {m}" for m, n in err_msgs.most_common(5))]
    L += ["", "## Feedback", f"- Thumbs up {thumbs.get('up', 0)}, down {thumbs.get('down', 0)}"]
    L += [f"  - down: {n}x {scrub(q)}" for q, n in down.most_common(10)]
    if fb_text:
        L += ["- Written feedback (masked, newest last):"] + [f"  - {d} [{k}] {scrub(t, 300)}" for d, k, t in fb_text[-15:]]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    load_env()
    if not (os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY")):
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set", file=sys.stderr)
        return 2
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"].strip().strip("\"'"), os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip().strip("\"'"))
    since = (datetime.now(timezone.utc) - timedelta(days=a.days)).isoformat()
    print(digest(fetch(sb, since), a.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
