# NuevAI

A question-answering assistant for The Nueva School community: courses, the school calendar and game
schedules, policies, and campus resources.

```
app/ components/ lib/      Next.js frontend (Vercel): Chat, Calendar, Resources, /insights metrics
query_server.py            FastAPI backend (Render, Dockerfile): streaming chat, calendar + links APIs, metrics
rag.py intent.py cache.py  retrieval (vector + rerank + structured calendar lookup), caching
analytics.py               anonymous usage events -> Supabase (supabase/analytics.sql)
pipeline/ run_pipeline.py  knowledge-base ingestion (see pipeline/README.md)
eval_retrieval.py          retrieval regression checks (python eval_retrieval.py)
.github/workflows/         daily calendar refresh, keep-warm ping
```

## Run it locally
```bash
npm ci && BACKEND_URL=http://localhost:8765 npm run dev          # frontend on :3000
pip install -r requirements.txt && uvicorn query_server:app --port 8765   # backend (needs .env)
```
`.env` needs `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (the **service / secret** key, never the anon key),
`GROQ_API_KEY`; optionally `METRICS_TOKEN` and `NUEVAI_CALENDAR_URL`. `GET /health` reports `data_ok`; if it is
`false` the backend cannot read the knowledge base and chat answers with an explicit error.

## Hosting
- **Frontend:** Vercel (`BACKEND_URL` or `CHAT_API_URL` points at the backend).
- **Backend:** Render free plan (512 MB RAM). No ML runs on it: questions and documents are embedded by the `embed` Supabase
  Edge Function (gte-small, 384-d; source in `supabase/functions/embed`), and results are re-ranked with a cheap lexical
  score, so the server needs ~100 MB. Chunks live in `document_chunks_v2`; set `SUPABASE_SERVICE_ROLE_KEY` to the
  *secret* key (the Edge Function accepts only callers holding it).
- A keep-warm GitHub job pings the backend every 10 minutes so the free plan does not sleep.

## Operations
Automations (all free: GitHub Actions + the existing Supabase/Render/Groq free plans). Secrets used are only the existing
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `NUEVAI_CALENDAR_URL`; none are ever echoed.

| Automation | When | What it does | Rerun it |
|---|---|---|---|
| `ci.yml` | every PR and push to main / `auto/**` | compiles all Python, runs `eval_retrieval.py` (read-only against the live index; skipped on fork PRs without secrets), `test_pipeline_fixes.py`, `test_resilience.py`, then `tsc --noEmit` and `next build` | locally: `python eval_retrieval.py`, `npx tsc --noEmit && npx next build`; on GitHub: Actions > CI > Re-run |
| `nightly-quality.yml` | daily 10:15 UTC | wakes the backend, runs `eval_golden.py` serially (6 s between questions, uses Groq quota, about 30-40 min), uploads the report as the `golden-report` artifact, then `scripts/check_baseline.py` fails the run (GitHub emails the owner) if the score is below `eval_baseline.json` score minus tolerance (now 0.872 - 0.05), or if fewer than 60% of questions could be graded (rate limits) | Actions > Nightly quality > Run workflow; locally `python eval_golden.py <backend-url>` then `python scripts/check_baseline.py --report data/eval/golden_*.json` |
| `refresh-knowledge-base.yml` | daily 13:30 UTC | iCal + live team sheets -> chunk -> index. Around it: `scripts/index_guard.py before/after` fails the job if the chunk table loses more than 10% of its rows (a bad scrape), then `scripts/audit_data.py` re-checks the index against its sources (known conflicts live in `scripts/audit_known.json`). The workbooks and documents are refreshed by hand, so in CI the audit covers the calendar feed, live team sheets and database checks; run it locally for the full 27 groups | Actions > Refresh calendar > Run workflow; locally `python run_pipeline.py --stages calendar,live,chunk,index` and `python scripts/audit_data.py` |
| `keep-warm.yml` | every 10 min | pings `/health` so Render's free plan stays awake | - |
| `scripts/weekly_digest.py` | by hand, weekly | prints a markdown digest (visitors, top questions, failures, feedback; grader traffic excluded, emails/numbers masked); read-only, sends nothing | `python scripts/weekly_digest.py --days 7` (needs `.env`) and paste the output into a chat |

Baseline: `eval_baseline.json` records which number is measured (239/274 = 87.2%, prompt 01) and which was only an estimate
(~98%, prompt 02, not used). After a full nightly run with few errors, record the real score with
`python scripts/check_baseline.py --report <report.json> --write-baseline` and commit the file. A red nightly run means a
regression or an exhausted Groq quota (the message says which); open the `golden-report` artifact for the failing questions.

More: [pipeline/README.md](pipeline/README.md) (ingestion, metrics, environment variables).
