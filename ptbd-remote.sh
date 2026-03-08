#!/usr/bin/env bash
set -euo pipefail

PTBD_REMOTE_HOST="${PTBD_REMOTE_HOST:-}"
PTBD_REMOTE_PORT="${PTBD_REMOTE_PORT:-22}"
PTBD_REMOTE_PASSWORD="${PTBD_REMOTE_PASSWORD:-}"
PTBD_REMOTE_PT_CMD="${PTBD_REMOTE_PT_CMD:-pt}"
PTBD_REMOTE_RETURN_PORT="${PTBD_REMOTE_RETURN_PORT:-18080}"
PTBD_LOCAL_HTTP_PORT="${PTBD_LOCAL_HTTP_PORT:-18080}"
PTBD_LOCAL_SAVE_DIR="${PTBD_LOCAL_SAVE_DIR:-}"
PTBD_SCAN_INCLUDE_ROOTS="${PTBD_SCAN_INCLUDE_ROOTS:-}"
PTBD_SCAN_EXCLUDE_ROOTS="${PTBD_SCAN_EXCLUDE_ROOTS:-}"
PTBD_AUTO_CLEANUP="${PTBD_AUTO_CLEANUP:-1}"
PTBD_KEEP_BRIDGE="${PTBD_KEEP_BRIDGE:-0}"
PTBD_REMOTE_TARGET_PATH="${PTBD_REMOTE_TARGET_PATH:-}"
PTBD_REMOTE_CONFIG_FILE="${PTBD_REMOTE_CONFIG_FILE:-$HOME/.config/ptbd-remote/config.env}"

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"
UPLOAD_SERVER_SCRIPT="${SCRIPT_DIR}/scripts/remote-upload-server.py"

UPLOAD_SERVER_PID=""
TUNNEL_PID=""
SETUP_MODE=0
ASKPASS_SCRIPT=""
SSH_AUTH_PREFIX=()

log() { printf '[ptbd-remote] %s\n' "$*"; }
err() { printf '[ptbd-remote][ERROR] %s\n' "$*" >&2; }

setup_ssh_auth() {
  if [[ -z "$PTBD_REMOTE_PASSWORD" ]]; then
    SSH_AUTH_PREFIX=()
    return 0
  fi

  ASKPASS_SCRIPT="$(mktemp)"
  cat > "$ASKPASS_SCRIPT" <<EOF
#!/usr/bin/env bash
printf '%s\n' $(quote_sh "$PTBD_REMOTE_PASSWORD")
EOF
  chmod 700 "$ASKPASS_SCRIPT"
  SSH_AUTH_PREFIX=(env "SSH_ASKPASS=$ASKPASS_SCRIPT" "SSH_ASKPASS_REQUIRE=force" "DISPLAY=${DISPLAY:-ptbd-askpass:0}")
}

usage() {
  cat <<'EOF'
Usage:
  ptbd-remote [options]
  ptbd-remote --setup

What it does:
  1. Start a local receive server on this machine
  2. Create a reverse SSH tunnel to the VPS
  3. Open remote PT-BDtool menu
  4. After you select an item, remote generation / return / cleanup run automatically
  5. Or process a specific remote path directly when --path is provided

Options:
  --host user@server        Remote SSH target
  --port N                  Remote SSH port (default: 22)
  --password TEXT           SSH password; if omitted, use SSH keys
  --remote-cmd CMD          Remote command to launch (default: pt)
  --save-dir DIR            Local receive directory (default: Desktop)
  --local-port N            Local HTTP server port (default: 18080)
  --remote-return-port N    Remote reverse tunnel port (default: 18080)
  --scan-include "DIRS"     Remote whitelist roots, separated by spaces or commas
  --scan-exclude "DIRS"     Remote extra exclude roots, separated by spaces or commas
  --path TARGET             Process this remote candidate directly, no menu interaction
  --config FILE             Config file path
  --setup                   Interactive first-run setup
  --show-config             Show the effective config and exit
  --keep-bridge             Keep local server and tunnel after command exits
  -h, --help                Show this help

Environment variables:
  PTBD_REMOTE_HOST
  PTBD_REMOTE_PORT
  PTBD_REMOTE_PASSWORD
  PTBD_REMOTE_PT_CMD
  PTBD_LOCAL_SAVE_DIR
  PTBD_SCAN_INCLUDE_ROOTS
  PTBD_SCAN_EXCLUDE_ROOTS
  PTBD_REMOTE_TARGET_PATH
  PTBD_REMOTE_CONFIG_FILE
EOF
}

quote_sh() {
  printf "'%s'" "$(printf '%s' "${1:-}" | sed "s/'/'\\\\''/g")"
}

resolve_save_dir() {
  if [[ -n "$PTBD_LOCAL_SAVE_DIR" ]]; then
    mkdir -p "$PTBD_LOCAL_SAVE_DIR"
    printf '%s' "$PTBD_LOCAL_SAVE_DIR"
    return 0
  fi
  if [[ -d "$HOME/Desktop" ]]; then
    printf '%s' "$HOME/Desktop"
    return 0
  fi
  if [[ -d "$HOME/桌面" ]]; then
    printf '%s' "$HOME/桌面"
    return 0
  fi
  mkdir -p "$HOME/Desktop"
  printf '%s' "$HOME/Desktop"
}

load_config_file() {
  local file="${1:-}"
  [[ -n "$file" && -f "$file" ]] || return 0
  # shellcheck disable=SC1090
  source "$file"
}

write_config_file() {
  local file="$1"
  mkdir -p "$(dirname "$file")"
  cat > "$file" <<EOF
PTBD_REMOTE_HOST=$(quote_sh "$PTBD_REMOTE_HOST")
PTBD_REMOTE_PORT=$(quote_sh "$PTBD_REMOTE_PORT")
PTBD_REMOTE_PASSWORD=$(quote_sh "$PTBD_REMOTE_PASSWORD")
PTBD_REMOTE_PT_CMD=$(quote_sh "$PTBD_REMOTE_PT_CMD")
PTBD_LOCAL_SAVE_DIR=$(quote_sh "$PTBD_LOCAL_SAVE_DIR")
PTBD_SCAN_INCLUDE_ROOTS=$(quote_sh "$PTBD_SCAN_INCLUDE_ROOTS")
PTBD_SCAN_EXCLUDE_ROOTS=$(quote_sh "$PTBD_SCAN_EXCLUDE_ROOTS")
PTBD_AUTO_CLEANUP=$(quote_sh "$PTBD_AUTO_CLEANUP")
EOF
  chmod 600 "$file"
}

prompt_value() {
  local prompt="$1"
  local default_value="${2:-}"
  local secret="${3:-0}"
  local value=""
  if [[ "$secret" == "1" ]]; then
    read -r -s -p "$prompt" value < /dev/tty || true
    printf '\n' > /dev/tty
  else
    read -r -p "$prompt" value < /dev/tty || true
  fi
  printf '%s' "${value:-$default_value}"
}

run_setup() {
  local current_save_dir=""
  current_save_dir="$(resolve_save_dir)"
  [[ -n "$PTBD_LOCAL_SAVE_DIR" ]] || PTBD_LOCAL_SAVE_DIR="$current_save_dir"

  echo "PT-BDtool 远端一步到位配置向导"
  echo "配置文件：$PTBD_REMOTE_CONFIG_FILE"

  PTBD_REMOTE_HOST="$(prompt_value "VPS 地址 (如 root@1.2.3.4) [${PTBD_REMOTE_HOST:-root@your-vps}]: " "${PTBD_REMOTE_HOST:-root@your-vps}")"
  PTBD_REMOTE_PORT="$(prompt_value "SSH 端口 [${PTBD_REMOTE_PORT:-22}]: " "${PTBD_REMOTE_PORT:-22}")"
  local auth_mode=""
  auth_mode="$(prompt_value "认证方式，输入 key 或 password [key]: " "key")"
  if [[ "$auth_mode" == "password" ]]; then
    PTBD_REMOTE_PASSWORD="$(prompt_value "SSH 密码: " "$PTBD_REMOTE_PASSWORD" "1")"
  else
    PTBD_REMOTE_PASSWORD=""
  fi
  PTBD_SCAN_INCLUDE_ROOTS="$(prompt_value "默认扫描目录白名单 [${PTBD_SCAN_INCLUDE_ROOTS:-/home/admin/Downloads}]: " "${PTBD_SCAN_INCLUDE_ROOTS:-/home/admin/Downloads}")"
  PTBD_SCAN_EXCLUDE_ROOTS="$(prompt_value "额外排除目录（可留空） [${PTBD_SCAN_EXCLUDE_ROOTS:-}]: " "${PTBD_SCAN_EXCLUDE_ROOTS:-}")"
  PTBD_LOCAL_SAVE_DIR="$(prompt_value "本机保存目录 [${PTBD_LOCAL_SAVE_DIR:-$current_save_dir}]: " "${PTBD_LOCAL_SAVE_DIR:-$current_save_dir}")"
  PTBD_AUTO_CLEANUP="$(prompt_value "处理完成后自动清理 VPS 生成目录？1=是 0=否 [${PTBD_AUTO_CLEANUP:-1}]: " "${PTBD_AUTO_CLEANUP:-1}")"

  write_config_file "$PTBD_REMOTE_CONFIG_FILE"
  echo "已写入：$PTBD_REMOTE_CONFIG_FILE"
  echo "下次直接运行：ptbd-remote"
}

show_config() {
  local masked_password="(empty)"
  [[ -n "$PTBD_REMOTE_PASSWORD" ]] && masked_password="******"
  local save_dir=""
  save_dir="$(resolve_save_dir)"
  cat <<EOF
Current config
  config file:      $PTBD_REMOTE_CONFIG_FILE
  remote host:      ${PTBD_REMOTE_HOST:-<unset>}
  remote port:      ${PTBD_REMOTE_PORT:-22}
  auth mode:        $( [[ -n "$PTBD_REMOTE_PASSWORD" ]] && echo password || echo key )
  remote command:   ${PTBD_REMOTE_PT_CMD:-pt}
  local save dir:   ${save_dir}
  scan include:     ${PTBD_SCAN_INCLUDE_ROOTS:-<unset>}
  scan exclude:     ${PTBD_SCAN_EXCLUDE_ROOTS:-<unset>}
  auto cleanup:     ${PTBD_AUTO_CLEANUP:-1}
  password:         ${masked_password}
EOF
}

cleanup() {
  local rc=$?
  if [[ "$PTBD_KEEP_BRIDGE" != "1" ]]; then
    if [[ -n "$TUNNEL_PID" ]]; then
      kill "$TUNNEL_PID" 2>/dev/null || true
    fi
    if [[ -n "$UPLOAD_SERVER_PID" ]]; then
      kill "$UPLOAD_SERVER_PID" 2>/dev/null || true
    fi
  fi
  if [[ -n "$ASKPASS_SCRIPT" ]]; then
    rm -f "$ASKPASS_SCRIPT"
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

load_config_file "$PTBD_REMOTE_CONFIG_FILE"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) PTBD_REMOTE_HOST="${2:-}"; shift 2 ;;
    --port) PTBD_REMOTE_PORT="${2:-}"; shift 2 ;;
    --password) PTBD_REMOTE_PASSWORD="${2:-}"; shift 2 ;;
    --remote-cmd) PTBD_REMOTE_PT_CMD="${2:-}"; shift 2 ;;
    --save-dir) PTBD_LOCAL_SAVE_DIR="${2:-}"; shift 2 ;;
    --local-port) PTBD_LOCAL_HTTP_PORT="${2:-}"; shift 2 ;;
    --remote-return-port) PTBD_REMOTE_RETURN_PORT="${2:-}"; shift 2 ;;
    --scan-include) PTBD_SCAN_INCLUDE_ROOTS="${2:-}"; shift 2 ;;
    --scan-exclude) PTBD_SCAN_EXCLUDE_ROOTS="${2:-}"; shift 2 ;;
    --path) PTBD_REMOTE_TARGET_PATH="${2:-}"; shift 2 ;;
    --config) PTBD_REMOTE_CONFIG_FILE="${2:-}"; shift 2; load_config_file "$PTBD_REMOTE_CONFIG_FILE" ;;
    --setup) SETUP_MODE=1; shift ;;
    --show-config) show_config; exit 0 ;;
    --keep-bridge) PTBD_KEEP_BRIDGE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) err "unknown argument: $1"; usage; exit 2 ;;
  esac
done

if [[ "$SETUP_MODE" == "1" ]]; then
  run_setup
  exit 0
fi

[[ -n "$PTBD_REMOTE_HOST" ]] || { err "missing --host"; usage; exit 2; }
[[ -f "$UPLOAD_SERVER_SCRIPT" ]] || { err "missing upload server script: $UPLOAD_SERVER_SCRIPT"; exit 1; }
command -v ssh >/dev/null 2>&1 || { err "missing ssh"; exit 1; }
command -v python3 >/dev/null 2>&1 || { err "missing python3"; exit 1; }

LOCAL_SAVE_DIR="$(resolve_save_dir)"
show_config
log "local receive dir: $LOCAL_SAVE_DIR"

PTBD_SAVE_DIR="$LOCAL_SAVE_DIR" nohup python3 "$UPLOAD_SERVER_SCRIPT" "$PTBD_LOCAL_HTTP_PORT" >/tmp/ptbd_remote_upload_server.log 2>&1 &
UPLOAD_SERVER_PID="$!"
sleep 1
kill -0 "$UPLOAD_SERVER_PID" 2>/dev/null || { err "failed to start local upload server"; exit 1; }
log "local receive server started on 127.0.0.1:${PTBD_LOCAL_HTTP_PORT}"

SSH_CMD=(ssh -tt -p "$PTBD_REMOTE_PORT" -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=accept-new)
setup_ssh_auth

nohup "${SSH_AUTH_PREFIX[@]}" ssh -N -p "$PTBD_REMOTE_PORT" -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=accept-new -R "${PTBD_REMOTE_RETURN_PORT}:127.0.0.1:${PTBD_LOCAL_HTTP_PORT}" "$PTBD_REMOTE_HOST" >/tmp/ptbd_remote_tunnel.log 2>&1 &
TUNNEL_PID="$!"
sleep 3
kill -0 "$TUNNEL_PID" 2>/dev/null || { err "failed to create reverse SSH tunnel"; exit 1; }
log "reverse tunnel ready: remote 127.0.0.1:${PTBD_REMOTE_RETURN_PORT} -> local ${PTBD_LOCAL_HTTP_PORT}"

REMOTE_SCRIPT="export BDTOOL_RETURN_MODE=http; export BDTOOL_RETURN_HTTP_URL=$(quote_sh "http://127.0.0.1:${PTBD_REMOTE_RETURN_PORT}/upload"); export BDTOOL_AUTO_CLEANUP=$(quote_sh "$PTBD_AUTO_CLEANUP");"
if [[ -n "$PTBD_SCAN_INCLUDE_ROOTS" ]]; then
  REMOTE_SCRIPT="${REMOTE_SCRIPT} export BDTOOL_SCAN_INCLUDE_ROOTS=$(quote_sh "$PTBD_SCAN_INCLUDE_ROOTS");"
fi
if [[ -n "$PTBD_SCAN_EXCLUDE_ROOTS" ]]; then
  REMOTE_SCRIPT="${REMOTE_SCRIPT} export BDTOOL_SCAN_EXCLUDE_ROOTS=$(quote_sh "$PTBD_SCAN_EXCLUDE_ROOTS");"
fi
if [[ -n "$PTBD_REMOTE_TARGET_PATH" ]]; then
  REMOTE_SCRIPT="${REMOTE_SCRIPT} exec $(quote_sh "$PTBD_REMOTE_PT_CMD") generate-path --path $(quote_sh "$PTBD_REMOTE_TARGET_PATH") --lang zh"
  log "processing remote path directly: $PTBD_REMOTE_TARGET_PATH"
else
  REMOTE_SCRIPT="${REMOTE_SCRIPT} exec $(quote_sh "$PTBD_REMOTE_PT_CMD")"
  log "opening remote menu; select an item and the rest runs automatically"
fi

"${SSH_AUTH_PREFIX[@]}" "${SSH_CMD[@]}" "$PTBD_REMOTE_HOST" "bash -lc $(quote_sh "$REMOTE_SCRIPT")"

log "done; returned files should now be in: $LOCAL_SAVE_DIR"
