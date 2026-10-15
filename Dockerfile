FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image so cold starts never download ~1.3GB.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-large-en-v1.5', device='cpu')"

COPY . .

EXPOSE 10000

CMD ["sh", "-c", "uvicorn query_server:app --host 0.0.0.0 --port ${PORT:-10000}"]
