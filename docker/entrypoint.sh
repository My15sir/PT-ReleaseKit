#!/usr/bin/env sh
set -eu

umask 077

: "${PTBD_WEB_HOST:=0.0.0.0}"
: "${PTBD_WEB_PORT:=8899}"
: "${PTBD_WEB_MODE:=local}"
: "${PTBD_WEB_LOCAL_ROOT:=/media}"
: "${PTBD_WEB_CONFIG:=/config/config.json}"
: "${PTBD_CONTAINER_SAVE_DIR:=/output}"
: "${BDTOOL_DATA_DIR:=/config/runtime}"

export PTBD_WEB_HOST PTBD_WEB_PORT PTBD_WEB_MODE PTBD_WEB_LOCAL_ROOT
export PTBD_WEB_CONFIG PTBD_CONTAINER_SAVE_DIR BDTOOL_DATA_DIR
export PTBDTOOL_ROOT=/opt/PT-BDtool
export PTBD_INSTALL_ROOT=/opt/PT-BDtool

ensure_writable_dir() {
  target="$1"
  if ! mkdir -p "$target" 2>/dev/null || [ ! -w "$target" ]; then
    printf '%s\n' \
      "PT ReleaseKit cannot write $target as uid=$(id -u) gid=$(id -g)." \
      "Create the host config/output directories and assign them to PTBD_UID:PTBD_GID before starting Compose." >&2
    exit 1
  fi
}

ensure_writable_dir "$(dirname "$PTBD_WEB_CONFIG")"
ensure_writable_dir "$PTBD_CONTAINER_SAVE_DIR"
ensure_writable_dir "$BDTOOL_DATA_DIR"

if [ -e "$PTBD_WEB_CONFIG" ] && [ ! -w "$PTBD_WEB_CONFIG" ]; then
  printf 'PT ReleaseKit cannot update config file %s as uid=%s gid=%s.\n' \
    "$PTBD_WEB_CONFIG" "$(id -u)" "$(id -g)" >&2
  exit 1
fi

if [ ! -f "$PTBD_WEB_CONFIG" ]; then
  python3 - "$PTBD_WEB_CONFIG" <<'PY'
import json
import os
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
payload = {
    "mode": "local",
    "local_root": os.environ["PTBD_WEB_LOCAL_ROOT"],
    "remote_host": "root@your-vps",
    "remote_port": "22",
    "remote_password": "",
    "remote_cmd": "pt",
    "remote_bootstrap": True,
    "save_dir": os.environ["PTBD_CONTAINER_SAVE_DIR"],
    "scan_include": "",
    "scan_exclude": "",
    "scan_full": False,
    "audio_spectrum_mode": "single",
    "audio_spectrum_backend": "auto",
    "audio_spectrum_combined_track_seconds": "12",
    "auto_cleanup": True,
}
config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
config_path.chmod(0o600)
PY
fi

exec python3 /opt/PT-BDtool/ptbd-web.py \
  --host "$PTBD_WEB_HOST" \
  --port "$PTBD_WEB_PORT" \
  "$@"
