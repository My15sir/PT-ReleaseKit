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

run_remote() {
  local script_path=""
  if command -v ptbd-remote >/dev/null 2>&1; then
    ptbd-remote "$@"
    return $?
  fi

  script_path="$(resolve_script_path "${BASH_SOURCE[0]}")"
  local script_dir=""
  script_dir="$(cd -P "$(dirname "$script_path")" && pwd)"
  if [[ -x "$script_dir/ptbd-remote.sh" ]]; then
    "$script_dir/ptbd-remote.sh" "$@"
    return $?
  fi

  echo "[ERROR] 找不到 ptbd-remote"
  return 127
}

run_remote "$@"
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
