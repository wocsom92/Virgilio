#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_FILE_DEFAULT="${SCRIPT_DIR}/deploy.targets.env"

usage() {
  cat <<'EOF'
Usage:
  scripts/deploy.sh --target <name> [options]

Options:
  --target <name>            Target name from config file (required).
  --profile <name>           Deployment profile: full | monitor-only (default: target value or full).
  --config <path>            Path to targets config (default: scripts/deploy.targets.env).
  --source <path>            Source repo root to deploy (default: current repo root).
  --auth <mode>              Auth mode: key | password (default: target value or key).
  --password <value>         SSH password (optional; prompts when auth=password and not provided).
  --key-path <path>          SSH private key path (optional; can come from config).
  --purge-mode <mode>        Cleanup mode: managed | full (default: target value or managed).
  --verbose                  Show extra SSH/SFTP diagnostics.
  --yes                      Non-interactive mode; skip confirmation prompt.
  -h, --help                 Show this help.

Profiles:
  full         Deploy backend + frontend + monitor stack via docker-compose.yml
  monitor-only Deploy monitor-only stack via docker-compose.monitor.yml
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  echo "Error: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

read_env_value() {
  local file_path="$1"
  local key="$2"
  [[ -f "${file_path}" ]] || return 1
  local raw
  raw="$(awk -F= -v k="${key}" '$1==k {sub(/^[^=]*=/,""); print; exit}' "${file_path}")"
  [[ -n "${raw}" ]] || return 1
  # Trim optional surrounding single/double quotes.
  raw="${raw%\"}"
  raw="${raw#\"}"
  raw="${raw%\'}"
  raw="${raw#\'}"
  printf '%s' "${raw}"
}

CONFIG_FILE="${CONFIG_FILE_DEFAULT}"
TARGET=""
PROFILE=""
SOURCE_ROOT="${REPO_ROOT}"
AUTH_MODE=""
PASSWORD=""
KEY_PATH=""
ASSUME_YES=0
VERBOSE=0
PURGE_MODE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      TARGET="${2:-}"
      shift 2
      ;;
    --profile)
      PROFILE="${2:-}"
      shift 2
      ;;
    --config)
      CONFIG_FILE="${2:-}"
      shift 2
      ;;
    --source)
      SOURCE_ROOT="${2:-}"
      shift 2
      ;;
    --auth)
      AUTH_MODE="${2:-}"
      shift 2
      ;;
    --password)
      PASSWORD="${2:-}"
      shift 2
      ;;
    --key-path)
      KEY_PATH="${2:-}"
      shift 2
      ;;
    --purge-mode)
      PURGE_MODE="${2:-}"
      shift 2
      ;;
    --yes)
      ASSUME_YES=1
      shift
      ;;
    --verbose)
      VERBOSE=1
      shift
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

[[ -n "${TARGET}" ]] || die "--target is required"
[[ -f "${CONFIG_FILE}" ]] || die "Config file not found: ${CONFIG_FILE}"
[[ -d "${SOURCE_ROOT}" ]] || die "Source root not found: ${SOURCE_ROOT}"

# shellcheck source=/dev/null
source "${CONFIG_FILE}"

target_key="TARGET_${TARGET}_"

get_target_var() {
  local suffix="$1"
  local var_name="${target_key}${suffix}"
  printf '%s' "${!var_name:-}"
}

HOST="$(get_target_var HOST)"
PORT="$(get_target_var PORT)"
USER_NAME="$(get_target_var USER)"
DEPLOY_PATH="$(get_target_var DEPLOY_PATH)"
TARGET_PROFILE="$(get_target_var PROFILE)"
TARGET_AUTH="$(get_target_var AUTH)"
TARGET_KEY_PATH="$(get_target_var KEY_PATH)"
TARGET_PASSWORD="$(get_target_var PASSWORD)"
TARGET_ENV_FILE="$(get_target_var ENV_FILE)"
TARGET_REMOTE_ENV_NAME="$(get_target_var REMOTE_ENV_NAME)"
TARGET_DOCKER_USE_SUDO="$(get_target_var DOCKER_USE_SUDO)"
TARGET_PURGE_MODE="$(get_target_var PURGE_MODE)"

[[ -n "${HOST}" ]] || die "Missing TARGET_${TARGET}_HOST in ${CONFIG_FILE}"
[[ -n "${USER_NAME}" ]] || die "Missing TARGET_${TARGET}_USER in ${CONFIG_FILE}"
[[ -n "${DEPLOY_PATH}" ]] || die "Missing TARGET_${TARGET}_DEPLOY_PATH in ${CONFIG_FILE}"
[[ "${DEPLOY_PATH}" = /* ]] || die "DEPLOY_PATH must be absolute: ${DEPLOY_PATH}"

case "${DEPLOY_PATH}" in
  "/"|"/home"|"/root"|"/var"|"/usr"|"/opt"|"/tmp"|"/etc")
    die "Refusing dangerous DEPLOY_PATH: ${DEPLOY_PATH}"
    ;;
esac

PORT="${PORT:-22}"
PROFILE="${PROFILE:-${TARGET_PROFILE:-full}}"
AUTH_MODE="${AUTH_MODE:-${TARGET_AUTH:-key}}"
KEY_PATH="${KEY_PATH:-${TARGET_KEY_PATH:-}}"
PASSWORD="${PASSWORD:-${TARGET_PASSWORD:-}}"
TARGET_REMOTE_ENV_NAME="${TARGET_REMOTE_ENV_NAME:-.env}"
TARGET_DOCKER_USE_SUDO="${TARGET_DOCKER_USE_SUDO:-false}"
TARGET_PURGE_MODE="${PURGE_MODE:-${TARGET_PURGE_MODE:-managed}}"

case "${PROFILE}" in
  full|monitor-only) ;;
  *) die "Invalid --profile '${PROFILE}'. Use full or monitor-only." ;;
esac

case "${AUTH_MODE}" in
  key|password) ;;
  *) die "Invalid --auth '${AUTH_MODE}'. Use key or password." ;;
esac

case "${TARGET_DOCKER_USE_SUDO}" in
  true|false) ;;
  *) die "Invalid TARGET_${TARGET}_DOCKER_USE_SUDO value '${TARGET_DOCKER_USE_SUDO}'. Use true or false." ;;
esac

case "${TARGET_PURGE_MODE}" in
  managed|full) ;;
  *) die "Invalid purge mode '${TARGET_PURGE_MODE}'. Use managed or full." ;;
esac

if [[ "${AUTH_MODE}" == "key" ]]; then
  [[ -n "${KEY_PATH}" ]] || KEY_PATH="${HOME}/.ssh/id_rsa"
  [[ -f "${KEY_PATH}" ]] || die "SSH key not found: ${KEY_PATH}"
fi

if [[ "${AUTH_MODE}" == "password" && -z "${PASSWORD}" ]]; then
  read -r -s -p "SSH password for ${USER_NAME}@${HOST}: " PASSWORD
  echo
fi

LOCAL_ENV_FILE=""
if [[ -n "${TARGET_ENV_FILE}" ]]; then
  if [[ "${TARGET_ENV_FILE}" = /* ]]; then
    LOCAL_ENV_FILE="${TARGET_ENV_FILE}"
  else
    LOCAL_ENV_FILE="${SOURCE_ROOT}/${TARGET_ENV_FILE}"
  fi
  [[ -f "${LOCAL_ENV_FILE}" ]] || die "Configured env file not found: ${LOCAL_ENV_FILE}"
fi

require_cmd ssh
require_cmd sftp
require_cmd rsync
if [[ "${AUTH_MODE}" == "password" ]]; then
  require_cmd sshpass
fi

SSH_BASE_OPTS=(
  -p "${PORT}"
  -o BatchMode=no
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=15
  -o ServerAliveInterval=10
  -o ServerAliveCountMax=3
)
if [[ "${AUTH_MODE}" == "key" ]]; then
  SSH_BASE_OPTS+=(-i "${KEY_PATH}")
fi
if [[ "${VERBOSE}" -eq 1 ]]; then
  SSH_BASE_OPTS+=(-v)
fi

run_ssh() {
  local remote_cmd="$1"
  log "SSH command: ${remote_cmd}"
  if [[ "${AUTH_MODE}" == "password" ]]; then
    SSHPASS="${PASSWORD}" sshpass -e ssh "${SSH_BASE_OPTS[@]}" "${USER_NAME}@${HOST}" "${remote_cmd}"
  else
    ssh "${SSH_BASE_OPTS[@]}" "${USER_NAME}@${HOST}" "${remote_cmd}"
  fi
}

run_sftp_batch() {
  local batch_file="$1"
  log "SFTP batch file: ${batch_file}"
  if [[ "${VERBOSE}" -eq 1 ]]; then
    log "SFTP batch commands:"
    sed 's/^/[SFTP] /' "${batch_file}"
  fi
  local sftp_opts=(-P "${PORT}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o BatchMode=no)
  if [[ "${AUTH_MODE}" == "password" ]]; then
    sftp_opts+=(-o PreferredAuthentications=password -o PubkeyAuthentication=no)
  fi
  if [[ "${VERBOSE}" -eq 1 ]]; then
    sftp_opts+=(-v)
  fi
  if [[ "${AUTH_MODE}" == "password" ]]; then
    SSHPASS="${PASSWORD}" sshpass -e sftp "${sftp_opts[@]}" -b "${batch_file}" "${USER_NAME}@${HOST}"
  else
    sftp "${sftp_opts[@]}" -i "${KEY_PATH}" -b "${batch_file}" "${USER_NAME}@${HOST}"
  fi
}

profile_items=()
if [[ "${PROFILE}" == "full" ]]; then
  profile_items=(
    backend
    frontend
    monitor
    docker
    docker-compose.yml
    docker-compose.monitor.yml
    docker-compose.monitor.nginx.yml
    .dockerignore
  )
else
  profile_items=(
    monitor
    docker
    docker-compose.monitor.yml
    docker-compose.monitor.nginx.yml
    .dockerignore
  )
fi

for item in "${profile_items[@]}"; do
  [[ -e "${SOURCE_ROOT}/${item}" ]] || die "Required source item missing: ${SOURCE_ROOT}/${item}"
done

if [[ "${ASSUME_YES}" -ne 1 ]]; then
  echo "Target:      ${TARGET} (${USER_NAME}@${HOST}:${PORT})"
  echo "Deploy path: ${DEPLOY_PATH}"
  echo "Profile:     ${PROFILE}"
  echo "Purge mode:  ${TARGET_PURGE_MODE}"
  echo "Auth:        ${AUTH_MODE}"
  if [[ "${VERBOSE}" -eq 1 ]]; then
    echo "Verbose:     enabled"
  fi
  echo "Docker sudo: ${TARGET_DOCKER_USE_SUDO}"
  if [[ -n "${LOCAL_ENV_FILE}" ]]; then
    echo "Env file:    ${LOCAL_ENV_FILE} -> ${TARGET_REMOTE_ENV_NAME}"
  fi
  read -r -p "Continue? [y/N] " confirm
  [[ "${confirm}" =~ ^[Yy]$ ]] || die "Aborted by user."
fi

stage_dir="$(mktemp -d)"
batch_file="$(mktemp)"
cleanup() {
  rm -rf "${stage_dir}"
  rm -f "${batch_file}"
}
trap cleanup EXIT

log "Preparing staging directory: ${stage_dir}"
copy_into_stage() {
  local rel_path="$1"
  local src="${SOURCE_ROOT}/${rel_path}"
  local dst="${stage_dir}/${rel_path}"
  if [[ -d "${src}" ]]; then
    mkdir -p "${dst}"
    rsync -a \
      --exclude ".git/" \
      --exclude "__pycache__/" \
      --exclude ".pytest_cache/" \
      --exclude ".mypy_cache/" \
      --exclude ".ruff_cache/" \
      --exclude ".venv/" \
      --exclude "venv/" \
      --exclude "env/" \
      --exclude "node_modules/" \
      --exclude "dist/" \
      "${src}/" "${dst}/"
  else
    mkdir -p "$(dirname "${dst}")"
    cp "${src}" "${dst}"
  fi
}

for item in "${profile_items[@]}"; do
  log "Staging item: ${item}"
  copy_into_stage "${item}"
done

if [[ -n "${LOCAL_ENV_FILE}" ]]; then
  log "Staging env file: ${LOCAL_ENV_FILE} -> ${TARGET_REMOTE_ENV_NAME}"
  cp "${LOCAL_ENV_FILE}" "${stage_dir}/${TARGET_REMOTE_ENV_NAME}"
fi

log "Running SSH connectivity check..."
run_ssh "echo 'connected to remote host:' \$(hostname)"

docker_prefix="docker"
if [[ "${TARGET_DOCKER_USE_SUDO}" == "true" ]]; then
  docker_prefix="sudo docker"
fi

log "Checking remote Docker access..."
docker_check_output=""
if ! docker_check_output="$(run_ssh "${docker_prefix} info 2>&1" 2>&1)"; then
  if [[ "${TARGET_DOCKER_USE_SUDO}" == "true" ]]; then
    die "Remote Docker access failed for user '${USER_NAME}' using sudo. Ensure the user has passwordless sudo for docker (e.g. sudoers NOPASSWD) or add the user to docker group and set TARGET_${TARGET}_DOCKER_USE_SUDO=false. Details: ${docker_check_output}"
  fi
  die "Remote Docker access failed for user '${USER_NAME}'. Add the user to docker group or set TARGET_${TARGET}_DOCKER_USE_SUDO=true (requires non-interactive sudo rights). Details: ${docker_check_output}"
fi

if [[ "${TARGET_PURGE_MODE}" == "full" ]]; then
  log "Cleaning remote deploy path (full): ${DEPLOY_PATH}"
  run_ssh "rm -rf \"${DEPLOY_PATH}\" && mkdir -p \"${DEPLOY_PATH}\""
else
  log "Cleaning remote deploy path (managed items only): ${DEPLOY_PATH}"
  cleanup_items=("${profile_items[@]}")
  if [[ -n "${LOCAL_ENV_FILE}" ]]; then
    cleanup_items+=("${TARGET_REMOTE_ENV_NAME}")
  fi
  cleanup_cmd="mkdir -p \"${DEPLOY_PATH}\" && cd \"${DEPLOY_PATH}\""
  for item in "${cleanup_items[@]}"; do
    cleanup_cmd="${cleanup_cmd} && rm -rf \"${item}\""
  done
  run_ssh "${cleanup_cmd}"
fi

log "Building SFTP upload batch..."
{
  for item in "${profile_items[@]}"; do
    local_item="${stage_dir}/${item}"
    if [[ -d "${local_item}" ]]; then
      echo "put -r ${local_item} ${DEPLOY_PATH}"
    else
      echo "put ${local_item} ${DEPLOY_PATH}/${item}"
    fi
  done
  if [[ -n "${LOCAL_ENV_FILE}" ]]; then
    echo "put ${stage_dir}/${TARGET_REMOTE_ENV_NAME} ${DEPLOY_PATH}/${TARGET_REMOTE_ENV_NAME}"
  fi
} > "${batch_file}"

log "Uploading files via SFTP..."
run_sftp_batch "${batch_file}"

monitor_container_name="server-monitor-agent"
if [[ -n "${LOCAL_ENV_FILE}" ]]; then
  monitor_container_name="$(read_env_value "${LOCAL_ENV_FILE}" "MONITOR_CONTAINER_NAME" || true)"
  monitor_container_name="${monitor_container_name:-server-monitor-agent}"
fi

containers_to_remove=()
if [[ "${PROFILE}" == "full" ]]; then
  containers_to_remove=(
    "server-monitor-db"
    "server-monitor-backend"
    "server-monitor-frontend"
    "server-monitor-telegram-bot"
    "${monitor_container_name}"
  )
else
  containers_to_remove=("${monitor_container_name}")
fi

for container_name in "${containers_to_remove[@]}"; do
  log "Removing conflicting container if present: ${container_name}"
  run_ssh "${docker_prefix} rm -f \"${container_name}\" >/dev/null 2>&1 || true"
done

log "Restarting Docker services on remote host..."
if [[ "${PROFILE}" == "full" ]]; then
  run_ssh "cd \"${DEPLOY_PATH}\" && ${docker_prefix} compose up -d --build"
else
  run_ssh "cd \"${DEPLOY_PATH}\" && ${docker_prefix} compose -f docker-compose.monitor.yml up -d --build monitor"
fi

log "Deploy completed successfully."
