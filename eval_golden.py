"""Golden-set accuracy grader: runs eval_golden.json against a running backend and scores every answer.

    python eval_golden.py                         # all questions against http://localhost:8765
    python eval_golden.py https://nuevai-backend.onrender.com
    python eval_golden.py --category clubs,ab_days --limit 20
    python eval_golden.py --threshold 0.8         # exit 1 if the pass rate is lower

Each question has `expect` (every entry must match the answer; an entry that is a list means any one of
them) and `forbid` (none may match). Patterns are regexes, matched case-insensitively. Questions flagged
`dynamic` depend on today's date, so their expectations are deliberately weak.

Rate limits: serial by default with a sleep between calls (per-visitor limit is 20/min, Groq's free tier
is token-limited). A 429 is retried with backoff; if it still fails the question is reported as ERROR and
is left out of the score (listed separately) so a busy model never looks like a wrong answer.
Results are written to data/eval/golden_<timestamp>.json (data/ is gitignored).
"""
import argparse
import collections
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_BASE = os.getenv("GOLDEN_BASE", "http://localhost:8765")


def pattern(p: str) -> re.Pattern:
    # inline (?i) flags are accepted in the JSON for readability; matching is always case-insensitive
    return re.compile(p.replace("(?i)", ""), re.I | re.S)


def normalize(text: str) -> str:
    """Models emit no-break spaces, narrow no-break spaces, curly quotes and en/em dashes; match on plain text."""
    for bad, good in (("\u00a0", " "), ("\u202f", " "), ("\u2009", " "), ("\u2019", "'"), ("\u2018", "'"),
                      ("\u201c", '"'), ("\u201d", '"'), ("\u2013", "-"), ("\u2014", "-"), ("\u2011", "-")):
        text = text.replace(bad, good)
    return text


def check(answer: str, item: dict) -> tuple[bool, list[str]]:
    answer = normalize(answer)
    problems = []
    for e in item.get("expect", []):
        alts = e if isinstance(e, list) else [e]
        if not any(pattern(a).search(answer) for a in alts):
            problems.append("missing: " + (" | ".join(alts))[:90])
    for f in item.get("forbid", []):
        m = pattern(f).search(answer)
        if m:
            problems.append(f"forbidden: {m.group(0)[:60]!r}")
    return (not problems), problems


def ask(base: str, item: dict, retries: int = 3) -> dict:
    body = {"query": item["q"], "history": item.get("history", []), "anon_id": "golden-" + uuid.uuid4().hex[:10],
            "session_id": "golden"}
    if item.get("campus"):
        body["campus"] = item["campus"]               # the saved campus setting (lunch questions)
    t = time.time()
    last = ""
    for attempt in range(retries + 1):
        req = urllib.request.Request(base.rstrip("/") + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            d = json.loads(urllib.request.urlopen(req, timeout=120).read())
            d["ms"] = int((time.time() - t) * 1000)
            return d
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (429, 502, 503, 504) and attempt < retries:
                time.sleep(12 * (attempt + 1))
                continue
            break
        except Exception as e:  # timeouts, connection resets
            last = type(e).__name__
            if attempt < retries:
                time.sleep(5)
                continue
            break
    return {"error": last, "answer": "", "sources": [], "ms": int((time.time() - t) * 1000)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default=DEFAULT_BASE)
    ap.add_argument("--file", default=str(HERE / "eval_golden.json"))
    ap.add_argument("--category", default="", help="comma-separated categories to run")
    ap.add_argument("--limit", type=int, default=0, help="run only the first N selected questions")
    ap.add_argument("--sleep", type=float, default=4.0, help="seconds between questions")
    ap.add_argument("--threshold", type=float, default=0.70, help="minimum pass rate (0-1) for exit code 0")
    ap.add_argument("--only-failed", default="", help="path to an earlier golden_*.json; run only its failing questions")
    ap.add_argument("--verbose", action="store_true", help="print every answer, not only failures")
    a = ap.parse_args()

    items = json.load(open(a.file))
    if a.category:
        want = set(a.category.split(","))
        items = [i for i in items if i["category"] in want]
    if a.only_failed:
        bad = {r["q"] for r in json.load(open(a.only_failed))["results"] if not r["ok"]}
        items = [i for i in items if i["q"] in bad]
    if a.limit:
        items = items[: a.limit]
    print(f"golden set: {len(items)} questions against {a.base}\n")

    results = []
    for n, it in enumerate(items, 1):
        d = ask(a.base, it)
        err = d.get("error")
        ok, problems = (False, [f"request failed: {err}"]) if err else check(d.get("answer", ""), it)
        results.append({"category": it["category"], "q": it["q"], "ok": ok, "error": err, "problems": problems,
                        "answer": d.get("answer", ""), "sources": len(d.get("sources", [])), "ms": d["ms"],
                        "model": d.get("model_used", ""), "cached": bool(d.get("cached")), "src": it.get("src", ""),
                        "dynamic": bool(it.get("dynamic"))})
        tag = "ERR " if err else ("PASS" if ok else "FAIL")
        print(f"[{n:3}/{len(items)}] {tag} {d['ms']:5}ms  {it['category'][:16]:16} {it['q'][:60]}")
        if not ok:
            print("          " + "; ".join(problems))
            if a.verbose or not err:
                print("          answer: " + d.get("answer", "")[:240].replace("\n", " "))
        elif a.verbose:
            print("          answer: " + d.get("answer", "")[:240].replace("\n", " "))
        # smalltalk and cache hits cost no tokens, so they need no pause
        if n < len(items) and not (d.get("cached") or not d.get("sources")):
            time.sleep(a.sleep)
        elif n < len(items):
            time.sleep(min(a.sleep, 1.0))

    graded = [r for r in results if not r["error"]]
    errors = [r for r in results if r["error"]]
    by = collections.defaultdict(lambda: [0, 0])
    for r in graded:
        by[r["category"]][1] += 1
        by[r["category"]][0] += r["ok"]
    print("\n%-20s %6s %6s %7s" % ("category", "pass", "total", "rate"))
    for c in sorted(by, key=lambda c: by[c][0] / by[c][1]):
        p, t = by[c]
        print("%-20s %6d %6d %6.0f%%" % (c, p, t, 100 * p / t))
    passed = sum(r["ok"] for r in graded)
    score = passed / len(graded) if graded else 0.0
    print("-" * 42)
    print("%-20s %6d %6d %6.0f%%" % ("OVERALL", passed, len(graded), 100 * score))
    if errors:
        print(f"\n{len(errors)} question(s) errored (rate limit/timeouts) and are NOT scored:")
        for r in errors:
            print(f"  - {r['q'][:70]}  [{r['error']}]")
    fails = [r for r in graded if not r["ok"]]
    if fails:
        print(f"\n{len(fails)} failing question(s):")
        for r in fails:
            print(f"  - [{r['category']}] {r['q'][:70]}  -> {'; '.join(r['problems'])[:120]}")

    out = HERE / "data" / "eval"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"golden_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    json.dump({"base": a.base, "when": datetime.now().isoformat(timespec="seconds"), "score": round(score, 4),
               "passed": passed, "graded": len(graded), "errors": len(errors),
               "by_category": {c: {"pass": p, "total": t} for c, (p, t) in by.items()},
               "results": results}, open(path, "w"), indent=1, ensure_ascii=False)
    print(f"\nwrote {path}")
    return 0 if score >= a.threshold else 1


if __name__ == "__main__":
    sys.exit(main())
