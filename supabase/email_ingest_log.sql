-- Review log for the email ingest (scripts/email_ingest.py). One row per forwarded message that was looked at.
-- Holds NO message bodies and NO full sender addresses: only a truncated subject, the sender's domain and a hash of the Message-ID.
-- Run once in the Supabase dashboard: SQL Editor -> New query -> paste -> Run (already applied through the Supabase MCP when this file was added).

create table if not exists public.email_ingest_log (
  id                  bigint generated always as identity primary key,
  received_at         timestamptz,                                  -- the Date header of the message that reached the inbox
  processed_at        timestamptz not null default now(),
  status              text not null check (status in ('indexed', 'skipped_privacy', 'rejected_sender', 'rejected_auth', 'duplicate', 'error', 'needs_review')),
  subject             text check (subject is null or char_length(subject) <= 120),
  sender_domain       text,
  message_id_hash     text,                                         -- sha256 of the Message-ID, first 16 hex characters
  chunk_count         integer not null default 0,
  reason              text,
  attachments_skipped integer not null default 0
);

create index if not exists email_ingest_log_processed_idx on public.email_ingest_log (processed_at desc);
create index if not exists email_ingest_log_msgid_idx     on public.email_ingest_log (message_id_hash);
create index if not exists email_ingest_log_status_idx    on public.email_ingest_log (status, processed_at desc);

-- RLS on with no policies: the public anon key can do nothing, the server's service role bypasses RLS (same as analytics_events).
alter table public.email_ingest_log enable row level security;
revoke all on public.email_ingest_log from anon, authenticated;
