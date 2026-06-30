#!/usr/bin/env sh
set -eu

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
WATCH_REGISTRY="$ROOT_DIR/langgraph.json"
WATCH_SRC_DIR="$ROOT_DIR/src"
RENDER_SCRIPT="$ROOT_DIR/scripts/render-mermaid-png.sh"

if [ ! -x "$RENDER_SCRIPT" ]; then
  echo "Render script missing or not executable: $RENDER_SCRIPT" >&2
  exit 1
fi

get_chart_url() {
  if command -v lando >/dev/null 2>&1; then
    if INFO_JSON=$(cd "$ROOT_DIR" && lando info --format json 2>/dev/null); then
      URL=$(printf '%s' "$INFO_JSON" | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)

for service in data:
    if service.get("service") == "charts":
        urls = service.get("urls", [])
        for u in urls:
            if "localhost" in u:
                print(u.rstrip("/"))
                raise SystemExit(0)
print("")
')
      if [ -n "$URL" ]; then
        printf '%s\n' "$URL"
        return 0
      fi
    fi
  fi

  printf '%s\n' "http://localhost:8124"
}

open_url() {
  url=$1
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
    return 0
  fi
  if command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
    return 0
  fi
  if command -v wslview >/dev/null 2>&1; then
    wslview "$url" >/dev/null 2>&1 || true
    return 0
  fi
  return 1
}

render_chart() {
  "$RENDER_SCRIPT"
}

echo "Rendering chart..."
render_chart

CHART_URL=$(get_chart_url)
echo "Chart URL: $CHART_URL"

if open_url "$CHART_URL"; then
  echo "Opened chart URL in your default browser."
else
  echo "Could not auto-open browser. Open this URL manually: $CHART_URL"
fi

echo "Watching $WATCH_REGISTRY and $WATCH_SRC_DIR for changes. Press Ctrl+C to stop."

if command -v inotifywait >/dev/null 2>&1; then
  while inotifywait -q -r -e close_write,move,create "$WATCH_REGISTRY" "$WATCH_SRC_DIR" >/dev/null 2>&1; do
    echo "Change detected, regenerating PNG..."
    render_chart
  done
else
  LAST_REG_SUM=$(cksum "$WATCH_REGISTRY" | awk '{print $1":"$2}')
  LAST_SRC_SUM=$(find "$WATCH_SRC_DIR" -type f -name '*.py' -exec cksum {} + 2>/dev/null | cksum | awk '{print $1":"$2}')
  while true; do
    sleep 2
    NEW_REG_SUM=$(cksum "$WATCH_REGISTRY" | awk '{print $1":"$2}')
    NEW_SRC_SUM=$(find "$WATCH_SRC_DIR" -type f -name '*.py' -exec cksum {} + 2>/dev/null | cksum | awk '{print $1":"$2}')
    if [ "$NEW_REG_SUM" != "$LAST_REG_SUM" ] || [ "$NEW_SRC_SUM" != "$LAST_SRC_SUM" ]; then
      LAST_REG_SUM="$NEW_REG_SUM"
      LAST_SRC_SUM="$NEW_SRC_SUM"
      echo "Change detected, regenerating PNG..."
      render_chart
    fi
  done
fi
