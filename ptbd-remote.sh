#!/usr/bin/env bash
set -euo pipefail

PTBD_REMOTE_HOST="${PTBD_REMOTE_HOST:-}"
PTBD_REMOTE_PORT="${PTBD_REMOTE_PORT:-22}"
PTBD_REMOTE_PASSWORD="${PTBD_REMOTE_PASSWORD:-}"
PTBD_REMOTE_PT_CMD="${PTBD_REMOTE_PT_CMD:-pt}"
PTBD_REMOTE_BOOTSTRAP="${PTBD_REMOTE_BOOTSTRAP:-1}"
PTBD_REMOTE_RETURN_PORT="${PTBD_REMOTE_RETURN_PORT:-18080}"
PTBD_LOCAL_HTTP_PORT="${PTBD_LOCAL_HTTP_PORT:-18080}"
PTBD_LOCAL_SAVE_DIR="${PTBD_LOCAL_SAVE_DIR:-}"
PTBD_SCAN_INCLUDE_ROOTS="${PTBD_SCAN_INCLUDE_ROOTS:-}"
PTBD_SCAN_INCLUDE_ROOTS_JSON="${PTBD_SCAN_INCLUDE_ROOTS_JSON:-}"
PTBD_SCAN_INCLUDE_ROOTS_LINES="${PTBD_SCAN_INCLUDE_ROOTS_LINES:-}"
PTBD_SCAN_EXCLUDE_ROOTS="${PTBD_SCAN_EXCLUDE_ROOTS:-}"
PTBD_SCAN_EXCLUDE_ROOTS_JSON="${PTBD_SCAN_EXCLUDE_ROOTS_JSON:-}"
PTBD_SCAN_EXCLUDE_ROOTS_LINES="${PTBD_SCAN_EXCLUDE_ROOTS_LINES:-}"
PTBD_AUDIO_SPECTRUM_MODE="${PTBD_AUDIO_SPECTRUM_MODE:-single}"
PTBD_AUDIO_SPECTRUM_BACKEND="${PTBD_AUDIO_SPECTRUM_BACKEND:-auto}"
PTBD_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS="${PTBD_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS:-12}"
PTBD_AUTO_CLEANUP="${PTBD_AUTO_CLEANUP:-1}"
PTBD_KEEP_BRIDGE="${PTBD_KEEP_BRIDGE:-0}"
PTBD_REMOTE_TARGET_PATH="${PTBD_REMOTE_TARGET_PATH:-}"
PTBD_REMOTE_CONFIG_FILE="${PTBD_REMOTE_CONFIG_FILE:-$HOME/.config/ptbd-remote/config.env}"

resolve_script_path() {
  local src="$1"
  if command -v readlink >/dev/null 2>&1; then
    local resolved=""
    resolved="$(readlink -f "$src" 2>/dev/null || true)"
    if [[ -n "$resolved" ]]; then
      echo "$resolved"
      return 0
    fi
  fi
  local dir=""
  while [[ -L "$src" ]]; do
    dir="$(cd -P "$(dirname "$src")" && pwd)"
    src="$(readlink "$src")"
    [[ "$src" != /* ]] && src="$dir/$src"
  done
  echo "$src"
}

SCRIPT_PATH="$(resolve_script_path "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"
UPLOAD_SERVER_SCRIPT="${SCRIPT_DIR}/scripts/remote-upload-server.py"
REMOTE_PREPARE_SCRIPT="${SCRIPT_DIR}/scripts/prepare-remote-runtime.sh"

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
  --remote-cmd CMD          Remote command to launch when bootstrap is off (default: pt)
  --bootstrap 0|1           Auto upload runtime to blank VPS first (default: 1)
  --save-dir DIR            Local receive directory (default: Desktop)
  --local-port N            Local HTTP server port (default: 18080)
  --remote-return-port N    Remote reverse tunnel port (default: 18080)
  --scan-include "DIRS"     Remote whitelist roots, separated by spaces or commas
  --scan-exclude "DIRS"     Remote extra exclude roots, separated by spaces or commas
  --audio-spectrum MODE     Audio spectrum mode: single or combined (default: single)
  --audio-spectrum-backend  Spectrum backend: auto, sox, sox_ng or ffmpeg (default: auto)
  --audio-spectrum-seconds  Combined mode sample seconds per track; 0 means full tracks (default: 12)
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
  PTBD_REMOTE_BOOTSTRAP
  PTBD_LOCAL_SAVE_DIR
  PTBD_SCAN_INCLUDE_ROOTS
  PTBD_SCAN_INCLUDE_ROOTS_JSON
  PTBD_SCAN_INCLUDE_ROOTS_LINES
  PTBD_SCAN_EXCLUDE_ROOTS
  PTBD_SCAN_EXCLUDE_ROOTS_JSON
  PTBD_SCAN_EXCLUDE_ROOTS_LINES
  PTBD_AUDIO_SPECTRUM_MODE
  PTBD_AUDIO_SPECTRUM_BACKEND
  PTBD_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS
  PTBD_REMOTE_TARGET_PATH
  PTBD_REMOTE_CONFIG_FILE
EOF
}

quote_sh() {
  printf "'%s'" "$(printf '%s' "${1:-}" | sed "s/'/'\\\\''/g")"
}

normalize_bool() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) printf '1' ;;
    0|false|FALSE|no|NO|off|OFF) printf '0' ;;
    *) return 1 ;;
  esac
}

derive_scan_roots() {
  local raw="${1:-}"
  local input_format="${2:-legacy}"
  local output_format="${3:-json}"
  PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" python3 - "$raw" "$input_format" "$output_format" <<'PY'
import json
import sys

from ptbd_core.config import (
    normalize_scan_roots,
    parse_path_roots_json,
    parse_path_roots_lines,
    split_path_roots,
)

raw, input_format, output_format = sys.argv[1:]
try:
    if input_format == "json":
        roots = parse_path_roots_json(raw)
    elif input_format == "lines":
        roots = parse_path_roots_lines(raw)
    else:
        roots = split_path_roots(raw)
except ValueError as exc:
    print(f"invalid scan roots {input_format}: {exc}", file=sys.stderr)
    raise SystemExit(2) from exc

if output_format == "lines":
    print("\n".join(roots), end="")
elif output_format == "legacy":
    print(normalize_scan_roots(roots), end="")
else:
    print(json.dumps(roots, ensure_ascii=False), end="")
PY
}

prepare_scan_root_metadata() {
  local raw=""
  local input_format=""
  local normalized_json=""
  local normalized_lines=""
  local normalized_legacy=""

  if [[ -n "$PTBD_SCAN_INCLUDE_ROOTS_JSON" ]]; then
    raw="$PTBD_SCAN_INCLUDE_ROOTS_JSON"
    input_format="json"
  elif [[ -n "$PTBD_SCAN_INCLUDE_ROOTS_LINES" ]]; then
    raw="$PTBD_SCAN_INCLUDE_ROOTS_LINES"
    input_format="lines"
  elif [[ -n "$PTBD_SCAN_INCLUDE_ROOTS" ]]; then
    raw="$PTBD_SCAN_INCLUDE_ROOTS"
    input_format="legacy"
  fi
  if [[ -n "$input_format" ]]; then
    normalized_json="$(derive_scan_roots "$raw" "$input_format" json)" || return 2
    normalized_lines="$(derive_scan_roots "$raw" "$input_format" lines)" || return 2
    normalized_legacy="$(derive_scan_roots "$raw" "$input_format" legacy)" || return 2
    if [[ "$normalized_json" == "[]" ]]; then
      err "scan include whitelist is configured but contains no valid roots"
      return 2
    fi
    PTBD_SCAN_INCLUDE_ROOTS_JSON="$normalized_json"
    PTBD_SCAN_INCLUDE_ROOTS_LINES="$normalized_lines"
    PTBD_SCAN_INCLUDE_ROOTS="$normalized_legacy"
  fi

  raw=""
  input_format=""
  if [[ -n "$PTBD_SCAN_EXCLUDE_ROOTS_JSON" ]]; then
    raw="$PTBD_SCAN_EXCLUDE_ROOTS_JSON"
    input_format="json"
  elif [[ -n "$PTBD_SCAN_EXCLUDE_ROOTS_LINES" ]]; then
    raw="$PTBD_SCAN_EXCLUDE_ROOTS_LINES"
    input_format="lines"
  elif [[ -n "$PTBD_SCAN_EXCLUDE_ROOTS" ]]; then
    raw="$PTBD_SCAN_EXCLUDE_ROOTS"
    input_format="legacy"
  fi
  if [[ -n "$input_format" ]]; then
    normalized_json="$(derive_scan_roots "$raw" "$input_format" json)" || return 2
    if [[ "$normalized_json" == "[]" ]]; then
      PTBD_SCAN_EXCLUDE_ROOTS=""
      PTBD_SCAN_EXCLUDE_ROOTS_JSON=""
      PTBD_SCAN_EXCLUDE_ROOTS_LINES=""
      return 0
    fi
    normalized_lines="$(derive_scan_roots "$raw" "$input_format" lines)" || return 2
    normalized_legacy="$(derive_scan_roots "$raw" "$input_format" legacy)" || return 2
    PTBD_SCAN_EXCLUDE_ROOTS_JSON="$normalized_json"
    PTBD_SCAN_EXCLUDE_ROOTS_LINES="$normalized_lines"
    PTBD_SCAN_EXCLUDE_ROOTS="$normalized_legacy"
  fi
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
PTBD_REMOTE_BOOTSTRAP=$(quote_sh "$PTBD_REMOTE_BOOTSTRAP")
PTBD_LOCAL_SAVE_DIR=$(quote_sh "$PTBD_LOCAL_SAVE_DIR")
PTBD_SCAN_INCLUDE_ROOTS=$(quote_sh "$PTBD_SCAN_INCLUDE_ROOTS")
PTBD_SCAN_EXCLUDE_ROOTS=$(quote_sh "$PTBD_SCAN_EXCLUDE_ROOTS")
PTBD_AUDIO_SPECTRUM_MODE=$(quote_sh "$PTBD_AUDIO_SPECTRUM_MODE")
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
  echo "说明：会先检测 Debian / Ubuntu / Alpine 依赖并尽量自动安装；只有系统依赖不够时，才回退上传内置运行包。"

  PTBD_REMOTE_HOST="$(prompt_value "VPS 地址 (如 root@1.2.3.4) [${PTBD_REMOTE_HOST:-root@your-vps}]: " "${PTBD_REMOTE_HOST:-root@your-vps}")"
  PTBD_REMOTE_PORT="$(prompt_value "SSH 端口 [${PTBD_REMOTE_PORT:-22}]: " "${PTBD_REMOTE_PORT:-22}")"
  local auth_mode=""
  auth_mode="$(prompt_value "认证方式，输入 key 或 password [key]: " "key")"
  if [[ "$auth_mode" == "password" ]]; then
    PTBD_REMOTE_PASSWORD="$(prompt_value "SSH 密码: " "$PTBD_REMOTE_PASSWORD" "1")"
  else
    PTBD_REMOTE_PASSWORD=""
  fi
  PTBD_REMOTE_BOOTSTRAP="$(prompt_value "空白 VPS 自动自举？1=是 0=否 [${PTBD_REMOTE_BOOTSTRAP:-1}]: " "${PTBD_REMOTE_BOOTSTRAP:-1}")"
  PTBD_SCAN_INCLUDE_ROOTS="$(prompt_value "默认扫描目录白名单（留空=智能扫描常见目录） [${PTBD_SCAN_INCLUDE_ROOTS:-}]: " "${PTBD_SCAN_INCLUDE_ROOTS:-}")"
  PTBD_SCAN_EXCLUDE_ROOTS="$(prompt_value "额外排除目录（可留空） [${PTBD_SCAN_EXCLUDE_ROOTS:-}]: " "${PTBD_SCAN_EXCLUDE_ROOTS:-}")"
  PTBD_AUDIO_SPECTRUM_MODE="$(prompt_value "音乐频谱模式 single=单曲图 combined=整包总图 [${PTBD_AUDIO_SPECTRUM_MODE:-single}]: " "${PTBD_AUDIO_SPECTRUM_MODE:-single}")"
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
  auto bootstrap:   ${PTBD_REMOTE_BOOTSTRAP:-1}
  local save dir:   ${save_dir}
  scan include:     ${PTBD_SCAN_INCLUDE_ROOTS:-<unset>}
  scan exclude:     ${PTBD_SCAN_EXCLUDE_ROOTS:-<unset>}
  audio spectrum:   ${PTBD_AUDIO_SPECTRUM_MODE:-single}
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
    --bootstrap) PTBD_REMOTE_BOOTSTRAP="${2:-}"; shift 2 ;;
    --save-dir) PTBD_LOCAL_SAVE_DIR="${2:-}"; shift 2 ;;
    --local-port) PTBD_LOCAL_HTTP_PORT="${2:-}"; shift 2 ;;
    --remote-return-port) PTBD_REMOTE_RETURN_PORT="${2:-}"; shift 2 ;;
    --scan-include)
      PTBD_SCAN_INCLUDE_ROOTS="${2:-}"
      PTBD_SCAN_INCLUDE_ROOTS_JSON=""
      PTBD_SCAN_INCLUDE_ROOTS_LINES=""
      shift 2
      ;;
    --scan-exclude)
      PTBD_SCAN_EXCLUDE_ROOTS="${2:-}"
      PTBD_SCAN_EXCLUDE_ROOTS_JSON=""
      PTBD_SCAN_EXCLUDE_ROOTS_LINES=""
      shift 2
      ;;
    --audio-spectrum) PTBD_AUDIO_SPECTRUM_MODE="${2:-}"; shift 2 ;;
    --audio-spectrum-backend) PTBD_AUDIO_SPECTRUM_BACKEND="${2:-}"; shift 2 ;;
    --audio-spectrum-seconds) PTBD_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS="${2:-}"; shift 2 ;;
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

command -v python3 >/dev/null 2>&1 || { err "missing python3"; exit 1; }
prepare_scan_root_metadata

[[ -n "$PTBD_REMOTE_HOST" ]] || { err "missing --host"; usage; exit 2; }
[[ -f "$UPLOAD_SERVER_SCRIPT" ]] || { err "missing upload server script: $UPLOAD_SERVER_SCRIPT"; exit 1; }
command -v ssh >/dev/null 2>&1 || { err "missing ssh"; exit 1; }
PTBD_REMOTE_BOOTSTRAP="$(normalize_bool "$PTBD_REMOTE_BOOTSTRAP" 2>/dev/null || true)"
[[ -n "$PTBD_REMOTE_BOOTSTRAP" ]] || { err "invalid --bootstrap value, expected 0 or 1"; exit 2; }
PTBD_AUDIO_SPECTRUM_MODE="${PTBD_AUDIO_SPECTRUM_MODE:-single}"
PTBD_AUDIO_SPECTRUM_MODE="${PTBD_AUDIO_SPECTRUM_MODE,,}"
case "$PTBD_AUDIO_SPECTRUM_MODE" in
  single|combined) ;;
  *) err "invalid --audio-spectrum value, expected single or combined"; exit 2 ;;
esac
PTBD_AUDIO_SPECTRUM_BACKEND="${PTBD_AUDIO_SPECTRUM_BACKEND:-auto}"
PTBD_AUDIO_SPECTRUM_BACKEND="${PTBD_AUDIO_SPECTRUM_BACKEND,,}"
case "$PTBD_AUDIO_SPECTRUM_BACKEND" in
  auto|sox|sox_ng|ffmpeg) ;;
  *) err "invalid --audio-spectrum-backend value, expected auto, sox, sox_ng or ffmpeg"; exit 2 ;;
esac
[[ "$PTBD_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS" =~ ^[0-9]+$ ]] || PTBD_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS=12
if [[ "$PTBD_REMOTE_BOOTSTRAP" == "1" && ! -f "$REMOTE_PREPARE_SCRIPT" ]]; then
  err "missing remote bootstrap helper: $REMOTE_PREPARE_SCRIPT"
  exit 1
fi

EFFECTIVE_REMOTE_CMD="$PTBD_REMOTE_PT_CMD"
if [[ "$PTBD_REMOTE_BOOTSTRAP" == "1" ]]; then
  log "bootstrapping remote runtime for blank VPS"
  log "bootstrap now prefers remote auto-install on Debian/Ubuntu/Alpine; only fallback bundle uploads may reach about 300MB"
  PREPARE_CMD=("$REMOTE_PREPARE_SCRIPT" --host "$PTBD_REMOTE_HOST" --port "$PTBD_REMOTE_PORT")
  EFFECTIVE_REMOTE_CMD="$("${PREPARE_CMD[@]}")"
  [[ -n "$EFFECTIVE_REMOTE_CMD" ]] || { err "remote bootstrap returned empty command"; exit 1; }
  log "remote runtime ready: $EFFECTIVE_REMOTE_CMD"
fi

LOCAL_SAVE_DIR="$(resolve_save_dir)"
show_config
log "local receive dir: $LOCAL_SAVE_DIR"

PTBD_SAVE_DIR="$LOCAL_SAVE_DIR" nohup python3 "$UPLOAD_SERVER_SCRIPT" "$PTBD_LOCAL_HTTP_PORT" >/tmp/ptbd_remote_upload_server.log 2>&1 &
UPLOAD_SERVER_PID="$!"
sleep 1
kill -0 "$UPLOAD_SERVER_PID" 2>/dev/null || { err "failed to start local upload server"; exit 1; }
log "local receive server started on 127.0.0.1:${PTBD_LOCAL_HTTP_PORT}"

SSH_CMD=(ssh -tt -p "$PTBD_REMOTE_PORT" -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes)
setup_ssh_auth

nohup "${SSH_AUTH_PREFIX[@]}" ssh -N -p "$PTBD_REMOTE_PORT" -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes -R "${PTBD_REMOTE_RETURN_PORT}:127.0.0.1:${PTBD_LOCAL_HTTP_PORT}" "$PTBD_REMOTE_HOST" >/tmp/ptbd_remote_tunnel.log 2>&1 &
TUNNEL_PID="$!"
sleep 3
kill -0 "$TUNNEL_PID" 2>/dev/null || { err "failed to create reverse SSH tunnel"; exit 1; }
log "reverse tunnel ready: remote 127.0.0.1:${PTBD_REMOTE_RETURN_PORT} -> local ${PTBD_LOCAL_HTTP_PORT}"

REMOTE_SCRIPT="export BDTOOL_RETURN_MODE=http; export BDTOOL_RETURN_HTTP_URL=$(quote_sh "http://127.0.0.1:${PTBD_REMOTE_RETURN_PORT}/upload"); export BDTOOL_AUTO_CLEANUP=$(quote_sh "$PTBD_AUTO_CLEANUP"); export BDTOOL_AUDIO_SPECTRUM_MODE=$(quote_sh "${PTBD_AUDIO_SPECTRUM_MODE:-single}"); export BDTOOL_AUDIO_SPECTRUM_BACKEND=$(quote_sh "${PTBD_AUDIO_SPECTRUM_BACKEND:-auto}"); export BDTOOL_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS=$(quote_sh "${PTBD_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS:-12}");"
if [[ -n "$PTBD_SCAN_INCLUDE_ROOTS_JSON" ]]; then
  REMOTE_SCRIPT="${REMOTE_SCRIPT} export BDTOOL_SCAN_INCLUDE_ROOTS=$(quote_sh "$PTBD_SCAN_INCLUDE_ROOTS");"
  REMOTE_SCRIPT="${REMOTE_SCRIPT} export BDTOOL_SCAN_INCLUDE_ROOTS_JSON=$(quote_sh "$PTBD_SCAN_INCLUDE_ROOTS_JSON");"
  REMOTE_SCRIPT="${REMOTE_SCRIPT} export BDTOOL_SCAN_INCLUDE_ROOTS_LINES=$(quote_sh "$PTBD_SCAN_INCLUDE_ROOTS_LINES");"
fi
if [[ -n "$PTBD_SCAN_EXCLUDE_ROOTS_JSON" ]]; then
  REMOTE_SCRIPT="${REMOTE_SCRIPT} export BDTOOL_SCAN_EXCLUDE_ROOTS=$(quote_sh "$PTBD_SCAN_EXCLUDE_ROOTS");"
  REMOTE_SCRIPT="${REMOTE_SCRIPT} export BDTOOL_SCAN_EXCLUDE_ROOTS_JSON=$(quote_sh "$PTBD_SCAN_EXCLUDE_ROOTS_JSON");"
  REMOTE_SCRIPT="${REMOTE_SCRIPT} export BDTOOL_SCAN_EXCLUDE_ROOTS_LINES=$(quote_sh "$PTBD_SCAN_EXCLUDE_ROOTS_LINES");"
fi
if [[ -n "$PTBD_REMOTE_TARGET_PATH" ]]; then
  REMOTE_SCRIPT="${REMOTE_SCRIPT} exec $(quote_sh "$EFFECTIVE_REMOTE_CMD") generate-path --path $(quote_sh "$PTBD_REMOTE_TARGET_PATH") --lang zh --audio-spectrum $(quote_sh "${PTBD_AUDIO_SPECTRUM_MODE:-single}")"
  log "processing remote path directly: $PTBD_REMOTE_TARGET_PATH"
else
  REMOTE_SCRIPT="${REMOTE_SCRIPT} exec $(quote_sh "$EFFECTIVE_REMOTE_CMD")"
  log "opening remote menu; select an item and the rest runs automatically"
fi

"${SSH_AUTH_PREFIX[@]}" "${SSH_CMD[@]}" "$PTBD_REMOTE_HOST" "bash -lc $(quote_sh "$REMOTE_SCRIPT")"

log "done; returned files should now be in: $LOCAL_SAVE_DIR"
