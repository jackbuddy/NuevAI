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
]


def main() -> int:
    r = Retriever(get_supabase)
    r.embed("warm up")
    # fixed date: the calendar checks refer to dates in the 2026-27 school year
    today = date.fromisoformat(os.getenv("EVAL_TODAY", "2026-10-22"))
    passed = 0
    for q, rx, note in CASES:
        t = time.time()
        res = r.retrieve(q, today=today)
        ctx, _ = build_context(res)
        ok = bool(re.search(rx, ctx))
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {int((time.time() - t) * 1000):5}ms  {q[:52]:54} [{note}]"
              f"{'' if ok else '   docs=' + str([ (d.get('metadata') or {}).get('title', '')[:25] for d in res['docs'][:3]])}")
    print(f"\n{passed}/{len(CASES)} passed")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
