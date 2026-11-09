"""Insights aggregation: pure functions from event rows to the dashboard payload. No network, no globals: fully unit-testable.

Inputs
  rows       list of {ts, anon_id, session_id, event, props, ua} (any order; duplicates by props._eid are ignored)
  profiles   all-time per-anon_id profiles from the SQL function analytics_anon_profile (or None: derived from `rows`, flagged)
  overrides  {anon_id: {audience, note}} from the owner-managed table
Conventions
  * every day/hour is America/Los_Angeles (DST aware); Groq's token day is UTC and is bucketed separately
  * "active" = did something (not just loaded the page); "visitor" = any event at all
  * audience is decided per browser across its whole history (audience.classify_anon); `selector` picks which audiences are shown
"""
import hashlib
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import audience as A
from chat_trace import topic_of

PACIFIC = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc
SELECTORS = ("users", "users_internal", "test_bots", "all")

# Groq free tier (per model): 200k tokens/day, 8k tokens/min. Constants, shown on the dashboard so a change is one edit.
GROQ_DAILY_TOKENS = 200_000
GROQ_TPM = 8_000

PASSIVE = {"session_start", "page_view", "perf_load", "web_vitals", "backend_warm", "server_boot", "rate_limited", "dependency_error",
           "client_error", "chat_error", "onboarding_shown", "hint_shown", "install_prompt_shown", "first_answer_seen", "chat_rendered"}
ASK_EVENTS = {"chat_sent", "chat_answered"}

FEATURES = [  # (label, predicate(name, props))
    ("Chat: typed question", lambda n, p: n == "chat_sent" and p.get("via") == "typed"),
    ("Chat: starter / suggestion", lambda n, p: n in ("starter_click", "suggestion_click") or (n == "chat_sent" and p.get("via") == "starter")),
    ("Chat: asked from another tab", lambda n, p: n == "chat_sent" and p.get("via") == "ask"),
    ("Sources opened", lambda n, p: n == "sources_toggle" and p.get("open")),
    ("Helpful / not helpful", lambda n, p: n == "feedback"),
    ("Copied an answer", lambda n, p: n == "copy_answer"),
    ("Calendar", lambda n, p: n.startswith("calendar_")),
    ("Calendar: add to my calendar", lambda n, p: n == "calendar_add"),
    ("Resources", lambda n, p: n.startswith("resource_")),
    ("Happening cards", lambda n, p: n.startswith("happening_") or n == "today_strip_click"),
    ("Lunch card", lambda n, p: n == "happening_click" and p.get("card") == "lunch"),
    ("Preferences (role / campus)", lambda n, p: n == "pref_set"),
    ("Written feedback", lambda n, p: n == "feedback_text"),
    ("Outbound / answer links", lambda n, p: n in ("outbound_click", "answer_link_click")),
]
DISCOVERY = ["onboarding_shown", "onboarding_dismiss", "hint_shown", "hint_click", "hint_dismiss", "ideas_open", "idea_click", "recent_click",
             "follow_team", "unfollow_team", "install_prompt_shown", "install_accept", "install_dismiss", "whatsnew_open", "pref_set", "first_answer_seen"]
KNOWN_EVENTS = set(PASSIVE) | {"tab_view", "chat_sent", "chat_answered", "feedback", "feedback_text", "feedback_open", "starter_click", "suggestion_click",
                               "sources_toggle", "copy_answer", "chat_cleared", "chat_auto_retry", "chat_error_ui", "outbound_click", "answer_link_click"} | set(DISCOVERY)


# ---------------------------------------------------------------- small helpers
_TS_RX = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.(\d+))?(Z|[+-]\d\d(?::?\d\d)?)?$")


def parse_ts(ts) -> datetime | None:
    """Tolerant ISO-8601 parser (Python 3.10 fromisoformat is strict about fractions, 'Z' and '+00')."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    m = _TS_RX.match(str(ts or "").strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, tz = m.groups()
    us = int((frac or "0")[:6].ljust(6, "0"))
    if not tz or tz == "Z":
        off = timedelta(0)
    else:
        sign = -1 if tz[0] == "-" else 1
        digits = tz[1:].replace(":", "")
        off = sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4] or 0))
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), us, tzinfo=timezone(off))
    except ValueError:
        return None


def pct(values, p):
    """Linear-interpolated percentile (p in 0..1). None for no data."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    if len(v) == 1:
        return round(v[0])
    k = (len(v) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo))


def norm_query(q: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (q or "").lower()).strip()[:160]


def short_id(anon_id: str) -> str:
    return hashlib.sha256(anon_id.encode()).hexdigest()[:10]


def ratio(a, b, nd=3):
    return round(a / b, nd) if b else None


def pac_date(dt: datetime) -> date:
    return dt.astimezone(PACIFIC).date()


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


class Ev:
    __slots__ = ("t", "anon", "sid", "name", "p", "ua", "pt", "aud")

    def __init__(self, t, anon, sid, name, p, ua):
        self.t, self.anon, self.sid, self.name, self.p, self.ua = t, anon, sid, name, p, ua
        self.pt = t.astimezone(PACIFIC)
        self.aud = "user"


def dedupe(rows) -> tuple[list[dict], int]:
    """Drop repeated events (client retries / double flushes) by their ingestion id. Rows without one are kept."""
    seen, out, dup = set(), [], 0
    for r in rows:
        eid = (r.get("props") or {}).get("_eid")
        if eid:
            if eid in seen:
                dup += 1
                continue
            seen.add(eid)
        out.append(r)
    return out, dup


def is_interaction(e: Ev) -> bool:
    if e.name in PASSIVE:
        return False
    if e.name == "tab_view" and e.p.get("source") in ("load", "url"):
        return False
    return True


def route_of(p: dict) -> tuple[str, bool]:
    """(route, inferred). Events before route tracking get a best-effort route from older fields."""
    r = p.get("route")
    if r:
        return {"cache_l1": "cache_l1", "cache_l2": "cache_l2"}.get(r, r), False
    if p.get("degraded"):
        return "degraded", True
    if p.get("smalltalk"):
        return "canned", True
    if p.get("cached"):
        return "cache", True
    return "llm", True


def gap_reason(p: dict) -> str | None:
    if p.get("degraded"):
        return "degraded"
    if p.get("no_context"):
        return "no sources"
    if p.get("fallback"):
        return "answered 'not found'"
    return None


def is_well(p: dict) -> bool:
    return gap_reason(p) is None


def device_of(e: Ev) -> str:
    return A.device_of_ua(e.ua)


# ---------------------------------------------------------------- main build
def build(rows, now: datetime, days: int, selector: str = "users", profiles=None, overrides=None, truncated: bool = False, health=None) -> dict:
    selector = selector if selector in SELECTORS else "users"
    overrides = overrides or {}
    rows, dups = dedupe(rows)
    events: list[Ev] = []
    for r in rows:
        t = parse_ts(r.get("ts"))
        if t is None or not r.get("anon_id"):
            continue
        events.append(Ev(t, r["anon_id"], r.get("session_id"), r.get("event") or "", r.get("props") or {}, r.get("ua") or ""))
    events.sort(key=lambda e: e.t)

    # ---- per-browser profile (all-time) + audience --------------------------------
    ev_aud, ev_ua = defaultdict(set), defaultdict(set)
    first_in_rows, last_in_rows = {}, {}
    for e in events:
        if "_aud" in e.p:
            ev_aud[e.anon].add(e.p["_aud"])
        if e.ua:
            ev_ua[e.anon].add(e.ua)
        first_in_rows.setdefault(e.anon, e.t)
        last_in_rows[e.anon] = e.t
    prof = {}
    profile_source = "sql_all_time"
    if profiles is None:
        profile_source = "loaded_rows"
        dates = defaultdict(set)
        sess, nchat, nev = defaultdict(set), Counter(), Counter()
        for e in events:
            dates[e.anon].add(pac_date(e.t))
            if e.sid:
                sess[e.anon].add(e.sid)
            nev[e.anon] += 1
            nchat[e.anon] += e.name == "chat_answered"
        for a in first_in_rows:
            prof[a] = {"first": first_in_rows[a], "last": last_in_rows[a], "events": nev[a], "chats": nchat[a], "sessions": len(sess[a]),
                       "dates": sorted(dates[a]), "uas": set(), "auds": set()}
    else:
        for r in profiles:
            ft, lt = parse_ts(r.get("first_ts")), parse_ts(r.get("last_ts"))
            if not ft:
                continue
            prof[r["anon_id"]] = {"first": ft, "last": lt or ft, "events": r.get("events") or 0, "chats": r.get("chats") or 0,
                                  "sessions": r.get("sessions") or 0, "uas": set(r.get("uas") or []), "auds": set(r.get("auds") or []),
                                  "dates": [date.fromisoformat(d) for d in (r.get("active_dates") or [])]}
        for a, ft in first_in_rows.items():                 # browsers newer than the cached profile list
            if a not in prof:
                prof[a] = {"first": ft, "last": last_in_rows[a], "events": 0, "chats": 0, "sessions": 0,
                           "dates": sorted({pac_date(e.t) for e in events if e.anon == a}), "uas": set(), "auds": set()}
    aud_of, aud_src = {}, {}
    for a, pr in prof.items():
        if A.is_system_id(a):
            aud_of[a], aud_src[a] = "system", "measured"
            continue
        ov = (overrides.get(a) or {}).get("audience")
        aud_of[a], aud_src[a] = A.classify_anon(a, pr["uas"] | ev_ua.get(a, set()), pr["auds"] | ev_aud.get(a, set()), ov)
    for e in events:
        e.aud = aud_of.get(e.anon, "user")
    show = lambda e: e.aud != "system" and A.wanted(e.aud, selector)

    # ---- windows --------------------------------------------------------------
    all_time = days == 0
    w_end = now
    w_start = None if all_time else now - timedelta(days=days)
    p_start = None if all_time else now - timedelta(days=2 * days)
    in_w = lambda e: (w_start is None or e.t >= w_start) and e.t <= w_end
    in_p = lambda e: (not all_time) and p_start <= e.t < w_start
    ew = [e for e in events if in_w(e)]
    ep = [e for e in events if in_p(e)]
    vis_w = [e for e in ew if show(e)]
    vis_p = [e for e in ep if show(e)]

    # ---- buckets (hourly for 24 h, daily otherwise) -------------------------------------
    hourly = days == 1
    now_p = now.astimezone(PACIFIC)
    if hourly:
        base = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)      # UTC hours: unique keys even across a DST change
        bucket_list = [base - timedelta(hours=23 - i) for i in range(24)]
        bkey = lambda dt: dt.astimezone(UTC).strftime("%Y-%m-%dT%H")
        bucket_keys = [b.strftime("%Y-%m-%dT%H") for b in bucket_list]
        bucket_labels = [b.astimezone(PACIFIC).strftime("%-I%p").lower() for b in bucket_list]
    else:
        n_days = days + 1 if days else max(1, min(180, (now_p.date() - (min((e.pt.date() for e in events), default=now_p.date()))).days + 1))
        d0 = now_p.date() - timedelta(days=n_days - 1)
        ds = [d0 + timedelta(days=i) for i in range(n_days)]
        bkey = lambda dt: dt.astimezone(PACIFIC).strftime("%Y-%m-%d")
        bucket_keys = [d.isoformat() for d in ds]
        bucket_labels = [d.strftime("%b %-d") for d in ds]

    def first_bucket(a):
        return bkey(prof[a]["first"]) if a in prof else None

    # ---- KPI block ---------------------------------------------------------------
    def kpis(evs, lo, hi):
        users_seen, active, askers, answered_users = set(), set(), set(), set()
        chats = [e for e in evs if e.name == "chat_answered"]
        for e in evs:
            users_seen.add(e.anon)
            if e.p.get("_src") != "server" and is_interaction(e):
                active.add(e.anon)
            if e.name in ASK_EVENTS:
                askers.add(e.anon)
        for e in chats:
            answered_users.add(e.anon)
        new = {a for a in users_seen if a in prof and (lo is None or prof[a]["first"] >= lo) and prof[a]["first"] <= hi}
        ret = users_seen - new
        up = sum(1 for e in evs if e.name == "feedback" and e.p.get("value") == "up")
        down = sum(1 for e in evs if e.name == "feedback" and e.p.get("value") == "down")
        well = sum(1 for e in chats if is_well(e.p))
        tot = [e.p.get("total_ms") for e in chats if not e.p.get("cached") and e.p.get("total_ms")]
        allt = [e.p.get("total_ms") for e in chats if e.p.get("total_ms") is not None]
        rendered = [e.p.get("total_ms") for e in evs if e.name == "chat_rendered" and e.p.get("total_ms")]
        first_r = [e.p.get("first_ms") for e in evs if e.name == "chat_rendered" and e.p.get("first_ms")]
        return {"visitors": len(users_seen), "active": len(active), "askers": len(askers), "chats": len(chats), "new": len(new), "returning": len(ret),
                "well": well, "well_rate": ratio(well, len(chats)), "up": up, "down": down, "helpful": ratio(up, up + down),
                "p50_total": pct(allt, .5), "p95_total": pct(allt, .95), "p50_llm_total": pct(tot, .5),
                "p50_seen": pct(rendered, .5), "p95_seen": pct(rendered, .95), "p50_first_seen": pct(first_r, .5), "p95_first_seen": pct(first_r, .95),
                "degraded": sum(1 for e in chats if e.p.get("degraded"))}

    cur = kpis(vis_w, w_start, w_end)
    prev = kpis(vis_p, p_start, w_start) if not all_time else None

    # ---- daily/hourly series --------------------------------------------------------
    per = {k: {"visitors": set(), "active": set(), "askers": set(), "chats": 0, "well": 0, "degraded": 0, "up": 0, "down": 0, "sessions": set(), "tot": []}
           for k in bucket_keys}
    for e in vis_w:
        b = per.get(bkey(e.t))
        if b is None:
            continue
        b["visitors"].add(e.anon)
        if e.sid:
            b["sessions"].add(e.sid)
        if e.p.get("_src") != "server" and is_interaction(e):
            b["active"].add(e.anon)
        if e.name in ASK_EVENTS:
            b["askers"].add(e.anon)
        if e.name == "chat_answered":
            b["chats"] += 1
            b["well"] += is_well(e.p)
            b["degraded"] += bool(e.p.get("degraded"))
            if e.p.get("total_ms") is not None:
                b["tot"].append(e.p["total_ms"])
        elif e.name == "feedback":
            b["up" if e.p.get("value") == "up" else "down"] += e.p.get("value") in ("up", "down")
    new_by = defaultdict(set)
    for a, pr in prof.items():
        if show_anon(a, aud_of, selector):
            new_by[bkey(pr["first"])].add(a)
    series = []
    for k, lab in zip(bucket_keys, bucket_labels):
        b = per[k]
        nw = len(new_by.get(k, set()) & b["visitors"])
        series.append({"k": k, "label": lab, "visitors": len(b["visitors"]), "active": len(b["active"]), "askers": len(b["askers"]), "chats": b["chats"],
                       "sessions": len(b["sessions"]), "new": nw, "returning": len(b["visitors"]) - nw, "well": b["well"], "degraded": b["degraded"],
                       "up": b["up"], "down": b["down"], "p50": pct(b["tot"], .5)})

    # ---- adoption & retention (all-time visit dates; independent of the range, always today-anchored) ------------------------
    today = now_p.date()
    sel_prof = {a: pr for a, pr in prof.items() if show_anon(a, aud_of, selector)}

    def uniq_in(lo_days: int) -> int:
        lo = today - timedelta(days=lo_days - 1)
        return sum(1 for pr in sel_prof.values() if any(lo <= d <= today for d in pr["dates"]))

    def dau_avg(n: int) -> float | None:
        lo = today - timedelta(days=n - 1)
        total = sum(1 for pr in sel_prof.values() for d in pr["dates"] if lo <= d <= today)
        return round(total / n, 2)

    wau, mau = uniq_in(7), uniq_in(30)
    adoption = {"dau_today": sum(1 for pr in sel_prof.values() if today in pr["dates"]), "dau_avg_7": dau_avg(7), "wau": wau, "mau": mau,
                "stickiness_wau": ratio(dau_avg(7), wau) if wau else None, "stickiness_mau": ratio(dau_avg(30), mau) if mau else None,
                "basis": "days on which a browser sent any event (all history)"}

    # funnel for browsers seen in the window
    f_users = {e.anon for e in vis_w}
    inter, asked, answered, asked2 = set(), set(), set(), Counter()
    for e in vis_w:
        if e.p.get("_src") != "server" and is_interaction(e):
            inter.add(e.anon)
        if e.name in ASK_EVENTS:
            asked.add(e.anon)
        if e.name == "chat_answered":
            answered.add(e.anon)
            asked2[e.anon] += 1
    came_back = {a for a in f_users if a in prof and len(set(prof[a]["dates"])) >= 2}
    funnel = [{"step": "Visited", "n": len(f_users)}, {"step": "Interacted", "n": len(inter)}, {"step": "Asked a question", "n": len(asked)},
              {"step": "Got an answer", "n": len(answered)}, {"step": "Asked again", "n": sum(1 for v in asked2.values() if v >= 2)},
              {"step": "Came back another day", "n": len(came_back & answered) if answered else len(came_back)}]
    bounced = sorted(f_users - inter)
    no_interaction = {"browsers": len(bounced), "of": len(f_users)}

    cohorts, overall = cohort_retention(sel_prof, today)
    sess_per = [pr["sessions"] for a, pr in sel_prof.items() if a in f_users and pr["sessions"]]
    gaps = []
    for pr in sel_prof.values():
        ds_ = sorted(set(pr["dates"]))
        if len(ds_) >= 2:
            gaps.append((ds_[1] - ds_[0]).days)
    adoption.update(sessions_per_user=round(sum(sess_per) / len(sess_per), 2) if sess_per else None,
                    days_to_return_median=pct(gaps, .5), days_to_return_n=len(gaps),
                    days_to_return_buckets={"next day": sum(1 for g in gaps if g == 1), "2-3 days": sum(1 for g in gaps if 2 <= g <= 3),
                                            "4-7 days": sum(1 for g in gaps if 4 <= g <= 7), "8+ days": sum(1 for g in gaps if g >= 8)})

    # ---- usage patterns -----------------------------------------------------------
    chats_w = [e for e in vis_w if e.name == "chat_answered"]
    by_hour = [0] * 24
    heat = [[0] * 24 for _ in range(7)]
    for e in chats_w:
        by_hour[e.pt.hour] += 1
        heat[e.pt.weekday()][e.pt.hour] += 1
    weekday_n = sum(1 for e in chats_w if e.pt.weekday() < 5)
    usage = {"by_hour": by_hour, "heatmap": heat, "weekday": weekday_n, "weekend": len(chats_w) - weekday_n,
             "note": "school day is approximated as Mon-Fri; holidays are not excluded"}
    today_tl = day_timeline(events, today, selector, aud_of)

    # ---- engagement & features ----------------------------------------------------
    n_active = max(1, len(inter))
    tabs = Counter(e.p.get("tab", "?") for e in vis_w if e.name == "tab_view" and e.p.get("source") not in ("load", "url"))
    tabs_users = defaultdict(set)
    for e in vis_w:
        if e.name == "tab_view":
            tabs_users[e.p.get("tab", "?")].add(e.anon)
    feats = []
    for label, pred in FEATURES:
        ev = [e for e in vis_w if e.p.get("_src") != "server" and _safe(pred, e)]
        us = {e.anon for e in ev}
        feats.append({"feature": label, "events": len(ev), "users": len(us), "share": ratio(len(us), n_active)})
    via = Counter(e.p.get("via", "unknown") for e in vis_w if e.name == "chat_sent")
    all_seen = Counter(e.name for e in events)
    first_seen_ev = {}
    for e in events:
        first_seen_ev.setdefault(e.name, e.t)
    disc = []
    for name in DISCOVERY:
        ev = [e for e in vis_w if e.name == name]
        detail = Counter(str(next((e.p[k] for k in ("variant", "reason", "id", "category", "label", "team", "platform", "role", "campus") if k in e.p), "")) for e in ev)
        disc.append({"event": name, "events": len(ev), "users": len({e.anon for e in ev}), "collected": name in all_seen,
                     "since": first_seen_ev[name].isoformat() if name in first_seen_ev else None, "top": [[k, v] for k, v in detail.most_common(4) if k]})
    other = Counter(e.name for e in vis_w if e.name not in KNOWN_EVENTS and not e.name.startswith(("calendar_", "resource_", "happening_")))
    engagement = {"tabs": [{"tab": k, "views": v, "users": len(tabs_users[k])} for k, v in tabs.most_common()], "features": feats, "chat_via": dict(via),
                  "discovery": disc, "n_active": len(inter)}
    diagnostics = [[k, v] for k, v in Counter(e.name for e in vis_w).most_common(60)]

    # ---- questions & content ------------------------------------------------------
    qs = {}
    topics = {}
    fb_down = Counter()
    for e in vis_w:
        if e.name == "feedback" and e.p.get("value") == "down" and e.p.get("query"):
            fb_down[norm_query(e.p["query"])] += 1
    for e in chats_w:
        raw = e.p.get("query", "")
        k = norm_query(raw)
        if not k:
            continue
        topic = e.p.get("topic")
        t_inf = topic is None
        topic = topic or topic_of(raw)
        route, _ = route_of(e.p)
        q = qs.setdefault(k, {"key": k, "q": raw[:140], "n": 0, "users": set(), "topic": topic, "gap": 0, "reasons": Counter(), "routes": Counter(), "last": e.t, "ms": []})
        q["n"] += 1
        q["users"].add(e.anon)
        q["routes"][route] += 1
        q["last"] = max(q["last"], e.t)
        if e.p.get("total_ms") is not None and not e.p.get("cached"):
            q["ms"].append(e.p["total_ms"])
        r = gap_reason(e.p)
        if r:
            q["gap"] += 1
            q["reasons"][r] += 1
        t = topics.setdefault(topic, {"topic": topic, "n": 0, "well": 0, "degraded": 0, "inferred": 0})
        t["n"] += 1
        t["well"] += is_well(e.p)
        t["degraded"] += bool(e.p.get("degraded"))
        t["inferred"] += t_inf

    def qrow(q):
        return {"key": q["key"], "q": q["q"], "n": q["n"], "users": len(q["users"]), "topic": q["topic"], "gap": q["gap"], "thumbs_down": fb_down.get(q["key"], 0),
                "reasons": dict(q["reasons"]), "routes": dict(q["routes"]), "last": q["last"].isoformat(), "p50_ms": pct(q["ms"], .5)}
    qlist = sorted(qs.values(), key=lambda q: (-q["n"], -q["last"].timestamp()))
    top_questions = [qrow(q) for q in qlist[:25]]
    gap_list = [qrow(q) for q in sorted((q for q in qs.values() if q["gap"]), key=lambda q: (-q["gap"], -q["n"]))[:25]]
    down_list = [qrow(qs[k]) if k in qs else {"key": k, "q": k, "n": 0, "users": 0, "topic": "?", "gap": 0, "thumbs_down": v, "reasons": {}, "routes": {}, "last": "", "p50_ms": None}
                 for k, v in fb_down.most_common(15)]
    feedback_msgs = [{"ts": e.t.isoformat(), "kind": e.p.get("kind", ""), "text": str(e.p.get("text", ""))[:600], "tab": e.p.get("tab", ""), "aud": e.aud}
                     for e in reversed(vis_w) if e.name == "feedback_text"][:30]
    content = {"topics": sorted(({**t, "well_rate": ratio(t["well"], t["n"])} for t in topics.values()), key=lambda t: -t["n"]), "top_questions": top_questions,
               "gaps": gap_list, "thumbs_down": down_list, "feedback_messages": feedback_msgs, "distinct_questions": len(qs),
               "topic_note": "topic is recorded by the server since the date in Data notes; older questions are classified from their text with the same rules (inferred)"}

    # ---- quality ---------------------------------------------------------------
    by_route = defaultdict(lambda: {"n": 0, "well": 0, "degraded": 0, "no_context": 0, "inferred": 0})
    for e in chats_w:
        r, inf = route_of(e.p)
        b = by_route[r]
        b["n"] += 1
        b["well"] += is_well(e.p)
        b["degraded"] += bool(e.p.get("degraded"))
        b["no_context"] += bool(e.p.get("no_context"))
        b["inferred"] += inf
    n_c = len(chats_w)
    quality = {"well_rate": ratio(cur["well"], n_c), "degraded_rate": ratio(cur["degraded"], n_c),
               "no_context_rate": ratio(sum(1 for e in chats_w if e.p.get("no_context")), n_c),
               "not_found_rate": ratio(sum(1 for e in chats_w if e.p.get("fallback")), n_c),
               "helpful": cur["helpful"], "up": cur["up"], "down": cur["down"], "n": n_c,
               "by_topic": content["topics"], "by_route": [{"route": r, **v, "well_rate": ratio(v["well"], v["n"])} for r, v in sorted(by_route.items(), key=lambda x: -x[1]["n"])]}

    # ---- system & models --------------------------------------------------------
    system = system_section(events, ew, chats_w, show, bucket_keys, bucket_labels, bkey, now, hourly)

    # ---- recent -----------------------------------------------------------------
    recent = []
    for e in reversed([x for x in events if x.name in ("chat_answered",) and show(x)]):
        if len(recent) >= 50:
            break
        r, inf = route_of(e.p)
        recent.append({"ts": e.t.isoformat(), "aud": e.aud, "q": (e.p.get("query") or "")[:90], "route": r, "route_inferred": inf, "model": e.p.get("model"),
                       "ms": e.p.get("total_ms"), "outcome": "degraded" if e.p.get("degraded") else gap_reason(e.p) or "ok", "topic": e.p.get("topic")})

    # ---- audiences, data notes, meta ---------------------------------------------
    audiences = audience_summary(prof, aud_of, aud_src, ew)
    notes = data_notes(events)
    meta = {"days": days, "selector": selector, "tz": "America/Los_Angeles", "now": now.isoformat(), "granularity": "hour" if hourly else "day",
            "window": {"start": w_start.isoformat() if w_start else None, "end": w_end.isoformat()},
            "previous": {"start": p_start.isoformat(), "end": w_start.isoformat()} if p_start else None,
            "rows_loaded": len(rows), "duplicates_removed": dups, "truncated": truncated, "profile_source": profile_source,
            "first_event": events[0].t.isoformat() if events else None, "last_event": events[-1].t.isoformat() if events else None}
    return {"meta": meta, "health": health or {}, "kpis": {"cur": cur, "prev": prev}, "series": series, "adoption": adoption, "funnel": funnel,
            "no_interaction": no_interaction, "cohorts": cohorts, "retention": overall, "usage": usage, "today": today_tl, "engagement": engagement,
            "content": content, "quality": quality, "system": system, "recent": recent, "audiences": audiences, "notes": notes, "diagnostics": diagnostics,
            "top_questions": [[q["key"], q["n"]] for q in top_questions]}


def show_anon(a, aud_of, selector) -> bool:
    au = aud_of.get(a, "user")
    return au != "system" and A.wanted(au, selector)


def _safe(pred, e) -> bool:
    try:
        return bool(pred(e.name, e.p))
    except Exception:
        return False


# ---------------------------------------------------------------- cohorts / retention
def cohort_retention(prof: dict, today: date, weeks: int = 12):
    """Weekly cohorts (Pacific, Monday start) by first-seen day, plus overall D1 / D7 / returned-within-7-days.
    Only browsers old enough to have had the chance to come back are counted in each rate (maturity), so recent cohorts are not penalised."""
    by_week = defaultdict(list)
    for a, pr in prof.items():
        ds = sorted(set(pr["dates"]))
        if not ds:
            continue
        first = pac_date(pr["first"])
        by_week[week_start(first)].append((first, set(ds)))
    cohorts = []
    this_week = week_start(today)
    for ws in sorted(by_week)[-weeks:]:
        members = by_week[ws]
        row = {"week": ws.isoformat(), "n": len(members), "small_n": len(members) < 20, "weeks": []}
        for k in range(0, (this_week - ws).days // 7 + 1):
            lo, hi = ws + timedelta(weeks=k), ws + timedelta(weeks=k, days=6)
            hit = sum(1 for _, ds in members if any(lo <= d <= hi for d in ds))
            row["weeks"].append(ratio(hit, len(members)))
        cohorts.append(row)
    o = {"d1": [0, 0], "d7": [0, 0], "ret7": [0, 0]}
    for members in by_week.values():
        for first, ds in members:
            if (today - first).days >= 1:
                o["d1"][1] += 1
                o["d1"][0] += (first + timedelta(days=1)) in ds
            if (today - first).days >= 7:
                o["d7"][1] += 1
                o["d7"][0] += (first + timedelta(days=7)) in ds
                o["ret7"][1] += 1
                o["ret7"][0] += any(first < d <= first + timedelta(days=7) for d in ds)
    overall = {k: {"hit": v[0], "n": v[1], "rate": ratio(v[0], v[1]), "small_n": v[1] < 20} for k, v in o.items()}
    overall["labels"] = {"d1": "came back the next day", "d7": "came back exactly 7 days later", "ret7": "came back within 7 days"}
    return cohorts, overall


# ---------------------------------------------------------------- hourly timeline for one Pacific date
def day_timeline(events, d: date, selector: str, aud_of: dict) -> dict:
    hours = [{"h": h, "chats": 0, "visitors": set(), "active": set()} for h in range(24)]
    for e in events:
        if e.pt.date() != d or not show_anon(e.anon, aud_of, selector):
            continue
        b = hours[e.pt.hour]
        b["visitors"].add(e.anon)
        if e.name == "chat_answered":
            b["chats"] += 1
        if e.p.get("_src") != "server" and is_interaction(e):
            b["active"].add(e.anon)
    return {"date": d.isoformat(), "hours": [{"h": b["h"], "chats": b["chats"], "visitors": len(b["visitors"]), "active": len(b["active"])} for b in hours],
            "total_chats": sum(b["chats"] for b in hours)}


def timeline_for(rows, date_str: str, selector: str, profiles, overrides) -> dict:
    """Entry point for the 'pick a date' control. `rows` should cover that Pacific day."""
    d = date.fromisoformat(date_str)
    end = datetime.combine(d, datetime.max.time(), tzinfo=PACIFIC).astimezone(UTC)
    return build(rows, end, 0, selector, profiles, overrides)["today"]


# ---------------------------------------------------------------- system section
def system_section(events, ew, chats_w, show, bucket_keys, bucket_labels, bkey, now, hourly) -> dict:
    # attempts (from the richer events; older/simple ones synthesise one ok attempt from model + llm_ms)
    att_rows = []
    for e in chats_w:
        p = e.p
        atts = p.get("attempts")
        if atts:
            for a in atts:
                att_rows.append((e, a))
            if p.get("model") and not any(a.get("o") == "ok" and a.get("m") == p["model"] for a in atts):
                att_rows.append((e, {"m": p["model"], "o": "ok", "ms": p.get("llm_ms")}))
        elif p.get("model"):
            att_rows.append((e, {"m": p["model"], "o": "ok", "ms": p.get("llm_ms")}))
    by_model = defaultdict(lambda: {"attempts": 0, "ok": 0, "kinds": Counter(), "ms_ok": [], "served": 0})
    for e, a in att_rows:
        b = by_model[a.get("m", "?")]
        b["attempts"] += 1
        if a.get("o") == "ok":
            b["ok"] += 1
            if a.get("ms") is not None:
                b["ms_ok"].append(a["ms"])
        else:
            b["kinds"][a.get("k", "other")] += 1
    for e in chats_w:
        if e.p.get("model") and e.p.get("route", "llm") == "llm":
            by_model[e.p["model"]]["served"] += 1
    models_tbl = [{"model": m, "attempts": b["attempts"], "served": b["served"], "ok": b["ok"], "ok_rate": ratio(b["ok"], b["attempts"]), **{k: b["kinds"].get(k, 0) for k in
                  ("rate_limit", "overloaded", "timeout", "connection", "server_error", "other")}, "p50_ms": pct(b["ms_ok"], .5), "p95_ms": pct(b["ms_ok"], .95)}
                  for m, b in sorted(by_model.items(), key=lambda x: -x[1]["attempts"])]
    # model served: per bucket and per hour of day
    served_day = {k: Counter() for k in bucket_keys}
    served_hour = [Counter() for _ in range(24)]
    err_day = {k: Counter() for k in bucket_keys}
    n_known = n_unknown = 0
    for e in chats_w:
        k = bkey(e.t)
        m = e.p.get("model")
        if e.p.get("route", "llm") == "llm" or m:
            lab = m or ("unknown (older event)" if not e.p.get("cached") and not e.p.get("smalltalk") and not e.p.get("degraded") else None)
            if lab:
                served_day.get(k, Counter())[lab] += 1
                served_hour[e.pt.hour][lab] += 1
                n_known += bool(m)
                n_unknown += not m
        if e.p.get("degraded") and not e.p.get("attempts"):
            err_day.get(k, Counter())["degraded (older event: " + str(e.p.get("llm_status", "?")) + ")"] += 1
    for e, a in att_rows:
        if a.get("o") != "ok":
            err_day.get(bkey(e.t), Counter())[a.get("k", "other")] += 1
    llm_chats = [e for e in chats_w if route_of(e.p)[0] == "llm"]
    with_att = [e for e in llm_chats if "route" in e.p]
    fb = [e for e in with_att if e.p.get("fb")]
    fb_reasons = Counter(e.p.get("fb_why", "other") for e in fb)
    routes = Counter(route_of(e.p)[0] for e in chats_w)
    cache_n = routes.get("cache_l1", 0) + routes.get("cache_l2", 0) + routes.get("cache", 0)
    stages = {}
    for key, label in (("embed_ms", "Embedding"), ("vector_ms", "Vector search"), ("calendar_ms", "Calendar lookup"), ("rerank_ms", "Rerank"),
                       ("retrieve_ms", "Retrieval total"), ("ttft_ms", "First token"), ("llm_ms", "Model"), ("total_ms", "Total")):
        vals = [e.p[key] for e in chats_w if e.p.get(key) is not None and not e.p.get("cached")]
        stages[key] = {"label": label, "n": len(vals), "p50": pct(vals, .5), "p95": pct(vals, .95)}
    lat_model = defaultdict(list)
    for e in llm_chats:
        if e.p.get("model") and e.p.get("total_ms") is not None:
            lat_model[e.p["model"]].append(e.p["total_ms"])
    lat_by_model = [{"model": m, "n": len(v), "p50": pct(v, .5), "p95": pct(v, .95)} for m, v in lat_model.items()]
    cold = [e.p["total_ms"] for e in chats_w if e.p.get("cold") is True and e.p.get("total_ms") is not None and not e.p.get("cached")]
    warm = [e.p["total_ms"] for e in chats_w if e.p.get("cold") is False and e.p.get("total_ms") is not None and not e.p.get("cached")]
    inflight = [e.p["inflight"] for e in chats_w if e.p.get("inflight") is not None]
    rss_by_day = {}
    for e in ew:
        if e.p.get("rss_mb") is not None and e.name == "chat_answered":
            k = bkey(e.t)
            rss_by_day[k] = max(rss_by_day.get(k, 0), e.p["rss_mb"])
    # infrastructure events: all audiences on purpose (a dependency failing is not an audience matter)
    dep = Counter()
    dep_day = {k: 0 for k in bucket_keys}
    rl = Counter()
    boots = []
    cerr = Counter()
    vit = defaultdict(lambda: {"lcp": [], "cls": []})
    for e in ew:
        if e.name == "dependency_error":
            dep[e.p.get("dep", "?")] += 1
            k = bkey(e.t)
            if k in dep_day:
                dep_day[k] += 1
        elif e.name == "rate_limited":
            rl[e.p.get("limiter", "?")] += 1
        elif e.name == "server_boot":
            boots.append({"ts": e.t.isoformat(), "version": e.p.get("version"), "reason": e.p.get("reason")})
        elif e.name == "client_error" and show(e):
            cerr[(str(e.p.get("message", ""))[:100])] += 1
        elif e.name == "web_vitals" and show(e):
            d = A.device_of_ua(e.ua)
            if e.p.get("lcp") is not None:
                vit[d]["lcp"].append(e.p["lcp"])
            if e.p.get("cls") is not None:
                vit[d]["cls"].append(e.p["cls"])
    vitals = [{"device": d, "n": len(v["lcp"]), "lcp_p50": pct(v["lcp"], .5), "lcp_p75": pct(v["lcp"], .75),
               "cls_p75": round(sorted(v["cls"])[min(len(v["cls"]) - 1, int(len(v["cls"]) * .75))], 3) if v["cls"] else None}
              for d, v in vit.items()]
    tokens = token_section(events, now)
    # 429s/overload: old events recorded only degraded + llm_status
    old_429 = sum(1 for e in chats_w if e.p.get("degraded") and e.p.get("llm_status") == 429 and "attempts" not in e.p)
    return {"models": models_tbl, "served_by_bucket": [{"k": k, "label": l, "models": dict(served_day[k])} for k, l in zip(bucket_keys, bucket_labels)],
            "served_by_hour": [{"h": h, "models": dict(c)} for h, c in enumerate(served_hour)],
            "errors_by_bucket": [{"k": k, "label": l, "kinds": dict(err_day[k])} for k, l in zip(bucket_keys, bucket_labels)],
            "model_known": n_known, "model_unknown": n_unknown,
            "fallback": {"llm_requests": len(with_att), "fallbacks": len(fb), "rate": ratio(len(fb), len(with_att)), "reasons": dict(fb_reasons),
                         "retried": sum(1 for e in with_att if e.p.get("retries"))},
            "routes": dict(routes), "cache": {"hit_rate": ratio(cache_n, len(chats_w)), "l1": routes.get("cache_l1", 0), "l2": routes.get("cache_l2", 0),
                                                "unknown_layer": routes.get("cache", 0), "n": len(chats_w)},
            "stages": stages, "latency_by_model": lat_by_model,
            "cold": {"cold_n": len(cold), "warm_n": len(warm), "cold_p50": pct(cold, .5), "warm_p50": pct(warm, .5)},
            "inflight": {"p50": pct(inflight, .5), "max": max(inflight) if inflight else None, "n": len(inflight)},
            "memory": {"max_mb": max(rss_by_day.values()) if rss_by_day else None,
                       "by_bucket": [{"k": k, "label": l, "mb": rss_by_day.get(k)} for k, l in zip(bucket_keys, bucket_labels)]},
            "dependency_errors": {"by_dep": dict(dep), "by_bucket": [{"k": k, "label": l, "n": dep_day[k]} for k, l in zip(bucket_keys, bucket_labels)]},
            "rate_limited": dict(rl), "boots": boots[-20:], "client_errors": [[k, v] for k, v in cerr.most_common(10)], "vitals": vitals,
            "tokens": tokens, "old_rate_limit_degraded": old_429}


def token_section(events, now: datetime) -> dict:
    """Groq quota view. ALL audiences count (tests and warm-ups spend the same quota); the day is UTC. Only events that carry model + token fields."""
    per = defaultdict(lambda: defaultdict(lambda: {"tokens": 0, "est": 0, "req": 0}))
    minute = defaultdict(lambda: defaultdict(int))
    first = None
    for e in events:
        if e.name != "chat_answered" or e.p.get("pt") is None or not e.p.get("model"):
            continue
        first = first or e.t
        tk = (e.p.get("pt") or 0) + (e.p.get("ct") or 0)
        d = e.t.astimezone(UTC).date().isoformat()
        cell = per[e.p["model"]][d]
        cell["tokens"] += tk
        cell["req"] += 1
        if e.p.get("tok_src") == "est":
            cell["est"] += tk
        minute[e.p["model"]][e.t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")] += tk
    today = now.astimezone(UTC)
    frac = max(0.04, (today.hour * 3600 + today.minute * 60 + today.second) / 86400)
    out = []
    for m, days_ in per.items():
        td = days_.get(today.date().isoformat(), {"tokens": 0, "est": 0, "req": 0})
        proj = round(td["tokens"] / frac) if td["tokens"] else 0
        out.append({"model": m, "today_used": td["tokens"], "today_est": td["est"], "today_requests": td["req"], "cap": GROQ_DAILY_TOKENS,
                    "pct_of_cap": ratio(td["tokens"], GROQ_DAILY_TOKENS), "projected_today": proj, "projected_exhausted": proj >= GROQ_DAILY_TOKENS,
                    "peak_minute": max(minute[m].values()) if minute[m] else 0, "tpm_cap": GROQ_TPM,
                    "days": [{"day": d, "tokens": v["tokens"], "est": v["est"], "requests": v["req"]} for d, v in sorted(days_.items())[-30:]]})
    return {"models": sorted(out, key=lambda x: -x["today_used"]), "cap": GROQ_DAILY_TOKENS, "tpm_cap": GROQ_TPM, "since": first.isoformat() if first else None,
            "note": "Counts every audience (tests, warm-ups and real users spend the same Groq quota). Day = UTC. Tokens are the provider's usage numbers when "
                    "returned, otherwise a labelled estimate (chars/3.6)."}


# ---------------------------------------------------------------- audiences, notes
def audience_summary(prof, aud_of, aud_src, ew) -> dict:
    w_by = defaultdict(lambda: {"events": 0, "chats": 0, "ids": set()})
    for e in ew:
        b = w_by[e.aud]
        b["events"] += 1
        b["chats"] += e.name == "chat_answered"
        b["ids"].add(e.anon)
    all_by = defaultdict(lambda: {"ids": 0, "events": 0, "chats": 0})
    srcs = Counter()
    for a, pr in prof.items():
        au = aud_of.get(a, "user")
        b = all_by[au]
        b["ids"] += 1
        b["events"] += pr["events"]
        b["chats"] += pr["chats"]
        srcs[(au, aud_src.get(a))] += 1
    return {"window": {k: {"ids": len(v["ids"]), "events": v["events"], "chats": v["chats"]} for k, v in w_by.items()},
            "all_time": dict(all_by), "sources": [{"audience": k[0], "source": k[1], "ids": v} for k, v in sorted(srcs.items())]}


def data_notes(events) -> list[dict]:
    """When each newer metric started being collected, from the data itself (so it is true even if code and data drift)."""
    def first(pred):
        for e in events:
            try:
                if pred(e):
                    return e.t.isoformat()
            except Exception:
                pass
        return None
    spec = [
        ("Audience stamp on events (user/internal/test/bot)", lambda e: "_aud" in e.p, "measured"),
        ("Serving route (cache layer, canned, guard, llm, degraded)", lambda e: e.name == "chat_answered" and "route" in e.p, "measured"),
        ("Answering model and fallback chain", lambda e: e.name == "chat_answered" and "model" in e.p, "measured"),
        ("Every LLM attempt (status, error kind, latency)", lambda e: "attempts" in e.p, "measured"),
        ("Token usage (provider numbers or labelled estimate)", lambda e: e.name == "chat_answered" and "pt" in e.p, "measured"),
        ("Topic recorded by the server", lambda e: e.name == "chat_answered" and "topic" in e.p, "measured"),
        ("Concurrency, memory, cold start", lambda e: e.name == "chat_answered" and "inflight" in e.p, "measured"),
        ("Dependency errors / rate-limit rejections / server boots", lambda e: e.name in ("dependency_error", "rate_limited", "server_boot"), "measured"),
        ("Visit number and days since first visit", lambda e: e.name == "session_start" and "visit_number" in e.p, "measured"),
        ("Real browser user-agent on chat events", lambda e: e.name == "chat_answered" and e.ua and e.ua != "node", "measured"),
    ]
    out = [{"metric": m, "since": first(f), "kind": k} for m, f, k in spec]
    out += [
        {"metric": "Audience for events before the stamp existed", "since": None, "kind": "inferred", "detail": "golden-/redteam- ids => test; UA patterns => bot; owner overrides apply to every event of a browser"},
        {"metric": "Topic and route for older questions", "since": None, "kind": "inferred", "detail": "derived from the question text and older flags (cached/degraded/smalltalk)"},
        {"metric": "New vs returning", "since": None, "kind": "measured", "detail": "first-seen is the browser's earliest event in the whole table, not the selected window"},
        {"metric": "Time of day", "since": None, "kind": "measured", "detail": "America/Los_Angeles; tokens use the UTC day because that is Groq's quota day"},
    ]
    return out


# ---------------------------------------------------------------- drill-down / audience management endpoints
def inspect_question(rows, key: str, selector: str, profiles, overrides, limit: int = 15) -> list[dict]:
    rows, _ = dedupe(rows)
    prof_aud = _aud_map(rows, profiles, overrides)
    out = []
    for r in reversed(sorted(rows, key=lambda r: str(r.get("ts")))):
        p = r.get("props") or {}
        if r.get("event") == "chat_answered" and norm_query(p.get("query", "")) == key:
            au = prof_aud.get(r["anon_id"], "user")
            if au == "system" or not A.wanted(au, selector):
                continue
            route, inf = route_of(p)
            out.append({"ts": r["ts"], "aud": au, "route": route, "route_inferred": inf, "model": p.get("model"), "ms": p.get("total_ms"),
                        "outcome": "degraded" if p.get("degraded") else gap_reason(p) or "ok", "sources": p.get("sources"), "topic": p.get("topic")})
            if len(out) >= limit:
                break
    return out


def _aud_map(rows, profiles, overrides):
    ev_aud, ev_ua = defaultdict(set), defaultdict(set)
    for r in rows:
        p = r.get("props") or {}
        if "_aud" in p:
            ev_aud[r["anon_id"]].add(p["_aud"])
        if r.get("ua"):
            ev_ua[r["anon_id"]].add(r["ua"])
    out = {}
    pm = {x["anon_id"]: x for x in (profiles or [])}
    for a in set(ev_aud) | set(ev_ua) | set(pm):
        if A.is_system_id(a):
            out[a] = "system"
            continue
        x = pm.get(a, {})
        out[a] = A.classify_anon(a, set(x.get("uas") or []) | ev_ua.get(a, set()), set(x.get("auds") or []) | ev_aud.get(a, set()), (overrides.get(a) or {}).get("audience"))[0]
    return out


def heaviest(profiles, overrides, limit: int = 40) -> list[dict]:
    """The browsers with the most events, for the 'who is this?' table. Ids are shown as short hashes; the server resolves them back."""
    out = []
    for r in sorted(profiles or [], key=lambda r: -(r.get("events") or 0))[:limit]:
        a = r["anon_id"]
        ov = overrides.get(a) or {}
        au, src = A.classify_anon(a, set(r.get("uas") or []), set(r.get("auds") or []), ov.get("audience"))
        uas = [u for u in (r.get("uas") or []) if u and not A.UNKNOWN_UA.match(u)]
        out.append({"id": short_id(a), "audience": au, "source": src, "override": ov.get("audience"), "note": ov.get("note", ""), "first": r.get("first_ts"),
                    "last": r.get("last_ts"), "events": r.get("events"), "chats": r.get("chats"), "sessions": r.get("sessions"),
                    "days": len(r.get("active_dates") or []), "device": A.device_of_ua(uas[0] if uas else ""), "ua": (uas[0] if uas else "")[:70]})
    return out


def resolve_short_id(profiles, sid: str) -> str | None:
    for r in profiles or []:
        if short_id(r["anon_id"]) == sid:
            return r["anon_id"]
    return None
