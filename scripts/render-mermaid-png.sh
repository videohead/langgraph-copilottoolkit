#!/usr/bin/env sh
set -eu

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
REGISTRY_FILE=${1:-"$ROOT_DIR/langgraph.json"}
OUTPUT_DIR=${2:-"$ROOT_DIR/public"}
MMD_TMP_DIR="$ROOT_DIR/.tmp-mermaid"
EXPORT_SCRIPT="$ROOT_DIR/scripts/export_graph_mermaid.py"

if [ ! -f "$REGISTRY_FILE" ]; then
  echo "Registry not found: $REGISTRY_FILE" >&2
  exit 1
fi

if [ ! -f "$EXPORT_SCRIPT" ]; then
  echo "Export script not found: $EXPORT_SCRIPT" >&2
  exit 1
fi

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$MMD_TMP_DIR"
rm -f "$MMD_TMP_DIR"/*.mmd

run_export() {
  if command -v lando >/dev/null 2>&1; then
    if (cd "$ROOT_DIR" && lando ssh -s appserver -c "python /app/scripts/export_graph_mermaid.py --registry /app/langgraph.json --output-dir /app/.tmp-mermaid") >/dev/null 2>&1; then
      return 0
    fi
  fi

  if command -v docker >/dev/null 2>&1; then
    if docker ps --format '{{.Names}}' | grep -q '^langgraph-dev$'; then
      docker exec langgraph-dev python /app/scripts/export_graph_mermaid.py \
        --registry /app/langgraph.json \
        --output-dir /app/.tmp-mermaid
      return 0
    fi
  fi

  echo "Could not run export in a container. Start services (lando start or docker compose up)." >&2
  return 1
}

run_export

if ! ls "$MMD_TMP_DIR"/*.mmd >/dev/null 2>&1; then
  echo "No Mermaid files generated in $MMD_TMP_DIR" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

for mmd in "$MMD_TMP_DIR"/*.mmd; do
  graph_id=$(basename "$mmd" .mmd)
  png="$OUTPUT_DIR/${graph_id}-chart.png"

  cp "$mmd" "$TMP_DIR/chart.mmd"

  docker run --rm \
    -u "$(id -u):$(id -g)" \
    -v "$TMP_DIR:/data" \
    minlag/mermaid-cli:latest \
    -i /data/chart.mmd \
    -o /data/chart.png \
    -t neutral \
    -b transparent

  cp "$TMP_DIR/chart.png" "$png"
  echo "Wrote chart PNG: $png"
done

if [ -f "$OUTPUT_DIR/swarm_v1-chart.png" ]; then
  cp "$OUTPUT_DIR/swarm_v1-chart.png" "$OUTPUT_DIR/swarm-chart.png"
  echo "Updated legacy chart PNG: $OUTPUT_DIR/swarm-chart.png"
fi
