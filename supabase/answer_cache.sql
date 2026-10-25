-- Persistent L2 answer cache (survives Render restarts). Service role only; no policies on purpose.
-- Holds only: a normalized question, the generated answer, source titles/links, follow-up chips, an expiry.
-- No anon_id, session_id, IP or user agent is ever stored here. Questions that look personal are not written (see answer_store.py).
create table if not exists public.answer_cache (
  key          text primary key,                 -- normalized question (lowercase, punctuation stripped, max 160 chars)
  answer       text        not null,
  model_used   text,
  sources      jsonb       not null default '[]'::jsonb,
  suggestions  jsonb       not null default '[]'::jsonb,
  date_bound   boolean     not null default false, -- true when the answer depends on today's date (short TTL)
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null
);
create index if not exists answer_cache_expires_idx on public.answer_cache (expires_at);
alter table public.answer_cache enable row level security;
-- Expired rows are ignored by reads; the server prunes its own expired rows opportunistically (DELETE by key/expiry from the
-- service role). To clean up by hand:  delete from public.answer_cache where expires_at < now();
