#!/usr/bin/env bash
# End-to-end smoke test against a running stack (make smoke / Spec 04 Task 1).
set -euo pipefail

API_URL=${API_URL:-http://localhost:${API_PORT:-8000}}
API_KEY=${API_KEY:-dev-local-key}
ID="SMOKE-$(date +%s)-$RANDOM"
MESSAGE="No tengo acceso al sistema de reportes internos."

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
BODY_FILE="$TMP_DIR/body.json"

fail() {
  echo "SMOKE FAILED: $1" >&2
  exit 1
}

# http METHOD PATH JSON_BODY(or "") USE_KEY(yes/no) STEP -> prints the HTTP status code, or fails
# with a named step if the connection itself cannot be made (never leaks curl's raw exit code).
http() {
  local method=$1 path=$2 data=$3 use_key=$4 step=$5
  local args=(-sS -o "$BODY_FILE" -w '%{http_code}' -X "$method" "$API_URL$path")
  args+=(-H "Content-Type: application/json")
  if [[ "$use_key" == "yes" ]]; then
    args+=(-H "X-API-Key: $API_KEY")
  fi
  if [[ -n "$data" ]]; then
    args+=(-d "$data")
  fi
  if ! curl "${args[@]}"; then
    fail "$step: cannot connect to $API_URL"
  fi
}

wait_for_health() {
  local deadline=$((SECONDS + 30))
  while (( SECONDS < deadline )); do
    if curl -sS -o /dev/null -w '%{http_code}' "$API_URL/health" 2>/dev/null | grep -q '^200$'; then
      return 0
    fi
    sleep 1
  done
  return 0
}

expect_status() {
  local got=$1 want=$2 step=$3
  if [[ "$got" != "$want" ]]; then
    fail "$step: expected HTTP $want, got $got ($(cat "$BODY_FILE"))"
  fi
}

check_body() {
  local step=$1
  shift
  if ! python3 -c "$@" "$BODY_FILE"; then
    fail "$step: unexpected response body ($(cat "$BODY_FILE"))"
  fi
}

wait_for_health

# 1. /health -> 200
status=$(http GET /health "" no "GET /health")
expect_status "$status" 200 "GET /health"
check_body "GET /health" '
import json, sys
data = json.load(open(sys.argv[1]))
assert data.get("status") == "ok", data
assert {"provider", "model", "prompt_version"} <= data.keys(), data
'

# 2. POST -> 201 with the 10 contract keys
post_body=$(python3 -c "import json,sys; print(json.dumps({'id': sys.argv[1], 'message': sys.argv[2]}))" "$ID" "$MESSAGE")
status=$(http POST /solicitudes "$post_body" yes "POST /solicitudes (first)")
expect_status "$status" 201 "POST /solicitudes (first)"
check_body "POST /solicitudes (first)" '
import json, sys
data = json.load(open(sys.argv[1]))
required = {
    "id", "categoria", "prioridad", "area_sugerida", "idioma", "resumen",
    "requiere_info", "pregunta_seguimiento", "confianza", "version_prompt",
}
assert required <= data.keys(), required - data.keys()
'
CATEGORIA=$(python3 -c "import json; print(json.load(open('$BODY_FILE'))['categoria'])")

# 3. same POST again -> 200 (idempotent by id)
status=$(http POST /solicitudes "$post_body" yes "POST /solicitudes (duplicate)")
expect_status "$status" 200 "POST /solicitudes (duplicate)"

# 4. POST without an API key -> 401
status=$(http POST /solicitudes "$post_body" no "POST /solicitudes (no key)")
expect_status "$status" 401 "POST /solicitudes (no key)"

# 5. GET /solicitudes/<id> -> 200
status=$(http GET "/solicitudes/$ID" "" yes "GET /solicitudes/{id}")
expect_status "$status" 200 "GET /solicitudes/{id}"

# 6. PATCH -> 200 and corrected true
patch_body='{"prioridad":"baja","author":"smoke"}'
status=$(http PATCH "/solicitudes/$ID" "$patch_body" yes "PATCH /solicitudes/{id}")
expect_status "$status" 200 "PATCH /solicitudes/{id}"
check_body "PATCH /solicitudes/{id}" '
import json, sys
data = json.load(open(sys.argv[1]))
assert data.get("prioridad") == "baja", data
assert data.get("corrected") is True, data
'

# 7. GET list filtered by categoria -> includes this id
status=$(http GET "/solicitudes?categoria=$CATEGORIA" "" yes "GET /solicitudes?categoria=")
expect_status "$status" 200 "GET /solicitudes?categoria="
check_body "GET /solicitudes?categoria=" "
import json, sys
data = json.load(open(sys.argv[1]))
ids = [item['id'] for item in data.get('items', [])]
assert '$ID' in ids, ids
"

echo "SMOKE OK"
