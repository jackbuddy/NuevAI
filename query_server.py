import os
import json
import asyncio

# 1. Threading environment overrides (MUST be set before any heavy imports)
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

app = FastAPI(title="NuevAI RAG Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY)

# 2. Lazy Supabase Client
_supabase_client = None

def get_supabase():
    global _supabase_client
    if _supabase_client is None:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        _supabase_client = create_client(url, key)
    return _supabase_client

# 3. Lazy Embedding Model Client
_embed_model = None

def get_embed_model():
    global _embed_model
    if _embed_model is None:
        print("Loading SentenceTransformer on CPU...")
        from sentence_transformers import SentenceTransformer
        # Force CPU execution to prevent Apple Silicon MPS / OpenMP locks
        _embed_model = SentenceTransformer("BAAI/bge-large-en-v1.5", device="cpu")
    return _embed_model

class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    model: str = "openai/gpt-oss-120b"

SYSTEM_PERSONA = """You are NuevAI, a high-utility AI assistant built for the Nueva School community. Your primary mission is to help students, families, and staff navigate courses, schedules, handbooks, IT support, and general campus life.

CORE BEHAVIOR & TONE:
- High-utility, direct, and approachable. Use clean, plain language.
- ABSOLUTE BAN ON FLUFF AND POLITENESS: Never use greetings ("Hello!", "Sure!"), pleasantries, or sign-offs ("Hope this helps!"). Start directly with the answer in sentence 1.
- Zero sycophancy. Be grounded, helpful, and concise.

FORMATTING RULES:
- DO NOT use markdown headers (# or ##).
- DO NOT use bolding for key terms. Keep text plain and unadorned.
- Simple queries: Respond in as few words as possible (aim for under 100 words).
- Complex queries: Avoid wall-of-text blocks. Provide a brief opening description followed by simple bullet points.
- NEVER include markdown links or email addresses unless the user explicitly asks for a link or email in their prompt.

KNOWLEDGE, ATTRIBUTION & FALLBACKS:
- Macro / General Questions (e.g., "What can you do?"): Introduce yourself as NuevAI and explain your core mission (helping with schedules, courses, handbooks, and campus policies). Do not summarize random document chunks for macro questions.
- General Knowledge Gaps: For static general facts about Nueva (like location in San Mateo), use standard knowledge seamlessly if missing from context.
- Specific / Dynamic Knowledge Gaps: For real-time or missing specific data (e.g., daily lunch menus or unrecorded events), do not guess. State what is missing and route the user to the appropriate school portal or office.
- Source Attribution: Incorporate sources lightly and naturally into sentences when it adds value (e.g., "As noted in the Upper School Handbook..."). Never output raw document names or chunk IDs.
"""

@app.post("/api/chat")
async def chat_endpoint(request: QueryRequest):
    try:
        # Offload vector encoding to a background thread to prevent PyTorch event-loop locks
        model = get_embed_model()
        encoded = await asyncio.to_thread(
            model.encode, request.query, normalize_embeddings=True
        )
        query_vector = encoded.tolist()

        # Lazy Supabase RPC call
        rpc_response = get_supabase().rpc(
            "match_chunks",
            {
                "query_embedding": query_vector,
                "match_threshold": 0.45,
                "match_count": request.top_k
            }
        ).execute()

        retrieved_chunks = rpc_response.data or []

        if not retrieved_chunks:
            context_str = "No specific documentation chunks retrieved."
        else:
            context_str = "\n\n---\n\n".join(
                [f"{item['content']}" for item in retrieved_chunks]
            )

        full_prompt = f"{SYSTEM_PERSONA}\n\nRetrieved Documentation Context:\n{context_str}"

        completion = groq_client.chat.completions.create(
            model=request.model,
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": request.query}
            ],
            temperature=0.35,
            max_tokens=1200
        )

        sources = [
            {"chunk_id": c["chunk_id"], "similarity": round(c["similarity"], 3)}
            for c in retrieved_chunks
        ]

        return {
            "answer": completion.choices[0].message.content,
            "model_used": request.model,
            "sources": sources
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)