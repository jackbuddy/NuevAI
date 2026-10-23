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

More: [pipeline/README.md](pipeline/README.md) (ingestion, metrics, environment variables).
