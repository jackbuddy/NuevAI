"""Natural-language robustness eval (FREE: retrieval mode, no LLM call).

    python eval_natural.py --build              # (re)write eval/natural_set.json from eval/build_natural_set.py (read-only on the index)
    python eval_natural.py                      # run the natural set + the direct source questions, print paraphrase robustness
    python eval_natural.py --no-direct          # paraphrases only (faster)
    python eval_natural.py --ids log-01,gm-18~1 --verbose
    python eval_natural.py --today 2026-11-16   # pretend it is another day (a weekday when run on a weekend); eval only
    python eval_natural.py --compare data/eval/natural_<a>.json data/eval/natural_<b>.json   # before / after table, no network

The set (eval/natural_set.json) holds casual / indirect / slangy / abbreviated / typo'd / implied-subject / parent-style / statement /
multi-part / follow-up rewordings of VERIFIED golden questions; each keeps its source item's verified expectation, so a pass still means
"the fact is in the context the model would see" (or in the deterministic no-LLM answer). Extra per-item checks used only here:
`retr_forbid` (patterns that must not be in the context, e.g. Middle School games leading an Upper School question) and `route`
(the path the question must take: canned = no retrieval and no LLM).
Report: overall, by category, by phrasing type, per source item (direct pass vs paraphrase pass rate), and every failure with what was
retrieved. Prompt size (characters of the system prompt, the token cost driver) and retrieval time are recorded per item.
Retrieval is slightly nondeterministic: run 2-3 times and compare.
"""
import argparse
import collections
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).parent
os.environ.setdefault("ANALYTICS_DISABLED", "1")
os.environ.setdefault("WARMUP_DISABLED", "1")
os.environ.setdefault("ANSWER_L2_DISABLED", "1")
sys.path.insert(0, str(HERE))
import eval_quality as eq          # noqa: E402

SET_PATH = HERE / "eval" / "natural_set.json"
SRC_PATH = HERE / "eval" / "quality_set.json"


def fix_today(qs, day: str):
    """Eval only: make the in-process server believe it is `day` (so weekday questions can be measured on a weekend)."""
    real = qs.datetime
    d = date.fromisoformat(day)

    class Fixed(real):
        @classmethod
        def now(cls, tz=None):
            n = real.now(tz)
            return n.replace(year=d.year, month=d.month, day=d.day)
    qs.datetime = Fixed


def run_item(env, it: dict, n: int, today: date, sb, vocab) -> dict:
    rec = {k: it.get(k) for k in ("id", "source_id", "category", "phrasing", "behavior", "q", "campus")}
    dyn = it.get("dynamic") or {}
    expect = None
    if dyn:
        expect, note = eq.resolve_dynamic(it, today, sb)
        if expect is None and (today > date.fromisoformat(dyn["valid_through"]) or dyn.get("resolver")):
            return {**rec, "status": "stale", "note": note or f"valid through {dyn['valid_through']}"}
        rec["resolved"] = note
    t0 = time.time()
    prep = env.prepare(it, n)
    rec["ms"] = round((time.time() - t0) * 1000)
    if prep["kind"] == "error":
        return {**rec, "status": "error", "note": prep["error"]}
    exp_eff = (it["expect"] if expect is None else list(expect) + list(it["expect"]))
    rg = eq.retr_grade(it, prep, exp_eff if (expect is not None or not it.get("retr")) else it["expect"], it.get("forbid") or [], today, vocab)
    route = "canned" if prep["kind"] == "canned" else ("lunch" if prep["result"].get("lunch") else "rag")
    ok = rg["retrievable"]
    missing = list(rg["missing"])
    if ok is None:                      # safety / redirect items: only a deterministic reply can be judged for free
        return {**rec, "status": "na", "route": route}
    hay = prep["context"]
    for rx in it.get("ctx_expect") or []:           # patterns shaped like the context ("US Boys Basketball Varsity - Game"), never used to grade an answer
        if not re.search(rx, hay):
            ok = False
            missing.append("not in context: " + rx[:60])
    for rx in it.get("retr_forbid") or []:
        if re.search(rx, hay):
            ok = False
            missing.append("FORBIDDEN in context: " + rx[:60])
    if it.get("route") and it["route"] != route:
        ok = False
        missing.append(f"route {route}, wanted {it['route']}")
    res = prep["result"] or {}
    intent = res.get("intent")
    rec.update(status="ok" if ok else "miss", route=route, missing=missing, sys_chars=len(prep["system"]), ctx_chars=len(prep["context"]),
               resolved_q=res.get("resolved"), intent=(f"{intent.label}|sport={intent.sport}|kind={intent.kind}|cat={intent.category}|div={intent.division}|g={intent.gender}" if intent else None),
               interp=res.get("interp"), ents=len(res.get("entities") or []), titles=[(s.get("title") or "")[:40] for s in prep["sources"][:6]])
    return rec


def pct(o, n):
    return f"{100 * o / n:5.1f}%" if n else "  n/a"


def table(title: str, rows: dict):
    print(f"\n{title:28} {'ok':>4} {'n':>4} {'rate':>7}")
    for k, (o, n) in sorted(rows.items(), key=lambda kv: (kv[1][0] / kv[1][1] if kv[1][1] else 0, str(kv[0]))):
        print(f"  {str(k)[:26]:26} {o:4d} {n:4d} {pct(o, n)}")


def summarize(results: list[dict], direct: dict) -> dict:
    scored = [r for r in results if r["status"] in ("ok", "miss")]
    ok = sum(r["status"] == "ok" for r in scored)
    by_cat, by_phr = collections.defaultdict(lambda: [0, 0]), collections.defaultdict(lambda: [0, 0])
    for r in scored:
        for d, k in ((by_cat, r["category"]), (by_phr, r["phrasing"])):
            d[k][1] += 1
            d[k][0] += r["status"] == "ok"
    per_src = collections.defaultdict(lambda: [0, 0])
    for r in scored:
        if r.get("source_id"):
            per_src[r["source_id"]][1] += 1
            per_src[r["source_id"]][0] += r["status"] == "ok"
    both = [s for s in per_src if s in direct]
    d_ok = [s for s in both if direct[s]]
    robust = [s for s in d_ok if per_src[s][0] == per_src[s][1]]
    para_given_direct = [sum(per_src[s][0] for s in d_ok), sum(per_src[s][1] for s in d_ok)]
    sysc = sorted(r["sys_chars"] for r in scored if r.get("sys_chars"))
    ms = sorted(r["ms"] for r in scored if r.get("ms") is not None)
    return {"n": len(scored), "ok": ok, "rate": ok / len(scored) if scored else None, "by_category": {k: v for k, v in by_cat.items()}, "by_phrasing": {k: v for k, v in by_phr.items()},
            "per_source": {k: v for k, v in per_src.items()}, "direct_n": len(both), "direct_ok": len(d_ok), "robust_sources": len(robust),
            "para_given_direct_ok": para_given_direct, "not_scored": dict(collections.Counter(r["status"] for r in results if r["status"] not in ("ok", "miss"))),
            "sys_chars_p50": sysc[len(sysc) // 2] if sysc else None, "sys_chars_mean": round(sum(sysc) / len(sysc)) if sysc else None,
            "ms_p50": ms[len(ms) // 2] if ms else None, "ms_p90": ms[int(len(ms) * 0.9)] if ms else None,
            "routes": dict(collections.Counter(r.get("route") for r in scored))}


def report(results, summ, direct):
    print("\n" + "=" * 100)
    print(f"NATURAL SET (retrieval mode, free): {summ['ok']}/{summ['n']} = {pct(summ['ok'], summ['n'])} of natural phrasings have the expected fact in the context   not scored: {summ['not_scored']}")
    table("by category", summ["by_category"])
    table("by phrasing type", summ["by_phrasing"])
    if summ["direct_n"]:
        o, n = summ["para_given_direct_ok"]
        print(f"\nPARAPHRASE ROBUSTNESS over {summ['direct_n']} source items that were run both ways:")
        print(f"  direct wording passes:                        {summ['direct_ok']}/{summ['direct_n']} = {pct(summ['direct_ok'], summ['direct_n'])}")
        print(f"  paraphrases pass when the direct one passes:  {o}/{n} = {pct(o, n)}")
        print(f"  source items where EVERY paraphrase passes:   {summ['robust_sources']}/{summ['direct_ok']} = {pct(summ['robust_sources'], summ['direct_ok'])}")
        weak = [(s, v) for s, v in summ["per_source"].items() if v[0] < v[1]]
        print(f"  source items with a failing paraphrase ({len(weak)}): " + ", ".join(f"{s} {v[0]}/{v[1]}{'' if direct.get(s, True) else ' (direct fails too)'}" for s, v in sorted(weak)))
    print(f"\nprompt size: median {summ['sys_chars_p50']} chars, mean {summ['sys_chars_mean']} chars (~chars/4 tokens);  retrieval time p50 {summ['ms_p50']} ms, p90 {summ['ms_p90']} ms;  routes {summ['routes']}")
    miss = [r for r in results if r["status"] == "miss"]
    print(f"\nFAILURES ({len(miss)}):")
    for r in miss:
        print(f"  [{r['id']}] ({r['phrasing']}, {r['category']}) {r['q'][:70]!r}\n      missing: {[m[:50] for m in r['missing'][:3]]}  route={r['route']} intent={r.get('intent')} ents={r.get('ents')}"
              f"\n      resolved={str(r.get('resolved_q'))[:80]!r} interp={str(r.get('interp'))[:120]}\n      got: {r.get('titles')}")


def compare(a_path: str, b_path: str):
    a, b = json.load(open(a_path)), json.load(open(b_path))
    print(f"before: {a_path} ({a['today']})\nafter:  {b_path} ({b['today']})")
    for dim in ("by_category", "by_phrasing"):
        print(f"\n{dim:28} {'before':>14} {'after':>14}")
        keys = sorted(set(a["summary"][dim]) | set(b["summary"][dim]))
        for k in keys:
            x, y = a["summary"][dim].get(k, [0, 0]), b["summary"][dim].get(k, [0, 0])
            print(f"  {k[:26]:26} {x[0]:3d}/{x[1]:<3d} {pct(*x)}  {y[0]:3d}/{y[1]:<3d} {pct(*y)}")
    x, y = a["summary"], b["summary"]
    print(f"\n  {'ALL':26} {x['ok']:3d}/{x['n']:<3d} {pct(x['ok'], x['n'])}  {y['ok']:3d}/{y['n']:<3d} {pct(y['ok'], y['n'])}")
    print(f"  prompt chars median {x['sys_chars_p50']} -> {y['sys_chars_p50']}, mean {x['sys_chars_mean']} -> {y['sys_chars_mean']}; retrieval p50 {x['ms_p50']} -> {y['ms_p50']} ms")
    sa = {r["id"]: r["status"] for r in a["results"]}
    sb_ = {r["id"]: r["status"] for r in b["results"]}
    print("  fixed:    ", sorted(i for i in sb_ if sa.get(i) == "miss" and sb_[i] == "ok"))
    print("  regressed:", sorted(i for i in sb_ if sa.get(i) == "ok" and sb_[i] == "miss"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="rebuild eval/natural_set.json first")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--file", default=str(SET_PATH))
    ap.add_argument("--ids", default="")
    ap.add_argument("--category", default="")
    ap.add_argument("--phrasing", default="")
    ap.add_argument("--no-direct", action="store_true", help="skip running the direct source questions")
    ap.add_argument("--holdout", action="store_true", help="run only the hold-out wordings (written after the fixes, never tuned on): the out-of-sample number")
    ap.add_argument("--today", default="", help="YYYY-MM-DD: evaluate as if it were this day")
    ap.add_argument("--tag", default="", help="label stored in the report (e.g. baseline, after)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    a = ap.parse_args()
    if a.compare:
        compare(*a.compare)
        return 0
    if a.build or a.build_only:
        sys.path.insert(0, str(HERE / "eval"))
        import build_natural_set as b
        items = b.build()
        json.dump(items, open(SET_PATH, "w"), indent=1, ensure_ascii=False)
        print(f"wrote {SET_PATH}: {len(items)} items, {sum(1 for i in items if i['verified'])} verified")
        for i in items:
            if not i["source_id"]:
                print(f"  {i['id']} verified={i['verified']}  {i['verification'][:160]}")
        if a.build_only:
            return 0
    items = [i for i in json.load(open(a.file)) if i.get("verified") and bool(i.get("holdout")) == a.holdout]      # the hold-out wordings are never mixed with the tuned set
    if a.ids:
        items = [i for i in items if i["id"] in a.ids.split(",") or i.get("source_id") in a.ids.split(",")]
    if a.category:
        items = [i for i in items if i["category"] in a.category.split(",")]
    if a.phrasing:
        items = [i for i in items if i["phrasing"] in a.phrasing.split(",")]
    env = eq.Env()
    if a.today:
        fix_today(env.qs, a.today)
    sb = env.qs.get_supabase()
    today = env.qs.datetime.now(eq.PACIFIC).date()
    vocab = eq.index_vocab()
    print(f"{len(items)} natural items, today={today}", flush=True)
    results = []
    for n, it in enumerate(items, 1):
        r = run_item(env, it, n, today, sb, vocab)
        results.append(r)
        if a.verbose or r["status"] in ("miss", "error"):
            print(f"[{n:3}/{len(items)}] {r['status']:5} {r['id']:11} {r['phrasing']:10} {it['q'][:60]}" + (f"  missing: {[m[:40] for m in r.get('missing', [])[:2]]}" if r.get("missing") else "") + (f"  {r.get('note', '')}" if r["status"] == "error" else ""), flush=True)
    direct = {}
    if not a.no_direct:
        src = {i["id"]: i for i in json.load(open(SRC_PATH))}
        want = sorted({i["source_id"] for i in items if i.get("source_id")})
        print(f"running {len(want)} direct source questions for comparison", flush=True)
        for n, sid in enumerate(want, 1):
            it = {**src[sid], "source_id": None}
            r = run_item(env, it, 1000 + n, today, sb, vocab)
            if r["status"] in ("ok", "miss"):
                direct[sid] = r["status"] == "ok"
    summ = summarize(results, direct)
    report(results, summ, direct)
    eq.OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = eq.OUT_DIR / f"natural_{datetime.now().strftime('%Y%m%d_%H%M%S')}{('_' + a.tag) if a.tag else ''}.json"
    json.dump({"when": datetime.now().isoformat(timespec="seconds"), "today": today.isoformat(), "tag": a.tag, "summary": summ, "direct": direct, "results": results}, open(path, "w"), indent=1, ensure_ascii=False, default=str)
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
