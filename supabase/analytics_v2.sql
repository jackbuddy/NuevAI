-- Insights v2. Safe to run more than once. Creates a NEW table and a read-only function; does not touch analytics_events rows.
-- Run in the Supabase SQL editor (or via the Supabase MCP apply_migration).

-- 1) Owner-managed audience overrides for already-recorded history (anon_id -> user | internal | test | bot).
create table if not exists public.analytics_audience_overrides (
  anon_id    text primary key,
  audience   text not null check (audience in ('user','internal','test','bot')),
  note       text,
  created_at timestamptz not null default now()
);
-- RLS on, no policies: only the server's service role can read or write.
alter table public.analytics_audience_overrides enable row level security;

-- 2) All-time per-anon_id summary: true first-seen across the whole history, Pacific active dates, and the
--    evidence needed to classify the audience (ingestion-time audience stamps and distinct user agents).
--    The server calls it at most once a minute (paged with .range()); it replaces "new vs returning inside the window" guesswork.
create or replace function public.analytics_anon_profile()
returns table(anon_id text, first_ts timestamptz, last_ts timestamptz, events bigint, chats bigint,
              sessions bigint, active_dates date[], uas text[], auds text[])
language sql stable security invoker set search_path = public as $$
  select e.anon_id,
         min(e.ts), max(e.ts), count(*),
         count(*) filter (where e.event = 'chat_answered'),
         count(distinct e.session_id),
         array_agg(distinct (e.ts at time zone 'America/Los_Angeles')::date order by (e.ts at time zone 'America/Los_Angeles')::date),
         (array_agg(distinct left(e.ua, 90)) filter (where e.ua is not null and e.ua <> ''))[1:6],
         array_agg(distinct e.props->>'_aud') filter (where e.props ? '_aud')
  from public.analytics_events e
  group by e.anon_id
  order by min(e.ts), e.anon_id
$$;
revoke all on function public.analytics_anon_profile() from public, anon, authenticated;
grant execute on function public.analytics_anon_profile() to service_role;

-- Note: an earlier draft of this function was applied as public.analytics_anon_summary(); it is unused and harmless.
-- Optional cleanup (owner): drop function if exists public.analytics_anon_summary();
