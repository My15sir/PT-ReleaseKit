#!/usr/bin/env bash

: "${BDTOOL_CMD_TIMEOUT:=300}"

if [[ -t 2 ]]; then
  C_RED='\033[31m'
  C_CYAN='\033[36m'
  C_RESET='\033[0m'
  if command -v tput >/dev/null 2>&1; then
    COLORS="$(tput colors 2>/dev/null || echo 0)"
  else
    COLORS=0
  fi
  if [[ "$COLORS" =~ ^[0-9]+$ ]] && (( COLORS >= 256 )); then
    C_MENU='\033[38;5;223m'
  else
    C_MENU='\033[33m'
  fi
else
  C_RED=''
  C_CYAN=''
  C_MENU=''
  C_RESET=''
fi

screen() {
  printf "%s\n" "$*"
}

log_info() {
  printf "[INFO] %s\n" "$*" >&2
}

log_warn() {
  printf "[WARN] %s\n" "$*" >&2
}

log_err() {
  printf "[ERROR] %s\n" "$*" >&2
}

log_success() {
  printf "[SUCCESS] %s\n" "$*" >&2
}

screen_error() {
  printf "%b%s%b\n" "$C_RED" "$*" "$C_RESET"
}

section() {
  printf "%b==============================================================%b\n" "$C_CYAN" "$C_RESET"
  printf "%s\n" "$*"
  printf "%b==============================================================%b\n" "$C_CYAN" "$C_RESET"
}

menu_option() {
  printf "%b%s%b\n" "$C_MENU" "$*" "$C_RESET"
}

resolve_data_dir() {
  if [[ -n "${BDTOOL_DATA_DIR:-}" ]]; then
    printf "%s" "$BDTOOL_DATA_DIR"
    return 0
  fi

  if [[ -d "/opt/PT-BDtool" ]] && [[ -w "/opt/PT-BDtool" || ${EUID:-$(id -u)} -eq 0 ]]; then
    printf "%s" "/opt/PT-BDtool/bdtool-output"
    return 0
  fi

  if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    printf "%s" "/opt/PT-BDtool/bdtool-output"
    return 0
  fi

  printf "%s" "$HOME/.local/share/pt-bdtool/bdtool-output"
}

resolve_effective_home() {
  local user_home="${HOME:-}"
  local owner=""

  # Honor an explicit HOME first. This keeps non-interactive SSH/test runs
  # isolated while still allowing sudo/root defaults to fall through below.
  if [[ -n "$user_home" && "$user_home" != "/root" ]]; then
    printf "%s" "$user_home"
    return 0
  fi

  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    if command -v getent >/dev/null 2>&1; then
      owner="$(getent passwd "$SUDO_USER" | awk -F: '{print $6}' | head -n1)"
    fi
    [[ -z "$owner" ]] && owner="/home/$SUDO_USER"
    [[ -n "$owner" ]] && { printf "%s" "$owner"; return 0; }
  fi

  if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    local guessed_user=""
    guessed_user="$(logname 2>/dev/null || true)"
    if [[ -n "$guessed_user" && "$guessed_user" != "root" ]]; then
      if command -v getent >/dev/null 2>&1; then
        owner="$(getent passwd "$guessed_user" | awk -F: '{print $6}' | head -n1)"
      fi
      [[ -z "$owner" ]] && owner="/home/$guessed_user"
      [[ -n "$owner" && -d "$owner" ]] && { printf "%s" "$owner"; return 0; }
    fi

    # Fallback to first regular user (UID>=1000) when available.
    if command -v getent >/dev/null 2>&1; then
      owner="$(getent passwd | awk -F: '$3>=1000 && $3<60000 && $1!="nobody"{print $6; exit}')"
      [[ -n "$owner" && -d "$owner" ]] && { printf "%s" "$owner"; return 0; }
    fi
  fi

  if [[ -n "${SUDO_USER:-}" ]]; then
    local sudo_home=""
    if command -v getent >/dev/null 2>&1; then
      sudo_home="$(getent passwd "$SUDO_USER" | awk -F: '{print $6}' | head -n1)"
    fi
    [[ -z "$sudo_home" ]] && sudo_home="/home/$SUDO_USER"
    user_home="$sudo_home"
  fi
  [[ -n "$user_home" ]] || return 1
  printf "%s" "$user_home"
}

resolve_default_download_dir() {
  if [[ -n "${BDTOOL_DOWNLOAD_DIR:-}" ]]; then
    mkdir -p "$BDTOOL_DOWNLOAD_DIR"
    [[ -d "$BDTOOL_DOWNLOAD_DIR" && -w "$BDTOOL_DOWNLOAD_DIR" ]] || return 1
    printf "%s" "$BDTOOL_DOWNLOAD_DIR"
    return 0
  fi

  local user_home=""
  user_home="$(resolve_effective_home)" || return 1

  if [[ -n "${SSH_CONNECTION:-}" ]]; then
    local remote_target="$user_home/PT-BDtool-downloads"
    mkdir -p "$remote_target"
    local remote_probe="$remote_target/.bdtool_write_probe.$$"
    : > "$remote_probe" || return 1
    rm -f "$remote_probe"
    printf "%s" "$remote_target"
    return 0
  fi

  local desktop_xdg=""
  local desktop_en="$user_home/Desktop"
  local desktop_zh="$user_home/桌面"
  if command -v xdg-user-dir >/dev/null 2>&1; then
    desktop_xdg="$(HOME="$user_home" xdg-user-dir DESKTOP 2>/dev/null || true)"
  fi

  local base_dir=""
  if [[ -n "$desktop_xdg" && "$desktop_xdg" != "$user_home" ]]; then
    mkdir -p "$desktop_xdg"
    base_dir="$desktop_xdg"
  elif [[ -d "$desktop_en" ]]; then
    base_dir="$desktop_en"
  elif [[ -d "$desktop_zh" ]]; then
    base_dir="$desktop_zh"
  else
    mkdir -p "$desktop_en"
    base_dir="$desktop_en"
  fi

  local target="$base_dir/PT-BDtool"
  mkdir -p "$target"
  local probe="$target/.bdtool_write_probe.$$"
  : > "$probe" || return 1
  rm -f "$probe"
  printf "%s" "$target"
}

setup_bundle_runtime() {
  local app_root="${1:-}"
  local bundle_dir="${PTBD_BUNDLE_DIR:-}"
  local bundle_bin=""
  local bundle_lib=""
  local wrapper_dir=""
  if [[ -z "$bundle_dir" && -n "$app_root" ]]; then
    bundle_dir="$app_root/third_party/bundle/linux-amd64"
  fi
  if [[ -z "$bundle_dir" ]]; then
    bundle_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/third_party/bundle/linux-amd64"
  fi

  bundle_bin="$bundle_dir/bin"
  bundle_lib="$bundle_dir/lib"

  if [[ -d "$bundle_bin" ]]; then
    if bundle_runtime_healthy "$bundle_bin" "$bundle_lib"; then
      PATH="$bundle_bin:$PATH"
      export PATH
    else
      wrapper_dir="$(ensure_bundle_wrapper_dir "$bundle_dir" "$bundle_bin" "$bundle_lib" || true)"
      if [[ -n "$wrapper_dir" ]] && bundle_runtime_healthy "$wrapper_dir" "$bundle_lib"; then
        PATH="$wrapper_dir:$PATH"
        export PATH
        log_info "bundle runtime check passed via wrapper dir"
      elif system_media_runtime_healthy; then
        log_info "bundle runtime check failed, use system ffmpeg/ffprobe/mediainfo"
      else
        log_warn "bundle runtime check failed, skip bundle/bin PATH injection"
        log_warn "current bundle may require a newer glibc/system runtime on this VPS"
        log_warn "install system deps: apt-get update && apt-get install -y ffmpeg mediainfo"
      fi
    fi
  fi
  # Do not export bundle lib path globally. It can poison system tools
  # (for example mkdir/coreutils) on some VPS images.
  if [[ -d "$bundle_dir" ]]; then
    BDTOOL_BUNDLE_DIR="$bundle_dir"
    export BDTOOL_BUNDLE_DIR
  fi
}

ensure_bundle_wrapper_dir() {
  local bundle_dir="$1"
  local bundle_bin="$2"
  local bundle_lib="$3"
  local wrapper_dir="$bundle_dir/.wrappers"
  local cmd=""
  local target=""
  local escaped_target=""
  local escaped_lib=""
  mkdir -p "$wrapper_dir"

  for cmd in ffmpeg ffprobe mediainfo BDInfo; do
    target="$bundle_bin/$cmd"
    [[ -x "$target" ]] || continue
    escaped_target="$(printf '%q' "$target")"
    escaped_lib="$(printf '%q' "$bundle_lib")"
    cat > "$wrapper_dir/$cmd" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export LD_LIBRARY_PATH=$escaped_lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}
exec $escaped_target "\$@"
EOF
    chmod +x "$wrapper_dir/$cmd"
  done

  printf '%s' "$wrapper_dir"
}

bundle_runtime_healthy() {
  local bundle_bin="$1"
  local bundle_lib="$2"
  local ffprobe_bin="$bundle_bin/ffprobe"
  local mediainfo_bin="$bundle_bin/mediainfo"
  : "${bundle_lib:=}"
  [[ -x "$ffprobe_bin" && -x "$mediainfo_bin" ]] || return 1
  if ! "$ffprobe_bin" -version >/dev/null 2>&1; then
    return 1
  fi
  if ! "$mediainfo_bin" --Version >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

system_media_runtime_healthy() {
  local cmd=""
  for cmd in ffmpeg ffprobe mediainfo; do
    command -v "$cmd" >/dev/null 2>&1 || return 1
  done
  return 0
}

is_client_upload_mode() {
  [[ "$(detect_return_mode)" == "http" ]]
}

is_scp_return_mode() {
  [[ "$(detect_return_mode)" == "scp" ]]
}

detect_return_mode() {
  local mode="${BDTOOL_RETURN_MODE:-}"
  if [[ -z "$mode" ]]; then
    if [[ -n "${BDTOOL_RETURN_SCP_HOST:-}" || -n "${BDTOOL_RETURN_SCP_REMOTE_DIR:-}" || -n "${BDTOOL_RETURN_SCP_USER:-}" ]]; then
      mode="scp"
    elif [[ -n "${BDTOOL_RETURN_HTTP_URL:-}" || -n "${BDTOOL_CLIENT_UPLOAD_URL:-}" ]]; then
      mode="http"
    else
      mode="local"
    fi
  fi
  case "$mode" in
    local|http|scp) ;;
    *) return 1 ;;
  esac
  printf "%s" "$mode"
}

build_client_upload_url() {
  local upload_url="${BDTOOL_RETURN_HTTP_URL:-${BDTOOL_CLIENT_UPLOAD_URL:-}}"
  local filename="${1:-}"
  local encoded_name=""
  [[ -n "$upload_url" && -n "$filename" ]] || return 1
  if [[ "$upload_url" == *"{filename}"* ]]; then
    printf "%s" "${upload_url//\{filename\}/$filename}"
    return 0
  fi
  encoded_name="$(printf "%s" "$filename" | sed -e 's/%/%25/g' -e 's/ /%20/g')"
  if [[ "$upload_url" == *\?* ]]; then
    printf "%s&filename=%s" "$upload_url" "$encoded_name"
  else
    printf "%s?filename=%s" "$upload_url" "$encoded_name"
  fi
}

upload_artifact_to_client() {
  local artifact_file="${1:-}"
  local upload_url=""
  local upload_resp=""
  local curl_bin=""
  local resp_file=""
  local err_file=""
  local curl_rc=0
  local elapsed_s=0
  local curl_err_msg=""
  [[ -f "$artifact_file" ]] || return 1
  is_client_upload_mode || return 1
  curl_bin="$(command -v curl || true)"
  [[ -n "$curl_bin" ]] || {
    log_err "客户端上传失败：缺少 curl"
    return 1
  }
  upload_url="$(build_client_upload_url "$(basename "$artifact_file")")" || return 1
  resp_file="$(mktemp)"
  err_file="$(mktemp)"
  "$curl_bin" --fail --silent --show-error --connect-timeout 10 --max-time 600 --http1.0 \
    -H 'Expect:' \
    -X PUT --data-binary @"$artifact_file" "$upload_url" >"$resp_file" 2>"$err_file" &
  local curl_pid=$!
  local curl_state=""
  while kill -0 "$curl_pid" 2>/dev/null; do
    curl_state="$(ps -o stat= -p "$curl_pid" 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -z "$curl_state" || "$curl_state" == Z* ]]; then
      break
    fi
    sleep 2
    elapsed_s=$((elapsed_s + 2))
    log_info "上传进行中（${elapsed_s}s）"
  done
  wait "$curl_pid" || curl_rc=$?
  upload_resp="$(cat "$resp_file" 2>/dev/null || true)"
  curl_err_msg="$(head -n1 "$err_file" 2>/dev/null || true)"
  rm -f "$resp_file" "$err_file"
  if [[ "$curl_rc" -ne 0 ]]; then
    if [[ -n "$curl_err_msg" ]]; then
      log_err "客户端上传失败：$upload_url ($curl_err_msg)"
    else
      log_err "客户端上传失败：$upload_url"
    fi
    return 1
  fi
  if [[ -z "$upload_resp" ]]; then
    log_err "客户端上传失败：$upload_url"
    return 1
  fi
  printf "%s" "$upload_resp"
  return 0
}

ptbd_quote_posix() {
  local raw="${1:-}"
  printf "'%s'" "$(printf "%s" "$raw" | sed "s/'/'\\\\''/g")"
}

scp_return_target() {
  local filename="${1:-}"
  local host="${BDTOOL_RETURN_SCP_HOST:-}"
  local user="${BDTOOL_RETURN_SCP_USER:-}"
  local remote_dir="${BDTOOL_RETURN_SCP_REMOTE_DIR:-}"
  [[ -n "$host" && -n "$user" && -n "$remote_dir" && -n "$filename" ]] || return 1
  printf "%s@%s:%s" "$user" "$host" "$(ptbd_quote_posix "$remote_dir/$filename")"
}

run_scp_transport() {
  local tool="$1"
  shift
  local port="${BDTOOL_RETURN_SCP_PORT:-22}"
  local strict="${BDTOOL_RETURN_SCP_STRICT_HOST_KEY_CHECKING:-accept-new}"
  local identity="${BDTOOL_RETURN_SCP_IDENTITY_FILE:-}"
  local password="${BDTOOL_RETURN_SCP_PASSWORD:-}"
  local -a transport_cmd=("$tool")

  case "$tool" in
    ssh)
      transport_cmd+=("-p" "$port" "-o" "StrictHostKeyChecking=$strict")
      ;;
    scp)
      transport_cmd+=("-P" "$port" "-o" "StrictHostKeyChecking=$strict")
      ;;
    *)
      return 1
      ;;
  esac

  if [[ -n "$identity" ]]; then
    transport_cmd+=("-i" "$identity")
  fi

  if [[ -n "$password" ]]; then
    command -v sshpass >/dev/null 2>&1 || {
      log_err "SCP 回传失败：已设置 BDTOOL_RETURN_SCP_PASSWORD，但系统缺少 sshpass"
      return 1
    }
    SSHPASS="$password" sshpass -e "${transport_cmd[@]}" "$@"
    return $?
  fi

  "${transport_cmd[@]}" "$@"
}

prepare_scp_return_path() {
  local host="${BDTOOL_RETURN_SCP_HOST:-}"
  local user="${BDTOOL_RETURN_SCP_USER:-}"
  local remote_dir="${BDTOOL_RETURN_SCP_REMOTE_DIR:-}"
  [[ -n "$host" && -n "$user" && -n "$remote_dir" ]] || return 1
  run_scp_transport ssh "$user@$host" "mkdir -p -- $(ptbd_quote_posix "$remote_dir")"
}

upload_artifact_via_scp() {
  local artifact_file="${1:-}"
  local target=""
  local elapsed_s=0
  local scp_pid=0
  [[ -f "$artifact_file" ]] || return 1
  is_scp_return_mode || return 1
  command -v ssh >/dev/null 2>&1 || {
    log_err "SCP 回传失败：缺少 ssh"
    return 1
  }
  command -v scp >/dev/null 2>&1 || {
    log_err "SCP 回传失败：缺少 scp"
    return 1
  }
  prepare_scp_return_path || {
    log_err "SCP 回传失败：无法创建远端目录"
    return 1
  }
  target="$(scp_return_target "$(basename "$artifact_file")")" || return 1
  run_scp_transport scp "$artifact_file" "$target" &
  scp_pid=$!
  while kill -0 "$scp_pid" 2>/dev/null; do
    sleep 2
    elapsed_s=$((elapsed_s + 2))
    log_info "SCP 回传进行中（${elapsed_s}s）"
  done
  wait "$scp_pid" || {
    log_err "SCP 回传失败：$target"
    return 1
  }
  printf "%s" "${BDTOOL_RETURN_SCP_REMOTE_DIR:-}/$(basename "$artifact_file")"
  return 0
}

ensure_output_root() {
  OUTPUT_ROOT="$(resolve_data_dir)/output"
  ERROR_FILE="$OUTPUT_ROOT/last_error.txt"
  mkdir -p "$OUTPUT_ROOT"
}

write_error_file() {
  local reason="$1"
  local suggestion="$2"
  ensure_output_root
  {
    printf "reason: %s\n" "$reason"
    printf "suggestion: %s\n" "$suggestion"
  } > "$ERROR_FILE"
}

ensure_log_dir() {
  local root="${1:-$(pwd)}"
  BDTOOL_ROOT="$root"
  BDTOOL_LOG_DIR="$root/bdtool-output/logs"
  BDTOOL_RUN_LOG="$BDTOOL_LOG_DIR/run.log"
  mkdir -p "$BDTOOL_LOG_DIR"
  touch "$BDTOOL_RUN_LOG"
}

setup_log_redirection() {
  ensure_log_dir "${1:-$(pwd)}"
  if [[ "${BDTOOL_LOG_REDIRECTED:-0}" == "1" ]]; then
    return 0
  fi
  BDTOOL_LOG_REDIRECTED=1
  exec > >(tee -a "$BDTOOL_RUN_LOG") 2> >(tee -a "$BDTOOL_RUN_LOG" >&2)
}

execute_with_spinner() {
  local message="$1"
  shift
  ensure_log_dir "${BDTOOL_ROOT:-$(pwd)}"
  "$@" >> "$BDTOOL_RUN_LOG" 2>&1
  local rc=$?
  if [[ "$rc" -eq 0 ]]; then
    log_success "$message 完成"
  else
    log_err "$message 失败 (请查看 $BDTOOL_RUN_LOG)"
  fi
  return "$rc"
}

die() {
  local message="${1:-执行失败}"
  local code="${2:-1}"
  ensure_log_dir "${BDTOOL_ROOT:-$(pwd)}"
  log_err "$message"
  log_err "详情日志：$BDTOOL_RUN_LOG"
  exit "$code"
}

run_ext() {
  local timeout_s="${1:-$BDTOOL_CMD_TIMEOUT}"
  shift

  if command -v timeout >/dev/null 2>&1; then
    timeout --preserve-status "${timeout_s}s" "$@"
    return $?
  fi

  "$@" &
  local pid=$!
  local start_ts now_ts
  start_ts="$(date +%s)"

  while kill -0 "$pid" 2>/dev/null; do
    now_ts="$(date +%s)"
    if (( now_ts - start_ts >= timeout_s )); then
      kill -TERM "$pid" 2>/dev/null || true
      sleep 1
      kill -KILL "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      return 124
    fi
    sleep 1
  done

  wait "$pid"
  return $?
}

# Shared output layout resolver used by both `bdtool` and `bdtool.sh`.
# Exports:
# - BDTOOL_SOURCE_INFO_ROOT: "<source-parent>/信息"
# - BDTOOL_SOURCE_GEN_NAME:  "<source-dir-name>"
resolve_source_output_layout() {
  local src_type="${1:-}"
  local src_path="${2:-}"
  local base_dir=""

  [[ -n "$src_type" && -n "$src_path" ]] || return 1

  case "$src_type" in
    VIDEO|AUDIO|ISO)
      base_dir="$(dirname "$src_path")"
      ;;
    BDMV|AUDIO_DIR)
      if [[ "$(basename "$src_path")" == "BDMV" ]]; then
        base_dir="$(dirname "$src_path")"
      else
        base_dir="$src_path"
      fi
      ;;
    *)
      return 1
      ;;
  esac

  [[ -n "$base_dir" ]] || return 1
  BDTOOL_SOURCE_INFO_ROOT="$(dirname "$base_dir")/信息"
  BDTOOL_SOURCE_GEN_NAME="$(basename "$base_dir")"
  [[ -n "$BDTOOL_SOURCE_INFO_ROOT" && -n "$BDTOOL_SOURCE_GEN_NAME" ]]
}

resolve_source_job_dir() {
  [[ -n "${BDTOOL_SOURCE_INFO_ROOT:-}" && -n "${BDTOOL_SOURCE_GEN_NAME:-}" ]] || return 1
  printf "%s" "$BDTOOL_SOURCE_INFO_ROOT/$BDTOOL_SOURCE_GEN_NAME"
}

bdinfo_section_has_content() {
  local report_file="${1:-}"
  local section_name="${2:-}"
  local mode="${3:-text}"
  [[ -s "$report_file" && -n "$section_name" ]] || return 1
  awk -v section="$section_name" -v mode="$mode" '
    function norm(line, out) {
      out=line
      gsub(/\r/, "", out)
      gsub(/^[ \t]+|[ \t]+$/, "", out)
      return out
    }
    function is_header(line, nline) {
      nline=norm(line)
      return (nline ~ /^[A-Z][A-Z0-9 _\/-]+:[ \t]*$/ || nline ~ /^BDInfo:[ \t]/ || nline ~ /^扫描(文件|时间):[ \t]/)
    }
    BEGIN { in_sec=0; found=0; seen=0; }
    {
      line=norm($0)
      if (line == section) {
        in_sec=1
        seen=1
        next
      }
      if (in_sec && is_header($0)) {
        exit(found ? 0 : 1)
      }
      if (!in_sec) {
        next
      }
      if (line == "" || line ~ /^-+$/) {
        next
      }
      if (mode == "files") {
        if (tolower(line) ~ /\.m2ts($|[ \t])/) {
          found=1
        }
      } else if (mode == "stream") {
        if (line ~ /[A-Za-z0-9]/) {
          found=1
        }
      } else {
        found=1
      }
    }
    END {
      if (!seen || !found) {
        exit 1
      }
    }
  ' "$report_file"
}

bdinfo_match_line() {
  local report_file="${1:-}"
  local pattern="${2:-}"
  local cleaned_file=""
  local rc=1
  [[ -s "$report_file" && -n "$pattern" ]] || return 1
  cleaned_file="$(mktemp)" || return 1
  tr -d '\r' < "$report_file" > "$cleaned_file"
  if LC_ALL=C grep -Eq "$pattern" "$cleaned_file"; then
    rc=0
  fi
  rm -f "$cleaned_file"
  return "$rc"
}

bdinfo_raw_report_valid() {
  local report_file="${1:-}"
  local line_count=0
  [[ -s "$report_file" ]] || return 1
  line_count="$(wc -l < "$report_file" | tr -d ' ')"
  [[ "$line_count" =~ ^[0-9]+$ ]] || return 1
  (( line_count >= 20 )) || return 1
  bdinfo_match_line "$report_file" '^[[:space:]]*DISC INFO:[[:space:]]*$' || return 1
  bdinfo_match_line "$report_file" '^[[:space:]]*PLAYLIST REPORT:[[:space:]]*$' || return 1
  bdinfo_match_line "$report_file" '^[[:space:]]*VIDEO:[[:space:]]*$' || return 1
  bdinfo_match_line "$report_file" '^[[:space:]]*AUDIO:[[:space:]]*$' || return 1
  bdinfo_match_line "$report_file" '^[[:space:]]*FILES:[[:space:]]*$' || return 1
  bdinfo_section_has_content "$report_file" "DISC INFO:" "text" || return 1
  bdinfo_section_has_content "$report_file" "PLAYLIST REPORT:" "text" || return 1
  bdinfo_section_has_content "$report_file" "VIDEO:" "stream" || return 1
  bdinfo_section_has_content "$report_file" "AUDIO:" "stream" || return 1
  bdinfo_section_has_content "$report_file" "FILES:" "files" || return 1
  if bdinfo_match_line "$report_file" '^[[:space:]]*SUBTITLES:[[:space:]]*$'; then
    bdinfo_section_has_content "$report_file" "SUBTITLES:" "text" || return 1
  fi
}

write_full_bdinfo_report() {
  local raw_report="${1:-}"
  local scan_target="${2:-}"
  local out_report="${3:-}"
  [[ -s "$raw_report" && -n "$scan_target" && -n "$out_report" ]] || return 1
  sed 's/\r$//' "$raw_report" > "$out_report"
}

bdinfo_write_report() {
  local scan_target="${1:-}"
  local out_dir="${2:-}"
  local stdout_file="${3:-}"
  [[ -n "$scan_target" && -n "$out_dir" && -n "$stdout_file" ]] || return 1
  : > "$stdout_file" || return 1

  # Prefer PTY execution to support BDInfoCLI variants that require interactive
  # playlist selection and fail when stdout is redirected.
  if command -v python3 >/dev/null 2>&1; then
    if python3 - "$scan_target" "$out_dir" "$stdout_file" <<'PY'
import os
import pty
import select
import sys
import time

scan_target = sys.argv[1]
out_dir = sys.argv[2]
stdout_file = sys.argv[3]
cmd = ["BDInfo", scan_target, out_dir]

master, slave = pty.openpty()
pid = os.fork()
if pid == 0:
    os.setsid()
    os.dup2(slave, 0)
    os.dup2(slave, 1)
    os.dup2(slave, 2)
    os.close(master)
    os.close(slave)
    os.execvp(cmd[0], cmd)

os.close(slave)
buf = ""
sent_choice = False
start_ts = time.time()

with open(stdout_file, "wb") as fp:
    while True:
        readable, _, _ = select.select([master], [], [], 1.0)
        if master in readable:
            try:
                data = os.read(master, 4096)
            except OSError:
                data = b""
            if data:
                fp.write(data)
                fp.flush()
                chunk = data.decode("utf-8", errors="ignore")
                buf += chunk
                if len(buf) > 16384:
                    buf = buf[-16384:]
                if (not sent_choice) and ("Select (q when finished):" in buf):
                    os.write(master, b"1\n")
                    time.sleep(0.15)
                    os.write(master, b"q\n")
                    sent_choice = True

        waited_pid, status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            os.close(master)
            if os.WIFEXITED(status):
                sys.exit(os.WEXITSTATUS(status))
            if os.WIFSIGNALED(status):
                sys.exit(128 + os.WTERMSIG(status))
            sys.exit(1)

        if time.time() - start_ts > 1800:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
            os.close(master)
            sys.exit(124)
PY
    then
      return 0
    fi
  fi

  # Fallback to direct call styles.
  : > "$stdout_file"
  if BDInfo "$scan_target" "$out_dir" >"$stdout_file" 2>/dev/null; then
    return 0
  fi
  : > "$stdout_file"
  if BDInfo -w "$scan_target" "$out_dir" >"$stdout_file" 2>/dev/null; then
    return 0
  fi
  return 1
}

write_bdinfo_fallback_report() {
  local scan_target="${1:-}"
  local out_report="${2:-}"
  local fail_reason="${3:-BDInfo 执行失败}"
  local probe_video="${4:-}"
  local scan_ts=""
  local probe_label=""
  local file_entry=""
  [[ -n "$scan_target" && -n "$out_report" ]] || return 1

  scan_ts="$(date '+%Y-%m-%d %H:%M:%S %z')"
  probe_label="${probe_video:-未找到可用主影片}"
  if [[ -n "$probe_video" ]]; then
    file_entry="$probe_video"
  elif [[ "$scan_target" == *.iso ]]; then
    file_entry="$scan_target"
  else
    file_entry="$scan_target/BDMV/STREAM/unknown.m2ts"
  fi

  {
    printf "BDInfo: fallback-report\n"
    printf "扫描文件: %s\n" "$scan_target"
    printf "扫描时间: %s\n" "$scan_ts"
    printf "DISC INFO:\n"
    printf "  原盘扫描已进入降级模式。\n"
    printf "  原因: %s\n" "$fail_reason"
    printf "  输入: %s\n" "$scan_target"
    printf "  说明: 当前环境中的 BDInfo 未返回可归档的完整原始报告。\n"
    printf "  影响: 结果包保留截图与说明文本，但不含精确盘结构明细。\n"
    printf "PLAYLIST REPORT:\n"
    printf "  BDInfo 未能产出完整原始报告。\n"
    printf "  建议: 可在其他机器上重试，或直接选择主影片文件处理。\n"
    printf "  当前截图来源: %s\n" "$probe_label"
    printf "  回退策略: 已优先选择检测到的主影片作为截图来源。\n"
    printf "  手动处理: 如需精确播放列表，请在支持的环境中重新扫描原盘。\n"
    printf "VIDEO:\n"
    printf "  Fallback stream: %s\n" "$probe_label"
    printf "  Screenshots generated from the detected main feature when available.\n"
    printf "  视频章节信息: 不可用（依赖 BDInfo 原始输出）。\n"
    printf "  编码明细: 请参考截图来源文件或重新运行 BDInfo。\n"
    printf "AUDIO:\n"
    printf "  音轨信息不可用：BDInfo 本次执行失败。\n"
    printf "  如需精确信息，请在 BDInfo 可运行环境中重试。\n"
    printf "  当前结果包仍可用于交付截图与基础说明。\n"
    printf "  若只关心主影片，也可以直接选择主片文件单独处理。\n"
    printf "SUBTITLES:\n"
    printf "  字幕轨信息不可用：BDInfo 本次执行失败。\n"
    printf "  这不影响本次结果包导出。\n"
    printf "  如需字幕轨明细，请在可稳定运行 BDInfo 的环境中重试。\n"
    printf "  当前文件仅保留失败说明，不伪造字幕数据。\n"
    printf "FILES:\n"
    printf "  %s\n" "$file_entry"
    printf "  Source root: %s\n" "$scan_target"
    printf "  Probe source: %s\n" "$probe_label"
    printf "  Generated by PT ReleaseKit fallback mode.\n"
  } > "$out_report"
}

bdinfo_report_valid() {
  local report_file="${1:-}"
  local line_count=0
  [[ -s "$report_file" ]] || return 1
  line_count="$(wc -l < "$report_file" | tr -d ' ')"
  [[ "$line_count" =~ ^[0-9]+$ ]] || return 1
  (( line_count >= 20 )) || return 1
  if bdinfo_raw_report_valid "$report_file"; then
    return 0
  fi
  bdinfo_match_line "$report_file" '^BDInfo:[[:space:]].+' || return 1
  bdinfo_match_line "$report_file" '^扫描文件:[[:space:]].+' || return 1
  bdinfo_match_line "$report_file" '^扫描时间:[[:space:]].+' || return 1
  bdinfo_raw_report_valid "$report_file"
}

find_valid_bdinfo_report() {
  local report_dir="${1:-}"
  local candidate=""
  [[ -d "$report_dir" ]] || return 1
  while IFS= read -r candidate; do
    [[ -n "$candidate" ]] || continue
    if bdinfo_raw_report_valid "$candidate"; then
      printf "%s" "$candidate"
      return 0
    fi
  done < <(
    find "$report_dir" -maxdepth 1 -type f -name '*.txt' -printf '%T@ %p\n' 2>/dev/null \
      | sort -nr \
      | sed -E 's/^[^ ]+ //'
  )
  return 1
}
