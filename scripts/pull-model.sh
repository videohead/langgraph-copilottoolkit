#!/bin/sh
# Pull the configured code model into the local Ollama container.
# Usage: ./scripts/pull-model.sh [model]
#   model defaults to qwen2.5-coder:7b
set -e

MODEL=${1:-${OLLAMA_MODEL:-qwen2.5-coder:7b}}
echo "Pulling model: $MODEL"
docker compose exec ollama ollama pull "$MODEL"
echo "Done. Model $MODEL is ready."
