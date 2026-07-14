#!/usr/bin/env bash
set -euo pipefail

PTBD_REMOTE_HOST="${PTBD_REMOTE_HOST:-}"
PTBD_REMOTE_PORT="${PTBD_REMOTE_PORT:-22}"
PTBD_REMOTE_PASSWORD="${PTBD_REMOTE_PASSWORD:-}"
PTBD_REMOTE_CACHE_ROOT="${PTBD_REMOTE_CACHE_ROOT:-}"
PTBD_AUDIO_SPECTRUM_MODE="${PTBD_AUDIO_SPECTRUM_MODE:-single}"

ASKPASS_SCRIPT=""
SSH_AUTH_PREFIX=()
TEMP_ROOT=""

REMOTE_OS=""
REMOTE_ARCH=""
REMOTE_HOME=""
REMOTE_ID=""
REMOTE_ID_LIKE=""
REMOTE_VERSION_ID=""
REMOTE_HAS_TAR="0"
REMOTE_HAS_BASH="0"
REMOTE_HAS_PYTHON3="0"
REMOTE_HAS_CURL="0"
REMOTE_HAS_FFMPEG="0"
REMOTE_HAS_FFPROBE="0"
REMOTE_HAS_MEDIAINFO="0"

log() { printf '[ptbd-bootstrap] %s\n' "$*" >&2; }
err() { printf '[ptbd-bootstrap][ERROR] %s\n' "$*" >&2; }

cleanup() {
  if [[ -n "$ASKPASS_SCRIPT" ]]; then
    rm -f "$ASKPASS_SCRIPT"
  fi
  if [[ -n "$TEMP_ROOT" && -d "$TEMP_ROOT" ]]; then
    rm -rf "$TEMP_ROOT"
  fi
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
Usage:
  prepare-remote-runtime.sh [options]

What it does:
  - Detect the remote Linux distro and current dependency state
  - Auto-install runtime packages on Debian / Ubuntu / Alpine when possible
  - Upload either a thin PT ReleaseKit runtime or a bundled linux-amd64 fallback
  - Print the remote launcher path to stdout

Options:
  --host user@server        Remote SSH target
  --port N                  Remote SSH port (default: 22)
  --password TEXT           SSH password; if omitted, use SSH keys
  --cache-root DIR          Remote cache root (default: $HOME/.cache/ptbd-remote)
  -h, --help                Show this help

Environment variables:
  PTBD_REMOTE_HOST
  PTBD_REMOTE_PORT
  PTBD_REMOTE_PASSWORD
  PTBD_REMOTE_CACHE_ROOT
  PTBD_AUDIO_SPECTRUM_MODE  single or combined (default: single)
EOF
}

quote_sh() {
  printf "'%s'" "$(printf '%s' "${1:-}" | sed "s/'/'\\\\''/g")"
}

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
  local -a candidates=(
    "${PTBDTOOL_ROOT:-}"
    "${PTBD_INSTALL_ROOT:-}"
    "$script_dir/.."
    "$script_dir"
    "/opt/PT-BDtool"
    "$HOME/.local/share/pt-bdtool/PT-BDtool-app"
  )
  for candidate in "${candidates[@]}"; do
    [[ -n "$candidate" ]] || continue
    if [[ -f "$candidate/bdtool" && -f "$candidate/lib/ui.sh" ]]; then
      (
        cd -P "$candidate" 2>/dev/null && pwd
      )
      return 0
    fi
  done
  return 1
}

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

run_ssh() {
  "${SSH_AUTH_PREFIX[@]}" ssh \
    -p "$PTBD_REMOTE_PORT" \
    -o StrictHostKeyChecking=yes \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 \
    "$PTBD_REMOTE_HOST" \
    "$@"
}

run_remote_sh() {
  local script="${1:-}"
  [[ -n "$script" ]] || {
    err "run_remote_sh missing script body"
    exit 2
  }
  run_ssh "sh -lc $(quote_sh "$script")"
}

run_scp() {
  "${SSH_AUTH_PREFIX[@]}" scp \
    -P "$PTBD_REMOTE_PORT" \
    -o StrictHostKeyChecking=yes \
    "$@"
}

normalize_remote_arch() {
  case "${1:-}" in
    x86_64|amd64) printf '%s' "linux-amd64" ;;
    *) return 1 ;;
  esac
}

ensure_local_bundle() {
  local bundle_root="$APP_ROOT/third_party/bundle/linux-amd64"
  if [[ -x "$bundle_root/bin/ffmpeg" && -x "$bundle_root/bin/ffprobe" && -x "$bundle_root/bin/mediainfo" && -x "$bundle_root/bin/BDInfo" && -d "$bundle_root/lib" ]]; then
    return 0
  fi

  if [[ -f "$APP_ROOT/scripts/ensure-bundle.py" ]] && command -v python3 >/dev/null 2>&1; then
    log "local linux bundle missing; trying GitHub Release asset"
    python3 "$APP_ROOT/scripts/ensure-bundle.py"
  fi
}

require_local_files() {
  local archive_mode="$1"
  local path=""
  if ! python3 "$APP_ROOT/ptbd_core/runtime_assets.py" validate \
    --profile remote --source-root "$APP_ROOT" >/dev/null; then
    err "local runtime asset manifest is incomplete"
    exit 1
  fi
  local -a required=()
  if [[ "$archive_mode" == "bundle" ]]; then
    ensure_local_bundle || true
    required+=(
      "$APP_ROOT/third_party/bundle/linux-amd64/bin"
      "$APP_ROOT/third_party/bundle/linux-amd64/lib"
    )
  fi
  for path in "${required[@]}"; do
    [[ -e "$path" ]] || {
      err "missing local runtime file: $path"
      exit 1
    }
  done
}

build_runtime_archive() {
  local archive_path="$1"
  local archive_mode="$2"
  python3 - "$APP_ROOT" "$archive_path" "$archive_mode" <<'PY'
import hashlib
import os
import sys
import tarfile
from pathlib import Path

app_root = Path(sys.argv[1]).resolve()
archive_path = Path(sys.argv[2]).resolve()
archive_mode = sys.argv[3]
bundle_root = app_root / "third_party" / "bundle" / "linux-amd64"

sys.path.insert(0, str(app_root))
from ptbd_core.runtime_assets import validate_profile

members = [
    (entry.source, entry.relative_path)
    for entry in validate_profile(app_root, "remote")
]

if archive_mode == "bundle":
    for branch in ("bin", "lib"):
        root = bundle_root / branch
        entries = sorted(root.rglob("*"), key=lambda item: item.relative_to(app_root).as_posix())
        for entry in entries:
            members.append((entry, entry.relative_to(app_root).as_posix()))
elif archive_mode != "minimal":
    raise SystemExit(f"unsupported archive mode: {archive_mode}")

hasher = hashlib.sha256()
hasher.update(f"mode:{archive_mode}".encode("utf-8"))
hasher.update(b"\0")
for source_path, relative_path in members:
    hasher.update(relative_path.encode("utf-8"))
    hasher.update(b"\0")
    stat_result = os.lstat(source_path)
    mode = stat_result.st_mode
    if os.path.islink(source_path):
        hasher.update(b"L")
        hasher.update(os.readlink(source_path).encode("utf-8"))
    elif os.path.isdir(source_path):
        hasher.update(b"D")
    else:
        hasher.update(b"F")
        with open(source_path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
    hasher.update(str(mode & 0o7777).encode("ascii"))
    hasher.update(b"\0")

runtime_hash = hasher.hexdigest()
archive_path.parent.mkdir(parents=True, exist_ok=True)

def normalize_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    return info

with tarfile.open(archive_path, "w:gz", compresslevel=6, format=tarfile.PAX_FORMAT) as tar_handle:
    for source_path, relative_path in members:
        tar_handle.add(source_path, arcname=relative_path, recursive=False, filter=normalize_tar_info)

print(runtime_hash)
PY
}

probe_remote_system() {
  local script_file="$APP_ROOT/ptbd_core/assets/remote-probe.sh"
  local script=""
  [[ -f "$script_file" ]] || {
    err "missing remote probe asset: $script_file"
    exit 1
  }
  script="$(cat "$script_file")"
  run_remote_sh "$script"
}

parse_remote_info() {
  local raw="${1:-}"
  local key=""
  local value=""

  REMOTE_OS=""
  REMOTE_ARCH=""
  REMOTE_HOME=""
  REMOTE_ID=""
  REMOTE_ID_LIKE=""
  REMOTE_VERSION_ID=""
  REMOTE_HAS_TAR="0"
  REMOTE_HAS_BASH="0"
  REMOTE_HAS_PYTHON3="0"
  REMOTE_HAS_CURL="0"
  REMOTE_HAS_FFMPEG="0"
  REMOTE_HAS_FFPROBE="0"
  REMOTE_HAS_MEDIAINFO="0"
  REMOTE_HAS_NUMPY="0"
  REMOTE_HAS_PIL="0"

  while IFS='=' read -r key value; do
    case "$key" in
      REMOTE_OS) REMOTE_OS="$value" ;;
      REMOTE_ARCH) REMOTE_ARCH="$value" ;;
      REMOTE_HOME) REMOTE_HOME="$value" ;;
      REMOTE_ID) REMOTE_ID="$value" ;;
      REMOTE_ID_LIKE) REMOTE_ID_LIKE="$value" ;;
      REMOTE_VERSION_ID) REMOTE_VERSION_ID="$value" ;;
      REMOTE_HAS_TAR) REMOTE_HAS_TAR="$value" ;;
      REMOTE_HAS_BASH) REMOTE_HAS_BASH="$value" ;;
      REMOTE_HAS_PYTHON3) REMOTE_HAS_PYTHON3="$value" ;;
      REMOTE_HAS_CURL) REMOTE_HAS_CURL="$value" ;;
      REMOTE_HAS_FFMPEG) REMOTE_HAS_FFMPEG="$value" ;;
      REMOTE_HAS_FFPROBE) REMOTE_HAS_FFPROBE="$value" ;;
      REMOTE_HAS_MEDIAINFO) REMOTE_HAS_MEDIAINFO="$value" ;;
      REMOTE_HAS_NUMPY) REMOTE_HAS_NUMPY="$value" ;;
      REMOTE_HAS_PIL) REMOTE_HAS_PIL="$value" ;;
    esac
  done <<< "$raw"
}

remote_os_supported_for_auto_install() {
  case " ${REMOTE_ID:-} ${REMOTE_ID_LIKE:-} " in
    *" debian "*|*" ubuntu "*|*" alpine "*) return 0 ;;
    *) return 1 ;;
  esac
}

remote_core_deps_ready() {
  [[ "${REMOTE_HAS_TAR:-0}" == "1" ]] || return 1
  [[ "${REMOTE_HAS_BASH:-0}" == "1" ]] || return 1
  [[ "${REMOTE_HAS_PYTHON3:-0}" == "1" ]] || return 1
  [[ "${REMOTE_HAS_CURL:-0}" == "1" ]] || return 1
  [[ "${REMOTE_HAS_FFMPEG:-0}" == "1" ]] || return 1
  [[ "${REMOTE_HAS_FFPROBE:-0}" == "1" ]] || return 1
  [[ "${REMOTE_HAS_MEDIAINFO:-0}" == "1" ]] || return 1
  if [[ "$PTBD_AUDIO_SPECTRUM_MODE" == "combined" ]]; then
    [[ "${REMOTE_HAS_NUMPY:-0}" == "1" ]] || return 1
    [[ "${REMOTE_HAS_PIL:-0}" == "1" ]] || return 1
  fi
  return 0
}

ensure_remote_system_deps() {
  local script_file="$APP_ROOT/ptbd_core/assets/remote-install-deps.sh"
  local script=""
  local output=""
  local rc=0

  [[ -f "$script_file" ]] || {
    err "missing remote dependency asset: $script_file"
    exit 1
  }
  script="$(cat "$script_file")"

  set +e
  output="$(run_remote_sh "PTBD_AUDIO_SPECTRUM_MODE=$(quote_sh "$PTBD_AUDIO_SPECTRUM_MODE"); export PTBD_AUDIO_SPECTRUM_MODE; $script")"
  rc=$?
  set -e

  if [[ "$rc" -ne 0 ]]; then
    log "remote dependency install attempt returned rc=$rc; will continue with reprobe/fallback logic"
  fi
  if [[ -n "$output" ]]; then
    log "remote dependency install result: $output"
  fi
}
SCRIPT_PATH="$(resolve_script_path "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"
APP_ROOT="$(find_app_root "$SCRIPT_DIR" || true)"
[[ -n "$APP_ROOT" ]] || APP_ROOT="$(cd -P "$SCRIPT_DIR/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) PTBD_REMOTE_HOST="${2:-}"; shift 2 ;;
    --port) PTBD_REMOTE_PORT="${2:-}"; shift 2 ;;
    --password) PTBD_REMOTE_PASSWORD="${2:-}"; shift 2 ;;
    --cache-root) PTBD_REMOTE_CACHE_ROOT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) err "unknown argument: $1"; usage; exit 2 ;;
  esac
done

[[ -n "$PTBD_REMOTE_HOST" ]] || { err "missing --host"; usage; exit 2; }
case "$PTBD_AUDIO_SPECTRUM_MODE" in
  single|combined) ;;
  *) err "invalid PTBD_AUDIO_SPECTRUM_MODE: $PTBD_AUDIO_SPECTRUM_MODE"; exit 2 ;;
esac
command -v ssh >/dev/null 2>&1 || { err "missing ssh"; exit 1; }
command -v scp >/dev/null 2>&1 || { err "missing scp"; exit 1; }
command -v python3 >/dev/null 2>&1 || { err "missing python3"; exit 1; }

setup_ssh_auth

REMOTE_INFO="$(probe_remote_system)"
parse_remote_info "$REMOTE_INFO"

[[ "$REMOTE_OS" == "Linux" ]] || { err "unsupported remote OS: ${REMOTE_OS:-unknown}; only Linux is supported"; exit 1; }

if [[ -z "$PTBD_REMOTE_CACHE_ROOT" ]]; then
  PTBD_REMOTE_CACHE_ROOT="${REMOTE_HOME:-$HOME}/.cache/ptbd-remote"
fi

log "remote detected: os=${REMOTE_OS:-unknown} id=${REMOTE_ID:-unknown} version=${REMOTE_VERSION_ID:-unknown} arch=${REMOTE_ARCH:-unknown}"

if remote_os_supported_for_auto_install; then
  log "supported distro detected; auto-checking Debian/Ubuntu/Alpine packages"
  ensure_remote_system_deps
  REMOTE_INFO="$(probe_remote_system)"
  parse_remote_info "$REMOTE_INFO"
fi

if [[ "$PTBD_AUDIO_SPECTRUM_MODE" == "combined" ]] \
  && { [[ "${REMOTE_HAS_NUMPY:-0}" != "1" ]] || [[ "${REMOTE_HAS_PIL:-0}" != "1" ]]; }; then
  err "combined audio spectrum requires remote Python numpy and Pillow"
  err "automatic installation did not provide them; install both modules or use single mode"
  exit 1
fi

ARCHIVE_MODE="minimal"
if remote_core_deps_ready; then
  log "remote system dependencies look good; using thin runtime archive"
else
  normalize_remote_arch "$REMOTE_ARCH" >/dev/null || {
    err "remote missing required system deps and arch=${REMOTE_ARCH:-unknown} cannot use bundled linux-amd64 fallback"
    err "need at least: tar bash python3 curl ffmpeg ffprobe mediainfo"
    err "supported auto-install targets: Debian / Ubuntu / Alpine"
    exit 1
  }
  ARCHIVE_MODE="bundle"
  log "remote system deps still incomplete; falling back to bundled linux-amd64 runtime"
fi

require_local_files "$ARCHIVE_MODE"

TEMP_ROOT="$(mktemp -d)"
ARCHIVE_PATH="$TEMP_ROOT/ptbd-runtime.tar.gz"
RUNTIME_HASH="$(build_runtime_archive "$ARCHIVE_PATH" "$ARCHIVE_MODE")"
REMOTE_RUNTIME_DIR="$PTBD_REMOTE_CACHE_ROOT/runtime-$RUNTIME_HASH"
REMOTE_LAUNCHER="$REMOTE_RUNTIME_DIR/ptbd-runtime"
REMOTE_ARCHIVE="$PTBD_REMOTE_CACHE_ROOT/runtime-$RUNTIME_HASH-$$.tar.gz"
REMOTE_TMP_DIR="$PTBD_REMOTE_CACHE_ROOT/.runtime-$RUNTIME_HASH-$$"

REMOTE_CHECK_SCRIPT="if [ -x $(quote_sh "$REMOTE_LAUNCHER") ]; then printf '%s\n' ready; else printf '%s\n' missing; fi"
if [[ "$(run_remote_sh "$REMOTE_CHECK_SCRIPT")" == "ready" ]]; then
  log "remote runtime cache hit: $REMOTE_RUNTIME_DIR"
  printf '%s\n' "$REMOTE_LAUNCHER"
  exit 0
fi

log "uploading ${ARCHIVE_MODE} runtime to $PTBD_REMOTE_HOST:$PTBD_REMOTE_CACHE_ROOT"
run_remote_sh "mkdir -p $(quote_sh "$PTBD_REMOTE_CACHE_ROOT")"
run_scp "$ARCHIVE_PATH" "$PTBD_REMOTE_HOST:$REMOTE_ARCHIVE"

REMOTE_PREPARE_SCRIPT=$(
  cat <<EOF
set -eu

runtime_dir=$(quote_sh "$REMOTE_RUNTIME_DIR")
launcher_path=$(quote_sh "$REMOTE_LAUNCHER")
archive_path=$(quote_sh "$REMOTE_ARCHIVE")
tmp_dir=$(quote_sh "$REMOTE_TMP_DIR")
archive_mode=$(quote_sh "$ARCHIVE_MODE")
work_dir="\$runtime_dir"

if [ -f "\$archive_path" ]; then
  rm -rf "\$tmp_dir"
  mkdir -p "\$tmp_dir"
  tar -xzf "\$archive_path" -C "\$tmp_dir"
  work_dir="\$tmp_dir"
fi

mkdir -p "\$work_dir/bin" "\$(dirname "\$runtime_dir")"
chmod +x "\$work_dir/bdtool" "\$work_dir/bdtool-legacy.sh" "\$work_dir/bdtool.sh"

rm -f "\$work_dir/bin/BDInfo"
if [ "\$archive_mode" = "minimal" ] && ! command -v BDInfo >/dev/null 2>&1 && command -v bd_info >/dev/null 2>&1; then
  cat > "\$work_dir/bin/BDInfo" <<'EOS'
#!/usr/bin/env sh
set -eu
if [ "\${1:-}" = "-w" ]; then
  shift
fi
target="\${1:-}"
if [ -z "\$target" ]; then
  echo "usage: BDInfo <scan_target> [out_dir]" >&2
  exit 2
fi
exec bd_info "\$target"
EOS
  chmod +x "\$work_dir/bin/BDInfo"
fi

cat > "\$work_dir/ptbd-runtime" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
RUNTIME_DIR=$(quote_sh "$REMOTE_RUNTIME_DIR")
export PTBDTOOL_ROOT="\$RUNTIME_DIR"
export PTBD_INSTALL_ROOT="\$RUNTIME_DIR"
export PATH="\$RUNTIME_DIR/bin:\$PATH"
exec "\$RUNTIME_DIR/bdtool" "\$@"
EOS
chmod +x "\$work_dir/ptbd-runtime"

if [ "\$work_dir" != "\$runtime_dir" ]; then
  if [ -d "\$runtime_dir" ]; then
    rm -rf "\$work_dir"
  else
    mv "\$work_dir" "\$runtime_dir"
  fi
fi

rm -f "\$archive_path"
[ -x "\$launcher_path" ]
EOF
)

run_remote_sh "$REMOTE_PREPARE_SCRIPT"
log "remote runtime ready: $REMOTE_LAUNCHER"
printf '%s\n' "$REMOTE_LAUNCHER"
