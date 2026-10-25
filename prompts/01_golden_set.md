# 01 - Build a golden question set and a grader

Goal: replace guesswork about accuracy with a measurable score.

1. Pull real questions: use the Supabase MCP (`analytics_events`, question events), `/api/metrics` locally if possible, and the 35 in eval_answers.py. Add synthetic questions covering every data source (courses, teachers+blocks+rooms, A/B/A'/B' days, bell times, sports by team/gender/home-away, clubs+leaders, advisory, calendar events, links like tours/giving, policies, campus addresses/phones, off-topic, greetings, ambiguous, typo-ridden, multi-part, follow-ups). Target 150+ questions.
2. For each, write `expect` facts (strings/regex that MUST appear) and `forbid` facts (must NOT appear, e.g. student names, last year's dates) in `eval_golden.json`, grounded in the actual data files, not in what the bot currently says. Verify each expected fact against source data yourself.
3. Write `eval_golden.py`: runs the set against a base URL (default local), respects rate limits (serial or low concurrency with sleeps), scores pass/fail per question, prints a table by category, writes `data/eval/golden_<timestamp>.json`, exits nonzero below a threshold.
4. Run it, record the baseline score in QUEUE_LOG.md, and list the failing categories. Do not fix them in this prompt except trivial data typos.
