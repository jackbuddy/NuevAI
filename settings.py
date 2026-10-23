"""Where the knowledge base lives. v2 holds 384-dimension vectors (gte-small, embedded inside Supabase)."""
import os

CHUNKS_TABLE = os.getenv("CHUNKS_TABLE", "document_chunks_v2")
MATCH_RPC = os.getenv("MATCH_RPC", "match_chunks_v2")
EMBED_DIM = 384
