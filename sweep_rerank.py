"""Offline grid search of rerank knobs against the golden set (docs path only, no LLM).

    python sweep_rerank.py [cache.json]     # first run fetches 40 vector candidates per question and caches them
"""
import copy
import itertools
import json
import os
import statistics
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv()
import rag                                    # noqa: E402
from eval_golden import normalize, pattern    # noqa: E402
from eval_recall import usable                # noqa: E402
from query_server import get_supabase         # noqa: E402

DEDUPE = os.getenv('DEDUPE') == '1'
CACHE = sys.argv[1] if len(sys.argv) > 1 else "sweep_cache.json"
cases = [c for c in json.load(open("eval_golden.json")) if usable(c)]


def build_cache():
    r = rag.Retriever(get_supabase)
    rag.CANDIDATES = 40
    out = []
    for c in cases:
        hist = c.get("history") or []
        prev = next((m["content"] for m in reversed(hist) if m.get("role") == "user"), "")
        q = c["q"]
        sq = rag.expand_query(f"{prev} {q}" if prev and len(q.split()) <= 6 else q)
        res = r.retrieve(q, hist, today=date(2026, 10, 22))
        rows = r.vector_search(r.embed(sq))
        out.append({"q": q, "sq": sq, "rows": rows, "events": bool(res["events"]),
                    "skip": [d["chunk_id"] for d in res["pinned"] + res["entities"]],
                    "expect": c["expect"], "cat": c["category"]})
    json.dump(out, open(CACHE, "w"))
    return out


def evaluate(data, n_cand=24, top=5, filt=True):
    rr, hit = [], 0
    for d in data:
        rows = copy.deepcopy(d["rows"][:n_cand])
        rows = [r for r in rows if not rag.BLOCKED_TITLE.search((r.get("metadata") or {}).get("title", "") or "")]
        fresh = [r for r in rows if (r.get("metadata") or {}).get("source_url")]
        if len(fresh) >= 3:
            rows = fresh
        if d["events"]:
            rows = [r for r in rows if (r.get("metadata") or {}).get("source") not in ("calendar", "calendar_day", "school_day")]
        ranked = rag.rerank_fn(d["sq"], rows) if hasattr(rag, "rerank_fn") else rag.Retriever(None).rerank(d["sq"], rows)
        if DEDUPE:
            ranked = rag.dedupe_rows(ranked)
        ranked = [x for x in ranked if x["chunk_id"] not in d["skip"]]
        txt = [normalize(x["content"][:1100]) for x in ranked]
        ranks = []
        for e in d["expect"]:
            alts = [pattern(a) for a in (e if isinstance(e, list) else [e])]
            ranks.append(next((i + 1 for i, s in enumerate(txt) if any(p.search(s) for p in alts)), None))
        w = None if None in ranks else max(ranks)
        hit += bool(w and w <= top)
        rr.append(1 / w if w else 0.0)
    return hit / len(data), statistics.mean(rr)


if __name__ == "__main__":
    data = json.load(open(CACHE)) if os.path.exists(CACHE) else build_cache()
    print("baseline", evaluate(data))
    import time
    t0 = time.time()
    base = dict(wv=0.5, ob=0.35, cp=0.2, hb=2.0, le=0.25)

    def run(**kw):
        p = {**base, **kw}
        rag.W_VECTOR, rag.W_LEXICAL = p["wv"], 1 - p["wv"]
        rag.OUTLINE_BOOST, rag.CAL_PENALTY, rag.HEAD_BOOST, rag.LEN_EXP = p["ob"], p["cp"], p["hb"], p["le"]
        return evaluate(data)

    grid = {"wv": [0.2, 0.3, 0.4, 0.5, 0.6, 0.7], "ob": [0.0, 0.2, 0.35, 0.5, 0.8], "cp": [0.0, 0.2, 0.4],
            "hb": [0.0, 1.0, 2.0, 3.0, 5.0], "le": [0.0, 0.15, 0.25, 0.4, 0.6]}
    for k, vals in grid.items():       # coordinate sweep, one knob at a time
        print(k, [(v, *[round(x, 3) for x in run(**{k: v})]) for v in vals], flush=True)
    print("elapsed", round(time.time() - t0))
    rag.W_VECTOR, rag.W_LEXICAL, rag.OUTLINE_BOOST, rag.CAL_PENALTY, rag.HEAD_BOOST, rag.LEN_EXP = 0.5, 0.5, 0.35, 0.2, 2.0, 0.25
    for nc in (12, 16, 24, 32, 40):
        print("candidates", nc, evaluate(data, n_cand=nc))
