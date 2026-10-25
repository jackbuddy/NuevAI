"""Text -> vector via the `embed` Supabase Edge Function (gte-small, 384-d, normalized).

Running the model inside Supabase keeps this server tiny (no PyTorch / ONNX, ~100 MB of RAM), which is what lets it
fit Render's 512 MB free plan. The function accepts only callers holding the project's secret key.
"""
import os
import time

import httpx

_client = httpx.Client(timeout=40)
MAX_BATCH = 4      # the Edge Function has a small CPU budget per call: ~250 ms per text, errors (546) above ~16


def _endpoint() -> str:
    return os.getenv("SUPABASE_URL", "").strip().strip("\"'").rstrip("/") + "/functions/v1/embed"


def _key() -> str:
    return os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip().strip("\"'")


QUERY_TIMEOUT = float(os.getenv("EMBED_QUERY_TIMEOUT", "8"))   # a visitor is waiting: fail fast and degrade instead of hanging


def embed(texts: list[str], _chars: int = 2400, timeout: float | None = None, attempts: int = 3) -> list[list[float]]:
    """Embed a few texts. Retries transient failures; if the function's per-call CPU budget is exceeded (HTTP 546)
    the batch is split in half, and a single oversized text is shortened."""
    last = ""
    for attempt in range(attempts):
        try:
            r = _client.post(_endpoint(), json={"inputs": [t[:_chars] for t in texts]}, headers={"x-embed-key": _key()},
                             **({"timeout": timeout} if timeout else {}))
            if r.status_code == 200:
                return r.json()["embeddings"]
            last = f"HTTP {r.status_code}"
            if r.status_code == 401:
                raise RuntimeError("embedding function rejected the key (SUPABASE_SERVICE_ROLE_KEY must be the secret key)")
            if r.status_code in (546, 500, 502, 503, 504):          # too heavy / transient
                if len(texts) > 1:
                    mid = len(texts) // 2
                    return embed(texts[:mid], _chars, timeout, attempts) + embed(texts[mid:], _chars, timeout, attempts)
                if _chars > 600:
                    return embed(texts, _chars // 2, timeout, attempts)
        except httpx.HTTPError as e:
            last = type(e).__name__
        if attempt + 1 < attempts:
            time.sleep(1.5 * (attempt + 1) if not timeout else 0.4)
    raise RuntimeError(f"embedding service unavailable ({last})")


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def embed_query(text: str) -> list[float]:
    """Query-time embedding: short timeout, two attempts (worst case about 17 s instead of minutes)."""
    return embed([text], timeout=QUERY_TIMEOUT, attempts=2)[0]
