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
        self.assertIn("ttk.Progressbar", source)
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

    def test_clicking_any_candidate_column_toggles_one_item_without_double_click_reversal(self) -> None:
        app = self.gui.App.__new__(self.gui.App)
        app.scan_tree = mock.Mock()
        app.scan_tree.identify_row.return_value = "item-1"
        app.last_scan_click_item = None
        app.last_scan_click_time = -1000
        app.toggle_scan_item_checked = mock.Mock()

        first = mock.Mock(y=20, time=1000)
        second = mock.Mock(y=20, time=1100)
        later = mock.Mock(y=20, time=1500)

        self.assertEqual("break", app.on_scan_click(first))
        self.assertEqual("break", app.on_scan_click(second))
        self.assertEqual("break", app.on_scan_click(later))
        self.assertEqual(app.toggle_scan_item_checked.call_count, 2)
        app.scan_tree.selection_set.assert_called_with("item-1")

    def test_desktop_exposes_local_mode_and_hidden_core_cli(self) -> None:
        source = inspect.getsource(self.gui.App._build_ui)
        self.assertIn('text="本机电脑"', source)
        self.assertIn('value="local"', source)
        self.assertIn("self.pick_local_root", source)
        cli_source = inspect.getsource(self.gui.cli_main)
        self.assertIn('"--core-cli"', cli_source)
        self.assertIn("core_cli_main", cli_source)

    def test_desktop_image_host_report_populates_copyable_links(self) -> None:
        app = self.gui.App.__new__(self.gui.App)
        app.log_queue = self.gui.queue.Queue()
        app.shell_cancel_event = self.gui.threading.Event()
        app.last_image_links = []
        report = mock.Mock()
        report.cancelled = False
        report.error = ""
        report.success_count = 1
        report.failed_count = 0
        report.attempted_count = 1
        report.urls = ("https://img.example/frame.png",)

        with mock.patch.object(self.gui, "upload_archive_images", return_value=report) as uploader:
            returned = app.upload_images_for_archive(
                {"image_host_enabled": True, "image_host_token": "secret"},
                "/output/Movie.zip",
            )

        self.assertFalse(uploader.call_args.kwargs["should_cancel"]())
        uploader.call_args.kwargs["progress_callback"](1, 2, "上传完成 1/2: frame.png")

        self.assertIs(returned, report)
        self.assertEqual(app.last_image_links, ["https://img.example/frame.png"])
        logs = []
        while not app.log_queue.empty():
            logs.append(app.log_queue.get_nowait())
        self.assertIn("图床上传：成功 1 / 失败 0 / 共 1", "\n".join(logs))
        self.assertIn("图床上传进度：1/2 · 上传完成 1/2: frame.png", "\n".join(logs))
        self.assertNotIn("secret", "\n".join(logs))

    def test_desktop_cancelled_image_upload_raises_without_success_log(self) -> None:
        app = self.gui.App.__new__(self.gui.App)
        app.log_queue = self.gui.queue.Queue()
        app.shell_cancel_event = self.gui.threading.Event()
        app.last_image_links = []
        report = mock.Mock()
        report.cancelled = True
        report.error = ""

        with mock.patch.object(self.gui, "upload_archive_images", return_value=report):
            with self.assertRaises(self.gui.TaskCancelledError):
                app.upload_images_for_archive(
                    {"image_host_enabled": True, "image_host_token": "secret"},
                    "/output/Movie.zip",
                )

        logs = []
        while not app.log_queue.empty():
            logs.append(app.log_queue.get_nowait())
        log_text = "\n".join(logs)
        self.assertIn("图床上传已取消", log_text)
        self.assertNotIn("图床上传：成功", log_text)

    def test_desktop_local_batch_upload_cancellation_ends_with_code_130(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_root = root / "media"
            source = media_root / "Movie.mkv"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"video")
            save_dir = root / "output"
            save_dir.mkdir()
            archive = save_dir / "Movie.zip"
            archive.write_bytes(b"zip")

            app = self.gui.App.__new__(self.gui.App)
            app.shell_cancel_event = self.gui.threading.Event()
            app.log_queue = self.gui.queue.Queue()
            app.backend_thread = None
            app.last_success_paths = []
            app.last_failed_paths = []
            app.last_image_links = []
            app.status_var = mock.Mock()
            app.append_log = app.log_queue.put
            app.run_cancellable_capture = mock.Mock(
                return_value=subprocess.CompletedProcess(
                    ["fake-core-cli"],
                    0,
                    self.gui.json.dumps(
                        {
                            "type": "ptbd-result",
                            "mode": "local",
                            "archive": str(archive),
                        }
                    ),
                    "",
                )
            )
            data = self.gui.DEFAULT_CONFIG.copy()
            data.update(
                {
                    "mode": "local",
                    "local_root": str(media_root),
                    "save_dir": str(save_dir),
                    "image_host_enabled": True,
                    "image_host_provider": "custom",
                    "image_host_endpoint": "https://images.example.test/upload",
                    "image_host_token": "secret",
                    "auto_cleanup": True,
                }
            )
            cancelled_report = self.gui.ImageHostReport(
                enabled=True,
                provider="custom",
                archive=str(archive),
                cancelled=True,
            )

            with (
                mock.patch.object(
                    self.gui,
                    "local_dependency_report",
                    return_value={"missing_required": []},
                ),
                mock.patch.object(self.gui, "CONFIG_PATH", root / "config.json"),
                mock.patch.object(
                    self.gui,
                    "upload_archive_images",
                    return_value=cancelled_report,
                ),
            ):
                app.start_local_batch(data, save_dir, [str(source)])
                app.backend_thread.join(timeout=5)

            self.assertFalse(app.backend_thread.is_alive())
            logs = []
            while not app.log_queue.empty():
                logs.append(app.log_queue.get_nowait())
            log_text = "\n".join(logs)
            self.assertIn("[gui] 本机任务已取消", log_text)
            self.assertIn("[gui] 任务结束，退出码：130", log_text)
            self.assertNotIn("[gui] 任务结束，退出码：0", log_text)
            self.assertNotIn("[gui] 成功 1/1", log_text)
            self.assertEqual(archive.read_bytes(), b"zip")

    def test_desktop_provider_change_clears_old_token_and_requires_reentry(self) -> None:
        class Variable:
            def __init__(self, value="") -> None:
                self.value = value

            def get(self):
                return self.value

            def set(self, value) -> None:
                self.value = value

        app = self.gui.App.__new__(self.gui.App)
        token = Variable("old-token")
        clear = Variable(False)
        app.config_vars = {
            "image_host_token": token,
            "clear_image_host_token": clear,
        }
        app.image_host_provider_token_reset = False
        app.status_var = mock.Mock()
        app.log_queue = self.gui.queue.Queue()

        app.on_image_host_provider_changed()

        self.assertEqual(token.get(), "")
        self.assertTrue(clear.get())
        self.assertTrue(app.image_host_provider_token_reset)
        app.status_var.set.assert_called_with("图床类型已切换：请重新填写 API Token。")
        token.set("new-token")
        app._on_image_host_token_changed()
        self.assertFalse(clear.get())
        token.set("")
        app._on_image_host_token_changed()
        self.assertTrue(clear.get())
        app.image_host_provider_token_reset = False
        clear.set(True)
        token.set("manually-replaced-token")
        app._on_image_host_token_changed()
        self.assertFalse(clear.get())

    def test_desktop_remote_stop_sets_gui_and_backend_cancellation(self) -> None:
        app = self.gui.App.__new__(self.gui.App)
        app.shell_cancel_event = self.gui.threading.Event()
        app.backend_thread = mock.Mock()
        app.backend_thread.is_alive.return_value = True
        app.backend = mock.Mock()
        app.append_log = mock.Mock()
        app.status_var = mock.Mock()

        app.stop_remote()

        self.assertTrue(app.shell_cancel_event.is_set())
        app.backend.cancel.assert_called_once_with()
        app.status_var.set.assert_called_with("已请求停止，请稍等。")

    def test_scan_preparing_copy_matches_local_or_vps_mode(self) -> None:
        app = self.gui.App.__new__(self.gui.App)
        app.form_mode = lambda: "local"
        self.assertEqual("正在准备本机扫描环境", app._scan_progress_text({"phase": "preparing"}))
        app.form_mode = lambda: "remote"
        self.assertEqual("正在准备VPS扫描环境", app._scan_progress_text({"phase": "preparing"}))

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
        self.assertIn('id="scanProgress"', html)
        self.assertIn("function renderScanProgress", html)
        self.assertNotIn("__PTBD_BASE_PATH_JSON__", html)

    def test_web_mode_switch_hides_irrelevant_connection_fields(self) -> None:
        html = self.web.render_index_html("").decode("utf-8")
        self.assertIn('data-mode-only="remote"', html)
        self.assertIn("function updateModeFields", html)
        self.assertIn('id="diagnoseBtn" data-mode-only="remote" hidden', html)
        self.assertIn("input::placeholder", html)

    def test_web_exposes_optional_image_host_settings_and_link_copy(self) -> None:
        html = self.web.render_index_html("").decode("utf-8")
        self.assertIn('name="image_host_enabled"', html)
        self.assertIn('name="image_host_provider"', html)
        self.assertIn('value="lsky_v2"', html)
        self.assertIn('value="see"', html)
        self.assertIn('name="image_host_token"', html)
        self.assertIn("function updateImageHostFields", html)
        self.assertIn("function handleImageHostProviderChange", html)
        self.assertIn('form.clear_image_host_token.checked = true;', html)
        self.assertIn("图床类型已切换，请重新填写 Token", html)
        self.assertIn("task.image_uploads || []", html)
        self.assertIn("复制 BBCode", html)
        self.assertIn("image-host-bbcode.txt", html)

    def test_web_polling_unlocks_when_server_lost_the_active_task(self) -> None:
        html = self.web.render_index_html("").decode("utf-8")
        self.assertIn("if (!status.active_task)", html)
        self.assertIn("任务记录已不存在，服务可能已重启。请重新提交任务。", html)
        self.assertIn("setTaskRunning(false)", html)

    def test_web_candidate_workbench_supports_roomy_single_item_selection(self) -> None:
        html = self.web.render_index_html("").decode("utf-8")
        self.assertIn("min-height: clamp(320px, 45vh, 560px);", html)
        self.assertIn("min-height: clamp(220px, 34vh, 360px);", html)
        self.assertIn(
            'class="candidate-list" role="region" aria-label="扫描候选资源" tabindex="0"',
            html,
        )
        self.assertIn('row.classList.toggle("is-selected", selected);', html)
        self.assertIn('row.setAttribute("role", "group");', html)
        self.assertIn('checkbox.setAttribute("aria-label", `选择资源：${displayTitle(item)}`);', html)
        self.assertIn('row.addEventListener("click", (event) => {', html)
        self.assertIn('onlyButton.textContent = isOnlySelected ? "取消选择" : "仅选此项";', html)
        self.assertIn('onlyButton.setAttribute("aria-pressed", String(isOnlySelected));', html)
        self.assertIn('${isOnlySelected ? "取消选择" : "仅选择"}资源：${displayTitle(item)}', html)
        self.assertIn('renderCandidates(item.path, "checkbox");', html)
        self.assertIn('renderCandidates(item.path, "only");', html)
        self.assertNotIn('const row = document.createElement("label");', html)

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
