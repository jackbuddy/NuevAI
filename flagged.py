"""Flagged & low-rated answers: a pure function from analytics event rows to the review list on the Insights Quality tab.

An item is one assistant answer that something went wrong with. Sources, in order of strength:
  down        thumbs-down (`feedback` value=down; the LATEST rating per answer id wins), with the reason chip / comment from `feedback_detail`
  not_really  "Did this answer your question?" -> Not really (`answer_check` value=no)
  written     header "Report a problem" note tied to an answer (`feedback_text` carrying `mid`) or free standing
  auto        flagged by the server's own signals on `chat_answered` (degraded / no sources / "not found" wording / a single source on an LLM answer)
              or by the client's fallback detection (`answer_fallback`)
Events of one answer are joined on (anon_id, props.mid). Events from before the feedback overhaul have no mid: a bare thumbs-down is joined to the
chat_answered with the same normalised question (nearest in time) so route/model/outcome still show; their answer text is NOT available and is
reported as such, never reconstructed. The answer text only exists on events from this feature onwards (feedback / answer_check / answer_fallback /
feedback_text carry a truncated copy; chat_answered never did).

Pure: no network, no globals. See test_flagged.py.
"""
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import audience as A
import insights as I
from chat_trace import topic_of

REASONS = ("wrong", "outdated", "not_answered", "too_long", "other")
TYPES = ("down", "not_really", "written", "auto")
MATCH_WINDOW_S = 2 * 3600       # client clocks drift; a chat_answered more than this far from a client event is not the same answer
MAX_ROWS = 200
MAX_INFRA_ROWS = 50           # degraded-only replies (the model was down) are listed after the real quality flags and hidden by default in the UI
STRUCTURED_TOPICS = ("lunch", "calendar", "athletics")
ANSWER_NOTE = "The answer text was not recorded for this event (only answers rated or flagged after the feedback update keep a copy)."


def _s(v, n) -> str:
    return str(v or "")[:n]


def auto_outcome(p: dict) -> str | None:
    """Why a chat_answered looks low quality on its own, or None. Reuses the dashboard's definition plus 'few sources' for LLM answers."""
    r = I.gap_reason(p)
    if r:
        return r
    # a single source is normal for structured topics (a lunch menu, one calendar day, a team schedule): only judge the open-ended ones, and only
    # when the server recorded the route (older events have no route/topic, so they would be guesses)
    topic = p.get("topic") or topic_of(p.get("query", ""))
    if p.get("route") == "llm" and p.get("sources") == 1 and topic not in STRUCTURED_TOPICS:
        return "few sources"
    return None


def build(rows, now: datetime, days: int, selector: str = "users", profiles=None, overrides=None, limit: int = MAX_ROWS) -> dict:
    selector = selector if selector in I.SELECTORS else "users"
    rows, _ = I.dedupe(rows)
    aud_of = I._aud_map(rows, profiles, overrides or {})
    lo = None if days == 0 else now - timedelta(days=days)

    evs = []
    for r in rows:
        t = I.parse_ts(r.get("ts"))
        if t is None or not r.get("anon_id") or (lo is not None and t < lo) or t > now + timedelta(minutes=5):
            continue
        au = aud_of.get(r["anon_id"], "user")
        if au == "system" or not A.wanted(au, selector):
            continue
        e = I.Ev(t, r["anon_id"], r.get("session_id"), r.get("event") or "", r.get("props") or {}, r.get("ua") or "")
        e.aud = au
        evs.append(e)
    evs.sort(key=lambda e: e.t)

    chats = defaultdict(list)                       # anon -> chat_answered events
    for e in evs:
        if e.name == "chat_answered":
            chats[e.anon].append(e)
    used = set()                                    # id() of chat_answered events already attached to an item

    def nearest_chat(anon, query, t):
        key = I.norm_query(query)
        best, gap = None, None
        for c in chats.get(anon, []):
            if id(c) in used or I.norm_query(c.p.get("query", "")) != key:
                continue
            d = abs((c.t - t).total_seconds())
            if d <= MATCH_WINDOW_S and (gap is None or d < gap):
                best, gap = c, d
        return best

    items: dict = {}
    order = []

    def item(key, e, q=""):
        it = items.get(key)
        if it is None:
            it = items[key] = {"key": key, "anon": e.anon, "aud": e.aud, "t": e.t, "t_first": e.t, "q": "", "answer": None, "value": None, "check": None,
                               "reason": None, "comment": "", "report": "", "auto": None, "chat": None, "mid": key[1] if key[1] and key[0] != "x" else None}
            order.append(key)
        it["t"] = max(it["t"], e.t)
        it["t_first"] = min(it["t_first"], e.t)
        if q and not it["q"]:
            it["q"] = _s(q, 300)
        if not it["answer"] and e.p.get("answer"):
            it["answer"] = _s(e.p["answer"], 700)
        return it

    n_legacy = 0
    for e in evs:
        p, n = e.p, e.name
        mid = p.get("mid") if isinstance(p.get("mid"), str) and p.get("mid") else None
        if n == "feedback" and p.get("value") in ("up", "down"):
            if mid:
                it = item((e.anon, mid), e, p.get("query"))
            else:                                   # pre-overhaul event: no id, no answer text
                n_legacy += 1
                it = item((e.anon, f"legacy{n_legacy}"), e, p.get("query"))
                it["mid"] = None
            it["value"] = p["value"]
            if p["value"] == "up":                  # the person changed their mind: the older reason no longer applies
                it["reason"] = None
        elif n == "feedback_detail" and mid:
            it = item((e.anon, mid), e, p.get("query"))
            if p.get("reason") in REASONS:
                it["reason"] = p["reason"]
            if p.get("comment"):
                it["comment"] = _s(p["comment"], 300)
            if p.get("src") == "check" and it["check"] is None:
                it["check"] = "no"
            if p.get("value") == "down" and it["value"] is None and p.get("src") != "check":
                it["value"] = "down"
        elif n == "answer_check" and mid and p.get("value") in ("yes", "no"):
            it = item((e.anon, mid), e, p.get("query"))
            it["check"] = p["value"]
        elif n == "answer_fallback" and mid:
            it = item((e.anon, mid), e, p.get("query"))
            it["auto"] = _s(p.get("why"), 20) or "fallback"
        elif n == "feedback_text":
            text = _s(p.get("text"), 600)
            if not text:
                continue
            if mid:
                it = item((e.anon, mid), e, p.get("lq"))
            else:
                it = item(("x", f"{e.anon}:{e.t.isoformat()}"), e)
                it["mid"] = None
            it["report"] = (it["report"] + "\n" if it["report"] else "") + text
            it["report_kind"] = _s(p.get("kind"), 40)

    # join client items to the server's record of the same answer (route / model / outcome)
    for it in items.values():
        q = it["q"]
        if q:
            c = nearest_chat(it["anon"], q, it["t_first"])
            if c is not None:
                used.add(id(c))
                it["chat"] = c
                if not it["q"]:
                    it["q"] = _s(c.p.get("query"), 300)

    def types_of(it) -> list[str]:
        out = []
        if it["value"] == "down":
            out.append("down")
        if it["check"] == "no":
            out.append("not_really")
        if it["report"]:
            out.append("written")
        c = it["chat"]
        if it["auto"] or (c is not None and auto_outcome(c.p)):
            out.append("auto")
        return out

    out_items = []
    for it in items.values():
        ty = types_of(it)
        if ty:
            out_items.append((it, ty))

    # server-only automatic flags with no client item (older events, or a browser that closed before the client event)
    for lst in chats.values():
        for c in lst:
            if id(c) in used:
                continue
            why = auto_outcome(c.p)
            if not why:
                continue
            it = {"key": ("c", c.p.get("_eid") or c.t.isoformat()), "anon": c.anon, "aud": c.aud, "t": c.t, "t_first": c.t, "q": _s(c.p.get("query"), 300),
                  "answer": None, "value": None, "check": None, "reason": None, "comment": "", "report": "", "auto": None, "chat": c, "mid": None}
            out_items.append((it, ["auto"]))

    rows_out = []
    counts_t, counts_r = Counter(), Counter()
    for it, ty in out_items:
        c = it["chat"]
        cp = c.p if c is not None else {}
        route, inferred = I.route_of(cp) if c is not None else (None, False)
        outcomes = []
        if cp:
            o = auto_outcome(cp)
            if o:
                outcomes.append(o)
        if it["auto"] and not outcomes:
            outcomes.append({"degraded": "degraded", "no_sources": "no sources", "hedge": "hedged / redirected"}.get(it["auto"], it["auto"]))
        reasons = ([it["reason"]] if it["reason"] else []) + outcomes
        if "not_really" in ty and "not_answered" not in reasons:
            reasons.insert(0, "not_answered")
        for t in ty:
            counts_t[t] += 1
        for r in dict.fromkeys(reasons):
            counts_r[r] += 1
        rid = hashlib.sha256(repr(it["key"]).encode()).hexdigest()[:12]
        rows_out.append({
            "id": rid, "ts": it["t"].isoformat(), "aud": it["aud"], "q": it["q"] or "(question not recorded)", "types": ty,
            "answer": it["answer"], "answer_note": None if it["answer"] else ANSWER_NOTE,
            "value": it["value"], "check": it["check"], "reasons": list(dict.fromkeys(reasons)), "comment": it["comment"], "report": it["report"],
            "report_kind": it.get("report_kind", ""), "route": route, "route_inferred": inferred, "model": cp.get("model"),
            "outcome": ("degraded" if cp.get("degraded") else I.gap_reason(cp) or ("few sources" if "few sources" in outcomes else "ok")) if cp else None,
            "ms": cp.get("total_ms"), "n_src": cp.get("sources") if cp else None, "topic": cp.get("topic"),
        })
    for r in rows_out:
        r["infra"] = r["types"] == ["auto"] and r["reasons"] == ["degraded"]     # the provider was down; not an answer-quality problem
    rows_out.sort(key=lambda r: r["ts"], reverse=True)
    main = [r for r in rows_out if not r["infra"]]
    infra = [r for r in rows_out if r["infra"]]
    total = len(rows_out)
    with_text = sum(1 for r in rows_out if r["answer"])
    return {
        "rows": main[:limit] + infra[:MAX_INFRA_ROWS], "total": total, "total_main": len(main), "total_infra": len(infra),
        "truncated": len(main) > limit or len(infra) > MAX_INFRA_ROWS, "with_answer_text": with_text,
        "counts": {"types": {t: counts_t.get(t, 0) for t in TYPES}, "reasons": dict(counts_r.most_common())},
        "meta": {"days": days, "selector": selector, "now": now.isoformat(), "tz": "America/Los_Angeles"},
    }
