#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
PKG_ROOT="$ROOT_DIR/.tmp-dist/PT-BDtool-linux-amd64"
OUT_TAR="$DIST_DIR/PT-BDtool-linux-amd64.tar.gz"

log() { printf '[build-bundle] %s\n' "$*"; }

bundle_ready() {
  local bundle_root="$ROOT_DIR/third_party/bundle/linux-amd64"
  local required=""
  local required_paths=(
    "$bundle_root/bin/ffmpeg"
    "$bundle_root/bin/ffprobe"
    "$bundle_root/bin/mediainfo"
    "$bundle_root/bin/BDInfo"
    "$bundle_root/lib"
  )
  for required in "${required_paths[@]}"; do
    [[ -e "$required" ]] || return 1
  done
  return 0
}

ensure_bundle() {
  if bundle_ready; then
    log "reuse existing local bundle: $ROOT_DIR/third_party/bundle/linux-amd64"
    return 0
  fi

  if [[ -f "$SCRIPT_DIR/ensure-bundle.py" ]] && command -v python3 >/dev/null 2>&1; then
    log "local bundle missing; trying GitHub Release asset"
    if python3 "$SCRIPT_DIR/ensure-bundle.py" && bundle_ready; then
      return 0
    fi
    log "release asset restore failed; fall back to local fetch-deps"
  fi

  bash "$SCRIPT_DIR/fetch-deps.sh"
  bundle_ready
}

rm -rf "$ROOT_DIR/.tmp-dist"
mkdir -p "$PKG_ROOT" "$DIST_DIR"

ensure_bundle || {
  echo "[build-bundle][ERROR] linux-amd64 bundle is still incomplete" >&2
  exit 1
}

cp -f "$ROOT_DIR/bdtool" "$PKG_ROOT/bdtool"
cp -f "$ROOT_DIR/bdtool.sh" "$PKG_ROOT/bdtool.sh"
cp -f "$ROOT_DIR/ptbd" "$PKG_ROOT/ptbd"
cp -f "$ROOT_DIR/ptbd-gui" "$PKG_ROOT/ptbd-gui"
cp -f "$ROOT_DIR/ptbd-gui.py" "$PKG_ROOT/ptbd-gui.py"
cp -f "$ROOT_DIR/ptbd_remote_backend.py" "$PKG_ROOT/ptbd_remote_backend.py"
cp -f "$ROOT_DIR/install.sh" "$PKG_ROOT/install.sh"
cp -f "$ROOT_DIR/ptbd-start.sh" "$PKG_ROOT/ptbd-start.sh"
cp -f "$ROOT_DIR/ptbd-remote.sh" "$PKG_ROOT/ptbd-remote.sh"
cp -f "$ROOT_DIR/ptbd-remote-start.sh" "$PKG_ROOT/ptbd-remote-start.sh"
cp -f "$ROOT_DIR/PT-BDtool.desktop" "$PKG_ROOT/PT-BDtool.desktop"
cp -f "$ROOT_DIR/PT-BDtool.command" "$PKG_ROOT/PT-BDtool.command"
cp -f "$ROOT_DIR/PT-BDtool.bat" "$PKG_ROOT/PT-BDtool.bat"
cp -f "$ROOT_DIR/README.md" "$PKG_ROOT/README.md"
mkdir -p "$PKG_ROOT/lib" "$PKG_ROOT/scripts" "$PKG_ROOT/third_party/bundle/linux-amd64"
cp -f "$ROOT_DIR/lib/ui.sh" "$PKG_ROOT/lib/ui.sh"
cp -f "$ROOT_DIR/scripts/deps.env" "$PKG_ROOT/scripts/deps.env"
cp -f "$ROOT_DIR/scripts/ensure-bundle.py" "$PKG_ROOT/scripts/ensure-bundle.py" 2>/dev/null || true
cp -f "$ROOT_DIR/scripts/fetch-deps.sh" "$PKG_ROOT/scripts/fetch-deps.sh"
cp -f "$ROOT_DIR/scripts/prepare-remote-runtime.sh" "$PKG_ROOT/scripts/prepare-remote-runtime.sh"
cp -f "$ROOT_DIR/scripts/remote-upload-server.py" "$PKG_ROOT/scripts/remote-upload-server.py"
cp -f "$ROOT_DIR/scripts/update-deps.sh" "$PKG_ROOT/scripts/update-deps.sh" 2>/dev/null || true
cp -a "$ROOT_DIR/third_party/bundle/linux-amd64/bin" "$PKG_ROOT/third_party/bundle/linux-amd64/"
cp -a "$ROOT_DIR/third_party/bundle/linux-amd64/lib" "$PKG_ROOT/third_party/bundle/linux-amd64/"
chmod +x "$PKG_ROOT/bdtool" "$PKG_ROOT/bdtool.sh" "$PKG_ROOT/ptbd" "$PKG_ROOT/ptbd-gui" "$PKG_ROOT/ptbd-gui.py" "$PKG_ROOT/ptbd_remote_backend.py" "$PKG_ROOT/install.sh" "$PKG_ROOT/ptbd-start.sh" "$PKG_ROOT/ptbd-remote.sh" "$PKG_ROOT/ptbd-remote-start.sh" "$PKG_ROOT/PT-BDtool.command" "$PKG_ROOT/scripts/ensure-bundle.py" "$PKG_ROOT/scripts/fetch-deps.sh" "$PKG_ROOT/scripts/prepare-remote-runtime.sh" "$PKG_ROOT/scripts/remote-upload-server.py" 2>/dev/null || true

rm -f "$OUT_TAR"
tar -czf "$OUT_TAR" -C "$ROOT_DIR/.tmp-dist" PT-BDtool-linux-amd64

log "created: $OUT_TAR"
log "package key files:"
tar -tzf "$OUT_TAR" | grep -E 'third_party/bundle/linux-amd64/bin/(ffmpeg|ffprobe|mediainfo|BDInfo)$|(^PT-BDtool-linux-amd64/(bdtool|bdtool.sh|install.sh|ptbd-start.sh)$)' | sed 's/^/  - /'
