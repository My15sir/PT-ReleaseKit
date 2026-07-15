from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ptbd_core.local_runtime import (
    build_local_cli_command,
    build_local_runtime_env,
    ensure_local_path_allowed,
    local_dependency_report,
    parse_local_archive,
)


class LocalRuntimeTests(unittest.TestCase):
    def test_desktop_hidden_cli_scans_local_media_without_starting_tk(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        temporary_parent = project_root / ".tmp"
        temporary_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_parent) as temporary_directory:
            media = Path(temporary_directory) / "media"
            media.mkdir()
            movie = media / "movie.mkv"
            movie.touch()
            result = subprocess.run(
                [
                    sys.executable,
                    str(project_root / "ptbd-gui.py"),
                    "--core-cli",
                    "scan-json",
                    "--dir",
                    str(media),
                ],
                cwd=project_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([item["path"] for item in payload["items"]], [str(movie)])

    def test_runtime_environment_limits_scan_to_selected_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            media = root / "media"
            extra = root / "extra media"
            output = root / "output"
            media.mkdir()
            extra.mkdir()

            env = build_local_runtime_env(
                {
                    "local_root": str(media),
                    "scan_include": str(extra),
                    "scan_exclude": str(extra / "cache"),
                    "save_dir": str(output),
                    "auto_cleanup": True,
                    "audio_spectrum_mode": "combined",
                    "audio_spectrum_backend": "ffmpeg",
                    "audio_spectrum_combined_track_seconds": "8",
                },
                base_env={"PATH": os.environ.get("PATH", "")},
            )

        self.assertEqual(env["BDTOOL_SCAN_FULL_ROOT"], str(media.resolve()))
        self.assertEqual(
            json.loads(env["BDTOOL_SCAN_INCLUDE_ROOTS_JSON"]),
            [str(media.resolve()), str(extra.resolve())],
        )
        self.assertEqual(json.loads(env["BDTOOL_SCAN_EXCLUDE_ROOTS_JSON"]), [str((extra / "cache").resolve())])
        self.assertEqual(env["BDTOOL_DOWNLOAD_DIR"], str(output.resolve()))
        self.assertEqual(env["BDTOOL_RETURN_MODE"], "local")
        self.assertEqual(env["BDTOOL_AUDIO_SPECTRUM_MODE"], "combined")

    def test_selected_path_must_stay_inside_configured_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            media = root / "media"
            outside = root / "outside"
            source = media / "movie.mkv"
            source.parent.mkdir()
            source.touch()
            outside.mkdir()
            config = {"local_root": str(media), "scan_include": ""}

            self.assertEqual(ensure_local_path_allowed(config, str(source)), source.resolve())
            with self.assertRaises(ValueError):
                ensure_local_path_allowed(config, str(outside))

    def test_frozen_app_uses_hidden_core_cli_entrypoint(self) -> None:
        self.assertEqual(
            build_local_cli_command(frozen=True, executable="/opt/PT-ReleaseKit"),
            ["/opt/PT-ReleaseKit", "--core-cli"],
        )
        command = build_local_cli_command(frozen=False, executable="/usr/bin/python3")
        self.assertEqual(command, ["/usr/bin/python3", "-m", "ptbd_core.cli"])

    def test_parse_local_archive_requires_one_valid_result_in_save_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            save_dir = Path(temporary_directory)
            archive = save_dir / "Movie.zip"
            archive.write_bytes(b"zip")
            line = json.dumps({"type": "ptbd-result", "mode": "local", "archive": str(archive)})

            self.assertEqual(parse_local_archive(line, save_dir), archive.resolve())
            with self.assertRaises(RuntimeError):
                parse_local_archive(f"{line}\n{line}", save_dir)

    def test_dependency_report_separates_required_and_optional_tools(self) -> None:
        available = {"ffmpeg": "/bin/ffmpeg", "ffprobe": "/bin/ffprobe", "mediainfo": None, "BDInfo": None}
        with mock.patch("ptbd_core.local_runtime.shutil.which", side_effect=lambda name: available[name]):
            report = local_dependency_report()

        self.assertFalse(report["ok"])
        self.assertEqual(report["missing_required"], ["mediainfo"])
        self.assertEqual(report["missing_optional"], ["BDInfo"])


if __name__ == "__main__":
    unittest.main()
