"""Stage 4: build the registry of important links (booking, forms, payments...).

Every anchor from every crawled page is classified from its URL host and its
visible text. Useful ones become 'link card' chunks so a question like
"how do I book a tour?" retrieves the actual URL, not just prose about it.
"""
import json
import re
from collections import defaultdict

from . import config
from .common import load_pages

TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_)", re.I)
BORING_TEXT = re.compile(r"^(click here|here|link|read more|learn more|more|view|open|\s*)$", re.I)


def classify(url: str, text: str, context: str):
    hay_text = f"{text} {context[:160]}"
    for cat, host_rx, kw_rx in config.LINK_RULES:
        host_hit = host_rx.search(url)
        if cat == "contact":
            if host_hit:
                return cat
            continue
        # a known scheduling/forms host is enough; otherwise need host AND wording,
        # except the wording-only booking case for same-site "book a tour" style buttons.
        if host_hit and (cat in ("booking", "form", "payment") or kw_rx.search(hay_text)):
            return cat
    if re.search(r"\b(book(ing)?|schedule) (a |your )?(tour|visit|appointment|meeting|conference)\b", hay_text, re.I):
        return "booking"
    if url.startswith("http") and config.PUBLIC_HOST not in url and not config.SOCIAL_HOSTS.search(url):
        for cat, rx in config.INTENT_RULES:
            if rx.search(hay_text):
                return cat
    return None


def clean_url(url: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlparse
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    return p._replace(query=urlencode(q)).geturl()


def build(log=print) -> list[dict]:
    found = {}
    anchors = defaultdict(set)
    for page in load_pages():
        for l in page.get("links", []):
            url = clean_url(l["url"])
            if config.STALE_URL.search(url) and "docs.google.com" not in url:
                continue
            if url.startswith(("mailto:", "tel:")) and page["source"] != "public_site":
                continue
            cat = classify(url, l["text"], l["context"])
            if not cat:
                continue
            if l["text"] and not BORING_TEXT.match(l["text"]):
                anchors[url].add(l["text"])
            rec = found.setdefault(url, {"url": url, "category": cat, "label": "", "found_on": [], "context": ""})
            if page["url"] not in [f["url"] for f in rec["found_on"]]:
                rec["found_on"].append({"url": page["url"], "title": page["title"]})
            if len(l["context"]) > len(rec["context"]):
                rec["context"] = l["context"]
    for p in config.PINNED_LINKS:
        found[p["url"]] = {"url": p["url"], "category": p["category"], "label": p["label"], "pinned": True, "context": p["note"],
                           "found_on": [{"url": "https://www.nuevaschool.org/", "title": "The Nueva School website"}]}
    for url, rec in found.items():
        if rec.get("pinned"):
            rec["alt_labels"] = []
            continue
        names = sorted(anchors[url], key=len)
        rec["label"] = names[-1] if names else rec["found_on"][0]["title"]
        rec["alt_labels"] = names[:5]
    out = sorted(found.values(), key=lambda r: (r["category"], r["label"].lower()))
    config.LINKS_FILE.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    by_cat = defaultdict(int)
    for r in out:
        by_cat[r["category"]] += 1
    log(f"links registry: {len(out)} important links {dict(by_cat)} -> {config.LINKS_FILE}")
    return out


def link_cards(links: list[dict]):
    """One small chunk per useful link, written the way a user would ask for it."""
    for r in links:
        if r["category"] not in config.LINK_CARD_CATEGORIES:
            continue
        kind = {"booking": "Booking / sign-up link", "form": "Form link", "payment": "Payment / store link",
                "calendar": "Calendar link", "document": "Document link", "portal": "Portal link"}[r["category"]]
        page = r["found_on"][0]
        how = {"booking": "Use this link to book, schedule, reserve, register or sign up (tour, visit, event, appointment).",
               "form": "Use this link to fill out or submit the form.", "payment": "Use this link to pay, donate, give, or buy.",
               "calendar": "Use this link to view the calendar.", "document": "Use this link to open the document.",
               "portal": "Use this link to sign in to the portal."}[r["category"]]
        lines = [f"{kind}: {r['label']}", how, f"URL: {r['url']}"]
        if r["alt_labels"] and len(r["alt_labels"]) > 1:
            lines.append("Also called: " + "; ".join(r["alt_labels"]))
        if r["context"]:
            lines.append(f"Context: {r['context']}")
        lines.append(f"Found on: {page['title']} ({page['url']})")
        yield {"source_url": r["url"], "title": f"{kind}: {r['label']}", "source": "link_registry",
               "text": "\n".join(lines), "link_category": r["category"], "label": r["label"], "found_on": page["url"], "pinned": bool(r.get("pinned"))}
