#!/usr/bin/env bash
set -euo pipefail

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

show_error() {
  local message="$1"
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display alert \"PT-BDtool\" message \"${message//\"/\\\"}\" as critical" >/dev/null 2>&1 || true
  fi
  printf '[ERROR] %s\n' "$message" >&2
}

SCRIPT_PATH="$(resolve_script_path "$0")"
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"
LAUNCHER="$SCRIPT_DIR/ptbd-gui"

if [[ ! -x "$LAUNCHER" ]]; then
  show_error "找不到 ptbd-gui。请确认你是在完整的 PT-BDtool 目录里双击这个文件。"
  read -r -p "按回车关闭..." _ < /dev/tty || true
  exit 1
fi

cd "$SCRIPT_DIR"
nohup "$LAUNCHER" "$@" >/tmp/ptbd-gui-launch.log 2>&1 &
disown || true
exit 0
