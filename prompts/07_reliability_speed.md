# 07 - Speed and resilience (free tier)

1. Measure live and local: cold start, TTFB, time to first token, full answer, by category. Identify the slowest stage with the timings in the SSE `done` event.
2. Persist the answer cache across Render restarts using Supabase (small `answer_cache` table, migration file in supabase/, TTL honored, no PII, key = normalized question). Keep in-memory as L1. Write the migration and apply it with the Supabase MCP (creating a table is allowed; deleting is not).
3. Pre-warm: top 30 questions from analytics warmed after deploy.
4. Failure drills (locally, by simulation): Groq 429 on all models, Groq timeout, Supabase slow/unreachable, edge function 546/5xx. Each must give a friendly, accurate degraded response (e.g. show retrieved sources/links without LLM text) rather than an error. Add tests.
5. Check Render memory stays under ~150 MB under 20 concurrent requests.
