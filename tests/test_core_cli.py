from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ptbd_core import cli
from ptbd_core.returns import ReturnResult, parse_return_record


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CoreCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_parent = PROJECT_ROOT / ".tmp"
        temp_parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temp_parent)
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_scan_json_keeps_legacy_shape(self) -> None:
        movie = self.root / "movie.mkv"
        movie.touch()
        stdout = StringIO()
        with redirect_stdout(stdout):
            result = cli.command_scan_json(["--dir", str(self.root), "--lang", "zh"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(
            payload["items"],
            [{"index": 1, "type": "VIDEO", "type_label": "视频", "path": str(movie)}],
        )

    def test_structured_scan_roots_preserve_paths_with_spaces(self) -> None:
        main_root = self.root / "My Movies"
        extra_root = self.root / "More Media"
        main_root.mkdir()
        extra_root.mkdir()
        main_movie = main_root / "main.mkv"
        extra_movie = extra_root / "extra.mp4"
        main_movie.touch()
        extra_movie.touch()
        stdout = StringIO()
        env = {
            "BDTOOL_SCAN_FULL_ROOT": str(main_root),
            "BDTOOL_SCAN_INCLUDE_ROOTS": str(extra_root),
            "BDTOOL_SCAN_INCLUDE_ROOTS_JSON": json.dumps([str(main_root), str(extra_root)]),
        }

        with mock.patch.dict(os.environ, env, clear=False), redirect_stdout(stdout):
            result = cli.command_scan_json(["--full", "--lang", "zh"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(
            {item["path"] for item in payload["items"]},
            {str(main_movie), str(extra_movie)},
        )

    def test_scan_root_env_prefers_json_then_lines_then_legacy(self) -> None:
        env = {
            "ROOTS": "/legacy root",
            "ROOTS_LINES": "/lines root\n/media/Movies, 2024\n/media/O'Brien",
            "ROOTS_JSON": json.dumps([r"C:\Media\Movies", "\\\\server\\share\\"]),
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(cli._roots_from_env("ROOTS"), [r"C:\Media\Movies", "\\\\server\\share\\"])

        env.pop("ROOTS_JSON")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                cli._roots_from_env("ROOTS"),
                ["/lines root", "/media/Movies, 2024", "/media/O'Brien"],
            )

        env.pop("ROOTS_LINES")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(cli._roots_from_env("ROOTS"), ["/legacy root"])

    def test_invalid_structured_whitelist_does_not_fall_back(self) -> None:
        movie = self.root / "movie.mkv"
        movie.touch()
        env = {
            "BDTOOL_SCAN_FULL_ROOT": str(self.root),
            "BDTOOL_SCAN_INCLUDE_ROOTS": str(self.root),
            "BDTOOL_SCAN_INCLUDE_ROOTS_LINES": str(self.root),
            "BDTOOL_SCAN_INCLUDE_ROOTS_JSON": "not-json",
        }
        stderr = StringIO()

        with mock.patch.dict(os.environ, env, clear=True), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                cli.command_scan_json(["--full", "--lang", "zh"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("BDTOOL_SCAN_INCLUDE_ROOTS_JSON is invalid", stderr.getvalue())
        self.assertNotIn(str(movie), stderr.getvalue())

    def test_absent_and_explicit_empty_whitelist_are_distinct(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(cli._roots_from_env("BDTOOL_SCAN_INCLUDE_ROOTS"))
        with mock.patch.dict(os.environ, {"BDTOOL_SCAN_INCLUDE_ROOTS_JSON": "[]"}, clear=True):
            self.assertEqual(cli._roots_from_env("BDTOOL_SCAN_INCLUDE_ROOTS"), [])

    def test_direct_dry_mode_preserves_output_layout(self) -> None:
        source_dir = self.root / "source"
        source_dir.mkdir()
        movie = source_dir / "movie.mp4"
        movie.touch()

        result = cli.command_process([str(movie), "--mode", "dry", "--quiet"])

        readme = self.root / "信息" / "source" / "README.txt"
        self.assertEqual(result, 0)
        self.assertTrue(readme.is_file())
        self.assertIn("这是预期行为", readme.read_text(encoding="utf-8"))

    def test_shell_entry_uses_python_core(self) -> None:
        result = subprocess.run(
            [str(PROJECT_ROOT / "bdtool"), "--version"],
            cwd=PROJECT_ROOT,
            env={**os.environ, "PTBD_PYTHON_CORE": "1"},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bdtool 0.2.0", result.stdout)

    def test_directory_scan_processes_audio_album_once(self) -> None:
        album = self.root / "Music" / "Album"
        album.mkdir(parents=True)
        (album / "01.flac").write_bytes(b"audio")
        (album / "02.flac").write_bytes(b"audio")
        pipeline = mock.Mock()
        pipeline.process.return_value = SimpleNamespace(output_dir=self.root / "信息" / "Album")

        with mock.patch.object(cli, "MediaPipeline", return_value=pipeline):
            result = cli.command_process([str(self.root / "Music"), "--quiet"])

        self.assertEqual(result, 0)
        pipeline.process.assert_called_once()
        source, media_type, _options = pipeline.process.call_args.args
        self.assertEqual(Path(source), album)
        self.assertEqual(media_type, "AUDIO_DIR")

    def test_generate_path_can_emit_machine_readable_archive_result(self) -> None:
        source = self.root / "Movie" / "movie.mkv"
        source.parent.mkdir()
        source.write_bytes(b"video")
        generated = self.root / "work" / "信息" / "Movie"
        archive = self.root / "output" / "Movie_2.zip"
        pipeline = mock.Mock()
        pipeline.process.return_value = SimpleNamespace(output_dir=generated)
        pipeline.package.return_value = archive
        stdout = StringIO()

        with mock.patch.object(cli, "resolve_candidate", return_value=("VIDEO", source)), mock.patch.object(
            cli,
            "MediaPipeline",
            return_value=pipeline,
        ), mock.patch.object(cli, "package_stage_dir", return_value=archive.parent), mock.patch.object(
            cli,
            "return_archive",
            return_value=ReturnResult(mode="local", destination=str(archive)),
        ), redirect_stdout(stdout):
            result = cli.command_generate_path(["--path", str(source), "--result-json"])

        self.assertEqual(result, 0)
        self.assertEqual(
            parse_return_record(stdout.getvalue().strip()),
            ReturnResult(mode="local", destination=str(archive)),
        )


if __name__ == "__main__":
    unittest.main()
