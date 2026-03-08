#!/usr/bin/env bash
set -euo pipefail

START_TS="$(date +%s)"
SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
case "$SCRIPT_SOURCE" in
  ""|"-"|/dev/fd/*|/proc/self/fd/*|/dev/stdin)
    cat >&2 <<'EOF'
[ERROR] install.sh is running from a file descriptor/stdin path and cannot resolve offline bundle files.
[ERROR] Please run install.sh from a local PT-BDtool directory or extracted release bundle.
[HINT]  git clone https://github.com/My15sir/PT-BDtool.git && cd PT-BDtool && bash install.sh --offline
EOF
    exit 2
    ;;
esac
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd)"
LANG_OVERRIDE=""
NON_INTERACTIVE=0
NO_LAUNCH="${PTBD_INSTALL_NO_LAUNCH:-0}"
COPIED_COUNT=0
SKIPPED_COUNT=0

log() { printf '[install] %s\n' "$*"; }
err() { printf '[install][ERROR] %s\n' "$*" >&2; }

print_bootstrap_commands() {
  cat >&2 <<'EOF'
[HINT] Copy-paste (normal user):
  cd ~
  git clone https://github.com/My15sir/PT-BDtool.git
  cd PT-BDtool
  bash scripts/fetch-deps.sh
  bash scripts/build-bundle.sh
  bash install.sh --offline

[HINT] Copy-paste (root/sudo):
  cd /opt
  sudo git clone https://github.com/My15sir/PT-BDtool.git
  cd PT-BDtool
  sudo bash scripts/fetch-deps.sh
  sudo bash scripts/build-bundle.sh
  sudo bash install.sh --offline
EOF
}

preflight_install_context() {
  local missing=0
  local req=""
  local required_project_files=(
    "$SCRIPT_DIR/bdtool"
    "$SCRIPT_DIR/bdtool.sh"
    "$SCRIPT_DIR/ptbd"
    "$SCRIPT_DIR/ptbd-gui"
    "$SCRIPT_DIR/ptbd-gui.py"
    "$SCRIPT_DIR/ptbd-start.sh"
    "$SCRIPT_DIR/ptbd-remote.sh"
    "$SCRIPT_DIR/ptbd-remote-start.sh"
    "$SCRIPT_DIR/PT-BDtool.desktop"
    "$SCRIPT_DIR/PT-BDtool.command"
    "$SCRIPT_DIR/PT-BDtool.bat"
    "$SCRIPT_DIR/lib/ui.sh"
    "$SCRIPT_DIR/lib/i18n.sh"
    "$SCRIPT_DIR/scripts/fetch-deps.sh"
    "$SCRIPT_DIR/scripts/build-bundle.sh"
    "$SCRIPT_DIR/scripts/remote-upload-server.py"
    "$SCRIPT_DIR/third_party/bundle/linux-amd64/bin"
  )

  for req in "${required_project_files[@]}"; do
    if [[ ! -e "$req" ]]; then
      err "missing required project file: $req"
      missing=1
    fi
  done

  if [[ "$missing" -ne 0 ]]; then
    err "install.sh must run from a complete local PT-BDtool repository or extracted offline bundle."
    print_bootstrap_commands
    exit 1
  fi

  if [[ "$PWD" != "$SCRIPT_DIR" ]]; then
    log "current directory is not project root; using script dir: $SCRIPT_DIR"
    log "if you see 'scripts/*.sh: No such file or directory', run: cd \"$SCRIPT_DIR\""
  fi
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

elapsed_since() {
  local since_ts="$1"
  local now_ts
  now_ts="$(date +%s)"
  printf '%ss' "$((now_ts - since_ts))"
}

copy_if_changed() {
  local src="$1"
  local dst="$2"
  local label="$3"
  mkdir -p "$(dirname "$dst")"
  if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then
    log "skip (unchanged): $label"
    SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
    return 0
  fi
  cp -f "$src" "$dst"
  log "copied: $label"
  COPIED_COUNT=$((COPIED_COUNT + 1))
}

bundle_dep_status() {
  local missing=0
  local item=""
  for item in "$@"; do
    if [[ -x "$item" ]]; then
      log "dependency present: $(basename "$item") ($item)"
    else
      err "dependency missing: $(basename "$item") ($item)"
      missing=1
    fi
  done
  return "$missing"
}

resolve_effective_home() {
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER:-}" != "root" ]]; then
    local sudo_home=""
    sudo_home="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6 || true)"
    if [[ -n "$sudo_home" && -d "$sudo_home" ]]; then
      printf '%s\n' "$sudo_home"
      return 0
    fi
  fi

  if [[ -n "${HOME:-}" ]]; then
    printf '%s\n' "$HOME"
    return 0
  fi

  return 1
}

should_skip_bundle_sync() {
  local src_bundle="$1"
  local dst_bundle="$2"
  local bin_name="" src_bin="" dst_bin="" src_sum="" dst_sum=""
  [[ -d "$dst_bundle/bin" && -d "$dst_bundle/lib" ]] || return 1
  find "$dst_bundle/lib" -maxdepth 1 -type f | grep -q . || return 1

  for bin_name in ffmpeg ffprobe mediainfo BDInfo; do
    src_bin="$src_bundle/bin/$bin_name"
    dst_bin="$dst_bundle/bin/$bin_name"
    [[ -x "$src_bin" && -x "$dst_bin" ]] || return 1
    src_sum="$(sha256_file "$src_bin")"
    dst_sum="$(sha256_file "$dst_bin")"
    [[ "$src_sum" == "$dst_sum" ]] || return 1
  done
  return 0
}

sync_bundle() {
  local src_bundle="$1"
  local dst_bundle="$2"
  if should_skip_bundle_sync "$src_bundle" "$dst_bundle"; then
    log "skip (bundle cached): $dst_bundle"
    SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
    return 0
  fi

  mkdir -p "$dst_bundle"
  cp -a "$src_bundle/bin" "$dst_bundle/"
  cp -a "$src_bundle/lib" "$dst_bundle/"
  log "copied: bundle bin/lib -> $dst_bundle"
  COPIED_COUNT=$((COPIED_COUNT + 1))
}

install_entrypoints() {
  local install_root="$1"
  local bin_dir="$2"
  local bdtool_link="$bin_dir/bdtool"
  local start_link="$bin_dir/ptbd-start"
  local easy_link="$bin_dir/ptbd"
  local gui_link="$bin_dir/ptbd-gui"
  local pt_link="$bin_dir/pt"
  local pts_link="$bin_dir/pts"
  local remote_link="$bin_dir/ptbd-remote"
  local remote_start_link="$bin_dir/ptbd-remote-start"

  mkdir -p "$bin_dir"
  # Force-replace stale copied wrappers (regular files) from old versions.
  [[ -e "$bdtool_link" && ! -L "$bdtool_link" ]] && rm -f "$bdtool_link"
  [[ -e "$start_link" && ! -L "$start_link" ]] && rm -f "$start_link"
  [[ -e "$easy_link" && ! -L "$easy_link" ]] && rm -f "$easy_link"
  [[ -e "$gui_link" && ! -L "$gui_link" ]] && rm -f "$gui_link"
  [[ -e "$pt_link" && ! -L "$pt_link" ]] && rm -f "$pt_link"
  [[ -e "$pts_link" && ! -L "$pts_link" ]] && rm -f "$pts_link"
  [[ -e "$remote_link" && ! -L "$remote_link" ]] && rm -f "$remote_link"
  [[ -e "$remote_start_link" && ! -L "$remote_start_link" ]] && rm -f "$remote_start_link"

  ln -sfn "$install_root/bdtool" "$bdtool_link"
  ln -sfn "$install_root/ptbd" "$easy_link"
  ln -sfn "$install_root/ptbd-gui" "$gui_link"
  ln -sfn "$install_root/ptbd-start.sh" "$start_link"
  ln -sfn "$install_root/bdtool" "$pt_link"
  ln -sfn "$install_root/ptbd-start.sh" "$pts_link"
  ln -sfn "$install_root/ptbd-remote.sh" "$remote_link"
  ln -sfn "$install_root/ptbd-remote-start.sh" "$remote_start_link"
}

install_runtime_wrappers() {
  local install_root="$1"
  local bin_dir="$2"
  local bundle_root="$install_root/third_party/bundle/linux-amd64"
  local bdinfo_wrapper="$bin_dir/BDInfo"
  mkdir -p "$bin_dir"
  cat > "$bdinfo_wrapper" <<EOF
#!/usr/bin/env bash
set -euo pipefail
BUNDLE_ROOT="$bundle_root"
exec "\$BUNDLE_ROOT/bin/BDInfo" "\$@"
EOF
  chmod +x "$bdinfo_wrapper"
  log "installed runtime wrapper: $bdinfo_wrapper"
}

post_install_self_check() {
  local install_root="$1"
  local bin_dir="$2"
  local fail=0
  local f=""
  local resolved_bdtool=""
  local resolved_start=""
  local resolved_pt=""
  local resolved_pts=""
  local self_check_path="$bin_dir:${PATH:-}"
  local required_files=(
    "$install_root/bdtool"
    "$install_root/bdtool.sh"
    "$install_root/ptbd"
    "$install_root/ptbd-gui"
    "$install_root/ptbd-gui.py"
    "$install_root/ptbd-start.sh"
    "$install_root/ptbd-remote.sh"
    "$install_root/ptbd-remote-start.sh"
    "$install_root/lib/ui.sh"
    "$install_root/lib/i18n.sh"
    "$install_root/scripts/remote-upload-server.py"
    "$install_root/third_party/bundle/linux-amd64/bin/ffmpeg"
    "$install_root/third_party/bundle/linux-amd64/bin/ffprobe"
    "$install_root/third_party/bundle/linux-amd64/bin/mediainfo"
    "$install_root/third_party/bundle/linux-amd64/bin/BDInfo"
  )

  log "post-install self-check start"
  for f in "${required_files[@]}"; do
    if [[ -e "$f" ]]; then
      log "self-check ok: $f"
    else
      err "self-check missing: $f"
      fail=1
    fi
  done

  if [[ -x "$bin_dir/bdtool" ]]; then
    log "self-check ok: entrypoint $bin_dir/bdtool"
  else
    err "self-check missing entrypoint: $bin_dir/bdtool"
    fail=1
  fi
  if [[ -x "$bin_dir/ptbd-start" ]]; then
    log "self-check ok: entrypoint $bin_dir/ptbd-start"
  else
    err "self-check missing entrypoint: $bin_dir/ptbd-start"
    fail=1
  fi
  if [[ -x "$bin_dir/ptbd" ]]; then
    log "self-check ok: entrypoint $bin_dir/ptbd"
  else
    err "self-check missing entrypoint: $bin_dir/ptbd"
    fail=1
  fi
  if [[ -x "$bin_dir/ptbd-gui" ]]; then
    log "self-check ok: entrypoint $bin_dir/ptbd-gui"
  else
    err "self-check missing entrypoint: $bin_dir/ptbd-gui"
    fail=1
  fi
  if [[ -x "$bin_dir/pt" ]]; then
    log "self-check ok: entrypoint $bin_dir/pt"
  else
    err "self-check missing entrypoint: $bin_dir/pt"
    fail=1
  fi
  if [[ -x "$bin_dir/pts" ]]; then
    log "self-check ok: entrypoint $bin_dir/pts"
  else
    err "self-check missing entrypoint: $bin_dir/pts"
    fail=1
  fi
  if [[ -x "$bin_dir/ptbd-remote" ]]; then
    log "self-check ok: entrypoint $bin_dir/ptbd-remote"
  else
    err "self-check missing entrypoint: $bin_dir/ptbd-remote"
    fail=1
  fi
  if [[ -x "$bin_dir/ptbd-remote-start" ]]; then
    log "self-check ok: entrypoint $bin_dir/ptbd-remote-start"
  else
    err "self-check missing entrypoint: $bin_dir/ptbd-remote-start"
    fail=1
  fi
  if [[ -x "$bin_dir/BDInfo" ]]; then
    log "self-check ok: runtime wrapper $bin_dir/BDInfo"
  else
    err "self-check missing runtime wrapper: $bin_dir/BDInfo"
    fail=1
  fi

  if ! "$install_root/bdtool" --help >/dev/null 2>&1; then
    err "self-check failed: $install_root/bdtool --help"
    fail=1
  fi
  if ! "$install_root/ptbd-start.sh" --help >/dev/null 2>&1; then
    err "self-check failed: $install_root/ptbd-start.sh --help"
    fail=1
  fi
  if ! PATH="$self_check_path" "$bin_dir/bdtool" --help >/dev/null 2>&1; then
    err "self-check failed: $bin_dir/bdtool --help"
    fail=1
  fi
  if ! PATH="$self_check_path" "$bin_dir/ptbd-start" --help >/dev/null 2>&1; then
    err "self-check failed: $bin_dir/ptbd-start --help"
    fail=1
  fi
  if ! PATH="$self_check_path" "$bin_dir/ptbd" --help >/dev/null 2>&1; then
    err "self-check failed: $bin_dir/ptbd --help"
    fail=1
  fi
  if ! PATH="$self_check_path" "$bin_dir/ptbd-gui" --self-check >/dev/null 2>&1; then
    err "self-check failed: $bin_dir/ptbd-gui --self-check"
    fail=1
  fi
  if ! PATH="$self_check_path" "$bin_dir/pt" --help >/dev/null 2>&1; then
    err "self-check failed: $bin_dir/pt --help"
    fail=1
  fi
  if ! PATH="$self_check_path" "$bin_dir/pts" --help >/dev/null 2>&1; then
    err "self-check failed: $bin_dir/pts --help"
    fail=1
  fi
  if ! PATH="$self_check_path" "$bin_dir/ptbd-remote" --help >/dev/null 2>&1; then
    err "self-check failed: $bin_dir/ptbd-remote --help"
    fail=1
  fi
  if ! PATH="$self_check_path" "$bin_dir/ptbd-remote-start" --help >/dev/null 2>&1; then
    err "self-check failed: $bin_dir/ptbd-remote-start --help"
    fail=1
  fi
  if ! PATH="$self_check_path" command -v BDInfo >/dev/null 2>&1; then
    err "self-check failed: command -v BDInfo"
    fail=1
  fi

  resolved_bdtool="$(command -v bdtool 2>/dev/null || true)"
  if [[ -n "$resolved_bdtool" && "$resolved_bdtool" != "$bin_dir/bdtool" ]]; then
    log "PATH warning: current shell still resolves bdtool -> $resolved_bdtool"
    log "copy-paste fix: hash -r && export PATH=\"$bin_dir:\$PATH\""
  fi
  resolved_start="$(command -v ptbd-start 2>/dev/null || true)"
  if [[ -n "$resolved_start" && "$resolved_start" != "$bin_dir/ptbd-start" ]]; then
    log "PATH warning: current shell still resolves ptbd-start -> $resolved_start"
    log "copy-paste fix: hash -r && export PATH=\"$bin_dir:\$PATH\""
  fi
  local resolved_easy=""
  resolved_easy="$(command -v ptbd 2>/dev/null || true)"
  if [[ -n "$resolved_easy" && "$resolved_easy" != "$bin_dir/ptbd" ]]; then
    log "PATH warning: current shell still resolves ptbd -> $resolved_easy"
    log "copy-paste fix: hash -r && export PATH=\"$bin_dir:\$PATH\""
  fi
  local resolved_gui=""
  resolved_gui="$(command -v ptbd-gui 2>/dev/null || true)"
  if [[ -n "$resolved_gui" && "$resolved_gui" != "$bin_dir/ptbd-gui" ]]; then
    log "PATH warning: current shell still resolves ptbd-gui -> $resolved_gui"
    log "copy-paste fix: hash -r && export PATH=\"$bin_dir:\$PATH\""
  fi
  resolved_pt="$(command -v pt 2>/dev/null || true)"
  if [[ -n "$resolved_pt" && "$resolved_pt" != "$bin_dir/pt" ]]; then
    log "PATH warning: current shell still resolves pt -> $resolved_pt"
    log "copy-paste fix: hash -r && export PATH=\"$bin_dir:\$PATH\""
  fi
  resolved_pts="$(command -v pts 2>/dev/null || true)"
  if [[ -n "$resolved_pts" && "$resolved_pts" != "$bin_dir/pts" ]]; then
    log "PATH warning: current shell still resolves pts -> $resolved_pts"
    log "copy-paste fix: hash -r && export PATH=\"$bin_dir:\$PATH\""
  fi
  local resolved_remote=""
  resolved_remote="$(command -v ptbd-remote 2>/dev/null || true)"
  if [[ -n "$resolved_remote" && "$resolved_remote" != "$bin_dir/ptbd-remote" ]]; then
    log "PATH warning: current shell still resolves ptbd-remote -> $resolved_remote"
    log "copy-paste fix: hash -r && export PATH=\"$bin_dir:\$PATH\""
  fi
  local resolved_remote_start=""
  resolved_remote_start="$(command -v ptbd-remote-start 2>/dev/null || true)"
  if [[ -n "$resolved_remote_start" && "$resolved_remote_start" != "$bin_dir/ptbd-remote-start" ]]; then
    log "PATH warning: current shell still resolves ptbd-remote-start -> $resolved_remote_start"
    log "copy-paste fix: hash -r && export PATH=\"$bin_dir:\$PATH\""
  fi

  if [[ "$fail" -ne 0 ]]; then
    err "post-install self-check failed."
    cat >&2 <<EOF
[HINT] Copy-paste fix:
  cd "$SCRIPT_DIR"
  rm -f "$bin_dir/bdtool" "$bin_dir/ptbd-start" "$bin_dir/pt" "$bin_dir/pts"
  bash install.sh --offline
  "$bin_dir/bdtool" --help
EOF
    {
      echo "[DIAG] command -v bdtool: $(command -v bdtool 2>/dev/null || echo missing)"
      echo "[DIAG] command -v ptbd: $(command -v ptbd 2>/dev/null || echo missing)"
      echo "[DIAG] command -v ptbd-gui: $(command -v ptbd-gui 2>/dev/null || echo missing)"
      echo "[DIAG] command -v ptbd-start: $(command -v ptbd-start 2>/dev/null || echo missing)"
      echo "[DIAG] command -v pt: $(command -v pt 2>/dev/null || echo missing)"
      echo "[DIAG] command -v pts: $(command -v pts 2>/dev/null || echo missing)"
      echo "[DIAG] command -v ptbd-remote: $(command -v ptbd-remote 2>/dev/null || echo missing)"
      echo "[DIAG] command -v ptbd-remote-start: $(command -v ptbd-remote-start 2>/dev/null || echo missing)"
      [[ -e "$bin_dir/bdtool" ]] && ls -l "$bin_dir/bdtool" || echo "[DIAG] missing: $bin_dir/bdtool"
      [[ -e "$bin_dir/ptbd" ]] && ls -l "$bin_dir/ptbd" || echo "[DIAG] missing: $bin_dir/ptbd"
      [[ -e "$bin_dir/ptbd-gui" ]] && ls -l "$bin_dir/ptbd-gui" || echo "[DIAG] missing: $bin_dir/ptbd-gui"
      [[ -e "$bin_dir/ptbd-start" ]] && ls -l "$bin_dir/ptbd-start" || echo "[DIAG] missing: $bin_dir/ptbd-start"
      [[ -e "$bin_dir/pt" ]] && ls -l "$bin_dir/pt" || echo "[DIAG] missing: $bin_dir/pt"
      [[ -e "$bin_dir/pts" ]] && ls -l "$bin_dir/pts" || echo "[DIAG] missing: $bin_dir/pts"
      [[ -e "$bin_dir/ptbd-remote" ]] && ls -l "$bin_dir/ptbd-remote" || echo "[DIAG] missing: $bin_dir/ptbd-remote"
      [[ -e "$bin_dir/ptbd-remote-start" ]] && ls -l "$bin_dir/ptbd-remote-start" || echo "[DIAG] missing: $bin_dir/ptbd-remote-start"
    } >&2
    exit 1
  fi

  log "post-install self-check done: PASS"
}

usage() {
  cat <<'USAGE'
Usage: bash install.sh [--offline] [--lang zh|en] [--non-interactive] [--no-launch]

Options:
  --offline          Offline install only (default)
  --online-legacy    Kept for compatibility; not supported anymore
  --lang <code>      Pass language to bdtool after install
  --non-interactive  Run installed bdtool in non-interactive mode
  --no-launch        Skip auto-launching menu after install
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --offline)
      shift
      ;;
    --online-legacy)
      echo "[ERROR] Online package-manager installation is disabled. Use offline bundle only." >&2
      exit 2
      ;;
    --lang)
      LANG_OVERRIDE="${2:-}"
      shift 2
      ;;
    --lang=*)
      LANG_OVERRIDE="${1#*=}"
      shift
      ;;
    --non-interactive)
      NON_INTERACTIVE=1
      shift
      ;;
    --no-launch)
      NO_LAUNCH=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      shift
      ;;
  esac
done

preflight_install_context

required_bundle_files=(
  "$SCRIPT_DIR/third_party/bundle/linux-amd64/bin/ffmpeg"
  "$SCRIPT_DIR/third_party/bundle/linux-amd64/bin/ffprobe"
  "$SCRIPT_DIR/third_party/bundle/linux-amd64/bin/mediainfo"
  "$SCRIPT_DIR/third_party/bundle/linux-amd64/bin/BDInfo"
)

PRECHECK_TS="$(date +%s)"
log "precheck start"
if ! bundle_dep_status "${required_bundle_files[@]}"; then
  err "offline bundle dependencies are incomplete."
  err "Fix option A (if ffmpeg/ffprobe/mediainfo/BDInfo already installed):"
  err "  bash scripts/fetch-deps.sh && bash scripts/build-bundle.sh"
  err "Fix option B (if they are NOT installed): use official release tarball then run install.sh --offline."
  exit 1
fi
log "precheck done (elapsed=$(elapsed_since "$PRECHECK_TS"))"

if [[ -w "/opt" || ${EUID:-$(id -u)} -eq 0 ]]; then
  INSTALL_ROOT="${PTBD_INSTALL_ROOT:-/opt/PT-BDtool}"
else
  INSTALL_ROOT="${PTBD_INSTALL_ROOT:-$HOME/.local/share/pt-bdtool/PT-BDtool-app}"
fi

INSTALL_TS="$(date +%s)"
log "install root: $INSTALL_ROOT"
mkdir -p "$INSTALL_ROOT/lib" "$INSTALL_ROOT/third_party/bundle/linux-amd64"
copy_if_changed "$SCRIPT_DIR/bdtool" "$INSTALL_ROOT/bdtool" "bdtool"
copy_if_changed "$SCRIPT_DIR/bdtool.sh" "$INSTALL_ROOT/bdtool.sh" "bdtool.sh"
copy_if_changed "$SCRIPT_DIR/ptbd" "$INSTALL_ROOT/ptbd" "ptbd"
copy_if_changed "$SCRIPT_DIR/ptbd-gui" "$INSTALL_ROOT/ptbd-gui" "ptbd-gui"
copy_if_changed "$SCRIPT_DIR/ptbd-gui.py" "$INSTALL_ROOT/ptbd-gui.py" "ptbd-gui.py"
copy_if_changed "$SCRIPT_DIR/ptbd-start.sh" "$INSTALL_ROOT/ptbd-start.sh" "ptbd-start.sh"
copy_if_changed "$SCRIPT_DIR/ptbd-remote.sh" "$INSTALL_ROOT/ptbd-remote.sh" "ptbd-remote.sh"
copy_if_changed "$SCRIPT_DIR/ptbd-remote-start.sh" "$INSTALL_ROOT/ptbd-remote-start.sh" "ptbd-remote-start.sh"
copy_if_changed "$SCRIPT_DIR/install.sh" "$INSTALL_ROOT/install.sh" "install.sh"
copy_if_changed "$SCRIPT_DIR/PT-BDtool.command" "$INSTALL_ROOT/PT-BDtool.command" "PT-BDtool.command"
copy_if_changed "$SCRIPT_DIR/PT-BDtool.bat" "$INSTALL_ROOT/PT-BDtool.bat" "PT-BDtool.bat"
if [[ -f "$SCRIPT_DIR/README.md" ]]; then
  copy_if_changed "$SCRIPT_DIR/README.md" "$INSTALL_ROOT/README.md" "README.md"
else
  log "skip (missing optional file): README.md"
  SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
fi
copy_if_changed "$SCRIPT_DIR/lib/ui.sh" "$INSTALL_ROOT/lib/ui.sh" "lib/ui.sh"
copy_if_changed "$SCRIPT_DIR/lib/i18n.sh" "$INSTALL_ROOT/lib/i18n.sh" "lib/i18n.sh"
mkdir -p "$INSTALL_ROOT/scripts"
copy_if_changed "$SCRIPT_DIR/scripts/remote-upload-server.py" "$INSTALL_ROOT/scripts/remote-upload-server.py" "scripts/remote-upload-server.py"
sync_bundle "$SCRIPT_DIR/third_party/bundle/linux-amd64" "$INSTALL_ROOT/third_party/bundle/linux-amd64"
chmod +x "$INSTALL_ROOT/bdtool" "$INSTALL_ROOT/bdtool.sh" "$INSTALL_ROOT/ptbd" "$INSTALL_ROOT/ptbd-gui" "$INSTALL_ROOT/ptbd-gui.py" "$INSTALL_ROOT/ptbd-start.sh" "$INSTALL_ROOT/ptbd-remote.sh" "$INSTALL_ROOT/ptbd-remote-start.sh" "$INSTALL_ROOT/install.sh" "$INSTALL_ROOT/scripts/remote-upload-server.py" "$INSTALL_ROOT/PT-BDtool.command"

install_desktop_launcher() {
  local install_root="$1"
  local bin_dir="$2"
  local user_home=""
  local desktop_dir=""
  local applications_dir=""
  local launcher_path=""
  user_home="$(resolve_effective_home)" || return 0
  applications_dir="$user_home/.local/share/applications"
  desktop_dir="$user_home/Desktop"
  [[ -d "$user_home/桌面" ]] && desktop_dir="$user_home/桌面"
  mkdir -p "$applications_dir"
  launcher_path="$applications_dir/PT-BDtool.desktop"
  cat > "$launcher_path" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=PT-BDtool
Comment=Beginner-friendly PT-BDtool GUI launcher
Exec=$bin_dir/ptbd-gui
Terminal=false
StartupNotify=true
Categories=Utility;
EOF
  chmod +x "$launcher_path"
  if [[ -d "$desktop_dir" && -w "$desktop_dir" ]]; then
    cp -f "$launcher_path" "$desktop_dir/PT-BDtool.desktop"
    chmod +x "$desktop_dir/PT-BDtool.desktop"
    log "installed desktop launcher: $desktop_dir/PT-BDtool.desktop"
  fi
  log "installed desktop launcher: $launcher_path"
}
log "install stage done (elapsed=$(elapsed_since "$INSTALL_TS"), copied=$COPIED_COUNT, skipped=$SKIPPED_COUNT)"

if [[ -w "/usr/local/bin" || ${EUID:-$(id -u)} -eq 0 ]]; then
  BIN_DIR="${PTBD_BIN_DIR:-/usr/local/bin}"
else
  BIN_DIR="${PTBD_BIN_DIR:-$HOME/.local/bin}"
fi
install_entrypoints "$INSTALL_ROOT" "$BIN_DIR"
install_runtime_wrappers "$INSTALL_ROOT" "$BIN_DIR"
install_desktop_launcher "$INSTALL_ROOT" "$BIN_DIR"
# Refresh command lookup cache so post-check sees the new symlink entrypoints.
hash -r 2>/dev/null || true
if [[ "$BIN_DIR" == "$HOME/.local/bin" ]]; then
  echo "[INFO] Ensure ~/.local/bin is in PATH" >&2
fi

post_install_self_check "$INSTALL_ROOT" "$BIN_DIR"

log "offline install complete: $INSTALL_ROOT"
log "entrypoints: $BIN_DIR/ptbd / $BIN_DIR/ptbd-gui / $BIN_DIR/ptbd-start / $BIN_DIR/bdtool"
log "total elapsed: $(elapsed_since "$START_TS")"

if [[ "$NON_INTERACTIVE" == "1" ]]; then
  if [[ -n "$LANG_OVERRIDE" ]]; then
    exec "$INSTALL_ROOT/bdtool" --non-interactive --lang "$LANG_OVERRIDE"
  fi
  exec "$INSTALL_ROOT/bdtool" --non-interactive
fi

if [[ "$NO_LAUNCH" == "1" ]]; then
  exit 0
fi

if [[ -t 0 && -t 1 ]]; then
  log "interactive terminal detected: launching main menu"
  if [[ -n "$LANG_OVERRIDE" ]]; then
    exec "$INSTALL_ROOT/bdtool" --lang "$LANG_OVERRIDE"
  fi
  exec "$INSTALL_ROOT/bdtool"
fi

log "non-interactive terminal detected: skip auto launch"
if [[ -f "$SCRIPT_DIR/PT-BDtool.desktop" ]]; then
  copy_if_changed "$SCRIPT_DIR/PT-BDtool.desktop" "$INSTALL_ROOT/PT-BDtool.desktop" "PT-BDtool.desktop"
fi
