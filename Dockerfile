FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# No ML libraries here: embeddings come from a Supabase Edge Function (gte-small), so the server needs ~100 MB of RAM
# and fits Render's 512 MB free plan. Cold starts are just the container booting.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

CMD ["sh", "-c", "uvicorn query_server:app --host 0.0.0.0 --port ${PORT:-10000}"]
