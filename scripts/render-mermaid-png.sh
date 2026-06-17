#!/usr/bin/env sh
set -eu

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
README_FILE=${1:-"$ROOT_DIR/readme.md"}
OUTPUT_FILE=${2:-"$ROOT_DIR/public/swarm-chart.png"}

if [ ! -f "$README_FILE" ]; then
  echo "README not found: $README_FILE" >&2
  exit 1
fi

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

MMD_FILE="$TMP_DIR/chart.mmd"
PNG_FILE="$TMP_DIR/chart.png"

awk '
BEGIN { in_block=0 }
/^```mermaid[[:space:]]*$/ { in_block=1; next }
in_block && /^```[[:space:]]*$/ { exit }
in_block { print }
' "$README_FILE" > "$MMD_FILE"

if [ ! -s "$MMD_FILE" ]; then
  echo "No mermaid block found in $README_FILE" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"

# Render the first Mermaid block in the README to PNG using the official CLI image.
docker run --rm \
  -u "$(id -u):$(id -g)" \
  -v "$TMP_DIR:/data" \
  minlag/mermaid-cli:latest \
  -i /data/chart.mmd \
  -o /data/chart.png \
  -t neutral \
  -b transparent

cp "$PNG_FILE" "$OUTPUT_FILE"
echo "Wrote chart PNG: $OUTPUT_FILE"
