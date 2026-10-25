# 05 - Retrieval and chunk quality

1. Measure recall@5 and MRR on eval_retrieval.py plus the golden set (extend eval_retrieval.py to 100+ cases from golden expect facts: the chunk containing the fact must appear in the top 5).
2. Inspect chunks: find too-long, too-short, duplicated, boilerplate-heavy (nav/footer text), or context-free chunks (no title/section in the text). Improve pipeline/chunker.py (headings carried into chunk text, dedupe near-duplicates, strip boilerplate), rebuild and re-embed only changed chunks.
3. Try cheap wins and keep only those that improve the measured numbers: query expansion tweaks, rerank weight tuning (grid search against the eval), title boosting, source-type priors per intent, MMR to diversify, `top_k`/candidate count.
4. Check that p50/p95 latency does not regress (time 30 queries before/after).
Report the metric table before/after.
