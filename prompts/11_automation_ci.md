# 11 - Continuous quality automation

1. GitHub Actions: `ci.yml` on PR/push running py_compile, eval_retrieval.py, tsc, and next build. `nightly-quality.yml` running eval_golden.py against the LIVE backend at low concurrency, uploading the report as an artifact, and failing (so GitHub emails the owner) if the score drops below the stored baseline minus a tolerance. Use only existing secrets; no new paid services. Never print secrets in logs.
2. Extend the daily refresh job to also run `scripts/audit_data.py` after indexing and to fail loudly if the index row count drops more than 10% (guard against bad scrapes wiping data).
3. Add `scripts/weekly_digest.py` that prints a markdown digest from analytics (usage, top questions, failures, feedback) so the user can paste it into a chat. Do not email anything.
4. Update README.md "Operations" with how each automation works and how to rerun it.
