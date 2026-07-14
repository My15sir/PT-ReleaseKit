set -eu

if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
fi

has_cmd() {
  if command -v "$1" >/dev/null 2>&1; then
    printf '1'
  else
    printf '0'
  fi
}

has_py_mod() {
  if python3 - "$1" <<'PY' >/dev/null 2>&1
import importlib.util
import sys
raise SystemExit(0 if importlib.util.find_spec(sys.argv[1]) else 1)
PY
  then
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
printf 'REMOTE_HAS_NUMPY=%s\n' "$(has_py_mod numpy)"
printf 'REMOTE_HAS_PIL=%s\n' "$(has_py_mod PIL)"
printf 'REMOTE_HAS_BDINFO=%s\n' "$(has_cmd BDInfo)"
printf 'REMOTE_HAS_BD_INFO=%s\n' "$(has_cmd bd_info)"
