# Hugging Face Docker Space: 16 GB RAM on the free tier, so the full bge-large + reranker setup fits.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/home/user/.cache/huggingface

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 user

USER user
ENV PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --user -r requirements.txt

# Bake both models into the image so starting (or waking) the Space never downloads anything.
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('BAAI/bge-large-en-v1.5', device='cpu'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2', device='cpu')"

COPY --chown=user . .

EXPOSE 7860
CMD ["sh", "-c", "uvicorn query_server:app --host 0.0.0.0 --port ${PORT:-7860}"]
