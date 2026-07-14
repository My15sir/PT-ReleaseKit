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
  if command -v zenity >/dev/null 2>&1; then
    zenity --error --title="PT ReleaseKit" --text="$message" >/dev/null 2>&1 || true
  elif command -v kdialog >/dev/null 2>&1; then
    kdialog --error "$message" >/dev/null 2>&1 || true
  elif command -v osascript >/dev/null 2>&1; then
    osascript -e "display alert \"PT ReleaseKit\" message \"${message//\"/\\\"}\" as critical" >/dev/null 2>&1 || true
  fi
  printf '[ERROR] %s\n' "$message" >&2
}

SCRIPT_PATH="$(resolve_script_path "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"
GUI_LAUNCHER="$SCRIPT_DIR/ptbd-gui"
CLI_LAUNCHER="$SCRIPT_DIR/ptbd-start.sh"

case "${1:-}" in
  -h|--help|help)
    cat <<'EOF'
Usage:
  ./PT-ReleaseKit.sh
  ./PT-ReleaseKit.sh --self-check

What it does:
  - Prefer the GUI launcher in this directory
  - Fallback to the CLI launcher when GUI wrapper is missing
EOF
    exit 0
    ;;
  --self-check)
    printf 'script=%s\n' "$SCRIPT_PATH"
    printf 'script_dir=%s\n' "$SCRIPT_DIR"
    printf 'gui_launcher=%s\n' "$GUI_LAUNCHER"
    printf 'cli_launcher=%s\n' "$CLI_LAUNCHER"
    exit 0
    ;;
esac

if [[ -x "$GUI_LAUNCHER" ]]; then
  exec "$GUI_LAUNCHER" "$@"
fi

if [[ -x "$CLI_LAUNCHER" ]]; then
  exec "$CLI_LAUNCHER" "$@"
fi

show_error "找不到 PT ReleaseKit 启动文件。请确认你是在完整的项目目录里运行。"
exit 1
