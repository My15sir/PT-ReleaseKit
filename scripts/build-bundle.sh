#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
PACKAGE_DIR_NAME="PT-ReleaseKit-linux-amd64"
PKG_ROOT="$ROOT_DIR/.tmp-dist/$PACKAGE_DIR_NAME"
OUT_TAR="$DIST_DIR/$PACKAGE_DIR_NAME.tar.gz"
OUT_SHA256="$OUT_TAR.sha256"

log() { printf '[build-bundle] %s\n' "$*"; }

write_sha256() {
  local checksum_tmp="${OUT_SHA256}.tmp"
  local archive_dir="${OUT_TAR%/*}"
  local archive_name="${OUT_TAR##*/}"
  [[ "$archive_dir" != "$OUT_TAR" ]] || archive_dir="."
  rm -f "$checksum_tmp"
  if command -v sha256sum >/dev/null 2>&1; then
    if ! (cd "$archive_dir" && sha256sum "$archive_name") > "$checksum_tmp"; then
      rm -f "$checksum_tmp"
      return 1
    fi
  elif command -v shasum >/dev/null 2>&1; then
    if ! (cd "$archive_dir" && shasum -a 256 "$archive_name") > "$checksum_tmp"; then
      rm -f "$checksum_tmp"
      return 1
    fi
  else
    echo "[build-bundle][ERROR] sha256sum or shasum is required to create the checksum" >&2
    return 1
  fi
  mv -f "$checksum_tmp" "$OUT_SHA256"
}

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
  if [[ "${PTBD_BUNDLE_FORCE_FETCH:-0}" == "1" ]]; then
    log "forced fresh dependency build"
    rm -rf "$ROOT_DIR/third_party/bundle/linux-amd64"
    bash "$SCRIPT_DIR/fetch-deps.sh"
    bundle_ready
    return
  fi
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

command -v python3 >/dev/null 2>&1 || {
  echo "[build-bundle][ERROR] python3 is required to resolve runtime assets" >&2
  exit 1
}
python3 "$ROOT_DIR/ptbd_core/runtime_assets.py" copy \
  --profile bundle \
  --source-root "$ROOT_DIR" \
  --destination-root "$PKG_ROOT"

mkdir -p "$PKG_ROOT/third_party/bundle/linux-amd64"
cp -a "$ROOT_DIR/third_party/bundle/linux-amd64/bin" "$PKG_ROOT/third_party/bundle/linux-amd64/"
cp -a "$ROOT_DIR/third_party/bundle/linux-amd64/lib" "$PKG_ROOT/third_party/bundle/linux-amd64/"
chmod +x "$PKG_ROOT/bdtool" "$PKG_ROOT/bdtool-legacy.sh" "$PKG_ROOT/bdtool.sh" "$PKG_ROOT/ptbd" "$PKG_ROOT/ptbd-gui" "$PKG_ROOT/ptbd-web" "$PKG_ROOT/ptbd-gui.py" "$PKG_ROOT/ptbd_remote_backend.py" "$PKG_ROOT/install.sh" "$PKG_ROOT/ptbd-start.sh" "$PKG_ROOT/ptbd-remote.sh" "$PKG_ROOT/ptbd-remote-start.sh" "$PKG_ROOT/PT-ReleaseKit.sh" "$PKG_ROOT/PT-ReleaseKit.command" "$PKG_ROOT/PT-ReleaseKit.desktop" "$PKG_ROOT/PT-BDtool.sh" "$PKG_ROOT/PT-BDtool.command" "$PKG_ROOT/PT-BDtool.desktop" "$PKG_ROOT/scripts/ensure-bundle.py" "$PKG_ROOT/scripts/audio-spectrum.py" "$PKG_ROOT/scripts/fetch-deps.sh" "$PKG_ROOT/scripts/prepare-remote-runtime.sh" "$PKG_ROOT/scripts/remote-upload-server.py" 2>/dev/null || true

rm -f "$OUT_TAR" "$OUT_SHA256" "${OUT_SHA256}.tmp"
tar -czf "$OUT_TAR" -C "$ROOT_DIR/.tmp-dist" "$PACKAGE_DIR_NAME"
write_sha256

log "created: $OUT_TAR"
log "created: $OUT_SHA256"
log "package key files:"
tar -tzf "$OUT_TAR" | grep -E 'third_party/bundle/linux-amd64/bin/(ffmpeg|ffprobe|mediainfo|BDInfo)$|(^PT-ReleaseKit-linux-amd64/(bdtool|bdtool.sh|install.sh|ptbd-start.sh)$)' | sed 's/^/  - /'
