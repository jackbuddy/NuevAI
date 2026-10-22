-- Run once in the Supabase dashboard: SQL Editor -> New query -> paste -> Run.
-- Stores anonymous usage events. No accounts or personal data: anon_id is a random
-- id generated in the visitor's browser. Only the server (service role key) can read or write.

create table if not exists public.analytics_events (
  id          bigint generated always as identity primary key,
  ts          timestamptz not null default now(),
  anon_id     text not null,
  session_id  text,
  event       text not null,
  props       jsonb not null default '{}'::jsonb,
  path        text,
  ua          text
);

create index if not exists analytics_events_ts_idx      on public.analytics_events (ts desc);
create index if not exists analytics_events_anon_idx    on public.analytics_events (anon_id, ts desc);
create index if not exists analytics_events_event_idx   on public.analytics_events (event, ts desc);

-- RLS on with no policies: the public anon key can do nothing, the server's service role bypasses RLS.
alter table public.analytics_events enable row level security;
