#!/usr/bin/env python3
import json
import os
import platform
import queue
import signal
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from tkinter import BOTH, END, LEFT, W, X, filedialog, messagebox, ttk
import tkinter as tk
import tkinter.font as tkfont
from tkinter.scrolledtext import ScrolledText

from ptbd_core.config import default_config as core_default_config
from ptbd_core.config import default_save_dir as core_default_save_dir
from ptbd_core.config import load_config as core_load_config
from ptbd_core.config import normalize_remote_connection
from ptbd_core.config import normalize_scan_roots
from ptbd_core.config import save_config as core_save_config
from ptbd_core.config import split_path_roots
from ptbd_core.runtime_assets import AssetManifestError, validate_profile
from ptbd_remote_backend import (
    PTBDRemoteBackend,
    TaskCancelledError,
    backend_available,
    backend_status,
    build_effective_scan_include,
    preferred_scan_roots_text,
)


PRODUCT_NAME = "PT ReleaseKit"
# Keep config and log locations stable across the product rename.
APP_NAME = "PT-BDtool"
WORKFLOW_STEPS = ("连接", "扫描", "选择", "生成")
UI_COLORS = {
    "bg": "#f2f5f6",
    "surface": "#ffffff",
    "surface_alt": "#eaf0f2",
    "surface_soft": "#f7f9fa",
    "line": "#cfdadd",
    "line_strong": "#aebdc2",
    "ink": "#16242c",
    "muted": "#52636b",
    "faint": "#6a7b83",
    "header": "#17272e",
    "header_muted": "#c0d0d4",
    "accent": "#0f766e",
    "accent_hover": "#0b5f59",
    "accent_soft": "#d9f1ed",
    "accent_pale": "#edf8f6",
    "danger": "#a93b32",
    "danger_hover": "#8d2f28",
    "danger_soft": "#f9e8e6",
    "warning": "#8a4f00",
    "tree_select": "#dcefeb",
    "tree_header": "#e5ecee",
    "log_bg": "#111c21",
    "log_text": "#d8e5e8",
}


def askpass_script_payload(password: str, *, windows: bool) -> tuple[str, str]:
    if windows:
        escaped = password
        for old, new in (
            ("^", "^^"),
            ("&", "^&"),
            ("|", "^|"),
            ("<", "^<"),
            (">", "^>"),
            ("(", "^("),
            (")", "^)"),
            ("%", "%%"),
        ):
            escaped = escaped.replace(old, new)
        return ".cmd", f"@echo off\r\nsetlocal DisableDelayedExpansion\r\necho({escaped}\r\n"
    escaped = password.replace("'", "'\\''")
    return ".sh", f"#!/usr/bin/env bash\nprintf '%s\\n' '{escaped}'\n"


def resolve_script_path(path: str) -> Path:
    return Path(path).expanduser().resolve()


def is_app_root(candidate: Path) -> bool:
    return (
        (candidate / "bdtool").is_file()
        and (candidate / "bdtool.sh").is_file()
        and (candidate / "lib" / "ui.sh").is_file()
    )


def find_app_root() -> Path:
    script_path = resolve_script_path(__file__)
    script_dir = script_path.parent
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")),
        Path(os.environ.get("PTBDTOOL_ROOT", "")),
        Path(os.environ.get("PTBD_INSTALL_ROOT", "")),
        script_dir,
        script_dir.parent,
        Path("/opt/PT-BDtool"),
        Path.home() / ".local/share/pt-bdtool/PT-BDtool-app",
    ]
    for candidate in candidates:
        if not str(candidate):
            continue
        if is_app_root(candidate):
            return candidate.resolve()
    return script_dir


APP_ROOT = find_app_root()
PORTABLE_CONFIG_FILENAME = "PT-BDtool-config.json"


def windows_roaming_config_path() -> Path:
    home = Path.home()
    base = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
    return base / APP_NAME / "gui-config.json"


def macos_app_support_config_path() -> Path:
    home = Path.home()
    return home / "Library/Application Support" / APP_NAME / "gui-config.json"


def find_app_bundle_root(path: Path) -> Path | None:
    current = path.resolve()
    for candidate in (current, *current.parents):
        if candidate.suffix.lower() == ".app":
            return candidate
    return None


def portable_windows_config_path() -> Path | None:
    if platform.system() != "Windows":
        return None
    if not (getattr(sys, "frozen", False) or os.environ.get("PTBD_PORTABLE_CONFIG") == "1"):
        return None
    base_file = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    return base_file.parent / PORTABLE_CONFIG_FILENAME


def portable_macos_config_path() -> Path | None:
    if platform.system() != "Darwin":
        return None
    if not (getattr(sys, "frozen", False) or os.environ.get("PTBD_PORTABLE_CONFIG") == "1"):
        return None
    base_file = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    bundle_root = find_app_bundle_root(base_file)
    if bundle_root is not None:
        return bundle_root.parent / PORTABLE_CONFIG_FILENAME
    return base_file.parent / PORTABLE_CONFIG_FILENAME


def portable_linux_config_path() -> Path | None:
    if platform.system() != "Linux":
        return None
    if not (getattr(sys, "frozen", False) or os.environ.get("PTBD_PORTABLE_CONFIG") == "1"):
        return None
    base_file = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    return base_file.parent / PORTABLE_CONFIG_FILENAME


def path_is_writable(target: Path) -> bool:
    parent = target.parent
    if target.exists():
        return os.access(target, os.W_OK)
    return parent.exists() and os.access(parent, os.W_OK)


def config_storage_mode() -> str:
    system = platform.system()
    if system == "Windows":
        portable_path = portable_windows_config_path()
        if portable_path is not None and path_is_writable(portable_path):
            return "portable-next-to-exe"
        if portable_path is not None:
            return "fallback-appdata"
        return "appdata"
    if system == "Darwin":
        portable_path = portable_macos_config_path()
        if portable_path is not None and path_is_writable(portable_path):
            return "portable-next-to-app"
        if portable_path is not None:
            return "fallback-app-support"
        return "macos-app-support"
    portable_path = portable_linux_config_path()
    if portable_path is not None and path_is_writable(portable_path):
        return "portable-next-to-bin"
    if portable_path is not None:
        return "fallback-xdg-config"
    return "xdg-config"


def config_path() -> Path:
    system = platform.system()
    home = Path.home()
    if system == "Windows":
        portable_path = portable_windows_config_path()
        if portable_path is not None and path_is_writable(portable_path):
            return portable_path
        return windows_roaming_config_path()
    if system == "Darwin":
        portable_path = portable_macos_config_path()
        if portable_path is not None and path_is_writable(portable_path):
            return portable_path
        return macos_app_support_config_path()
    portable_path = portable_linux_config_path()
    if portable_path is not None and path_is_writable(portable_path):
        return portable_path
    return home / ".config/ptbd-gui/config.json"


CONFIG_PATH = config_path()
LOG_PATH = CONFIG_PATH.parent / "PT-BDtool.log"


def default_save_dir() -> str:
    return core_default_save_dir()


DEFAULT_CONFIG = core_default_config().to_dict()


def load_config() -> dict:
    return core_load_config(CONFIG_PATH, include_secret=True)


def save_config(data: dict) -> None:
    core_save_config(CONFIG_PATH, data)


def append_gui_log_line(text: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def find_bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def shell_script_path() -> Path:
    return APP_ROOT / "ptbd-remote.sh"


def bootstrap_script_path() -> Path:
    return APP_ROOT / "scripts/prepare-remote-runtime.sh"


def standalone_backend_label() -> str:
    return "内置独立控制后端" if backend_available() else "旧版 shell 回退后端"


def workflow_phase_for_status(status: str) -> int:
    text = status.strip()
    if text.startswith("就绪"):
        return 1
    if "测连" in text or "测试连接" in text:
        return 1
    if text.startswith("当前没有运行中的任务"):
        return 1
    if any(marker in text for marker in ("扫描中", "扫描已取消", "获取候选失败")):
        return 2
    if any(marker in text for marker in ("扫描完成", "过滤完成", "等待选择", "勾选", "候选")):
        return 3
    if any(marker in text for marker in ("运行中", "任务", "停止", "批量完成", "结果已保存")):
        return 4
    return 1


def reconcile_checked_paths(scan_items: list[dict], checked_paths: set[str]) -> set[str]:
    available = {str(item.get("path") or "").strip() for item in scan_items}
    available.discard("")
    return {path for path in checked_paths if path in available}


def ui_font_families() -> tuple[str, str]:
    system = platform.system()
    if system == "Windows":
        return "Segoe UI", "Consolas"
    if system == "Darwin":
        return "Helvetica Neue", "Menlo"
    return "DejaVu Sans", "DejaVu Sans Mono"


def configure_gradient_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    colors = UI_COLORS
    ui_family, mono_family = ui_font_families()
    default_font = tkfont.nametofont("TkDefaultFont")
    default_font.configure(family=ui_family, size=10)
    text_font = tkfont.nametofont("TkTextFont")
    text_font.configure(family=ui_family, size=10)
    heading_font = tkfont.nametofont("TkHeadingFont")
    heading_font.configure(family=ui_family, size=10, weight="bold")
    fixed_font = tkfont.nametofont("TkFixedFont")
    fixed_font.configure(family=mono_family, size=9)

    root.configure(bg=colors["bg"])

    style.configure(
        ".",
        background=colors["bg"],
        foreground=colors["ink"],
        bordercolor=colors["line"],
        darkcolor=colors["line"],
        lightcolor=colors["line"],
        troughcolor=colors["surface_alt"],
        fieldbackground=colors["surface"],
        focuscolor=colors["accent"],
        font=default_font,
    )
    style.configure("TFrame", background=colors["bg"])
    style.configure("App.TFrame", background=colors["bg"])
    style.configure("Panel.TFrame", background=colors["surface"])
    style.configure("Surface.TFrame", background=colors["surface"], borderwidth=1, relief="solid")
    style.configure("SurfaceBody.TFrame", background=colors["surface"])
    style.configure("Soft.TFrame", background=colors["surface_alt"])
    style.configure("Status.TFrame", background=colors["accent_pale"], borderwidth=1, relief="solid")
    style.configure("TLabel", background=colors["bg"], foreground=colors["ink"])
    style.configure("Surface.TLabel", background=colors["surface"], foreground=colors["ink"])
    style.configure(
        "Field.TLabel",
        background=colors["surface_alt"],
        foreground=colors["ink"],
        font=(ui_family, 10, "bold"),
    )
    style.configure(
        "Hint.TLabel",
        background=colors["surface"],
        foreground=colors["muted"],
        font=(ui_family, 9),
    )
    style.configure(
        "PanelHint.TLabel",
        background=colors["surface_alt"],
        foreground=colors["muted"],
        font=(ui_family, 9),
    )
    style.configure(
        "SectionTitle.TLabel",
        background=colors["surface"],
        foreground=colors["ink"],
        font=(ui_family, 12, "bold"),
    )
    style.configure(
        "Status.TLabel",
        background=colors["accent_pale"],
        foreground=colors["accent_hover"],
        font=(ui_family, 10, "bold"),
    )
    style.configure(
        "StatusMeta.TLabel",
        background=colors["accent_pale"],
        foreground=colors["muted"],
        font=(ui_family, 9, "bold"),
    )
    style.configure(
        "Summary.TLabel",
        background=colors["surface"],
        foreground=colors["ink"],
        font=(ui_family, 10, "bold"),
    )
    style.configure(
        "SummaryHint.TLabel",
        background=colors["surface"],
        foreground=colors["muted"],
        font=(ui_family, 9),
    )
    style.configure(
        "Path.TLabel",
        background=colors["surface"],
        foreground=colors["muted"],
        font=fixed_font,
    )
    style.configure(
        "Cyber.TEntry",
        fieldbackground=colors["surface"],
        foreground=colors["ink"],
        bordercolor=colors["line"],
        lightcolor=colors["line"],
        darkcolor=colors["line"],
        borderwidth=1,
        relief="flat",
        padding=(8, 7),
    )
    style.map(
        "Cyber.TEntry",
        bordercolor=[("focus", colors["accent"]), ("!focus", colors["line"])],
        lightcolor=[("focus", colors["accent"]), ("!focus", colors["line"])],
    )
    style.configure(
        "TCheckbutton",
        background=colors["surface"],
        foreground=colors["ink"],
    )
    style.map(
        "TCheckbutton",
        foreground=[("disabled", colors["faint"]), ("active", colors["accent"]), ("!disabled", colors["ink"])],
        background=[("active", colors["surface"]), ("!disabled", colors["surface"])],
    )
    style.configure(
        "Primary.TButton",
        background=colors["accent"],
        foreground="#ffffff",
        bordercolor=colors["accent"],
        lightcolor=colors["accent"],
        darkcolor=colors["accent"],
        borderwidth=1,
        relief="flat",
        padding=(13, 8),
        font=(ui_family, 9, "bold"),
    )
    style.map(
        "Primary.TButton",
        background=[("pressed", colors["accent_hover"]), ("active", colors["accent_hover"]), ("disabled", colors["line_strong"])],
        foreground=[("disabled", colors["surface_soft"]), ("!disabled", "#ffffff")],
    )
    style.configure(
        "Action.TButton",
        background=colors["surface"],
        foreground=colors["ink"],
        bordercolor=colors["line_strong"],
        lightcolor=colors["line_strong"],
        darkcolor=colors["line_strong"],
        borderwidth=1,
        relief="flat",
        padding=(11, 7),
        font=(ui_family, 9, "bold"),
    )
    style.map(
        "Action.TButton",
        background=[("pressed", colors["surface_alt"]), ("active", colors["surface_alt"]), ("disabled", colors["surface_soft"])],
        foreground=[("disabled", colors["faint"]), ("!disabled", colors["ink"])],
        bordercolor=[("focus", colors["accent"]), ("active", colors["accent"]), ("!disabled", colors["line_strong"])],
    )
    style.configure(
        "Danger.TButton",
        background=colors["danger_soft"],
        foreground=colors["danger"],
        bordercolor=colors["danger"],
        lightcolor=colors["danger"],
        darkcolor=colors["danger"],
        borderwidth=1,
        relief="flat",
        padding=(11, 7),
        font=(ui_family, 9, "bold"),
    )
    style.map(
        "Danger.TButton",
        background=[("pressed", colors["danger"]), ("active", colors["danger"]), ("disabled", colors["surface_soft"])],
        foreground=[("pressed", "#ffffff"), ("active", "#ffffff"), ("disabled", colors["faint"]), ("!disabled", colors["danger"])],
    )
    style.configure(
        "Cyber.Treeview",
        background=colors["surface"],
        fieldbackground=colors["surface"],
        foreground=colors["ink"],
        bordercolor=colors["line"],
        lightcolor=colors["line"],
        darkcolor=colors["line"],
        rowheight=27,
        font=default_font,
    )
    style.map(
        "Cyber.Treeview",
        background=[("selected", colors["tree_select"])],
        foreground=[("selected", colors["ink"])],
    )
    style.configure(
        "Cyber.Treeview.Heading",
        background=colors["tree_header"],
        foreground=colors["ink"],
        bordercolor=colors["line"],
        lightcolor=colors["line"],
        darkcolor=colors["line"],
        font=heading_font,
        padding=(6, 6),
    )
    style.map(
        "Cyber.Treeview.Heading",
        background=[("active", colors["surface_alt"])],
        foreground=[("active", colors["accent"]), ("!disabled", colors["ink"])],
    )
    style.configure("App.TNotebook", background=colors["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure(
        "App.TNotebook.Tab",
        background=colors["surface_alt"],
        foreground=colors["muted"],
        borderwidth=1,
        padding=(18, 8),
        font=(ui_family, 9, "bold"),
    )
    style.map(
        "App.TNotebook.Tab",
        background=[("selected", colors["surface"]), ("active", colors["accent_soft"])],
        foreground=[("selected", colors["accent_hover"]), ("active", colors["accent_hover"])],
    )


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{PRODUCT_NAME} 远程材料工作台")
        self.root.geometry("1040x760")
        self.root.minsize(820, 710)
        self.process: subprocess.Popen[str] | None = None
        self.shell_cancel_event = threading.Event()
        self.reader_threads: list[threading.Thread] = []
        self.backend: PTBDRemoteBackend | None = None
        self.backend_thread: threading.Thread | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.status_var = tk.StringVar(value="就绪：确认 VPS 连接后扫描候选，再勾选需要生成材料的条目")
        self.selected_path_var = tk.StringVar(value="")
        self.workspace_summary_var = tk.StringVar(value="0 个候选 · 0 个已选")
        self.backend_summary_var = tk.StringVar(value=standalone_backend_label())
        self.config_vars = {}
        self.scan_items: list[dict] = []
        self.filtered_scan_items: list[dict] = []
        self.auto_start_after_scan = False
        self.form_expanded = tk.BooleanVar(value=False)
        self.summary_host_var = tk.StringVar(value="VPS：未配置")
        self.summary_save_var = tk.StringVar(value="保存目录：未配置")
        self.filter_type_var = tk.StringVar(value="全部类型")
        self.filter_keyword_var = tk.StringVar(value="")
        self.checked_scan_paths: set[str] = set()
        self.path_tooltip: tk.Toplevel | None = None
        self.path_tooltip_label: tk.Label | None = None
        self.tooltip_item: str | None = None
        self.tooltip_after_id: str | None = None
        self.tree_font = tkfont.nametofont("TkDefaultFont")
        self.scan_context_menu: tk.Menu | None = None
        self.last_failed_paths: list[str] = []
        self.last_success_paths: list[str] = []
        self.workflow_step_widgets: list[tuple[tk.Frame, tk.Label, tk.Label]] = []
        self._build_ui()
        loaded_config = load_config()
        self._load_into_form(loaded_config)
        self.status_var.trace_add("write", self._on_status_change)
        remote_host = str(loaded_config.get("remote_host") or "").strip().lower()
        first_run = not CONFIG_PATH.is_file() or remote_host in {"", "root@your-vps"}
        if first_run and not CONFIG_PATH.is_file():
            self.config_vars["remote_host"].set("")
        self.toggle_form_panel(force=first_run)
        self._on_status_change()
        self._poll_logs()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        colors = UI_COLORS
        container = ttk.Frame(self.root, style="App.TFrame", padding=(14, 12, 14, 12))
        container.pack(fill=BOTH, expand=True)

        header = tk.Frame(container, bg=colors["header"], height=72, padx=18, pady=12)
        header.pack(fill=X, pady=(0, 8))
        header.pack_propagate(False)
        header.columnconfigure(0, weight=1)
        title_group = tk.Frame(header, bg=colors["header"])
        title_group.grid(row=0, column=0, sticky="w")
        tk.Label(
            title_group,
            text=PRODUCT_NAME,
            bg=colors["header"],
            fg="#ffffff",
            font=(ui_font_families()[0], 16, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_group,
            text="远程材料工作台 · 扫描、生成、回传在一个窗口完成",
            bg=colors["header"],
            fg=colors["header_muted"],
            font=(ui_font_families()[0], 9),
        ).pack(anchor="w", pady=(2, 0))
        tk.Label(
            header,
            textvariable=self.backend_summary_var,
            bg=colors["accent_soft"],
            fg=colors["accent_hover"],
            font=(ui_font_families()[0], 9, "bold"),
            padx=12,
            pady=7,
        ).grid(row=0, column=1, sticky="e")

        progress = tk.Frame(
            container,
            bg=colors["surface"],
            highlightbackground=colors["line"],
            highlightthickness=1,
            padx=8,
            pady=7,
        )
        progress.pack(fill=X, pady=(0, 8))
        for column, title in enumerate(WORKFLOW_STEPS, start=1):
            progress.columnconfigure(column - 1, weight=1, uniform="workflow")
            cell = tk.Frame(progress, bg=colors["surface"], padx=9, pady=3)
            cell.grid(row=0, column=column - 1, sticky="ew", padx=(0 if column == 1 else 3, 0))
            number = tk.Label(
                cell,
                text=str(column),
                width=2,
                bg=colors["surface_alt"],
                fg=colors["muted"],
                font=(ui_font_families()[0], 9, "bold"),
                padx=3,
                pady=2,
            )
            number.pack(side=tk.LEFT)
            label = tk.Label(
                cell,
                text=title,
                bg=colors["surface"],
                fg=colors["muted"],
                font=(ui_font_families()[0], 9, "bold"),
                padx=7,
            )
            label.pack(side=tk.LEFT)
            self.workflow_step_widgets.append((cell, number, label))

        self.main_notebook = ttk.Notebook(container, style="App.TNotebook", takefocus=True)
        self.main_notebook.pack(fill=BOTH, expand=True)
        self.workbench_tab = ttk.Frame(self.main_notebook, style="App.TFrame", padding=(0, 8, 0, 0))
        self.settings_tab = ttk.Frame(self.main_notebook, style="App.TFrame", padding=(0, 8, 0, 0))
        self.main_notebook.add(self.workbench_tab, text="工作台")
        self.main_notebook.add(self.settings_tab, text="连接设置")
        self.main_notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

        summary = ttk.Frame(self.workbench_tab, style="Surface.TFrame", padding=(14, 9))
        summary.pack(fill=X, pady=(0, 7))
        summary.columnconfigure(0, weight=1)
        summary_text = ttk.Frame(summary, style="SurfaceBody.TFrame")
        summary_text.grid(row=0, column=0, sticky="ew")
        ttk.Label(summary_text, textvariable=self.summary_host_var, style="Summary.TLabel").pack(anchor=W)
        ttk.Label(summary_text, textvariable=self.summary_save_var, style="SummaryHint.TLabel").pack(
            anchor=W, pady=(2, 0)
        )
        summary_actions = ttk.Frame(summary, style="SurfaceBody.TFrame")
        summary_actions.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.form_toggle_button = ttk.Button(
            summary_actions,
            text="编辑连接",
            command=self.toggle_form_panel,
            style="Action.TButton",
        )
        self.form_toggle_button.grid(row=0, column=0, padx=(0, 7))
        ttk.Button(summary_actions, text="保存连接", command=self.save_form, style="Action.TButton").grid(
            row=0, column=1, padx=(0, 7)
        )
        self.test_button = ttk.Button(
            summary_actions,
            text="测试连接",
            command=self.test_connection,
            style="Action.TButton",
        )
        self.test_button.grid(row=0, column=2)

        status_frame = ttk.Frame(self.workbench_tab, style="Status.TFrame", padding=(12, 8))
        status_frame.pack(fill=X, pady=(0, 7))
        status_frame.columnconfigure(0, weight=1)
        ttk.Label(
            status_frame,
            textvariable=self.status_var,
            style="Status.TLabel",
            wraplength=700,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.workspace_summary_var, style="StatusMeta.TLabel").grid(
            row=0, column=1, sticky="e", padx=(12, 0)
        )

        self.workspace_paned = tk.PanedWindow(
            self.workbench_tab,
            orient=tk.VERTICAL,
            background=colors["line"],
            borderwidth=0,
            sashwidth=6,
            sashrelief="flat",
            showhandle=False,
            opaqueresize=True,
        )
        self.workspace_paned.pack(fill=BOTH, expand=True)

        candidate_panel = ttk.Frame(self.workspace_paned, style="Surface.TFrame", padding=(12, 10))
        activity_panel = ttk.Frame(self.workspace_paned, style="Surface.TFrame", padding=(12, 10))
        self.workspace_paned.add(candidate_panel, minsize=220, stretch="always")
        self.workspace_paned.add(activity_panel, minsize=166, stretch="never")

        candidate_head = ttk.Frame(candidate_panel, style="SurfaceBody.TFrame")
        candidate_head.pack(fill=X, pady=(0, 8))
        candidate_head.columnconfigure(0, weight=1)
        self.candidate_head = candidate_head
        candidate_title = ttk.Frame(candidate_head, style="SurfaceBody.TFrame")
        candidate_title.grid(row=0, column=0, sticky="w")
        ttk.Label(candidate_title, text="VPS 候选", style="SectionTitle.TLabel").pack(anchor=W)
        self.candidate_hint = ttk.Label(
            candidate_title,
            text="勾选一个或多个条目，再生成并回传材料包",
            style="SummaryHint.TLabel",
        )
        self.candidate_hint.pack(anchor=W, pady=(1, 0))
        candidate_actions = ttk.Frame(candidate_head, style="SurfaceBody.TFrame")
        candidate_actions.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.scan_button = ttk.Button(
            candidate_actions,
            text="扫描 VPS",
            command=self.scan_remote,
            style="Action.TButton",
        )
        self.scan_button.grid(row=0, column=0, padx=(0, 7))
        self.start_button = ttk.Button(
            candidate_actions,
            text="生成所选",
            command=self.start_remote,
            style="Primary.TButton",
        )
        self.start_button.grid(row=0, column=1, padx=(0, 7))
        self.stop_button = ttk.Button(
            candidate_actions,
            text="停止任务",
            command=self.stop_remote,
            style="Danger.TButton",
        )
        self.stop_button.grid(row=0, column=2, padx=(0, 7))
        self.retry_button = ttk.Button(
            candidate_actions,
            text="重试失败",
            command=self.retry_failed_paths,
            style="Action.TButton",
        )
        self.retry_button.grid(row=0, column=3)

        filter_bar = ttk.Frame(candidate_panel, style="Soft.TFrame", padding=(10, 8))
        filter_bar.pack(fill=X, pady=(0, 8))
        filter_bar.columnconfigure(3, weight=1)
        ttk.Label(filter_bar, text="类型", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        type_box = ttk.Combobox(
            filter_bar,
            textvariable=self.filter_type_var,
            values=("全部类型", "视频", "音频", "原盘", "镜像"),
            state="readonly",
            width=10,
        )
        type_box.grid(row=0, column=1, padx=(7, 12), sticky="w")
        type_box.bind("<<ComboboxSelected>>", lambda _event: self.apply_scan_filters())
        ttk.Label(filter_bar, text="路径", style="Field.TLabel").grid(row=0, column=2, sticky="w")
        keyword_entry = ttk.Entry(filter_bar, textvariable=self.filter_keyword_var, style="Cyber.TEntry")
        keyword_entry.grid(row=0, column=3, padx=(7, 7), sticky="ew")
        keyword_entry.bind("<KeyRelease>", lambda _event: self.apply_scan_filters())
        ttk.Button(filter_bar, text="清除筛选", command=self.clear_scan_filters, style="Action.TButton").grid(
            row=0, column=4, padx=(0, 7)
        )
        ttk.Button(
            filter_bar,
            text="全选结果",
            command=self.select_visible_scan_items,
            style="Action.TButton",
        ).grid(row=0, column=5, padx=(0, 7))
        ttk.Button(
            filter_bar,
            text="清空勾选",
            command=self.clear_checked_scan_items,
            style="Action.TButton",
        ).grid(row=0, column=6)

        scan_list = ttk.Frame(candidate_panel, style="SurfaceBody.TFrame")
        scan_list.pack(fill=BOTH, expand=True)
        columns = ("pick", "index", "type", "path")
        self.scan_tree = ttk.Treeview(
            scan_list,
            columns=columns,
            show="headings",
            height=8,
            style="Cyber.Treeview",
            selectmode="browse",
            takefocus=True,
        )
        self.scan_tree.heading("pick", text="选择")
        self.scan_tree.heading("index", text="#")
        self.scan_tree.heading("type", text="类型")
        self.scan_tree.heading("path", text="路径")
        self.scan_tree.column("pick", width=66, anchor="center", stretch=False)
        self.scan_tree.column("index", width=48, anchor="center", stretch=False)
        self.scan_tree.column("type", width=92, anchor="center", stretch=False)
        self.scan_tree.column("path", width=700, minwidth=280, anchor="w", stretch=True)
        scan_scrollbar = ttk.Scrollbar(scan_list, orient="vertical", command=self.scan_tree.yview)
        scan_scrollbar_x = ttk.Scrollbar(scan_list, orient="horizontal", command=self.scan_tree.xview)
        self.scan_tree.configure(yscrollcommand=scan_scrollbar.set, xscrollcommand=scan_scrollbar_x.set)
        self.scan_tree.grid(row=0, column=0, sticky="nsew")
        scan_scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        scan_scrollbar_x.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        scan_list.columnconfigure(0, weight=1)
        scan_list.rowconfigure(0, weight=1)
        self.scan_tree.bind("<<TreeviewSelect>>", self.on_scan_select)
        self.scan_tree.bind("<Double-1>", self.on_scan_double_click)
        self.scan_tree.bind("<Button-1>", self.on_scan_click, add="+")
        self.scan_tree.bind("<space>", self.on_scan_space)
        self.scan_tree.bind("<Return>", self.on_scan_return)
        self.scan_tree.bind("<Motion>", self.on_scan_tree_hover)
        self.scan_tree.bind("<Leave>", self.hide_path_tooltip)
        self.scan_tree.bind("<ButtonPress-1>", self.hide_path_tooltip)
        self.scan_tree.bind("<Button-3>", self.on_scan_right_click)
        self.scan_tree.bind("<Control-Button-1>", self.on_scan_right_click)
        ttk.Label(
            candidate_panel,
            textvariable=self.selected_path_var,
            style="Path.TLabel",
            wraplength=920,
            justify="left",
        ).pack(anchor=W, pady=(7, 0))
        self.scan_context_menu = tk.Menu(self.root, tearoff=0)
        self.scan_context_menu.add_command(label="复制路径", command=self.copy_selected_scan_path)
        self.scan_context_menu.add_command(label="查看完整路径", command=self.show_selected_scan_path_dialog)
        self.scan_context_menu.add_command(label="仅生成这一项", command=self.start_selected_scan_item)

        activity_head = ttk.Frame(activity_panel, style="SurfaceBody.TFrame")
        activity_head.pack(fill=X, pady=(0, 7))
        activity_head.columnconfigure(0, weight=1)
        self.activity_head = activity_head
        activity_title = ttk.Frame(activity_head, style="SurfaceBody.TFrame")
        activity_title.grid(row=0, column=0, sticky="w")
        ttk.Label(activity_title, text="运行日志", style="SectionTitle.TLabel").pack(anchor=W)
        self.activity_hint = ttk.Label(
            activity_title,
            text="任务输出会实时显示，完整日志同时写入本机",
            style="SummaryHint.TLabel",
        )
        self.activity_hint.pack(anchor=W, pady=(1, 0))
        log_actions = ttk.Frame(activity_head, style="SurfaceBody.TFrame")
        log_actions.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.open_output_button = ttk.Button(
            log_actions,
            text="打开结果目录",
            command=self.open_save_dir,
            style="Action.TButton",
        )
        self.open_output_button.grid(row=0, column=0, padx=(0, 7))
        ttk.Button(log_actions, text="复制日志", command=self.copy_log_view, style="Action.TButton").grid(
            row=0, column=1, padx=(0, 7)
        )
        ttk.Button(log_actions, text="清空显示", command=self.clear_log_view, style="Action.TButton").grid(
            row=0, column=2, padx=(0, 7)
        )
        ttk.Button(log_actions, text="日志文件", command=self.open_log_file, style="Action.TButton").grid(
            row=0, column=3
        )
        self.log_view = ScrolledText(
            activity_panel,
            wrap="word",
            font=tkfont.nametofont("TkFixedFont"),
            height=7,
            background=colors["log_bg"],
            foreground=colors["log_text"],
            insertbackground="#ffffff",
            selectbackground=colors["accent"],
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=colors["line_strong"],
            highlightcolor=colors["accent"],
            padx=10,
            pady=8,
        )
        self.log_view.pack(fill=BOTH, expand=True)
        self.log_view.configure(state="disabled")
        self._workspace_headers_compact: bool | None = None
        candidate_head.bind("<Configure>", self._on_workspace_header_resize)
        activity_head.bind("<Configure>", self._on_workspace_header_resize)

        settings_view = ttk.Frame(self.settings_tab, style="App.TFrame")
        settings_view.pack(fill=BOTH, expand=True)
        settings_canvas = tk.Canvas(
            settings_view,
            background=colors["bg"],
            borderwidth=0,
            highlightthickness=0,
        )
        settings_scrollbar = ttk.Scrollbar(settings_view, orient="vertical", command=settings_canvas.yview)
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        settings_canvas.pack(side=tk.LEFT, fill=BOTH, expand=True)
        settings_scrollbar.pack(side=tk.RIGHT, fill="y")
        settings_shell = ttk.Frame(settings_canvas, style="Surface.TFrame", padding=(18, 14))
        self.settings_canvas = settings_canvas
        self.settings_shell = settings_shell
        settings_window = settings_canvas.create_window((0, 0), anchor="nw", window=settings_shell)
        settings_shell.bind(
            "<Configure>",
            lambda _event: settings_canvas.configure(scrollregion=settings_canvas.bbox("all")),
        )
        settings_canvas.bind(
            "<Configure>",
            lambda event: settings_canvas.itemconfigure(settings_window, width=max(event.width, 560)),
        )
        settings_head = ttk.Frame(settings_shell, style="SurfaceBody.TFrame")
        settings_head.pack(fill=X, pady=(0, 10))
        settings_head.columnconfigure(0, weight=1)
        settings_title = ttk.Frame(settings_head, style="SurfaceBody.TFrame")
        settings_title.grid(row=0, column=0, sticky="w")
        ttk.Label(settings_title, text="连接与保存设置", style="SectionTitle.TLabel").pack(anchor=W)
        ttk.Label(
            settings_title,
            text="首次使用先填写 VPS；低频扫描规则留在这里，不占用工作台空间",
            style="SummaryHint.TLabel",
        ).pack(anchor=W, pady=(2, 0))
        ttk.Button(
            settings_head,
            text="返回工作台",
            command=lambda: self.toggle_form_panel(force=False),
            style="Action.TButton",
        ).grid(row=0, column=1, sticky="e")

        self.form_details = ttk.Frame(settings_shell, style="SurfaceBody.TFrame")
        self.form_details.pack(fill=BOTH, expand=True)
        form = self.form_details
        self._add_compact_entry(form, "VPS 地址", "remote_host", 0, 0, "例如：root@1.2.3.4")
        self._add_compact_entry(form, "SSH 端口", "remote_port", 0, 1, "默认 22")
        self._add_compact_entry(form, "SSH 密码", "remote_password", 1, 0, "留空表示使用密钥", show="*")
        self._add_compact_entry(form, "远端命令", "remote_cmd", 1, 1, "通常保持 pt")
        self._add_compact_entry(
            form,
            "额外扫描目录",
            "scan_include",
            2,
            0,
            f"可留空，默认：{preferred_scan_roots_text()}",
        )
        self._add_compact_entry(form, "排除目录", "scan_exclude", 2, 1, "多个目录用空格分隔，可留空")

        save_group = ttk.Frame(form, style="Soft.TFrame", padding=(12, 9))
        save_group.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(5, 4))
        save_group.columnconfigure(0, weight=1)
        ttk.Label(save_group, text="本机保存目录", style="Field.TLabel").grid(row=0, column=0, sticky=W)
        variable = tk.StringVar()
        variable.trace_add("write", lambda *_args: self.refresh_config_summary())
        self.config_vars["save_dir"] = variable
        ttk.Entry(save_group, textvariable=variable, style="Cyber.TEntry").grid(
            row=1, column=0, sticky="ew", pady=(6, 0)
        )
        ttk.Button(save_group, text="选择目录", command=self.pick_save_dir, style="Action.TButton").grid(
            row=1, column=1, padx=(8, 0)
        )

        option_group = ttk.Frame(form, style="SurfaceBody.TFrame", padding=(0, 7, 0, 0))
        option_group.grid(row=4, column=0, columnspan=2, sticky="ew")
        option_group.columnconfigure(0, weight=1)
        option_group.columnconfigure(1, weight=1)
        self.config_vars["auto_cleanup"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            option_group,
            text="成功后清理 VPS 临时结果",
            variable=self.config_vars["auto_cleanup"],
        ).grid(row=0, column=0, sticky=W, pady=(0, 6))
        self.config_vars["remote_bootstrap"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            option_group,
            text="空白 VPS 自动准备运行环境",
            variable=self.config_vars["remote_bootstrap"],
        ).grid(row=0, column=1, sticky=W, pady=(0, 6))
        self.config_vars["scan_full"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            option_group,
            text="启用全盘扫描（较慢）",
            variable=self.config_vars["scan_full"],
        ).grid(row=1, column=0, sticky=W)

        settings_actions = ttk.Frame(form, style="SurfaceBody.TFrame")
        settings_actions.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.settings_save_button = ttk.Button(
            settings_actions,
            text="保存并返回",
            command=self.save_and_show_workbench,
            style="Primary.TButton",
        )
        self.settings_save_button.pack(side=tk.LEFT)
        self.settings_test_button = ttk.Button(
            settings_actions,
            text="测试连接",
            command=self.test_connection,
            style="Action.TButton",
        )
        self.settings_test_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(settings_actions, text="配置目录", command=self.open_config_dir, style="Action.TButton").pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(settings_actions, text="日志文件", command=self.open_log_file, style="Action.TButton").pack(
            side=tk.LEFT, padx=(8, 0)
        )
        form.columnconfigure(0, weight=1, uniform="form")
        form.columnconfigure(1, weight=1, uniform="form")
        self._bind_settings_scroll(settings_canvas)

        initial_lines = (
            f"App root: {APP_ROOT}",
            f"Config: {CONFIG_PATH}",
            f"Log: {LOG_PATH}",
            f"Config mode: {config_storage_mode()}",
            f"Backend: {backend_status()}",
            "准备完成。",
        )
        self.append_log_lines(initial_lines)
        self.main_notebook.select(self.workbench_tab)
        self._set_workflow_phase(1)
        self._sync_action_states()

    def _bind_settings_scroll(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._on_settings_mousewheel, add="+")
        widget.bind("<Button-4>", self._on_settings_mousewheel, add="+")
        widget.bind("<Button-5>", self._on_settings_mousewheel, add="+")
        widget.bind("<FocusIn>", self._on_settings_focus, add="+")
        for child in widget.winfo_children():
            self._bind_settings_scroll(child)

    def _on_settings_mousewheel(self, event) -> str:
        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            units = -int(delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
        self.settings_canvas.yview_scroll(units, "units")
        return "break"

    def _on_settings_focus(self, event) -> None:
        self.root.after_idle(lambda widget=event.widget: self._scroll_settings_widget_into_view(widget))

    def _scroll_settings_widget_into_view(self, widget: tk.Misc) -> None:
        if not widget.winfo_exists() or not self.settings_canvas.winfo_viewable():
            return
        content_height = max(self.settings_shell.winfo_height(), 1)
        viewport_height = self.settings_canvas.winfo_height()
        if content_height <= viewport_height:
            return
        widget_top = widget.winfo_rooty() - self.settings_shell.winfo_rooty()
        widget_bottom = widget_top + widget.winfo_height()
        view_top = self.settings_canvas.canvasy(0)
        view_bottom = view_top + viewport_height
        if widget_top < view_top + 8:
            self.settings_canvas.yview_moveto(max(0.0, (widget_top - 8) / content_height))
        elif widget_bottom > view_bottom - 8:
            target = (widget_bottom - viewport_height + 8) / content_height
            self.settings_canvas.yview_moveto(min(1.0, max(0.0, target)))

    def _add_entry(self, parent, label_text: str, key: str, row: int, hint: str, show: str | None = None) -> None:
        ttk.Label(parent, text=label_text, style="Field.TLabel").grid(row=row, column=0, sticky=W, padx=(0, 10), pady=4)
        variable = tk.StringVar()
        self.config_vars[key] = variable
        entry = ttk.Entry(parent, textvariable=variable, show=show or "", style="Cyber.TEntry")
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Label(parent, text=hint, style="Hint.TLabel").grid(row=row, column=2, sticky=W, padx=(10, 0), pady=4)

    def _add_compact_entry(self, parent, label_text: str, key: str, row: int, column: int, hint: str, show: str | None = None) -> None:
        field = ttk.Frame(parent, style="Soft.TFrame", padding=(10, 6, 10, 6))
        field.grid(row=row, column=column, sticky="nsew", padx=(0, 10 if column == 0 else 0), pady=2)
        field.columnconfigure(0, weight=1)
        ttk.Label(field, text=label_text, style="Field.TLabel").grid(row=0, column=0, sticky=W)
        variable = tk.StringVar()
        self.config_vars[key] = variable
        variable.trace_add("write", lambda *_args: self.refresh_config_summary())
        entry = ttk.Entry(field, textvariable=variable, show=show or "", style="Cyber.TEntry")
        entry.grid(row=1, column=0, sticky="ew", pady=(3, 2))
        ttk.Label(
            field,
            text=hint,
            style="PanelHint.TLabel",
            wraplength=320,
            justify="left",
        ).grid(row=2, column=0, sticky=W)

    def _load_into_form(self, data: dict) -> None:
        for key, value in data.items():
            var = self.config_vars.get(key)
            if isinstance(var, tk.BooleanVar):
                var.set(bool(value))
            elif var is not None:
                var.set(str(value))
        self.refresh_config_summary()

    def refresh_config_summary(self) -> None:
        host = self.config_vars.get("remote_host")
        save_dir = self.config_vars.get("save_dir")
        host_value = host.get().strip() if isinstance(host, tk.StringVar) else ""
        save_value = save_dir.get().strip() if isinstance(save_dir, tk.StringVar) else ""
        display_host = "未配置" if host_value.lower() in {"", "root@your-vps"} else host_value
        self.summary_host_var.set(f"VPS：{display_host}")
        self.summary_save_var.set(f"保存目录：{save_value or default_save_dir()}")

    def toggle_form_panel(self, force: bool | None = None) -> None:
        selected = self.main_notebook.select()
        settings_selected = selected == str(self.settings_tab)
        expanded = (not settings_selected) if force is None else bool(force)
        self.form_expanded.set(expanded)
        if expanded:
            self.main_notebook.select(self.settings_tab)
        else:
            self.main_notebook.select(self.workbench_tab)

    def _on_notebook_tab_changed(self, _event=None) -> None:
        settings_selected = self.main_notebook.select() == str(self.settings_tab)
        self.form_expanded.set(settings_selected)
        if settings_selected:
            self._set_workflow_phase(1)
        else:
            self._set_workflow_phase(workflow_phase_for_status(self.status_var.get()))

    def _on_workspace_header_resize(self, _event=None) -> None:
        available_width = min(self.candidate_head.winfo_width(), self.activity_head.winfo_width())
        compact = available_width < 900
        if compact == self._workspace_headers_compact:
            return
        self._workspace_headers_compact = compact
        for hint in (self.candidate_hint, self.activity_hint):
            if compact:
                hint.pack_forget()
            elif not hint.winfo_manager():
                hint.pack(anchor=W, pady=(1, 0))

    def _set_workflow_phase(self, phase: int) -> None:
        phase = max(1, min(len(WORKFLOW_STEPS), phase))
        for index, (cell, number, label) in enumerate(self.workflow_step_widgets, start=1):
            if index == phase:
                cell_bg = UI_COLORS["accent_pale"]
                number_bg = UI_COLORS["accent"]
                number_fg = "#ffffff"
                label_fg = UI_COLORS["accent_hover"]
            elif index < phase:
                cell_bg = UI_COLORS["surface"]
                number_bg = UI_COLORS["accent_soft"]
                number_fg = UI_COLORS["accent_hover"]
                label_fg = UI_COLORS["ink"]
            else:
                cell_bg = UI_COLORS["surface"]
                number_bg = UI_COLORS["surface_alt"]
                number_fg = UI_COLORS["muted"]
                label_fg = UI_COLORS["muted"]
            cell.configure(bg=cell_bg)
            number.configure(bg=number_bg, fg=number_fg)
            label.configure(bg=cell_bg, fg=label_fg)

    def _on_status_change(self, *_args) -> None:
        if not self.form_expanded.get():
            phase = workflow_phase_for_status(self.status_var.get())
            if phase == 1 and self.scan_items:
                phase = 3
            self._set_workflow_phase(phase)
        self._sync_action_states()

    @staticmethod
    def _set_widget_enabled(widget: ttk.Widget, enabled: bool) -> None:
        widget.state(["!disabled"] if enabled else ["disabled"])

    def _sync_action_states(self) -> None:
        if not hasattr(self, "scan_button"):
            return
        running = self.task_running()
        self.scan_button.configure(style="Primary.TButton" if not self.scan_items else "Action.TButton")
        self._set_widget_enabled(self.scan_button, not running)
        self._set_widget_enabled(self.start_button, not running and bool(self.checked_scan_paths))
        self._set_widget_enabled(self.stop_button, running)
        self._set_widget_enabled(self.test_button, not running)
        if hasattr(self, "settings_test_button"):
            self._set_widget_enabled(self.settings_test_button, not running)
        self._set_widget_enabled(self.retry_button, not running and bool(self.last_failed_paths))

    def refresh_workspace_summary(self) -> None:
        shown = len(self.visible_scan_items())
        total = len(self.scan_items)
        selected = len(self.checked_scan_paths)
        visible_text = f"{shown} / {total} 个候选" if shown != total else f"{total} 个候选"
        self.workspace_summary_var.set(f"{visible_text} · {selected} 个已选")
        self._sync_action_states()

    def save_and_show_workbench(self) -> None:
        if self.save_form():
            self.toggle_form_panel(force=False)

    def form_data(self) -> dict:
        return {
            "remote_host": self.config_vars["remote_host"].get().strip(),
            "remote_port": self.config_vars["remote_port"].get().strip() or "22",
            "remote_password": self.config_vars["remote_password"].get(),
            "remote_cmd": self.config_vars["remote_cmd"].get().strip() or "pt",
            "remote_bootstrap": bool(self.config_vars["remote_bootstrap"].get()),
            "save_dir": self.config_vars["save_dir"].get().strip() or default_save_dir(),
            "scan_include": self.config_vars["scan_include"].get().strip(),
            "scan_exclude": self.config_vars["scan_exclude"].get().strip(),
            "scan_full": bool(self.config_vars["scan_full"].get()),
            "auto_cleanup": bool(self.config_vars["auto_cleanup"].get()),
        }

    def normalize_connection_fields(self) -> dict | None:
        data = self.form_data()
        if not data["remote_host"] or data["remote_host"].strip().lower() == "root@your-vps":
            messagebox.showerror("缺少配置", "请先填写 VPS 地址。")
            self.toggle_form_panel(force=True)
            return None
        try:
            normalized_host, normalized_port = normalize_remote_connection(
                data["remote_host"],
                data["remote_port"],
            )
        except Exception as exc:
            messagebox.showerror("连接配置无效", str(exc))
            return None
        self.config_vars["remote_host"].set(normalized_host)
        self.config_vars["remote_port"].set(normalized_port)
        data["remote_host"] = normalized_host
        data["remote_port"] = normalized_port
        return data

    def save_form(self) -> bool:
        data = self.normalize_connection_fields()
        if data is None:
            return False
        try:
            save_config(data)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            return False
        self.status_var.set(f"已保存配置：{CONFIG_PATH}")
        self.append_log(f"[gui] 配置已保存到 {CONFIG_PATH}")
        self.refresh_config_summary()
        return True

    def pick_save_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.config_vars["save_dir"].get() or default_save_dir())
        if chosen:
            self.config_vars["save_dir"].set(chosen)
            self.refresh_config_summary()

    def open_config_dir(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        target = str(CONFIG_PATH.parent)
        try:
            if platform.system() == "Windows":
                os.startfile(target)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as exc:
            messagebox.showinfo("配置目录", f"{target}\n\n无法自动打开：{exc}")

    def open_log_file(self) -> None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not LOG_PATH.exists():
            append_gui_log_line("日志文件已创建。")
        target = str(LOG_PATH)
        try:
            if platform.system() == "Windows":
                os.startfile(target)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as exc:
            messagebox.showinfo("日志文件", f"{target}\n\n无法自动打开：{exc}")

    def open_in_file_manager(self, target: str) -> None:
        try:
            if platform.system() == "Windows":
                os.startfile(target)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as exc:
            messagebox.showinfo("打开目录失败", f"{target}\n\n无法自动打开：{exc}")

    def open_save_dir(self) -> None:
        target = Path(self.form_data()["save_dir"]).expanduser()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("无法打开结果目录", str(exc))
            return
        self.open_in_file_manager(str(target))

    def copy_log_view(self) -> None:
        text = self.log_view.get("1.0", "end-1c")
        if not text.strip():
            self.status_var.set("当前没有可复制的日志。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        self.status_var.set("日志已复制到剪贴板。")

    def clear_log_view(self) -> None:
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", END)
        self.log_view.insert(END, "日志显示已清空，磁盘日志仍然保留。\n")
        self.log_view.configure(state="disabled")
        self.status_var.set("已清空当前日志显示。")

    def task_running(self) -> bool:
        legacy_running = self.process is not None and self.process.poll() is None
        backend_running = self.backend_thread is not None and self.backend_thread.is_alive()
        return legacy_running or backend_running

    @staticmethod
    def terminate_shell_process(process: subprocess.Popen[str], *, force: bool = False) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            taskkill = shutil.which("taskkill")
            if taskkill:
                try:
                    result = subprocess.run(
                        [taskkill, "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    if result.returncode == 0:
                        return
                except OSError:
                    pass
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
                return
            except (OSError, ProcessLookupError):
                pass
        try:
            process.kill() if force else process.terminate()
        except OSError:
            pass

    def run_cancellable_capture(
        self,
        command: list[str],
        *,
        env: dict[str, str],
        timeout: int = 1800,
    ) -> subprocess.CompletedProcess[str]:
        if self.shell_cancel_event.is_set():
            raise TaskCancelledError("任务已取消。")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            cwd=str(APP_ROOT),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=(os.name != "nt"),
        )
        self.process = process
        if self.shell_cancel_event.is_set():
            self.terminate_shell_process(process)
        try:
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                self.terminate_shell_process(process, force=True)
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                raise RuntimeError(f"命令执行超时（{timeout} 秒）。") from exc
        finally:
            if self.process is process:
                self.process = None
        if self.shell_cancel_event.is_set():
            raise TaskCancelledError("任务已取消。")
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def clear_backend_task(self, backend: PTBDRemoteBackend) -> None:
        backend.close()
        if self.backend is backend:
            self.backend = None
        self.root.after(0, self.clear_backend_thread_if_idle)

    def clear_backend_thread_if_idle(self) -> None:
        if self.backend_thread and not self.backend_thread.is_alive():
            self.backend_thread = None

    def start_remote_with_backend(self, data: dict, save_dir: Path, selected_path: str) -> None:
        self.start_remote_with_backend_batch(data, save_dir, [selected_path])

    def start_remote_with_backend_batch(self, data: dict, save_dir: Path, selected_paths: list[str]) -> None:
        backend = PTBDRemoteBackend(APP_ROOT, data, logger=self.log_queue.put)
        self.backend = backend
        self.append_log("")
        self.append_log("[gui] 启动方式：内置独立控制后端（本机不再依赖 bash / ssh / scp）")
        self.append_log(f"[gui] 本机保存目录：{save_dir}")
        self.append_log(f"[gui] 已选 {len(selected_paths)} 个候选")
        for index, path in enumerate(selected_paths, start=1):
            self.append_log(f"[gui] 待处理 {index}/{len(selected_paths)}：{path}")
        if data["remote_bootstrap"]:
            self.append_log("[gui] 空白 VPS 自举已开启：先尝试系统依赖自动安装，不够时才回退上传内置运行包")
        self.status_var.set(f"运行中：准备顺序处理 {len(selected_paths)} 个条目")
        self.last_success_paths = []
        self.last_failed_paths = []

        def worker() -> None:
            success_paths: list[str] = []
            failed: list[tuple[str, str]] = []
            last_local: Path | None = None
            try:
                for index, selected_path in enumerate(selected_paths, start=1):
                    self.log_queue.put(f"[gui] 开始处理 {index}/{len(selected_paths)}：{selected_path}")
                    try:
                        local_path = backend.process_selected_path(selected_path, save_dir)
                        last_local = local_path
                        success_paths.append(str(local_path))
                        self.log_queue.put(f"[gui] 成功 {index}/{len(selected_paths)}：{local_path}")
                    except TaskCancelledError:
                        raise
                    except Exception as item_exc:
                        failed.append((selected_path, str(item_exc)))
                        self.log_queue.put(f"[gui] 失败 {index}/{len(selected_paths)}：{selected_path} -> {item_exc}")
                        continue
                self.last_success_paths = success_paths
                self.last_failed_paths = [path for path, _ in failed]
                summary = f"[gui] 批量完成：成功 {len(success_paths)} / 失败 {len(failed)} / 共 {len(selected_paths)}"
                self.log_queue.put(summary)
                if success_paths:
                    if last_local is not None:
                        self.log_queue.put(f"[gui] 成功结果目录：{last_local.parent}")
                    self.log_queue.put("[gui] 成功文件：")
                    for path in success_paths:
                        self.log_queue.put(f"  - {path}")
                if failed:
                    self.log_queue.put("[gui] 失败条目（可点“重试失败”）：")
                    for path, err in failed:
                        self.log_queue.put(f"  - {path} | {err}")
                if failed and not success_paths:
                    self.log_queue.put("[gui] 任务结束，退出码：1")
                elif failed:
                    self.log_queue.put("[gui] 任务结束，退出码：2")
                else:
                    self.log_queue.put("[gui] 任务结束，退出码：0")
            except TaskCancelledError:
                self.log_queue.put("[gui] 任务已取消")
                self.log_queue.put("[gui] 任务结束，退出码：130")
            except Exception as exc:
                self.log_queue.put(f"[gui] 任务失败：{exc}")
                self.log_queue.put("[gui] 任务结束，退出码：1")
            finally:
                self.last_success_paths = list(success_paths)
                self.last_failed_paths = [path for path, _ in failed]
                self.clear_backend_task(backend)

        self.backend_thread = threading.Thread(target=worker, daemon=True)
        self.backend_thread.start()

    def test_connection(self) -> None:
        if self.task_running():
            messagebox.showinfo("任务进行中", "请先等当前任务结束，或点“停止”。")
            return
        if not self.save_form():
            return
        if not backend_available():
            messagebox.showerror(
                "无法测连",
                "当前环境没有内置 Python 后端（缺少 paramiko）。\n请使用打包版，或安装 paramiko 后重试。",
            )
            return
        data = self.form_data()
        self.status_var.set("测连中：正在检查 SSH 与远端依赖")
        self.append_log("[gui] 开始测试连接与依赖预检")
        backend = PTBDRemoteBackend(APP_ROOT, data, logger=self.log_queue.put)
        self.backend = backend

        def worker() -> None:
            try:
                report = backend.diagnose_connection()
                self.log_queue.put(f"[gui] 测连结果：{report.get('message')}")
                self.log_queue.put(
                    "[gui] 远端："
                    f"os={report.get('os_name') or '?'} distro={report.get('distro_id') or '?'} "
                    f"arch={report.get('arch') or '?'}"
                )
                self.log_queue.put(
                    f"[gui] 扫描模式：{report.get('scan_mode')} 根目录={report.get('scan_roots') or '(默认)'}"
                )
                missing = report.get("missing_core_deps") or []
                if missing:
                    self.log_queue.put(f"[gui] 缺少核心依赖：{', '.join(missing)}")
                else:
                    self.log_queue.put("[gui] 核心依赖：已就绪")
                self.log_queue.put(f"[gui] BDInfo：{'可用' if report.get('has_bdinfo') else '未检测到（原盘可能降级）'}")
                for hint in report.get("hints") or []:
                    self.log_queue.put(f"[gui] 建议：{hint}")

                def finish_ui() -> None:
                    if report.get("ok"):
                        self.status_var.set(f"测连成功：{report.get('message')}")
                        messagebox.showinfo("测试连接", report.get("message") or "连接成功")
                    else:
                        self.status_var.set(f"测连失败：{report.get('message')}")
                        messagebox.showerror("测试连接失败", report.get("message") or "连接失败")

                self.root.after(0, finish_ui)
            except TaskCancelledError:
                self.log_queue.put("[gui] 测连已取消")
                self.root.after(0, lambda: self.status_var.set("测连已取消"))
            except Exception as exc:
                message = str(exc)
                self.log_queue.put(f"[gui] 测连异常：{message}")
                self.root.after(0, lambda message=message: self.status_var.set(f"测连失败：{message}"))
                self.root.after(0, lambda message=message: messagebox.showerror("测试连接失败", message))
            finally:
                self.clear_backend_task(backend)

        self.backend_thread = threading.Thread(target=worker, daemon=True)
        self.backend_thread.start()

    def build_remote_shell_env(self, data: dict, save_dir: Path) -> dict[str, str]:
        include_values = split_path_roots(build_effective_scan_include(data))
        exclude_values = split_path_roots(data["scan_exclude"])
        env = os.environ.copy()
        env.update(
            {
                "PTBD_REMOTE_HOST": data["remote_host"],
                "PTBD_REMOTE_PORT": data["remote_port"],
                "PTBD_REMOTE_PASSWORD": data["remote_password"],
                "PTBD_REMOTE_PT_CMD": data["remote_cmd"],
                "PTBD_REMOTE_BOOTSTRAP": "1" if data["remote_bootstrap"] else "0",
                "PTBD_LOCAL_SAVE_DIR": str(save_dir),
                "PTBD_SCAN_INCLUDE_ROOTS": normalize_scan_roots(include_values),
                "PTBD_SCAN_INCLUDE_ROOTS_JSON": json.dumps(include_values, ensure_ascii=False),
                "PTBD_SCAN_INCLUDE_ROOTS_LINES": "\n".join(include_values),
                "PTBD_SCAN_EXCLUDE_ROOTS": normalize_scan_roots(exclude_values),
                "PTBD_SCAN_EXCLUDE_ROOTS_JSON": json.dumps(exclude_values, ensure_ascii=False),
                "PTBD_SCAN_EXCLUDE_ROOTS_LINES": "\n".join(exclude_values),
                "PTBD_AUTO_CLEANUP": "1" if data["auto_cleanup"] else "0",
            }
        )
        return env

    def retry_failed_paths(self) -> None:
        if self.task_running():
            messagebox.showinfo("任务进行中", "请先等当前任务结束。")
            return
        if not self.last_failed_paths:
            messagebox.showinfo("没有失败项", "当前没有可重试的失败条目。请先批量处理后查看失败列表。")
            return
        if not self.save_form():
            return
        data = self.form_data()
        save_dir = Path(data["save_dir"]).expanduser()
        save_dir.mkdir(parents=True, exist_ok=True)
        paths = list(self.last_failed_paths)
        self.checked_scan_paths = set(paths)
        self.refresh_scan_items()
        self.append_log(f"[gui] 重试失败条目：{len(paths)} 个")
        if backend_available():
            self.start_remote_with_backend_batch(data, save_dir, paths)
            return
        bash_bin = find_bash()
        remote_script = shell_script_path()
        if not bash_bin or not shutil.which("ssh") or not remote_script.is_file():
            messagebox.showerror(
                "无法重试",
                "Shell 回退后端不可用。请安装 bash/ssh，或改用包含 Paramiko 的打包版。",
            )
            return
        env = self.build_remote_shell_env(data, save_dir)
        self.start_remote_with_shell_batch(bash_bin, remote_script, env, save_dir, paths)

    def start_remote(self) -> None:
        if self.task_running():
            messagebox.showinfo("任务进行中", "当前已经有任务在运行。")
            return
        if not self.save_form():
            return

        data = self.form_data()
        save_dir = Path(data["save_dir"]).expanduser()
        save_dir.mkdir(parents=True, exist_ok=True)
        selected_paths = self.current_selected_paths()
        if not selected_paths and not self.scan_items:
            self.append_log("[gui] 尚未选择候选，先自动扫描一次 VPS")
            self.status_var.set("扫描中：先自动获取 VPS 候选列表")
            self.scan_remote(auto_start=True)
            return

        if backend_available():
            if not selected_paths:
                messagebox.showinfo("先选条目", "请先在候选列表里勾选一个或多个条目，再点“生成所选”。")
                self.status_var.set("等待选择：先在候选列表里选中要处理的条目")
                return
            self.start_remote_with_backend_batch(data, save_dir, selected_paths)
            return

        bash_bin = find_bash()
        if not bash_bin:
            messagebox.showerror(
                "缺少 bash",
                "当前机器没有找到 bash。\n\n当前也没有可用的内置独立控制后端，所以没法继续。\n"
                "Windows 请先安装 Git for Windows，或直接使用打包后的独立版应用。",
            )
            return
        if not shutil.which("ssh"):
            messagebox.showerror(
                "缺少 ssh",
                "当前机器没有找到 ssh。\n\n当前也没有可用的内置独立控制后端，所以没法继续。\n"
                "Windows 请先安装 Git for Windows，或直接使用打包后的独立版应用。",
            )
            return

        remote_script = shell_script_path()
        if not remote_script.is_file():
            messagebox.showerror("缺少脚本", f"找不到远端入口：{remote_script}")
            return

        env = self.build_remote_shell_env(data, save_dir)
        if not selected_paths:
            self.status_var.set("运行中：未选中候选，将回退到远端菜单模式")
            selected_paths = [""]
        self.start_remote_with_shell_batch(bash_bin, remote_script, env, save_dir, selected_paths)

    def start_remote_with_shell_batch(
        self,
        bash_bin: str,
        remote_script: Path,
        env: dict[str, str],
        save_dir: Path,
        selected_paths: list[str],
    ) -> None:
        self.shell_cancel_event.clear()
        self.append_log("")
        self.append_log(f"[gui] 启动命令：{bash_bin} {remote_script}")
        self.append_log(f"[gui] 本机保存目录：{save_dir}")
        real_paths = [path for path in selected_paths if path]
        if real_paths:
            self.append_log(f"[gui] 已选 {len(real_paths)} 个候选，旧版 shell 模式将顺序处理")
        self.last_success_paths = []
        self.last_failed_paths = []
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        def worker() -> None:
            success_items: list[str] = []
            failed: list[tuple[str, str]] = []
            try:
                for index, selected_path in enumerate(selected_paths, start=1):
                    if self.shell_cancel_event.is_set():
                        self.log_queue.put("[gui] shell 任务已取消")
                        self.log_queue.put("[gui] 任务结束，退出码：130")
                        return
                    step_env = env.copy()
                    if selected_path:
                        step_env["PTBD_REMOTE_TARGET_PATH"] = selected_path
                        self.log_queue.put(f"[gui] shell 模式开始处理 {index}/{len(selected_paths)}：{selected_path}")
                    else:
                        step_env.pop("PTBD_REMOTE_TARGET_PATH", None)
                    item_label = selected_path or "(旧版交互菜单)"
                    try:
                        proc = subprocess.Popen(
                            [bash_bin, str(remote_script)],
                            cwd=str(APP_ROOT),
                            env=step_env,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            bufsize=1,
                            creationflags=creationflags,
                            start_new_session=(os.name != "nt"),
                        )
                        self.process = proc
                        if self.shell_cancel_event.is_set():
                            self.terminate_shell_process(proc)
                        assert proc.stdout is not None
                        for line in proc.stdout:
                            self.log_queue.put(line.rstrip("\r\n"))
                        rc = proc.wait()
                        if self.shell_cancel_event.is_set():
                            self.log_queue.put("[gui] shell 任务已取消")
                            self.log_queue.put("[gui] 任务结束，退出码：130")
                            return
                        if rc != 0:
                            failed.append((selected_path, f"退出码 {rc}"))
                            self.log_queue.put(
                                f"[gui] 失败 {index}/{len(selected_paths)}：{item_label} -> 退出码 {rc}"
                            )
                            continue
                        success_items.append(item_label)
                        self.log_queue.put(f"[gui] 成功 {index}/{len(selected_paths)}：{item_label}")
                    except Exception as exc:
                        failed.append((selected_path, str(exc)))
                        self.log_queue.put(
                            f"[gui] 失败 {index}/{len(selected_paths)}：{item_label} -> {exc}"
                        )
                        continue
                    finally:
                        self.process = None
                self.last_success_paths = list(success_items)
                self.last_failed_paths = [path for path, _ in failed if path]
                self.log_queue.put(
                    f"[gui] 批量完成：成功 {len(success_items)} / 失败 {len(failed)} / 共 {len(selected_paths)}"
                )
                if failed:
                    self.log_queue.put("[gui] 失败条目（可点“重试失败”）：")
                    for path, error in failed:
                        self.log_queue.put(f"  - {path or '(旧版交互菜单)'} | {error}")
                self.log_queue.put(f"[gui] 如果成功，结果应该已经回到：{save_dir}")
                if failed and not success_items:
                    self.log_queue.put("[gui] 任务结束，退出码：1")
                elif failed:
                    self.log_queue.put("[gui] 任务结束，退出码：2")
                else:
                    self.log_queue.put("[gui] 任务结束，退出码：0")
            finally:
                self.last_success_paths = list(success_items)
                self.last_failed_paths = [path for path, _ in failed if path]
                self.process = None

        self.backend_thread = threading.Thread(target=worker, daemon=True)
        self.backend_thread.start()

    def create_askpass_script(self, password: str) -> str:
        suffix, payload = askpass_script_payload(password, windows=os.name == "nt")
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=suffix, encoding="utf-8") as handle:
            script_path = handle.name
            handle.write(payload)
        if os.name != "nt":
            os.chmod(script_path, 0o700)
        return script_path

    def build_ssh_env(self, data: dict) -> tuple[dict[str, str], str | None]:
        env = os.environ.copy()
        askpass_path = None
        password = data["remote_password"]
        if password:
            askpass_path = self.create_askpass_script(password)
            env["SSH_ASKPASS"] = askpass_path
            env["SSH_ASKPASS_REQUIRE"] = "force"
            env.setdefault("DISPLAY", "ptbd-askpass:0")
        return env, askpass_path

    def prepare_remote_runtime(self, data: dict) -> str:
        bash_bin = find_bash()
        if not bash_bin:
            raise RuntimeError("本机缺少 bash。")
        helper_script = bootstrap_script_path()
        if not helper_script.is_file():
            raise RuntimeError(f"找不到自举脚本：{helper_script}")
        helper_env = os.environ.copy()
        helper_env.update(
            {
                "PTBD_REMOTE_HOST": data["remote_host"],
                "PTBD_REMOTE_PORT": data["remote_port"],
                "PTBD_REMOTE_PASSWORD": data["remote_password"],
            }
        )
        result = self.run_cancellable_capture(
            [bash_bin, str(helper_script), "--host", data["remote_host"], "--port", data["remote_port"]],
            env=helper_env,
            timeout=1800,
        )
        if result.stderr.strip():
            for line in result.stderr.strip().splitlines():
                self.log_queue.put(line)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"bootstrap rc={result.returncode}")
        remote_cmd = result.stdout.strip()
        if not remote_cmd:
            raise RuntimeError("远端自举成功了，但没有拿到远端 bdtool 路径。")
        return remote_cmd

    def build_scan_command(self, data: dict, remote_cmd: str) -> list[str]:
        ssh_bin = shutil.which("ssh")
        if not ssh_bin:
            raise RuntimeError("本机缺少 ssh。")
        include_values = split_path_roots(build_effective_scan_include(data))
        exclude_values = split_path_roots(data["scan_exclude"])
        remote_script = " ".join(
            [
            f"export BDTOOL_SCAN_INCLUDE_ROOTS={shlex.quote(normalize_scan_roots(include_values))};"
            if include_values
            else "",
            f"export BDTOOL_SCAN_INCLUDE_ROOTS_JSON={shlex.quote(json.dumps(include_values, ensure_ascii=False))};"
            if include_values
            else "",
            f"export BDTOOL_SCAN_INCLUDE_ROOTS_LINES={shlex.quote(chr(10).join(include_values))};"
            if include_values
            else "",
            f"export BDTOOL_SCAN_EXCLUDE_ROOTS={shlex.quote(normalize_scan_roots(exclude_values))};"
            if exclude_values
            else "",
            f"export BDTOOL_SCAN_EXCLUDE_ROOTS_JSON={shlex.quote(json.dumps(exclude_values, ensure_ascii=False))};"
            if exclude_values
            else "",
            f"export BDTOOL_SCAN_EXCLUDE_ROOTS_LINES={shlex.quote(chr(10).join(exclude_values))};"
            if exclude_values
            else "",
            f"exec {shlex.quote(remote_cmd)} scan-json --full --lang zh",
            ]
        ).strip()
        return [
            ssh_bin,
            "-p",
            data["remote_port"],
            "-o",
            "StrictHostKeyChecking=yes",
            data["remote_host"],
            f"bash -lc {shlex.quote(remote_script)}",
        ]

    def apply_scan_results(self, items: list[dict], *, auto_start: bool = False) -> None:
        self.scan_items = list(items)
        self.checked_scan_paths = reconcile_checked_paths(self.scan_items, self.checked_scan_paths)
        if self.has_active_scan_filter():
            self.apply_scan_filters(auto_start=auto_start)
            return
        self.filtered_scan_items = []
        self.refresh_scan_items(auto_start=auto_start)

    def scan_remote(self, auto_start: bool = False) -> None:
        if not self.save_form():
            return
        if self.task_running():
            messagebox.showinfo("任务进行中", "请先等当前任务结束，或点“停止当前任务”。")
            return
        self.auto_start_after_scan = auto_start
        data = self.form_data()
        self.status_var.set("扫描中：正在从 VPS 获取候选列表")
        self.append_log("[gui] 开始通过 scan-json 获取 VPS 候选列表")

        if backend_available():
            backend = PTBDRemoteBackend(APP_ROOT, data, logger=self.log_queue.put)
            self.backend = backend

            def worker() -> None:
                try:
                    if data["remote_bootstrap"]:
                        self.log_queue.put("[gui] 空白 VPS 自举已开启：先尝试系统依赖自动安装，不够时才回退上传内置运行包")
                    items = backend.scan_items()
                    self.log_queue.put(f"[gui] scan-json 返回 {len(items)} 个候选")
                    self.root.after(0, lambda items=items: self.apply_scan_results(items, auto_start=auto_start))
                except TaskCancelledError:
                    self.log_queue.put("[gui] 扫描已取消")
                    self.root.after(0, lambda: self.status_var.set("扫描已取消"))
                except Exception as exc:
                    self.log_queue.put(f"[gui] 获取候选失败：{exc}")
                    self.root.after(0, lambda: self.status_var.set("获取候选失败：请看下方日志或打开日志文件"))
                finally:
                    self.clear_backend_task(backend)

            self.backend_thread = threading.Thread(target=worker, daemon=True)
            self.backend_thread.start()
            return

        def worker() -> None:
            askpass_path = None
            try:
                remote_cmd = "bdtool"
                if data["remote_bootstrap"]:
                    self.log_queue.put("[gui] 空白 VPS 自举已开启：会先尝试系统依赖自动安装；只有回退内置运行包时才可能上传约 300MB")
                    remote_cmd = self.prepare_remote_runtime(data)
                    self.log_queue.put(f"[gui] 远端运行包就绪：{remote_cmd}")
                cmd = self.build_scan_command(data, remote_cmd)
                env, askpass_path = self.build_ssh_env(data)
                result = self.run_cancellable_capture(cmd, env=env, timeout=1800)
                if result.returncode != 0:
                    if result.stdout.strip():
                        self.log_queue.put("[gui] scan-json stdout:")
                        for line in result.stdout.strip().splitlines():
                            self.log_queue.put(line)
                    if result.stderr.strip():
                        self.log_queue.put("[gui] scan-json stderr:")
                        for line in result.stderr.strip().splitlines():
                            self.log_queue.put(line)
                    raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"ssh rc={result.returncode}")
                if not result.stdout.strip():
                    if result.stderr.strip():
                        self.log_queue.put("[gui] scan-json stderr:")
                        for line in result.stderr.strip().splitlines():
                            self.log_queue.put(line)
                    raise RuntimeError("scan-json 没有返回任何内容，通常是远端命令没有真正执行。")
                payload = json.loads(result.stdout)
                items = payload.get("items", [])
                self.log_queue.put(f"[gui] scan-json 返回 {len(items)} 个候选")
                self.root.after(0, lambda items=items: self.apply_scan_results(items, auto_start=auto_start))
            except TaskCancelledError:
                self.log_queue.put("[gui] 扫描已取消")
                self.root.after(0, lambda: self.status_var.set("扫描已取消"))
            except Exception as exc:
                self.log_queue.put(f"[gui] 获取候选失败：{exc}")
                self.root.after(0, lambda: self.status_var.set("获取候选失败：请看下方日志或打开日志文件"))
            finally:
                if askpass_path:
                    try:
                        os.remove(askpass_path)
                    except OSError:
                        pass

        self.shell_cancel_event.clear()
        self.backend_thread = threading.Thread(target=worker, daemon=True)
        self.backend_thread.start()

    def clear_scan_filters(self) -> None:
        self.filter_type_var.set("全部类型")
        self.filter_keyword_var.set("")
        self.apply_scan_filters()

    def select_visible_scan_items(self) -> None:
        self.checked_scan_paths.update(
            str(item.get("path") or "").strip()
            for item in self.visible_scan_items()
            if str(item.get("path") or "").strip()
        )
        self.refresh_scan_items()

    def clear_checked_scan_items(self) -> None:
        self.checked_scan_paths.clear()
        self.refresh_scan_items()

    def has_active_scan_filter(self) -> bool:
        return bool(self.filter_keyword_var.get().strip()) or self.filter_type_var.get().strip() != "全部类型"

    def visible_scan_items(self) -> list[dict]:
        return self.filtered_scan_items if self.has_active_scan_filter() else self.scan_items

    def apply_scan_filters(self, *, auto_start: bool = False) -> None:
        type_filter = self.filter_type_var.get().strip()
        keyword = self.filter_keyword_var.get().strip().lower()
        self.filtered_scan_items = []
        for item in self.scan_items:
            type_label = str(item.get("type_label", item.get("type", "")))
            path = str(item.get("path", ""))
            if type_filter and type_filter != "全部类型" and type_label != type_filter:
                continue
            haystack = f"{type_label} {path}".lower()
            if keyword and keyword not in haystack:
                continue
            self.filtered_scan_items.append(item)
        self.refresh_scan_items(auto_start=auto_start)

    def refresh_scan_items(self, auto_start: bool = False) -> None:
        previous_paths = set(self.current_selected_paths())
        for item in self.scan_tree.get_children():
            self.scan_tree.delete(item)
        source_items = self.visible_scan_items()
        for item in source_items:
            path = item["path"]
            marker = "☑" if path in self.checked_scan_paths else "☐"
            self.scan_tree.insert("", END, values=(marker, item["index"], item.get("type_label", item["type"]), path))
        shown = len(source_items)
        total = len(self.scan_items)
        if self.has_active_scan_filter() and shown == 0:
            self.status_var.set(f"过滤完成：0 / {total} 个候选匹配")
        else:
            self.status_var.set(f"扫描完成：显示 {shown} / 共 {total} 个候选")
        to_select: list[str] = []
        for item_id in self.scan_tree.get_children():
            values = self.scan_tree.item(item_id, "values")
            if values and len(values) >= 4 and str(values[3]).strip() in previous_paths:
                to_select.append(item_id)
        if to_select:
            self.scan_tree.selection_set(to_select[0])
            self.scan_tree.focus(to_select[0])
        elif source_items:
            first = self.scan_tree.get_children()[0]
            self.scan_tree.selection_set(first)
            self.scan_tree.focus(first)
        else:
            self.selected_path_var.set("当前没有匹配的候选")
            self.refresh_workspace_summary()
            return
        self.on_scan_select()
        self.refresh_workspace_summary()
        if auto_start:
            self.auto_start_after_scan = False
            if len(self.scan_items) == 1:
                only_path = self.scan_items[0].get("path")
                if only_path:
                    self.checked_scan_paths = {str(only_path)}
                    self.refresh_scan_items(auto_start=False)
                self.append_log("[gui] 只发现 1 个候选，自动开始处理")
                self.root.after(0, self.start_remote)
            elif len(self.scan_items) == 0:
                self.status_var.set("扫描完成：没有发现可处理候选")
            else:
                self.status_var.set("扫描完成：已定位第一项，请先勾选要处理的条目，再点“生成所选”")

    def on_scan_select(self, _event=None) -> None:
        checked_count = len(self.checked_scan_paths)
        focus_path = self.current_selected_path()
        if checked_count and focus_path:
            self.selected_path_var.set(f"已勾选 {checked_count} 项，当前定位：{focus_path}")
        elif checked_count:
            sample = next(iter(self.checked_scan_paths))
            self.selected_path_var.set(f"已勾选 {checked_count} 项，第一项：{sample}")
        elif focus_path:
            self.selected_path_var.set(f"当前定位：{focus_path}，左侧打勾后才会批量启动")
        else:
            self.selected_path_var.set("当前未勾选条目")
        self.refresh_workspace_summary()

    def current_selected_path(self) -> str:
        selection = self.scan_tree.selection()
        if not selection:
            return ""
        values = self.scan_tree.item(selection[0], "values")
        if not values or len(values) < 4:
            return ""
        return str(values[3]).strip()

    def current_selected_paths(self) -> list[str]:
        paths: list[str] = []
        for item in self.scan_items:
            path = str(item.get("path", "")).strip()
            if path and path in self.checked_scan_paths and path not in paths:
                paths.append(path)
        return paths

    def selected_scan_item_path(self) -> str:
        return self.current_selected_path()

    def set_scan_item_checked(self, path: str, checked: bool) -> None:
        if not path:
            return
        if checked:
            self.checked_scan_paths.add(path)
        else:
            self.checked_scan_paths.discard(path)

    def toggle_scan_item_checked(self, item_id: str) -> None:
        values = self.scan_tree.item(item_id, "values")
        if not values or len(values) < 4:
            return
        path = str(values[3]).strip()
        if not path:
            return
        checked = path not in self.checked_scan_paths
        self.set_scan_item_checked(path, checked)
        marker = "☑" if checked else "☐"
        self.scan_tree.item(item_id, values=(marker, values[1], values[2], values[3]))
        self.on_scan_select()

    def copy_selected_scan_path(self) -> None:
        selected_path = self.selected_scan_item_path()
        if not selected_path:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(selected_path)
        self.root.update_idletasks()
        self.status_var.set("已复制候选路径到剪贴板")
        self.append_log(f"[gui] 已复制候选路径：{selected_path}")

    def show_selected_scan_path_dialog(self) -> None:
        selected_path = self.selected_scan_item_path()
        if not selected_path:
            return
        self.show_full_path_dialog(selected_path)

    def start_selected_scan_item(self) -> None:
        selected_path = self.selected_scan_item_path()
        if not selected_path:
            return
        self.hide_path_tooltip()
        self.checked_scan_paths = {selected_path}
        focus_item = self.scan_tree.focus()
        if focus_item:
            self.scan_tree.selection_set(focus_item)
        self.refresh_scan_items()
        self.start_remote()

    def hide_path_tooltip(self, _event=None) -> None:
        if self.tooltip_after_id:
            self.root.after_cancel(self.tooltip_after_id)
            self.tooltip_after_id = None
        if self.path_tooltip is not None:
            self.path_tooltip.destroy()
            self.path_tooltip = None
            self.path_tooltip_label = None
        self.tooltip_item = None

    def show_path_tooltip(self, text: str, x: int, y: int, item_id: str) -> None:
        self.hide_path_tooltip()
        tooltip = tk.Toplevel(self.root)
        tooltip.wm_overrideredirect(True)
        tooltip.wm_geometry(f"+{x}+{y}")
        tooltip.configure(bg="#c7d8fb")
        label = tk.Label(
            tooltip,
            text=text,
            background="#f7fbff",
            foreground="#23314d",
            justify="left",
            padx=10,
            pady=6,
            font=("Consolas", 9),
            borderwidth=0,
        )
        label.pack()
        self.path_tooltip = tooltip
        self.path_tooltip_label = label
        self.tooltip_item = item_id

    def on_scan_tree_hover(self, event) -> None:
        region = self.scan_tree.identify("region", event.x, event.y)
        column = self.scan_tree.identify_column(event.x)
        item_id = self.scan_tree.identify_row(event.y)
        if region != "cell" or column != "#4" or not item_id:
            self.hide_path_tooltip()
            return
        values = self.scan_tree.item(item_id, "values")
        if not values or len(values) < 4:
            self.hide_path_tooltip()
            return
        full_path = str(values[3]).strip()
        bbox = self.scan_tree.bbox(item_id, column)
        if not bbox:
            self.hide_path_tooltip()
            return
        text_width = self.tree_font.measure(full_path)
        visible_width = max(bbox[2] - 12, 0)
        if text_width <= visible_width:
            self.hide_path_tooltip()
            return
        if self.tooltip_item == item_id and self.path_tooltip is not None:
            return

        x = self.scan_tree.winfo_rootx() + min(event.x + 18, self.scan_tree.winfo_width() - 80)
        y = self.scan_tree.winfo_rooty() + event.y + 22

        if self.tooltip_after_id:
            self.root.after_cancel(self.tooltip_after_id)
        self.tooltip_after_id = self.root.after(
            250,
            lambda: self.show_path_tooltip(full_path, x, y, item_id),
        )

    def on_scan_double_click(self, _event=None) -> None:
        selected_path = self.current_selected_path()
        if selected_path:
            self.hide_path_tooltip()
            self.show_full_path_dialog(selected_path)

    def on_scan_click(self, event) -> str | None:
        item_id = self.scan_tree.identify_row(event.y)
        column = self.scan_tree.identify_column(event.x)
        if not item_id:
            return None
        self.scan_tree.focus(item_id)
        if column == "#1":
            self.toggle_scan_item_checked(item_id)
            self.scan_tree.selection_set(item_id)
            return "break"
        return None

    def on_scan_space(self, _event=None) -> str:
        item_id = self.scan_tree.focus()
        if item_id:
            self.toggle_scan_item_checked(item_id)
            self.scan_tree.selection_set(item_id)
        return "break"

    def on_scan_return(self, _event=None) -> str:
        self.show_selected_scan_path_dialog()
        return "break"

    def on_scan_right_click(self, event) -> None:
        item_id = self.scan_tree.identify_row(event.y)
        if not item_id or self.scan_context_menu is None:
            return
        self.hide_path_tooltip()
        self.scan_tree.selection_set(item_id)
        self.scan_tree.focus(item_id)
        self.on_scan_select()
        self.scan_context_menu.tk_popup(event.x_root, event.y_root)

    def copy_full_path(self, path: str, status_var: tk.StringVar) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(path)
        self.root.update_idletasks()
        status_var.set("已复制到剪贴板")

    def open_parent_dir_for_path(self, path: str, status_var: tk.StringVar) -> None:
        target = Path(path).expanduser()
        parent = target if target.is_dir() else target.parent
        if not parent.exists():
            status_var.set("目录不存在，无法打开")
            return
        self.open_in_file_manager(str(parent))
        status_var.set(f"已尝试打开目录：{parent}")

    def show_full_path_dialog(self, path: str) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("完整路径")
        dialog.transient(self.root)
        dialog.resizable(True, False)
        dialog.geometry("760x180")
        dialog.minsize(540, 160)

        frame = ttk.Frame(dialog, padding=14, style="Panel.TFrame")
        frame.pack(fill=BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        ttk.Label(frame, text="完整路径", style="Field.TLabel").grid(row=0, column=0, sticky=W)
        path_box = tk.Text(
            frame,
            wrap="word",
            height=4,
            font=tkfont.nametofont("TkFixedFont"),
            background=UI_COLORS["surface_soft"],
            foreground=UI_COLORS["ink"],
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=UI_COLORS["line"],
            highlightcolor=UI_COLORS["accent"],
            padx=10,
            pady=8,
        )
        path_box.grid(row=1, column=0, sticky="nsew", pady=(6, 10))
        path_box.insert("1.0", path)
        path_box.configure(state="disabled")

        footer = ttk.Frame(frame, style="Panel.TFrame")
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        copy_status = tk.StringVar(value="可复制后粘贴到终端、文件管理器或 SSH 命令中")
        ttk.Label(footer, textvariable=copy_status, style="PanelHint.TLabel").grid(row=0, column=0, sticky=W)
        ttk.Button(
            footer,
            text="复制路径",
            command=lambda: self.copy_full_path(path, copy_status),
            style="Action.TButton",
        ).grid(row=0, column=1, padx=(10, 0))
        ttk.Button(
            footer,
            text="打开目录",
            command=lambda: self.open_parent_dir_for_path(path, copy_status),
            style="Action.TButton",
        ).grid(row=0, column=2, padx=(8, 0))
        close_button = ttk.Button(footer, text="关闭", command=dialog.destroy, style="Action.TButton")
        close_button.grid(row=0, column=3, padx=(8, 0))

        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()
        close_button.focus_set()

    def _start_reader(self, proc: subprocess.Popen[str]) -> None:
        def read_stream() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.log_queue.put(line.rstrip("\n"))
            rc = proc.wait()
            self.log_queue.put(f"[gui] 任务结束，退出码：{rc}")
            if rc == 0:
                self.log_queue.put(f"[gui] 成功：请到本机保存目录查看结果：{self.form_data()['save_dir']}")
            else:
                self.log_queue.put(f"[gui] 失败：退出码 {rc}。请检查上方日志、VPS 依赖与选中路径。")

        thread = threading.Thread(target=read_stream, daemon=True)
        thread.start()
        self.reader_threads.append(thread)

    def stop_remote(self) -> None:
        if self.backend_thread and self.backend_thread.is_alive() and self.backend is not None:
            self.backend.cancel()
            self.append_log("[gui] 已请求停止当前独立后端任务")
            self.status_var.set("已请求停止，请稍等。")
            return
        if self.backend_thread and self.backend_thread.is_alive():
            self.shell_cancel_event.set()
            try:
                if self.process and self.process.poll() is None:
                    self.terminate_shell_process(self.process)
                self.append_log("[gui] 已请求停止当前任务")
                self.status_var.set("已请求停止，请稍等。")
            except Exception as exc:
                messagebox.showerror("停止失败", str(exc))
            return
        self.status_var.set("当前没有运行中的任务。")

    def append_log(self, text: str) -> None:
        self.append_log_lines((text,))

    def append_log_lines(self, lines) -> None:
        normalized = [str(line).rstrip("\r\n") for line in lines]
        if not normalized:
            return
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(normalized) + "\n")
        self.log_view.configure(state="normal")
        self.log_view.insert(END, "\n".join(normalized) + "\n")
        line_count = int(self.log_view.index("end-1c").split(".", 1)[0])
        if line_count > 4000:
            self.log_view.delete("1.0", f"{line_count - 4000}.0")
        self.log_view.see(END)
        self.log_view.configure(state="disabled")

    def _poll_logs(self) -> None:
        pending: list[str] = []
        for _ in range(250):
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            pending.append(line)
        if pending:
            self.append_log_lines(pending)
        for line in pending:
            if line.startswith("[gui] 任务结束"):
                if "退出码：0" in line:
                    self.status_var.set("任务成功结束。可打开本机保存目录查看结果包。")
                elif "退出码：2" in line:
                    self.status_var.set("任务部分成功。可点“重试失败”继续处理失败项。")
                elif "退出码：130" in line:
                    self.status_var.set("任务已取消。")
                else:
                    self.status_var.set("任务失败。请查看日志，或先点“测试连接”检查依赖。")
            elif line.startswith("[gui] 成功：结果已保存到") or line.startswith("[gui] 成功结果目录："):
                self.status_var.set(line.replace("[gui] ", "", 1))
            elif line.startswith("[gui] 批量完成："):
                self.status_var.set(line.replace("[gui] ", "", 1))
            elif line.startswith("[gui] 测连结果："):
                self.status_var.set(line.replace("[gui] ", "", 1))
        self._sync_action_states()
        self.root.after(25 if not self.log_queue.empty() else 150, self._poll_logs)

    def on_close(self) -> None:
        if self.task_running():
            if not messagebox.askyesno("退出", "当前任务还在运行。确定要退出并停止它吗？"):
                return
            self.stop_remote()
            time.sleep(0.2)
        self.root.destroy()


def run_ui_smoke_check() -> int:
    root = tk.Tk()
    configure_gradient_theme(root)
    app = App(root)
    app.toggle_form_panel(force=False)
    widgets = {
        "candidates": app.scan_tree,
        "log": app.log_view,
        "scan_button": app.scan_button,
        "start_button": app.start_button,
    }
    failures: list[str] = []
    if PRODUCT_NAME not in root.title():
        failures.append("window title does not use the current product name")

    def check_workbench(label: str) -> None:
        root.update_idletasks()
        root.update()
        root_bottom = root.winfo_rooty() + root.winfo_height()
        for name, widget in widgets.items():
            if not widget.winfo_viewable():
                failures.append(f"{label}: {name} is not visible")
                continue
            widget_bottom = widget.winfo_rooty() + widget.winfo_height()
            if widget_bottom > root_bottom + 1:
                failures.append(f"{label}: {name} extends below the window")
        if app.scan_tree.winfo_height() < 70:
            failures.append(f"{label}: candidates pane is too short")
        if app.log_view.winfo_height() < 60:
            failures.append(f"{label}: log pane is too short")

    check_workbench("default")
    default_geometry = f"{root.winfo_width()}x{root.winfo_height()}"
    root.geometry("820x700")
    check_workbench("minimum")
    minimum_geometry = f"{root.winfo_width()}x{root.winfo_height()}"
    if app.candidate_hint.winfo_ismapped() or app.activity_hint.winfo_ismapped():
        failures.append("minimum: workspace helper text was not collapsed")
    app.toggle_form_panel(force=True)
    root.update_idletasks()
    root.update()
    app._scroll_settings_widget_into_view(app.settings_save_button)
    root.update_idletasks()
    settings_bottom = app.settings_canvas.winfo_rooty() + app.settings_canvas.winfo_height()
    save_bottom = app.settings_save_button.winfo_rooty() + app.settings_save_button.winfo_height()
    if not app.settings_canvas.winfo_viewable() or save_bottom > settings_bottom + 1:
        failures.append("minimum: settings actions are not reachable by scrolling")
    if not isinstance(app.scan_button, ttk.Button) or not isinstance(app.start_button, ttk.Button):
        failures.append("primary actions are not native ttk buttons")
    root.destroy()
    if failures:
        print(f"ui_layout=FAIL default={default_geometry} minimum={minimum_geometry}: {'; '.join(failures)}")
        return 1
    print(
        f"ui_layout=PASS default={default_geometry} minimum={minimum_geometry} "
        "panes=candidates,log settings=scrollable controls=ttk"
    )
    return 0


def cli_main() -> int:
    if "--print-config-path" in sys.argv:
        print(CONFIG_PATH)
        return 0
    if "--ui-smoke-check" in sys.argv:
        return run_ui_smoke_check()
    if "--self-check" in sys.argv:
        bash_bin = find_bash() or "<missing>"
        ssh_bin = shutil.which("ssh") or "<missing>"
        print(f"app_root={APP_ROOT}")
        print(f"config={CONFIG_PATH}")
        print(f"log={LOG_PATH}")
        print(f"config_mode={config_storage_mode()}")
        print(f"backend={backend_status()}")
        print(f"bash={bash_bin}")
        print(f"ssh={ssh_bin}")
        print(f"remote_script={shell_script_path()}")
        print(f"bootstrap_script={bootstrap_script_path()}")
        try:
            assets = validate_profile(APP_ROOT, "controller")
        except AssetManifestError as exc:
            print(f"runtime_assets=FAIL: {exc}")
            return 1
        print(f"runtime_assets=PASS ({len(assets)} files)")
        return 0

    root = tk.Tk()
    configure_gradient_theme(root)
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
