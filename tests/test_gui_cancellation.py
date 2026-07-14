from __future__ import annotations

import importlib.util
import os
import queue
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_gui_module():
    spec = importlib.util.spec_from_file_location("ptbd_gui_cancel_test", PROJECT_ROOT / "ptbd-gui.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load ptbd-gui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GuiCancellationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gui = load_gui_module()

    def make_app(self):
        app = self.gui.App.__new__(self.gui.App)
        app.process = None
        app.shell_cancel_event = threading.Event()
        return app

    def test_shell_batch_continues_after_failure_and_records_retry_paths(self) -> None:
        class FakeProcess:
            def __init__(self, returncode: int) -> None:
                self.stdout = iter(["child output\n"])
                self.returncode = returncode

            def wait(self) -> int:
                return self.returncode

        app = self.make_app()
        app.log_queue = queue.Queue()
        app.last_success_paths = []
        app.last_failed_paths = []
        app.append_log = mock.Mock()
        processes = [FakeProcess(7), FakeProcess(0)]
        paths = ["/media/broken.mkv", "/media/movie.mkv"]

        with mock.patch.object(self.gui.subprocess, "Popen", side_effect=processes) as popen:
            app.start_remote_with_shell_batch(
                "/bin/bash",
                PROJECT_ROOT / "ptbd-remote.sh",
                os.environ.copy(),
                PROJECT_ROOT / "output",
                paths,
            )
            app.backend_thread.join(timeout=5)

        self.assertFalse(app.backend_thread.is_alive())
        self.assertEqual(popen.call_count, 2)
        for call in popen.call_args_list:
            self.assertEqual(call.kwargs["encoding"], "utf-8")
            self.assertEqual(call.kwargs["errors"], "replace")
        self.assertEqual(app.last_success_paths, ["/media/movie.mkv"])
        self.assertEqual(app.last_failed_paths, ["/media/broken.mkv"])
        logs: list[str] = []
        while not app.log_queue.empty():
            logs.append(app.log_queue.get_nowait())
        self.assertIn("[gui] 批量完成：成功 1 / 失败 1 / 共 2", logs)
        self.assertIn("[gui] 任务结束，退出码：2", logs)

    def test_windows_askpass_payload_handles_echo_words_and_literal_bang(self) -> None:
        for password, expected in (
            ("on", "echo(on"),
            ("off", "echo(off"),
            ("off!a&b", "echo(off!a^&b"),
        ):
            with self.subTest(password=password):
                suffix, payload = self.gui.askpass_script_payload(password, windows=True)

                self.assertEqual(suffix, ".cmd")
                self.assertIn("setlocal DisableDelayedExpansion", payload)
                self.assertIn(expected, payload)

    def test_windows_taskkill_failure_falls_back_to_terminate(self) -> None:
        process = mock.Mock(pid=1234)
        process.poll.return_value = None

        with (
            mock.patch.object(self.gui.os, "name", "nt"),
            mock.patch.object(self.gui.shutil, "which", return_value="taskkill.exe"),
            mock.patch.object(
                self.gui.subprocess,
                "run",
                return_value=self.gui.subprocess.CompletedProcess([], 1),
            ),
        ):
            self.gui.App.terminate_shell_process(process)

        process.terminate.assert_called_once_with()

    def test_windows_taskkill_launch_error_falls_back_to_terminate(self) -> None:
        process = mock.Mock(pid=1234)
        process.poll.return_value = None

        with (
            mock.patch.object(self.gui.os, "name", "nt"),
            mock.patch.object(self.gui.shutil, "which", return_value="taskkill.exe"),
            mock.patch.object(self.gui.subprocess, "run", side_effect=OSError("taskkill unavailable")),
        ):
            self.gui.App.terminate_shell_process(process)

        process.terminate.assert_called_once_with()

    def test_backend_batch_reports_success_partial_error_and_cancelled(self) -> None:
        cases = (
            (
                "success",
                [Path("/output/one.zip"), Path("/output/two.zip")],
                2,
                "[gui] 任务结束，退出码：0",
                [],
            ),
            (
                "partial",
                [RuntimeError("first failed"), Path("/output/two.zip")],
                2,
                "[gui] 任务结束，退出码：2",
                ["/media/one.mkv"],
            ),
            (
                "error",
                [RuntimeError("first failed"), RuntimeError("second failed")],
                2,
                "[gui] 任务结束，退出码：1",
                ["/media/one.mkv", "/media/two.mkv"],
            ),
            (
                "cancelled",
                [self.gui.TaskCancelledError("cancelled"), Path("/output/two.zip")],
                1,
                "[gui] 任务结束，退出码：130",
                [],
            ),
        )

        for name, outcomes, expected_calls, expected_status, expected_failed in cases:
            with self.subTest(status=name):
                app = self.make_app()
                app.log_queue = queue.Queue()
                app.last_success_paths = ["stale-success"]
                app.last_failed_paths = ["stale-failure"]
                app.append_log = mock.Mock()
                app.status_var = mock.Mock()
                app.clear_backend_task = mock.Mock()
                remote_backend = mock.Mock()
                remote_backend.process_selected_path.side_effect = outcomes
                paths = ["/media/one.mkv", "/media/two.mkv"]

                with mock.patch.object(self.gui, "PTBDRemoteBackend", return_value=remote_backend):
                    app.start_remote_with_backend_batch(
                        {"remote_bootstrap": False},
                        PROJECT_ROOT / "output",
                        paths,
                    )
                    app.backend_thread.join(timeout=5)

                self.assertFalse(app.backend_thread.is_alive())
                self.assertEqual(remote_backend.process_selected_path.call_count, expected_calls)
                self.assertEqual(app.last_failed_paths, expected_failed)
                logs: list[str] = []
                while not app.log_queue.empty():
                    logs.append(app.log_queue.get_nowait())
                self.assertIn(expected_status, logs)

                if name == "partial":
                    self.assertIn("[gui] 批量完成：成功 1 / 失败 1 / 共 2", logs)
                elif name == "error":
                    self.assertIn("[gui] 批量完成：成功 0 / 失败 2 / 共 2", logs)
                elif name == "cancelled":
                    self.assertNotIn("[gui] 批量完成：成功 0 / 失败 0 / 共 2", logs)

    def test_connection_cancellation_is_not_reported_as_failure(self) -> None:
        app = self.make_app()
        app.log_queue = queue.Queue()
        app.task_running = mock.Mock(return_value=False)
        app.save_form = mock.Mock(return_value=True)
        app.form_data = mock.Mock(return_value={})
        app.status_var = mock.Mock()
        app.append_log = mock.Mock()
        app.clear_backend_task = mock.Mock()
        app.root = mock.Mock()
        app.root.after.side_effect = lambda _delay, callback: callback()
        remote_backend = mock.Mock()
        remote_backend.diagnose_connection.side_effect = self.gui.TaskCancelledError("cancelled")

        with (
            mock.patch.object(self.gui, "backend_available", return_value=True),
            mock.patch.object(self.gui, "PTBDRemoteBackend", return_value=remote_backend),
            mock.patch.object(self.gui.messagebox, "showerror") as showerror,
        ):
            app.test_connection()
            app.backend_thread.join(timeout=5)

        self.assertFalse(app.backend_thread.is_alive())
        app.status_var.set.assert_called_with("测连已取消")
        showerror.assert_not_called()

    def test_cancellable_capture_forces_utf8_decoding(self) -> None:
        class FakeProcess:
            returncode = 0

            @staticmethod
            def communicate(timeout=None):
                return "中文路径\n", ""

        app = self.make_app()
        with mock.patch.object(self.gui.subprocess, "Popen", return_value=FakeProcess()) as popen:
            result = app.run_cancellable_capture(["fake"], env={})

        self.assertEqual(result.stdout, "中文路径\n")
        self.assertEqual(popen.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(popen.call_args.kwargs["errors"], "replace")

    def test_new_shell_batch_clears_stale_retry_state_before_worker(self) -> None:
        class FakeThread:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def start(self) -> None:
                pass

        app = self.make_app()
        app.last_success_paths = ["old-success"]
        app.last_failed_paths = ["old-failure"]
        app.append_log = mock.Mock()

        with mock.patch.object(self.gui.threading, "Thread", FakeThread):
            app.start_remote_with_shell_batch(
                "/bin/bash",
                PROJECT_ROOT / "ptbd-remote.sh",
                {},
                PROJECT_ROOT / "output",
                ["/media/new.mkv"],
            )

        self.assertEqual(app.last_success_paths, [])
        self.assertEqual(app.last_failed_paths, [])

    def test_cancel_before_process_registration_prevents_start(self) -> None:
        app = self.make_app()
        app.shell_cancel_event.set()

        with self.assertRaises(self.gui.TaskCancelledError):
            app.run_cancellable_capture([sys.executable, "-c", "print('not started')"], env=os.environ.copy())

        self.assertIsNone(app.process)

    @unittest.skipIf(os.name == "nt", "process-group assertion is POSIX-specific")
    def test_registered_process_can_be_terminated(self) -> None:
        app = self.make_app()
        outcome: dict[str, BaseException | None] = {"error": None}

        def worker() -> None:
            try:
                app.run_cancellable_capture(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    env=os.environ.copy(),
                    timeout=60,
                )
            except BaseException as exc:
                outcome["error"] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        deadline = time.monotonic() + 5
        while app.process is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(app.process)

        app.shell_cancel_event.set()
        app.terminate_shell_process(app.process)
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertIsInstance(outcome["error"], self.gui.TaskCancelledError)
        self.assertIsNone(app.process)

    @unittest.skipIf(os.name == "nt", "process-group assertion is POSIX-specific")
    def test_cancel_during_popen_registration_terminates_process(self) -> None:
        app = self.make_app()
        original_popen = self.gui.subprocess.Popen

        def cancelling_popen(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            app.shell_cancel_event.set()
            return process

        with mock.patch.object(self.gui.subprocess, "Popen", side_effect=cancelling_popen):
            with self.assertRaises(self.gui.TaskCancelledError):
                app.run_cancellable_capture(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    env=os.environ.copy(),
                    timeout=60,
                )

        self.assertIsNone(app.process)


if __name__ == "__main__":
    unittest.main()
