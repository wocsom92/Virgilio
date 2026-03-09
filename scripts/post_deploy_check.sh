#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/post_deploy_check.sh --profile <full|monitor-only> [--env-file .env]

Options:
  --profile <name>   Deployment profile: full | monitor-only (required)
  --env-file <path>  Environment file to read ports/tokens from (default: .env)
  --timeout <sec>    Total wait timeout per endpoint (default: 60)
  -h, --help         Show this help
EOF
}

log() {
  printf '[post-check %s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  echo "post-check error: $*" >&2
  exit 1
}

read_env_value() {
  local file_path="$1"
  local key="$2"
  [[ -f "${file_path}" ]] || return 1
  local raw
  raw="$(awk -F= -v k="${key}" '$1==k {sub(/^[^=]*=/,""); print; exit}' "${file_path}")"
  [[ -n "${raw}" ]] || return 1
  raw="${raw%\"}"
  raw="${raw#\"}"
  raw="${raw%\'}"
  raw="${raw#\'}"
  printf '%s' "${raw}"
}

PROFILE=""
ENV_FILE=".env"
TIMEOUT_SECONDS=60

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE="${2:-}"
      shift 2
      ;;
    --env-file)
      ENV_FILE="${2:-}"
      shift 2
      ;;
    --timeout)
      TIMEOUT_SECONDS="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

case "${PROFILE}" in
  full|monitor-only) ;;
  *) die "--profile must be full or monitor-only" ;;
esac

case "${TIMEOUT_SECONDS}" in
  ''|*[!0-9]*) die "--timeout must be a positive integer" ;;
esac
if [[ "${TIMEOUT_SECONDS}" -lt 1 ]]; then
  die "--timeout must be >= 1"
fi

command -v curl >/dev/null 2>&1 || die "curl is required on remote host"

backend_port="$(read_env_value "${ENV_FILE}" "SERVER_MONITOR_PORT" || true)"
backend_port="${backend_port:-28000}"
monitor_port="$(read_env_value "${ENV_FILE}" "MONITOR_PORT" || true)"
if [[ -z "${monitor_port}" ]]; then
  if [[ "${PROFILE}" == "full" ]]; then
    monitor_port="29000"
  else
    monitor_port="9000"
  fi
fi
monitor_token="$(read_env_value "${ENV_FILE}" "MONITOR_API_TOKEN" || true)"
monitor_token="${monitor_token:-monitor-token}"

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmp_dir}"
}
trap cleanup EXIT

http_check() {
  local label="$1"
  local url="$2"
  local expected_csv="$3"
  local auth_header="${4:-}"
  local deadline=$((SECONDS + TIMEOUT_SECONDS))
  local body_file="${tmp_dir}/body.$$"
  local code="000"

  while (( SECONDS < deadline )); do
    if [[ -n "${auth_header}" ]]; then
      code="$(curl -sS -m 10 -o "${body_file}" -w '%{http_code}' -H "${auth_header}" "${url}" || true)"
    else
      code="$(curl -sS -m 10 -o "${body_file}" -w '%{http_code}' "${url}" || true)"
    fi

    if [[ "${code}" != "404" && "${code}" != 5* ]]; then
      if [[ -n "${expected_csv}" ]]; then
        IFS=',' read -r -a expected_codes <<< "${expected_csv}"
        for expected in "${expected_codes[@]}"; do
          if [[ "${code}" == "${expected}" ]]; then
            log "OK ${label}: ${url} -> ${code}"
            return 0
          fi
        done
      else
        log "OK ${label}: ${url} -> ${code}"
        return 0
      fi
    fi

    sleep 2
  done

  local body
  body="$(cat "${body_file}" 2>/dev/null || true)"
  die "${label} failed: ${url} -> HTTP ${code} (expected: ${expected_csv:-not 404/5xx}). Body: ${body}"
}

log "Starting endpoint smoke checks (profile=${PROFILE}, env=${ENV_FILE})"

if [[ "${PROFILE}" == "full" ]]; then
  http_check "backend-health" "http://127.0.0.1:${backend_port}/healthz" "200"
  http_check "backend-version" "http://127.0.0.1:${backend_port}/version" "200"
  http_check "backend-auth-status" "http://127.0.0.1:${backend_port}/auth/status" "200"
  http_check "frontend-root" "http://127.0.0.1:5173/" "200"
  http_check "frontend-api-health" "http://127.0.0.1:5173/api/healthz" "200"
fi

http_check "monitor-health" "http://127.0.0.1:${monitor_port}/healthz" "200"
http_check "monitor-latest-metrics" "http://127.0.0.1:${monitor_port}/metrics/latest" "200" "Authorization: Bearer ${monitor_token}"

log "All endpoint smoke checks passed."
