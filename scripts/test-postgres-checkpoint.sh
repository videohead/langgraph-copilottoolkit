#!/usr/bin/env sh
set -eu

GRAPH="${1:-${GRAPH:-basic}}"
THREAD_ID="${THREAD_ID:-checkpoint-test-$(date +%s)}"
TOKEN="${TOKEN:-ORBIT-$(date +%s)}"
DJANGO_URL="${DJANGO_URL:-http://localhost:8080}"
REQUEST_TIMEOUT_SECONDS="${REQUEST_TIMEOUT_SECONDS:-180}"

if ! command -v lando >/dev/null 2>&1; then
  echo "Error: lando is required." >&2
  exit 1
fi

echo "[1/6] Checking Postgres readiness..."
lando ssh -s postgres -c "sh -lc 'pg_isready -U langgraph -d langgraph >/dev/null'"
echo "Postgres is accepting connections."

echo "[2/6] Preparing checkpoint continuity test inputs..."
PROMPT1="Remember this token exactly: ${TOKEN}. Reply only: stored"
PROMPT2="What token did I ask you to remember earlier in this thread? Reply with token only."

TMP1="$(mktemp)"
TMP2="$(mktemp)"
trap 'rm -f "$TMP1" "$TMP2"' EXIT

post_turn() {
  body="$1"
  lando curl -sN -X POST "${DJANGO_URL}/api/agents/${GRAPH}/" \
    --max-time "${REQUEST_TIMEOUT_SECONDS}" \
    -H "Content-Type: application/json" \
    --data "$body"
}

echo "[3/6] Running first turn with threadId=${THREAD_ID}..."
BODY1=$(cat <<EOF
{"threadId":"${THREAD_ID}","messages":[{"role":"user","content":"${PROMPT1}"}]}
EOF
)
post_turn "$BODY1" >"$TMP1"

echo "[4/6] Running second turn with same threadId=${THREAD_ID}..."
BODY2=$(cat <<EOF
{"threadId":"${THREAD_ID}","messages":[{"role":"user","content":"${PROMPT2}"}]}
EOF
)
post_turn "$BODY2" >"$TMP2"

echo "[5/6] Validating SSE runs completed..."
if ! grep -q '"type": "RUN_FINISHED"\|"type":"RUN_FINISHED"' "$TMP1"; then
  echo "FAIL: First turn did not finish successfully." >&2
  cat "$TMP1" >&2
  exit 1
fi
if ! grep -q '"type": "RUN_FINISHED"\|"type":"RUN_FINISHED"' "$TMP2"; then
  echo "FAIL: Second turn did not finish successfully." >&2
  cat "$TMP2" >&2
  exit 1
fi

echo "[6/6] Validating checkpoint continuity (token recall)..."
if grep -q "$TOKEN" "$TMP2"; then
  echo "PASS: Token recalled on second turn with same threadId."
  echo "threadId=${THREAD_ID} graph=${GRAPH} token=${TOKEN}"
else
  echo "FAIL: Token not recalled on second turn." >&2
  echo "Expected token: $TOKEN" >&2
  echo "Second turn response:" >&2
  cat "$TMP2" >&2
  exit 1
fi

if lando ssh -s postgres -c "psql -U langgraph -d langgraph -tAc \"SELECT to_regclass('public.checkpoints');\"" | grep -q checkpoints; then
  COUNT=$(lando ssh -s postgres -c "psql -U langgraph -d langgraph -tAc \"SELECT count(*) FROM checkpoints;\"" | tr -d ' ')
  echo "Checkpoint rows in public.checkpoints: ${COUNT}"
else
  echo "Note: public.checkpoints table not found yet (checkpointer setup may differ by package version)."
fi
