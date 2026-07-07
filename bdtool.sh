#!/usr/bin/env bash
set -euo pipefail

# =====================
# Bootstrap
# =====================
bt_resolve_script_path() {
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

bt_find_app_root() {
  local script_dir="$1"
  local candidate=""
  local -a candidates=()
  local user_home="${HOME:-}"

  [[ -n "${PTBDTOOL_ROOT:-}" ]] && candidates+=("${PTBDTOOL_ROOT}")
  [[ -n "${PTBD_INSTALL_ROOT:-}" ]] && candidates+=("${PTBD_INSTALL_ROOT}")
  candidates+=(
    "$script_dir"
    "$script_dir/.."
    "/opt/PT-BDtool"
  )
  [[ -n "$user_home" ]] && candidates+=("$user_home/.local/share/pt-bdtool/PT-BDtool-app")

  for candidate in "${candidates[@]}"; do
    [[ -n "$candidate" ]] || continue
    if [[ -f "$candidate/lib/ui.sh" ]]; then
      (
        cd -P "$candidate" 2>/dev/null && pwd
      )
      return 0
    fi
  done
  return 1
}

BT_SCRIPT_PATH="$(bt_resolve_script_path "${BASH_SOURCE[0]}")"
BT_SCRIPT_DIR="$(cd -P "$(dirname "$BT_SCRIPT_PATH")" && pwd)"
BDTOOL_ROOT="$(bt_find_app_root "$BT_SCRIPT_DIR" || true)"
if [[ -n "$BDTOOL_ROOT" && -f "$BDTOOL_ROOT/lib/ui.sh" ]]; then
  # shellcheck disable=SC1091
  source "$BDTOOL_ROOT/lib/ui.sh"
  setup_bundle_runtime "$BDTOOL_ROOT"
else
  BDTOOL_ROOT="$BT_SCRIPT_DIR"
  log_info() { printf "[INFO] %s\n" "$*" >&2; }
  log_warn() { printf "[WARN] %s\n" "$*" >&2; }
  log_err() { printf "[ERROR] %s\n" "$*" >&2; }
  log_success() { printf "[SUCCESS] %s\n" "$*" >&2; }
  hr() { printf "================================================================\n" >&2; }
  section() { hr; printf "%s\n" "$*" >&2; hr; }
  prompt_with_default() { local p="${1:-请输入值}" d="${2:-}" v=""; read -r -p "  ▶ ${p} [默认 ${d}]: " v < /dev/tty || true; printf "%s" "${v:-$d}"; }
  validate_nonempty() { [[ -n "${1:-}" ]]; }
  validate_int_range() { local v="${1:-}" min="${2:-1}" max="${3:-65535}"; [[ "$v" =~ ^[0-9]+$ ]] && (( v >= min && v <= max )); }
  ensure_log_dir() { local root="${1:-$BT_SCRIPT_DIR}"; BDTOOL_ROOT="$root"; BDTOOL_LOG_DIR="$root/bdtool-output/logs"; BDTOOL_RUN_LOG="$BDTOOL_LOG_DIR/run.log"; mkdir -p "$BDTOOL_LOG_DIR"; touch "$BDTOOL_RUN_LOG"; }
  setup_log_redirection() { ensure_log_dir "${1:-$BT_SCRIPT_DIR}"; [[ "${BDTOOL_LOG_REDIRECTED:-0}" == "1" ]] && return 0; BDTOOL_LOG_REDIRECTED=1; exec > >(tee -a "$BDTOOL_RUN_LOG") 2> >(tee -a "$BDTOOL_RUN_LOG" >&2); }
  execute_with_spinner() { local m="$1"; shift; ensure_log_dir "${BDTOOL_ROOT:-$BT_SCRIPT_DIR}"; "$@" >> "$BDTOOL_RUN_LOG" 2>&1; local r=$?; if [[ "$r" -eq 0 ]]; then log_success "$m 完成"; else log_err "$m 失败 (请查看 $BDTOOL_RUN_LOG)"; fi; return "$r"; }
  die() {
    local m="${1:-执行失败}" c="${2:-1}"
    ensure_log_dir "${BDTOOL_ROOT:-$BT_SCRIPT_DIR}"
    log_err "$m"
    log_err "详情日志：$BDTOOL_RUN_LOG"
    log_err "修复建议：进入 PT-BDtool 目录执行 bash install.sh --offline，然后重试 bdtool --help"
    exit "$c"
  }
fi

APP_NAME="bdtool"
BT_VERSION="${BT_VERSION:-0.1.0}"
: "${BDTOOL_AUDIO_SPECTRUM_SECONDS:=90}"
: "${BDTOOL_AUDIO_SPECTRUM_SIZE:=1280x720}"

bt_die() { die "$*"; }
bt_log() {
  local level="${OPT_LOG_LEVEL:-normal}"
  [[ "$level" == "quiet" ]] && return 0
  log_info "[$APP_NAME] $*"
}
bt_debug() {
  local level="${OPT_LOG_LEVEL:-normal}"
  [[ "$level" == "debug" ]] || return 0
  log_info "[$APP_NAME][DEBUG] $*"
}
bt_need_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 && return 0
  if [[ -n "${BDTOOL_BUNDLE_DIR:-}" && -x "$BDTOOL_BUNDLE_DIR/bin/$cmd" ]]; then
    PATH="$BDTOOL_BUNDLE_DIR/bin:$PATH"
    export PATH
    command -v "$cmd" >/dev/null 2>&1 && return 0
  fi
  if [[ "$cmd" == "ffmpeg" ]]; then
    bt_die "缺少依赖命令：ffmpeg。可复制修复：apt-get update && apt-get install -y ffmpeg mediainfo；然后执行 bash install.sh --offline"
  fi
  if [[ "$cmd" == "BDInfo" ]]; then
    bt_die "缺少依赖命令：BDInfo。请执行 bash install.sh --offline 完成离线依赖安装。"
  fi
  bt_die "缺少依赖命令：$cmd。请执行 bash install.sh --offline 后重试。"
}

bt_safe_name() {
  local s="$1"
  s="${s//\//_}"
  s="${s//$'\n'/ }"
  s="${s//$'\r'/ }"
  s="$(echo "$s" | sed 's/[[:space:]]\+/ /g; s/^ *//; s/ *$//')"
  [[ -z "$s" ]] && s="unknown"
  echo "$s"
}

bt_unique_dir() {
  local root="$1"
  local base="$2"
  local out="$root/$base"
  local idx=2
  while [[ -e "$out" ]]; do
    out="$root/${base}_$idx"
    idx=$((idx + 1))
  done
  echo "$out"
}

bt_prepare_output_layout() {
  local src_type="$1"
  local src_path="$2"
  local out_override="${3:-}"
  local _label_unused="${4:-$(basename "$src_path")}"
  local root_dir=""

  if [[ -n "$out_override" ]]; then
    root_dir="$out_override/PT-BDtool"
    BT_JOB_DIR="$root_dir"
    BT_INFO_DIR="$BT_JOB_DIR/信息"
    mkdir -p "$BT_INFO_DIR"
    bt_debug "output layout=override job_dir=$BT_JOB_DIR info_dir=$BT_INFO_DIR"
    return 0
  fi

  resolve_source_output_layout "$src_type" "$src_path" || bt_die "无法计算输出路径：$src_type $src_path"
  root_dir="$(resolve_source_job_dir)" || bt_die "无法计算输出路径：$src_type $src_path"
  BT_JOB_DIR="$root_dir"
  BT_INFO_DIR="$BT_JOB_DIR"
  mkdir -p "$BT_INFO_DIR"
  bt_debug "output layout=source-based root=$BDTOOL_SOURCE_INFO_ROOT job_dir=$BT_JOB_DIR info_dir=$BT_INFO_DIR"
}

bt_is_positive_int() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

# =====================
# Module: discover
# =====================
bt_find_video_files() {
  local base="$1"
  local path=""
  find "$base" -type f \( \
    -iname "*.mkv" -o -iname "*.mp4" -o -iname "*.m2ts" -o -iname "*.ts" -o \
    -iname "*.avi" -o -iname "*.mov" -o -iname "*.wmv" -o -iname "*.webm" -o \
    -iname "*.mpg" -o -iname "*.mpeg" \
  \) ! -iname "*.d.ts" 2>/dev/null | sort -u | while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    if bt_bdmv_root_from_stream_file "$path" >/dev/null 2>&1; then
      continue
    fi
    if ! bt_is_video_file "$path"; then
      continue
    fi
    printf '%s\n' "$path"
  done
}

bt_find_audio_files() {
  local base="$1"
  find "$base" -type f \( \
    -iname "*.mp3" -o -iname "*.flac" -o -iname "*.wav" -o -iname "*.m4a" -o \
    -iname "*.aac" -o -iname "*.ogg" -o -iname "*.opus" \
  \) 2>/dev/null | sort -u
}

bt_bdmv_root_from_stream_file() {
  local path="${1:-}"
  local marker="/BDMV/STREAM/"
  local prefix=""
  [[ -n "$path" && "$path" == *"$marker"* ]] || return 1
  prefix="${path%%"$marker"*}"
  [[ -n "$prefix" && -d "$prefix/BDMV/PLAYLIST" ]] || return 1
  printf "%s" "$prefix"
}

bt_is_video_file() {
  local p="$1"
  local l
  l="$(echo "$p" | tr '[:upper:]' '[:lower:]')"
  case "$l" in
    *.d.ts) return 1 ;;
    *.ts)
      if command -v ffprobe >/dev/null 2>&1; then
        ffprobe -v error -select_streams v:0 -show_entries stream=codec_type -of csv=p=0 "$p" 2>/dev/null | grep -qx 'video'
        return $?
      fi
      return 1
      ;;
    *.mkv|*.mp4|*.m2ts|*.avi|*.mov|*.wmv|*.webm|*.mpg|*.mpeg) return 0 ;;
    *) return 1 ;;
  esac
}

bt_is_audio_file() {
  local p="$1"
  local l
  l="$(echo "$p" | tr '[:upper:]' '[:lower:]')"
  case "$l" in
    *.mp3|*.flac|*.wav|*.m4a|*.aac|*.ogg|*.opus) return 0 ;;
    *) return 1 ;;
  esac
}

bt_resolve_bd_path() {
  local p="$1"
  if [[ -f "$p" ]]; then
    local l
    l="$(echo "$p" | tr '[:upper:]' '[:lower:]')"
    [[ "$l" == *.iso ]] && { echo "$p"; return 0; }
    return 1
  fi

  if [[ -d "$p" ]]; then
    if [[ -d "$p/BDMV" ]]; then
      echo "$p"
      return 0
    fi
    if [[ "$(basename "$p")" == "BDMV" && -d "$p/STREAM" && -d "$p/PLAYLIST" ]]; then
      dirname "$p"
      return 0
    fi
  fi
  return 1
}

# =====================
# Module: media
# =====================
bt_pick_random_seconds() {
  local duration_s="$1"
  local n="$2"

  local dur_int="${duration_s%.*}"
  [[ -z "$dur_int" || "$dur_int" -lt 1 ]] && dur_int=1

  local start=0
  local end="$dur_int"
  if [[ "$dur_int" -ge 120 ]]; then
    start=$((dur_int / 20))
    end=$((dur_int - dur_int / 20))
    [[ "$end" -le "$start" ]] && { start=0; end="$dur_int"; }
  fi

  local i=1
  while [[ "$i" -le "$n" ]]; do
    local r
    r="$(od -An -N2 -tu2 /dev/urandom | tr -d ' ')"
    local span=$((end - start))
    [[ "$span" -lt 1 ]] && span=1
    echo $((start + (r % span)))
    i=$((i + 1))
  done
}

bt_make_screenshots() {
  local video="$1"
  local info_dir="$2"
  local _n_ignored="${3:-}"
  local n=6

  bt_need_cmd ffprobe
  bt_need_cmd ffmpeg

  local duration
  duration="$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$video" || true)"
  [[ -z "$duration" ]] && bt_die "无法读取视频时长：$video"

  mkdir -p "$info_dir"

  local idx=1
  while read -r sec; do
    execute_with_spinner "截图 ${idx}/6" ffmpeg -nostdin -hide_banner -loglevel error -ss "$sec" -i "$video" -frames:v 1 -y "$info_dir/${idx}.png" || bt_die "截图失败：$video"
    idx=$((idx + 1))
  done < <(bt_pick_random_seconds "$duration" "$n")
}

bt_run_mediainfo_report() {
  local video="$1"
  local info_dir="$2"

  bt_need_cmd mediainfo
  mkdir -p "$info_dir"
  execute_with_spinner "生成 MediaInfo" bt_write_mediainfo "$video" "$info_dir/mediainfo.txt" || bt_die "MediaInfo 生成失败：$video"
}

bt_write_mediainfo() {
  local video="$1"
  local out_file="$2"
  mediainfo "$video" > "$out_file"
}

bt_bdinfo_report_valid() {
  local report_file="${1:-}"
  local cleaned=""
  if declare -F bdinfo_report_valid >/dev/null 2>&1; then
    bdinfo_report_valid "$report_file"
    return $?
  fi
  [[ -s "$report_file" ]] || return 1
  cleaned="$(mktemp)"
  tr -d '\r' < "$report_file" > "$cleaned"
  LC_ALL=C grep -Eq '^BDInfo:[[:space:]].+' "$cleaned" || { rm -f "$cleaned"; return 1; }
  LC_ALL=C grep -Eq '^扫描文件:[[:space:]].+' "$cleaned" || { rm -f "$cleaned"; return 1; }
  LC_ALL=C grep -Eq '^扫描时间:[[:space:]].+' "$cleaned" || { rm -f "$cleaned"; return 1; }
  LC_ALL=C grep -Eq '^[[:space:]]*DISC INFO:[[:space:]]*$' "$cleaned" || { rm -f "$cleaned"; return 1; }
  LC_ALL=C grep -Eq '^[[:space:]]*PLAYLIST REPORT:[[:space:]]*$' "$cleaned" || { rm -f "$cleaned"; return 1; }
  LC_ALL=C grep -Eq '^[[:space:]]*VIDEO:[[:space:]]*$' "$cleaned" || { rm -f "$cleaned"; return 1; }
  LC_ALL=C grep -Eq '^[[:space:]]*AUDIO:[[:space:]]*$' "$cleaned" || { rm -f "$cleaned"; return 1; }
  LC_ALL=C grep -Eq '^[[:space:]]*SUBTITLES:[[:space:]]*$' "$cleaned" || { rm -f "$cleaned"; return 1; }
  LC_ALL=C grep -Eq '^[[:space:]]*FILES:[[:space:]]*$' "$cleaned" || { rm -f "$cleaned"; return 1; }
  rm -f "$cleaned"
}

bt_bdinfo_raw_report_valid() {
  local report_file="${1:-}"
  local cleaned=""
  if declare -F bdinfo_raw_report_valid >/dev/null 2>&1; then
    bdinfo_raw_report_valid "$report_file"
    return $?
  fi
  [[ -s "$report_file" ]] || return 1
  cleaned="$(mktemp)"
  tr -d '\r' < "$report_file" > "$cleaned"
  LC_ALL=C grep -Eq '^[[:space:]]*DISC INFO:[[:space:]]*$' "$cleaned" || { rm -f "$cleaned"; return 1; }
  LC_ALL=C grep -Eq '^[[:space:]]*PLAYLIST REPORT:[[:space:]]*$' "$cleaned" || { rm -f "$cleaned"; return 1; }
  LC_ALL=C grep -Eq '^[[:space:]]*VIDEO:[[:space:]]*$' "$cleaned" || { rm -f "$cleaned"; return 1; }
  LC_ALL=C grep -Eq '^[[:space:]]*AUDIO:[[:space:]]*$' "$cleaned" || { rm -f "$cleaned"; return 1; }
  LC_ALL=C grep -Eq '^[[:space:]]*SUBTITLES:[[:space:]]*$' "$cleaned" || { rm -f "$cleaned"; return 1; }
  LC_ALL=C grep -Eq '^[[:space:]]*FILES:[[:space:]]*$' "$cleaned" || { rm -f "$cleaned"; return 1; }
  rm -f "$cleaned"
}

bt_write_full_bdinfo_report() {
  local raw_report="${1:-}"
  local scan_target="${2:-}"
  local out_report="${3:-}"
  if declare -F write_full_bdinfo_report >/dev/null 2>&1; then
    write_full_bdinfo_report "$raw_report" "$scan_target" "$out_report"
    return $?
  fi
  [[ -s "$raw_report" && -n "$scan_target" && -n "$out_report" ]] || return 1
  {
    printf "BDInfo: BDInfoCLI-ng\n"
    printf "扫描文件: %s\n" "$scan_target"
    printf "扫描时间: %s\n" "$(date '+%Y-%m-%d %H:%M:%S %z')"
    cat "$raw_report"
  } > "$out_report"
}

bt_find_valid_bdinfo_report() {
  local info_dir="${1:-}"
  local candidate=""
  if declare -F find_valid_bdinfo_report >/dev/null 2>&1; then
    find_valid_bdinfo_report "$info_dir"
    return $?
  fi
  [[ -d "$info_dir" ]] || return 1
  while IFS= read -r candidate; do
    [[ -n "$candidate" ]] || continue
    if bt_bdinfo_raw_report_valid "$candidate"; then
      printf "%s" "$candidate"
      return 0
    fi
  done < <(
    find "$info_dir" -maxdepth 1 -type f -name '*.txt' -printf '%T@ %p\n' 2>/dev/null \
      | sort -nr \
      | sed -E 's/^[^ ]+ //'
  )
  return 1
}

bt_write_bdinfo_report() {
  local bd_path="$1"
  local info_dir="$2"
  local stdout_file="$3"
  if declare -F bdinfo_write_report >/dev/null 2>&1; then
    bdinfo_write_report "$bd_path" "$info_dir" "$stdout_file"
    return $?
  fi
  BDInfo "$bd_path" "$info_dir" > "$stdout_file"
}

bt_run_bdinfo_report() {
  local bd_path="$1"
  local info_dir="$2"
  local latest_txt=""
  local stdout_txt=""
  local err_msg=""
  local attempt=0
  local probe_video=""

  bt_need_cmd BDInfo
  mkdir -p "$info_dir"

  stdout_txt="$info_dir/.bdinfo_stdout_$$.txt"
  rm -f "$info_dir/BDInfo.txt" "$stdout_txt"
  bt_log "BDInfo: run on $bd_path"
  probe_video="$(bt_pick_disc_probe_video "$bd_path" || true)"
  if ! execute_with_spinner "生成 BDInfo" bt_write_bdinfo_report "$bd_path" "$info_dir" "$stdout_txt"; then
    if ! write_bdinfo_fallback_report "$bd_path" "$info_dir/BDInfo.txt" "BDInfo 执行失败或崩溃" "$probe_video"; then
      err_msg="BDInfo 执行失败：$bd_path"
    fi
  else
    while [[ "$attempt" -lt 10 ]]; do
      if bt_bdinfo_raw_report_valid "$stdout_txt"; then
        latest_txt="$stdout_txt"
        break
      fi
      latest_txt="$(bt_find_valid_bdinfo_report "$info_dir" || true)"
      [[ -n "$latest_txt" ]] && break
      attempt=$((attempt + 1))
      sleep 1
    done
    if [[ -z "$latest_txt" ]]; then
      if ! write_bdinfo_fallback_report "$bd_path" "$info_dir/BDInfo.txt" "BDInfo 输出无效：缺少完整区块" "$probe_video"; then
        err_msg="BDInfo 输出无效：缺少完整区块（DISC INFO/PLAYLIST REPORT/VIDEO/AUDIO/SUBTITLES/FILES）（$bd_path）"
      fi
    else
      if ! bt_write_full_bdinfo_report "$latest_txt" "$bd_path" "$info_dir/BDInfo.txt"; then
        err_msg="BDInfo 报告归档失败：$info_dir/BDInfo.txt"
      fi
      if [[ -z "$err_msg" ]] && ! bt_bdinfo_report_valid "$info_dir/BDInfo.txt"; then
        err_msg="BDInfo 输出无效：$info_dir/BDInfo.txt（需含 BDInfo/扫描文件/扫描时间 + 全区块且非空）"
      fi
    fi
  fi

  find "$info_dir" -maxdepth 1 -type f -name '*.txt' ! -name 'BDInfo.txt' -delete
  rm -f "$stdout_txt"
  [[ -z "$err_msg" ]] || bt_die "$err_msg"
}

bt_finalize_video_artifacts() {
  local info_dir="$1"
  local i
  local keep_re='^[1-6]\.png$|^mediainfo\.txt$'

  for i in 1 2 3 4 5 6; do
    [[ -s "$info_dir/${i}.png" ]] || bt_die "生成失败：缺少有效截图 $info_dir/${i}.png"
  done
  [[ -s "$info_dir/mediainfo.txt" ]] || bt_die "生成失败：未发现有效 mediainfo.txt（$info_dir）"

  while IFS= read -r f; do
    [[ "$f" =~ $keep_re ]] || rm -f -- "$info_dir/$f"
  done < <(find "$info_dir" -maxdepth 1 -type f -printf '%f\n')

  local cnt
  cnt="$(find "$info_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')"
  [[ "$cnt" == "7" ]] || bt_die "生成失败：产物数量异常（期望7，实际$cnt）：$info_dir"
}

bt_make_audio_spectrum() {
  local audio="$1"
  local info_dir="$2"
  bt_need_cmd ffmpeg
  mkdir -p "$info_dir"
  execute_with_spinner "生成频谱图" \
    ffmpeg -nostdin -hide_banner -loglevel error -y -i "$audio" \
    -filter_complex "[0:a]aformat=channel_layouts=mono,atrim=end=${BDTOOL_AUDIO_SPECTRUM_SECONDS},showspectrumpic=s=${BDTOOL_AUDIO_SPECTRUM_SIZE}:legend=disabled" \
    -frames:v 1 "$info_dir/频谱图.png" || bt_die "频谱图生成失败：$audio"
}

bt_finalize_audio_artifacts() {
  local info_dir="$1"
  local keep_re='^频谱图\.png$|^mediainfo\.txt$'
  local f cnt
  [[ -s "$info_dir/频谱图.png" ]] || bt_die "生成失败：缺少有效频谱图 $info_dir/频谱图.png"
  [[ -s "$info_dir/mediainfo.txt" ]] || bt_die "生成失败：缺少有效信息文件 $info_dir/mediainfo.txt"
  while IFS= read -r f; do
    [[ "$f" =~ $keep_re ]] || rm -f -- "$info_dir/$f"
  done < <(find "$info_dir" -maxdepth 1 -type f -printf '%f\n')
  cnt="$(find "$info_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')"
  [[ "$cnt" == "2" ]] || bt_die "生成失败：音频产物数量异常（期望2，实际$cnt）：$info_dir"
}

bt_create_placeholder_png() {
  local dst="$1"
  if command -v base64 >/dev/null 2>&1; then
    printf 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=' | base64 -d > "$dst" 2>/dev/null || : > "$dst"
  else
    : > "$dst"
  fi
}

bt_ensure_disc_six_png() {
  local info_dir="$1"
  local i last=""
  for i in 1 2 3 4 5 6; do
    if [[ -s "$info_dir/${i}.png" ]]; then
      last="$info_dir/${i}.png"
      break
    fi
  done
  for i in 1 2 3 4 5 6; do
    if [[ ! -s "$info_dir/${i}.png" ]]; then
      if [[ -n "$last" && -s "$last" ]]; then
        cp -f "$last" "$info_dir/${i}.png" 2>/dev/null || bt_create_placeholder_png "$info_dir/${i}.png"
      else
        bt_create_placeholder_png "$info_dir/${i}.png"
        last="$info_dir/${i}.png"
      fi
    fi
  done
}

bt_pick_disc_probe_video() {
  local bd_path="$1"
  local candidate=""
  if [[ -d "$bd_path" && -d "$bd_path/BDMV/STREAM" ]]; then
    candidate="$(find "$bd_path/BDMV/STREAM" -type f -iname '*.m2ts' -printf '%s %p\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2- || true)"
  elif [[ -d "$bd_path" && -d "$bd_path/STREAM" ]]; then
    candidate="$(find "$bd_path/STREAM" -type f -iname '*.m2ts' -printf '%s %p\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2- || true)"
  elif [[ -f "$bd_path" ]]; then
    candidate="$bd_path"
  fi
  printf "%s" "$candidate"
}

bt_make_disc_screenshots() {
  local bd_path="$1"
  local info_dir="$2"
  local probe_video duration dur_int i sec

  probe_video="$(bt_pick_disc_probe_video "$bd_path")"
  if [[ -n "$probe_video" ]] && command -v ffprobe >/dev/null 2>&1 && command -v ffmpeg >/dev/null 2>&1; then
    duration="$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$probe_video" 2>/dev/null || true)"
    dur_int="${duration%.*}"
    [[ "$dur_int" =~ ^[0-9]+$ ]] || dur_int=60
    (( dur_int < 12 )) && dur_int=12
    for i in 1 2 3 4 5 6; do
      sec=$(( (dur_int * i) / 7 ))
      ffmpeg -nostdin -hide_banner -loglevel error -ss "$sec" -i "$probe_video" -frames:v 1 -y "$info_dir/${i}.png" >/dev/null 2>&1 || true
    done
  fi
  bt_ensure_disc_six_png "$info_dir"
}

bt_finalize_disc_artifacts() {
  local info_dir="$1"
  local i keep_re='^[1-6]\.png$|^BDInfo\.txt$' f cnt
  bt_bdinfo_report_valid "$info_dir/BDInfo.txt" || bt_die "生成失败：缺少有效 BDInfo.txt（$info_dir）"
  for i in 1 2 3 4 5 6; do
    [[ -s "$info_dir/${i}.png" ]] || bt_die "生成失败：缺少有效截图 $info_dir/${i}.png"
  done
  while IFS= read -r f; do
    [[ "$f" =~ $keep_re ]] || rm -f -- "$info_dir/$f"
  done < <(find "$info_dir" -maxdepth 1 -type f -printf '%f\n')
  cnt="$(find "$info_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')"
  [[ "$cnt" == "7" ]] || bt_die "生成失败：原盘产物数量异常（期望7，实际$cnt）：$info_dir"
}

# =====================
# Module: defaults/config
# =====================
: "${OPT_MEDIAINFO:=1}"
: "${OPT_SHOTS:=1}"
: "${OPT_SHOTS_N:=}"
: "${OPT_JOBS:=}"
: "${OPT_LOG_LEVEL:=normal}"

bt_default_jobs() {
  local n=1
  if command -v nproc >/dev/null 2>&1; then
    n="$(nproc)"
  elif command -v getconf >/dev/null 2>&1; then
    n="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
  fi
  [[ "$n" =~ ^[0-9]+$ ]] || n=1
  [[ "$n" -lt 1 ]] && n=1
  [[ "$n" -gt 4 ]] && n=4
  echo "$n"
}

bt_infer_out_dir() {
  local scan_path="$1"
  local base="./bdtool-output"
  if [[ -d "$scan_path" ]]; then
    local name
    name="$(basename "$scan_path")"
    echo "$base/$(bt_safe_name "$name")"
  else
    echo "$base"
  fi
}

bt_validate_options() {
  bt_is_positive_int "$OPT_SHOTS_N" || bt_die "--shots 必须是正整数"
  bt_is_positive_int "$OPT_JOBS" || bt_die "--jobs 必须是正整数"
}

# =====================
# Module: jobs
# =====================
bt_run_with_jobs() {
  local jobs="$1"
  shift

  local running=0
  local cmd
  for cmd in "$@"; do
    bash -c "$cmd" &
    running=$((running + 1))
    if [[ "$running" -ge "$jobs" ]]; then
      wait -n
      running=$((running - 1))
    fi
  done
  wait
}

# =====================
# Module: worker
# =====================
bt_process_video_file() {
  local video="$1"
  local base_out="${2:-}"

  local name
  name="$(basename "$video")"

  bt_prepare_output_layout "VIDEO" "$video" "$base_out" "$name"
  local jobdir="$BT_JOB_DIR"
  local info_dir="$BT_INFO_DIR"

  bt_log "VIDEO: $video"
  bt_log "OUT:   $jobdir"
  bt_log "OPTS:  mediainfo=$OPT_MEDIAINFO shots=$OPT_SHOTS shots_n=$OPT_SHOTS_N"

  if [[ "$OPT_MEDIAINFO" != "1" && "$OPT_SHOTS" != "1" ]]; then
    echo "本次已关闭 mediainfo 与 screenshots，因此该目录为空（这是预期行为）。" > "$info_dir/README.txt"
    return 0
  fi

  if [[ "$OPT_MEDIAINFO" == "1" ]]; then
    bt_run_mediainfo_report "$video" "$info_dir"
  fi

  if [[ "$OPT_SHOTS" == "1" ]]; then
    bt_make_screenshots "$video" "$info_dir" "$OPT_SHOTS_N"
  fi

  if [[ "$OPT_MEDIAINFO" == "1" && ! -s "$info_dir/mediainfo.txt" ]]; then
    bt_die "生成失败：未发现有效 mediainfo.txt（$info_dir）"
  fi
  if [[ "$OPT_MEDIAINFO" == "1" && "$OPT_SHOTS" == "1" ]]; then
    bt_finalize_video_artifacts "$info_dir"
  elif [[ "$OPT_SHOTS" == "1" ]]; then
    local i
    for i in 1 2 3 4 5 6; do
      [[ -s "$info_dir/${i}.png" ]] || bt_die "生成失败：缺少有效截图 $info_dir/${i}.png"
    done
  fi
  bt_debug "artifact check ok type=VIDEO dir=$info_dir"

  bt_log "BDInfo: skipped (非 BDMV/ISO 输入)"
}

bt_process_audio_file() {
  local audio="$1"
  local base_out="${2:-}"
  local name
  name="$(basename "$audio")"

  # Reuse source-based output layout (same as video inputs).
  bt_prepare_output_layout "VIDEO" "$audio" "$base_out" "$name"
  local info_dir="$BT_INFO_DIR"

  bt_log "AUDIO: $audio"
  bt_log "OUT:   $BT_JOB_DIR"

  bt_run_mediainfo_report "$audio" "$info_dir"
  bt_make_audio_spectrum "$audio" "$info_dir"
  bt_finalize_audio_artifacts "$info_dir"
  bt_debug "artifact check ok type=AUDIO dir=$info_dir"
}

bt_worker_entry() {
  OPT_MEDIAINFO="${OPT_MEDIAINFO:-1}"
  OPT_SHOTS="${OPT_SHOTS:-1}"
  OPT_SHOTS_N="${OPT_SHOTS_N:-4}"

  local video="$1"
  local out_base="$2"
  if bt_is_audio_file "$video"; then
    bt_process_audio_file "$video" "$out_base"
  else
    bt_process_video_file "$video" "$out_base"
  fi
}

# =====================
# Module: scan
# =====================
bt_process_local_scan() {
  local scan_path="$1"
  local out_base="${2:-}"

  bt_log "SCAN:  $scan_path"
  if [[ -n "$out_base" ]]; then
    bt_log "OUT:   override=$out_base"
  else
    bt_log "OUT:   auto=源文件上层目录/信息"
  fi
  bt_log "OPTS:  mediainfo=$OPT_MEDIAINFO shots=$OPT_SHOTS shots_n=$OPT_SHOTS_N jobs=$OPT_JOBS"
  bt_debug "scan_path_type=$( [[ -f "$scan_path" ]] && echo file || echo dir )"

  local bd_path=""
  if bd_path="$(bt_resolve_bd_path "$scan_path")"; then
    local src_type="BDMV"
    [[ -f "$bd_path" ]] && src_type="ISO"
    bt_prepare_output_layout "$src_type" "$bd_path" "$out_base" "$(basename "$scan_path")"
    bt_run_bdinfo_report "$bd_path" "$BT_INFO_DIR"
    bt_make_disc_screenshots "$bd_path" "$BT_INFO_DIR"
    bt_finalize_disc_artifacts "$BT_INFO_DIR"
    bt_debug "artifact check ok type=$src_type dir=$BT_INFO_DIR"
    echo "$BT_JOB_DIR"
    return 0
  fi

  if [[ -f "$scan_path" ]]; then
    if ! bt_is_video_file "$scan_path" && ! bt_is_audio_file "$scan_path"; then
      bt_die "不支持的文件类型：$scan_path（仅视频/音频文件或 Blu-ray BDMV/ISO）"
    fi
    export OPT_MEDIAINFO OPT_SHOTS OPT_SHOTS_N OPT_LOG_LEVEL BDTOOL_ROOT
    local worker_cmd
    worker_cmd="$(printf '%q ' "$0") __worker_video $(printf '%q ' "$scan_path") $(printf '%q' "$out_base")"
    execute_with_spinner "处理媒体 $(basename "$scan_path")" bash -c "$worker_cmd" || bt_die "处理失败：$scan_path"
    echo "DONE"
    return 0
  fi

  local media_list=()
  while IFS= read -r vf; do
    [[ -n "$vf" ]] && media_list+=("$vf")
  done < <(bt_find_video_files "$scan_path")
  while IFS= read -r af; do
    [[ -n "$af" ]] && media_list+=("$af")
  done < <(bt_find_audio_files "$scan_path")

  [[ "${#media_list[@]}" -gt 0 ]] || bt_die "未发现可处理媒体文件：$scan_path"

  local cmds=()
  local v
  export OPT_MEDIAINFO OPT_SHOTS OPT_SHOTS_N OPT_LOG_LEVEL BDTOOL_ROOT
  for v in "${media_list[@]}"; do
    cmds+=("$(printf '%q ' "$0") __worker_video $(printf '%q ' "$v") $(printf '%q' "$out_base")")
  done

  if [[ "$OPT_JOBS" -le 1 ]]; then
    local c
    for c in "${cmds[@]}"; do
      execute_with_spinner "处理媒体任务" bash -c "$c" || bt_die "处理失败：$scan_path"
    done
  else
    execute_with_spinner "并行处理 ${#cmds[@]} 个视频任务" bt_run_with_jobs "$OPT_JOBS" "${cmds[@]}" || bt_die "并行处理失败：$scan_path"
  fi

  echo "DONE"
}

# =====================
# Module: cli
# =====================
bt_usage() {
  cat <<'USAGE'
bdtool <path> [options]
bdtool scan <path> --out <dir> [options]  # 兼容入口
bdtool doctor
bdtool status
bdtool version
bdtool start
bdtool install
bdtool clean

options:
  --log-level LEVEL  日志级别：quiet|normal|debug（默认 normal）
  --quiet            等价于 --log-level quiet
  --no-mediainfo     不生成 MediaInfo
  --no-shots         不截图
  --mode dry         等价于 --no-shots --no-mediainfo
  --shots N          参数保留；最终固定输出 6 张截图（1.png..6.png）
  -s N               等价于 --shots N
  --jobs N           并行任务数（默认 1）
  -j N               等价于 --jobs N
  --out DIR          输出目录（默认按源路径上层目录/信息；显式指定时覆盖）

examples:
  ./bdtool.sh movie.mkv
  ./bdtool.sh song.flac
  ./bdtool.sh /data/videos -s 6 -j 2
  ./bdtool.sh movie.mkv --log-level debug
  ./bdtool.sh scan /data/videos --out output
  ./bdtool.sh --version
  ./bdtool.sh install
  ./bdtool.sh clean

tips:
  直接执行 `bdtool` 或 `pt` 会进入菜单模式
  执行 `bdtool --help` / `pt --help` 会显示这份 CLI 帮助
USAGE
}

bt_cmd_version() {
  echo "$APP_NAME $BT_VERSION"
}

bt_cmd_start() {
  local launcher="$BT_SCRIPT_DIR/ptbd-start.sh"
  if [[ -x "$launcher" ]]; then
    exec "$launcher"
  fi
  if command -v bdtool >/dev/null 2>&1; then
    exec bdtool
  fi
  bt_die "未找到启动入口：请确认已安装或存在 ptbd-start.sh"
}

bt_cmd_doctor() {
  section "doctor"
  local c
  for c in find sort od awk sed; do
    if command -v "$c" >/dev/null 2>&1; then
      log_success "OK: $c"
    else
      log_warn "MISS: $c"
    fi
  done
  for c in ffmpeg ffprobe mediainfo; do
    if command -v "$c" >/dev/null 2>&1; then
      log_success "OK: $c"
    else
      log_warn "MISS: $c"
    fi
  done
  if command -v BDInfo >/dev/null 2>&1; then
    log_success "OK: BDInfo"
  else
    log_warn "MISS: BDInfo (安装提示：运行 install.sh，Linux x64 会自动安装 BDInfoCLI-ng)"
  fi
}

bt_try_version_cmd() {
  local out
  if out="$("$@" 2>/dev/null)"; then
    out="$(echo "$out" | head -n 1)"
    [[ -n "$out" ]] && { echo "$out"; return 0; }
  fi
  return 1
}

bt_cmd_status() {
  section "status"
  local locale="${LC_ALL:-${LANG:-}}"
  local is_zh=0
  [[ "$locale" == *zh* || "$locale" == *ZH* ]] && is_zh=1

  local install_path
  install_path="$(command -v bdtool 2>/dev/null || true)"
  [[ -n "$install_path" ]] || install_path="$0"

  local version="unknown"
  if bt_try_version_cmd bdtool --version >/dev/null 2>&1; then
    version="$(bt_try_version_cmd bdtool --version)"
  elif bt_try_version_cmd "$0" --version >/dev/null 2>&1; then
    version="$(bt_try_version_cmd "$0" --version)"
  elif command -v git >/dev/null 2>&1 && git rev-parse --short HEAD >/dev/null 2>&1; then
    version="$(git rev-parse --short HEAD)"
  fi

  if [[ "$is_zh" == "1" ]]; then
    log_info "[bdtool] 安装路径：$install_path"
    log_info "[bdtool] 版本：$version"
    log_info "[bdtool] 依赖检查："
  else
    log_info "[bdtool] Install path: $install_path"
    log_info "[bdtool] Version: $version"
    log_info "[bdtool] Dependency check:"
  fi

  local fail=0
  local dep
  for dep in ffmpeg ffprobe mediainfo BDInfo; do
    if command -v "$dep" >/dev/null 2>&1; then
      log_success "OK: $dep"
    else
      log_warn "MISS: $dep"
      fail=1
    fi
  done

  if [[ "$is_zh" == "1" ]]; then
    if [[ "$fail" -eq 0 ]]; then
      log_success "[bdtool] 结果：PASS"
    else
      log_err "[bdtool] 结果：FAIL"
    fi
  else
    if [[ "$fail" -eq 0 ]]; then
      log_success "[bdtool] Result: PASS"
    else
      log_err "[bdtool] Result: FAIL"
    fi
  fi

  return "$fail"
}

bt_cmd_install() {
  section "install"
  execute_with_spinner "运行安装脚本" ./install.sh || bt_die "安装失败"
  execute_with_spinner "运行依赖检查" "$0" doctor || bt_die "安装后依赖检查失败"
  log_success "INSTALL OK"
}

bt_cmd_clean() {
  local target="./bdtool-output"
  [[ "$target" == "./bdtool-output" ]] || bt_die "clean safety check failed"

  if [[ -d "$target" ]]; then
    rm -rf -- "$target" || bt_die "清理失败"
    log_success "清理输出目录 完成"
    bt_log "cleaned: $target"
  else
    log_info "nothing to clean"
  fi
}

bt_main_scan() {
  local scan_path="$1"
  shift

  local out_dir=""
  local quiet=0
  local default_jobs
  default_jobs="$(bt_default_jobs)"

  OPT_MEDIAINFO="${OPT_MEDIAINFO:-1}"
  OPT_SHOTS="${OPT_SHOTS:-1}"
  OPT_SHOTS_N="${OPT_SHOTS_N:-4}"
  OPT_JOBS="${OPT_JOBS:-$default_jobs}"
  OPT_LOG_LEVEL="${OPT_LOG_LEVEL:-normal}"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --log-level)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || bt_die "--log-level requires a value. Example: ./bdtool.sh <path> --log-level debug"
        case "$2" in
          quiet|normal|debug) OPT_LOG_LEVEL="$2" ;;
          *) bt_die "invalid log level: $2. Use quiet|normal|debug" ;;
        esac
        shift 2
        ;;
      --out)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || bt_die "--out requires a value. Example: ./bdtool.sh scan <path> --out ./bdtool-output"
        out_dir="${2:-}"
        shift 2
        ;;
      --no-mediainfo) OPT_MEDIAINFO=0; shift 1 ;;
      --no-shots) OPT_SHOTS=0; shift 1 ;;
      --mode)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || bt_die "--mode requires a value. Example: ./bdtool.sh <path> --mode dry"
        if [[ "$2" == "dry" ]]; then
          OPT_MEDIAINFO=0
          OPT_SHOTS=0
        else
          bt_die "unsupported mode: $2. Example: ./bdtool.sh <path> --mode dry"
        fi
        shift 2
        ;;
      --shots)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || bt_die "--shots requires a value. Example: ./bdtool.sh <path> --shots 4"
        OPT_SHOTS_N="${2:-}"
        shift 2
        ;;
      -s)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || bt_die "-s requires a value. Example: ./bdtool.sh <path> -s 4"
        OPT_SHOTS_N="${2:-}"
        shift 2
        ;;
      --jobs)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || bt_die "--jobs requires a value. Example: ./bdtool.sh <path> --jobs 2"
        OPT_JOBS="${2:-}"
        shift 2
        ;;
      -j)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || bt_die "-j requires a value. Example: ./bdtool.sh <path> -j 2"
        OPT_JOBS="${2:-}"
        shift 2
        ;;
      --quiet) quiet=1; shift 1 ;;
      -h|--help) bt_usage; exit 0 ;;
      *) bt_die "未知参数：$1。示例：./bdtool.sh <path> --mode dry" ;;
    esac
  done

  if [[ "$quiet" == "1" ]]; then
    OPT_LOG_LEVEL="quiet"
  fi

  [[ -e "$scan_path" ]] || bt_die "路径不存在：$scan_path。示例：./bdtool.sh ./movie.mkv --mode dry"
  [[ -n "$out_dir" ]] || bt_log "OUT(auto): 源文件上层目录/信息"

  bt_debug "effective options: mediainfo=$OPT_MEDIAINFO shots=$OPT_SHOTS shots_n=$OPT_SHOTS_N jobs=$OPT_JOBS log_level=$OPT_LOG_LEVEL"
  OPT_SHOTS_N=6
  bt_validate_options

  if [[ "$OPT_LOG_LEVEL" == "quiet" ]]; then
    bt_process_local_scan "$scan_path" "$out_dir" >/dev/null
  else
    bt_process_local_scan "$scan_path" "$out_dir"
  fi
}

bt_main() {
  if [[ "${1:-}" == "__worker_video" ]]; then
    ensure_log_dir "$BT_SCRIPT_DIR"
    shift
    bt_worker_entry "$1" "$2"
    exit 0
  fi

  if [[ "${1:-}" != "clean" ]]; then
    ensure_log_dir "$BT_SCRIPT_DIR"
    setup_log_redirection "$BT_SCRIPT_DIR"
  fi

  [[ $# -gt 0 ]] || { bt_usage; exit 0; }

  case "$1" in
    scan)
      shift
      [[ $# -ge 1 ]] || bt_die "用法：bdtool scan <path> --out <dir>。示例：./bdtool.sh scan ./movie.mkv --out ./bdtool-output"
      bt_main_scan "$@"
      ;;
    doctor)
      shift
      bt_cmd_doctor
      ;;
    status)
      shift
      bt_cmd_status
      ;;
    install)
      shift
      bt_cmd_install
      ;;
    start)
      shift
      bt_cmd_start
      ;;
    clean)
      shift
      bt_cmd_clean
      ;;
    -h|--help|help)
      bt_usage
      ;;
    -v|--version|version)
      bt_cmd_version
      ;;
    *)
      if [[ -e "$1" ]]; then
        bt_main_scan "$1" "${@:2}"
      else
        bt_usage
        bt_die "未知命令或路径不存在：$1。示例：./bdtool.sh ./movie.mkv 或 ./bdtool.sh scan ./movie.mkv --out ./bdtool-output"
      fi
      ;;
  esac
}

bt_main "$@"
