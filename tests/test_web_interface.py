from __future__ import annotations

import importlib.util
import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from ptbd_core.returns import ReturnResult, serialize_return_record


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_web_module():
    spec = importlib.util.spec_from_file_location("ptbd_web_test", PROJECT_ROOT / "ptbd-web.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load ptbd-web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WebInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web = load_web_module()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.web.CONFIG_PATH = Path(self.temporary.name) / "config.json"
        self.web.TASK_REGISTRY = self.web.JobRegistry(max_completed=10)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.web.WebHandler)
        self.server.ptbd_base_path = ""  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def request_json(self, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.load(response)

    def test_status_and_config_interface(self) -> None:
        status_payload = self.request_json("/api/status")
        self.assertTrue(status_payload["ok"])

        saved = self.request_json(
            "/api/config",
            {
                "mode": "local",
                "local_root": "/media",
                "save_dir": "/output",
                "remote_password": "secret-value",
            },
        )
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["config"]["remote_password"], "")
        self.assertTrue(saved["config"]["password_saved"])
        self.assertNotIn("secret-value", json.dumps(saved, ensure_ascii=False))

        loaded = self.request_json("/api/config")
        self.assertEqual(loaded["config"]["remote_password"], "")
        self.assertTrue(loaded["config"]["password_saved"])
        mode = stat.S_IMODE(self.web.CONFIG_PATH.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_scan_full_config_round_trip(self) -> None:
        saved = self.request_json(
            "/api/config",
            {
                "mode": "local",
                "local_root": "/media",
                "save_dir": "/output",
                "scan_full": True,
            },
        )

        self.assertTrue(saved["config"]["scan_full"])
        loaded = self.request_json("/api/config")
        self.assertTrue(loaded["config"]["scan_full"])
        persisted = json.loads(self.web.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertIs(persisted["scan_full"], True)

    def test_local_processing_uses_isolated_workspace(self) -> None:
        root = Path(self.temporary.name)
        media_root = root / "media"
        source = media_root / "Feature Film" / "movie.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video")
        save_dir = root / "output"
        runtime_dir = root / "runtime"
        config = self.web.DEFAULT_CONFIG.copy()
        config.update(
            {
                "mode": "local",
                "local_root": str(media_root),
                "save_dir": os.path.relpath(save_dir, Path.cwd()),
                "auto_cleanup": True,
            }
        )
        task = self.web.WebTask(kind="process")
        commands: list[list[str]] = []

        archive = save_dir / "Feature Film.zip"

        def fake_process(
            command: list[str],
            child_env: dict[str, str],
            _task,
            *,
            output_lines: list[str] | None = None,
        ) -> int:
            commands.append(command)
            self.assertEqual(child_env["PYTHONIOENCODING"], "utf-8")
            self.assertEqual(child_env["PYTHONUTF8"], "1")
            self.assertEqual(child_env["BDTOOL_DOWNLOAD_DIR"], str(save_dir.resolve()))
            self.assertEqual(child_env["BDTOOL_DATA_DIR"], str(runtime_dir.resolve()))
            work_dir = Path(command[command.index("--work-dir") + 1])
            work_dir.mkdir(parents=True)
            (work_dir / "partial.txt").write_text("partial\n", encoding="utf-8")
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(b"zip")
            self.assertIsNotNone(output_lines)
            output_lines.append(
                serialize_return_record(ReturnResult(mode="local", destination=str(archive)))
            )
            return 0

        with mock.patch.dict(
            os.environ,
            {"BDTOOL_DATA_DIR": os.path.relpath(runtime_dir, Path.cwd())},
        ), mock.patch.object(
            self.web,
            "run_process_stream",
            side_effect=fake_process,
        ):
            self.web.local_process_paths(config, [os.path.relpath(source, Path.cwd())], task)

        self.assertEqual(len(commands), 1)
        work_dir = Path(commands[0][commands[0].index("--work-dir") + 1])
        self.assertEqual(work_dir, runtime_dir / "ptbd-web" / "jobs" / task.id / "0001")
        self.assertFalse((runtime_dir / "ptbd-web" / "jobs" / task.id).exists())
        self.assertIn("--result-json", commands[0])
        self.assertEqual(commands[0][:4], [self.web.sys.executable, "-m", "ptbd_core.cli", "generate-path"])
        self.assertEqual(commands[0][commands[0].index("--path") + 1], str(source.resolve()))
        self.assertEqual(task.outputs, [str(archive)])

    def test_windows_askpass_payload_uses_cmd_escaping(self) -> None:
        suffix, payload = self.web.askpass_script_payload("a&b%PATH%!^", windows=True)

        self.assertEqual(suffix, ".cmd")
        self.assertIn("setlocal DisableDelayedExpansion", payload)
        self.assertIn("echo(a^&b%%PATH%%!^^", payload)
        self.assertNotIn("#!/usr/bin/env sh", payload)

    def test_local_batch_continues_after_failure_and_reports_partial(self) -> None:
        root = Path(self.temporary.name)
        media_root = root / "media"
        failed_source = media_root / "Broken Film" / "broken.mkv"
        successful_source = media_root / "Feature Film" / "movie.mkv"
        failed_source.parent.mkdir(parents=True)
        successful_source.parent.mkdir(parents=True)
        failed_source.write_bytes(b"broken")
        successful_source.write_bytes(b"video")
        save_dir = root / "output"
        runtime_dir = root / "runtime"
        archive = save_dir / "Feature Film.zip"
        config = self.web.DEFAULT_CONFIG.copy()
        config.update(
            {
                "mode": "local",
                "local_root": str(media_root),
                "save_dir": str(save_dir),
                "auto_cleanup": True,
            }
        )
        task = self.web.WebTask(kind="process")
        processed_paths: list[str] = []

        def fake_process(
            command: list[str],
            _env: dict[str, str],
            _task,
            *,
            output_lines: list[str] | None = None,
        ) -> int:
            selected_path = command[command.index("--path") + 1]
            processed_paths.append(selected_path)
            if selected_path == str(failed_source):
                return 7
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(b"zip")
            self.assertIsNotNone(output_lines)
            output_lines.append(
                serialize_return_record(ReturnResult(mode="local", destination=str(archive)))
            )
            return 0

        with mock.patch.dict(os.environ, {"BDTOOL_DATA_DIR": str(runtime_dir)}), mock.patch.object(
            self.web,
            "run_process_stream",
            side_effect=fake_process,
        ):
            self.web.run_process_task(task, config, [str(failed_source), str(successful_source)])

        state = task.to_public()
        self.assertEqual(processed_paths, [str(failed_source), str(successful_source)])
        self.assertEqual(state["status"], "partial")
        self.assertEqual(state["outputs"], [str(archive.resolve())])
        self.assertEqual(state["failed"], [{"path": str(failed_source), "error": "本地处理失败，退出码：7"}])
        self.assertEqual(
            state["result_summary"],
            {
                "success": 1,
                "failed": 1,
                "total": 2,
                "outputs": [str(archive.resolve())],
                "failed_items": [{"path": str(failed_source), "error": "本地处理失败，退出码：7"}],
            },
        )

    def test_local_batch_all_failures_reports_error_after_every_item(self) -> None:
        root = Path(self.temporary.name)
        media_root = root / "media"
        sources = [media_root / "one.mkv", media_root / "two.mkv"]
        for source in sources:
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"broken")
        config = self.web.DEFAULT_CONFIG.copy()
        config.update({"mode": "local", "local_root": str(media_root), "save_dir": str(root / "output")})
        task = self.web.WebTask(kind="process")
        calls: list[str] = []

        def fail_process(command, _env, _task, *, output_lines=None):
            del output_lines
            calls.append(command[command.index("--path") + 1])
            return 9

        with mock.patch.object(self.web, "run_process_stream", side_effect=fail_process):
            self.web.run_process_task(task, config, [str(source) for source in sources])

        state = task.to_public()
        self.assertEqual(calls, [str(source) for source in sources])
        self.assertEqual(state["status"], "error")
        self.assertEqual(state["result_summary"]["success"], 0)
        self.assertEqual(state["result_summary"]["failed"], 2)

    def test_python_backend_batch_continues_after_item_failure(self) -> None:
        class FakeBackend:
            def __init__(self, *_args, **_kwargs) -> None:
                self.calls: list[str] = []
                self.closed = False

            def cancel(self) -> None:
                pass

            def close(self) -> None:
                self.closed = True

            def process_selected_path(self, selected_path: str, _save_dir: Path) -> Path:
                self.calls.append(selected_path)
                if selected_path == "/media/broken.mkv":
                    raise RuntimeError("probe failed")
                return Path("/output/movie.zip")

        fake_backend = FakeBackend()
        config = self.web.DEFAULT_CONFIG.copy()
        config.update({"mode": "remote", "save_dir": "/output"})
        task = self.web.WebTask(kind="process")
        paths = ["/media/broken.mkv", "/media/movie.mkv"]

        with (
            mock.patch.object(self.web, "backend_available", return_value=True),
            mock.patch.object(self.web, "PTBDRemoteBackend", return_value=fake_backend),
        ):
            self.web.run_process_task(task, config, paths)

        state = task.to_public()
        self.assertEqual(fake_backend.calls, paths)
        self.assertTrue(fake_backend.closed)
        self.assertEqual(state["status"], "partial")
        self.assertEqual(state["outputs"], ["/output/movie.zip"])
        self.assertEqual(state["failed"], [{"path": paths[0], "error": "probe failed"}])

    def test_shell_backend_batch_continues_after_item_failure(self) -> None:
        config = self.web.DEFAULT_CONFIG.copy()
        config.update({"mode": "remote", "save_dir": "/output"})
        task = self.web.WebTask(kind="process")
        paths = ["/media/broken.mkv", "/media/movie.mkv"]

        with mock.patch.object(self.web, "run_process_stream", side_effect=[7, 0]) as stream:
            self.web.shell_process_paths(config, paths, task)

        state = task.to_public()
        self.assertEqual(stream.call_count, 2)
        self.assertEqual(state["outputs"], ["/output"])
        self.assertEqual(state["failed"], [{"path": paths[0], "error": "远端处理失败，退出码：7"}])
        self.assertEqual(state["result_summary"]["success"], 1)
        self.assertEqual(state["result_summary"]["failed"], 1)

    def test_run_process_stream_decodes_binary_output_once(self) -> None:
        class FakeProcess:
            pid = 12345
            stdout = iter([b"valid UTF-8\n", b"invalid: \xff\r\n", b"\n"])

            @staticmethod
            def wait(timeout=None) -> int:
                return 0

        task = self.web.WebTask(kind="process")
        output_lines: list[str] = []

        with mock.patch.object(self.web.subprocess, "Popen", return_value=FakeProcess()) as popen:
            rc = self.web.run_process_stream(["fake-command"], {}, task, output_lines=output_lines)

        self.assertEqual(rc, 0)
        self.assertEqual(output_lines, ["valid UTF-8", "invalid: \ufffd"])
        log_text = "\n".join(task.to_public()["logs"])
        self.assertEqual(log_text.count("valid UTF-8"), 1)
        self.assertEqual(log_text.count("invalid: \ufffd"), 1)
        self.assertNotIn("text", popen.call_args.kwargs)
        self.assertNotIn("encoding", popen.call_args.kwargs)

    def test_windows_termination_uses_taskkill_for_the_process_tree(self) -> None:
        process = mock.Mock(pid=4321)

        with (
            mock.patch.object(self.web.os, "name", "nt"),
            mock.patch.object(self.web.shutil, "which", return_value=r"C:\\Windows\\System32\\taskkill.exe"),
            mock.patch.object(
                self.web.subprocess,
                "run",
                return_value=self.web.subprocess.CompletedProcess([], 0),
            ) as run,
        ):
            self.web.terminate_process_tree(process, force=True)

        run.assert_called_once_with(
            [r"C:\\Windows\\System32\\taskkill.exe", "/PID", "4321", "/T", "/F"],
            stdout=self.web.subprocess.DEVNULL,
            stderr=self.web.subprocess.DEVNULL,
            check=False,
        )
        process.kill.assert_not_called()
        process.terminate.assert_not_called()

    def test_windows_termination_falls_back_when_taskkill_fails(self) -> None:
        process = mock.Mock(pid=4321)

        with (
            mock.patch.object(self.web.os, "name", "nt"),
            mock.patch.object(self.web.shutil, "which", return_value="taskkill.exe"),
            mock.patch.object(
                self.web.subprocess,
                "run",
                return_value=self.web.subprocess.CompletedProcess([], 1),
            ),
        ):
            self.web.terminate_process_tree(process)

        process.terminate.assert_called_once_with()

    def test_local_scan_uses_current_python_without_bash(self) -> None:
        config = self.web.DEFAULT_CONFIG.copy()
        config.update({"mode": "local", "local_root": "/media", "save_dir": "/output"})
        task = self.web.WebTask(kind="scan")
        payload = json.dumps(
            {"items": [{"index": 1, "type": "VIDEO", "type_label": "视频", "path": "/media/movie.mkv"}]}
        )

        with mock.patch.object(
            self.web,
            "run_capture_process",
            return_value=self.web.subprocess.CompletedProcess([], 0, payload, ""),
        ) as run:
            items = self.web.local_scan_items(config, task)

        command = run.call_args.args[0]
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(command[:4], [self.web.sys.executable, "-m", "ptbd_core.cli", "scan-json"])
        self.assertEqual(items[0]["path"], "/media/movie.mkv")
        self.assertEqual(child_env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(child_env["PYTHONUTF8"], "1")

    def test_local_environment_keeps_main_root_and_resolves_relative_paths(self) -> None:
        config = self.web.DEFAULT_CONFIG.copy()
        config.update(
            {
                "mode": "local",
                "local_root": "relative-media",
                "scan_include": "relative-extra",
                "save_dir": "relative-output",
            }
        )

        env = self.web.local_runtime_env(config)

        cwd = Path.cwd()
        self.assertEqual(env["BDTOOL_SCAN_FULL_ROOT"], str((cwd / "relative-media").resolve()))
        self.assertEqual(
            env["BDTOOL_SCAN_INCLUDE_ROOTS"],
            str((cwd / "relative-extra").resolve()),
        )
        self.assertEqual(
            json.loads(env["BDTOOL_SCAN_INCLUDE_ROOTS_JSON"]),
            [str((cwd / "relative-media").resolve()), str((cwd / "relative-extra").resolve())],
        )
        self.assertEqual(env["BDTOOL_DOWNLOAD_DIR"], "relative-output")

    def test_local_structured_roots_preserve_spaces(self) -> None:
        config = self.web.DEFAULT_CONFIG.copy()
        config.update(
            {
                "mode": "local",
                "local_root": "/media/My Movies",
                "scan_include": "/mnt/extra",
            }
        )

        env = self.web.local_runtime_env(config)

        self.assertEqual(
            json.loads(env["BDTOOL_SCAN_INCLUDE_ROOTS_JSON"]),
            ["/media/My Movies", "/mnt/extra"],
        )

    def test_pre_cancelled_local_diagnose_stays_cancelled_and_reports_local_root(self) -> None:
        config = self.web.DEFAULT_CONFIG.copy()
        config.update({"mode": "local", "local_root": "/media", "scan_include": "/mnt/extra"})

        cancelled = self.web.WebTask(kind="diagnose")
        cancelled.cancel()
        self.web.run_diagnose_task(cancelled, config)
        self.assertEqual(cancelled.to_public()["status"], "cancelled")

        completed = self.web.WebTask(kind="diagnose")
        self.web.run_diagnose_task(completed, config)
        state = completed.to_public()
        self.assertEqual(state["status"], "success")
        self.assertEqual(state["result_summary"]["scan_roots"], "/media /mnt/extra")

    def test_remote_scan_full_forces_root_and_strict_host_checking(self) -> None:
        config = self.web.DEFAULT_CONFIG.copy()
        config.update({"mode": "remote", "scan_include": "", "scan_full": True})

        with mock.patch.object(self.web.shutil, "which", return_value="/usr/bin/ssh"):
            command = self.web.build_scan_command(config, "bdtool")

        self.assertEqual(self.web.scan_include_env_value(config), "/")
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("BDTOOL_SCAN_INCLUDE_ROOTS=/", command[-1])

    def test_diagnose_rejects_queued_or_running_task_without_overwriting_registry(self) -> None:
        for status in ("queued", "running"):
            with self.subTest(status=status):
                self.web.TASK_REGISTRY = self.web.JobRegistry(max_completed=10)
                existing, active = self.web.TASK_REGISTRY.reserve("scan")
                self.assertIsNone(active)
                self.assertIsNotNone(existing)
                if status == "running":
                    existing.start()

                request = urllib.request.Request(
                    self.base_url + "/api/diagnose",
                    data=json.dumps({}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=5)

                self.assertEqual(caught.exception.code, 409)
                payload = json.load(caught.exception)
                self.assertFalse(payload["ok"])
                self.assertIn(existing.id, payload["error"])
                self.assertIs(self.web.TASK_REGISTRY.active(), existing)
                self.assertIs(self.web.TASK_REGISTRY.get(existing.id), existing)
                self.assertEqual(existing.status, status)


if __name__ == "__main__":
    unittest.main()
