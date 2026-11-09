"""Audience classification: who produced an event?  user | internal | test | bot  (plus `system` for server-only events).

Decided at ingestion (stamped on the event as props._aud / props._why) and re-derivable for history from the same rules, so rows
written before this module existed are classified deterministically and labelled `inferred` in the dashboard.

Precedence, per anon_id (a browser):  override (owner-managed table)  >  test  >  internal  >  bot  >  user.
Everything is anon_id/UA based. No IPs, no personal data.
"""
import re

AUDIENCES = ("user", "internal", "test", "bot")

# Our own harnesses. New scheme: test-<harness>-<id>. Legacy ids that exist in history are still recognised.
TEST_PREFIXES = ("test-", "golden-", "redteam-", "eval-", "bench-", "smoke-", "e2e-", "ci-", "deploycheck", "deploych")
SYSTEM_PREFIX = "system-"
SYSTEM_ID = "system-server"

# Clearly automated clients. Bare "node"/"undici" is NOT here: it is what the Next.js proxy sends when a browser's UA was not forwarded
# (all history before 2026-11 looks like that), so it says nothing about the person.
BOT_UA = re.compile(
    r"(bot\b|bots\b|crawl|spider|slurp|scrap|headless|phantom|puppeteer|playwright|selenium|lighthouse|pagespeed|"
    r"python-requests|python-urllib|python-httpx|aiohttp|httpx|urllib|curl/|wget|libwww|go-http-client|okhttp|java/|apache-httpclient|"
    r"axios|got \(|postman|insomnia|"
    r"facebookexternalhit|twitterbot|slackbot|discordbot|whatsapp|telegrambot|linkedinbot|skypeuripreview|embedly|"
    r"preview|monitor|uptime|pingdom|statuscake|render-health|googlebot|google-read|google-inspectiontool|^google$|bing|yandex|baidu|duckduck)",
    re.I)
UNKNOWN_UA = re.compile(r"^(node|undici|node-fetch|)$", re.I)
OLD_BROWSER = re.compile(r"(chrome/(?:[1-9]|[1-5]\d|6\d|7\d|8\d)\.|msie |trident/)", re.I)   # <= Chrome 89 / IE: crawlers and ancient embeds, only a hint


def is_test_id(anon_id: str) -> bool:
    return (anon_id or "").lower().startswith(TEST_PREFIXES)


def is_system_id(anon_id: str) -> bool:
    return (anon_id or "").startswith(SYSTEM_PREFIX)


def ua_kind(ua: str) -> str:
    """'bot' | 'unknown' (UA missing or proxy default) | 'browser'."""
    ua = (ua or "").strip()
    if UNKNOWN_UA.match(ua):
        return "unknown"
    if BOT_UA.search(ua):
        return "bot"
    return "browser"


def device_of_ua(ua: str, viewport_w: int | None = None) -> str:
    if viewport_w:
        return "mobile" if viewport_w < 640 else "tablet" if viewport_w < 1024 else "desktop"
    u = (ua or "").lower()
    if "ipad" in u or "tablet" in u:
        return "tablet"
    if "iphone" in u or "android" in u and "mobile" in u or "mobile" in u:
        return "mobile"
    if not u or UNKNOWN_UA.match(u):
        return "unknown"
    return "desktop"


def event_audience(anon_id: str, ua: str = "", flags: dict | None = None) -> tuple[str, str]:
    """(audience, why) for ONE event at ingestion. `flags` is the client envelope: {internal, webdriver, dev, test}."""
    flags = flags or {}
    if is_system_id(anon_id):
        return "internal", "system"
    if is_test_id(anon_id):
        return "test", "id_prefix"
    if flags.get("test") or flags.get("dev"):
        return "test", "dev_host" if flags.get("dev") else "test_flag"
    if flags.get("internal"):
        return "internal", "flag"
    if flags.get("webdriver"):
        return "bot", "webdriver"
    if ua_kind(ua) == "bot":
        return "bot", "ua"
    return "user", "default"


_RANK = {"test": 3, "internal": 2, "bot": 1, "user": 0}


def classify_anon(anon_id: str, uas=(), stamped=(), override: str | None = None) -> tuple[str, str]:
    """(audience, source) for a whole browser across its history.
    `stamped`: the ingestion-time `_aud` values seen on its events (measured). `uas`: its distinct user agents (used for inference only
    when nothing was stamped). source: override | measured | inferred."""
    if override in AUDIENCES:
        return override, "override"
    stamped = [s for s in stamped if s in _RANK]
    if stamped:
        best = max(stamped, key=lambda s: _RANK[s])
        # one internal flag anywhere in a browser's history makes the whole browser internal, even older events
        if best != "user":
            return best, "measured"
    if is_test_id(anon_id):
        return "test", "inferred"
    if any(ua_kind(u) == "bot" for u in uas):
        return "bot", "inferred"
    return "user", "measured" if stamped else "inferred"


def wanted(audience: str, selector: str) -> bool:
    """Dashboard selector: users | users_internal | test_bots | all."""
    if selector == "all":
        return True
    if selector == "users_internal":
        return audience in ("user", "internal")
    if selector == "test_bots":
        return audience in ("test", "bot")
    return audience == "user"
