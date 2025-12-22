#!/usr/bin/env bash
set -euo pipefail

# ============================
# Config
# ============================
BASE_URL="${BASE_URL:-http://127.0.0.1:8002}"
USER_ID="${USER_ID:-test_ci_$(date +%s)}"

# 1 = run dev tests, 0 = skip
ALLOW_DEV="${ALLOW_DEV:-1}"

# timeouts
CURL_COMMON=(-sS --connect-timeout 4 --max-time 10)

# jq presence
if command -v jq >/dev/null 2>&1; then
  HAS_JQ=1
else
  HAS_JQ=0
fi

hr() { echo "============================================" >&2; }
log() { echo "$*" >&2; }

fail() {
  log "FAIL $1"
  if [ -n "${2:-}" ]; then
    log "Response:"
    log "$2"
  fi
  exit 1
}

ok() { log "OK $1"; }

# run_http_expect NAME EXPECT_HTTP CAPTURE(0/1) CMD...
# - logs go to stderr
# - if CAPTURE=1: prints body to stdout (so you can do VAR="$(...)")
# - else: prints body to stderr (so you still see it)
run_http_expect() {
  local name="$1"
  local expect="$2"
  local capture="$3"
  shift 3
  local cmd=( "$@" )

  log "TESTUJU: $name"
  log -n "CMD:"
  printf " %q" "${cmd[@]}" >&2
  log ""

  local out http body
  out="$("${cmd[@]}" -w $'\n%{http_code}')"
  http="$(echo "$out" | tail -n1)"
  body="$(echo "$out" | sed '$d')"

  if [ "$http" != "$expect" ]; then
    fail "$name (expected HTTP $expect, got $http)" "$body"
  fi

  ok "$name (HTTP $http)"

  if [ "$capture" = "1" ]; then
    printf "%s" "$body"
  else
    # print body for human reading, but don't pollute stdout
    if [ -n "$body" ]; then
      log "$body"
    fi
  fi
}

assert_json_has() {
  local name="$1"
  local json="$2"
  local jq_expr="$3"

  log "TESTUJU: $name"
  if [ "$HAS_JQ" != "1" ]; then
    log "SKIP (jq not installed)"
    return 0
  fi

  # jq parse check
  echo "$json" | jq -e "$jq_expr" >/dev/null 2>&1 || fail "$name (jq assert failed)" "$json"
  ok "$name"
}

say_hdr() {
  hr
  log "BASE_URL=$BASE_URL"
  log "USER_ID=$USER_ID"
  log "ALLOW_DEV=$ALLOW_DEV"
  log "jq=$HAS_JQ"
  hr
}

# ============================
# Tests
# ============================
say_hdr

run_http_expect "GET / (health)" "200" 0 \
  curl "${CURL_COMMON[@]}" "$BASE_URL/"

run_http_expect "POST /new_game create user" "200" 0 \
  curl "${CURL_COMMON[@]}" -X POST "$BASE_URL/new_game" \
    -H 'Content-Type: application/json' \
    -d "{\"user_id\":\"$USER_ID\"}"

run_http_expect "POST /new_game same user -> 409" "409" 0 \
  curl "${CURL_COMMON[@]}" -X POST "$BASE_URL/new_game" \
    -H 'Content-Type: application/json' \
    -d "{\"user_id\":\"$USER_ID\"}"

CITY_JSON="$(run_http_expect "GET /city/{user_id}" "200" 1 \
  curl "${CURL_COMMON[@]}" "$BASE_URL/city/$USER_ID")"
log "$CITY_JSON"

assert_json_has "City response has resources.gold + buildings + world" "$CITY_JSON" \
  '.resources.gold != null and .buildings != null and .world != null'

run_http_expect "POST /city/{user_id}/place farm (1,0)" "200" 0 \
  curl "${CURL_COMMON[@]}" -X POST "$BASE_URL/city/$USER_ID/place" \
    -H 'Content-Type: application/json' \
    -d '{"building_type":"farm","x":1,"y":0}'

run_http_expect "POST /city/{user_id}/place farm (1,0) again -> 400" "400" 0 \
  curl "${CURL_COMMON[@]}" -X POST "$BASE_URL/city/$USER_ID/place" \
    -H 'Content-Type: application/json' \
    -d '{"building_type":"farm","x":1,"y":0}'

CITY_JSON="$(run_http_expect "GET /city/{user_id} after place" "200" 1 \
  curl "${CURL_COMMON[@]}" "$BASE_URL/city/$USER_ID")"
log "$CITY_JSON"

FARM_ID=""
if [ "$HAS_JQ" = "1" ]; then
  FARM_ID="$(echo "$CITY_JSON" | jq -r '.buildings | to_entries[] | select(.value.type=="farm") | .key' | head -n1)"
fi
if [ -z "$FARM_ID" ] || [ "$FARM_ID" = "null" ]; then
  fail "Could not find any farm in city response (needed for upgrade test)" "$CITY_JSON"
fi
log "Picked FARM_ID=$FARM_ID"

run_http_expect "POST /city/{user_id}/upgrade farm ($FARM_ID)" "200" 0 \
  curl "${CURL_COMMON[@]}" -X POST "$BASE_URL/city/$USER_ID/upgrade" \
    -H 'Content-Type: application/json' \
    -d "{\"building_id\":\"$FARM_ID\"}"

# Demolish: place a house then demolish it
PLACE_HOUSE_JSON="$(run_http_expect "POST /city/{user_id}/place house (2,0)" "200" 1 \
  curl "${CURL_COMMON[@]}" -X POST "$BASE_URL/city/$USER_ID/place" \
    -H 'Content-Type: application/json' \
    -d '{"building_type":"house","x":2,"y":0}')"
log "$PLACE_HOUSE_JSON"

HOUSE_ID=""
if [ "$HAS_JQ" = "1" ]; then
  HOUSE_ID="$(echo "$PLACE_HOUSE_JSON" | jq -r '.building_id // empty')"
fi
if [ -n "$HOUSE_ID" ]; then
  run_http_expect "POST /city/{user_id}/demolish house ($HOUSE_ID)" "200" 0 \
    curl "${CURL_COMMON[@]}" -X POST "$BASE_URL/city/$USER_ID/demolish" \
      -H 'Content-Type: application/json' \
      -d "{\"building_id\":\"$HOUSE_ID\"}"
else
  log "SKIP demolish test (could not parse house id; install jq?)"
fi

run_http_expect "POST /city/{user_id}/expand" "200" 0 \
  curl "${CURL_COMMON[@]}" -X POST "$BASE_URL/city/$USER_ID/expand" \
    -H 'Content-Type: application/json' \
    -d '{}'

# ============================
# DEV Tests
# ============================
if [ "$ALLOW_DEV" = "1" ]; then
  run_http_expect "DEV: POST /dev/grant/{user_id} give big resources" "200" 0 \
    curl "${CURL_COMMON[@]}" -X POST "$BASE_URL/dev/grant/$USER_ID" \
      -H 'Content-Type: application/json' \
      -d '{"gold":1000000,"wood":1000000}'

  run_http_expect "DEV: POST /dev/world/set_radius/{user_id} radius=3" "200" 0 \
      curl "${CURL_COMMON[@]}" -X POST -G "$BASE_URL/dev/world/set_radius/$USER_ID" \
        --data-urlencode "radius=3"

  run_http_expect "DEV: POST /dev/reset/{user_id}" "200" 0 \
    curl "${CURL_COMMON[@]}" -X POST "$BASE_URL/dev/reset/$USER_ID" \
      -H 'Content-Type: application/json' \
      -d '{}'

  run_http_expect "DEV: POST /dev/wipe/{user_id}" "200" 0 \
    curl "${CURL_COMMON[@]}" -X POST "$BASE_URL/dev/wipe/$USER_ID" \
      -H 'Content-Type: application/json' \
      -d '{}'
else
  log "DEV tests skipped (ALLOW_DEV=0)"
fi

hr
log "ALL TESTS PASSED ✅"
hr

