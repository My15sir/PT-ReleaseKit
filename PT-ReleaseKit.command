#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "$0")" && pwd)"
APP_BUNDLE="$SCRIPT_DIR/PT-ReleaseKit.app"
LEGACY_APP_BUNDLE="$SCRIPT_DIR/PT-BDtool.app"
LAUNCHER="$SCRIPT_DIR/ptbd-gui"

if [[ -d "$APP_BUNDLE" ]]; then
  open "$APP_BUNDLE" >/dev/null 2>&1 || true
  exit 0
fi

if [[ -d "$LEGACY_APP_BUNDLE" ]]; then
  open "$LEGACY_APP_BUNDLE" >/dev/null 2>&1 || true
  exit 0
fi

if [[ ! -x "$LAUNCHER" ]]; then
  printf '[ERROR] 找不到 ptbd-gui。请从完整的 PT ReleaseKit 目录启动。\n' >&2
  read -r -p "按回车关闭..." _ < /dev/tty || true
  exit 1
fi

cd "$SCRIPT_DIR"
nohup "$LAUNCHER" "$@" >/tmp/ptbd-gui-launch.log 2>&1 &
disown || true
exit 0
