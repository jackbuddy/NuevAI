"""Per-request trace for the chat path: how a question was served, which model answered, every LLM attempt, timings, load.

Pure and cheap: plain Python objects, no network, nothing here can raise into the chat path (callers also wrap it).
The result is flattened into the props of one `chat_answered` event (see Trace.props).
"""
import os
import re
import threading
import time

BOOT_TS = time.time()
_lock = threading.Lock()
_inflight = 0
_first_chat = True
MAX_ATTEMPTS_KEPT = 6


def inflight_enter() -> tuple[int, bool]:
    """Returns (concurrent chat requests including this one, is_cold_first_request_since_boot)."""
    global _inflight, _first_chat
    with _lock:
        _inflight += 1
        cold, _first_chat = _first_chat, False
        return _inflight, cold


def inflight_exit():
    global _inflight
    with _lock:
        _inflight = max(0, _inflight - 1)


def inflight_now() -> int:
    return _inflight


def rss_mb() -> float | None:
    """Resident set size now (Linux /proc), else the process peak (macOS dev)."""
    try:
        with open("/proc/self/statm") as f:
            return round(int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576, 1)
    except Exception:
        try:
            import resource, sys
            r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return round(r / (1048576 if sys.platform == "darwin" else 1024), 1)
        except Exception:
            return None


def error_kind(status: int | None, exc_name: str = "") -> str:
    """rate_limit | overloaded | timeout | connection | server_error | other"""
    n = exc_name.lower()
    if "timeout" in n or status == 408:
        return "timeout"
    if status == 429:
        return "rate_limit"
    if status in (503, 529):
        return "overloaded"
    if status is not None and status >= 500:
        return "server_error"
    if "connection" in n:
        return "connection"
    return "other"


TOPIC_RULES = [
    ("lunch", r"\b(lunch|cafe|cafeteria|menu|pizza day|food|dining|snack)\b"),
    ("athletics", r"\b(sports?|athletic\w*|game|games|practice|team|teams|varsity|jv|basketball|soccer|volleyball|tennis|swim\w*|track|baseball|softball|cross country|golf|lacrosse|badminton|fencing|rugby|flag football)\b"),
    ("calendar", r"\b(calendar|schedule|when is|what day|break|holiday|no school|a day|b day|a'|b'|early release|dismissal|exam|finals?|due|deadline|today|tomorrow|next week|this week|date)\b"),
    ("courses", r"\b(course|courses|class|classes|elective|prerequisite|prereq|teacher|taught|teaches|teach|block|syllabus|credit|ap |honors|requirements?|graduat\w*|offered|engineering|math|science|history|english|language|art|music|cs|computer science)\b"),
    ("clubs", r"\b(clubs?|activities|organization|student government|council|societ(y|ies))\b"),
    ("policy", r"\b(polic\w*|rules?|allowed|attendance|absen\w*|tardy|late|phones?|dress code|uniform|discipline|plagiar\w*|cheat\w*|honor|grading|grades?|homework|retake|laptops?|excused|handbook)\b"),
    ("tech_help", r"\b(wifi|wi-fi|password|login|log in|laptop|chromebook|printer|print|email|google|canvas|schoology|powerschool|it help|tech|vpn)\b"),
    ("admissions_giving", r"\b(admission\w*|apply|application|tour|tuition|financial aid|donate|giving|alumni|enroll\w*|visit)\b"),
    ("contact_people", r"\b(contact|phone number|email address|who is|who do i|office|counselor|advisor|advisory|principal|head of|director|dean)\b"),
    ("campus_logistics", r"\b(parking|bus|transport\w*|address|where is|directions?|room|building|gym|library|bell|start time|end time|pick ?up|drop ?off|hours)\b"),
]
_TOPIC_RX = [(t, re.compile(rx, re.I)) for t, rx in TOPIC_RULES]


def topic_of(query: str, hint: str | None = None) -> str:
    """Coarse topic label. `hint` is a structured signal from the server ('lunch', 'calendar', 'athletics', 'course') that wins when present.
    Rule-based on the question text only, so the same rules re-derive a topic for history (labelled inferred in the UI)."""
    if hint in ("lunch", "athletics", "calendar", "courses"):
        return hint
    q = query or ""
    for t, rx in _TOPIC_RX:
        if rx.search(q):
            return t
    return "other"


class Trace:
    """Collected while a chat request runs. All methods are no-fail."""

    def __init__(self):
        self.t0 = time.time()
        self.route: str | None = None        # cache_l1 | cache_l2 | canned | guard | structured | llm | degraded
        self.cache_layer: str | None = None
        self.attempts: list[dict] = []
        self.retries = 0                     # whole-chain rounds beyond the first
        self.waited_ms = 0
        self.requested_model: str | None = None
        self.model: str | None = None
        self.usage: dict | None = None       # {"pt":..,"ct":..,"src":"api"|"est"}
        self.inflight = 0
        self.cold = False
        self.dep_errors: list[dict] = []
        self.topic_hint: str | None = None

    def attempt(self, model: str, outcome: str, status: int | None = None, kind: str | None = None, ms: int = 0, retry_after: float = 0.0):
        try:
            a = {"m": model, "o": outcome, "ms": int(ms)}
            if status:
                a["s"] = int(status)
            if kind:
                a["k"] = kind
            if retry_after:
                a["ra"] = round(retry_after, 1)
            self.attempts.append(a)
        except Exception:
            pass

    def set_usage(self, prompt_tokens, completion_tokens, src: str):
        try:
            self.usage = {"pt": int(prompt_tokens or 0), "ct": int(completion_tokens or 0), "src": src}
        except Exception:
            pass

    def usage_from(self, obj, msgs=None, text: str = ""):
        """Real usage when the provider returned it (completion.usage, or x_groq.usage on the last stream chunk); else a labelled estimate."""
        try:
            u = getattr(obj, "usage", None) or getattr(getattr(obj, "x_groq", None), "usage", None)
            if u is not None and getattr(u, "total_tokens", None) is not None:
                self.set_usage(getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0), "api")
                return
        except Exception:
            pass
        if self.usage is None and msgs is not None:
            chars = sum(len(str(m.get("content", ""))) for m in msgs)
            self.set_usage(chars / 3.6, len(text) / 3.6 + 40, "est")      # ~3.6 chars/token for English; +40 for hidden reasoning, a floor

    def props(self, extra: dict | None = None) -> dict:
        p = dict(extra or {})
        try:
            first = self.requested_model
            p.update(route=self.route, retries=self.retries)
            if self.cache_layer:
                p["cache_layer"] = self.cache_layer
            if self.model and self.model != "none":
                p["model"] = self.model
            failed = [a for a in self.attempts if a["o"] != "ok"]
            p["fb"] = bool(self.model and first and self.model != first and self.model != "none")
            if failed:
                p["fb_why"] = failed[0].get("k", "other")
                p["n_att"] = len(self.attempts)
                p["attempts"] = self.attempts[:MAX_ATTEMPTS_KEPT]
            if self.waited_ms:
                p["waited_ms"] = self.waited_ms
            if self.usage:
                p.update(pt=self.usage["pt"], ct=self.usage["ct"], tok_src=self.usage["src"])
            p.update(inflight=self.inflight, cold=self.cold, up_s=round(time.time() - BOOT_TS))
            r = rss_mb()
            if r is not None:
                p["rss_mb"] = r
        except Exception:
            pass
        return {k: v for k, v in p.items() if v is not None}
