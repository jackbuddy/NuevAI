import os
import json
import asyncio
from typing import Dict, Any
from dotenv import load_dotenv
from tqdm.asyncio import tqdm_asyncio
from groq import AsyncGroq

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
INPUT_FILE = "data/cleaned_chunks.json"
OUTPUT_FILE = "data/enriched_chunks.json"
CHECKPOINT_FILE = "data/enriched_chunks_checkpoint.json"
MODEL_NAME = "llama-3.1-8b-instant"  # High throughput & cost-effective

if not GROQ_API_KEY:
    print("Error: GROQ_API_KEY is missing from your .env file.")
    exit(1)

client = AsyncGroq(api_key=GROQ_API_KEY)
SEMAPHORE = asyncio.Semaphore(15)  # Concurrency limit to respect API rate limits

SYSTEM_PROMPT = "You are a JSON-only data classifier. Respond STRICTLY with valid JSON. No Markdown formatting or commentary."

async def classify_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
    # Return immediately if already processed in checkpoint
    if "metadata" in chunk and chunk["metadata"]:
        return chunk

    async with SEMAPHORE:
        # Pass first 120 words to minimize token costs
        truncated_text = " ".join(chunk["content"].split()[:120])
        
        user_prompt = f"""Snippet: "{truncated_text}"
Source Category: "{chunk['source_category']}"

Return JSON matching this schema:
{{
  "audience": ["student", "parent", "teacher"],
  "category": "academics" | "it_support" | "athletics" | "facility" | "policy" | "general",
  "keywords": ["tag1", "tag2"]
}}"""

        try:
            response = await client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=100
            )
            metadata = json.loads(response.choices[0].message.content)
            chunk["metadata"] = metadata
        except Exception:
            # Safe default fallback on error or rate-limit
            chunk["metadata"] = {
                "audience": ["student", "parent", "teacher"],
                "category": chunk["source_category"],
                "keywords": []
            }
        return chunk

async def main():
    if not os.path.exists(INPUT_FILE):
        print(f"Error: Could not find '{INPUT_FILE}'. Run parse_and_chunk.py first.")
        return

    # Check for existing checkpoint
    if os.path.exists(CHECKPOINT_FILE):
        print(f"Resuming from checkpoint '{CHECKPOINT_FILE}'...")
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            chunks = json.load(f)
    else:
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            chunks = json.load(f)

    print(f"Enriching metadata for {len(chunks)} chunks via Groq...")
    
    # Process in batches of 500 for state saving
    batch_size = 500
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        unprocessed = [c for c in batch if "metadata" not in c or not c["metadata"]]
        
        if unprocessed:
            tasks = [classify_chunk(c) for c in unprocessed]
            await tqdm_asyncio.gather(*tasks, desc=f"Batch {i//batch_size + 1}/{(len(chunks)-1)//batch_size + 1}")
            
            # Save checkpoint
            with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
                json.dump(chunks, f, indent=2)

    # Save final output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(chunks, f, indent=2)

    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)

    print(f"\nMetadata enrichment complete! Saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    asyncio.run(main())
