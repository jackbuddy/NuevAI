#!/usr/bin/env bash
# Assemble the backend (python files + the Space Dockerfile) and push it to a Hugging Face Space.
#   HF_SPACE=username/space-name HF_TOKEN=hf_xxx ./scripts/push_hf_space.sh
# The token needs "write" access. It is only used for this push and is never printed.
set -euo pipefail
if [ -z "${HF_TOKEN:-}" ] || [ -z "${HF_SPACE:-}" ]; then
  echo "HF_TOKEN / HF_SPACE not set - skipping Hugging Face deploy."; exit 0
fi
here="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cp "$here"/query_server.py "$here"/rag.py "$here"/intent.py "$here"/cache.py "$here"/analytics.py "$here"/requirements.txt "$tmp"/
cp "$here"/deploy/hf-space/Dockerfile "$here"/deploy/hf-space/README.md "$tmp"/
cd "$tmp"
git init -q -b main && git add -A
git -c user.name=nuevai-deploy -c user.email=deploy@nuevai.invalid commit -q -m "Deploy $(git -C "$here" rev-parse --short HEAD 2>/dev/null || echo manual)"
git push -q -f "https://user:${HF_TOKEN}@huggingface.co/spaces/${HF_SPACE}" main 2>&1 | sed "s/${HF_TOKEN}/***/g" || { echo "push failed"; exit 1; }
echo "Pushed to https://huggingface.co/spaces/${HF_SPACE}"
