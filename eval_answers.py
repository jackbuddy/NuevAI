"""Read-the-answers check: runs realistic questions through the real backend and prints what a user would see.

    python eval_answers.py [base_url]     # default http://localhost:8765
Use it after changing prompts, retrieval or data. Judge the answers by eye; eval_retrieval.py is the automated check.
"""
import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765"
QUESTIONS = [
    # calendar & school days
    "What's happening at school today?", "Is there school next Monday?", "When does winter break start?", "When are parent-teacher conferences?",
    "When is the last day of school?", "What events are coming up this week?",
    # sports
    "When is the next girls soccer game?", "What time is boys basketball practice?", "Who do the middle school boys basketball teams play at home?",
    "What sports can I play in the winter?", "Are there any upper school sports games coming up?",
    # courses
    "What computer science courses are offered?", "What is Software Engineering about?", "What are the prerequisites for Intro to Machine Learning?",
    "What art classes can I take?", "Are there any courses for 9th graders in math?", "What languages can I study?",
    # policies & handbook
    "What is the cell phone policy?", "What is the attendance policy?", "What happens if I'm late to class?", "What is the plagiarism policy?", "Can I use AI on assignments?",
    "What is the dress code?",
    # admissions & families
    "How do I book a campus tour?", "How do I apply to Nueva?", "How can I donate?", "Where can I buy Nueva clothing?",
    # campus & clubs
    "What clubs can I join?", "How do I start a club?", "How do I contact the school?", "Where is the upper school located?",
    # meta / edge
    "Hi", "What can you do?", "What's the weather today?", "Tell me a joke",
]


def ask(q):
    req = urllib.request.Request(BASE + "/api/chat", data=json.dumps({"query": q}).encode(), headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=120).read())
    except Exception as e:
        return {"answer": f"ERROR {e}", "sources": [], "ms": int((time.time() - t) * 1000)}
    d["ms"] = int((time.time() - t) * 1000)
    return d


if __name__ == "__main__":
    for q in QUESTIONS:
        d = ask(q)
        print(f"\n### {q}   [{d['ms']}ms, {len(d.get('sources', []))} sources, {d.get('model_used', '')}{', cached' if d.get('cached') else ''}]")
        print(d["answer"][:700])
        time.sleep(4)   # stay under the LLM token-per-minute limit
