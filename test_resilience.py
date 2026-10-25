"""Failure drills, run locally by simulation (no real outage needed):

    ANALYTICS_DISABLED=1 WARMUP_DISABLED=1 ANSWER_L2_DISABLED=1 python test_resilience.py

Retrieval uses the real Supabase data; only the failing dependency is faked. Every drill must give HTTP 200 / a normal SSE
stream with a friendly, accurate degraded answer (retrieved facts and links, no model text) or a friendly 503, never a stack trace.
"""
import json
import os
import sys
import time

os.environ.setdefault("ANALYTICS_DISABLED", "1")
os.environ.setdefault("WARMUP_DISABLED", "1")
os.environ.setdefault("ANSWER_L2_DISABLED", "1")

import httpx
from fastapi.testclient import TestClient
from groq import APIConnectionError, APIStatusError, APITimeoutError

import embedder
import query_server as qs
from answer_store import AnswerStore, norm_key, storable

client = TestClient(qs.app)
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def status_err(code):
    req = httpx.Request("POST", "https://api.groq.com/x")
    body = "Rate limit reached ... Please try again in 40m1.6s" if code == 429 else "boom"
    return APIStatusError(body, response=httpx.Response(code, request=req), body=None)


class FakeGroq:
    """Stands in for groq_client. behavior(model) -> raises, or returns a stream/completion."""
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []
        self.chat = self
        self.completions = self

    def with_options(self, **kw):
        return self

    def create(self, messages, stream, **kw):
        self.calls.append(kw["model"])
        return self.behavior(kw["model"], stream)


def good_stream(text="Here is the answer.", break_after=None):
    class D:  # chunk
        def __init__(s, t): s.choices = [type("C", (), {"delta": type("X", (), {"content": t})()})()]
    def gen():
        for i, w in enumerate(text.split(" ")):
            if break_after is not None and i >= break_after:
                raise httpx.ReadTimeout("stream stalled")
            yield D(w + " ")
    return gen()


def good_completion(text="Here is the answer."):
    return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": text})()})()]})()


def sse(resp):
    ev, out = None, []
    for line in resp.text.splitlines():
        if line.startswith("event: "):
            ev = line[7:]
        elif line.startswith("data: "):
            out.append((ev, json.loads(line[6:])))
    return out


def stream_q(q, **extra):
    r = client.post("/api/chat/stream", json={"query": q, **extra})
    return r, sse(r)


def text_of(evs):
    return "".join(d.get("t", "") for e, d in evs if e == "delta")


def reset():
    qs.answer_cache.clear()
    qs.api_cache.clear()
    qs._hits.clear()
    qs.retriever.embed_cache.clear()


Q_CAL = "When is the November break?"
Q_DOC = "What is the cell phone policy?"
orig_groq = qs.groq_client
orig_post = embedder._client.post
orig_vs = qs.retriever.vector_search

# ---------------------------------------------------------------- 1. Groq 429 on every model
reset()
fake = FakeGroq(lambda m, s: (_ for _ in ()).throw(status_err(429)))
qs.groq_client = fake
t = time.time()
r, evs = stream_q(Q_CAL)
body = text_of(evs)
done = [d for e, d in evs if e == "done"]
check("429 all models: stream is 200 with degraded answer", r.status_code == 200 and done and done[0].get("degraded") is True, r.text[:200])
check("429 all models: tried every model in the chain", len(fake.calls) >= 3, str(fake.calls))
check("429 all models: answer shows retrieved calendar facts", "November" in body and "trouble writing" in body, body[:200])
check("429 all models: no error event", not [1 for e, _ in evs if e == "error"])
check("429 all models: degraded answer is not cached", qs.answer_cache.stats()["items"] == 0)
r2 = client.post("/api/chat", json={"query": Q_DOC})
j = r2.json()
check("429 all models: /api/chat returns 200 degraded with links/titles", r2.status_code == 200 and j.get("degraded") and j["sources"] and "Related pages" in j["answer"], r2.text[:200])
check("429 all models: fast (<5 s with no waits)", time.time() - t < 5, f"{time.time()-t:.1f}s")

# ---------------------------------------------------------------- 2. Groq timeout / connection error
reset()
def timeout(m, s): raise APITimeoutError(request=httpx.Request("POST", "https://api.groq.com/x"))
qs.groq_client = FakeGroq(timeout)
r, evs = stream_q(Q_DOC)
done = [d for e, d in evs if e == "done"]
check("Groq timeout: degraded answer, not an error", r.status_code == 200 and done and done[0].get("degraded") and not [1 for e, _ in evs if e == "error"], r.text[:200])
reset()
def conn(m, s): raise APIConnectionError(request=httpx.Request("POST", "https://api.groq.com/x"))
qs.groq_client = FakeGroq(conn)
r = client.post("/api/chat", json={"query": Q_DOC})
check("Groq connection error: degraded 200", r.status_code == 200 and r.json().get("degraded"), r.text[:200])
reset()
qs.groq_client = FakeGroq(lambda m, s: (_ for _ in ()).throw(status_err(500)))
r = client.post("/api/chat", json={"query": Q_DOC})
check("Groq HTTP 500 everywhere: degraded 200", r.status_code == 200 and r.json().get("degraded"), r.text[:200])

# ---------------------------------------------------------------- 3. first model fails, fallback works (no degradation)
reset()
def second_ok(m, s):
    if m == qs.LLM_MODEL: raise status_err(429)
    return good_stream() if s else good_completion()
qs.groq_client = FakeGroq(second_ok)
r, evs = stream_q(Q_DOC)
check("fallback model answers normally", "Here is the answer" in text_of(evs) and not [d for e, d in evs if e == "done" and d.get("degraded")])
check("normal answer is cached (L1)", qs.answer_cache.stats()["items"] == 1)

# ---------------------------------------------------------------- 4. stream breaks mid-answer
reset()
qs.groq_client = FakeGroq(lambda m, s: good_stream("one two three four five six", break_after=3))
r, evs = stream_q(Q_DOC)
body = text_of(evs)
check("mid-stream failure: partial text kept plus honest cut-off note", "one two three" in body and "cut off" in body, body[:200])
check("mid-stream failure: truncated answer is not cached", qs.answer_cache.stats()["items"] == 0)

# ---------------------------------------------------------------- 5. edge function 546 / 5xx / timeout
class Resp:
    def __init__(self, code): self.status_code = code
    def json(self): return {}
for code in (546, 500, 503):
    reset()
    embedder._client.post = lambda *a, _c=code, **k: Resp(_c)
    qs.groq_client = FakeGroq(lambda m, s: good_stream() if s else good_completion())
    t = time.time()
    r, evs = stream_q(Q_DOC)
    err = [d for e, d in evs if e == "error"]
    check(f"embed HTTP {code}: friendly message, no stack trace", r.status_code == 200 and (err and "temporarily" in err[0]["message"] or "Here is the answer" in text_of(evs)), r.text[:200])
    check(f"embed HTTP {code}: bounded time (<25 s)", time.time() - t < 25, f"{time.time()-t:.1f}s")
reset()
embedder._client.post = lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("slow"))
qs.groq_client = FakeGroq(lambda m, s: good_stream() if s else good_completion())
r, evs = stream_q(Q_CAL)
check("embed timeout: calendar question still answered from structured lookup", "Here is the answer" in text_of(evs), r.text[:200])
r = client.post("/api/chat", json={"query": Q_DOC})
check("embed timeout: /api/chat gives friendly 503 (not 500)", r.status_code in (200, 503) and "Traceback" not in r.text, r.text[:200])
embedder._client.post = orig_post

# ---------------------------------------------------------------- 6. Supabase vector search slow / unreachable
reset()
def slow_vs(q):
    time.sleep(3); raise RuntimeError("db timeout")
qs.retriever.vector_search = slow_vs
qs.groq_client = FakeGroq(lambda m, s: good_stream() if s else good_completion())
r, evs = stream_q(Q_CAL)
check("vector search failing: calendar question still answered", "Here is the answer" in text_of(evs), r.text[:200])
reset()
r, evs = stream_q("tell me about the mentor program philosophy")
err = [d for e, d in evs if e == "error"]
check("vector search failing + no structured hit: friendly 'search unavailable'", (err and "unavailable" in err[0]["message"]) or "Here is the answer" in text_of(evs), r.text[:200])
qs.retriever.vector_search = orig_vs

reset()
orig_sb = qs.retriever.sb
def dead_sb(): raise ConnectionError("supabase unreachable")
qs.retriever.sb = dead_sb
qs.get_supabase_orig = qs._supabase_client
qs._data_state.update(at=0.0, rows=None)
r, evs = stream_q(Q_DOC)
err = [d for e, d in evs if e == "error"]
check("Supabase unreachable: friendly error event", r.status_code == 200 and err and "went wrong" in err[0]["message"] and "Traceback" not in r.text, r.text[:200])
r = client.post("/api/chat", json={"query": "hello"})
check("Supabase unreachable: smalltalk still works", r.status_code == 200 and r.json()["model_used"] == "none")
qs.retriever.sb = orig_sb
qs._data_state.update(at=0.0, rows=None)

# L1 hit keeps working while Supabase is down
reset()
qs.groq_client = FakeGroq(lambda m, s: good_stream() if s else good_completion())
stream_q(Q_DOC)
qs.retriever.sb = dead_sb
r, evs = stream_q(Q_DOC)
check("Supabase down: cached answers still served from L1", "Here is the answer" in text_of(evs) and [d for e, d in evs if e == "meta"][0].get("cached"))
qs.retriever.sb = orig_sb
qs.groq_client = orig_groq

# ---------------------------------------------------------------- 7. persistent cache (L2) with a fake table
class FakeTable:
    def __init__(self, store, fail=None): self.store, self.fail, self.f = store, fail, {}
    def select(self, *a): return self
    def eq(self, c, v): self.f["key"] = v; return self
    def gt(self, c, v): self.f["gt"] = v; return self
    def limit(self, n): return self
    def upsert(self, row, on_conflict=None): self.row = row; self.mode = "up"; return self
    def execute(self):
        if self.fail: raise self.fail
        if getattr(self, "mode", "") == "up":
            self.store[self.row["key"]] = self.row
            return type("R", (), {"data": [self.row]})()
        row = self.store.get(self.f.get("key"))
        ok = row and row["expires_at"] > self.f["gt"]
        return type("R", (), {"data": [row] if ok else []})()
class FakeSB:
    def __init__(self, store, fail=None): self.store, self.fail = store, fail
    def table(self, name): return FakeTable(self.store, self.fail)

store = {}
l2 = AnswerStore(lambda: FakeSB(store))
payload = {"answer": "A", "model_used": "m", "sources": [{"title": "t"}], "suggestions": []}
check("L2 key is the normalized question", norm_key("  What IS the Cell-Phone policy?? ") == "what is the cellphone policy")
l2.put("What is the cell phone policy?", payload, ttl=3600)
l2.flush()
hit = l2.get("what is the CELL phone policy")
check("L2 round trip (case/punctuation insensitive)", hit and hit["answer"] == "A")
l2.put("old question here", payload, ttl=-5)
l2.flush()
check("L2 honours TTL (expired rows ignored)", l2.get("old question here") is None)
l2.put("my email is kid@example.com", payload, ttl=3600)
l2.put("call 650-235-7100 please", payload, ttl=3600)
l2.put("student id 12345678", payload, ttl=3600)
l2.put("no sources question", {**payload, "sources": []}, ttl=3600)
l2.flush()
check("L2 skips personal-looking questions and source-less answers", len(store) == 2 and not any("example" in k or "650" in k for k in store), str(list(store)))
check("L2 rows contain no ids", all(set(r) <= {"key", "answer", "model_used", "sources", "suggestions", "date_bound", "created_at", "expires_at"} for r in store.values()))
bad = AnswerStore(lambda: FakeSB({}, fail=RuntimeError('relation "answer_cache" does not exist')))
check("L2 table missing: get returns None, no exception", bad.get("anything goes here") is None and bad.down_until > time.time())
bad.put("another question here", payload, ttl=60)
check("L2 breaker open: later calls are no-ops", bad.get("x y z q") is None)
class SlowSB(FakeSB):
    def table(self, name): time.sleep(5); return super().table(name)
slow = AnswerStore(lambda: SlowSB({}))
t = time.time()
check("L2 slow Supabase: read gives up in ~2 s", slow.get("some slow question") is None and time.time() - t < 3.5, f"{time.time()-t:.1f}s")

# end to end: restart simulation (L1 empty, L2 populated)
reset()
store2 = {}
qs.answer_l2 = AnswerStore(lambda: FakeSB(store2))
qs.groq_client = FakeGroq(lambda m, s: good_stream("Persistent answer.") if s else good_completion("Persistent answer."))
stream_q(Q_DOC)
qs.answer_l2.flush()
check("answer written to L2 after generation", len(store2) == 1)
qs.answer_cache.clear()      # "Render restarted"
qs.groq_client = FakeGroq(lambda m, s: (_ for _ in ()).throw(status_err(429)))
r, evs = stream_q(Q_DOC)
check("after restart: served from L2 with LLM down", "Persistent answer" in text_of(evs) and [d for e, d in evs if e == "meta"][0].get("cached"), text_of(evs)[:100])
check("L2 hit is promoted to L1", qs.answer_cache.stats()["items"] == 1)


# ---------------------------------------------------------------- 8. warm-up: top 30, skips cached, stops on LLM outage
reset()
os.environ.pop("WARMUP_DISABLED", None)
qs.answer_l2 = AnswerStore(lambda: FakeSB({}))
qs.analytics.summary = lambda days: {"top_questions": [(f"what is the {w} policy", 5) for w in ["cell phone", "dress code", "lunch", "bus", "late", "grading"] * 6][:40]}
qs.answer_cache.set((qs._norm(qs.STARTERS[0]), "x", "y"), {}, ttl=1)
asked = []
qs.answer_once = lambda q: (asked.append(q), {"cached": False, "degraded": True})[1]
qs.time.sleep = lambda *_: None
qs._warm()
check("warm-up stops after the first degraded (LLM down) answer", len(asked) == 1, str(asked))
asked.clear()
qs.answer_once = lambda q: (asked.append(q), {"cached": True})[1]
qs._warm()
check("warm-up targets at most 30 questions (starters + top asked)", 4 <= len(asked) <= 30, str(len(asked)))
os.environ["WARMUP_DISABLED"] = "1"

qs.groq_client = orig_groq
fails = [n for n, ok in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} drills pass")
sys.exit(1 if fails else 0)
