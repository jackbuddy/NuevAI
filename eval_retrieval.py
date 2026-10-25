"""Retrieval regression check: does the context handed to the model contain the answer?

    python eval_retrieval.py            # run all cases against the live Supabase data

Each case passes when a regex is found in the retrieved context (documents + calendar block).
Add cases whenever a real question goes wrong (the /insights page lists them).
"""
import os
import re
import sys
import time
from datetime import date

from dotenv import load_dotenv

load_dotenv()
from query_server import get_supabase           # noqa: E402  (reuses the server's client config)
from rag import Retriever, build_context        # noqa: E402

CASES = [
    # (question, regex that must appear in the context, note)
    ("What is the cell phone policy?", r"(?i)phone", "handbook / policy doc"),
    ("What is the plagiarism policy?", r"(?i)plagiar", "slides / handbook"),
    ("What are the graduation requirements?", r"(?i)graduat", "handbook / website"),
    ("What engineering courses are offered?", r"(?i)engineer", "catalog outline"),
    ("What math courses can I take?", r"(?i)calculus|algebra|geometry", "catalog"),
    ("What performing arts classes are there?", r"(?i)theat|dance|music|choir|orchestra", "catalog"),
    ("What prerequisites does Mixed Media have?", r"Mixed Media", "catalog course entry"),
    ("What clubs can I join?", r"(?i)club", "clubs sheet"),
    ("How do I book a campus tour?", r"(?i)ravenna", "link registry"),
    ("How can I donate to Nueva?", r"(?i)givecampus", "link registry"),
    ("When is the next girls soccer game?", r"(?i)soccer", "calendar: game"),
    ("What time is boys basketball practice?", r"(?i)basketball", "calendar: practice"),
    ("What sports are playing this season?", r"(?i)TEAMS ON THE SCHOOL CALENDAR", "calendar: team overview"),
    ("When is the November break?", r"(?i)november break", "calendar: no school"),
    ("What is happening on November 12?", r"(?i)schedule for thursday, november 12", "calendar: day digest"),
    ("When is the winter concert?", r"(?i)concert", "calendar: arts"),
    ("Are there any upper school basketball games coming up?", r"US Girls Basketball - Game", "calendar: US sheet games"),
    ("When does the girls volleyball team play next?", r"US Girls Volleyball - Game", "calendar: US sheet games"),
    ("What clubs can I join?", r"clubs and affinity groups \(\d+ listed\)", "clubs outline"),
    ("What happens if I'm late to class?", r"(?i)tard", "synonym expansion -> attendance policy"),
    ("What time is boys basketball practice?", r"PRACTICE SCHEDULE", "calendar: practice overview"),
    # master schedule, course map, clubs, team schedules (facts checked against the source workbooks)
    ("Who teaches Chemistry?", r"David Eik", "teacher map: course -> teacher"),
    ("What does Wes Chao teach?", r"Computer Vision", "teacher map: teacher -> courses"),
    ("What room is Computer Security in?", r"(?i)room 240", "teacher map: course -> room"),
    ("What block is Software Engineering?", r"Block 2", "teacher map: course -> block"),
    ("Is October 5 an A day or B day?", r"October 5, 2026 - A day", "A/B day calendar"),
    ("Is there school on Labor Day?", r"Labor Day", "no-class day"),
    ("When is advisory for juniors on August 20?", r"Juniors: Advisory", "advisory by grade"),
    ("Where does advisory 9.1 meet?", r"(?i)advisory 9\.1.*room 215", "advisor rooms"),
    ("Who advises the AI/ML club?", r"Matt Hesby", "clubs: advisor"),
    ("Who leads the AMC/AIME club?", r"Alex Yang", "clubs: leaders"),
    ("When is the next US boys basketball game?", r"US Boys Basketball (Varsity|JV) - Game", "live team schedule"),
    ("What courses are offered in Block 3?", r"Block 3 .*offerings", "offerings by block"),
    ("What time is Block 3?", r"12:35", "bell schedule fact"),
    ("What order are the blocks on an A' day?", r"1, 2, 4, 3", "A' order fact"),
    ("What time is Block 7 tomorrow?", r"B' day.*Block 8 12:35-1:50 PM; Block 7 1:55-3:10 PM", "per-day block times (B' flips 7/8)"),
    ("Who teaches Chemistry?", r"Jeremy Jacquot", "confirmed spelling"),
    ("Where is the upper school located?", r"131 E 28th", "campus fact"),
    ("What is the phone number for the front desk?", r"650-235-7100", "campus fact"),
    # added by golden round 1: direct lookups and pinned facts
    ("What time is Block 2?", r"10:15-11:30", "bell facts pinned even though the question parses as a date question"),
    ("When does the school day end?", r"1:55-3:10", "bell facts pinned"),
    ("What time is lunch?", r"11:30 AM and ends at 12:00", "bell facts pinned (not just the lunch synonyms)"),
    ("What's the phone number for the Hillsborough campus?", r"650-350-4600", "campus facts pinned"),
    ("Who teaches art of repair", r"Hilary Fenton", "course lookup without the leading 'the'"),
    ("who teaches psychology", r"Amy Hunt", "course lookup by a single distinctive word"),
    ("Who teaches Chinese 5?", r"Ting Zhang", "course lookup: prefix of a longer course name"),
    ("Who teaches philsophy of consioucness", r"Lee Holtzman", "course lookup tolerates typos and abbreviations"),
    ("Who teaches Linguistics?", r"Theodosopoulos", "course lookup: abbreviated catalog name"),
    ("Which room is advisory 11.1?", r"Advisory 11\.1: Alexa Hart and Jen Paull, room 1102", "advisory number lookup"),
    ("Who are the advisors of advisory 11.5?", r"Sarah Ruddy and Terry Lee, room 316", "advisory number lookup"),
    ("When does Model UN meet?", r"Tuesday, Lunch in room 1103", "club alias lookup"),
    ("Who advises the AMC/AIME club and when does it meet?", r"(?i)room 318", "club acronym lookup"),
    ("Who is the advisor?", r"Patrick Berger", "club lookup from the previous question", [{"role": "user", "content": "When does Model UN meet?"}]),
    ("What kind of day is November 6, 2026?", r"November 6, 2026 - B' day", "'kind of day' is not a keyword search"),
    ("What about the next day?", r"October 6, 2026 - B day", "'the next day' follow-up resolves the date", [{"role": "user", "content": "Is October 5 an A day or B day?"}]),
    ("When is the Spring Sports Athlete/Parent meeting?", r"US Spring Sports Athlete/Parent Meeting", "named athletics event is not a team overview"),
    ("When is Thanksgiving and when is the November break?", r"Thursday, November 26, 2026, all day: Thanksgiving", "named holiday inside a no-school question"),
    ("On the calendar it shows a fika, what is it?", r"US Parent Fika", "any-keyword fallback when the strict keyword match is empty"),
    ("wat time is blok 3", r"12:35-1:50", "typo 'blok' still pins the bell facts"),
    ("What languages can I study?", r"(?i)chinese", "language synonym expansion"),
    # added by usage review (prompt 04): real questions from alpha testers
    ("Who teaches meche", r"(?s)Mechanical Engineering \(Fall 2026\).*Dan Norgan", "student shorthand 'meche' resolves to the course"),
    ("how long has dan norgren worked here", r"Dan Norgan", "misspelled last name next to the right first name"),
    ("Who teaches Mechanical Engineering - which blocks and rooms?", r"Block 1 \(A/A' days\): Dan Norgan, room 112", "course sections"),
    ("Who leads HTI club", r"(?i)Human Technology Interaction", "club acronym"),
    ("who is the leader of chemlab club", r"(?i)chem", "club lookup without 'the' prefix"),
    ("When does the ethics bowl club meet", r"(?i)ethics bowl", "club lookup"),
    ("What is the cell phone policy? can have my phone in class", r"(?i)phone", "ungrammatical phone question"),
    ("Who leads it?", r"(?i)ethics bowl", "club follow-up", [{"role": "user", "content": "When does the ethics bowl club meet"}]),
    # added by the source-of-truth audit (prompt 10): parsing bugs and source conflicts found by scripts/audit_data.py
    ("What kind of day is February 24, 2027?", r"February 24, 2027 - A day: Blocks 1-4", "date cell typed as text ('24 Community (90')') was skipped by the day parser"),
    ("Is there school on June 4, 2027?", r"Last Day of School\. Noon Dismissal \(early dismissal", "last day is a noon-dismissal day, not 'No classes'"),
    ("Is there school on October 28?", r"October 28, 2026 - No classes \(Parent Teacher Conferences\)", "no-classes day from the master sheet (the iCal feed has no no-school event)"),
    ("Is there school on November 18?", r"Full-day special event: CSL Day", "full-day event, not 'No classes'"),
    ("Who teaches Biology Research Teams 2?", r"(?s)\A(?!.*TEAMS ON THE SCHOOL CALENDAR).*Jehnna Ronan.*Lindy Jensen", "joint class 'Jensen + Ronan' split into two teachers; 'Teams' in a course name is not a sports question"),
    ("What does Laura Mendez-Ortiz teach?", r"Block 7 \(B/B' days\): Spanish 3, room 1206", "Roles tab wins over the offerings sheet for the same teacher and block"),
    ("What does Laura Mendez-Ortiz teach?", r"(?s)\A(?!.*Block 7 \(B/B' days\): Spanish 4)", "offerings entry that double-books a Roles block is not shown"),
    ("What does Laura Mendez-Ortiz teach?", r"listed only on the course offerings sheet", "unconfirmed offerings-only assignment is labelled"),
    ("When does daylight saving time end?", r"Sunday, November 1, 2026, all day: Daylight Saving Time Ends", "all-day event with a timed DTEND keeps its date"),
    ("What is the 2025-26 year at a glance calendar?", r"(?s)\A(?!.*2025.2026 Year-at-a-Glance).*2026.2027 Year-at-a-Glance", "current-year calendar document wins over last year's copy"),
]


def golden_cases() -> list:
    """Every golden question whose expectation is a plain fact (not a refusal phrasing) must have that fact
    in the retrieved context. Built from eval_golden.json so the set grows with the golden set."""
    import json
    from pathlib import Path
    from eval_golden import normalize, pattern
    from eval_recall import usable
    seen = {(c[0].lower(), repr(c[3] if len(c) > 3 else None)) for c in CASES}
    out = []
    for g in json.loads((Path(__file__).parent / "eval_golden.json").read_text()):
        k = (g["q"].lower(), repr(g.get("history")))
        if not usable(g) or k in seen:
            continue
        seen.add(k)
        out.append((g["q"], g["expect"], f"golden/{g['category']}", g.get("history")))
    return out


def has_facts(ctx: str, rx) -> bool:
    if isinstance(rx, str):
        return bool(re.search(rx, ctx))
    from eval_golden import normalize, pattern
    ctx = normalize(ctx)
    return all(any(pattern(a).search(ctx) for a in (e if isinstance(e, list) else [e])) for e in rx)


def main() -> int:
    CASES.extend(golden_cases())
    r = Retriever(get_supabase)
    r.embed("warm up")
    # fixed date: the calendar checks refer to dates in the 2026-27 school year
    today = date.fromisoformat(os.getenv("EVAL_TODAY", "2026-10-22"))
    passed = 0
    for case in CASES:
        q, rx, note = case[:3]
        history = case[3] if len(case) > 3 else None
        t = time.time()
        res = r.retrieve(q, history, today=today)
        ctx, _ = build_context(res)
        ok = has_facts(ctx, rx)
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {int((time.time() - t) * 1000):5}ms  {q[:52]:54} [{note}]"
              f"{'' if ok else '   docs=' + str([ (d.get('metadata') or {}).get('title', '')[:25] for d in res['docs'][:3]])}")
    print(f"\n{passed}/{len(CASES)} passed")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
