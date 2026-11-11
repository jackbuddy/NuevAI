"""Offline tests for the flagged-answers aggregation (flagged.py) and the feedback additions to insights.py.

    ANALYTICS_DISABLED=1 WARMUP_DISABLED=1 ANSWER_L2_DISABLED=1 python test_flagged.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("ANALYTICS_DISABLED", "1")
os.environ.setdefault("WARMUP_DISABLED", "1")
os.environ.setdefault("ANSWER_L2_DISABLED", "1")
os.environ["METRICS_TOKEN"] = "test-token-123"

import flagged as F
import insights as I
from analytics import MAX_PROPS_BYTES, _fit

RESULTS = []
UTC = timezone.utc
NOW = datetime(2026, 11, 9, 20, 0, tzinfo=UTC)
UA = "Mozilla/5.0 (iPhone) Safari/605"
AN = "a" * 12
BN = "b" * 12


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def row(ts, anon, event, props=None, ua=UA):
    return {"ts": ts.isoformat(), "anon_id": anon, "session_id": anon[:8] + "-s1", "event": event, "props": dict(props or {}), "ua": ua}


def chat(ts, anon, q, **kw):
    return row(ts, anon, "chat_answered", {"query": q, "cached": False, "sources": 4, "total_ms": 1500, "route": "llm", "model": "openai/gpt-oss-120b", **kw})


def M(minutes):
    return NOW - timedelta(minutes=minutes)


def build(rows, days=7, sel="users", **kw):
    return F.build(rows, NOW, days, sel, None, {}, **kw)


def find(out, q):
    return [r for r in out["rows"] if r["q"] == q]


# ---------------------------------------------------------------- thumbs-down joined with reason + comment + answer + server record
rows = [
    chat(M(60), AN, "when is the bus"),
    row(M(59), AN, "feedback", {"mid": "m1", "value": "down", "src": "thumb", "query": "when is the bus", "answer": "There is a bus at 3:30.", "n_src": 4}),
    row(M(58), AN, "feedback_detail", {"mid": "m1", "value": "down", "src": "thumb", "reason": "wrong"}),
    row(M(57), AN, "feedback_detail", {"mid": "m1", "value": "down", "src": "thumb", "reason": "outdated", "comment": "the bus moved to 4:00"}),
]
o = build(rows)
r = o["rows"][0] if o["rows"] else {}
check("down: one row for one answer", o["total"] == 1 and len(o["rows"]) == 1, o["total"])
check("down: latest reason wins, comment kept", r.get("reasons", [None])[0] == "outdated" and r.get("comment") == "the bus moved to 4:00", r)
check("down: answer text shown, no 'not recorded' note", r.get("answer") == "There is a bus at 3:30." and r.get("answer_note") is None)
check("down: server route/model joined from chat_answered", r.get("route") == "llm" and r.get("model") == "openai/gpt-oss-120b", r)
check("down: type and counts", r.get("types") == ["down"] and o["counts"]["types"]["down"] == 1 and o["counts"]["reasons"].get("outdated") == 1)

# ---------------------------------------------------------------- changed mind: up after down is not flagged; down after up is
rows = [row(M(50), AN, "feedback", {"mid": "m2", "value": "down", "query": "q2"}), row(M(49), AN, "feedback", {"mid": "m2", "value": "up", "query": "q2"})]
check("mind change: down then up -> not flagged", build(rows)["total"] == 0)
rows = [row(M(50), AN, "feedback", {"mid": "m3", "value": "up", "query": "q3"}), row(M(49), AN, "feedback", {"mid": "m3", "value": "down", "query": "q3"})]
check("mind change: up then down -> flagged", build(rows)["total"] == 1)
rows = [row(M(50), AN, "feedback", {"mid": "m4", "value": "up", "query": "q4", "answer": "fine"})]
check("plain thumbs-up is never flagged", build(rows)["total"] == 0)

# ---------------------------------------------------------------- thumbs counting dedupes by mid (insights)
rows_fb = [row(M(50), AN, "feedback", {"mid": "m5", "value": "down", "query": "q"}), row(M(49), AN, "feedback", {"mid": "m5", "value": "up", "query": "q"}),
           row(M(40), BN, "feedback", {"value": "down", "query": "legacy"}), row(M(39), BN, "feedback", {"value": "down", "query": "legacy2"})]
b = I.build(rows_fb + [chat(M(55), AN, "q")], NOW, 7, "users", None, {})
check("kpi: latest rating per answer id counts once; legacy events (no mid) all count", (b["kpis"]["cur"]["up"], b["kpis"]["cur"]["down"]) == (1, 2), b["kpis"]["cur"])

# ---------------------------------------------------------------- Not really on a fallback reply
rows = [
    chat(M(30), AN, "what is the bus fare", no_context=True, sources=0, topic="other"),
    row(M(30), AN, "answer_fallback", {"mid": "f1", "query": "what is the bus fare", "answer": "I don't have that.", "why": "no_sources"}),
    row(M(29), AN, "answer_check", {"mid": "f1", "value": "no", "query": "what is the bus fare", "answer": "I don't have that."}),
    row(M(29), AN, "feedback_detail", {"mid": "f1", "value": "down", "src": "check", "reason": "not_answered"}),
]
o = build(rows)
r = o["rows"][0]
check("not really: one row, merged with the auto flag and chat record", o["total"] == 1 and set(r["types"]) == {"not_really", "auto"}, r["types"])
check("not really: reason chip not_answered, outcome 'no sources'", "not_answered" in r["reasons"] and "no sources" in r["reasons"], r["reasons"])
rows = [row(M(30), AN, "answer_check", {"mid": "f2", "value": "yes", "query": "x", "answer": "ok"}), row(M(30), AN, "answer_fallback", {"mid": "f2", "query": "x", "answer": "ok", "why": "hedge"})]
o = build(rows)
check("fallback reply answered 'Yes' is still listed as auto (hedged) but not as not-really", o["total"] == 1 and o["rows"][0]["types"] == ["auto"])

# ---------------------------------------------------------------- automatic flags from chat_answered alone (no answer text: say so)
rows = [chat(M(20), AN, "q degraded", degraded=True, route="degraded"), chat(M(19), AN, "q nosrc", no_context=True, sources=0),
        chat(M(18), AN, "q notfound", fallback=True), chat(M(17), AN, "q good"), chat(M(16), AN, "q one source", sources=1),
        chat(M(15), AN, "q one source cached", sources=1, route="cache_l1", cached=True), chat(M(14), AN, "q canned", route="canned", sources=0, cached=True)]
o = build(rows)
qs_ = [r["q"] for r in o["rows"]]
check("auto: degraded / no sources / not found / single-source LLM flagged; good, cached, canned are not", sorted(qs_) == ["q degraded", "q nosrc", "q notfound", "q one source"], qs_)
check("auto: newest real flag first", [r["q"] for r in o["rows"]][0] == "q one source")
check("auto: no answer text -> explicit note, never fabricated", all(r["answer"] is None and r["answer_note"] for r in o["rows"]) and o["with_answer_text"] == 0)
check("auto: outcome labels", {r["q"]: r["reasons"] for r in o["rows"]}["q degraded"] == ["degraded"])
check("auto: degraded-only replies are marked infra and listed last; real flags first", [r["infra"] for r in o["rows"]] == [False, False, False, True] and o["total_main"] == 3 and o["total_infra"] == 1, [r["infra"] for r in o["rows"]])
o2 = build([chat(M(5), AN, "What's for lunch today?", sources=1, topic="lunch"), chat(M(4), AN, "who is the head of school", sources=1, topic="other"),
            chat(M(3), AN, "old event one source", sources=1, route=None)])
check("auto: a single source is fine for structured topics and for events without a route", [r["q"] for r in o2["rows"]] == ["who is the head of school"], [r["q"] for r in o2["rows"]])

# ---------------------------------------------------------------- legacy thumbs-down (no mid, no answer) is joined to its chat by question
rows = [chat(M(90), AN, "What is the dress code?"), row(M(89), AN, "feedback", {"value": "down", "query": "what is the dress code"})]
o = build(rows)
check("legacy down: listed, answer unavailable, route joined by normalised question", o["total"] == 1 and o["rows"][0]["answer"] is None and o["rows"][0]["route"] == "llm", o["rows"])
check("legacy down: the chat is consumed (not listed twice)", o["total"] == 1)

# ---------------------------------------------------------------- written feedback
rows = [row(M(10), AN, "feedback_text", {"kind": "Wrong answer", "text": "lunch was wrong", "tab": "chat", "mid": "w1", "lq": "lunch today", "answer": "Pizza."}),
        row(M(9), BN, "feedback_text", {"kind": "Idea", "text": "add dark mode", "tab": "calendar"})]
o = build(rows)
check("written: tied to an answer when mid present, free standing otherwise", o["total"] == 2 and o["counts"]["types"]["written"] == 2)
tied = find(o, "lunch today")[0]
check("written: tied row carries the answer and the report", tied["answer"] == "Pizza." and tied["report"] == "lunch was wrong" and tied["report_kind"] == "Wrong answer")

# ---------------------------------------------------------------- audience, range, bounds
rows = [row(M(5), AN, "feedback", {"mid": "z1", "value": "down", "query": "mine"}, ua="python-requests/2.31")]
check("audience: bot browser hidden from Real users, shown for Test & bots / Everything", build(rows)["total"] == 0 and build(rows, sel="test_bots")["total"] == 1 and build(rows, sel="all")["total"] == 1)
rows = [row(NOW - timedelta(days=3), AN, "feedback", {"mid": "r1", "value": "down", "query": "old"})]
check("range: 24h excludes a 3 day old item, 7d includes it, all time includes it", build(rows, days=1)["total"] == 0 and build(rows, days=7)["total"] == 1 and build(rows, days=0)["total"] == 1)
many = [row(M(i), AN, "feedback", {"mid": f"k{i}", "value": "down", "query": f"q{i}"}) for i in range(1, 260)]
o = build(many, limit=50)
check("bounded: rows capped, total and truncated reported", len(o["rows"]) == 50 and o["total"] == 259 and o["truncated"])
dup = [row(M(5), AN, "feedback", {"mid": "d1", "value": "down", "query": "d", "_eid": "same"}) for _ in range(3)]
check("duplicates by _eid collapse", build(dup)["total"] == 1)
check("junk rows tolerated", build([{"ts": "garbage", "anon_id": AN, "event": "feedback", "props": {}}, {"ts": M(1).isoformat(), "anon_id": "", "event": "feedback"}, row(M(1), AN, "feedback", {"mid": 5, "value": "down"})])["total"] >= 0)
check("json serialisable", bool(json.dumps(build(many))))

# ---------------------------------------------------------------- the server keeps the new events under the props limit
big = {"mid": "m" * 12, "value": "down", "src": "thumb", "reason": "wrong", "query": "q" * 200, "answer": "a" * 600, "cached": False, "degraded": False, "n_src": 5}
check("size: a full-size client feedback event (ASCII) passes through _fit unchanged", len(json.dumps(big)) <= MAX_PROPS_BYTES and _fit(big) == big, len(json.dumps(big)))

# ---------------------------------------------------------------- HTTP contract: token-gated, bounded range, audience falls back
from fastapi.testclient import TestClient
import query_server as qs

client = TestClient(qs.app)
H = {"x-metrics-token": "test-token-123"}
seen = []
orig = qs.analytics.flagged
qs.analytics.flagged = lambda days, sel="users", force=False: (seen.append((days, sel)) or {"rows": [], "total": 0})
check("flagged endpoint: needs the token", client.get("/api/metrics/flagged").status_code == 401 and client.get("/api/metrics/flagged", headers={"x-metrics-token": "nope"}).status_code == 401)
check("flagged endpoint: 200 with token", client.get("/api/metrics/flagged?days=30&aud=all", headers=H).status_code == 200 and seen[-1] == (30, "all"))
client.get("/api/metrics/flagged?days=400&aud=bogus", headers=H)
check("flagged endpoint: range is clamped to 90 and unknown audience falls back to users", seen[-1] == (90, "users"), seen[-1])
qs.analytics.flagged = orig
from analytics import Analytics


def _boom():
    raise RuntimeError("no database in tests")


an = Analytics(_boom, start_thread=False)
an.table_ok = False                                    # events come from the in-memory ring, as when the table is missing
now = datetime.now(UTC)
an._ring.append({"ts": (now - timedelta(minutes=3)).isoformat(), "anon_id": AN, "session_id": None, "event": "feedback", "ua": UA,
                 "props": {"mid": "e1", "value": "down", "query": "ring q", "answer": "ring a"}})
o1 = an.flagged(7)
check("Analytics.flagged: end to end from the ring buffer", o1["total"] == 1 and o1["rows"][0]["answer"] == "ring a", o1)
an._ring.append({"ts": now.isoformat(), "anon_id": AN, "session_id": None, "event": "feedback", "ua": UA, "props": {"mid": "e2", "value": "down", "query": "second"}})
check("Analytics.flagged: cached for 60 s (second call returns the same object), force refreshes", an.flagged(7) is o1 and an.flagged(7, force=True)["total"] == 2)

fails = [n for n, ok_ in RESULTS if not ok_]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
