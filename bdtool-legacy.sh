#!/usr/bin/env bash
set -Euo pipefail

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

find_app_root() {
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

SCRIPT_SOURCE="${BASH_SOURCE[0]:-${0:-}}"
if [[ -z "$SCRIPT_SOURCE" ]]; then
  SCRIPT_SOURCE="$(command -v bdtool 2>/dev/null || true)"
fi
[[ -n "$SCRIPT_SOURCE" ]] || SCRIPT_SOURCE="./bdtool"
SCRIPT_PATH="$(resolve_script_path "$SCRIPT_SOURCE")"
[[ -n "$SCRIPT_PATH" ]] || SCRIPT_PATH="$SCRIPT_SOURCE"
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" 2>/dev/null && pwd)"
ROOT_DIR=""
USER_HOME="${HOME:-}"
for candidate in "${PTBDTOOL_ROOT:-}" "${PTBD_INSTALL_ROOT:-}" "$SCRIPT_DIR" "/opt/PT-BDtool" "${USER_HOME:+$USER_HOME/.local/share/pt-bdtool/PT-BDtool-app}"; do
  [[ -n "$candidate" ]] || continue
  if [[ -f "$candidate/lib/ui.sh" ]]; then
    ROOT_DIR="$candidate"
    break
  fi
done
if [[ -z "$ROOT_DIR" || ! -f "$ROOT_DIR/lib/ui.sh" ]]; then
  echo "[ERROR] PT ReleaseKit runtime not found: lib/ui.sh" >&2
  echo "[ERROR] Current entry path: $SCRIPT_PATH" >&2
  echo "[HINT] You may be running an old copied wrapper under /usr/local/bin." >&2
  echo "[HINT] Reinstall from the PT ReleaseKit project root:" >&2
  echo "  cd /path/to/PT-ReleaseKit" >&2
  echo "  bash install.sh --offline" >&2
  echo "[HINT] Then verify:" >&2
  echo "  command -v bdtool && ls -l \"\$(command -v bdtool)\"" >&2
  echo "  bdtool --help" >&2
  exit 1
fi
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/ui.sh"
setup_bundle_runtime "$ROOT_DIR"

: "${LANG_CODE:=zh}"
: "${BDTOOL_NO_PROMPT:=0}"
: "${BDTOOL_DOWNLOAD_DIR:=}"
: "${BDTOOL_SCAN_FULL_ROOT:=/}"
: "${BDTOOL_DEBUG:=0}"
: "${BDTOOL_AUDIO_SPECTRUM_MODE:=single}"
: "${BDTOOL_AUDIO_SPECTRUM_BACKEND:=auto}"
: "${BDTOOL_AUDIO_SPECTRUM_SECONDS:=90}"
: "${BDTOOL_AUDIO_SPECTRUM_COMBINED_SECONDS:=0}"
: "${BDTOOL_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS:=12}"
: "${BDTOOL_AUDIO_SPECTRUM_SIZE:=1280x720}"
: "${BDTOOL_SCREENSHOT_CANDIDATES:=18}"
: "${BDTOOL_CLI_MEDIAINFO:=1}"
: "${BDTOOL_CLI_SHOTS:=1}"
: "${BDTOOL_CLI_OUT_DIR:=}"

ensure_output_root
MAX_SCAN_DISPLAY=10
BDTOOL_EXIT_REQUESTED=0

on_err() {
  local rc=$?
  if declare -F write_error_file >/dev/null 2>&1; then
    write_error_file "执行失败 rc=$rc" "请检查输入目录或依赖后重试。"
    screen_error "错误详情：${ERROR_FILE:-unknown}"
  else
    screen_error "执行失败 rc=$rc"
  fi
  exit "$rc"
}
trap on_err ERR

dbg() {
  [[ "$BDTOOL_DEBUG" == "1" ]] || return 0
  screen "[DEBUG] $*"
}

msg() {
  local key="$1"
  if [[ "$LANG_CODE" == "en" ]]; then
    case "$key" in
      main_title) echo "Main Menu" ;;
      main_1) echo "1) Scan" ;;
      main_2) echo "2) Switch language" ;;
      main_3) echo "3) Quit" ;;
      scan_title) echo "Scan" ;;
      scan_1) echo "1) Full scan" ;;
      scan_2) echo "2) Scan directory" ;;
      scan_0) echo "0) Back" ;;
      scan_warn_1) echo "Warning: full scan may take a long time." ;;
      scan_warn_2) echo "Warning: full scan may cause high IO." ;;
      scan_confirm) echo "Confirm full scan? 1) Continue 2) Back" ;;
      prompt) echo "Select" ;;
      invalid) echo "Invalid input" ;;
      dir_prompt) echo "Enter directory" ;;
      dir_invalid) echo "Directory invalid" ;;
      none) echo "No items found" ;;
      none_hint) echo "Supported scan targets: video/audio files (*.mkv/*.mp4/*.mp3/... ), *.iso, or BDMV folders." ;;
      dir_exec_file) echo "You entered an executable file path. Please enter a directory containing media files, e.g. /home/$USER/Downloads." ;;
      limit) echo "Only first 5 shown, others omitted" ;;
      pick) echo "Input command/indexes (n/p/g N for pages, indexes like 12 19 33, 0 back, r rescan)" ;;
      pick_limit) echo "Too many invalid attempts" ;;
      gen_done) echo "Generated: " ;;
      post_title) echo "Post process" ;;
      post_1) echo "1) Download result package" ;;
      post_2) echo "2) Cleanup generated files" ;;
      post_0) echo "0) Next item" ;;
      post_9) echo "9) Exit" ;;
      downloaded) echo "Downloaded: " ;;
      download_dir_prompt) echo "Download directory (empty = default)" ;;
      post_hint_next) echo "Press Enter for next item, input 1 to continue this menu" ;;
      cleaned) echo "Cleaned: " ;;
      clean_missing) echo "Path not found, skip cleanup: " ;;
      op_ok) echo "Operation succeeded" ;;
      op_fail) echo "Operation failed" ;;
      bye) echo "Bye" ;;
      type_video) echo "video" ;;
      type_audio) echo "audio" ;;
      type_audio_dir) echo "music directory" ;;
      type_bdmv) echo "bluray" ;;
      type_iso) echo "iso" ;;
      *) echo "$key" ;;
    esac
  else
    case "$key" in
      main_title) echo "主菜单" ;;
      main_1) echo "1) 扫描" ;;
      main_2) echo "2) 切换语言" ;;
      main_3) echo "3) 退出" ;;
      scan_title) echo "扫描" ;;
      scan_1) echo "1) 扫描全盘" ;;
      scan_2) echo "2) 扫描指定目录" ;;
      scan_0) echo "0) 返回" ;;
      scan_warn_1) echo "风险提示：全盘扫描耗时较长。" ;;
      scan_warn_2) echo "风险提示：全盘扫描会产生较高 IO。" ;;
      scan_confirm) echo "确认全盘扫描？1)继续 2)返回" ;;
      prompt) echo "请输入选项" ;;
      invalid) echo "输入无效" ;;
      dir_prompt) echo "请输入目录" ;;
      dir_invalid) echo "目录无效" ;;
      none) echo "未发现可处理条目" ;;
      none_hint) echo "可扫描类型：视频/音频文件（*.mkv/*.mp4/*.mp3 等）、ISO 文件（*.iso）、BDMV 目录。" ;;
      dir_exec_file) echo "你输入的是可执行文件路径，请输入包含媒体文件的目录，例如 /home/$USER/Downloads。" ;;
      limit) echo "仅显示前5条，其余已省略" ;;
      pick) echo "请输入指令/序号（n下一页 p上一页 g 页码；可多选如 12 19 33；0 返回；r 重扫）" ;;
      pick_limit) echo "输入错误过多，已返回" ;;
      gen_done) echo "生成完成：" ;;
      post_title) echo "结果处理" ;;
      post_1) echo "1) 下载结果包" ;;
      post_2) echo "2) 清理生成文件" ;;
      post_0) echo "0) 下一个条目" ;;
      post_9) echo "9) 退出" ;;
      downloaded) echo "已下载：" ;;
      download_dir_prompt) echo "下载目录（留空使用默认）" ;;
      post_hint_next) echo "回车进入下一个条目，输入 1 继续当前结果处理" ;;
      cleaned) echo "已清理：" ;;
      clean_missing) echo "路径不存在，跳过清理：" ;;
      op_ok) echo "操作成功" ;;
      op_fail) echo "操作失败" ;;
      bye) echo "已退出" ;;
      type_video) echo "视频" ;;
      type_audio) echo "音频" ;;
      type_audio_dir) echo "音乐目录" ;;
      type_bdmv) echo "原盘" ;;
      type_iso) echo "ISO" ;;
      *) echo "$key" ;;
    esac
  fi
}

read_line() {
  local prompt="$1"
  local __out="$2"
  local line=""

  if [[ "$BDTOOL_NO_PROMPT" == "1" ]]; then
    return 1
  fi

  if [[ -t 0 ]]; then
    read -r -p "$prompt" line || return 1
  elif [[ ! -t 0 ]]; then
    IFS= read -r line || return 1
  elif [[ -t 1 && -r /dev/tty ]]; then
    read -r -p "$prompt" line < /dev/tty || return 1
  else
    return 1
  fi

  printf -v "$__out" '%s' "$line"
  return 0
}

safe_name() {
  local n="$1"
  n="${n// /_}"
  n="$(echo "$n" | tr -cd '[:alnum:]_.-')"
  [[ -n "$n" ]] || n="unknown"
  echo "${n:0:64}"
}

unique_dir() {
  local info_root="$1"
  local base="$2"
  local out="$info_root/$base"
  local idx=2
  while [[ -e "$out" ]]; do
    out="$info_root/${base}_$idx"
    idx=$((idx + 1))
  done
  echo "$out"
}

resolve_fixed_output_root() {
  local type="$1"
  local src="$2"
  if [[ -n "${BDTOOL_CLI_OUT_DIR:-}" ]]; then
    printf "%s" "$BDTOOL_CLI_OUT_DIR/PT-BDtool/信息"
    return 0
  fi
  resolve_source_output_layout "$type" "$src" || return 1
  resolve_source_job_dir
}

create_placeholder_png() {
  local dst="$1"
  if command -v base64 >/dev/null 2>&1; then
    printf 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=' | base64 -d > "$dst" 2>/dev/null || : > "$dst"
  else
    : > "$dst"
  fi
}

ensure_six_png() {
  local dir="$1"
  local last=""
  local i
  for i in 1 2 3 4 5 6; do
    if [[ -f "$dir/$i.png" ]]; then
      last="$dir/$i.png"
      break
    fi
  done
  for i in 1 2 3 4 5 6; do
    if [[ ! -f "$dir/$i.png" ]]; then
      if [[ -n "$last" && -f "$last" ]]; then
        cp -f "$last" "$dir/$i.png" 2>/dev/null || create_placeholder_png "$dir/$i.png"
      else
        create_placeholder_png "$dir/$i.png"
        last="$dir/$i.png"
      fi
    fi
  done
}

screenshot_candidate_count() {
  local n="${BDTOOL_SCREENSHOT_CANDIDATES:-18}"
  [[ "$n" =~ ^[1-9][0-9]*$ ]] || n=18
  (( n < 6 )) && n=6
  (( n > 48 )) && n=48
  printf "%s" "$n"
}

screenshot_frame_usable() {
  local image="$1"
  local stats yavg ymin ymax yrange
  [[ -s "$image" ]] || return 1
  stats="$(ffmpeg -nostdin -hide_banner -loglevel error -i "$image" -vf signalstats,metadata=print:file=- -frames:v 1 -f null - 2>/dev/null || true)"
  yavg="$(awk -F= '/lavfi.signalstats.YAVG=/{printf "%d", $2 + 0; exit}' <<< "$stats")"
  ymin="$(awk -F= '/lavfi.signalstats.YMIN=/{printf "%d", $2 + 0; exit}' <<< "$stats")"
  ymax="$(awk -F= '/lavfi.signalstats.YMAX=/{printf "%d", $2 + 0; exit}' <<< "$stats")"
  [[ "$yavg" =~ ^[0-9]+$ && "$ymin" =~ ^[0-9]+$ && "$ymax" =~ ^[0-9]+$ ]] || return 1
  yrange=$((ymax - ymin))
  (( yavg >= 18 && yavg <= 238 && yrange >= 20 ))
}

screenshot_array_contains() {
  local needle="$1"
  shift || true
  local item
  for item in "$@"; do
    [[ "$item" == "$needle" ]] && return 0
  done
  return 1
}

screenshot_copy_selection() {
  local out="$1"
  local valid_name="$2"
  local fallback_name="$3"
  local -n valid_ref="$valid_name"
  local -n fallback_ref="$fallback_name"
  local -a chosen=()
  local total idx i item copied

  for i in 1 2 3 4 5 6; do
    rm -f -- "$out/$i.png"
  done

  total="${#valid_ref[@]}"
  if (( total > 0 )); then
    for i in 1 2 3 4 5 6; do
      if (( total == 1 )); then
        idx=0
      else
        idx=$(( (total - 1) * (i - 1) / 5 ))
      fi
      chosen+=("${valid_ref[$idx]}")
    done
  else
    for item in "${fallback_ref[@]}"; do
      screenshot_array_contains "$item" "${chosen[@]}" && continue
      chosen+=("$item")
      (( ${#chosen[@]} >= 6 )) && break
    done
  fi

  copied=0
  for item in "${chosen[@]}"; do
    (( copied >= 6 )) && break
    (( copied += 1 ))
    cp -f -- "$item" "$out/$copied.png" || return 1
  done
  (( copied > 0 ))
}

screenshot_progress() {
  local base="${1:-0}"
  local span="${2:-0}"
  local done_count="${3:-0}"
  local total="${4:-1}"
  local label="${5:-截图生成}"
  local pct
  [[ "$base" =~ ^[0-9]+$ && "$span" =~ ^[0-9]+$ && "$done_count" =~ ^[0-9]+$ && "$total" =~ ^[1-9][0-9]*$ ]] || return 0
  (( span > 0 )) || return 0
  pct=$((base + done_count * span / total))
  (( pct > 99 )) && pct=99
  screen "生成进度: ${pct}% (${label})"
}

make_quality_screenshots() {
  local src="$1"
  local out="$2"
  local progress_base="${3:-0}"
  local progress_span="${4:-0}"
  local duration dur_int candidate_count tmp_dir i permille sec candidate
  local rc=1
  local -a valid_candidates=()
  local -a fallback_candidates=()
  local start_permille=100
  local end_permille=900
  local span_permille=800

  duration="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$src" 2>/dev/null || true)"
  dur_int="${duration%.*}"
  [[ "$dur_int" =~ ^[0-9]+$ ]] || dur_int=60
  candidate_count="$(screenshot_candidate_count)"
  tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/ptbd-shots.XXXXXX")" || return 1

  # Prefer mid-content frames (10%-90%) to reduce title/credit black frames.
  for ((i = 1; i <= candidate_count; i++)); do
    if (( dur_int < 2 )); then
      sec=0
    else
      if (( candidate_count <= 1 )); then
        permille=$(( (start_permille + end_permille) / 2 ))
      else
        permille=$((start_permille + (i - 1) * span_permille / (candidate_count - 1)))
      fi
      sec=$((dur_int * permille / 1000))
      (( sec < 1 )) && sec=1
      (( sec >= dur_int )) && sec=$((dur_int - 1))
    fi
    candidate="$tmp_dir/$i.png"
    if ffmpeg -nostdin -hide_banner -loglevel error -ss "$sec" -i "$src" -frames:v 1 -y "$candidate" >/dev/null 2>&1; then
      if [[ -s "$candidate" ]]; then
        fallback_candidates+=("$candidate")
        if screenshot_frame_usable "$candidate"; then
          valid_candidates+=("$candidate")
        fi
      fi
    fi
    if (( i == 1 || i == candidate_count || i % 3 == 0 )); then
      screenshot_progress "$progress_base" "$progress_span" "$i" "$candidate_count" "截图候选生成 ${i}/${candidate_count}"
    fi
  done

  screen "截图筛选完成：有效 ${#valid_candidates[@]}/${#fallback_candidates[@]}，输出 6 张"
  if (( ${#valid_candidates[@]} < 6 )); then
    screen "质量状态：screenshots=degraded（有效帧不足 6 张，使用可用帧补齐）"
  else
    screen "质量状态：screenshots=full"
  fi
  if screenshot_copy_selection "$out" valid_candidates fallback_candidates; then
    rc=0
  fi
  rm -rf -- "$tmp_dir"
  return "$rc"
}

finalize_video_output() {
  local dir="$1"
  local keep_re='^[1-6]\.png$|^mediainfo\.txt$'
  local file cnt i
  for i in 1 2 3 4 5 6; do
    [[ -s "$dir/$i.png" ]] || return 1
  done
  [[ -s "$dir/mediainfo.txt" ]] || return 1
  for file in "$dir"/*; do
    [[ -f "$file" ]] || continue
    if [[ ! "$(basename "$file")" =~ $keep_re ]]; then
      rm -f -- "$file"
    fi
  done
  cnt="$(find "$dir" -maxdepth 1 -type f | wc -l | tr -d ' ')"
  [[ "$cnt" == "7" ]] || return 1
  return 0
}

finalize_video_selected_output() {
  local dir="$1"
  local want_mediainfo="${2:-1}"
  local want_shots="${3:-1}"
  local keep_re=""
  local file i

  if [[ "$want_mediainfo" == "1" && "$want_shots" == "1" ]]; then
    finalize_video_output "$dir"
    return $?
  fi
  if [[ "$want_mediainfo" != "1" && "$want_shots" != "1" ]]; then
    [[ -s "$dir/README.txt" ]] || return 1
    keep_re='^README\.txt$'
  elif [[ "$want_mediainfo" == "1" ]]; then
    [[ -s "$dir/mediainfo.txt" ]] || return 1
    keep_re='^mediainfo\.txt$'
  else
    for i in 1 2 3 4 5 6; do
      [[ -s "$dir/$i.png" ]] || return 1
    done
    keep_re='^[1-6]\.png$'
  fi

  for file in "$dir"/*; do
    [[ -f "$file" ]] || continue
    if [[ ! "$(basename "$file")" =~ $keep_re ]]; then
      rm -f -- "$file"
    fi
  done
  return 0
}

is_video() {
  local lower="${1,,}"
  case "$lower" in
    *.d.ts) return 1 ;;
    *.ts)
      if command -v ffprobe >/dev/null 2>&1; then
        ffprobe -v error -select_streams v:0 -show_entries stream=codec_type -of csv=p=0 "$1" 2>/dev/null | grep -qx 'video'
        return $?
      fi
      return 1
      ;;
    *.mkv|*.mp4|*.avi|*.mov|*.m2ts|*.wmv|*.webm|*.mpg|*.mpeg) return 0 ;;
    *) return 1 ;;
  esac
}

is_audio() {
  case "${1,,}" in
    *.mp3|*.flac|*.wav|*.m4a|*.aac|*.ogg|*.opus) return 0 ;;
    *) return 1 ;;
  esac
}

normalize_audio_spectrum_mode() {
  case "${1,,}" in
    single|combined) printf '%s' "${1,,}" ;;
    *) return 1 ;;
  esac
}

audio_spectrum_tail_filter() {
  local seconds="${1:-0}"
  if [[ "$seconds" =~ ^[1-9][0-9]*$ ]]; then
    printf 'atrim=end=%s,showspectrumpic=s=%s:legend=disabled' "$seconds" "$BDTOOL_AUDIO_SPECTRUM_SIZE"
  else
    printf 'showspectrumpic=s=%s:legend=disabled' "$BDTOOL_AUDIO_SPECTRUM_SIZE"
  fi
}

audio_spectrum_size_part() {
  local size="$1"
  local part="$2"
  local width="${size%x*}"
  local height="${size#*x}"
  [[ "$width" =~ ^[1-9][0-9]*$ ]] || width=1280
  [[ "$height" =~ ^[1-9][0-9]*$ ]] || height=720
  if [[ "$part" == "height" ]]; then
    printf '%s' "$height"
  else
    printf '%s' "$width"
  fi
}

format_spectrum_duration() {
  local seconds="${1:-0}"
  local hours=0
  local minutes=0
  local remain=0
  [[ "$seconds" =~ ^[1-9][0-9]*$ ]] || seconds=12
  hours=$((seconds / 3600))
  minutes=$(((seconds % 3600) / 60))
  remain=$((seconds % 60))
  if (( hours > 0 )); then
    printf '%d:%02d:%02d' "$hours" "$minutes" "$remain"
  else
    printf '%d:%02d' "$minutes" "$remain"
  fi
}

find_sox_spectrum_binary() {
  local backend="${BDTOOL_AUDIO_SPECTRUM_BACKEND:-auto}"
  backend="${backend,,}"
  [[ "$backend" == "ffmpeg" ]] && return 1
  if command -v sox_ng >/dev/null 2>&1; then
    command -v sox_ng
    return 0
  fi
  if command -v sox >/dev/null 2>&1; then
    command -v sox
    return 0
  fi
  return 1
}

make_audio_spectrum_sox() {
  local audio="$1"
  local image="$2"
  local size="$3"
  local seconds="${4:-0}"
  local title="${5:-}"
  local sox_bin=""
  local width=""
  local height=""
  local -a duration_args=()
  local -a title_args=()

  sox_bin="$(find_sox_spectrum_binary)" || return 1
  width="$(audio_spectrum_size_part "$size" width)"
  height="$(audio_spectrum_size_part "$size" height)"
  if [[ "$seconds" =~ ^[1-9][0-9]*$ ]]; then
    duration_args=(-S 0 -d "$(format_spectrum_duration "$seconds")")
  fi
  if [[ -n "$title" ]]; then
    title_args=(-t "$title" -c "PT ReleaseKit")
  fi
  "$sox_bin" "$audio" -n remix 1 spectrogram \
    -x "$width" -y "$height" -z 120 -w Kaiser \
    "${duration_args[@]}" "${title_args[@]}" -o "$image" >/dev/null 2>&1
}

make_audio_spectrum_ffmpeg() {
  local audio="$1"
  local image="$2"
  local size="$3"
  local seconds="${4:-0}"
  local filter_tail=""
  if [[ "$seconds" =~ ^[1-9][0-9]*$ ]]; then
    filter_tail="atrim=end=${seconds},showspectrumpic=s=${size}:legend=disabled"
  else
    filter_tail="showspectrumpic=s=${size}:legend=disabled"
  fi
  require_runtime_cmd ffmpeg || return 1
  ffmpeg -nostdin -hide_banner -loglevel error -y -i "$audio" \
    -filter_complex "[0:a]aformat=channel_layouts=mono,${filter_tail}" \
    -frames:v 1 "$image" >/dev/null 2>&1
}

make_audio_spectrum_bounded() {
  local audio="$1"
  local image="$2"
  local size="$3"
  local seconds="${4:-0}"
  local title="${5:-}"
  local backend="${BDTOOL_AUDIO_SPECTRUM_BACKEND:-auto}"
  backend="${backend,,}"
  case "$backend" in
    auto|"")
      make_audio_spectrum_sox "$audio" "$image" "$size" "$seconds" "$title" && return 0
      make_audio_spectrum_ffmpeg "$audio" "$image" "$size" "$seconds"
      ;;
    sox|sox_ng)
      make_audio_spectrum_sox "$audio" "$image" "$size" "$seconds" "$title"
      ;;
    ffmpeg)
      make_audio_spectrum_ffmpeg "$audio" "$image" "$size" "$seconds"
      ;;
    *)
      make_audio_spectrum_ffmpeg "$audio" "$image" "$size" "$seconds"
      ;;
  esac
}

find_audio_files_in_dir() {
  local dir="$1"
  find "$dir" -maxdepth 1 -type f \( \
    -iname '*.mp3' -o -iname '*.flac' -o -iname '*.wav' -o -iname '*.m4a' -o \
    -iname '*.aac' -o -iname '*.ogg' -o -iname '*.opus' \
  \) -print0 2>/dev/null | LC_ALL=C sort -z
}

is_audio_dir() {
  local dir="$1"
  local audio=""
  local count=0
  [[ -d "$dir" ]] || return 1
  while IFS= read -r -d '' audio; do
    count=$((count + 1))
    (( count >= 2 )) && return 0
  done < <(find_audio_files_in_dir "$dir")
  return 1
}

emit_audio_dir_candidates() {
  local scan_root="$1"
  local audio=""
  local dir=""
  local -A dir_counts=()

  while IFS= read -r -d '' audio; do
    dir="$(dirname "$audio")"
    dir_counts["$dir"]=$(( ${dir_counts["$dir"]:-0} + 1 ))
  done < <(
    find "$scan_root" \( -type d \( \
      -name proc -o -name sys -o -name dev -o -name run -o -name tmp -o \
      -name node_modules -o -name .git -o -name .svn -o -name .cache -o -name .npm -o -name .pnpm-store \
    \) -prune \) -o \
    \( -type f \( -iname '*.mp3' -o -iname '*.flac' -o -iname '*.wav' -o -iname '*.m4a' -o -iname '*.aac' -o -iname '*.ogg' -o -iname '*.opus' \) -print0 \) 2>/dev/null
  )

  for dir in "${!dir_counts[@]}"; do
    if (( dir_counts["$dir"] >= 2 )); then
      printf '%s\n' "$dir"
    fi
  done | LC_ALL=C sort
}

write_audio_mediainfo_report() {
  local out="$1"
  shift
  local audio=""
  local hard_fail=0
  : > "$out/mediainfo.txt"
  for audio in "$@"; do
    {
      printf '===== %s =====\n' "$(basename "$audio")"
      if require_runtime_cmd mediainfo; then
        mediainfo "$audio" || hard_fail=1
      else
        printf '未安装 mediainfo，无法生成有效信息。\n'
        hard_fail=1
      fi
      printf '\n'
    } >> "$out/mediainfo.txt" 2>/dev/null
  done
  return "$hard_fail"
}

make_audio_spectrum_single() {
  local audio="$1"
  local image="$2"
  make_audio_spectrum_bounded "$audio" "$image" "$BDTOOL_AUDIO_SPECTRUM_SIZE" "$BDTOOL_AUDIO_SPECTRUM_SECONDS" "$(basename "$audio")"
}

make_audio_spectrum_combined() {
  local image="$1"
  shift
  local count="$#"
  local sample_seconds="${BDTOOL_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS:-12}"
  local spectrum_script="$ROOT_DIR/scripts/audio-spectrum.py"

  (( count > 0 )) || return 1
  [[ "$sample_seconds" =~ ^[0-9]+$ ]] || sample_seconds=12
  require_runtime_cmd ffmpeg || return 1
  command -v python3 >/dev/null 2>&1 || return 1
  [[ -f "$spectrum_script" ]] || return 1
  python3 "$spectrum_script" --output "$image" --size "$BDTOOL_AUDIO_SPECTRUM_SIZE" --seconds "$sample_seconds" "$@"
}

finalize_audio_dir_single_output() {
  local dir="$1"
  local track_dir=""
  local count=0
  while IFS= read -r track_dir; do
    [[ -d "$track_dir" ]] || continue
    [[ -s "$track_dir/频谱图.png" ]] || return 1
    [[ -s "$track_dir/mediainfo.txt" ]] || return 1
    count=$((count + 1))
  done < <(find "$dir" -mindepth 1 -maxdepth 1 -type d | LC_ALL=C sort)
  (( count > 0 ))
}

bdmv_root_from_stream_file() {
  local path="${1:-}"
  local marker="/BDMV/STREAM/"
  local prefix=""
  [[ -n "$path" && "$path" == *"$marker"* ]] || return 1
  prefix="${path%%"$marker"*}"
  [[ -n "$prefix" && -d "$prefix/BDMV/PLAYLIST" ]] || return 1
  printf "%s" "$prefix"
}

finalize_audio_output() {
  local dir="$1"
  local keep_re='^频谱图\.png$|^mediainfo\.txt$'
  local file cnt
  [[ -s "$dir/频谱图.png" ]] || return 1
  [[ -s "$dir/mediainfo.txt" ]] || return 1
  for file in "$dir"/*; do
    [[ -f "$file" ]] || continue
    if [[ ! "$(basename "$file")" =~ $keep_re ]]; then
      rm -f -- "$file"
    fi
  done
  cnt="$(find "$dir" -maxdepth 1 -type f | wc -l | tr -d ' ')"
  [[ "$cnt" == "2" ]] || return 1
  return 0
}

pick_disc_probe_video() {
  local src="$1"
  local candidate=""
  if [[ -d "$src/BDMV/STREAM" ]]; then
    candidate="$(find "$src/BDMV/STREAM" -type f -iname '*.m2ts' -printf '%s %p\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2- || true)"
  elif [[ -d "$src/STREAM" ]]; then
    candidate="$(find "$src/STREAM" -type f -iname '*.m2ts' -printf '%s %p\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2- || true)"
  elif [[ -f "$src" ]]; then
    candidate="$src"
  fi
  printf "%s" "$candidate"
}

make_disc_screenshots() {
  local src="$1"
  local out="$2"
  local progress_base="${3:-0}"
  local progress_step="${4:-0}"
  local probe progress_span
  probe="$(pick_disc_probe_video "$src")"
  if [[ -n "$probe" ]] && require_runtime_cmd ffmpeg && require_runtime_cmd ffprobe; then
    progress_span=0
    if [[ "$progress_step" =~ ^[0-9]+$ ]]; then
      progress_span=$((progress_step * 6))
    fi
    make_quality_screenshots "$probe" "$out" "$progress_base" "$progress_span" || true
  fi
  ensure_six_png "$out"
}

finalize_disc_output() {
  local dir="$1"
  local keep_re='^[1-6]\.png$|^BDInfo\.txt$'
  local file cnt i
  bdinfo_report_valid "$dir/BDInfo.txt" || return 1
  for i in 1 2 3 4 5 6; do
    [[ -s "$dir/$i.png" ]] || return 1
  done
  while IFS= read -r file; do
    [[ -f "$file" ]] || continue
    if [[ ! "$(basename "$file")" =~ $keep_re ]]; then
      rm -f -- "$file"
    fi
  done < <(find "$dir" -maxdepth 1 -type f)
  cnt="$(find "$dir" -maxdepth 1 -type f | wc -l | tr -d ' ')"
  [[ "$cnt" == "7" ]] || return 1
  return 0
}

collect_results() {
  local target="$1"
  local full="$2"
  local p type normalized root tmp_candidates total processed pct last_pct

  normalize_scan_root() {
    local in="${1:-/}"
    [[ -n "$in" ]] || in="/"
    while [[ "$in" != "/" && "$in" == */ ]]; do
      in="${in%/}"
    done
    printf "%s" "$in"
  }

  collect_scan_roots() {
    local full_mode="$1"
    local target_root="$2"
    local raw="${BDTOOL_SCAN_INCLUDE_ROOTS:-}"
    local lines="${BDTOOL_SCAN_INCLUDE_ROOTS_LINES:-}"
    local item=""
    local -a roots=()
    local explicit_roots=0

    if [[ -n "$lines" ]]; then
      explicit_roots=1
      while IFS= read -r item; do
        [[ -n "$item" ]] || continue
        item="$(normalize_scan_root "$item")"
        [[ -d "$item" ]] || continue
        roots+=("$item")
      done <<< "$lines"
    elif [[ -n "$raw" ]]; then
      explicit_roots=1
      raw="${raw//,/ }"
      for item in $raw; do
        item="$(normalize_scan_root "$item")"
        [[ -d "$item" ]] || continue
        roots+=("$item")
      done
    elif [[ "$full_mode" == "1" ]]; then
      if [[ "$target_root" != "/" ]]; then
        roots+=("$target_root")
      elif [[ -n "${SSH_CONNECTION:-}" ]]; then
        for item in /home /root /data /mnt /media /srv; do
          [[ -d "$item" ]] && roots+=("$item")
        done
      else
        roots+=("/")
      fi
    else
      roots+=("$target_root")
    fi

    if [[ "${#roots[@]}" -eq 0 && "$explicit_roots" == "0" ]]; then
      roots+=("$target_root")
    fi

    printf '%s\n' "${roots[@]}"
  }

  scan_should_prune_dir() {
    local path="$1"
    local base=""
    local extra_raw="${BDTOOL_SCAN_EXCLUDE_ROOTS:-}"
    local extra_lines="${BDTOOL_SCAN_EXCLUDE_ROOTS_LINES:-}"
    local item=""

    base="$(basename "$path")"
    case "$base" in
      proc|sys|dev|run|tmp|var|node_modules|.git|.svn|.cache|.npm|.pnpm-store)
        return 0
        ;;
    esac

    case "$path" in
      /proc|/proc/*|/sys|/sys/*|/dev|/dev/*|/run|/run/*|/tmp|/tmp/*|/var/tmp|/var/tmp/*|/var/cache|/var/cache/*|/var/lib/docker|/var/lib/docker/*|/var/lib/containerd|/var/lib/containerd/*|/snap|/snap/*|/nix|/nix/*)
        return 0
        ;;
    esac

    if [[ -n "$extra_lines" ]]; then
      while IFS= read -r item; do
        [[ -n "$item" ]] || continue
        item="$(normalize_scan_root "$item")"
        case "$path" in
          "$item"|"$item"/*) return 0 ;;
        esac
      done <<< "$extra_lines"
    elif [[ -n "$extra_raw" ]]; then
      extra_raw="${extra_raw//,/ }"
      for item in $extra_raw; do
        item="$(normalize_scan_root "$item")"
        [[ -n "$item" ]] || continue
        case "$path" in
          "$item"|"$item"/*) return 0 ;;
        esac
      done
    fi

    return 1
  }

  emit_scan_candidates() {
    local scan_root="$1"
    find "$scan_root" \( -type d \( \
      -name proc -o -name sys -o -name dev -o -name run -o -name tmp -o \
      -name node_modules -o -name .git -o -name .svn -o -name .cache -o -name .npm -o -name .pnpm-store \
    \) -prune \) -o \
    \( -type f \( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.avi' -o -iname '*.mov' -o -iname '*.ts' -o -iname '*.m2ts' -o -iname '*.wmv' -o -iname '*.webm' -o -iname '*.mpg' -o -iname '*.mpeg' -o -iname '*.iso' -o -iname '*.mp3' -o -iname '*.flac' -o -iname '*.wav' -o -iname '*.m4a' -o -iname '*.aac' -o -iname '*.ogg' -o -iname '*.opus' \) ! -iname '*.d.ts' -print -o -type d -name BDMV -print \) 2>/dev/null
    emit_audio_dir_candidates "$scan_root"
  }

  SCAN_TYPES=()
  SCAN_ITEMS=()
  unset SCAN_SEEN
  declare -gA SCAN_SEEN=()

  if [[ "$full" == "1" ]]; then
    root="$(normalize_scan_root "${BDTOOL_SCAN_FULL_ROOT:-/}")"
    local -a scan_roots=()
    while IFS= read -r p; do
      [[ -n "$p" ]] && scan_roots+=("$p")
    done < <(collect_scan_roots "1" "$root")
    screen "扫描中：全盘扫描（$root）"
    if [[ -n "${BDTOOL_SCAN_INCLUDE_ROOTS:-}" ]]; then
      screen "扫描白名单：${BDTOOL_SCAN_INCLUDE_ROOTS}"
    elif [[ "$root" == "/" && -n "${SSH_CONNECTION:-}" ]]; then
      screen "扫描白名单：/home /root /data /mnt /media /srv"
    fi
    screen "扫描进度: 0%"
    tmp_candidates="$(mktemp)"
    : > "$tmp_candidates"
    for p in "${scan_roots[@]}"; do
      emit_scan_candidates "$p" >> "$tmp_candidates" || true
    done
    total="$(wc -l < "$tmp_candidates" | tr -d ' ')"
    processed=0
    last_pct=0
    while IFS= read -r p; do
      [[ -n "$p" ]] || continue
      processed=$((processed + 1))
      if [[ "${total:-0}" -gt 0 ]]; then
        pct=$((processed * 100 / total))
        if [[ "$pct" -ge 100 ]]; then pct=100; fi
        if (( pct >= last_pct + 10 || pct == 100 )); then
          screen "扫描进度: ${pct}%"
          last_pct="$pct"
        fi
      fi
      if scan_should_prune_dir "$p"; then
        continue
      fi
      if ! resolve_scan_candidate "$p" type normalized; then
        continue
      fi
      if [[ -z "${SCAN_SEEN[$normalized]:-}" ]]; then
        SCAN_SEEN["$normalized"]=1
        SCAN_TYPES+=("$type")
        SCAN_ITEMS+=("$normalized")
      fi
    done < "$tmp_candidates"
    rm -f "$tmp_candidates"
  else
    root="$(normalize_scan_root "$target")"
    screen "扫描中：目录扫描（$root）"
    screen "扫描进度: 0%"
    tmp_candidates="$(mktemp)"
    emit_scan_candidates "$root" > "$tmp_candidates" || true
    total="$(wc -l < "$tmp_candidates" | tr -d ' ')"
    processed=0
    last_pct=0
    while IFS= read -r p; do
      [[ -n "$p" ]] || continue
      processed=$((processed + 1))
      if [[ "${total:-0}" -gt 0 ]]; then
        pct=$((processed * 100 / total))
        if [[ "$pct" -ge 100 ]]; then pct=100; fi
        if (( pct >= last_pct + 10 || pct == 100 )); then
          screen "扫描进度: ${pct}%"
          last_pct="$pct"
        fi
      fi
      if scan_should_prune_dir "$p"; then
        continue
      fi
      if ! resolve_scan_candidate "$p" type normalized; then
        continue
      fi
      if [[ -z "${SCAN_SEEN[$normalized]:-}" ]]; then
        SCAN_SEEN["$normalized"]=1
        SCAN_TYPES+=("$type")
        SCAN_ITEMS+=("$normalized")
      fi
    done < "$tmp_candidates"
    rm -f "$tmp_candidates"
  fi
  if (( last_pct < 100 )); then
    screen "扫描进度: 100%"
  fi
  screen "扫描完成：共发现 ${#SCAN_ITEMS[@]} 个候选"
}

show_result_item() {
  local idx="$1"
  local label=""
  case "${SCAN_TYPES[idx-1]}" in
    VIDEO) label="$(msg type_video)" ;;
    AUDIO) label="$(msg type_audio)" ;;
    AUDIO_DIR) label="$(msg type_audio_dir)" ;;
    BDMV) label="$(msg type_bdmv)" ;;
    ISO) label="$(msg type_iso)" ;;
    *) label="${SCAN_TYPES[idx-1]}" ;;
  esac
  printf "%d) [%s] %s\n" "$idx" "$label" "${SCAN_ITEMS[idx-1]}"
}

resolve_scan_candidate() {
  local raw_path="${1:-}"
  local resolved_type_var="${2:-}"
  local resolved_path_var="${3:-}"
  local candidate_type=""
  local resolved_path=""
  local bdmv_root=""

  [[ -n "$raw_path" ]] || return 1

  if [[ "${raw_path##*/}" == "BDMV" && -d "$raw_path" ]]; then
    candidate_type="BDMV"
    resolved_path="$(dirname "$raw_path")"
  elif [[ "${raw_path,,}" == *.iso && -f "$raw_path" ]]; then
    candidate_type="ISO"
    resolved_path="$raw_path"
  elif [[ -f "$raw_path" ]] && is_video "$raw_path"; then
    if bdmv_root="$(bdmv_root_from_stream_file "$raw_path")"; then
      candidate_type="BDMV"
      resolved_path="$bdmv_root"
    else
      candidate_type="VIDEO"
      resolved_path="$raw_path"
    fi
  elif [[ -f "$raw_path" ]] && is_audio "$raw_path"; then
    candidate_type="AUDIO"
    resolved_path="$raw_path"
  elif [[ -d "$raw_path" && -d "$raw_path/BDMV" ]]; then
    candidate_type="BDMV"
    resolved_path="$raw_path"
  elif [[ -d "$raw_path" ]] && is_audio_dir "$raw_path"; then
    candidate_type="AUDIO_DIR"
    resolved_path="$raw_path"
  else
    return 1
  fi

  printf -v "$resolved_type_var" '%s' "$candidate_type"
  printf -v "$resolved_path_var" '%s' "$resolved_path"
  return 0
}

scan_total_pages() {
  local total="${#SCAN_ITEMS[@]}"
  local page_size="$1"
  if (( total <= 0 )); then
    printf "0"
    return 0
  fi
  printf "%d" $(( (total + page_size - 1) / page_size ))
}

show_results_page() {
  local page="$1"
  local page_size="$2"
  local total="${#SCAN_ITEMS[@]}"
  local total_pages start_idx end_idx i

  if (( total == 0 )); then
    screen "$(msg none)"
    screen "$(msg none_hint)"
    return 1
  fi

  total_pages="$(scan_total_pages "$page_size")"
  (( page < 1 )) && page=1
  (( page > total_pages )) && page="$total_pages"
  start_idx=$(( (page - 1) * page_size + 1 ))
  end_idx=$(( start_idx + page_size - 1 ))
  (( end_idx > total )) && end_idx="$total"

  if [[ "$LANG_CODE" == "en" ]]; then
    screen "Candidates: page ${page}/${total_pages}, items ${start_idx}-${end_idx} / ${total}"
    if (( total_pages > 1 )); then
      screen "Browse: n=next page, p=prev page, g N=go page"
    fi
    screen "Select by global index, supports multi-select: e.g. 12 19 33"
    screen "Input r to rescan, 0 to back"
  else
    screen "候选列表：第 ${page}/${total_pages} 页，条目 ${start_idx}-${end_idx} / ${total}"
    if (( total_pages > 1 )); then
      screen "浏览命令：n 下一页，p 上一页，g 页码（如 g 3）"
    fi
    screen "可按全局序号选择，支持多选：如 12 19 33"
    screen "输入 r 重新扫描，输入 0 返回"
  fi

  for ((i=start_idx; i<=end_idx; i++)); do
    show_result_item "$i"
  done
  return 0
}

scan_type_label() {
  local type="${1:-}"
  case "$type" in
    VIDEO) [[ "$LANG_CODE" == "en" ]] && echo "video" || echo "视频" ;;
    AUDIO) [[ "$LANG_CODE" == "en" ]] && echo "audio" || echo "音频" ;;
    AUDIO_DIR) [[ "$LANG_CODE" == "en" ]] && echo "music directory" || echo "音乐目录" ;;
    BDMV) [[ "$LANG_CODE" == "en" ]] && echo "bluray" || echo "原盘" ;;
    ISO) [[ "$LANG_CODE" == "en" ]] && echo "iso" || echo "镜像" ;;
    *) printf '%s' "$type" ;;
  esac
}

scan_results_json() {
  local target="${1:-/}"
  local full="${2:-1}"
  local tmp_json=""
  local i=0

  collect_results "$target" "$full" >/dev/null
  tmp_json="$(mktemp)"
  : > "$tmp_json"
  for i in "${!SCAN_ITEMS[@]}"; do
    printf '%s\t%s\t%s\t%s\n' \
      "$((i + 1))" \
      "${SCAN_TYPES[$i]}" \
      "$(scan_type_label "${SCAN_TYPES[$i]}")" \
      "${SCAN_ITEMS[$i]}" >> "$tmp_json"
  done

  python3 - "$tmp_json" <<'PY'
import json
import sys

path = sys.argv[1]
items = []
with open(path, "r", encoding="utf-8") as handle:
    for raw in handle:
        raw = raw.rstrip("\n")
        if not raw:
            continue
        index, item_type, type_label, item_path = raw.split("\t", 3)
        items.append(
            {
                "index": int(index),
                "type": item_type,
                "type_label": type_label,
                "path": item_path,
            }
        )
print(json.dumps({"items": items}, ensure_ascii=False, indent=2))
PY
  rm -f "$tmp_json"
}

handle_scan_json_command() {
  local target="/"
  local full="1"

  shift || true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --full)
        full="1"
        target="${BDTOOL_SCAN_FULL_ROOT:-/}"
        shift
        ;;
      --dir)
        target="${2:-}"
        full="0"
        shift 2
        ;;
      --dir=*)
        target="${1#*=}"
        full="0"
        shift
        ;;
      --lang)
        LANG_CODE="${2:-$LANG_CODE}"
        shift 2
        ;;
      --lang=*)
        LANG_CODE="${1#*=}"
        shift
        ;;
      --help|-h)
        if [[ "$LANG_CODE" == "en" ]]; then
          echo "Usage: bdtool scan-json [--full] [--dir PATH] [--lang zh|en]"
        else
          echo "用法: bdtool scan-json [--full] [--dir 路径] [--lang zh|en]"
        fi
        return 0
        ;;
      *)
        screen_error "未知参数：$1"
        return 2
        ;;
    esac
  done

  if [[ "$full" == "0" ]]; then
    [[ -n "$target" && -d "$target" ]] || {
      screen_error "扫描目录无效：${target:-<empty>}"
      return 2
    }
  fi

  scan_results_json "$target" "$full"
  return 0
}

handle_generate_path_command() {
  local target=""
  local item_type=""
  local item_path=""
  local spectrum_mode="${BDTOOL_AUDIO_SPECTRUM_MODE:-single}"

  shift || true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --path)
        target="${2:-}"
        shift 2
        ;;
      --path=*)
        target="${1#*=}"
        shift
        ;;
      --lang)
        LANG_CODE="${2:-$LANG_CODE}"
        shift 2
        ;;
      --lang=*)
        LANG_CODE="${1#*=}"
        shift
        ;;
      --audio-spectrum)
        spectrum_mode="${2:-}"
        shift 2
        ;;
      --audio-spectrum=*)
        spectrum_mode="${1#*=}"
        shift
        ;;
      --help|-h)
        if [[ "$LANG_CODE" == "en" ]]; then
          echo "Usage: bdtool generate-path --path TARGET [--lang zh|en] [--audio-spectrum single|combined]"
        else
          echo "用法: bdtool generate-path --path 目标路径 [--lang zh|en] [--audio-spectrum single|combined]"
        fi
        return 0
        ;;
      *)
        if [[ -z "$target" ]]; then
          target="$1"
          shift
        else
          screen_error "未知参数：$1"
          return 2
        fi
        ;;
    esac
  done

  [[ -n "$target" ]] || {
    screen_error "缺少目标路径：请传入 --path"
    return 2
  }

  resolve_scan_candidate "$target" item_type item_path || {
    screen_error "不支持的目标路径：$target"
    return 2
  }
  if ! BDTOOL_AUDIO_SPECTRUM_MODE="$(normalize_audio_spectrum_mode "$spectrum_mode")"; then
    screen_error "无效音频频谱模式：$spectrum_mode（可选 single 或 combined）"
    return 2
  fi
  export BDTOOL_AUDIO_SPECTRUM_MODE BDTOOL_AUDIO_SPECTRUM_BACKEND BDTOOL_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS BDTOOL_SCREENSHOT_CANDIDATES

  preflight_post_process || return 1
  generate_item "$item_type" "$item_path" || return 1
  post_process_item "$LAST_OUTPUT_DIR" || return 1
  screen "$(msg op_ok)"
  return 0
}

preflight_post_process() {
  local resolved_default_dir=""
  local return_mode=""
  return_mode="$(detect_return_mode)" || {
    screen_error "步骤失败：BDTOOL_RETURN_MODE 无效（可选: local/http/scp）"
    return 1
  }

  if [[ "$return_mode" == "http" ]]; then
    if ! command -v curl >/dev/null 2>&1; then
      screen_error "步骤失败：已启用 HTTP 回传但系统缺少 curl"
      return 1
    fi
    if ! build_client_upload_url "ptbd-preflight.bin" >/dev/null 2>&1; then
      screen_error "步骤失败：HTTP 回传地址无效（请设置 BDTOOL_RETURN_HTTP_URL 或兼容变量 BDTOOL_CLIENT_UPLOAD_URL）"
      return 1
    fi
    dbg "preflight mode=http-return"
    return 0
  fi

  if [[ "$return_mode" == "scp" ]]; then
    if ! command -v ssh >/dev/null 2>&1 || ! command -v scp >/dev/null 2>&1; then
      screen_error "步骤失败：已启用 SCP 回传，但系统缺少 ssh/scp"
      return 1
    fi
    if [[ -z "${BDTOOL_RETURN_SCP_HOST:-}" || -z "${BDTOOL_RETURN_SCP_USER:-}" || -z "${BDTOOL_RETURN_SCP_REMOTE_DIR:-}" ]]; then
      screen_error "步骤失败：SCP 回传缺少必要参数（需要 BDTOOL_RETURN_SCP_HOST / BDTOOL_RETURN_SCP_USER / BDTOOL_RETURN_SCP_REMOTE_DIR）"
      return 1
    fi
    dbg "preflight mode=scp-return"
    return 0
  fi

  resolved_default_dir="$(resolve_default_download_dir)" || {
    screen_error "步骤失败：无法解析默认下载目录"
    return 1
  }
  dbg "preflight mode=local-download dir=$resolved_default_dir"
  return 0
}

write_quality_status_file() {
  local out="$1"
  local status="$2"
  printf '%s\n' "$status" > "$out/QUALITY_STATUS.txt" 2>/dev/null || true
  screen "质量状态：$status"
  screen "QUALITY_STATUS=$status"
}

generate_item() {
  local type="$1"
  local src="$2"
  local out
  local hard_fail=0
  local quality_status="full"
  local quality_notes=()

  resolve_source_output_layout "$type" "$src" || {
    screen_error "输出路径计算失败：$type $src"
    return 1
  }
  out="$(resolve_fixed_output_root "$type" "$src" || true)"
  [[ -n "$out" ]] || {
    screen_error "输出路径计算失败：$type $src"
    return 1
  }
  dbg "output root=$BDTOOL_SOURCE_INFO_ROOT dir=$out type=$type src=$src"
  mkdir -p "$out"
  screen "生成中：$src"
  screen "生成进度: 0% (任务初始化)"

  if [[ "$type" == "VIDEO" ]]; then
    if [[ "${BDTOOL_CLI_MEDIAINFO:-1}" != "1" && "${BDTOOL_CLI_SHOTS:-1}" != "1" ]]; then
      echo "本次已关闭 mediainfo 与 screenshots，因此该目录为空（这是预期行为）。" > "$out/README.txt"
      screen "生成进度: 96% (生成结果校验中)"
      finalize_video_selected_output "$out" 0 0 || hard_fail=1
      quality_status="partial"
      quality_notes+=("mediainfo=off" "screenshots=off")
    else
      if [[ "${BDTOOL_CLI_MEDIAINFO:-1}" == "1" ]]; then
        screen "生成进度: 10% (mediainfo 生成中)"
        if require_runtime_cmd mediainfo; then
          if ! mediainfo "$src" > "$out/mediainfo.txt" 2>/dev/null; then
            echo "mediainfo 执行失败" > "$out/mediainfo.txt"
            hard_fail=1
            quality_notes+=("mediainfo=failed")
          else
            quality_notes+=("mediainfo=full")
          fi
        else
          echo "未安装 mediainfo，无法生成有效信息。" > "$out/mediainfo.txt"
          hard_fail=1
          quality_notes+=("mediainfo=missing")
        fi
        screen "生成进度: 30% (mediainfo 生成完成)"
      fi

      if [[ "${BDTOOL_CLI_SHOTS:-1}" == "1" ]]; then
        if require_runtime_cmd ffmpeg && require_runtime_cmd ffprobe; then
          screen "生成进度: 35% (截图生成准备)"
          if make_quality_screenshots "$src" "$out" 35 56; then
            quality_notes+=("screenshots=full")
          else
            hard_fail=1
            quality_notes+=("screenshots=failed")
          fi
        else
          hard_fail=1
          quality_notes+=("screenshots=missing-ffmpeg")
        fi
        ensure_six_png "$out"
      fi
      screen "生成进度: 96% (生成结果校验中)"
      finalize_video_selected_output "$out" "${BDTOOL_CLI_MEDIAINFO:-1}" "${BDTOOL_CLI_SHOTS:-1}" || hard_fail=1
    fi
  elif [[ "$type" == "AUDIO" ]]; then
    screen "生成进度: 10% (mediainfo 生成中)"
    if require_runtime_cmd mediainfo; then
      if ! mediainfo "$src" > "$out/mediainfo.txt" 2>/dev/null; then
        echo "mediainfo 执行失败" > "$out/mediainfo.txt"
        hard_fail=1
        quality_notes+=("mediainfo=failed")
      else
        quality_notes+=("mediainfo=full")
      fi
    else
      echo "未安装 mediainfo，无法生成有效信息。" > "$out/mediainfo.txt"
      hard_fail=1
      quality_notes+=("mediainfo=missing")
    fi
    screen "生成进度: 45% (mediainfo 生成完成)"

    screen "生成进度: 50% (频谱图生成中)"
    if ! make_audio_spectrum_single "$src" "$out/频谱图.png"; then
      hard_fail=1
      quality_notes+=("spectrum=failed")
    else
      quality_notes+=("spectrum=full")
    fi
    screen "生成进度: 96% (生成结果校验中)"
    finalize_audio_output "$out" || hard_fail=1
  elif [[ "$type" == "AUDIO_DIR" ]]; then
    local audio_files=()
    local audio=""
    local track_out=""
    local track_name=""
    while IFS= read -r -d '' audio; do
      audio_files+=("$audio")
    done < <(find_audio_files_in_dir "$src")
    find "$out" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + 2>/dev/null || true
    if (( ${#audio_files[@]} < 2 )); then
      screen_error "音乐目录至少需要 2 个音频文件：$src"
      hard_fail=1
      quality_notes+=("audio_dir=too-few-tracks")
    elif [[ "${BDTOOL_AUDIO_SPECTRUM_MODE:-single}" == "combined" ]]; then
      local combined_scope="每首采样 ${BDTOOL_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS:-12} 秒"
      if [[ "${BDTOOL_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS:-12}" == "0" ]]; then
        combined_scope="每首完整音频"
      fi
      screen "生成进度: 10% (整包 mediainfo 生成中)"
      if ! write_audio_mediainfo_report "$out" "${audio_files[@]}"; then
        hard_fail=1
        quality_notes+=("mediainfo=failed")
      else
        quality_notes+=("mediainfo=full")
      fi
      screen "生成进度: 45% (整包 mediainfo 生成完成)"
      screen "生成进度: 50% (连续整包总频谱图生成中，共 ${#audio_files[@]} 首，${combined_scope})"
      if ! make_audio_spectrum_combined "$out/频谱图.png" "${audio_files[@]}"; then
        hard_fail=1
        quality_notes+=("spectrum=failed")
      else
        quality_notes+=("spectrum=combined-full")
      fi
      screen "生成进度: 96% (生成结果校验中)"
      finalize_audio_output "$out" || hard_fail=1
    else
      screen "生成进度: 10% (单曲频谱图生成中，共 ${#audio_files[@]} 首)"
      for audio in "${audio_files[@]}"; do
        track_name="$(safe_name "$(basename "${audio%.*}")")"
        track_out="$(unique_dir "$out" "$track_name")"
        mkdir -p "$track_out"
        if ! write_audio_mediainfo_report "$track_out" "$audio"; then
          hard_fail=1
          quality_notes+=("mediainfo=partial")
        fi
        if ! make_audio_spectrum_single "$audio" "$track_out/频谱图.png"; then
          hard_fail=1
          quality_notes+=("spectrum=partial")
        fi
      done
      quality_notes+=("spectrum=single-tracks")
      screen "生成进度: 96% (生成结果校验中)"
      finalize_audio_dir_single_output "$out" || hard_fail=1
    fi
  else
    screen "生成进度: 10% (BDInfo 生成中)"
    local bd_source="$src"
    local bd_stdout_tmp=""
    local bd_report=""
    local bd_attempt=0
    local disc_probe_video=""
    local bdinfo_mode="full"
    bd_stdout_tmp="$out/.bdinfo_stdout_$$.txt"
    find "$out" -maxdepth 1 -type f \( -name '.bdinfo_stdout_*.txt' -o -name 'BDInfo.txt' \) -delete 2>/dev/null || true
    rm -f "$bd_stdout_tmp"
    disc_probe_video="$(pick_disc_probe_video "$src" || true)"
    if require_runtime_cmd BDInfo; then
      if ! bdinfo_write_report "$bd_source" "$out" "$bd_stdout_tmp"; then
        screen_error "BDInfo 执行失败，改用降级报告继续导出：$bd_source"
        bdinfo_mode="degraded"
        if ! write_bdinfo_fallback_report "$bd_source" "$out/BDInfo.txt" "BDInfo 执行失败或崩溃" "$disc_probe_video"; then
          hard_fail=1
          bdinfo_mode="failed"
        fi
      else
        while (( bd_attempt < 10 )); do
          if bdinfo_raw_report_valid "$bd_stdout_tmp"; then
            bd_report="$bd_stdout_tmp"
            break
          fi
          bd_report="$(find_valid_bdinfo_report "$out" || true)"
          [[ -n "$bd_report" ]] && break
          bd_attempt=$((bd_attempt + 1))
          sleep 1
        done
        if [[ -z "$bd_report" ]]; then
          screen_error "BDInfo 输出无效，改用降级报告继续导出"
          bdinfo_mode="degraded"
          if ! write_bdinfo_fallback_report "$bd_source" "$out/BDInfo.txt" "BDInfo 输出无效：缺少完整区块" "$disc_probe_video"; then
            hard_fail=1
            bdinfo_mode="failed"
          fi
        else
          if ! write_full_bdinfo_report "$bd_report" "$bd_source" "$out/BDInfo.txt"; then
            screen_error "BDInfo 报告归档失败：$out/BDInfo.txt"
            hard_fail=1
            bdinfo_mode="failed"
          fi
          if ! bdinfo_report_valid "$out/BDInfo.txt"; then
            screen_error "BDInfo 输出无效：$out/BDInfo.txt（需含 BDInfo/扫描文件/扫描时间 + 全区块且非空）"
            hard_fail=1
            bdinfo_mode="failed"
          fi
          find "$out" -maxdepth 1 -type f -name '*.txt' ! -name 'BDInfo.txt' -delete
        fi
      fi
    else
      screen_error "缺少 BDInfo，改用降级报告继续导出：$bd_source"
      bdinfo_mode="degraded"
      if ! write_bdinfo_fallback_report "$bd_source" "$out/BDInfo.txt" "缺少 BDInfo 命令" "$disc_probe_video"; then
        hard_fail=1
        bdinfo_mode="failed"
      fi
    fi
    rm -f "$bd_stdout_tmp"
    quality_notes+=("BDInfo=${bdinfo_mode}")
    if [[ "$bdinfo_mode" != "full" ]]; then
      quality_status="degraded"
    fi
    if [[ "$hard_fail" -eq 0 ]]; then
      screen "生成进度: 55% (BDInfo 生成完成)"
      screen "生成进度: 60% (截图生成准备)"
      make_disc_screenshots "$src" "$out" 60 6
      screen "生成进度: 96% (生成结果校验中)"
      finalize_disc_output "$out" || hard_fail=1
    fi
  fi

  if (( ${#quality_notes[@]} > 0 )); then
    local note
    for note in "${quality_notes[@]}"; do
      case "$note" in
        *"=failed"|*"=missing"*|*"=degraded"|*"=partial"*|*"=too-few"*)
          quality_status="degraded"
          ;;
      esac
    done
    write_quality_status_file "$out" "${quality_status};${quality_notes[*]}"
  else
    write_quality_status_file "$out" "$quality_status"
  fi

  if [[ "$hard_fail" -eq 0 ]]; then
    screen "生成进度: 100% (生成阶段完成)"
    screen "生成阶段输出：$out"
    LAST_OUTPUT_DIR="$out"
    return 0
  else
    screen_error "步骤失败：生成阶段校验未通过（$type）"
    screen "生成阶段输出：$out"
    screen_error "$(msg op_fail)"
    LAST_OUTPUT_DIR="$out"
    return 1
  fi
}

download_item() {
  local out="$1"
  local parent="${2:-}"
  local base src_parent zip_path tar_path final_pkg=""
  local client_upload_path=""
  local package_dir=""
  local return_mode="local"
  src_parent="$(dirname "$out")"
  return_mode="$(detect_return_mode)" || return 1
  if [[ "$return_mode" == "http" || "$return_mode" == "scp" ]]; then
    package_dir="${BDTOOL_CLIENT_STAGE_DIR:-$src_parent}"
  else
    if [[ -z "$parent" ]]; then
      parent="$(resolve_default_download_dir)" || return 1
    fi
    package_dir="$parent"
  fi
  mkdir -p "$package_dir"
  base="$(basename "$out")"
  zip_path="$package_dir/$base.zip"
  tar_path="$package_dir/$base.tar.gz"
  screen "打包/上传进度: 0% (准备打包)"

  if command -v zip >/dev/null 2>&1; then
    screen "打包/上传进度: 20% (打包中)"
    (cd "$src_parent" && zip -qr "$zip_path" "$base") || return 1
    screen "打包/上传进度: 65% (打包完成)"
    final_pkg="$zip_path"
  else
    screen "打包/上传进度: 20% (打包中)"
    tar -czf "$tar_path" -C "$src_parent" "$base" || return 1
    screen "打包/上传进度: 65% (打包完成)"
    final_pkg="$tar_path"
  fi

  if [[ "$return_mode" == "http" ]]; then
    screen "打包/上传进度: 70% (上传中)"
    client_upload_path="$(upload_artifact_to_client "$final_pkg" || true)"
    if [[ -z "$client_upload_path" ]]; then
      return 1
    fi
    screen "打包/上传进度: 100% (上传完成)"
    screen "$(msg downloaded)$client_upload_path"
    rm -f "$final_pkg"
    return 0
  fi

  if [[ "$return_mode" == "scp" ]]; then
    screen "打包/上传进度: 70% (SCP 回传中)"
    client_upload_path="$(upload_artifact_via_scp "$final_pkg" || true)"
    if [[ -z "$client_upload_path" ]]; then
      return 1
    fi
    screen "打包/上传进度: 100% (SCP 回传完成)"
    screen "$(msg downloaded)$client_upload_path"
    rm -f "$final_pkg"
    return 0
  fi

  screen "打包/上传进度: 100% (打包完成)"
  screen "$(msg downloaded)$final_pkg"
  if [[ -n "${SSH_CONNECTION:-}" ]]; then
    screen "远程会话提示：以上路径位于当前 VPS，本地请使用 scp/sftp 下载；也可设置 BDTOOL_RETURN_MODE=http|scp 实现自动回传。"
  fi
  return 0
}

ask_download_dir() {
  local out_var="$1"
  local input=""
  local default_dir=""
  default_dir="$(resolve_default_download_dir)" || return 1
  if read_line "$(msg download_dir_prompt): " input; then
    if [[ -z "$input" ]]; then
      printf -v "$out_var" '%s' "$default_dir"
    else
      printf -v "$out_var" '%s' "$input"
    fi
  else
    printf -v "$out_var" '%s' "$default_dir"
  fi
}

cleanup_item() {
  local out="$1"
  if [[ -e "$out" ]]; then
    rm -rf "$out"
    screen "$(msg cleaned)$out"
  else
    screen "$(msg clean_missing)$out"
  fi
}

post_process_item() {
  local out="$1"
  local c=""
  local download_dir=""
  local default_action="${BDTOOL_POST_ACTION:-}"
  local auto_cleanup="${BDTOOL_AUTO_CLEANUP:-1}"
  if is_client_upload_mode; then
    download_dir=""
  else
    download_dir="$(resolve_default_download_dir)" || {
      screen_error "步骤失败：无法解析默认下载目录"
      screen_error "$(msg op_fail)"
      return 1
    }
  fi

  if download_item "$out" "$download_dir"; then
    screen "$(msg op_ok)"
    if [[ "$auto_cleanup" == "1" ]]; then
      if cleanup_item "$out" >/dev/null 2>&1; then
        screen "$(msg cleaned)$out"
      else
        screen_error "步骤失败：清理阶段失败（$out）"
        screen_error "$(msg op_fail)"
        return 1
      fi
    fi
  else
    screen_error "步骤失败：打包/下载阶段失败（$out）"
    screen_error "$(msg op_fail)"
    return 1
  fi

  # Auto flow completed; do not block waiting for extra input.
  section "$(msg post_title)"
  menu_option "$(msg post_0)"
  menu_option "$(msg post_9)"
  if [[ -n "$default_action" ]]; then
    c="$default_action"
  else
    c="0"
  fi
  case "$c" in
    9|q|Q)
      BDTOOL_EXIT_REQUESTED=1
      screen "$(msg bye)"
      return 0
      ;;
    0|"")
      return 0
      ;;
    *)
      return 0
      ;;
  esac
}

handle_multi_select() {
  local raw="$1"
  local tokens t idx
  local total="${#SCAN_ITEMS[@]}"
  local -A seen=()
  local -a selected=()

  read -r -a tokens <<< "$raw"
  if [[ "${#tokens[@]}" -eq 1 && "${tokens[0]}" == "0" ]]; then
    return 1
  fi

  for t in "${tokens[@]}"; do
    [[ "$t" =~ ^[0-9]+$ ]] || return 2
    idx="$t"
    if (( idx <= 0 || idx > total )); then
      return 2
    fi
    if [[ -z "${seen[$idx]:-}" ]]; then
      seen[$idx]=1
      selected+=("$idx")
    fi
  done

  if (( ${#selected[@]} == 0 )); then
    return 2
  fi

  for idx in "${selected[@]}"; do
    if ! preflight_post_process; then
      screen_error "$(msg op_fail)"
      return 3
    fi
    if ! generate_item "${SCAN_TYPES[idx-1]}" "${SCAN_ITEMS[idx-1]}"; then
      return 3
    fi
    if ! post_process_item "$LAST_OUTPUT_DIR"; then
      return 3
    fi
    if [[ "$BDTOOL_EXIT_REQUESTED" == "1" ]]; then
      return 0
    fi
  done

  return 0
}

scan_flow() {
  local target="$1"
  local full="$2"
  local pick rc
  local page_size=0
  local current_page=1
  local total_pages=1
  local number_part=""

  collect_results "$target" "$full"
  if (( ${#SCAN_ITEMS[@]} == 0 )); then
    screen "$(msg none)"
    screen "$(msg none_hint)"
    return 0
  fi

  page_size="${BDTOOL_PAGE_SIZE:-$MAX_SCAN_DISPLAY}"
  [[ "$page_size" =~ ^[0-9]+$ ]] || page_size="$MAX_SCAN_DISPLAY"
  (( page_size < 1 )) && page_size="$MAX_SCAN_DISPLAY"

  while true; do
    total_pages="$(scan_total_pages "$page_size")"
    (( current_page < 1 )) && current_page=1
    (( current_page > total_pages )) && current_page="$total_pages"
    show_results_page "$current_page" "$page_size" || return 0

    read_line "$(msg pick): " pick || return 0
    pick="${pick#"${pick%%[![:space:]]*}"}"
    pick="${pick%"${pick##*[![:space:]]}"}"

    [[ -z "$pick" ]] && continue
    case "$pick" in
      0)
        return 0
        ;;
      r|R)
        collect_results "$target" "$full"
        if (( ${#SCAN_ITEMS[@]} == 0 )); then
          screen "$(msg none)"
          screen "$(msg none_hint)"
          return 0
        fi
        current_page=1
        continue
        ;;
      n|N)
        if (( current_page < total_pages )); then
          current_page=$((current_page + 1))
        else
          screen_error "$(msg invalid)"
        fi
        continue
        ;;
      p|P)
        if (( current_page > 1 )); then
          current_page=$((current_page - 1))
        else
          screen_error "$(msg invalid)"
        fi
        continue
        ;;
      g\ *|G\ *)
        number_part="$(echo "$pick" | sed -E 's/^[gG][[:space:]]*//')"
        if [[ "$number_part" =~ ^[0-9]+$ ]] && (( number_part >= 1 && number_part <= total_pages )); then
          current_page="$number_part"
        else
          screen_error "$(msg invalid)"
        fi
        continue
        ;;
      g|G)
        screen_error "$(msg invalid)"
        continue
        ;;
    esac

    if handle_multi_select "$pick"; then
      rc=0
    else
      rc=$?
    fi
    [[ "$BDTOOL_EXIT_REQUESTED" == "1" ]] && return 0
    if [[ "$rc" -eq 0 || "$rc" -eq 1 ]]; then
      return 0
    fi
    if [[ "$rc" -eq 3 ]]; then
      return 0
    fi
    screen_error "$(msg invalid)"
  done
}

scan_menu() {
  scan_flow "/" "1"
  return 0
}

main_menu() {
  local c
  while true; do
    section "$(msg main_title)"
    menu_option "$(msg main_1)"
    menu_option "$(msg main_2)"
    menu_option "$(msg main_3)"
    read_line "$(msg prompt): " c || return 0
    case "$c" in
      1|/) scan_menu; [[ "$BDTOOL_EXIT_REQUESTED" == "1" ]] && return 0 ;;
      2)
        if [[ "$LANG_CODE" == "zh" ]]; then LANG_CODE="en"; else LANG_CODE="zh"; fi
        ;;
      3|9|q|Q)
        screen "$(msg bye)"
        return 0
        ;;
      *) screen_error "$(msg invalid)" ;;
    esac
  done
}

cli_usage() {
  cat <<'USAGE'
bdtool <path> [options]
bdtool scan <path> --out <dir> [options]
bdtool doctor
bdtool status
bdtool version
bdtool start
bdtool install
bdtool clean

options:
  --log-level LEVEL  日志级别：quiet|normal|debug（默认 normal）
  --quiet            等价于 --log-level quiet
  --no-mediainfo     视频不生成 MediaInfo
  --no-shots         视频不截图
  --mode dry         等价于 --no-shots --no-mediainfo
  --shots N          参数保留；最终固定输出 6 张截图（1.png..6.png）
  -s N               等价于 --shots N
  --jobs N           参数保留；当前主实现串行处理
  -j N               等价于 --jobs N
  --audio-spectrum MODE
                    音频频谱模式：single=单曲图，combined=目录整包总图（默认 single）
  --audio-spectrum-backend BACKEND
                    频谱后端：auto|sox|sox_ng|ffmpeg（默认 auto）
  --audio-spectrum-seconds N
                    combined 模式每首采样秒数；0 表示完整音频
  --out DIR          输出目录（默认按源路径上层目录/信息；显式指定时覆盖）

examples:
  bdtool movie.mkv
  bdtool song.flac
  bdtool /data/music/album --audio-spectrum combined
  bdtool /data/videos -s 6 -j 2
  bdtool movie.mkv --log-level debug
  bdtool scan /data/videos --out output
USAGE
}

cli_cmd_version() {
  echo "bdtool ${BT_VERSION:-0.1.0}"
}

cli_cmd_start() {
  local launcher="$ROOT_DIR/ptbd-start.sh"
  if [[ -x "$launcher" ]]; then
    exec "$launcher" "$@"
  fi
  main_menu
}

cli_cmd_doctor() {
  section "doctor"
  local c
  for c in find sort awk sed ffmpeg ffprobe mediainfo BDInfo; do
    if require_runtime_cmd "$c" >/dev/null 2>&1; then
      log_success "OK: $c"
    else
      log_warn "MISS: $c"
    fi
  done
}

cli_cmd_status() {
  section "status"
  local install_path=""
  local version=""
  local dep=""
  local fail=0
  install_path="$(command -v bdtool 2>/dev/null || true)"
  [[ -n "$install_path" ]] || install_path="$SCRIPT_PATH"
  version="$(cli_cmd_version)"
  log_info "[bdtool] 安装路径：$install_path"
  log_info "[bdtool] 版本：$version"
  log_info "[bdtool] 依赖检查："
  for dep in ffmpeg ffprobe mediainfo BDInfo; do
    if require_runtime_cmd "$dep" >/dev/null 2>&1; then
      log_success "OK: $dep"
    else
      log_warn "MISS: $dep"
      fail=1
    fi
  done
  if [[ "$fail" -eq 0 ]]; then
    log_success "[bdtool] 结果：PASS"
  else
    log_err "[bdtool] 结果：FAIL"
  fi
  return "$fail"
}

cli_cmd_install() {
  section "install"
  if [[ ! -f "$ROOT_DIR/install.sh" ]]; then
    screen_error "找不到安装脚本：$ROOT_DIR/install.sh"
    return 1
  fi
  bash "$ROOT_DIR/install.sh" || return 1
  cli_cmd_doctor
}

cli_cmd_clean() {
  local target="./bdtool-output"
  [[ "$target" == "./bdtool-output" ]] || return 1
  if [[ -d "$target" ]]; then
    rm -rf -- "$target"
    log_success "清理输出目录 完成"
    log_info "[bdtool] cleaned: $target"
  else
    log_info "nothing to clean"
  fi
}

cli_is_positive_int() {
  [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]
}

cli_write_dry_output() {
  local type="$1"
  local src="$2"
  local out=""
  out="$(resolve_fixed_output_root "$type" "$src" || true)"
  [[ -n "$out" ]] || return 1
  mkdir -p "$out"
  find "$out" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + 2>/dev/null || true
  echo "本次已关闭 mediainfo 与 screenshots，因此该目录为空（这是预期行为）。" > "$out/README.txt"
  screen "生成阶段输出：$out"
  LAST_OUTPUT_DIR="$out"
}

cli_process_one() {
  local type="$1"
  local path="$2"
  if [[ "${BDTOOL_CLI_MEDIAINFO:-1}" != "1" && "${BDTOOL_CLI_SHOTS:-1}" != "1" ]]; then
    cli_write_dry_output "$type" "$path"
    return $?
  fi
  generate_item "$type" "$path"
}

cli_process_local_scan() {
  local scan_path="$1"
  local out_dir="${2:-}"
  local item_type=""
  local item_path=""
  local idx parent
  local -A audio_dirs=()
  local -a process_types=()
  local -a process_paths=()

  [[ -e "$scan_path" ]] || {
    screen_error "路径不存在：$scan_path"
    return 1
  }
  BDTOOL_CLI_OUT_DIR="$out_dir"
  export BDTOOL_CLI_OUT_DIR

  if resolve_scan_candidate "$scan_path" item_type item_path; then
    if [[ -f "$scan_path" || "$item_type" == "BDMV" || "$item_type" == "ISO" || ( "$item_type" == "AUDIO_DIR" && "${BDTOOL_AUDIO_SPECTRUM_MODE:-single}" == "combined" ) ]]; then
      cli_process_one "$item_type" "$item_path"
      return $?
    fi
  elif [[ ! -d "$scan_path" ]]; then
    screen_error "不支持的文件类型：$scan_path（仅视频/音频文件或 Blu-ray BDMV/ISO）"
    return 1
  fi

  collect_results "$scan_path" 0 >/dev/null
  for idx in "${!SCAN_ITEMS[@]}"; do
    if [[ "${SCAN_TYPES[$idx]}" == "AUDIO_DIR" ]]; then
      audio_dirs["${SCAN_ITEMS[$idx]}"]=1
    fi
  done
  for idx in "${!SCAN_ITEMS[@]}"; do
    item_type="${SCAN_TYPES[$idx]}"
    item_path="${SCAN_ITEMS[$idx]}"
    if [[ "$item_type" == "AUDIO_DIR" && "${BDTOOL_AUDIO_SPECTRUM_MODE:-single}" != "combined" ]]; then
      continue
    fi
    if [[ "$item_type" == "AUDIO" && "${BDTOOL_AUDIO_SPECTRUM_MODE:-single}" == "combined" ]]; then
      parent="$(dirname "$item_path")"
      [[ -n "${audio_dirs[$parent]:-}" ]] && continue
    fi
    process_types+=("$item_type")
    process_paths+=("$item_path")
  done

  if (( ${#process_paths[@]} == 0 )); then
    screen_error "未发现可处理媒体文件：$scan_path"
    return 1
  fi

  for idx in "${!process_paths[@]}"; do
    screen "处理媒体 $((idx + 1))/${#process_paths[@]}：${process_paths[$idx]}"
    cli_process_one "${process_types[$idx]}" "${process_paths[$idx]}" || return 1
  done
  echo "DONE"
}

cli_main_scan() {
  local scan_path="$1"
  shift
  local out_dir=""
  local quiet=0
  local log_level="normal"
  local jobs=1

  BDTOOL_CLI_MEDIAINFO=1
  BDTOOL_CLI_SHOTS=1
  BDTOOL_AUDIO_SPECTRUM_MODE="${BDTOOL_AUDIO_SPECTRUM_MODE:-single}"
  BDTOOL_AUDIO_SPECTRUM_BACKEND="${BDTOOL_AUDIO_SPECTRUM_BACKEND:-auto}"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --log-level)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || { screen_error "--log-level requires a value"; return 2; }
        case "$2" in
          quiet|normal|debug) log_level="$2" ;;
          *) screen_error "invalid log level: $2"; return 2 ;;
        esac
        shift 2
        ;;
      --out)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || { screen_error "--out requires a value"; return 2; }
        out_dir="${2:-}"
        shift 2
        ;;
      --no-mediainfo) BDTOOL_CLI_MEDIAINFO=0; shift ;;
      --no-shots) BDTOOL_CLI_SHOTS=0; shift ;;
      --mode)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || { screen_error "--mode requires a value"; return 2; }
        if [[ "$2" == "dry" ]]; then
          BDTOOL_CLI_MEDIAINFO=0
          BDTOOL_CLI_SHOTS=0
        else
          screen_error "unsupported mode: $2"
          return 2
        fi
        shift 2
        ;;
      --shots|-s)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || { screen_error "$1 requires a value"; return 2; }
        cli_is_positive_int "$2" || { screen_error "--shots 必须是正整数"; return 2; }
        shift 2
        ;;
      --jobs|-j)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || { screen_error "$1 requires a value"; return 2; }
        cli_is_positive_int "$2" || { screen_error "--jobs 必须是正整数"; return 2; }
        jobs="$2"
        shift 2
        ;;
      --audio-spectrum)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || { screen_error "--audio-spectrum requires a value"; return 2; }
        case "${2,,}" in
          single|combined) BDTOOL_AUDIO_SPECTRUM_MODE="${2,,}" ;;
          *) screen_error "invalid audio spectrum mode: $2"; return 2 ;;
        esac
        shift 2
        ;;
      --audio-spectrum-backend)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || { screen_error "--audio-spectrum-backend requires a value"; return 2; }
        case "${2,,}" in
          auto|sox|sox_ng|ffmpeg) BDTOOL_AUDIO_SPECTRUM_BACKEND="${2,,}" ;;
          *) screen_error "invalid audio spectrum backend: $2"; return 2 ;;
        esac
        shift 2
        ;;
      --audio-spectrum-seconds)
        [[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] || { screen_error "--audio-spectrum-seconds requires a value"; return 2; }
        [[ "$2" =~ ^[0-9]+$ ]] || { screen_error "--audio-spectrum-seconds 必须是非负整数"; return 2; }
        BDTOOL_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS="$2"
        shift 2
        ;;
      --quiet) quiet=1; shift ;;
      -h|--help) cli_usage; return 0 ;;
      *) screen_error "未知参数：$1"; return 2 ;;
    esac
  done

  [[ "$log_level" == "debug" ]] && BDTOOL_DEBUG=1
  [[ "$log_level" == "quiet" ]] && quiet=1
  export BDTOOL_CLI_MEDIAINFO BDTOOL_CLI_SHOTS BDTOOL_AUDIO_SPECTRUM_MODE BDTOOL_AUDIO_SPECTRUM_BACKEND BDTOOL_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS BDTOOL_SCREENSHOT_CANDIDATES
  if (( jobs > 1 )); then
    dbg "direct CLI currently processes serially; requested jobs=$jobs"
  fi

  if [[ "$quiet" == "1" ]]; then
    cli_process_local_scan "$scan_path" "$out_dir" >/dev/null
  else
    cli_process_local_scan "$scan_path" "$out_dir"
  fi
}

handle_cli_command() {
  local cmd="${1:-}"
  [[ -n "$cmd" ]] || { cli_usage; return 0; }
  case "$cmd" in
    scan)
      shift
      [[ $# -ge 1 ]] || { screen_error "用法：bdtool scan <path> --out <dir>"; return 2; }
      cli_main_scan "$@"
      ;;
    doctor)
      shift
      cli_cmd_doctor "$@"
      ;;
    status)
      shift
      cli_cmd_status "$@"
      ;;
    install)
      shift
      cli_cmd_install "$@"
      ;;
    start)
      shift
      cli_cmd_start "$@"
      ;;
    clean)
      shift
      cli_cmd_clean "$@"
      ;;
    -h|--help|help)
      cli_usage
      ;;
    -v|--version|version)
      cli_cmd_version
      ;;
    *)
      if [[ -e "$cmd" ]]; then
        cli_main_scan "$cmd" "${@:2}"
      else
        cli_usage
        screen_error "未知命令或路径不存在：$cmd"
        return 2
      fi
      ;;
  esac
}

main() {
  # Prefer script CLI mode when called with a direct target path/command.
  # This preserves menu UX for plain `bdtool` while making `bdtool <path>`
  # and compatibility commands execute real processing.
  if [[ $# -gt 0 ]]; then
    if [[ "$1" == "scan-json" ]]; then
      handle_scan_json_command "$@"
      exit $?
    fi
    if [[ "$1" == "generate-path" ]]; then
      handle_generate_path_command "$@"
      exit $?
    fi
    case "$1" in
      scan|doctor|status|install|clean|start|version|--version|-v|help|--help|-h)
        handle_cli_command "$@"
        exit $?
        ;;
      *)
        if [[ -e "$1" ]]; then
          handle_cli_command "$@"
          exit $?
        fi
        case "$1" in
          --lang|--lang=*|--non-interactive) ;;
          *)
            handle_cli_command "$@"
            exit $?
            ;;
        esac
        ;;
    esac
  fi

  local lang=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --lang)
        lang="${2:-}"
        shift 2
        ;;
      --lang=*)
        lang="${1#*=}"
        shift
        ;;
      --non-interactive)
        BDTOOL_NO_PROMPT=1
        shift
        ;;
      --help|-h)
        if [[ "$LANG_CODE" == "en" ]]; then
          echo "Usage: bdtool [--lang zh|en]"
        else
          echo "用法: bdtool [--lang zh|en]"
        fi
        exit 0
        ;;
      *)
        shift
        ;;
    esac
  done

  if [[ -n "$lang" ]]; then
    if [[ "$lang" == "en" ]]; then LANG_CODE="en"; else LANG_CODE="zh"; fi
  fi

  if [[ "$BDTOOL_NO_PROMPT" == "1" ]]; then
    if [[ "$LANG_CODE" == "en" ]]; then
      echo "Usage: bdtool [--lang zh|en]"
    else
      echo "用法: bdtool [--lang zh|en]"
    fi
    exit 0
  fi

  main_menu
}

require_runtime_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 && return 0
  if [[ -n "${BDTOOL_BUNDLE_DIR:-}" && -x "$BDTOOL_BUNDLE_DIR/bin/$cmd" ]]; then
    PATH="$BDTOOL_BUNDLE_DIR/bin:$PATH"
    export PATH
    command -v "$cmd" >/dev/null 2>&1 && return 0
  fi
  if [[ "$cmd" == "ffmpeg" ]]; then
    screen_error "缺少依赖命令：ffmpeg"
    screen_error "可复制修复：apt-get update && apt-get install -y ffmpeg mediainfo"
    return 1
  fi
  if [[ "$cmd" == "BDInfo" ]]; then
    screen_error "缺少依赖命令：BDInfo"
    screen_error "请先执行：bash install.sh --offline"
    return 1
  fi
  screen_error "缺少依赖命令：$cmd"
  screen_error "请先执行：bash install.sh --offline"
  return 1
}

main "$@"
