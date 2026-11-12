-- Manifest and review log for the Drive ingest (scripts/email_ingest.py --only google; pipeline/share_ingest.py).
-- NEW tables only; nothing existing is altered. Run once in the Supabase dashboard: SQL Editor -> New query -> paste -> Run.
-- Neither table holds file contents, Drive file ids, owner addresses or any student/personal data: files are identified by a 12-hex hash of
-- the Drive file id, the owner only by email DOMAIN, and `name` is the file title (max 120 characters).

-- One row per shared Sheet/Doc: what was last ingested. This is the "already processed" bookkeeping that survives between CI runs.
create table if not exists public.share_ingest_manifest (
  file_hash        text primary key,                 -- first 12 hex of sha1(Drive file id); chunk ids are share-<file_hash>-...
  source_type      text not null check (source_type in ('sheet', 'doc')),
  name             text check (name is null or char_length(name) <= 120),
  mime_type        text,
  modified_time    timestamptz,                      -- Drive modifiedTime at the last attempt
  version          text,                             -- Drive version at the last attempt
  status           text not null check (status in ('indexed', 'needs_review', 'skipped', 'error', 'failed', 'unshared', 'removed')),
  attempts         integer not null default 0,       -- failed tries for this modifiedTime; at 3 the status becomes 'failed' until the file changes
  missing_runs     integer not null default 0,       -- consecutive complete runs in which the file was not listed (2 -> 'unshared')
  chunk_count      integer not null default 0,
  event_count      integer not null default 0,
  owner_domain     text,
  reason           text,
  last_ingested_at timestamptz,
  updated_at       timestamptz not null default now()
);

-- Review log: one row per file that was looked at and did something (indexed, needs_review, skipped, error, failed, unshared). Unchanged files are not logged.
create table if not exists public.share_ingest_log (
  id            bigint generated always as identity primary key,
  processed_at  timestamptz not null default now(),
  source_type   text not null,
  file_hash     text,
  name          text check (name is null or char_length(name) <= 120),
  status        text not null,
  chunk_count   integer not null default 0,
  event_count   integer not null default 0,
  modified_time timestamptz,
  reason        text,
  attempts      integer not null default 0,
  owner_domain  text
);

create index if not exists share_ingest_log_processed_idx on public.share_ingest_log (processed_at desc);
create index if not exists share_ingest_log_file_idx      on public.share_ingest_log (file_hash);
create index if not exists share_ingest_log_status_idx    on public.share_ingest_log (status, processed_at desc);

-- RLS on with no policies: the public anon key can do nothing, the server's service role bypasses RLS (same as email_ingest_log / analytics_events).
alter table public.share_ingest_manifest enable row level security;
alter table public.share_ingest_log      enable row level security;
revoke all on public.share_ingest_manifest from anon, authenticated;
revoke all on public.share_ingest_log      from anon, authenticated;
