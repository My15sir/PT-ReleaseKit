#!/usr/bin/env bash
set -euo pipefail

PTBD_REMOTE_HOST="${PTBD_REMOTE_HOST:-}"
PTBD_REMOTE_PORT="${PTBD_REMOTE_PORT:-22}"
PTBD_REMOTE_PASSWORD="${PTBD_REMOTE_PASSWORD:-}"
PTBD_REMOTE_CACHE_ROOT="${PTBD_REMOTE_CACHE_ROOT:-}"

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
  - Upload either a thin PT-BDtool runtime or a bundled linux-amd64 fallback
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
    -o StrictHostKeyChecking=accept-new \
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
    -o StrictHostKeyChecking=accept-new \
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
  local -a required=(
    "$APP_ROOT/bdtool"
    "$APP_ROOT/bdtool.sh"
    "$APP_ROOT/lib/ui.sh"
  )
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

members = [
    (app_root / "bdtool", "bdtool"),
    (app_root / "bdtool.sh", "bdtool.sh"),
    (app_root / "lib" / "ui.sh", "lib/ui.sh"),
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
  local script=""
  script=$(cat <<'EOF'
set -eu

if [ -r /etc/os-release ]; then
  . /etc/os-release
fi

has_cmd() {
  if command -v "$1" >/dev/null 2>&1; then
    printf '1'
  else
    printf '0'
  fi
}

printf 'REMOTE_OS=%s\n' "$(uname -s 2>/dev/null || echo unknown)"
printf 'REMOTE_ARCH=%s\n' "$(uname -m 2>/dev/null || echo unknown)"
printf 'REMOTE_HOME=%s\n' "${HOME:-}"
printf 'REMOTE_ID=%s\n' "${ID:-}"
printf 'REMOTE_ID_LIKE=%s\n' "${ID_LIKE:-}"
printf 'REMOTE_VERSION_ID=%s\n' "${VERSION_ID:-}"
printf 'REMOTE_HAS_TAR=%s\n' "$(has_cmd tar)"
printf 'REMOTE_HAS_BASH=%s\n' "$(has_cmd bash)"
printf 'REMOTE_HAS_PYTHON3=%s\n' "$(has_cmd python3)"
printf 'REMOTE_HAS_CURL=%s\n' "$(has_cmd curl)"
printf 'REMOTE_HAS_FFMPEG=%s\n' "$(has_cmd ffmpeg)"
printf 'REMOTE_HAS_FFPROBE=%s\n' "$(has_cmd ffprobe)"
printf 'REMOTE_HAS_MEDIAINFO=%s\n' "$(has_cmd mediainfo)"
printf 'REMOTE_HAS_BDINFO=%s\n' "$(has_cmd BDInfo)"
printf 'REMOTE_HAS_BD_INFO=%s\n' "$(has_cmd bd_info)"
EOF
)
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
  return 0
}

ensure_remote_system_deps() {
  local script=""
  local output=""
  local rc=0

  script=$(cat <<'EOF'
set -eu

if [ -r /etc/os-release ]; then
  . /etc/os-release
fi

id="${ID:-}"
id_like="${ID_LIKE:-}"

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

word_match() {
  case " $1 " in
    *" $2 "*) return 0 ;;
    *) return 1 ;;
  esac
}

is_debian_like() {
  word_match "$id $id_like" debian || word_match "$id $id_like" ubuntu
}

is_alpine_like() {
  word_match "$id $id_like" alpine
}

as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
    return $?
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo -n "$@"
    return $?
  fi
  return 97
}

enable_ubuntu_universe() {
  if [ "$id" != "ubuntu" ]; then
    return 0
  fi
  if apt-cache show mediainfo >/dev/null 2>&1 && apt-cache show ffmpeg >/dev/null 2>&1; then
    return 0
  fi
  echo "[remote] Ubuntu universe repo looks unavailable; trying to enable it" >&2
  if ! command -v add-apt-repository >/dev/null 2>&1; then
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y software-properties-common >/dev/null
  fi
  if command -v add-apt-repository >/dev/null 2>&1; then
    as_root add-apt-repository -y universe >/dev/null 2>&1 || true
  fi
}

enable_alpine_community() {
  if grep -Eq '^[[:space:]]*[^#].*/community/?[[:space:]]*$' /etc/apk/repositories 2>/dev/null; then
    return 0
  fi
  echo "[remote] Alpine community repo looks unavailable; trying to enable it" >&2
  as_root sh -eu <<'EOS'
tmp_file="$(mktemp)"
cleanup() {
  rm -f "$tmp_file"
}
trap cleanup EXIT

if grep -Eq '^[[:space:]]*#.*\/community/?[[:space:]]*$' /etc/apk/repositories 2>/dev/null; then
  awk '
    /^[[:space:]]*#/ && /\/community\/?[[:space:]]*$/ { sub(/^[[:space:]]*#[[:space:]]*/, "", $0) }
    { print }
  ' /etc/apk/repositories > "$tmp_file"
else
  awk '
    { print }
    /^[[:space:]]*[^#].*\/main\/?[[:space:]]*$/ {
      line=$0
      sub(/\/main\/?[[:space:]]*$/, "/community", line)
      print line
    }
  ' /etc/apk/repositories > "$tmp_file"
fi

cp "$tmp_file" /etc/apk/repositories
EOS
}

missing_required=""
for cmd in tar bash python3 curl ffmpeg ffprobe mediainfo; do
  if ! has_cmd "$cmd"; then
    missing_required="$missing_required $cmd"
  fi
done

need_optional_bd="0"
if ! has_cmd BDInfo && ! has_cmd bd_info; then
  need_optional_bd="1"
fi

if [ -z "${missing_required# }" ] && [ "$need_optional_bd" = "0" ]; then
  echo "status=ready"
  exit 0
fi

if is_debian_like; then
  if ! as_root true >/dev/null 2>&1; then
    echo "status=missing-required-no-privilege"
    exit 0
  fi
  echo "[remote] Debian/Ubuntu detected; installing system packages for PT-BDtool" >&2
  as_root apt-get update >/dev/null
  if ! as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y bash curl python3 tar ffmpeg mediainfo zip >/dev/null 2>&1; then
    enable_ubuntu_universe
    as_root apt-get update >/dev/null
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y bash curl python3 tar ffmpeg mediainfo zip >/dev/null
  fi
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y libbluray-bin >/dev/null 2>&1 || true
  echo "status=installed"
  exit 0
fi

if is_alpine_like; then
  if ! as_root true >/dev/null 2>&1; then
    echo "status=missing-required-no-privilege"
    exit 0
  fi
  echo "[remote] Alpine detected; installing system packages for PT-BDtool" >&2
  if ! as_root apk add --no-cache bash curl python3 tar ffmpeg mediainfo zip >/dev/null 2>&1; then
    enable_alpine_community
    as_root apk update >/dev/null
    as_root apk add --no-cache bash curl python3 tar ffmpeg mediainfo zip >/dev/null
  fi
  as_root apk add --no-cache libbluray >/dev/null 2>&1 || true
  echo "status=installed"
  exit 0
fi

echo "status=unsupported"
EOF
)

  set +e
  output="$(run_remote_sh "$script")"
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
chmod +x "\$work_dir/bdtool" "\$work_dir/bdtool.sh"

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
export PTBDTOOL_ROOT="\\\$RUNTIME_DIR"
export PTBD_INSTALL_ROOT="\\\$RUNTIME_DIR"
export PATH="\\\$RUNTIME_DIR/bin:\\\$PATH"
exec "\\\$RUNTIME_DIR/bdtool" "\\\$@"
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
