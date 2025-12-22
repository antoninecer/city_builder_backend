#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8002}"
USER_ID="${USER_ID:-test_ci_$(date +%s)}"
ALLOW_DEV="${ALLOW_DEV:-1}"
VERBOSE="${VERBOSE:-0}"

HAS_JQ=0
if command -v jq >/dev/null 2>&1; then
  HAS_JQ=1
fi

RED=$'\e[31m'
GREEN=$'\e[32m'
YELLOW=$'\e[33m'
RESET=$'\e[0m'

fail() {
  echo "${RED}FAIL${RESET} $1"
  exit 1
}

ok() {
  echo "${GREEN}OK${RESET} $1"
}

print_verbose() {
  if [[ "${VERBOSE}" == "1" ]]; then
    echo "---- response body ----"
    cat
    echo "-----------------------"
  else
    cat >/dev/null
  fi
}

run_test() {
  local label="$1"
  local expected="$2"
  local cmd="$3"

  echo "${YELLOW}TESTUJU:${RESET} ${label}"
  echo "CMD: ${cmd}"

  local tmp_body
  tmp_body="$(mktemp)"
  local http_code
  set +e
  http_code="$(bash -lc "${cmd} -o '${tmp_body}' -w '%{http_code}'" 2>/dev/null)"
  local rc=$?
  set -e

  if [[ $rc -ne 0 ]]; then
    echo "${RED}FAIL${RESET} (curl rc=${rc}) ${label}"
    echo "CMD: ${cmd}"
    echo "Body (if any):"
    cat "${tmp_body}" || true
    rm -f "${tmp_body}"
    exit 1
  fi

  if [[ "${http_code}" != "${expected}" ]]; then
    echo "${RED}FAIL${RESET} ${label} (expected HTTP ${expected}, got ${http_code})"
    echo "CMD: ${cmd}"
    echo "Response:"
    cat "${tmp_body}" || true
    rm -f "${tmp_body}"
    exit 1
  fi

  if [[ $HAS_JQ -eq 1 ]]; then
    if file "${tmp_body}" | grep -qi "json"; then
      if ! jq -e . "${tmp_body}" >/dev/null 2>&1; then
        echo "${RED}FAIL${RESET} ${label} (invalid JSON)"
        echo "CMD: ${cmd}"
        echo "Response:"
        cat "${tmp_body}" || true
        rm -f "${tmp_body}"
        exit 1
      fi
    fi
  fi

  ok "${label} (HTTP ${http_code})"
  cat "${tmp_body}" | print_verbose
  rm -f "${tmp_body}"
}

assert_json_path() {
  local label="$1"
  local cmd="$2"
  local jqexpr="$3"

  [[ $HAS_JQ -eq 1 ]] || { echo "SKIP (no jq): ${label}"; return 0; }

  echo "${YELLOW}TESTUJU:${RESET} ${label}"
  echo "CMD: ${cmd}"

  local tmp_body
  tmp_body="$(mktemp)"

  local http_code
  http_code="$(bash -lc "${cmd} -o '${tmp_body}' -w '%{http_code}'" 2>/dev/null || true)"

  if [[ "${http_code}" != "200" ]]; then
    echo "${RED}FAIL${RESET} ${label} (expected HTTP 200, got ${http_code})"
    echo "CMD: ${cmd}"
    echo "Response:"
    cat "${tmp_body}" || true
    rm -f "${tmp_body}"
    exit 1
  fi

  if ! jq -e "${jqexpr}" "${tmp_body}" >/dev/null 2>&1; then
    echo "${RED}FAIL${RESET} ${label} (jq assertion failed)"
    echo "JQ: ${jqexpr}"
    echo "CMD: ${cmd}"
    echo "Response:"
    cat "${tmp_body}" || true
    rm -f "${tmp_body}"
    exit 1
  fi

  ok "${label}"
  cat "${tmp_body}" | print_verbose
  rm -f "${tmp_body}"
}

CURL_BASE="curl -sS --connect-timeout 4 --max-time 10"

echo "============================================"
echo "BASE_URL=${BASE_URL}"
echo "USER_ID=${USER_ID}"
echo "ALLOW_DEV=${ALLOW_DEV}"
echo "jq=${HAS_JQ}"
echo "============================================"

run_test "GET / (health)" "200" "${CURL_BASE} '${BASE_URL}/'"

run_test "POST /new_game create user" "200" \
  "${CURL_BASE} -X POST '${BASE_URL}/new_game' -H 'Content-Type: application/json' -d '{\"user_id\":\"${USER_ID}\"}'"

run_test "POST /new_game same user -> 409" "409" \
  "${CURL_BASE} -X POST '${BASE_URL}/new_game' -H 'Content-Type: application/json' -d '{\"user_id\":\"${USER_ID}\"}'"

run_test "GET /city/{user_id}" "200" \
  "${CURL_BASE} '${BASE_URL}/city/${USER_ID}'"

assert_json_path "City response has resources + buildings + world" \
  "${CURL_BASE} '${BASE_URL}/city/${USER_ID}'" \
  '.resources.gold != null and .buildings != null and .world != null'

# -------------------------
# Place building & capture ID
# -------------------------
echo "${YELLOW}TESTUJU:${RESET} POST /city/{user_id}/place farm (1,0) + capture building_id"
PLACE_BODY="$(${CURL_BASE} -X POST "${BASE_URL}/city/${USER_ID}/place" \
  -H 'Content-Type: application/json' \
  -d '{"building_type":"farm","x":1,"y":0}' \
  -w '\n%{http_code}' )"

PLACE_HTTP="$(echo "${PLACE_BODY}" | tail -n1)"
PLACE_JSON="$(echo "${PLACE_BODY}" | sed '$d')"

if [[ "${PLACE_HTTP}" != "200" ]]; then
  echo "${RED}FAIL${RESET} place farm (expected 200 got ${PLACE_HTTP})"
  echo "Response:"
  echo "${PLACE_JSON}"
  exit 1
fi

if [[ $HAS_JQ -eq 1 ]]; then
  PLACED_ID="$(echo "${PLACE_JSON}" | jq -r '.building_id // empty')"
else
  # fallback naive parse (works if JSON simple)
  PLACED_ID="$(echo "${PLACE_JSON}" | sed -n 's/.*"building_id":"\([^"]*\)".*/\1/p')"
fi

if [[ -z "${PLACED_ID}" ]]; then
  echo "${RED}FAIL${RESET} place farm: could not extract building_id"
  echo "Response:"
  echo "${PLACE_JSON}"
  exit 1
fi

ok "POST /place returned building_id=${PLACED_ID}"

run_test "POST /city/{user_id}/place farm (1,0) again -> 400" "400" \
  "${CURL_BASE} -X POST '${BASE_URL}/city/${USER_ID}/place' -H 'Content-Type: application/json' -d '{\"building_type\":\"farm\",\"x\":1,\"y\":0}'"

# -------------------------
# Upgrade the placed building (not farm_0)
# -------------------------
echo "${YELLOW}TESTUJU:${RESET} POST /city/{user_id}/upgrade ${PLACED_ID}"
UP_BODY="$(${CURL_BASE} -X POST "${BASE_URL}/city/${USER_ID}/upgrade" \
  -H 'Content-Type: application/json' \
  -d "{\"building_id\":\"${PLACED_ID}\"}" \
  -w '\n%{http_code}' )"

UP_HTTP="$(echo "${UP_BODY}" | tail -n1)"
UP_JSON="$(echo "${UP_BODY}" | sed '$d')"

if [[ "${UP_HTTP}" != "200" ]]; then
  echo "${RED}FAIL${RESET} upgrade ${PLACED_ID} (expected 200 got ${UP_HTTP})"
  echo "CMD: ${CURL_BASE} -X POST '${BASE_URL}/city/${USER_ID}/upgrade' -H 'Content-Type: application/json' -d '{\"building_id\":\"${PLACED_ID}\"}'"
  echo "Response:"
  echo "${UP_JSON}"
  exit 1
fi
ok "Upgrade ${PLACED_ID} (HTTP 200)"
echo "${UP_JSON}" | print_verbose

# If duration_seconds > 0 then second upgrade should be 400
if [[ $HAS_JQ -eq 1 ]]; then
  DURATION="$(echo "${UP_JSON}" | jq -r '.duration_seconds // 0')"
else
  DURATION="1"
fi

if [[ "${DURATION}" != "0" ]]; then
  run_test "POST /city/{user_id}/upgrade ${PLACED_ID} again -> 400" "400" \
    "${CURL_BASE} -X POST '${BASE_URL}/city/${USER_ID}/upgrade' -H 'Content-Type: application/json' -d '{\"building_id\":\"${PLACED_ID}\"}'"
else
  echo "SKIP second upgrade check (duration_seconds == 0)"
fi

# -------------------------
# Demolish: pick first non-townhall if possible
# -------------------------
if [[ $HAS_JQ -eq 1 ]]; then
  echo "${YELLOW}TESTUJU:${RESET} Find a building id to demolish"
  DEMO_ID="$(${CURL_BASE} "${BASE_URL}/city/${USER_ID}" | jq -r '.buildings | keys[] | select(. != "townhall_0")' | head -n 1 || true)"
  if [[ -z "${DEMO_ID}" || "${DEMO_ID}" == "null" ]]; then
    echo "SKIP demolish (no suitable building found)"
  else
    run_test "POST /city/{user_id}/demolish ${DEMO_ID}" "200" \
      "${CURL_BASE} -X POST '${BASE_URL}/city/${USER_ID}/demolish' -H 'Content-Type: application/json' -d '{\"building_id\":\"${DEMO_ID}\"}'"
  fi
else
  echo "SKIP demolish auto-test (install jq to enable)"
fi

run_test "POST /city/{user_id}/demolish townhall_0 -> 400" "400" \
  "${CURL_BASE} -X POST '${BASE_URL}/city/${USER_ID}/demolish' -H 'Content-Type: application/json' -d '{\"building_id\":\"townhall_0\"}'"

# -------------------------
# DEV endpoints (optional)
# -------------------------
if [[ "${ALLOW_DEV}" == "1" ]]; then
  run_test "DEV: POST /dev/resources/{user_id} add big resources" "200" \
    "${CURL_BASE} -X POST '${BASE_URL}/dev/resources/${USER_ID}' -H 'Content-Type: application/json' -d '{\"gold\":1000000,\"wood\":1000000}'"

  run_test "DEV: POST /dev/reset/{user_id}" "200" \
    "${CURL_BASE} -X POST '${BASE_URL}/dev/reset/${USER_ID}'"
else
  echo "DEV tests disabled (ALLOW_DEV=0)"
fi

echo "============================================"
echo "${GREEN}ALL TESTS PASSED${RESET}"
echo "============================================"

