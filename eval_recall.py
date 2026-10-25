"""Retrieval-only metrics over the golden set (no LLM, no Groq quota).

    python eval_recall.py [--json out.json] [--misses]

For every golden question whose `expect` entries are plain facts (not "I don't have that" phrasings), retrieve
and check where the fact appears. Retrieved items are ordered the way the context is built: structured
items first (pinned facts, teaching assignments, calendar block, link cards), then ranked documents.
  recall@5 : every expected fact is inside the first 5 items      MRR : 1 / rank of the last fact's first hit
  ctx      : every expected fact is anywhere in the final context
Questions flagged dynamic, or whose expectations describe a refusal, are skipped.
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
from eval_golden import normalize, pattern      # noqa: E402
from query_server import get_supabase           # noqa: E402
from rag import Retriever, build_context, fmt_event  # noqa: E402

REFUSAL = re.compile(r"can\.t|cannot|don\.t|do not|isn\.t|unable|privacy|not \(|couldn|won\.t|doesn", re.I)


def usable(item) -> bool:
    if item.get("dynamic") or not item.get("expect"):
        return False
    if item["category"] in ("greetings", "off_topic_safety", "privacy"):
        return False
    for e in item["expect"]:
        for a in (e if isinstance(e, list) else [e]):
            if REFUSAL.search(a):
                return False
    return True


def items_of(res) -> list[str]:
    out = [p["content"] for p in res.get("pinned") or []] + [e["content"] for e in res.get("entities") or []]
    ev = res.get("events") or []
    if ev:
        out.append("\n".join(fmt_event(e) for e in ev[:14]))
    out += [d["content"][:1100] for d in res["docs"]]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--misses", action="store_true")
    ap.add_argument("--today", default=os.getenv("EVAL_TODAY", "2026-10-22"))
    a = ap.parse_args()
    cases = [c for c in json.load(open(Path(__file__).parent / "eval_golden.json")) if usable(c)]
    r = Retriever(get_supabase)
    r.embed("warm up")
    today = date.fromisoformat(a.today)
    n = hit5 = ctxhit = 0
    rr, lat, rows = [], [], []
    drr, dhit5 = [], 0
    for c in cases:
        hist = c.get("history")
        t = time.time()
        res = r.retrieve(c["q"], hist, today=today)
        lat.append((time.time() - t) * 1000)
        its = [normalize(x) for x in items_of(res)]
        ctx = normalize(build_context(res)[0])
        ranks = []
        for e in c["expect"]:
            alts = [pattern(x) for x in (e if isinstance(e, list) else [e])]
            ranks.append(next((i + 1 for i, s in enumerate(its) if any(p.search(s) for p in alts)), None))
        dts = [normalize(d["content"][:1100]) for d in res["docs"]]
        dr = []
        for e in c["expect"]:
            alts = [pattern(x) for x in (e if isinstance(e, list) else [e])]
            dr.append(next((i + 1 for i, s_ in enumerate(dts) if any(p_.search(s_) for p_ in alts)), None))
        dworst = None if None in dr else max(dr)
        drr.append(1 / dworst if dworst else 0.0)
        dhit5 += dworst is not None and dworst <= 5
        full = all(any(pattern(x).search(ctx) for x in (e if isinstance(e, list) else [e])) for e in c["expect"])
        n += 1
        ctxhit += full
        worst = None if None in ranks else max(ranks)
        hit5 += worst is not None and worst <= 5
        rr.append(1 / worst if worst else 0.0)
        rows.append({"q": c["q"], "cat": c["category"], "ranks": ranks, "ctx": full})
        if a.misses and not (worst and worst <= 5):
            print(f"MISS ranks={ranks} ctx={full} [{c['category']}] {c['q']}")
    lat.sort()
    print(f"cases={n} recall@5={hit5 / n:.3f} MRR={statistics.mean(rr):.3f} ctx_contains={ctxhit / n:.3f} docs_recall@5={dhit5 / n:.3f} docs_MRR={statistics.mean(drr):.3f} "
          f"p50={lat[n // 2]:.0f}ms p95={lat[int(n * .95)]:.0f}ms")
    if a.json:
        json.dump(rows, open(a.json, "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
