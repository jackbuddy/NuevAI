-- Chunk table for 384-dimension vectors (gte-small, embedded by the `embed` Edge Function).
-- Already applied to the project via migration "create_document_chunks_v2_384"; kept here for reproducibility.
create table if not exists public.document_chunks_v2 (
  id          bigint generated always as identity primary key,
  chunk_id    text not null unique,
  content     text not null,
  source_file varchar(255),
  metadata    jsonb not null default '{}'::jsonb,
  embedding   vector(384) not null
);
create index if not exists document_chunks_v2_embedding_idx on public.document_chunks_v2 using hnsw (embedding vector_cosine_ops);
create index if not exists document_chunks_v2_source_idx    on public.document_chunks_v2 ((metadata->>'source'));
create index if not exists document_chunks_v2_date_idx      on public.document_chunks_v2 ((metadata->>'event_date'));
alter table public.document_chunks_v2 enable row level security;

create or replace function public.match_chunks_v2(query_embedding vector(384), match_threshold float, match_count int)
returns table (id bigint, chunk_id text, content text, metadata jsonb, similarity float)
language sql stable set search_path = public as $$
  select c.id, c.chunk_id, c.content, c.metadata, 1 - (c.embedding <=> query_embedding) as similarity
  from public.document_chunks_v2 c
  where 1 - (c.embedding <=> query_embedding) > match_threshold
  order by c.embedding <=> query_embedding
  limit match_count;
$$;
