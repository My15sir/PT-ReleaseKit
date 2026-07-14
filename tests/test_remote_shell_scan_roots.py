from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from ptbd_core.config import normalize_scan_roots


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RemoteShellScanRootTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_parent = PROJECT_ROOT / ".tmp"
        temporary_parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temporary_parent)
        self.root = Path(self.temporary.name)
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.capture_path = self.root / "remote-env.bin"
        self.remote_capture = self.root / "capture-remote-env"

        self._write_executable(
            self.fake_bin / "sleep",
            """
            #!/usr/bin/env bash
            exit 0
            """,
        )
        self._write_executable(
            self.fake_bin / "ssh",
            """
            #!/usr/bin/env bash
            for argument in "$@"; do
              if [[ "$argument" == "-N" ]]; then
                trap 'exit 0' TERM INT
                while :; do /bin/sleep 1; done
              fi
            done
            command="${!#}"
            exec /bin/bash -c "$command"
            """,
        )
        self._write_executable(
            self.remote_capture,
            """
            #!/usr/bin/env bash
            printf '%s\\0' \
              "${BDTOOL_SCAN_INCLUDE_ROOTS-__UNSET__}" \
              "${BDTOOL_SCAN_INCLUDE_ROOTS_JSON-__UNSET__}" \
              "${BDTOOL_SCAN_INCLUDE_ROOTS_LINES-__UNSET__}" \
              "${BDTOOL_SCAN_EXCLUDE_ROOTS-__UNSET__}" \
              "${BDTOOL_SCAN_EXCLUDE_ROOTS_JSON-__UNSET__}" \
              "${BDTOOL_SCAN_EXCLUDE_ROOTS_LINES-__UNSET__}" \
              > "$PTBD_TEST_ENV_CAPTURE"
            """,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    @staticmethod
    def _unused_port() -> int:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def _run_remote(self, extra_env: dict[str, str], *extra_args: str) -> subprocess.CompletedProcess[str]:
        port = self._unused_port()
        home = self.root / "home"
        save_dir = self.root / "downloads"
        home.mkdir(exist_ok=True)
        env = {
            **os.environ,
            "HOME": str(home),
            "PATH": f"{self.fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "PTBD_REMOTE_CONFIG_FILE": str(home / "missing-config"),
            "PTBD_TEST_ENV_CAPTURE": str(self.capture_path),
            **extra_env,
        }
        command = [
            str(PROJECT_ROOT / "ptbd-remote.sh"),
            "--host",
            "sandbox.example",
            "--bootstrap",
            "0",
            "--remote-cmd",
            str(self.remote_capture),
            "--save-dir",
            str(save_dir),
            "--local-port",
            str(port),
            "--remote-return-port",
            str(port + 1),
            *extra_args,
        ]
        return subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

    def _captured_roots(self) -> list[str]:
        values = self.capture_path.read_bytes().split(b"\0")
        self.assertEqual(values[-1], b"")
        return [value.decode("utf-8") for value in values[:-1]]

    def test_structured_roots_round_trip_to_remote_shell(self) -> None:
        include_roots = [
            r"C:\Media\Movies",
            "\\\\server\\share\\",
            "/media/Movies, 2024",
            "/media/O'Brien Movies, 2024",
        ]
        exclude_roots = ["/media/Cache Files", "/mnt/Old, Stuff", "/srv/O'Brien"]
        result = self._run_remote(
            {
                "PTBD_SCAN_INCLUDE_ROOTS": "/stale/include",
                "PTBD_SCAN_INCLUDE_ROOTS_LINES": "/stale/lines",
                "PTBD_SCAN_INCLUDE_ROOTS_JSON": json.dumps(include_roots),
                "PTBD_SCAN_EXCLUDE_ROOTS": "/stale/exclude",
                "PTBD_SCAN_EXCLUDE_ROOTS_LINES": "\n".join(exclude_roots),
                "PTBD_SCAN_EXCLUDE_ROOTS_JSON": "",
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._captured_roots(),
            [
                normalize_scan_roots(include_roots),
                json.dumps(include_roots, ensure_ascii=False),
                "\n".join(include_roots),
                normalize_scan_roots(exclude_roots),
                json.dumps(exclude_roots, ensure_ascii=False),
                "\n".join(exclude_roots),
            ],
        )

    def test_cli_scan_include_overrides_stale_structured_environment(self) -> None:
        roots = ["/media/My Movies", "/srv/O'Brien"]
        result = self._run_remote(
            {
                "PTBD_SCAN_INCLUDE_ROOTS": "/stale/raw",
                "PTBD_SCAN_INCLUDE_ROOTS_LINES": "/stale/lines",
                "PTBD_SCAN_INCLUDE_ROOTS_JSON": json.dumps(["/stale/json"]),
            },
            "--scan-include",
            normalize_scan_roots(roots),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._captured_roots()[:3],
            [normalize_scan_roots(roots), json.dumps(roots), "\n".join(roots)],
        )

    def test_invalid_json_whitelist_fails_before_ssh(self) -> None:
        result = self._run_remote(
            {
                "PTBD_SCAN_INCLUDE_ROOTS": "/fallback",
                "PTBD_SCAN_INCLUDE_ROOTS_LINES": "/fallback",
                "PTBD_SCAN_INCLUDE_ROOTS_JSON": "not-json",
            }
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid scan roots json", result.stderr)
        self.assertFalse(self.capture_path.exists())

    def test_legacy_scanner_consumes_line_roots_without_resplitting(self) -> None:
        first_root = self.root / "Legacy My Movies"
        second_root = self.root / "Legacy O'Brien, 2024"
        first_root.mkdir()
        second_root.mkdir()
        first_movie = first_root / "first.mkv"
        second_movie = second_root / "second.mp4"
        first_movie.touch()
        second_movie.touch()
        env = {
            **os.environ,
            "BDTOOL_SCAN_FULL_ROOT": "/",
            "BDTOOL_SCAN_INCLUDE_ROOTS": "/does/not/exist",
            "BDTOOL_SCAN_INCLUDE_ROOTS_LINES": f"{first_root}\n{second_root}",
        }
        env.pop("BDTOOL_SCAN_INCLUDE_ROOTS_JSON", None)

        result = subprocess.run(
            [str(PROJECT_ROOT / "bdtool-legacy.sh"), "scan-json", "--full", "--lang", "en"],
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            {item["path"] for item in payload["items"]},
            {str(first_movie), str(second_movie)},
        )


if __name__ == "__main__":
    unittest.main()
