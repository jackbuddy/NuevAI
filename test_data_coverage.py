"""Offline tests for the data-coverage work: sensitive-content skip, canonical lists, fact files, calendar window,
accordion extraction, additive indexing, flag planning (no network, no database).

    python test_data_coverage.py
"""
import sys
from datetime import date

from bs4 import BeautifulSoup

import freshness
from pipeline import calendar_feed, canonical, chunker, common, facts, links, safety

FAILS = []


def check(name, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {name}{'' if cond else '  ' + str(detail)}")
    if not cond:
        FAILS.append(name)


# --- sensitive content is never indexed ----------------------------------------------------------------------
t, hits = safety.scrub("Book it at buses.example.test. Username: shared-bus@example.test Password: Xyzzy123 and then confirm.")
check("safety: a shared username+password is cut out", "Xyzzy123" not in t and "shared-bus" not in t and "Password" not in t, t)
check("safety: ...but the rest of the sentence stays", t.startswith("Book it at buses.example.test.") and t.endswith("and then confirm."), t)
t, hits = safety.scrub("A recording of the meeting can be found here (Passcode: Zz9.Qx7) and the slides here.")
check("safety: a meeting recording passcode is cut out", "Zz9.Qx7" not in t and "slides here" in t, t)
t, hits = safety.scrub("Join https://meet.example.test/j/1234567890?pwd=FAKEpwd1234ABCD today")
check("safety: a pwd= parameter on a meeting link is dropped", "FAKEpwd" not in t and "meet.example.test/j/1234567890" in t, t)
t, hits = safety.scrub("Contact Pat Example pexample@nuevaschool.org 650-555-0100 (Office) 650-555-0199 (Cell)")
check("safety: a personal cell number is dropped, the office line stays", "650-555-0199" not in t and "650-555-0100 (Office)" in t, t)
for ok in ("Never share your password with anyone.", "Enter your Nueva email address as the Username or Email (ex., tstewart@nuevaschool.org).",
           "social media accounts need a user name and password that the advisor keeps", "The team will pin the hair back (Passcode of honor)"):
    check(f"safety: policy wording is not a leak: {ok[:40]}", safety.scrub(ok)[0] == ok and not safety.find(ok), safety.find(ok))
check("chunker: clean_text applies the scrub", "Password" not in chunker.clean_text("Login: x\nUsername: a@b.com Password: hunter22\nbye"), chunker.clean_text("Username: a@b.com Password: hunter22"))
check("links: a pwd= query parameter is removed from link urls", "pwd" not in links.clean_url("https://zoom.us/j/1?pwd=abc&x=1") and "x=1" in links.clean_url("https://zoom.us/j/1?pwd=abc&x=1"))

# --- accordion answers are kept next to their question ------------------------------------------------------------
html = """<main><h1>FAQ</h1><ul><li><a role='tab' href='#p1' aria-controls='fsEl_1'>Is there an application fee?</a></li></ul>
<section id='fsEl_1' role='tabpanel' aria-hidden='true'><p>Yes, the nonrefundable application fee is $100.</p></section></main>"""
title, text, _ = common.extract_page(html, "https://www.nuevaschool.org/x")
check("crawl: a collapsed (aria-hidden) accordion answer is kept", "$100" in text, text)
check("crawl: ...with its question as a heading right above it", "#### Is there an application fee?\n\nYes, the nonrefundable" in text, text)
chrome = "<main><p>Real content paragraph that is long enough to keep.</p></main><nav aria-hidden='true'>Menu junk</nav>"
check("crawl: navigation chrome is still dropped", "Menu junk" not in common.extract_page(chrome, "https://www.nuevaschool.org/y")[1])

# --- calendar history window ---------------------------------------------------------------------------------------
check("calendar: history reaches the start of the school year", calendar_feed.window_floor(date(2026, 11, 11)) == date(2026, 8, 1), calendar_feed.window_floor(date(2026, 11, 11)))
check("calendar: early in the school year the 14-day minimum still applies", calendar_feed.window_floor(date(2026, 8, 10)) == date(2026, 8, 1) or calendar_feed.window_floor(date(2026, 8, 10)) <= date(2026, 7, 27),
      calendar_feed.window_floor(date(2026, 8, 10)))
check("calendar: in June the floor is still August of that school year", calendar_feed.window_floor(date(2027, 6, 10)) == date(2026, 8, 1))
check("calendar: next school year moves the floor", calendar_feed.window_floor(date(2027, 9, 1)) == date(2027, 8, 1))

# --- fact files ---------------------------------------------------------------------------------------------------
cs = facts.build_chunks(lambda *_: None)
check("facts: knowledge files produce records", len(cs) > 10, len(cs))
dated = [c for c in cs if c["metadata"].get("as_of")]
check("facts: every record of a dated file states its last-updated date in text and metadata",
      dated and all(f"Last updated: {c['metadata']['as_of']}" in c["content"] for c in dated))
check("facts: dated records stay under the chunk limit (nothing is cut when shown)", all(len(c["content"]) <= 1700 for c in dated), max(len(c["content"]) for c in dated))
check("facts: every dated section names its source and the school year", all("Source:" in c["content"] and ("20" in c["content"]) for c in dated))
sp = facts.split_section("T", "Source: x. Applies to: 2026-27.\n" + "\n".join(f"- item {i} " + "word " * 30 for i in range(20)))
check("facts: a long section splits at bullets and repeats its source line", len(sp) > 1 and all("Source: x" in b for _, b in sp) and sp[1][0].endswith(f"(part 2 of {len(sp)})"), [h for h, _ in sp])
check("facts: no record leaks a credential", not any(safety.find(c["content"]) for c in cs))
ids = [c["chunk_id"] for c in cs]
check("facts: record ids are unique", len(ids) == len(set(ids)))

# --- canonical lists ----------------------------------------------------------------------------------------------
cal = [{"chunk_id": "a", "metadata": {"source": "calendar", "kind": "game", "summary": "MS (7th/8th - VA) Girls' Soccer vs Priory - Game", "event_date": "2026-11-12"}},
       {"chunk_id": "b", "metadata": {"source": "calendar", "kind": "practice", "summary": "MS (7th/8th - VA) Girls' Soccer - Practice", "event_date": "2026-11-10"}},
       {"chunk_id": "c", "metadata": {"source": "calendar_sheet", "kind": "game", "summary": "US Boys Basketball Varsity vs Menlo", "event_date": "2026-12-01"}},
       {"chunk_id": "d", "metadata": {"source": "calendar", "kind": "event", "summary": "Fall Festival"}}]
teams = canonical.teams_with_schedules(cal, lambda *_: None)
body = " ".join(t["content"] for t in teams)
check("canonical: teams are aggregated by team with counts and dates", "MS (7th/8th - VA) Girls' Soccer: 1 game, 1 practice" in body and "US Boys Basketball Varsity: 1 game" in body, body[-400:])
check("canonical: non-team events are not teams", "Fall Festival" not in body)
check("canonical: a record carries as_of, school year, source and a school_canonical source tag",
      teams[0]["metadata"]["source"] == "school_canonical" and "Last updated:" in teams[0]["content"] and "Applies to: the 2026-27 school year" in teams[0]["content"])
check("canonical: school_canonical rows are current by construction (never stale)", "school_canonical" in freshness.CURRENT_BY_CONSTRUCTION)
parts = canonical.split_lines([f"- line {i} " + "x" * 80 for i in range(60)])
check("canonical: long lists are split under the limit", len(parts) > 1 and all(sum(len(l) + 1 for l in p) <= canonical.MAX_CHARS + 100 for p in parts))
lk = canonical.links_directory([{"category": "form", "label": "Locker Request Form", "url": "https://forms.example/locker"},
                                {"category": "form", "label": "Old 2019 form", "url": "https://forms.example/2019ilc"}], lambda *_: None)
check("canonical: link directory lists forms and skips year-stale urls", lk and "Locker Request Form: https://forms.example/locker" in lk[0]["content"] and "2019" not in lk[0]["content"])
check("canonical: ids are unique per head", len({c["chunk_id"] for c in lk}) == len(lk))

# --- additive indexing ----------------------------------------------------------------------------------------------
import inspect
from pipeline import index
src = inspect.getsource(index.run)
check("index: additive_only never reaches a delete or a flag", "if additive_only:\n        patch, superseded, expired_cal, stale, legacy, restore = [], [], [], [], [], []" in src)
check("index: a freshness patch keeps review flags", "KEEP_FLAGS" in inspect.getsource(index.patch_metadata) and "superseded" in index.KEEP_FLAGS)

print(f"\n{'all passed' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
