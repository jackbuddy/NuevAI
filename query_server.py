import os
import re
import json
import time
import hmac
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Threading overrides must be set before the heavy imports. Locally (Apple Silicon) one thread avoids
# OpenMP locks; on the Linux server use every core, which makes embedding several times faster.
os.environ["TOKENIZERS_PARALLELISM"] = "false"
_threads = os.getenv("TORCH_THREADS") or ("1" if os.uname().sysname == "Darwin" else str(os.cpu_count() or 1))
os.environ["OMP_NUM_THREADS"] = _threads
os.environ["MKL_NUM_THREADS"] = _threads

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from groq import Groq

from analytics import Analytics
from cache import TTLCache
from rag import Retriever, build_context

load_dotenv()

PACIFIC = ZoneInfo("America/Los_Angeles")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
ANSWER_TTL = 6 * 3600
DATE_ANSWER_TTL = 600           # answers that depend on the calendar go stale quickly

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY)

_supabase_client = None


def get_supabase():
    global _supabase_client
    if _supabase_client is None:
        from supabase import create_client
        _supabase_client = create_client(os.getenv("SUPABASE_URL", "").strip().strip("\"'"),
                                         os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip().strip("\"'"))
    return _supabase_client


retriever = Retriever(get_supabase)
analytics = Analytics(get_supabase)
answer_cache = TTLCache(max_items=800, ttl=ANSWER_TTL)
api_cache = TTLCache(max_items=128, ttl=600)

STARTERS = [
    "What sports are playing this season?",
    "What engineering courses are offered?",
    "What is the cell phone policy?",
    "When is the next school break?",
]


def _warm():
    """Load models, then pre-compute the starter answers so the first click is instant."""
    try:
        retriever.models.embed_model()
        retriever.models.rerank_model()
        time.sleep(2)
        for q in STARTERS:
            try:
                answer_once(q)
            except Exception as e:
                print(f"starter warm failed for {q!r}: {e}", flush=True)
        print("Warm-up complete.", flush=True)
    except Exception as e:
        print(f"warm-up failed: {e}", flush=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_warm, daemon=True).start()
    yield


app = FastAPI(title="NuevAI RAG Service", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------ health
@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": retriever.models.ready["embedding"], **retriever.models.ready,
            "answer_cache": answer_cache.stats()}


def _key_kind(key: str) -> str:
    if not key:
        return "missing"
    if key.startswith("sb_secret_"):
        return "secret (server)"
    if key.startswith("sb_publishable_"):
        return "publishable (public, RLS applies)"
    if key.startswith("eyJ"):
        import base64
        try:
            payload = key.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return "jwt:" + str(json.loads(base64.urlsafe_b64decode(payload)).get("role"))
        except Exception:
            return "jwt:unreadable"
    return "unknown format"


@app.get("/health/deep")
def health_deep():
    key, url = os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""), os.getenv("SUPABASE_URL", "")
    info = {"supabase_host": url.replace("https://", "").split(".")[0][:4] + "…", "key_kind": _key_kind(key)}
    try:
        res = get_supabase().table("document_chunks").select("chunk_id", count="exact").limit(1).execute()
        info["chunk_rows_visible"] = res.count
    except Exception as e:
        info["error"] = str(e)[:200]
    return info


# ------------------------------------------------------------------ chat
class Turn(BaseModel):
    role: str
    content: str = Field(max_length=2000)


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = 6
    model: str | None = None
    history: list[Turn] = Field(default_factory=list, max_length=8)
    anon_id: str = ""
    session_id: str = ""


SYSTEM_PERSONA = """You are NuevAI, a high-utility AI assistant for the Nueva School community. You help students, families, and staff with courses, the school calendar and sports schedules, the handbook, IT help, and campus life.

STYLE
- Direct and plain. Start with the answer in the first sentence. No greetings, pleasantries, or sign-offs.
- Simple questions: under about 80 words. Complex ones: one short lead-in sentence, then simple "-" bullets.
- No markdown headers and no bold text.
- When the user asks how or where to do something that has a URL in the context (book a tour, register, sign up, pay, fill out a form, open a document), give the link as a markdown link [short label](url). Copy URLs exactly, character for character, from the context; never shorten, guess or invent one.
- Do not print raw document names, chunk ids, or the words "context" or "documentation".

GROUNDING
- Answer from the context below. The SCHOOL CALENDAR block, when present, is authoritative for dates, times, places, opponents and Home/Away: list its entries faithfully and in order, with exact dates. Never present a past event as upcoming.
- "What sports / teams" questions: combine the TEAMS list from the calendar with any sports named in the other context, grouped by sport, and mention which have games on the calendar. Say that Upper School game schedules may not be on the calendar yet if none are listed.
- For "next game / what's next" questions give the first matching entry (and the one after it if helpful).
- If the calendar block says nothing matched, say that no such event is on the school calendar yet and suggest checking with the relevant office; do not guess.
- Course questions: use the course entries and "Contents of ..." lists in the context and name every relevant course found.
- If the answer is not in the context: say what is missing in one sentence and point to the right office or portal. Do not guess.
- Greetings or "what can you do": introduce yourself briefly as NuevAI and list what you can help with."""


def _norm(q: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", q.lower())).strip()


def _prepare(req: QueryRequest):
    """Retrieval + prompt assembly. Returns everything the LLM step and the logs need."""
    now = datetime.now(PACIFIC)
    result = retriever.retrieve(req.query, [t.model_dump() for t in req.history], top_k=min(max(req.top_k, 3), 8), today=now.date())
    context, sources = build_context(result)
    if not context.strip():
        context = "No relevant documentation was found for this question."
    system = (f"{SYSTEM_PERSONA}\n\nToday is {now.strftime('%A, %B %-d, %Y')} (Pacific time).\n\n"
              f"CONTEXT:\n{context}")
    msgs = [{"role": "system", "content": system}]
    msgs += [{"role": t.role if t.role in ("user", "assistant") else "user", "content": t.content} for t in req.history[-6:]]
    msgs.append({"role": "user", "content": req.query})
    return msgs, sources, result, now


FALLBACK_MODELS = [m.strip() for m in os.getenv("LLM_FALLBACKS", "llama-3.3-70b-versatile,llama-3.1-8b-instant").split(",") if m.strip()]


def _create(msgs, model: str, stream: bool):
    """Groq call with automatic fallback when a model is rate limited (each model has its own token budget)."""
    last = None
    for m in [model] + [x for x in FALLBACK_MODELS if x != model]:
        try:
            return groq_client.chat.completions.create(messages=msgs, stream=stream, **_llm_kwargs(m)), m
        except Exception as e:
            last = e
            text = str(e).lower()
            if "429" in text or "rate_limit" in text or "rate limit" in text or "over capacity" in text or "503" in text:
                print(f"LLM {m} unavailable ({text[:80]}); trying next model", flush=True)
                continue
            raise
    raise last


def _llm_kwargs(model: str) -> dict:
    kw = dict(model=model, temperature=0.2, max_tokens=800)
    if model.startswith("openai/gpt-oss"):
        kw["extra_body"] = {"reasoning_effort": "low"}     # reasoning at "medium" adds seconds for little gain here
    return kw


def _track(req: QueryRequest, request: Request | None, props: dict):
    if req.anon_id:
        analytics.record("chat_answered", req.anon_id, req.session_id, props,
                         path="/chat", ua=(request.headers.get("user-agent", "") if request else ""))


def _cache_key(req: QueryRequest, now: datetime):
    if req.history:
        return None                    # follow-ups depend on the conversation; never cached
    return (_norm(req.query), now.strftime("%Y-%m-%d"), req.model or LLM_MODEL)


def answer_once(query: str) -> dict:
    """Blocking, uncached-or-cached answer used by /api/chat and the warm-up."""
    req = QueryRequest(query=query)
    return _answer(req, None)


def _answer(req: QueryRequest, request: Request | None) -> dict:
    t0 = time.time()
    now = datetime.now(PACIFIC)
    key = _cache_key(req, now)
    if key and (hit := answer_cache.get(key)):
        total = round((time.time() - t0) * 1000)
        _track(req, request, {"query": req.query[:300], "cached": True, "total_ms": total, "ttft_ms": total, "sources": len(hit["sources"])})
        return {**hit, "cached": True, "timings": {"total_ms": total}}
    msgs, sources, result, now = _prepare(req)
    model = req.model or LLM_MODEL
    t_llm = time.time()
    completion, model = _create(msgs, model, stream=False)
    answer = completion.choices[0].message.content or ""
    timings = {**result["timings"], "llm_ms": round((time.time() - t_llm) * 1000), "total_ms": round((time.time() - t0) * 1000)}
    payload = {"answer": answer, "model_used": model, "sources": sources}
    if key:
        intent = result["intent"]
        answer_cache.set(key, payload, ttl=DATE_ANSWER_TTL if intent else ANSWER_TTL)
    _track(req, request, {"query": req.query[:300], "cached": False, "sources": len(sources), "no_context": not sources,
                          "fallback": bool(re.search(r"(isn't|is not|not) (in|available)|not on the school calendar", answer, re.I)),
                          "ttft_ms": timings["total_ms"], **timings})
    return {**payload, "cached": False, "timings": timings}


@app.post("/api/chat")
def chat_endpoint(req: QueryRequest, request: Request):
    _rate_limit(req, request)
    try:
        return _answer(req, request)
    except Exception as e:
        if req.anon_id:
            analytics.record("chat_error", req.anon_id, req.session_id, {"error": str(e)[:200], "query": req.query[:200]})
        raise HTTPException(status_code=500, detail=str(e))


_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()
PER_USER_PER_MIN = int(os.getenv("RATE_PER_USER_PER_MIN", "20"))
GLOBAL_PER_MIN = int(os.getenv("RATE_GLOBAL_PER_MIN", "150"))


def _rate_limit(req: "QueryRequest", request: Request):
    """Protects the LLM budget: per-visitor and overall caps over a sliding 60 seconds."""
    now = time.time()
    who = req.anon_id or (request.client.host if request.client else "unknown")
    with _hits_lock:
        for key, limit in ((who, PER_USER_PER_MIN), ("*", GLOBAL_PER_MIN)):
            window = [t for t in _hits.get(key, []) if now - t < 60]
            if len(window) >= limit:
                _hits[key] = window
                raise HTTPException(429, "You're asking very quickly - please wait a few seconds and try again.")
            window.append(now)
            _hits[key] = window
        if len(_hits) > 5000:
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > 60]:
                del _hits[k]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
def chat_stream(req: QueryRequest, request: Request):
    """Server-sent events: meta (sources) -> delta* (text) -> done (timings). Cached answers replay instantly."""
    _rate_limit(req, request)
    def gen():
        t0 = time.time()
        try:
            now = datetime.now(PACIFIC)
            key = _cache_key(req, now)
            hit = answer_cache.get(key) if key else None
            if hit:
                yield _sse("meta", {"sources": hit["sources"], "cached": True})
                text = hit["answer"]
                for i in range(0, len(text), 60):          # replay quickly so the UI looks the same as a live stream
                    yield _sse("delta", {"t": text[i:i + 60]})
                total = round((time.time() - t0) * 1000)
                yield _sse("done", {"cached": True, "timings": {"total_ms": total, "ttft_ms": 0}})
                _track(req, request, {"query": req.query[:300], "cached": True, "total_ms": total, "ttft_ms": total, "sources": len(hit["sources"])})
                return
            msgs, sources, result, now = _prepare(req)
            yield _sse("meta", {"sources": sources, "cached": False, "intent": bool(result["intent"])})
            model = req.model or LLM_MODEL
            t_llm, ttft, parts = time.time(), None, []
            stream, model = _create(msgs, model, stream=True)
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    if ttft is None:
                        ttft = round((time.time() - t0) * 1000)
                    parts.append(delta)
                    yield _sse("delta", {"t": delta})
            answer = "".join(parts)
            timings = {**result["timings"], "ttft_ms": ttft, "llm_ms": round((time.time() - t_llm) * 1000),
                       "total_ms": round((time.time() - t0) * 1000)}
            yield _sse("done", {"cached": False, "timings": timings})
            if key and answer:
                answer_cache.set(key, {"answer": answer, "model_used": model, "sources": sources},
                                 ttl=DATE_ANSWER_TTL if result["intent"] else ANSWER_TTL)
            _track(req, request, {"query": req.query[:300], "cached": False, "sources": len(sources), "no_context": not sources,
                                  "fallback": bool(re.search(r"(isn't|is not|not) (in|available)|not on the school calendar", answer, re.I)),
                                  **timings})
        except Exception as e:
            if req.anon_id:
                analytics.record("chat_error", req.anon_id, req.session_id, {"error": str(e)[:200], "query": req.query[:200]})
            yield _sse("error", {"message": str(e)[:300]})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ calendar + links for the UI
@app.get("/api/calendar")
def calendar_events(start: str = Query(..., alias="from"), to: str = Query(...), category: str = "", q: str = "", limit: int = 400):
    for d in (start, to):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise HTTPException(400, "from/to must be YYYY-MM-DD")
    key = (start, to, category, q.lower(), limit)
    if (hit := api_cache.get(key)) is not None:
        return hit
    query = get_supabase().table("document_chunks").select("metadata").eq("metadata->>source", "calendar") \
        .gte("metadata->>end_date", start).lte("metadata->>event_date", to)
    if category and re.fullmatch(r"[a-z_]{3,20}", category):
        query = query.eq("metadata->>category", category)
    if q:
        safe = re.sub(r"[^A-Za-z0-9 '&-]", "", q)[:60]
        if safe:
            query = query.or_(f"metadata->>summary.ilike.%{safe}%,metadata->>location.ilike.%{safe}%")
    rows = query.limit(min(limit, 600)).execute().data or []
    events = [{k: (r["metadata"] or {}).get(k) for k in ("summary", "event_date", "end_date", "when", "time", "all_day", "start_time",
                                                           "end_time", "location", "description", "category", "kind", "home_away")}
              for r in rows]
    events.sort(key=lambda e: (e["event_date"] or "", e["start_time"] or ""))
    out = {"events": events, "count": len(events)}
    api_cache.set(key, out, ttl=600)
    return out


@app.get("/api/links")
def important_links():
    if (hit := api_cache.get("links")) is not None:
        return hit
    rows = get_supabase().table("document_chunks").select("metadata").eq("metadata->>source", "link_registry").limit(300).execute().data or []
    links = [{k: (r["metadata"] or {}).get(k) for k in ("label", "url", "link_category", "found_on")} for r in rows]
    out = {"links": [l for l in links if l.get("url")]}
    api_cache.set("links", out, ttl=3600)
    return out


# ------------------------------------------------------------------ instrumentation
class TrackEvent(BaseModel):
    event: str
    props: dict = Field(default_factory=dict)
    ts: str | None = None


class TrackBatch(BaseModel):
    anon_id: str
    session_id: str = ""
    path: str = ""
    events: list[TrackEvent] = Field(max_length=50)


@app.post("/api/track")
async def track(batch: TrackBatch, request: Request):
    ua = request.headers.get("user-agent", "")
    accepted = sum(analytics.record(e.event, batch.anon_id, batch.session_id, e.props, batch.path, ua, e.ts) for e in batch.events)
    return {"accepted": accepted}


@app.get("/api/metrics")
def metrics(request: Request, days: int = 14, token: str = ""):
    expected = os.getenv("METRICS_TOKEN", "")
    if not expected:
        raise HTTPException(503, "Metrics are disabled: set METRICS_TOKEN on the server.")
    supplied = request.headers.get("x-metrics-token") or token
    if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "Invalid metrics token.")
    out = analytics.summary(min(max(days, 1), 60))
    out["server"] = {"answer_cache": answer_cache.stats(), "embed_cache": retriever.embed_cache.stats(),
                     "models": retriever.models.ready, "llm_model": LLM_MODEL}
    return out


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
