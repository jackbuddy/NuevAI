"""A small extra: "How many people are in the lunch line?" answered from a classmate's live lunch line monitor.

The monitor (https://nuevasocratica.com/lunch-line-monitor) counts people in a camera frame and publishes the number as JSON. We read only that
number: no image, nothing about the asker is sent (the request carries no query text, session or user data). The result is cached for a few
seconds so a busy lunch hour is one request, not one per student, and every failure falls back to a link, never to a guess.
"""
import json
import re
import threading
import time
import urllib.request

PAGE = "https://nuevasocratica.com/lunch-line-monitor"
API = PAGE + "/api/count"
TTL_S = 15            # how long one reading is reused
TIMEOUT_S = 3         # never hold up an answer for the monitor
FRESH_S = 120         # a reading older than this means the camera is off, so we do not report it as current

# "how many people are in the lunch line", "how long is the lunch line", "is the cafeteria line crowded", "lunch line?" ...
LINE_Q = re.compile(
    r"\b(lunch|cafe|caf[eé]teria|food|dining)\s+(line|queue)\b"
    r"|\b(line|queue)\b.{0,25}\b(for lunch|at lunch|in the (cafe|caf[eé]teria|dining))\b"
    r"|\bhow (long|busy|crowded|packed|big|bad)\b.{0,20}\b(is|are)\b.{0,12}\b(the )?(line|queue)\b.{0,20}\b(lunch|cafe|caf[eé]teria)\b", re.I)

_lock = threading.Lock()
_cache: dict = {"t": 0.0, "data": None}


def is_lunch_line_question(q: str) -> bool:
    return bool(LINE_Q.search(q))


def _fetch() -> dict | None:
    req = urllib.request.Request(API, headers={"User-Agent": "NuevaAI/1.0 (school assistant)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        data = json.loads(r.read(20_000))
    count = data.get("count")
    if isinstance(count, bool) or not isinstance(count, (int, float)) or count < 0 or count > 5000:
        return None
    return {"count": int(count), "age": data.get("count_age_seconds")}


def reading() -> dict | None:
    """The current count, from a 15 second cache; None when the monitor cannot be reached or looks stale."""
    now = time.time()
    with _lock:
        if _cache["data"] is not None and now - _cache["t"] < TTL_S:
            return _cache["data"]
    try:
        data = _fetch()
    except Exception:
        data = None
    with _lock:
        _cache.update(t=now, data=data)
    return data


def answer(q: str) -> str | None:
    """The reply for a lunch line question, or None when the question is something else."""
    if not is_lunch_line_question(q):
        return None
    link = f"[Open the live lunch line monitor]({PAGE})"
    data = reading()
    age = data["age"] if data and isinstance(data.get("age"), (int, float)) else 0
    if data is None or age > FRESH_S:
        why = "I couldn't reach the lunch line monitor right now." if data is None else "The lunch line monitor hasn't updated in a while, so it may be off."
        return f"Sorry. {why} You can check it directly:\n\n{link}"
    n = data["count"]
    if n == 0:
        head = "Looks like **nobody is in the lunch line** right now."
    elif n == 1:
        head = "About **1 person** is in the lunch line right now."
    else:
        head = f"About **{n} people** are in the lunch line right now."
    return (f"{head}\n\nThis is a camera count from a student-made lunch line monitor, updated every few seconds. It's an estimate and can undercount a packed line, "
            f"so treat it as a gauge, not an exact number.\n\n{link}")
