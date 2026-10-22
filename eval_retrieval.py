"""Retrieval regression check: does the context handed to the model contain the answer?

    python eval_retrieval.py            # run all cases against the live Supabase data

Each case passes when a regex is found in the retrieved context (documents + calendar block).
Add cases whenever a real question goes wrong (the /insights page lists them).
"""
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

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
]


def main() -> int:
    r = Retriever(get_supabase)
    r.embed("warm up")
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
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
