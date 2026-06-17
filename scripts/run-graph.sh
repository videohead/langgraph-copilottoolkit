#!/usr/bin/env sh
set -eu

detect_base_url() {
  if command -v lando >/dev/null 2>&1; then
    INFO_JSON=$(lando info --format json 2>/dev/null || true)
    if [ -n "$INFO_JSON" ]; then
      URL=$(printf '%s' "$INFO_JSON" | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)

for service in data:
    if service.get("service") == "appserver":
        for url in service.get("urls", []):
            if "localhost" in url:
                print(url.rstrip("/"))
                raise SystemExit(0)
print("")
')
      if [ -n "$URL" ]; then
        printf '%s\n' "$URL"
        return 0
      fi
    fi
  fi

  printf '%s\n' "http://localhost:8123"
}

BASE_URL=${BASE_URL:-$(detect_base_url)}
GRAPH_ID=${1:-basic}
INPUT_TEXT=${2:-"hello"}

escape_json() {
  printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

INPUT_JSON=$(escape_json "$INPUT_TEXT")

THREAD_JSON=$(curl -sS -X POST "$BASE_URL/threads" -H 'Content-Type: application/json' -d '{}')
THREAD_ID=$(printf '%s' "$THREAD_JSON" | sed -n 's/.*"thread_id":"\([^"]*\)".*/\1/p')

if [ -z "$THREAD_ID" ]; then
  echo "Failed to create thread. Response: $THREAD_JSON" >&2
  exit 1
fi

curl -sS -X POST "$BASE_URL/threads/$THREAD_ID/runs/wait" \
  -H 'Content-Type: application/json' \
  -d "{\"assistant_id\":\"$GRAPH_ID\",\"input\":{\"messages\":[{\"role\":\"user\",\"content\":$INPUT_JSON}]}}"

echo
