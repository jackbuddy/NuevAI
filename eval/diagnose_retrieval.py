"""Why did retrieval miss? For each missed item of a retrieval-mode report, find the chunks that DO contain the answer (the item's
verify_checks) and report where they were lost: not in the vector candidates / outranked / dropped by a filter / never looked up
structurally. Read-only. Usage:

    python eval/diagnose_retrieval.py data/eval/quality_<ts>.json [--ids a,b]
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--ids", default="")
    a = ap.parse_args()
    import eval_quality as eq
    env = eq.Env()
    qs, rag = env.qs, __import__("rag")
    items = {i["id"]: i for i in json.load(open(eq.SET_PATH))}
    rep = json.load(open(a.report))
    miss = [r for r in rep["results"] if r.get("retrievable") is False]
    if a.ids:
        miss = [r for r in miss if r["id"] in a.ids.split(",")]
    sb = qs.get_supabase()
    rows, off = [], 0
    while True:
        r = sb.table("document_chunks_v2").select("chunk_id,content,metadata").range(off, off + 999).execute().data
        rows += r
        off += 1000
        if len(r) < 1000:
            break
    out = []
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    for m in miss:
        it = items[m["id"]]
        checks = it.get("verify_checks") or []
        ans_ids = {r["chunk_id"] for r in rows if any(re.search(c, r["content"], re.I | re.S) for c in checks)}
        req = env.request(it, 0)
        retr = qs.retriever
        hist = [t.model_dump() for t in req.history]
        q = rag.fix_typos(req.query)
        standalone = rag.rewrite_followup(q, hist)
        prev_user = next((x["content"] for x in reversed(hist) if x.get("role") == "user"), "")
        resolved = rag.apply_grade(standalone, None)
        search_q = rag.expand_query(resolved if resolved != q else f"{prev_user} {q}" if prev_user and len(q.split()) <= 6 else q)
        intent = rag.parse_intent(resolved, today, prev_user) or rag.parse_intent(q, today, prev_user)
        vec = retr.vector_search(retr.embed(search_q))
        vec_rank = {r["chunk_id"]: k + 1 for k, r in enumerate(vec)}
        fts = retr.fts_search(search_q)
        res = retr.retrieve(req.query, hist, top_k=5, today=today, campus=req.campus)
        final = [d["chunk_id"] for d in res["docs"]] + [p["chunk_id"] for p in res["pinned"]] + [e["chunk_id"] for e in res["entities"]] + [e["chunk_id"] for e in res["events"]]
        in_vec = sorted((vec_rank[c], c) for c in ans_ids if c in vec_rank)
        in_fts = [r["chunk_id"] for r in fts if r["chunk_id"] in ans_ids]
        in_final = [c for c in final if c in ans_ids]
        ev_ids = [e["chunk_id"] for e in res["events"]]
        ev_pos = [ev_ids.index(c) for c in ans_ids if c in ev_ids]
        cal_vec = [c for c in ans_ids if c in vec_rank and c.startswith(("cal-", "sheet-"))]
        if ev_pos and min(ev_pos) >= 14:
            cause = f"retrieval miss: calendar block truncation - answer event is #{min(ev_pos) + 1} of {len(ev_ids)} structured matches but build_context shows only the first 14 by date"
        elif cal_vec and res["events"] and not in_final:
            cause = "retrieval miss: structured calendar lookup returned other events, and calendar rows from the vector search are discarded when any structured events exist (rag.py 'calendar chunks come from the structured lookup')"
        elif in_final:
            ctx_text = rag.build_context(res)[0]
            if any(re.search(c, ctx_text, re.I | re.S) for c in checks):
                cause = "present in final context but expected pattern unmatched (grader/expectation issue)"
            else:
                cause = "retrieval miss: chunk retrieved but its answer text was cut by the context builder (doc content[:1100] cap, 14-event cap, or events[:3] for next-game questions)"
        elif in_vec and in_vec[0][0] <= 32:
            cause = f"retrieval miss: answer chunk was a vector candidate (rank {in_vec[0][0]}/32) but lost in rerank/dedupe/top-k"
        elif in_fts:
            cause = "retrieval miss: only keyword (FTS) found it and it was lost in rerank"
        else:
            cause = "retrieval miss: answer chunk not among the 32 vector candidates (embedding/query mismatch; no keyword fallback)"
        out.append({"id": m["id"], "q": it["q"], "behavior": it["behavior"], "intent": (intent.label if intent else None), "intent_kind": (getattr(intent, "kind", None) if intent else None),
                    "search_q": search_q[:140], "answer_chunks": sorted(ans_ids)[:6], "vector_hits": in_vec[:3], "fts_off": retr._fts_off, "in_final": in_final[:3],
                    "final_titles": [(d.get("metadata") or {}).get("title", "")[:48] for d in res["docs"]][:5], "n_events": len(res["events"]), "cause": cause})
        print(f"[{m['id']}] {it['q'][:70]}\n   intent={out[-1]['intent']} events={out[-1]['n_events']} final={out[-1]['final_titles']}\n   answer chunks={out[-1]['answer_chunks'][:3]} vec_rank={in_vec[:2]}\n   -> {cause}")
    json.dump(out, open(ROOT / "data" / "eval" / "diagnosis_latest.json", "w"), indent=1)


if __name__ == "__main__":
    main()
