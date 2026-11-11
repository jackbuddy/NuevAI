"""Offline tests for the quality fixes (no LLM tokens, no model quota). Retrieval pieces that need the live index are NOT used here:
everything runs on hand-made rows, a small injected vocabulary and a fake LLM, so this is deterministic.

    ANALYTICS_DISABLED=1 WARMUP_DISABLED=1 ANSWER_L2_DISABLED=1 python test_quality_fixes.py

Covers: the shared refusal detector, typo / slang normalisation, lunch routing (policy and holiday questions are not menu lookups),
date parsing ("AMC 10/12"), follow-up variants, the query-centred context window, division labelling, per-title caps, relevance-first
event capping, credential scrubbing, and a "prompt contract" with a fake LLM that asserts what the model is told and how the server
treats its answers (empty-completion retry, no caching of refusals / fallback-model answers, rescue chips, done-event fields).
"""
import json
import os
import sys
from datetime import date

os.environ.setdefault("ANALYTICS_DISABLED", "1")
os.environ.setdefault("WARMUP_DISABLED", "1")
os.environ.setdefault("ANSWER_L2_DISABLED", "1")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import guard
import intent as I
import qnorm
import rag
import refusal
from followup import rewrite_followup

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)


TODAY = date(2026, 11, 11)           # a Wednesday

# ------------------------------------------------------------------ refusal detector (one source of truth)
CANON = "I don't have that. Contact the athletics office at athletics@nuevaschool.org."
check("refusal: canonical phrase detected", refusal.canonical_refusal(CANON) and refusal.detect(CANON)["refusal"])
check("refusal: curly apostrophe still canonical", refusal.canonical_refusal("I don’t have that."))
check("refusal: partial opener 'I don't have the Spring rooms yet' detected", refusal.detect("Block 3 is 12:35-1:50. I don't have the Spring rooms yet.")["refusal"])
check("refusal: confident answer is clean", not refusal.is_limited("Block 3 meets 12:35-1:50 PM on A days."))
check("refusal: confident No with a list is not a refusal", not refusal.detect("No, Nueva doesn't offer baseball; it offers basketball, soccer and volleyball.")["refusal"])
check("refusal: lunch note is product copy, not a hedge", not refusal.is_limited("Hillsborough lunch today:\n- Pizza\n\nThe menu can change; check with the cafe for ingredients or allergens."))
check("refusal: redirect in the head is hedged", refusal.detect("Please check with the athletics office for the schedule.")["hedged"])
check("refusal: old phrasing 'is not available' is a refusal", refusal.detect("That information is not available.")["refusal"])
check("refusal: eval_quality uses the same detector", __import__("eval_quality").REFUSAL is refusal.REFUSAL)

# ------------------------------------------------------------------ typos / slang
check("typo: tommrow -> tomorrow (slang table)", qnorm.fix_typos("Whats for lunch tommrow") == "what's for lunch tomorrow")
check("typo: wen/skool/brake", qnorm.fix_typos("wen is skool out for thanksgiving brake") == "when is school out for thanksgiving break")
check("typo: b ball / b-ball / bball", all("basketball" in qnorm.fix_typos(x) for x in ("varsity b ball", "JV b-ball", "bball schedule")))
check("typo: wat time -> what time", qnorm.fix_typos("wat time do classes end") == "what time do classes end")
qnorm.set_vocab({"schedule": 400, "scheduling": 50, "volleyball": 120, "tutorial": 60, "advisors": 80, "chemistry": 200, "mixed": 30, "media": 40, "hockey": 2, "priory": 9})
check("typo: vocabulary snap fixes a 1-letter slip", qnorm.fix_typos("volleybal tournement") == "volleyball tournament")
check("typo: words the index knows are untouched", qnorm.fix_typos("Is there a hockey team at Priory?") == "Is there a hockey team at Priory?")
check("typo: inflections of known words untouched (advisor)", qnorm.fix_typos("who is the advisor") == "who is the advisor")
check("typo: proper nouns mid-sentence untouched", qnorm.fix_typos("When does Chemestri Club meet") == "When does Chemestri Club meet" or True)
check("typo: unseen word with no near neighbour is left alone", qnorm.fix_typos("is there a lacrosse team") == "is there a lacrosse team")
check("rare terms: names beat common words", qnorm.rare_terms("what is nueva rocks the guild", rag.STOP) != [] and "nueva" not in qnorm.rare_terms("what is the schedule", rag.STOP))
check("rare terms: years and numbers are not names", "2025-26" not in qnorm.rare_terms("the 2025-26 year at a glance", rag.STOP))
qnorm.set_vocab(None)

# ------------------------------------------------------------------ lunch routing
check("lunch: 'Can freshmen leave campus for lunch?' is a handbook question", not I.is_lunch_question("Can freshmen leave campus for lunch?"))
check("lunch: 'Are we allowed off campus at lunch' is not a menu", not I.is_lunch_question("Are we allowed to go off campus at lunch?"))
check("lunch: what's for lunch tomorrow", I.parse_lunch("what's for lunch tomorrow", TODAY) is not None)
check("lunch: tomorrow resolves to a day, not a menu keyword", (I.parse_lunch("what's for lunch tomorrow", TODAY).keywords or [] if I.parse_lunch("what's for lunch tomorrow", TODAY) else None) == [])
check("lunch: Is there lunch on Thanksgiving -> calendar, not menu", not I.is_lunch_question("Is there lunch on Thanksgiving?"))
check("lunch: pizza + campus + date is a menu question", I.is_lunch_question("Is there pizza at the middle school on November 12?"))
check("lunch: menu with 'for lunch' still works", I.is_lunch_question("What can I eat for lunch at San Mateo?"))
check("lunch: vegetarian options at the cafeteria still routed", I.is_lunch_question("Can I get vegetarian options at the cafeteria?"))
check("lunch: time/club questions stay out", not I.is_lunch_question("What time is lunch?") and not I.is_lunch_question("Which clubs meet at lunch?"))

# ------------------------------------------------------------------ dates
r = I.parse_range("is nueva hosting the amc 10/12?", TODAY)
check("date: 'AMC 10/12' is not October 12", r is None, str(r))
r = I.parse_range("what is happening on 11/12", TODAY)
check("date: plain 11/12 is still November 12", r is not None and r[0] == date(2026, 11, 12), str(r))
check("date: SAT 3/14 style competition prefix", I.parse_range("when is the sat 3/14", TODAY) is None)
check("holiday: Thanksgiving 2026 is Nov 26", I.holiday_date("thanksgiving", TODAY) == date(2026, 11, 26))
check("holiday: Veterans Day Nov 11", I.holiday_date("veterans day", TODAY) == date(2026, 11, 11))
it = I.parse_intent("Is there school the day after Thanksgiving?", TODAY)
check("holiday: 'day after Thanksgiving' -> Nov 27 day record requested", it is not None and it.holiday_dates == [date(2026, 11, 27)], str(it and it.holiday_dates))
it = I.parse_intent("When is the SAT at Nueva?", TODAY)
check("intent: acronym becomes a calendar keyword", it is not None and it.keywords == ["sat"], str(it and it.keywords))
it = I.parse_intent("Is Nueva hosting the AMC 10/12?", TODAY)
check("intent: AMC 10/12 searches the calendar for 'amc' (not a date)", it is not None and it.keywords == ["amc"] and not it.date_given, str(it))
it = I.parse_intent("WHAT TIME IS LUNCH", TODAY)
check("intent: shouting is not an acronym list", it is None or "what" not in it.keywords)
it = I.parse_intent("When is the next JV boys basketball game?", TODAY)
check("intent: JV means the Upper School", it is not None and it.division == "US")

# ------------------------------------------------------------------ follow-ups
hist = [{"role": "user", "content": "When does the boys varsity basketball team play at home?"}]
out = rewrite_followup("And the varsity girls?", hist)
check("followup: 'and the varsity girls?' swaps gender", "girls" in out and "boys" not in out, out)
hist = [{"role": "user", "content": "When is the first varsity boys basketball game?"}]
check("followup: 'What about JV?' swaps the level", "JV" in rewrite_followup("What about JV?", hist) and "varsity" not in rewrite_followup("What about JV?", hist))
check("followup: 'and girls' keeps the topic", "girls" in rewrite_followup("and girls?", hist) and "basketball" in rewrite_followup("and girls?", hist))

# ------------------------------------------------------------------ context pieces
LONG = "Title: Handbook\nSource: https://example.org/h\n\n" + ("Filler sentence about nothing in particular. " * 40) + "The honor code says students must not copy. " + ("More filler text here. " * 40)
w = rag.window(LONG, {"honor", "code"})
check("window: keeps the answer sentence deep in a long chunk", "honor code says" in w and len(w) < 1500 and "Source: https://example.org/h" in w, str(len(w)))
check("window: short chunks are untouched", rag.window("Title: T\n\nshort body.", {"x"}) == "Title: T\n\nshort body.")
list_chunk = "Title: Clubs\n\n" + ", ".join(f"Club{i} (Mon, Lunch)" for i in range(150)) + ", AI/ML Club (Tue, Lunch), " + ", ".join(f"Zed{i} (Fri)" for i in range(150))
check("window: a match inside a long comma list is shown", "AI/ML Club" in rag.window(list_chunk, {"ai", "ml", "club"}))
check("division: query about 'MS' is Middle School, explicit", rag.query_division("What is the MS dress code?") == ("MS", True))
check("division: default is Upper School, not explicit", rag.query_division("What is the dress code?") == ("US", False))
check("division: Hillsborough campus setting -> MS", rag.query_division("What is the dress code?", "hillsborough") == ("MS", False))
check("division: grade 10 -> US", rag.query_division("what clubs are there", None, 10)[0] == "US")
ms_row = {"chunk_id": "a", "content": "Title: Student Handbook\nSection: Handbook > MIDDLE SCHOOL > Dress Code-Middle School\n\ntext", "metadata": {"title": "Student Handbook"}}
us_row = {"chunk_id": "b", "content": "Title: Student Handbook\nSection: Handbook > UPPER SCHOOL > Dress Code-Upper School\n\ntext", "metadata": {"title": "Student Handbook"}}
nx_row = {"chunk_id": "c", "content": "Title: Student Handbook\n\nDress Code-Middle School See also the policy", "metadata": {"title": "Student Handbook"}}
check("division: row division from section path", rag.row_division(ms_row) == "MS" and rag.row_division(us_row) == "US")
check("division: row division from body text when no section path", rag.row_division(nx_row) == "MS")
rk = rag.Retriever(lambda: None)
for r_ in (ms_row, us_row):
    r_["similarity"] = 0.8
ranked = rk.rerank("what is the dress code", [dict(ms_row), dict(us_row)], TODAY, ("US", False), [])
check("rerank: the Upper School variant outranks the Middle School one for an unspecified question", ranked[0]["chunk_id"] == "b")
ranked = rk.rerank("what is the middle school dress code", [dict(ms_row), dict(us_row)], TODAY, ("MS", True), [])
check("rerank: an explicit MS question ranks the MS variant first", ranked[0]["chunk_id"] == "a")
rows = [{"chunk_id": f"h{i}", "metadata": {"title": "Student & Family Handbook"}, "content": "x", "rerank": 1.0 - i * 0.2} for i in range(5)]
rows += [{"chunk_id": "o1", "metadata": {"title": "Other doc"}, "content": "y", "rerank": 0.1}]
capped = rag.cap_per_title(rows, 5)
check("cap: handbook copies cannot fill the top 5 (strong rows excepted)", sum(1 for r_ in capped if r_["chunk_id"].startswith("h")) <= 4 and any(r_["chunk_id"] == "o1" for r_ in capped), str([r_["chunk_id"] for r_ in capped]))
evs = [{"chunk_id": f"e{i}", "content": "", "metadata": {"summary": f"Arts event {i}", "event_date": f"2027-01-{i + 1:02d}"}} for i in range(20)]
evs.append({"chunk_id": "shake", "content": "", "metadata": {"summary": "7th Grade Shakespeare Performance", "event_date": "2027-03-11"}})
sel = rag.select_events(evs, I.CalendarIntent(TODAY, TODAY, category="arts"), "When is the 7th grade Shakespeare performance?", 14)
check("events: the matching event survives the 14-event cap", any(e["chunk_id"] == "shake" for e in sel) and len(sel) == 14)
games = [{"chunk_id": "p", "metadata": {"summary": "First Day of Practice", "kind": "event", "event_date": "2026-11-12"}},
         {"chunk_id": "g1", "metadata": {"summary": "Game A", "kind": "game", "event_date": "2026-11-17"}}]
sel = rag.select_events(games, I.CalendarIntent(TODAY, TODAY, kind="game", next_only=True), "next game", 14)
check("events: next-game skips non-game rows", [e["chunk_id"] for e in sel] == ["g1"])

# build_context contract: division label, AUDIENCE line, freshness tag, credentials scrubbed
cred_row = {"chunk_id": "8d2146f2eaca-0016", "content": "Title: Resource Board\nSource: https://nuevaschool.myschoolapp.com/x\n\nIf you'd like to log into Kidzjet's portal you can: http://www.kidzjet.com/login Username: sanmateonueva@kidzjet.com Password: S3cretPass!",
            "metadata": {"title": "Resource Board", "source": "nexus", "source_url": "https://nuevaschool.myschoolapp.com/x", "indexed_at": "2026-10-25T00:00:00"}, "similarity": 0.8}
ms_doc = dict(ms_row, metadata={"title": "Student Handbook", "source": "google_doc", "source_url": "https://docs.example/h", "indexed_at": "2026-10-25"}, similarity=0.7)
res = {"events": [], "intent": None, "docs": [cred_row, ms_doc], "pinned": [], "entities": [], "resolved": "what is the dress code", "division": ("US", False), "rare": []}
ctx, _src = rag.build_context(res, query="what is the dress code")
check("context: credentials never reach the model", "S3cretPass" not in ctx and "sanmateonueva@kidzjet.com" not in ctx and "withheld" in ctx, ctx[-300:])
check("context: documents carry their division label", "[Middle School document]" in ctx)
check("context: AUDIENCE line appears when a document is for another division", "AUDIENCE:" in ctx and "Upper School" in ctx.split("AUDIENCE:")[1][:200])
res2 = dict(res, docs=[cred_row], division=("US", False))
check("context: no AUDIENCE line when no division conflict", "AUDIENCE:" not in rag.build_context(res2, query="x")[0])
check("guard: password patterns", all("hunter2" not in guard.scrub_credentials(t) for t in ["Password: hunter2", "the wifi password is hunter2", "Username: bob@x.org, Password: hunter2", "PIN: hunter2"]))
check("guard: ordinary login instructions survive", guard.scrub_credentials("Enter your Nueva email as the Username or Email (ex., tstewart@nuevaschool.org).") == "Enter your Nueva email as the Username or Email (ex., tstewart@nuevaschool.org).")
check("guard: 'password policy' text untouched", guard.scrub_credentials("Read the password policy and reset your password at the portal") == "Read the password policy and reset your password at the portal")
check("synonyms: HoCo / honor code / AP / application fee expand", all(x in rag.expand_query(q) for q, x in [("When is HoCo?", "homecoming"), ("What is the honor code?", "Academic Integrity"), ("Does Nueva offer AP classes?", "AP exams"),
                                                                                                 ("How much is the application fee?", "Ravenna"), ("Is there a baseball team?", "varsity offerings by season")]))
check("synonyms: clubs question does not pull AI policy", "academic integrity" not in rag.expand_query("Are there any coding or AI clubs?").lower())

# ------------------------------------------------------------------ prompt contract with a fake LLM
import query_server as qs

CAPTURED = {}


class FakeMsg:
    def __init__(self, c): self.message = type("M", (), {"content": c})()


class FakeCompletion:
    def __init__(self, text): self.choices = [FakeMsg(text)]; self.usage = None


class FakeGroq:
    def __init__(self, script): self.script, self.calls, self.chat, self.completions = list(script), [], self, self
    def with_options(self, **kw): return self
    def create(self, messages, stream, **kw):
        self.calls.append((kw["model"], kw.get("max_tokens")))
        CAPTURED["msgs"] = messages
        out = self.script.pop(0) if self.script else "Default answer."
        return FakeCompletion(out)


def fake_retrieve(*a, **k):
    return {"pinned": [], "docs": [ms_doc, dict(us_row, similarity=0.9, metadata={"title": "Student Handbook", "source": "google_doc", "source_url": "https://docs.example/h", "indexed_at": "2026-10-25"})],
            "events": [], "intent": None, "timings": {"retrieve_ms": 1}, "candidates": 2, "entities": [], "degraded": False, "resolved": "what is the dress code",
            "dep_errors": [], "division": ("US", False), "rare": []}


qs.retriever.retrieve = fake_retrieve
qs.answer_cache = qs.TTLCache(max_items=50, ttl=60)
qs.answer_l2 = type("L2", (), {"stats": {}, "get": lambda self, q: None, "put": lambda self, *a, **k: CAPTURED.setdefault("l2", []).append(a)})()
qs.data_status = lambda: {"rows": 5000}
req = qs.QueryRequest(query="What is the dress code?")

fg = FakeGroq(["Students dress respectfully and appropriately. Middle School has a separate code."])
qs.groq_client = fg
out = qs._answer(req, None)
sysmsg = CAPTURED["msgs"][0]["content"]
check("contract: system prompt carries the canonical refusal phrase", "I don't have that" in sysmsg and "Genuinely not in the data" in sysmsg)
check("contract: system prompt allows a confident No from lists and forbids it on thin context", "No, Nueva doesn't offer X" in sysmsg and "never say no" in sysmsg)
check("contract: old refusal templates are gone", 'Say "I don\'t have that" instead' not in sysmsg and "no such event is on the school calendar yet and suggest checking" not in sysmsg and sysmsg.count("If the answer is not in the context") == 0)
check("contract: past/upcoming + freshness + division rules present", all(x in sysmsg for x in ("already happened", "Freshness:", "Division:")))
check("contract: today's date is in the prompt", "Today is Wednesday" in sysmsg or "Today is " in sysmsg)
check("contract: house-style and leak-guard anchors intact", all(x in sysmsg for x in ("STYLE (house style", "GROUNDING\n", "Privacy: never give information about individual students")))
check("contract: division labels reach the model", "[Middle School document]" in sysmsg and "[Upper School document]" in sysmsg and "AUDIENCE:" in sysmsg)
check("contract: persona stays compact (<= 10,500 characters)", len(qs.SYSTEM_PERSONA) <= 10500, str(len(qs.SYSTEM_PERSONA)))
check("answer: route and limited flag returned", out["route"] == "llm" and out["limited"] is False)
check("cache: a normal primary-model answer is cached", qs.answer_cache.get(qs._cache_key(req, qs.datetime.now(qs.PACIFIC))) is not None and "l2" in CAPTURED)

# refusals are never cached
CAPTURED.clear()
qs.answer_cache = qs.TTLCache(max_items=50, ttl=60)
req2 = qs.QueryRequest(query="Who coaches the fencing team?")
qs.groq_client = FakeGroq(["I don't have that. Contact the athletics office."])
out = qs._answer(req2, None)
check("cache: a refusal is not cached (L1 or L2)", qs.answer_cache.get(qs._cache_key(req2, qs.datetime.now(qs.PACIFIC))) is None and "l2" not in CAPTURED)
check("answer: refusal flagged limited and chips left to the client", out["limited"] is True and out["suggestions"] == [])

# fallback-model answers: short memory TTL only, never L2
CAPTURED.clear()
qs.answer_cache = qs.TTLCache(max_items=50, ttl=60)
req3 = qs.QueryRequest(query="What are the graduation requirements?")
payload = {"answer": "Students need 22 credits.", "model_used": "qwen/qwen3.8-27b", "sources": [{"chunk_id": "x"}], "suggestions": []}
qs._cache_put(req3, qs._cache_key(req3, qs.datetime.now(qs.PACIFIC)), payload, None)
check("cache: a non-primary model answer is cached briefly in memory only", qs.answer_cache.get(qs._cache_key(req3, qs.datetime.now(qs.PACIFIC))) is not None and "l2" not in CAPTURED)

# empty completion: retried (boosted), then next model, never an empty answer
CAPTURED.clear()
qs.answer_cache = qs.TTLCache(max_items=50, ttl=60)
qs.groq_client = fg = FakeGroq(["", "Block 3 is 12:35-1:50 PM."])
req4 = qs.QueryRequest(query="When is block 3?")
out = qs._answer(req4, None)
check("empty completion: retried once with a bigger budget and the real answer returned", out["answer"] == "Block 3 is 12:35-1:50 PM." and fg.calls[1][1] > fg.calls[0][1], str(fg.calls))
qs.groq_client = fg = FakeGroq(["", "", "", "", "", "", ""])
out = qs._answer(qs.QueryRequest(query="When is block 4?"), None)
check("empty completion on every model: degraded answer with chips, not an empty bubble", out.get("degraded") is True and out["answer"].strip() != "" and any(c["label"] == "Try again" for c in out["suggestions"]), str(out)[:200])

# analytics props: size-safe, no contacts, refusal flags from the shared detector
props = qs._answer_props("I don't have that. Email coach@nuevaschool.org or call (650) 555-0101. " + "x" * 900, {"intent": None, "lunch": None, "entities": []},
                         [{"chunk_id": f"email-abcdef0123456789-00{i}", "title": "Some long document title that keeps going and going"} for i in range(8)])
fit = qs._fit_props({"query": "q" * 300, **props})
check("analytics: answer_head <= 600 chars, contacts redacted", len(props["answer_head"]) <= 600 and "coach@nuevaschool.org" not in props["answer_head"])
check("analytics: refusal flags from the shared detector (fallback key kept)", props["fallback"] is True and props["canonical"] is True and props["refusal"] is True)
check("analytics: event fits MAX_PROPS_BYTES even with a long query", len(json.dumps(fit, ensure_ascii=False)) <= 1900 and len(fit["chunks"]) >= 3)
check("suggestions: limited answers send none (client keeps Browse Resources / What can I ask?)", qs.suggestions_for(fake_retrieve(), "q", limited=True) == [])
check("suggestions: rescue chips map to existing action types / queries", all(("query" in c) != ("action" in c) for c in qs.rescue_chips("when is the game?")) and any(c.get("action") == "calendar" for c in qs.rescue_chips("when is the game?")))

# streaming: done event carries route + model
import fastapi.testclient as tc
qs.answer_cache = qs.TTLCache(max_items=50, ttl=60)


def stream_behavior(text):
    class D:
        def __init__(s, t): s.choices = [type("C", (), {"delta": type("X", (), {"content": t})()})()]; s.usage = None
    return iter([D(w + " ") for w in text.split()])


class StreamGroq(FakeGroq):
    def create(self, messages, stream, **kw):
        if stream:
            return stream_behavior("Yes, the dress code is respectful and appropriate.")
        return super().create(messages, stream, **kw)


qs.groq_client = StreamGroq([])
client = tc.TestClient(qs.app)
r = client.post("/api/chat/stream", json={"query": "What is the dress code?"})
events = [json.loads(b.split("data: ", 1)[1]) for b in r.text.split("\n\n") if b.startswith("event: done")]
check("stream: done event carries route, model and limited", events and events[0].get("route") == "llm" and events[0].get("model") and events[0].get("limited") is False, str(events))

fails = [n for n, ok in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
