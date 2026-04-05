#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/configure_logs_retention.sh [--env-file .env]

Options:
  --env-file <path>  Environment file to read settings from (default: .env)
  -h, --help         Show this help
EOF
}

log() {
  printf '[logs-retention %s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  echo "logs-retention error: $*" >&2
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

put_json() {
  local url="$1"
  local payload="$2"
  curl -fsS -X PUT "${url}" -H 'Content-Type: application/json' -d "${payload}"
}

ENV_FILE=".env"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="${2:-}"
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

command -v curl >/dev/null 2>&1 || die "curl is required"

compose_profiles="$(read_env_value "${ENV_FILE}" "COMPOSE_PROFILES" || true)"
if [[ ",${compose_profiles}," != *",logs,"* ]]; then
  log "Skipping Elasticsearch log retention because COMPOSE_PROFILES does not include logs."
  exit 0
fi

elasticsearch_port="$(read_env_value "${ENV_FILE}" "ELASTICSEARCH_PORT" || true)"
elasticsearch_port="${elasticsearch_port:-9200}"
retention_days="$(read_env_value "${ENV_FILE}" "ELASTICSEARCH_LOG_RETENTION_DAYS" || true)"
retention_days="${retention_days:-7}"

case "${retention_days}" in
  ''|*[!0-9]*)
    die "ELASTICSEARCH_LOG_RETENTION_DAYS must be a positive integer"
    ;;
esac

if [[ "${retention_days}" -lt 1 ]]; then
  die "ELASTICSEARCH_LOG_RETENTION_DAYS must be >= 1"
fi

base_url="http://127.0.0.1:${elasticsearch_port}"
policy_name="server-monitor-logs-${retention_days}d"
template_name="server-monitor-logs"

deadline=$((SECONDS + 60))
until curl -fsS "${base_url}/_cluster/health?wait_for_status=yellow&timeout=1s" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    die "Elasticsearch did not become ready within 60 seconds"
  fi
  sleep 2
done

policy_payload="$(cat <<EOF
{
  "policy": {
    "phases": {
      "hot": {
        "min_age": "0ms",
        "actions": {}
      },
      "delete": {
        "min_age": "${retention_days}d",
        "actions": {
          "delete": {
            "delete_searchable_snapshot": true
          }
        }
      }
    },
    "_meta": {
      "managed_by": "server_monitor_deploy",
      "description": "Automatically delete server monitor logs after ${retention_days} days"
    }
  }
}
EOF
)"

template_payload="$(cat <<EOF
{
  "index_patterns": ["server-monitor-logs-*"],
  "priority": 500,
  "data_stream": {},
  "template": {
    "settings": {
      "index.number_of_shards": 1,
      "index.number_of_replicas": 0,
      "index.lifecycle.name": "${policy_name}"
    }
  },
  "_meta": {
    "managed_by": "server_monitor_deploy",
    "description": "Template for server monitor log data streams with ${retention_days}-day retention"
  }
}
EOF
)"

settings_payload="$(cat <<EOF
{
  "index": {
    "lifecycle": {
      "name": "${policy_name}"
    }
  }
}
EOF
)"

log "Applying ILM policy ${policy_name}"
put_json "${base_url}/_ilm/policy/${policy_name}" "${policy_payload}" >/dev/null

log "Applying index template ${template_name}"
put_json "${base_url}/_index_template/${template_name}" "${template_payload}" >/dev/null

log "Applying lifecycle policy to existing server monitor log backing indices"
curl -fsS -X PUT "${base_url}/.ds-server-monitor-logs-*/_settings?expand_wildcards=all&allow_no_indices=true" \
  -H 'Content-Type: application/json' \
  -d "${settings_payload}" >/dev/null || true

log "Current ILM status for server monitor logs:"
curl -fsS "${base_url}/.ds-server-monitor-logs-*/_ilm/explain?expand_wildcards=all&only_managed=false"
echo
