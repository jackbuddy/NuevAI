"""Speed benchmark over the SSE endpoint, by question category.

    python bench_speed.py http://localhost:8765            # local
    python bench_speed.py https://nuevai-backend.onrender.com --repeat

Per question it records time to first byte (the `meta` event, i.e. retrieval finished), time to first token, total, and
the server-side stage timings from the `done` event. LLM 429s are reported as such, never as speed numbers.
"""
import json
import statistics as st
import sys
import time

import httpx

QUESTIONS = {
    "smalltalk": ["hi", "thanks!", "what can you do"],
    "calendar": ["When is the next girls soccer game?", "What is happening on November 12?", "When is the November break?"],
    "teacher/course": ["Who teaches Multivariable Calculus?", "What engineering courses are offered?", "Where does Ms Jacquot teach?"],
    "policy/docs": ["What is the cell phone policy?", "What is the plagiarism policy?", "What are the graduation requirements?"],
    "clubs": ["What clubs can I join?", "Is there a robotics club?"],
    "links": ["How do I book a campus tour?", "How can I donate to Nueva?"],
}


def one(base: str, q: str) -> dict:
    t0 = time.time()
    out = {"q": q, "ttfb": None, "ttft": None, "total": None, "status": "ok", "timings": {}, "cached": None}
    try:
        with httpx.stream("POST", base + "/api/chat/stream", json={"query": q}, timeout=90) as r:
            if r.status_code != 200:
                out["status"] = f"http{r.status_code}"
                r.read()
                return out
            ev = None
            for line in r.iter_lines():
                if line.startswith("event: "):
                    ev = line[7:]
                elif line.startswith("data: "):
                    now = round((time.time() - t0) * 1000)
                    d = json.loads(line[6:])
                    if out["ttfb"] is None:
                        out["ttfb"] = now
                    if ev == "meta":
                        out["cached"] = d.get("cached")
                    if ev == "delta" and out["ttft"] is None:
                        out["ttft"] = now
                    if ev == "done":
                        out["timings"] = d.get("timings", {})
                        out["degraded"] = d.get("degraded", False)
                    if ev == "error":
                        out["status"] = "error:" + d.get("message", "")[:50]
    except Exception as e:
        out["status"] = type(e).__name__
    out["total"] = round((time.time() - t0) * 1000)
    return out


def p(vals, q):
    vals = sorted(v for v in vals if v is not None)
    return vals[min(len(vals) - 1, int(len(vals) * q))] if vals else None


if __name__ == "__main__":
    base = sys.argv[1].rstrip("/")
    repeat = "--repeat" in sys.argv
    t = time.time()
    h = httpx.get(base + "/health", timeout=120)
    print(f"/health {h.status_code} in {round((time.time() - t) * 1000)} ms (first call: cold start if the service was asleep)")
    rows = []
    for cat, qs in QUESTIONS.items():
        for q in qs:
            r = one(base, q)
            r["cat"] = cat
            rows.append(r)
            print(f"{cat:15} {r['status']:10} ttfb={r['ttfb']} ttft={r['ttft']} total={r['total']} cached={r['cached']} {r['timings']} :: {q}", flush=True)
            time.sleep(1.5)
    if repeat:
        print("--- repeat (should be cache hits)")
        for r0 in rows[:6]:
            r = one(base, r0["q"])
            print(f"{r0['cat']:15} {r['status']:10} ttfb={r['ttfb']} total={r['total']} cached={r['cached']} :: {r0['q']}")
    print("--- by category (ok answers only; ms)")
    for cat in QUESTIONS:
        ok = [r for r in rows if r["cat"] == cat and r["status"] == "ok"]
        bad = len([r for r in rows if r["cat"] == cat]) - len(ok)
        print(f"{cat:15} n={len(ok)} errors={bad} ttfb p50={p([r['ttfb'] for r in ok], .5)} ttft p50={p([r['ttft'] for r in ok], .5)} "
              f"total p50={p([r['total'] for r in ok], .5)} max={p([r['total'] for r in ok], .99)}")
    json.dump(rows, open(f"data/eval/speed_{int(time.time())}.json", "w"), indent=1)
