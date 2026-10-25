# 04 - Learn from real usage

Use the Supabase MCP to query `analytics_events` (read-only SELECTs) for the last 14 days: questions with no/low sources, thumbs-down, feedback_text, errors, slow answers (>5 s), repeated rephrasings (users asking the same thing twice), and top question clusters.
For each distinct problem: reproduce against local backend, fix at the right layer (see prompt 02), add a golden-set and retrieval case. Ignore rows containing "[deploy check". Write `data/eval/usage_review_<date>.md` summarizing clusters, fixes, and what still needs a human (missing docs go in NEEDS_USER.md as specific download requests). If there is no real traffic yet, say so and instead mine eval_answers.py plus golden failures.
