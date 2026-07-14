#!/usr/bin/env bash
set -euo pipefail

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
  local user_home="${HOME:-}"
  local -a candidates=()

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
    if [[ -x "$candidate/bdtool" && -f "$candidate/lib/ui.sh" ]]; then
      (
        cd -P "$candidate" 2>/dev/null && pwd
      )
      return 0
    fi
  done
  return 1
}

BT_SCRIPT_PATH="$(bt_resolve_script_path "${BASH_SOURCE[0]:-$0}")"
BT_SCRIPT_DIR="$(cd -P "$(dirname "$BT_SCRIPT_PATH")" && pwd)"
BDTOOL_ROOT="$(bt_find_app_root "$BT_SCRIPT_DIR" || true)"

if [[ -z "$BDTOOL_ROOT" || ! -x "$BDTOOL_ROOT/bdtool" ]]; then
  echo "[ERROR] PT ReleaseKit runtime not found: bdtool" >&2
  echo "[ERROR] Current entry path: $BT_SCRIPT_PATH" >&2
  echo "[HINT] Reinstall from the PT ReleaseKit project root:" >&2
  echo "  cd /path/to/PT-ReleaseKit && bash install.sh --offline" >&2
  exit 127
fi

exec "$BDTOOL_ROOT/bdtool" "$@"
