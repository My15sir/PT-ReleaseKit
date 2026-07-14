from __future__ import annotations

import importlib.util
import inspect
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_script_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DesktopUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gui = load_script_module("ptbd_gui_ui_test", "ptbd-gui.py")

    def test_status_messages_map_to_visible_workflow_phases(self) -> None:
        cases = {
            "就绪：请先确认 VPS 连接": 1,
            "扫描中：正在从 VPS 获取候选列表": 2,
            "扫描已取消": 2,
            "扫描完成：显示 8 / 共 8 个候选": 3,
            "过滤完成：2 / 8 个候选匹配": 3,
            "运行中：准备顺序处理 2 个条目": 4,
            "任务部分成功。可重试失败项。": 4,
            "测连已取消": 1,
            "当前没有运行中的任务。": 1,
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.assertEqual(expected, self.gui.workflow_phase_for_status(status))

    def test_desktop_workbench_uses_native_focusable_controls(self) -> None:
        source = inspect.getsource(self.gui.App._build_ui)
        self.assertIn("ttk.Notebook", source)
        self.assertIn("PanedWindow", source)
        self.assertIn("ttk.Button", source)
        self.assertNotIn("_pack_round_button", source)

    def test_selected_paths_include_checked_items_hidden_by_current_filter(self) -> None:
        app = self.gui.App.__new__(self.gui.App)
        app.scan_items = [
            {"path": "/media/movie.mkv"},
            {"path": "/media/album"},
        ]
        app.checked_scan_paths = {"/media/movie.mkv", "/media/album"}

        self.assertEqual(
            ["/media/movie.mkv", "/media/album"],
            app.current_selected_paths(),
        )

    def test_new_scan_results_drop_checked_paths_that_no_longer_exist(self) -> None:
        checked = {"/media/old.mkv", "/media/keep.mkv"}
        new_items = [
            {"path": "/media/keep.mkv"},
            {"path": "/media/new.mkv"},
        ]

        self.assertEqual(
            {"/media/keep.mkv"},
            self.gui.reconcile_checked_paths(new_items, checked),
        )

    def test_new_scan_results_reapply_active_filters(self) -> None:
        app = self.gui.App.__new__(self.gui.App)
        app.scan_items = [{"path": "/media/old.mkv", "type": "video", "type_label": "视频"}]
        app.filtered_scan_items = list(app.scan_items)
        app.checked_scan_paths = {"/media/old.mkv", "/media/new.flac"}
        app.filter_type_var = mock.Mock()
        app.filter_type_var.get.return_value = "音频"
        app.filter_keyword_var = mock.Mock()
        app.filter_keyword_var.get.return_value = "new"
        refresh_calls: list[bool] = []
        app.refresh_scan_items = lambda auto_start=False: refresh_calls.append(auto_start)

        new_items = [
            {"path": "/media/new.flac", "type": "audio", "type_label": "音频"},
            {"path": "/media/new.mkv", "type": "video", "type_label": "视频"},
        ]
        app.apply_scan_results(new_items, auto_start=True)

        self.assertEqual([new_items[0]], app.filtered_scan_items)
        self.assertEqual({"/media/new.flac"}, app.checked_scan_paths)
        self.assertEqual([True], refresh_calls)

    @unittest.skipUnless(shutil.which("xvfb-run"), "Xvfb is required for the real Tk layout smoke test")
    def test_default_desktop_layout_keeps_candidates_and_log_visible(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            result = subprocess.run(
                ["xvfb-run", "-a", sys.executable, "ptbd-gui.py", "--ui-smoke-check"],
                cwd=PROJECT_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=15,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("ui_layout=PASS", result.stdout)


class WebUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web = load_script_module("ptbd_web_ui_test", "ptbd-web.py")

    def test_web_workflow_exposes_progress_and_live_task_state(self) -> None:
        html = self.web.render_index_html("/ptbd").decode("utf-8")
        self.assertIn('id="workflowProgress"', html)
        self.assertEqual(4, html.count('class="progress-step"'))
        self.assertIn('aria-live="polite"', html)
        self.assertIn("function setWorkflowPhase", html)
        self.assertNotIn("__PTBD_BASE_PATH_JSON__", html)

    def test_web_mode_switch_hides_irrelevant_connection_fields(self) -> None:
        html = self.web.render_index_html("").decode("utf-8")
        self.assertIn('data-mode-only="remote"', html)
        self.assertIn("function updateModeFields", html)
        self.assertIn('id="diagnoseBtn" data-mode-only="remote" hidden', html)
        self.assertIn("input::placeholder", html)

    def test_web_polling_unlocks_when_server_lost_the_active_task(self) -> None:
        html = self.web.render_index_html("").decode("utf-8")
        self.assertIn("if (!status.active_task)", html)
        self.assertIn("任务记录已不存在，服务可能已重启。请重新提交任务。", html)
        self.assertIn("setTaskRunning(false)", html)

    def test_web_base_path_rejects_script_breakout_characters(self) -> None:
        self.assertEqual("/ptbd/tools", self.web.normalize_base_path("ptbd/tools/"))
        for invalid in ("/ptbd?<script>", "/ptbd//tools", "/</script>", "/../api"):
            with self.subTest(base_path=invalid):
                with self.assertRaises(ValueError):
                    self.web.normalize_base_path(invalid)
        rendered = self.web.render_index_html("</script>").decode("utf-8")
        self.assertNotIn("const PTBD_BASE_PATH = \"</script>\"", rendered)
        self.assertIn("\\u003c/script\\u003e", rendered)


if __name__ == "__main__":
    unittest.main()
