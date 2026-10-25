-- Keyword (full-text) candidates to complement vector search. Apply once in the Supabase SQL editor (or via migration).
-- rag.py calls it as RPC search_chunks_fts; if it is not installed the server silently stays vector-only.
create index if not exists document_chunks_v2_fts_idx on public.document_chunks_v2 using gin (to_tsvector('english', content));

create or replace function public.search_chunks_fts(query_text text, match_count int)
returns table (id bigint, chunk_id text, content text, metadata jsonb, rank real)
language sql stable set search_path = public as $$
  select c.id, c.chunk_id, c.content, c.metadata,
         ts_rank_cd(to_tsvector('english', c.content), to_tsquery('english', query_text)) as rank
  from public.document_chunks_v2 c
  where to_tsvector('english', c.content) @@ to_tsquery('english', query_text)
  order by rank desc
  limit match_count;
$$;
