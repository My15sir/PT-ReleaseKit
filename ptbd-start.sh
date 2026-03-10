#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC2317
# This function is only reached via the ERR trap.
on_err() {
  local line="${1:-unknown}"
  local rc="${2:-1}"
  echo "[ERROR] Start workflow failed at line ${line} (rc=${rc})" >&2
  exit "$rc"
}
trap 'on_err "${LINENO}" "$?"' ERR

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

  [[ -n "${PTBDTOOL_ROOT:-}" ]] && candidates+=("${PTBDTOOL_ROOT}")
  [[ -n "${PTBD_INSTALL_ROOT:-}" ]] && candidates+=("${PTBD_INSTALL_ROOT}")
  candidates+=(
    "$script_dir"
    "$script_dir/.."
    "/opt/PT-BDtool"
    "$HOME/.local/share/pt-bdtool/PT-BDtool-app"
  )

  for candidate in "${candidates[@]}"; do
    [[ -n "$candidate" ]] || continue
    if [[ -f "$candidate/lib/ui.sh" && ( -x "$candidate/bdtool" || -x "$candidate/bdtool.sh" ) ]]; then
      (
        cd -P "$candidate" 2>/dev/null && pwd
      )
      return 0
    fi
  done
  return 1
}

SCRIPT_PATH="$(resolve_script_path "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"
APP_ROOT="$(find_app_root "$SCRIPT_DIR" || true)"
if [[ -n "$APP_ROOT" && -f "$APP_ROOT/lib/ui.sh" ]]; then
  # shellcheck disable=SC1091
  source "$APP_ROOT/lib/ui.sh"
  setup_bundle_runtime "$APP_ROOT"
fi

run_beginner_entry() {
  if [[ -n "$APP_ROOT" && -x "$APP_ROOT/ptbd" ]]; then
    "$APP_ROOT/ptbd" "$@"
    return $?
  fi

  if command -v ptbd >/dev/null 2>&1; then
    ptbd "$@"
    return $?
  fi

  if [[ -n "$APP_ROOT" && -x "$APP_ROOT/bdtool" ]]; then
    "$APP_ROOT/bdtool" "$@"
    return $?
  fi

  if [[ -n "$APP_ROOT" && -x "$APP_ROOT/bdtool.sh" ]]; then
    "$APP_ROOT/bdtool.sh" "$@"
    return $?
  fi

  if command -v bdtool >/dev/null 2>&1; then
    bdtool "$@"
    return $?
  fi

  echo "[ERROR] Cannot find PT-BDtool entrypoint." >&2
  echo "[HINT] Reinstall from project root: bash install.sh --offline" >&2
  echo "Tried: \`ptbd\`, \`${APP_ROOT:-$SCRIPT_DIR}/ptbd\`, \`bdtool\`, \`${APP_ROOT:-$SCRIPT_DIR}/bdtool\`, \`${APP_ROOT:-$SCRIPT_DIR}/bdtool.sh\`" >&2
  return 1
}

echo "================================"
echo "Starting PT-BDtool workflow..."
echo "================================"

run_beginner_entry "$@"
rc=$?
case "${1:-}" in
  -h|--help|--setup|--show-config) exit "$rc" ;;
esac
if [[ -t 0 && -t 1 ]]; then
  echo
  if [[ "$rc" -eq 0 ]]; then
    echo "处理结束。按回车关闭。"
  else
    echo "执行失败（rc=$rc）。按回车关闭。"
  fi
  read -r _ < /dev/tty || true
fi
exit "$rc"
