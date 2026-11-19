"""Lunch line answer (lunchline.py): question matching, wording, caching and every failure path. No network: the monitor is faked."""
import time
import lunchline as L
import query_server as qs

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    fails += 0 if ok else 1

YES = ["How many people are in the lunch line?", "how many ppl in the lunch line", "is the lunch line long right now", "How long is the cafeteria line?",
       "lunch line?", "how busy is the line for lunch", "what's the line like at lunch", "How crowded is the cafe line"]
NO = ["What's for lunch today?", "What's the lunch menu?", "Is there school online?", "When is lunch?", "What clubs meet at lunch?", "Which line do I stand in for graduation?",
      "How many people are on the basketball team?", "What is the deadline for course requests?"]
check("matches lunch line questions", all(L.is_lunch_line_question(q) for q in YES), str([q for q in YES if not L.is_lunch_line_question(q)]))
check("ignores other questions", not any(L.is_lunch_line_question(q) for q in NO), str([q for q in NO if L.is_lunch_line_question(q)]))

calls = []
def fake(count, age=2):
    def f():
        calls.append(1)
        return {"count": count, "age": age}
    return f

def run(count, age=2, q="How many people are in the lunch line?"):
    L._cache.update(t=0.0, data=None)
    L._fetch = fake(count, age)
    return L.answer(q)

a = run(7); check("reports the count with a link and a caveat", "**7 people**" in a and "nuevasocratica.com/lunch-line-monitor" in a and "estimate" in a, a)
check("singular", "**1 person**" in run(1))
check("zero reads naturally", "nobody is in the lunch line" in run(0))
check("a stale reading is not reported as current", "7" not in (s := run(7, age=600)) and "hasn't updated" in s and "monitor" in s, s)

L._cache.update(t=0.0, data=None)
def boom(): raise OSError("down")
L._fetch = boom
s = L.answer("how many people are in the lunch line")
check("an unreachable monitor falls back to the link, no number", "couldn't reach" in s and "nuevasocratica.com/lunch-line-monitor" in s and not any(c.isdigit() for c in s.replace("nuevasocratica", "")), s)

import urllib.request as ur
orig = ur.urlopen
def serve(payload):
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, n): return payload
    return lambda *a, **k: R()
import importlib; importlib.reload(L)
for payload, ok in [(b'{"count": 12, "count_age_seconds": 3}', True), (b'{"count": -1}', False), (b'{"count": "9"}', False), (b'{"count": true}', False), (b'{"count": 99999}', False), (b'not json', False), (b'{}', False)]:
    L._cache.update(t=0.0, data=None)
    ur.urlopen = serve(payload)
    got = L.reading()
    check(f"parser {payload[:30]!r}", (got is not None) == ok, str(got))
ur.urlopen = orig

L._cache.update(t=0.0, data=None); calls.clear(); L._fetch = fake(5)
L.answer("lunch line?"); L.answer("lunch line?"); L.answer("lunch line?")
check("one fetch serves repeated questions within the cache window", len(calls) == 1, str(calls))
L._cache["t"] = time.time() - L.TTL_S - 1; L.answer("lunch line?")
check("a new fetch after the window", len(calls) == 2, str(calls))

L._cache.update(t=0.0, data=None); L._fetch = fake(4)
r = qs.quick_reply("how many people are in the lunch line")
check("the server answers it as a quick reply, no model", r is not None and r[1] == "lunchline" and "**4 people**" in r[0], str(r))
check("lunch menu questions are untouched by it", qs.quick_reply("What's for lunch today?") is None)
print(f"{fails} failure(s)")
raise SystemExit(1 if fails else 0)
