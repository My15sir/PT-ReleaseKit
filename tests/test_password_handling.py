from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_web_module():
    spec = importlib.util.spec_from_file_location("ptbd_web_password_test", PROJECT_ROOT / "ptbd-web.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load ptbd-web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PasswordHandlingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web = load_web_module()

    @staticmethod
    def config() -> dict:
        config = PasswordHandlingTests.web.DEFAULT_CONFIG.copy()
        config.update(
            {
                "remote_host": "user@example.test",
                "remote_port": "22",
                "remote_password": "secret value",
                "remote_bootstrap": True,
                "save_dir": "/tmp/ptbd-password-test",
            }
        )
        return config

    def test_bootstrap_password_is_only_in_environment(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(command, *, env, task):
            del task
            captured["command"] = command
            captured["env"] = env
            return subprocess.CompletedProcess(command, 0, "/remote/bdtool\n", "")

        task = self.web.WebTask(kind="scan")
        with mock.patch.object(self.web, "run_capture_process", side_effect=fake_run):
            remote_command = self.web.prepare_remote_runtime(self.config(), task)

        self.assertEqual(remote_command, "/remote/bdtool")
        command = captured["command"]
        self.assertNotIn("--password", command)
        self.assertNotIn("secret value", command)
        self.assertEqual(captured["env"]["PTBD_REMOTE_PASSWORD"], "secret value")

    def test_shell_processing_password_is_only_in_environment(self) -> None:
        captured: dict[str, object] = {}

        def fake_stream(command, env, task):
            del task
            captured["command"] = command
            captured["env"] = env
            return 0

        task = self.web.WebTask(kind="process")
        with mock.patch.object(self.web, "run_process_stream", side_effect=fake_stream):
            self.web.shell_process_paths(self.config(), ["/media/movie.mkv"], task)

        command = captured["command"]
        self.assertNotIn("--password", command)
        self.assertNotIn("secret value", command)
        self.assertEqual(captured["env"]["PTBD_REMOTE_PASSWORD"], "secret value")

    def test_remote_shell_does_not_forward_password_in_child_argv(self) -> None:
        script = (PROJECT_ROOT / "ptbd-remote.sh").read_text(encoding="utf-8")
        self.assertNotIn('PREPARE_CMD+=(--password "$PTBD_REMOTE_PASSWORD")', script)


if __name__ == "__main__":
    unittest.main()
