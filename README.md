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
- **Frontend:** Vercel. Set `BACKEND_URL` to the backend's address.
- **Backend:** a Hugging Face Docker Space (free, 16 GB RAM; the models need ~1.6 GB, which is why Render's 512 MB free plan
  crashes). `.github/workflows/deploy-hf-space.yml` pushes it on every change to the backend files. One-time setup:
  create a Docker Space, add its secrets (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GROQ_API_KEY`, `METRICS_TOKEN`), then add the
  GitHub secret `HF_TOKEN` (write token) and variable `HF_SPACE` (`username/space-name`). Manual deploy:
  `HF_SPACE=username/space HF_TOKEN=hf_... ./scripts/push_hf_space.sh`.
- `Dockerfile` (root) is the original Render setup and only works on a plan with 2 GB+ of RAM.

More: [pipeline/README.md](pipeline/README.md) (ingestion, metrics, environment variables).
