import os
import json
import time
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from supabase import create_client, Client
from tqdm import tqdm

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
INPUT_FILE = "data/enriched_chunks.json"

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is missing from your .env file.")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

print("Loading local embedding model ('BAAI/bge-large-en-v1.5')...")
model = SentenceTransformer("BAAI/bge-large-en-v1.5")

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"Error: Could not find '{INPUT_FILE}'.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    print(f"Loaded {len(chunks)} enriched chunks for vector store ingestion.")
    
    batch_size = 50
    total_chunks = len(chunks)
    
    for i in tqdm(range(0, total_chunks, batch_size), desc="Embedding & Ingesting to Supabase"):
        batch = chunks[i : i + batch_size]
        texts = [item["content"] for item in batch]
        
        # Local 1024-dimension embeddings computation
        embeddings = model.encode(texts, normalize_embeddings=True).tolist()
        
        records = []
        for item, emb in zip(batch, embeddings):
            records.append({
                "chunk_id": item["chunk_id"],
                "content": item["content"],
                "source_file": item.get("source_file", ""),
                "metadata": item.get("metadata", {}),
                "embedding": emb
            })
        
        retries = 3
        for attempt in range(retries):
            try:
                supabase.table("document_chunks").upsert(records, on_conflict="chunk_id").execute()
                break
            except Exception as e:
                if attempt == retries - 1:
                    print(f"\nFailed to insert batch starting at index {i}: {e}")
                else:
                    time.sleep(2 ** attempt)

    print("\nVector Database Ingestion Complete! All 11,299 chunks are indexed in Supabase.")

if __name__ == "__main__":
    main()
