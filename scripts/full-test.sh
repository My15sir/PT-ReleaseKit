#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$ROOT_DIR/bdtool-output/logs"
RUN_LOG="$LOG_DIR/run.log"
FULL_LOG="$LOG_DIR/full-test.log"
RESULTS_TSV="$LOG_DIR/full-test-results.tsv"
TMP_FULL_LOG="$ROOT_DIR/.full-test.log.tmp"
TMP_RESULTS_TSV="$ROOT_DIR/.full-test-results.tsv.tmp"
TMPDIR_ROOT="$ROOT_DIR/.tmp"
TEST_DOWNLOAD_DIR="$ROOT_DIR/bdtool-output/test-downloads"
SSH_TEST_HOME="$ROOT_DIR/.full-test-ssh-home"
SSH_TEST_DOWNLOAD_DIR="$SSH_TEST_HOME/PT-BDtool-downloads"
SCP_TEST_HOME="$ROOT_DIR/.full-test-scp-home"
SCP_TEST_ROOT="$ROOT_DIR/.full-test-return-scp"
SCP_TEST_REMOTE_DIR="$SCP_TEST_ROOT/local-target"
SCP_TEST_BIN="$SCP_TEST_ROOT/mock-bin"

mkdir -p "$LOG_DIR"
mkdir -p "$TMPDIR_ROOT" "$TEST_DOWNLOAD_DIR"
touch "$RUN_LOG" "$FULL_LOG"
: > "$TMP_FULL_LOG"
: > "$TMP_RESULTS_TSV"

: > "$RESULTS_TSV"
find "${TEST_DOWNLOAD_DIR:?}" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +

TIMEOUT_SECONDS="${BDTOOL_CMD_TIMEOUT:-300}"
CLI_BIN="${BDTOOL_TEST_BIN:-}"
MENU_BIN="$ROOT_DIR/bdtool"
NOEMPTY_SAMPLE="$ROOT_DIR/.full-test-sample.mp4"
NOEMPTY_OUT="$ROOT_DIR/bdtool-output/test-run-bdtool"
PATHRULE_ROOT="$ROOT_DIR/.full-test-pathrule"
PATHRULE_SRC_DIR="$PATHRULE_ROOT/srcdir"
PATHRULE_SAMPLE="$PATHRULE_SRC_DIR/movie.mp4"
FULLSCAN_ROOT="$ROOT_DIR/.full-test-fullscan"
FULLSCAN_SRC_DIR="$FULLSCAN_ROOT/subdir"
FULLSCAN_SAMPLE="$FULLSCAN_SRC_DIR/fullscan.mp4"
FULLSCAN_SSH_ROOT="$ROOT_DIR/.full-test-fullscan-ssh"
FULLSCAN_SSH_SRC_DIR="$FULLSCAN_SSH_ROOT/subdir"
FULLSCAN_SSH_SAMPLE="$FULLSCAN_SSH_SRC_DIR/fullscan.mp4"
FULLSCAN_SCP_ROOT="$ROOT_DIR/.full-test-fullscan-scp"
FULLSCAN_SCP_SRC_DIR="$FULLSCAN_SCP_ROOT/subdir"
FULLSCAN_SCP_SAMPLE="$FULLSCAN_SCP_SRC_DIR/fullscan.mp4"
RUNTIME_RENDER_TEST="$TMPDIR_ROOT/test-render-runtime.py"
PLACEHOLDER_PNG_TEST="$TMPDIR_ROOT/test-placeholder-png.py"

if [[ -z "$CLI_BIN" ]]; then
  if [[ -x "$ROOT_DIR/bdtool.sh" ]]; then
    CLI_BIN="$ROOT_DIR/bdtool.sh"
  elif [[ -x "$ROOT_DIR/bdtool" ]]; then
    CLI_BIN="$ROOT_DIR/bdtool"
  else
    echo "No testable CLI entry found. Expected bdtool.sh or bdtool in $ROOT_DIR" >&2
    exit 1
  fi
fi

# Minimal sample for dry-mode execution path validation.
: > "$NOEMPTY_SAMPLE"
rm -rf "$PATHRULE_ROOT" "$FULLSCAN_ROOT"
rm -rf "$FULLSCAN_SSH_ROOT" "$SSH_TEST_HOME" "$FULLSCAN_SCP_ROOT" "$SCP_TEST_ROOT" "$SCP_TEST_HOME"
mkdir -p "$PATHRULE_SRC_DIR" "$FULLSCAN_SRC_DIR" "$FULLSCAN_SSH_SRC_DIR" "$FULLSCAN_SCP_SRC_DIR" "$SCP_TEST_BIN" "$SCP_TEST_REMOTE_DIR"
ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc=duration=1:size=320x240:rate=24 -c:v libx264 -pix_fmt yuv420p "$PATHRULE_SAMPLE" -y
ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc=duration=1:size=320x240:rate=24 -c:v libx264 -pix_fmt yuv420p "$FULLSCAN_SAMPLE" -y
ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc=duration=1:size=320x240:rate=24 -c:v libx264 -pix_fmt yuv420p "$FULLSCAN_SSH_SAMPLE" -y
ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc=duration=1:size=320x240:rate=24 -c:v libx264 -pix_fmt yuv420p "$FULLSCAN_SCP_SAMPLE" -y

cat > "$SCP_TEST_BIN/ssh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
last_arg="${@: -1}"
remote_dir="$(printf '%s' "$last_arg" | sed -n "s/^mkdir -p -- '\\(.*\\)'$/\\1/p" | sed "s/'\\\\''/'/g")"
[[ -n "$remote_dir" ]] || exit 1
mkdir -p "$remote_dir"
SH
chmod +x "$SCP_TEST_BIN/ssh"

cat > "$SCP_TEST_BIN/scp" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
src=""
dst=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -P|-i|-o)
      shift 2
      ;;
    -*)
      shift 1
      ;;
    *)
      if [[ -z "$src" ]]; then
        src="$1"
      elif [[ -z "$dst" ]]; then
        dst="$1"
      fi
      shift 1
      ;;
  esac
done
[[ -n "$src" && -n "$dst" ]] || exit 1
dst="${dst#*:}"
dst="${dst#\'}"
dst="${dst%\'}"
dst="$(printf '%s' "$dst" | sed "s/'\\\\''/'/g")"
mkdir -p "$(dirname "$dst")"
cp -f "$src" "$dst"
SH
chmod +x "$SCP_TEST_BIN/scp"

cat > "$RUNTIME_RENDER_TEST" <<'PY'
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, sys.argv[1])

from ptbd_remote_backend import PTBDRemoteBackend


with tempfile.TemporaryDirectory(prefix="ptbd-runtime-render-") as temp_dir:
    temp_path = Path(temp_dir)
    runtime_dir = temp_path / "runtime with spaces"
    runtime_dir.mkdir()

    bdtool_path = runtime_dir / "bdtool"
    bdtool_path.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$PTBDTOOL_ROOT\"\n",
        encoding="utf-8",
    )
    bdtool_path.chmod(0o755)

    bdtool_sh_path = runtime_dir / "bdtool.sh"
    bdtool_sh_path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    bdtool_sh_path.chmod(0o755)

    backend = PTBDRemoteBackend(temp_path, {}, logger=None)
    script = backend.render_prepare_runtime_script(
        remote_runtime_dir=str(runtime_dir),
        remote_launcher=str(runtime_dir / "ptbd-runtime"),
        remote_archive=str(temp_path / "missing.tar.gz"),
        remote_tmp_dir=str(temp_path / "tmp"),
        archive_mode="minimal",
    )

    subprocess.run(["sh", "-lc", script], check=True)
    output = subprocess.check_output([str(runtime_dir / "ptbd-runtime")], text=True).strip()
    if output != str(runtime_dir):
        raise SystemExit(f"unexpected PTBDTOOL_ROOT: {output!r}")
PY

cat > "$PLACEHOLDER_PNG_TEST" <<'PY'
import base64
import binascii
import re
import struct
import sys
from pathlib import Path

pattern = re.compile(r"printf '([^']+)' \| base64 -d")

for rel in ("bdtool", "bdtool.sh"):
    path = Path(sys.argv[1]) / rel
    text = path.read_text(encoding="utf-8")
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"placeholder png literal not found in {path}")
    data = base64.b64decode(match.group(1))
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"invalid png signature in {path}")
    pos = 8
    seen_iend = False
    while pos < len(data):
        if pos + 12 > len(data):
            raise SystemExit(f"truncated chunk header in {path}")
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise SystemExit(f"truncated chunk payload in {path}")
        chunk_data = data[pos + 8 : pos + 8 + length]
        chunk_crc = struct.unpack(">I", data[pos + 8 + length : end])[0]
        calc_crc = binascii.crc32(chunk_type)
        calc_crc = binascii.crc32(chunk_data, calc_crc) & 0xFFFFFFFF
        if calc_crc != chunk_crc:
            raise SystemExit(f"bad png crc in {path} chunk={chunk_type.decode('ascii', 'replace')}")
        if chunk_type == b"IEND":
            seen_iend = True
            break
        pos = end
    if not seen_iend:
        raise SystemExit(f"missing IEND chunk in {path}")
PY

write_log() {
  mkdir -p "$LOG_DIR"
  touch "$FULL_LOG" "$TMP_FULL_LOG"
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$TMP_FULL_LOG" >/dev/null
  cp -f "$TMP_FULL_LOG" "$FULL_LOG"
}

run_step() {
  local name="$1"
  local expect_mode="${2:-success}"
  shift 2
  case "$expect_mode" in
    success|fail) ;;
    *)
      write_log "Invalid expect_mode=$expect_mode for step=$name"
      return 2
      ;;
  esac

  local status expected_desc
  if [[ "$expect_mode" == "fail" ]]; then
    expected_desc="non-zero exit"
  else
    expected_desc="zero exit"
  fi

  write_log "EXPECT: $expected_desc"

  mkdir -p "$LOG_DIR"
  touch "$RUN_LOG" "$FULL_LOG" "$TMP_FULL_LOG" "$TMP_RESULTS_TSV"

  write_log "STEP START: $name"
  write_log "CMD: $*"

  local rc
  set +e
  if command -v timeout >/dev/null 2>&1; then
    timeout --preserve-status "${TIMEOUT_SECONDS}s" "$@" >> "$TMP_FULL_LOG" 2>&1
    rc=$?
  else
    "$@" >> "$TMP_FULL_LOG" 2>&1 &
    local pid=$!
    local begin now
    begin="$(date +%s)"
    rc=0
    while kill -0 "$pid" 2>/dev/null; do
      now="$(date +%s)"
      if (( now - begin >= TIMEOUT_SECONDS )); then
        kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        kill -KILL "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        rc=124
        break
      fi
      sleep 1
    done
    if [[ "$rc" -ne 124 ]]; then
      wait "$pid"
      rc=$?
    fi
  fi
  set -e

  status="PASS"
  if [[ "$rc" -eq 124 ]]; then
    status="TIMEOUT"
    write_log "TIMEOUT: $name exceeded ${TIMEOUT_SECONDS}s"
    printf '[%s] [TIMEOUT] %s exceeded %ss\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$name" "$TIMEOUT_SECONDS" >> "$RUN_LOG"
  elif [[ "$expect_mode" == "success" && "$rc" -ne 0 ]]; then
    status="FAIL"
  elif [[ "$expect_mode" == "fail" && "$rc" -eq 0 ]]; then
    status="FAIL"
  fi

  write_log "STEP END: $name status=$status rc=$rc"
  mkdir -p "$LOG_DIR"
  touch "$RESULTS_TSV" "$TMP_RESULTS_TSV"
  printf '%s\t%s\t%s\n' "$name" "$status" "$rc" >> "$TMP_RESULTS_TSV"
  cp -f "$TMP_RESULTS_TSV" "$RESULTS_TSV"
}

run_step "syntax-shell-scripts" success bash -n "$ROOT_DIR/bdtool" "$ROOT_DIR/bdtool.sh" "$ROOT_DIR/scripts/full-test.sh" "$ROOT_DIR/install.sh" "$ROOT_DIR/ptbd" "$ROOT_DIR/ptbd-gui" "$ROOT_DIR/ptbd-remote.sh" "$ROOT_DIR/ptbd-remote-start.sh" "$ROOT_DIR/ptbd-start.sh" "$ROOT_DIR/PT-BDtool.sh" "$ROOT_DIR/PT-BDtool.command" "$ROOT_DIR/scripts/build-bundle.sh" "$ROOT_DIR/scripts/fetch-deps.sh" "$ROOT_DIR/scripts/prepare-remote-runtime.sh" "$ROOT_DIR/scripts/update-deps.sh" "$ROOT_DIR/lib/ui.sh"
run_step "syntax-python-scripts" success python3 -m py_compile "$ROOT_DIR/ptbd-gui.py" "$ROOT_DIR/ptbd_remote_backend.py" "$ROOT_DIR/scripts/build-controller-app.py" "$ROOT_DIR/scripts/ensure-bundle.py" "$ROOT_DIR/scripts/remote-upload-server.py"
run_step "placeholder-png-valid" success python3 "$PLACEHOLDER_PNG_TEST" "$ROOT_DIR"
run_step "remote-runtime-render" success python3 "$RUNTIME_RENDER_TEST" "$ROOT_DIR"
run_step "workflow-ci-markers" success bash -c "grep -q 'name: Validate Project' '$ROOT_DIR/.github/workflows/ci.yml' && grep -q 'name: Release Portable Apps' '$ROOT_DIR/.github/workflows/controller-build.yml' && grep -q 'name: Release Linux Bundle' '$ROOT_DIR/.github/workflows/bundle-release.yml' && grep -q 'upload-artifact' '$ROOT_DIR/.github/workflows/controller-build.yml'"
run_step "bdtool-help" success "$CLI_BIN" --help
run_step "bdtool-version" success "$CLI_BIN" --version
run_step "bdtool-doctor" success "$CLI_BIN" doctor
run_step "ptbd-help" success "$ROOT_DIR/ptbd" --help
run_step "ptbd-start-help" success "$ROOT_DIR/ptbd-start.sh" --help
run_step "ptbd-remote-help" success "$ROOT_DIR/ptbd-remote.sh" --help
run_step "ptbd-remote-start-help" success "$ROOT_DIR/ptbd-remote-start.sh" --help
run_step "ptbd-gui-self-check" success "$ROOT_DIR/ptbd-gui" --self-check
run_step "build-controller-help" success python3 "$ROOT_DIR/scripts/build-controller-app.py" --help
run_step "ensure-bundle-help" success python3 "$ROOT_DIR/scripts/ensure-bundle.py" --help
run_step "prepare-remote-runtime-help" success bash "$ROOT_DIR/scripts/prepare-remote-runtime.sh" --help
run_step "scan-dry-invalid-input" fail "$CLI_BIN" "$ROOT_DIR/bdtool.sh" --mode dry --out "$ROOT_DIR/bdtool-output/test-run"
run_step "bdtool-dry-noempty" success "$MENU_BIN" "$NOEMPTY_SAMPLE" --mode dry --out "$NOEMPTY_OUT"
run_step "default-out-pathrule-video" success "$CLI_BIN" "$PATHRULE_SAMPLE" --log-level debug
run_step "fullscan-confirm-enters-flow" success env TMPDIR="$TMPDIR_ROOT" BDTOOL_DOWNLOAD_DIR="$TEST_DOWNLOAD_DIR" BDTOOL_AUTO_CLEANUP=0 BDTOOL_SCAN_FULL_ROOT="$FULLSCAN_ROOT" bash -c "printf '1\n1\n1\n1\n0\n0\n3\n' | '$MENU_BIN'"
run_step "fullscan-ssh-fallback-download" success env -u BDTOOL_DOWNLOAD_DIR HOME="$SSH_TEST_HOME" TMPDIR="$TMPDIR_ROOT" SSH_CONNECTION="127.0.0.1 10022 127.0.0.1 22" BDTOOL_SCAN_FULL_ROOT="$FULLSCAN_SSH_ROOT" bash -c "printf '1\n1\n1\n1\n0\n3\n' | '$MENU_BIN'"
run_step "fullscan-scp-return" success env HOME="$SCP_TEST_HOME" PATH="$SCP_TEST_BIN:$PATH" TMPDIR="$TMPDIR_ROOT" SSH_CONNECTION="127.0.0.1 10022 127.0.0.1 22" BDTOOL_RETURN_MODE="scp" BDTOOL_RETURN_SCP_HOST="127.0.0.1" BDTOOL_RETURN_SCP_USER="receiver" BDTOOL_RETURN_SCP_REMOTE_DIR="$SCP_TEST_REMOTE_DIR" BDTOOL_SCAN_FULL_ROOT="$FULLSCAN_SCP_ROOT" bash -c "printf '1\n1\n1\n1\n0\n3\n' | '$MENU_BIN'"

if ! find "$NOEMPTY_OUT" -type f -name 'README.txt' 2>/dev/null | grep -q .; then
  write_log "FULL TEST RESULT: FAIL (no output artifact for bdtool-dry-noempty)"
  exit 1
fi

if ! find "$PATHRULE_ROOT/信息/srcdir" -type f -name 'mediainfo.txt' 2>/dev/null | grep -q .; then
  write_log "FULL TEST RESULT: FAIL (default output path rule mismatch for video)"
  exit 1
fi

if ! find "$PATHRULE_ROOT/信息/srcdir" -type f -name '1.png' 2>/dev/null | grep -q .; then
  write_log "FULL TEST RESULT: FAIL (default output artifact missing screenshot)"
  exit 1
fi

if ! find "$FULLSCAN_ROOT/信息/subdir" -type f -name 'mediainfo.txt' 2>/dev/null | grep -q .; then
  write_log "FULL TEST RESULT: FAIL (full scan confirm did not enter scan_flow)"
  exit 1
fi

if [[ ! -f "$SSH_TEST_DOWNLOAD_DIR/subdir.zip" && ! -f "$SSH_TEST_DOWNLOAD_DIR/subdir.tar.gz" ]]; then
  write_log "FULL TEST RESULT: FAIL (ssh session did not fallback to VPS local download dir)"
  exit 1
fi

if [[ ! -f "$SCP_TEST_REMOTE_DIR/subdir.zip" && ! -f "$SCP_TEST_REMOTE_DIR/subdir.tar.gz" ]]; then
  write_log "FULL TEST RESULT: FAIL (scp return mode did not upload package to remote dir)"
  exit 1
fi

run_step "clean" success "$CLI_BIN" clean
run_step "bad-args" fail "$CLI_BIN" unknown-command

write_log "FULL TEST COMPLETE"
cp -f "$TMP_FULL_LOG" "$FULL_LOG"
cp -f "$TMP_RESULTS_TSV" "$RESULTS_TSV"

if awk -F '\t' '$2 != "PASS"{found=1} END{exit found ? 0 : 1}' "$RESULTS_TSV"; then
  write_log "FULL TEST RESULT: FAIL (see $RESULTS_TSV)"
  exit 1
fi

write_log "FULL TEST RESULT: PASS"
rm -f "$NOEMPTY_SAMPLE"
rm -rf "$PATHRULE_ROOT" "$FULLSCAN_ROOT" "$FULLSCAN_SSH_ROOT" "$SSH_TEST_HOME" "$FULLSCAN_SCP_ROOT" "$SCP_TEST_ROOT" "$SCP_TEST_HOME"
