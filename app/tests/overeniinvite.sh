#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8002}"
NOW=$(date +%s)

OWNER="owner_$NOW"
GUEST="guest_$NOW"

echo "BASE_URL=$BASE_URL"
echo "OWNER=$OWNER"
echo "GUEST=$GUEST"
echo

# -----------------------------------------------------------------------------
# helper: POST JSON + jq + HTTP status check
# -----------------------------------------------------------------------------
post_json () {
  local url="$1"
  local json="$2"

  RESP=$(curl -s -w "\n%{http_code}" -X POST "$url" \
    -H "Content-Type: application/json" \
    -d "$json")

  BODY=$(echo "$RESP" | head -n -1)
  CODE=$(echo "$RESP" | tail -n 1)

  if [[ "$CODE" != "200" && "$CODE" != "201" ]]; then
    echo "❌ HTTP $CODE from $url"
    echo "$BODY"
    exit 1
  fi

  echo "$BODY" | jq
}

# -----------------------------------------------------------------------------
# helper: GET + jq + HTTP status check
# -----------------------------------------------------------------------------
get_json () {
  local url="$1"

  RESP=$(curl -s -w "\n%{http_code}" "$url")
  BODY=$(echo "$RESP" | head -n -1)
  CODE=$(echo "$RESP" | tail -n 1)

  if [[ "$CODE" != "200" ]]; then
    echo "❌ HTTP $CODE from $url"
    echo "$BODY"
    exit 1
  fi

  echo "$BODY" | jq
}

# -----------------------------------------------------------------------------
echo "=== 1. Create OWNER ==="
post_json "$BASE_URL/new_game" "{\"user_id\":\"$OWNER\"}"

echo
echo "=== 2. Owner builds farm ==="
post_json "$BASE_URL/city/$OWNER/place" \
  '{"building_type":"farm","x":1,"y":0}'

echo
echo "=== 3. Owner builds lumbermill ==="
post_json "$BASE_URL/city/$OWNER/place" \
  '{"building_type":"lumbermill","x":0,"y":1}'

echo
echo "=== 4. Owner creates invite ==="
INVITE=$(curl -s -X POST "$BASE_URL/city/$OWNER/invite" \
  -H "Content-Type: application/json" \
  -d '{"role":"editor"}' | jq -r .invite_token)

if [[ -z "$INVITE" || "$INVITE" == "null" ]]; then
  echo "❌ Invite token not returned"
  exit 1
fi

echo "INVITE TOKEN: $INVITE"

echo
echo "=== 5. Guest accepts invite ==="
post_json "$BASE_URL/invite/accept" \
  "{\"token\":\"$INVITE\",\"user_id\":\"$GUEST\"}"

echo
echo "=== 6. Guest GET city (must match owner city) ==="
get_json "$BASE_URL/city/$GUEST"

echo
echo "=== 7. Owner GET city (for comparison) ==="
get_json "$BASE_URL/city/$OWNER"

echo
echo "✅ ALL TESTS PASSED"
