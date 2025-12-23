#!/usr/bin/env bash
set -euo pipefail
CLEANUP="${CLEANUP:-1}"

BASE_URL="${BASE_URL:-http://127.0.0.1:8002}"
USER_ID="${USER_ID:-test_ci_$(date +%s)}"
VERBOSE="${VERBOSE:-0}"

CURL_BASE=(curl -sS --connect-timeout 4 --max-time 10)
HAS_JQ=0
command -v jq >/dev/null 2>&1 && HAS_JQ=1

RED=$'\e[31m'
GREEN=$'\e[32m'
YELLOW=$'\e[33m'
RESET=$'\e[0m'

log() { echo "$*" >&2; }
fail() { log "${RED}FAIL${RESET} $1"; exit 1; }
ok()   { log "${GREEN}OK${RESET} $1"; }

print_body_if_verbose() {
  local f="$1"
  if [[ "${VERBOSE}" == "1" ]]; then
    log "---- response body ----"
    cat "$f" >&2
    log "-----------------------"
  fi
}

request() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local expected="${4:-200}"

  local url="${BASE_URL}${path}"
  local tmp; tmp="$(mktemp)"

  log "${YELLOW}TESTUJU:${RESET} ${method} ${path}"
  if [[ -n "${body}" ]]; then
    log "CMD: curl -sS -X ${method} '${url}' -H 'Content-Type: application/json' -d '${body}'"
  else
    log "CMD: curl -sS -X ${method} '${url}'"
  fi

  local code=""
  if [[ -n "${body}" ]]; then
    code="$("${CURL_BASE[@]}" -X "${method}" "${url}" -H 'Content-Type: application/json' -d "${body}" -o "${tmp}" -w '%{http_code}')"
  else
    code="$("${CURL_BASE[@]}" -X "${method}" "${url}" -o "${tmp}" -w '%{http_code}')"
  fi

  if [[ "${code}" != "${expected}" ]]; then
    log "${RED}FAIL${RESET} ${method} ${path} (expected HTTP ${expected}, got ${code})"
    log "Response:"
    cat "${tmp}" >&2 || true
    rm -f "${tmp}"
    exit 1
  fi

  if [[ $HAS_JQ -eq 1 ]]; then
    # Validate JSON if it looks like JSON
    if grep -qE '^\s*\{|\[\s*\{' "${tmp}"; then
      jq -e . "${tmp}" >/dev/null 2>&1 || {
        log "${RED}FAIL${RESET} invalid JSON from ${method} ${path}"
        cat "${tmp}" >&2 || true
        rm -f "${tmp}"
        exit 1
      }
    fi
  fi

  ok "${method} ${path} (HTTP ${code})"
  print_body_if_verbose "${tmp}"

  # IMPORTANT: only body goes to STDOUT (so command substitution is clean)
  cat "${tmp}"
  rm -f "${tmp}"
}

assert_jq() {
  local label="$1"
  local json="$2"
  local expr="$3"

  [[ $HAS_JQ -eq 1 ]] || { log "SKIP (no jq): ${label}"; return 0; }

  log "${YELLOW}TESTUJU:${RESET} ${label}"
  log "JQ: ${expr}"

  # First: ensure it's valid JSON
  echo "${json}" | jq -e . >/dev/null 2>&1 || {
    log "${RED}FAIL${RESET} ${label} (not valid JSON input to jq)"
    log "Input was:"
    echo "${json}" >&2
    exit 1
  }

  echo "${json}" | jq -e "${expr}" >/dev/null 2>&1 || fail "${label} (jq assertion failed)"
  ok "${label}"
}

extract_user_id() {
  local json="$1"
  if [[ $HAS_JQ -eq 1 ]]; then
    echo "${json}" | jq -r '.user_id // empty'
  else
    echo "${json}" | sed -n 's/.*"user_id":"\([^"]*\)".*/\1/p'
  fi
}

echo "============================================"
echo "BASE_URL=${BASE_URL}"
echo "USER_ID=${USER_ID}"
echo "jq=${HAS_JQ}"
echo "============================================"

# 1) Health
_="$(request GET "/" "" 200)"

# 2) New game (create user)
NEW_JSON="$(request POST "/new_game" "{\"user_id\":\"${USER_ID}\"}" 200)"

# Use returned user_id (prevents surprises)
REAL_UID="$(extract_user_id "${NEW_JSON}")"
[[ -n "${REAL_UID}" ]] || fail "new_game did not return user_id"
ok "new_game returned user_id=${REAL_UID}"
USER_ID="${REAL_UID}"

assert_jq "new_game returns user_id (non-empty)" "${NEW_JSON}" '.user_id != null and (.user_id | length) > 0'

# 3) Load city & print summary
CITY_JSON="$(request GET "/city/${USER_ID}" "" 200)"
assert_jq "city has resources + buildings + world" "${CITY_JSON}" '.resources.gold != null and .buildings != null and .world != null and .world.radius != null'

if [[ $HAS_JQ -eq 1 ]]; then
  log "---- SUMMARY (before expand) ----"
  log "$(echo "${CITY_JSON}" | jq -r '"radius=\(.world.radius) gold=\(.resources.gold) wood=\(.resources.wood)"')"
  log "$(echo "${CITY_JSON}" | jq -r '.world.bounds | "bounds: x[\(.min_x)..\(.max_x)] y[\(.min_y)..\(.max_y)]"')"
  log "buildings:"
  echo "${CITY_JSON}" | jq -r '.buildings | to_entries[] | "\(.key)\t\(.value.type)\t@\(.value.x),\(.value.y)\tL\(.value.level)"' | sort >&2
  log "---------------------------------"
fi

# 4) Expand world by +1 radius
EXPAND_JSON="$(request POST "/city/${USER_ID}/expand" "{}" 200)"
assert_jq "expand returns new_radius" "${EXPAND_JSON}" ".new_radius != null"

# 5) Reload & compute corners from bounds
CITY2_JSON="$(request GET "/city/${USER_ID}" "" 200)"

if [[ $HAS_JQ -eq 1 ]]; then
  MINX="$(echo "${CITY2_JSON}" | jq -r '.world.bounds.min_x')"
  MAXX="$(echo "${CITY2_JSON}" | jq -r '.world.bounds.max_x')"
  MINY="$(echo "${CITY2_JSON}" | jq -r '.world.bounds.min_y')"
  MAXY="$(echo "${CITY2_JSON}" | jq -r '.world.bounds.max_y')"

  log "---- SUMMARY (after expand) ----"
  log "$(echo "${CITY2_JSON}" | jq -r '"radius=\(.world.radius) gold=\(.resources.gold) wood=\(.resources.wood)"')"
  log "$(echo "${CITY2_JSON}" | jq -r '.world.bounds | "bounds: x[\(.min_x)..\(.max_x)] y[\(.min_y)..\(.max_y)]"')"
  log "--------------------------------"

  log "Corners to place farms:"
  log "  (${MINX},${MINY})"
  log "  (${MAXX},${MINY})"
  log "  (${MINX},${MAXY})"
  log "  (${MAXX},${MAXY})"

  place_corner() {
    local x="$1"; local y="$2"
    log "${YELLOW}TESTUJU:${RESET} place farm at (${x},${y})"
    local tmp; tmp="$(mktemp)"
    local code
    code="$("${CURL_BASE[@]}" -X POST "${BASE_URL}/city/${USER_ID}/place" \
      -H 'Content-Type: application/json' \
      -d "{\"building_type\":\"farm\",\"x\":${x},\"y\":${y}}" \
      -o "${tmp}" -w '%{http_code}')"

    if [[ "${code}" == "200" ]]; then
      ok "placed farm (${x},${y})"
    elif [[ "${code}" == "400" ]]; then
      log "OK (already occupied) farm (${x},${y})"
      [[ "${VERBOSE}" == "1" ]] && cat "${tmp}" >&2
    else
      log "${RED}FAIL${RESET} place farm (${x},${y}) unexpected HTTP ${code}"
      cat "${tmp}" >&2 || true
      rm -f "${tmp}"
      exit 1
    fi
    rm -f "${tmp}"
  }

  place_corner "${MINX}" "${MINY}"
  place_corner "${MAXX}" "${MINY}"
  place_corner "${MINX}" "${MAXY}"
  place_corner "${MAXX}" "${MAXY}"
else
  fail "No jq installed -> can't compute corners from bounds. Install jq."
fi

# 7) Final city dump (what is where)
CITY3_JSON="$(request GET "/city/${USER_ID}" "" 200)"

log "---- FINAL WORLD/RESOURCES ----"
echo "${CITY3_JSON}" | jq -r '"radius=\(.world.radius) gold=\(.resources.gold) wood=\(.resources.wood)"' >&2
echo "${CITY3_JSON}" | jq -r '.world.bounds | "bounds: x[\(.min_x)..\(.max_x)] y[\(.min_y)..\(.max_y)]"' >&2
log ""

log "---- FINAL BUILDINGS (sorted by y,x) ----"
echo "${CITY3_JSON}" | jq -r '
  .buildings
  | to_entries
  | map({id:.key, type:.value.type, level:(.value.level//1), x:.value.x, y:.value.y})
  | sort_by(.y,.x,.id)
  | .[]
  | "\(.id)\t\(.type)\t@\(.x),\(.y)\tL\(.level)"
' >&2
log "----------------------------------------"

echo "============================================"
echo "${GREEN}ALL TESTS PASSED${RESET}"
echo "============================================"

if [[ "${CLEANUP}" == "1" ]]; then
  log "${YELLOW}TESTUJU:${RESET} DEV wipe (cleanup) /dev/wipe/${USER_ID}"
  _="$(request POST "/dev/wipe/${USER_ID}" "{}" 200)"
  ok "Cleanup done (wiped ${USER_ID})"
else
  log "Cleanup skipped (CLEANUP=0)"
fi


exit 0