#!/usr/bin/env python3
import json
import os
import platform
import queue
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

from ptbd_remote_backend import (
    PTBDRemoteBackend,
    TaskCancelledError,
    backend_available,
    backend_status,
    normalize_remote_connection,
)


APP_NAME = "PT-BDtool"
CONTENT_INSET_X = 14
HEADER_TEXT_INSET_X = 16


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
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "桌面",
        home / "Downloads",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)
    return str(home)


DEFAULT_CONFIG = {
    "remote_host": "root@your-vps",
    "remote_port": "22",
    "remote_password": "",
    "remote_cmd": "pt",
    "remote_bootstrap": True,
    "save_dir": default_save_dir(),
    "scan_include": "",
    "scan_exclude": "",
    "auto_cleanup": True,
}


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        return DEFAULT_CONFIG.copy()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_CONFIG.copy()
    merged = DEFAULT_CONFIG.copy()
    merged.update({k: v for k, v in data.items() if k in merged})
    return merged


def save_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def blend_hex_color(start: str, end: str, ratio: float) -> str:
    ratio = max(0.0, min(1.0, ratio))
    start_rgb = tuple(int(start[index : index + 2], 16) for index in (1, 3, 5))
    end_rgb = tuple(int(end[index : index + 2], 16) for index in (1, 3, 5))
    mixed = tuple(int(a + (b - a) * ratio) for a, b in zip(start_rgb, end_rgb))
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def hero_gradient_color(ratio: float) -> str:
    if ratio <= 0.55:
        return blend_hex_color("#5678f0", "#4f99ef", ratio / 0.55 if ratio else 0.0)
    return blend_hex_color("#4f99ef", "#79b5f4", (ratio - 0.55) / 0.45)


def configure_gradient_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    colors = {
        "bg": "#eef3ff",
        "bg_alt": "#f6f9ff",
        "panel": "#ffffff",
        "panel_alt": "#f4f8ff",
        "panel_edge": "#e0e0e0",
        "entry": "#ffffff",
        "text": "#23314d",
        "muted": "#6c7a96",
        "accent": "#4f7cff",
        "accent_soft": "#b9d8ff",
        "accent_deep": "#365ad6",
        "warning": "#4b64b8",
        "danger": "#d96d6d",
        "danger_soft": "#fdeeee",
        "button": "#eef4ff",
        "button_hover": "#e2ebff",
        "button_pressed": "#d5e2ff",
        "tree_select": "#dce8ff",
        "tree_header": "#edf4ff",
        "log_bg": "#f8fbff",
    }

    root.configure(bg=colors["bg"])

    style.configure(
        ".",
        background=colors["bg"],
        foreground=colors["text"],
        bordercolor=colors["accent_soft"],
        darkcolor=colors["panel_edge"],
        lightcolor=colors["accent_soft"],
        troughcolor=colors["panel_alt"],
        fieldbackground=colors["entry"],
        focuscolor=colors["accent"],
    )
    style.configure("TFrame", background=colors["bg"])
    style.configure(
        "Panel.TFrame",
        background=colors["panel"],
        borderwidth=0,
        relief="flat",
    )
    style.configure("TLabel", background=colors["bg"], foreground=colors["text"])
    style.configure(
        "Field.TLabel",
        background=colors["panel"],
        foreground=colors["accent_deep"],
        font=("Arial", 10, "bold"),
    )
    style.configure(
        "Hint.TLabel",
        background=colors["panel"],
        foreground=colors["muted"],
        font=("Arial", 9),
    )
    style.configure(
        "PanelHint.TLabel",
        background=colors["panel_alt"],
        foreground=colors["muted"],
        font=("Arial", 9),
        padding=(0, 2),
    )
    style.configure(
        "Tips.TLabel",
        background=colors["panel"],
        foreground=colors["text"],
        font=("Arial", 10),
    )
    style.configure(
        "Status.TLabel",
        background=colors["panel_alt"],
        foreground=colors["warning"],
        font=("Arial", 10, "bold"),
        padding=(0, 8),
    )
    style.configure(
        "Path.TLabel",
        background=colors["panel"],
        foreground=colors["muted"],
        font=("Consolas", 9),
    )
    style.configure(
        "Section.TLabelframe",
        background=colors["bg"],
        borderwidth=0,
        relief="flat",
    )
    style.configure(
        "Section.TLabelframe.Label",
        background=colors["bg"],
        foreground=colors["accent_deep"],
        font=("Arial", 10, "bold"),
        padding=(0, 0, 0, 0),
    )
    style.configure(
        "Cyber.TEntry",
        fieldbackground=colors["entry"],
        foreground=colors["text"],
        bordercolor=colors["panel_edge"],
        lightcolor=colors["panel_edge"],
        darkcolor=colors["panel_edge"],
        padding=6,
    )
    style.map(
        "Cyber.TEntry",
        bordercolor=[("focus", colors["accent"]), ("!focus", colors["panel_edge"])],
        lightcolor=[("focus", colors["accent"]), ("!focus", colors["panel_edge"])],
    )
    style.configure(
        "TCheckbutton",
        background=colors["panel"],
        foreground=colors["text"],
    )
    style.map(
        "TCheckbutton",
        foreground=[
            ("disabled", colors["muted"]),
            ("active", colors["accent_deep"]),
            ("!disabled", colors["text"]),
        ],
        background=[("active", colors["panel"]), ("!disabled", colors["panel"])],
    )
    style.configure(
        "HeroNote.TLabel",
        background=colors["bg"],
        foreground=colors["accent_deep"],
        font=("Arial", 9, "bold"),
        padding=(10, 4),
    )
    style.configure(
        "Primary.TButton",
        background=colors["accent_deep"],
        foreground="#ffffff",
        bordercolor=colors["accent_deep"],
        lightcolor=colors["accent_deep"],
        darkcolor=colors["accent_deep"],
        padding=(14, 9),
        font=("Arial", 10, "bold"),
    )
    style.map(
        "Primary.TButton",
        background=[
            ("pressed", "#2747b5"),
            ("active", colors["accent"]),
        ],
        foreground=[("!disabled", "#ffffff")],
    )
    style.configure(
        "Action.TButton",
        background=colors["button"],
        foreground=colors["accent_deep"],
        bordercolor=colors["panel_edge"],
        lightcolor=colors["panel_edge"],
        darkcolor=colors["panel_edge"],
        padding=(10, 7),
    )
    style.map(
        "Action.TButton",
        background=[
            ("pressed", colors["button_pressed"]),
            ("active", colors["button_hover"]),
        ],
        foreground=[
            ("pressed", colors["accent_deep"]),
            ("active", colors["accent"]),
            ("!disabled", colors["accent_deep"]),
        ],
        bordercolor=[("active", colors["accent_soft"]), ("!disabled", colors["panel_edge"])],
    )
    style.configure(
        "Accent.TButton",
        background=colors["accent"],
        foreground="#ffffff",
        bordercolor=colors["accent"],
        lightcolor=colors["accent"],
        darkcolor=colors["accent_deep"],
        padding=(10, 7),
        font=("Arial", 10, "bold"),
    )
    style.map(
        "Accent.TButton",
        background=[
            ("pressed", colors["accent_deep"]),
            ("active", "#6d96ff"),
        ],
        foreground=[("!disabled", "#ffffff")],
    )
    style.configure(
        "Toolbar.TFrame",
        background=colors["bg"],
    )
    style.configure(
        "Danger.TButton",
        background=colors["danger_soft"],
        foreground=colors["danger"],
        bordercolor="#f3caca",
        lightcolor="#f3caca",
        darkcolor=colors["panel_edge"],
        padding=(10, 7),
    )
    style.map(
        "Danger.TButton",
        background=[
            ("pressed", colors["button_pressed"]),
            ("active", colors["button_hover"]),
        ],
        foreground=[
            ("pressed", colors["danger"]),
            ("active", "#c45757"),
            ("!disabled", colors["danger"]),
        ],
    )
    style.configure(
        "Cyber.Treeview",
        background=colors["panel"],
        fieldbackground=colors["panel"],
        foreground=colors["text"],
        bordercolor=colors["panel_edge"],
        lightcolor=colors["panel_edge"],
        darkcolor=colors["panel_edge"],
        rowheight=28,
    )
    style.map(
        "Cyber.Treeview",
        background=[("selected", colors["tree_select"])],
        foreground=[("selected", colors["text"])],
    )
    style.configure(
        "Cyber.Treeview.Heading",
        background=colors["tree_header"],
        foreground=colors["accent_deep"],
        bordercolor=colors["panel_edge"],
        lightcolor=colors["panel_edge"],
        darkcolor=colors["panel_edge"],
        font=("Arial", 10, "bold"),
        padding=(6, 6),
    )
    style.map(
        "Cyber.Treeview.Heading",
        background=[("active", colors["button_hover"])],
        foreground=[("active", colors["accent"]), ("!disabled", colors["accent_deep"])],
    )


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("PT-BDtool 小白启动器")
        self.root.geometry("920x760")
        self.root.minsize(920, 720)
        self.process: subprocess.Popen[str] | None = None
        self.reader_threads: list[threading.Thread] = []
        self.backend: PTBDRemoteBackend | None = None
        self.backend_thread: threading.Thread | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.status_var = tk.StringVar(value="就绪：先填 VPS，扫描候选后双击条目或点“启动所选条目”")
        self.selected_path_var = tk.StringVar(value="")
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
        self._build_ui()
        self._load_into_form(load_config())
        self._poll_logs()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill=BOTH, expand=True)

        self.hero_canvas = tk.Canvas(
            container,
            height=76,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
            background="#eef3ff",
        )
        self.hero_canvas.pack(fill=X, pady=(0, 8))
        self.hero_canvas.bind("<Configure>", self._on_hero_resize)
        self._render_hero_banner(888)

        form_panel = ttk.LabelFrame(container, text="连接与保存设置", style="Section.TLabelframe", padding=0)
        form_panel.pack(fill=X, pady=(0, 6))
        form_summary = ttk.Frame(form_panel, style="Panel.TFrame", padding=(CONTENT_INSET_X, 12))
        form_summary.pack(fill=X)
        form_summary.columnconfigure(0, weight=1)
        ttk.Label(form_summary, textvariable=self.summary_host_var, style="Field.TLabel").grid(row=0, column=0, sticky=W)
        ttk.Label(form_summary, textvariable=self.summary_save_var, style="PanelHint.TLabel").grid(
            row=1, column=0, sticky=W, pady=(2, 0)
        )
        summary_actions = ttk.Frame(form_summary, style="Panel.TFrame")
        summary_actions.grid(row=0, column=1, rowspan=2, padx=(10, 0), sticky="e")
        ttk.Button(summary_actions, text="💾 保存", command=self.save_form, style="Action.TButton").pack(side=LEFT)
        ttk.Button(
            summary_actions,
            text="📁 配置",
            command=self.open_config_dir,
            style="Action.TButton",
        ).pack(side=LEFT, padx=(8, 0))
        ttk.Button(
            summary_actions,
            text="📜 日志",
            command=self.open_log_file,
            style="Action.TButton",
        ).pack(side=LEFT, padx=(8, 0))
        self.form_toggle_button = ttk.Button(
            form_summary,
            text="▾ 设置",
            command=self.toggle_form_panel,
            style="Action.TButton",
        )
        self.form_toggle_button.grid(row=0, column=2, rowspan=2, padx=(10, 0))

        self.form_details = ttk.Frame(
            form_panel,
            style="Panel.TFrame",
            padding=(CONTENT_INSET_X, 6, CONTENT_INSET_X, 14),
        )
        form = self.form_details

        self._add_compact_entry(form, "VPS 地址", "remote_host", 0, 0, "例如：root@1.2.3.4 或 ssh root@1.2.3.4")
        self._add_compact_entry(form, "SSH 端口", "remote_port", 0, 1, "默认 22")
        self._add_compact_entry(form, "SSH 密码", "remote_password", 1, 0, "留空表示走密钥", show="*")
        self._add_compact_entry(form, "远端命令", "remote_cmd", 1, 1, "只有源码旧模式才需要")
        self._add_compact_entry(form, "扫描白名单", "scan_include", 2, 0, "留空时自动扫描常见媒体目录")
        self._add_compact_entry(form, "额外排除", "scan_exclude", 2, 1, "可留空")

        save_group = ttk.Frame(form, style="Panel.TFrame", padding=(0, 8, 0, 0))
        save_group.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        save_group.columnconfigure(0, weight=1)
        ttk.Label(save_group, text="本机保存目录", style="Field.TLabel").grid(row=0, column=0, sticky=W)
        ttk.Label(save_group, text="生成完成后，结果会回到这个目录", style="PanelHint.TLabel").grid(
            row=1, column=0, sticky=W, pady=(2, 6)
        )
        save_path_row = ttk.Frame(save_group, style="Panel.TFrame")
        save_path_row.grid(row=2, column=0, sticky="ew")
        save_path_row.columnconfigure(0, weight=1)
        variable = tk.StringVar()
        variable.trace_add("write", lambda *_args: self.refresh_config_summary())
        self.config_vars["save_dir"] = variable
        entry = ttk.Entry(save_path_row, textvariable=variable, style="Cyber.TEntry")
        entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(save_path_row, text="📂 选择", command=self.pick_save_dir, style="Action.TButton").grid(
            row=0, column=1, padx=(8, 0)
        )

        option_group = ttk.Frame(form, style="Panel.TFrame", padding=(0, 12, 0, 0))
        option_group.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        option_group.columnconfigure(0, weight=1)
        ttk.Label(option_group, text="运行选项", style="Field.TLabel").grid(row=0, column=0, sticky=W)
        ttk.Label(option_group, text="这些设置会影响回传和远端清理行为", style="PanelHint.TLabel").grid(
            row=1, column=0, sticky=W, pady=(2, 8)
        )
        self.config_vars["auto_cleanup"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            option_group,
            text="成功后自动清理 VPS 生成目录",
            variable=self.config_vars["auto_cleanup"],
        ).grid(row=2, column=0, sticky=W, pady=(0, 6))
        self.config_vars["remote_bootstrap"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            option_group,
            text="空白 VPS 自动上传运行包（推荐）",
            variable=self.config_vars["remote_bootstrap"],
        ).grid(row=3, column=0, sticky=W)

        form.columnconfigure(0, weight=1, uniform="form")
        form.columnconfigure(1, weight=1, uniform="form")
        self.toggle_form_panel(force=False)

        tips = ttk.Label(
            container,
            text=(
                f"当前优先走 {standalone_backend_label()}。如果只是反复扫描和启动，通常不用一直展开上面的低频配置。"
            ),
            style="Hint.TLabel",
            wraplength=860,
            justify="left",
        )
        tips.pack(anchor=W, pady=(0, 6))

        status = ttk.Label(container, textvariable=self.status_var, style="Status.TLabel", wraplength=860, justify="left")
        status.pack(anchor=W, pady=(2, 12))

        scan_panel = ttk.LabelFrame(container, text="VPS 候选列表（新接口预览）", style="Section.TLabelframe", padding=0)
        scan_panel.pack(fill=BOTH, expand=True, pady=(0, 8))
        scan_body = ttk.Frame(
            scan_panel,
            style="Panel.TFrame",
            padding=(CONTENT_INSET_X, 12, CONTENT_INSET_X, 12),
        )
        scan_body.pack(fill=BOTH, expand=True)
        actions = ttk.Frame(scan_body, style="Toolbar.TFrame")
        actions.pack(fill=X, pady=(0, 8))
        primary_actions = ttk.Frame(actions, style="Panel.TFrame")
        primary_actions.pack(side=LEFT, fill=X, expand=True)
        ttk.Button(
            primary_actions,
            text="🔎 扫描",
            command=self.scan_remote,
            style="Primary.TButton",
        ).pack(side=LEFT)
        ttk.Button(
            primary_actions,
            text="▶ 启动",
            command=self.start_remote,
            style="Accent.TButton",
        ).pack(side=LEFT, padx=(10, 0))
        ttk.Button(
            primary_actions,
            text="■ 停止",
            command=self.stop_remote,
            style="Danger.TButton",
        ).pack(side=LEFT, padx=(10, 0))
        ttk.Label(
            primary_actions,
            text="提示：左侧勾选即可多选",
            style="PanelHint.TLabel",
        ).pack(side=LEFT, padx=(12, 0))
        filter_bar = ttk.Frame(scan_body, style="Panel.TFrame")
        filter_bar.pack(fill=X, pady=(0, 8))
        ttk.Label(filter_bar, text="过滤", style="Field.TLabel").pack(side=LEFT)
        type_box = ttk.Combobox(
            filter_bar,
            textvariable=self.filter_type_var,
            values=("全部类型", "视频", "音频", "原盘", "镜像"),
            state="readonly",
            width=10,
        )
        type_box.pack(side=LEFT, padx=(10, 8))
        type_box.bind("<<ComboboxSelected>>", lambda _event: self.apply_scan_filters())
        keyword_entry = ttk.Entry(filter_bar, textvariable=self.filter_keyword_var, style="Cyber.TEntry")
        keyword_entry.pack(side=LEFT, fill=X, expand=True)
        keyword_entry.bind("<KeyRelease>", lambda _event: self.apply_scan_filters())
        ttk.Button(filter_bar, text="✕ 清空", command=self.clear_scan_filters, style="Action.TButton").pack(
            side=LEFT, padx=(8, 0)
        )
        scan_list = ttk.Frame(scan_body, style="Panel.TFrame")
        scan_list.pack(fill=BOTH, expand=True)
        columns = ("pick", "index", "type", "path")
        self.scan_tree = ttk.Treeview(
            scan_list,
            columns=columns,
            show="headings",
            height=18,
            style="Cyber.Treeview",
            selectmode="browse",
        )
        self.scan_tree.heading("pick", text="选择")
        self.scan_tree.heading("index", text="#")
        self.scan_tree.heading("type", text="类型")
        self.scan_tree.heading("path", text="路径")
        self.scan_tree.column("pick", width=76, anchor="center", stretch=False)
        self.scan_tree.column("index", width=56, anchor="center")
        self.scan_tree.column("type", width=100, anchor="center")
        self.scan_tree.column("path", width=980, minwidth=560, anchor="w", stretch=True)
        scan_scrollbar = ttk.Scrollbar(scan_list, orient="vertical", command=self.scan_tree.yview)
        scan_scrollbar_x = ttk.Scrollbar(scan_body, orient="horizontal", command=self.scan_tree.xview)
        self.scan_tree.configure(yscrollcommand=scan_scrollbar.set, xscrollcommand=scan_scrollbar_x.set)
        self.scan_tree.pack(side=LEFT, fill=BOTH, expand=True)
        scan_scrollbar.pack(side=LEFT, fill="y", padx=(6, 0))
        scan_scrollbar_x.pack(fill=X, pady=(8, 0))
        self.scan_tree.bind("<<TreeviewSelect>>", self.on_scan_select)
        self.scan_tree.bind("<Double-1>", self.on_scan_double_click)
        self.scan_tree.bind("<Button-1>", self.on_scan_click, add="+")
        self.scan_tree.bind("<Motion>", self.on_scan_tree_hover)
        self.scan_tree.bind("<Leave>", self.hide_path_tooltip)
        self.scan_tree.bind("<ButtonPress-1>", self.hide_path_tooltip)
        self.scan_tree.bind("<Button-3>", self.on_scan_right_click)
        self.scan_tree.bind("<Control-Button-1>", self.on_scan_right_click)
        ttk.Label(
            scan_body,
            text="左侧勾选列用于批量启动；单击勾选，双击路径看完整内容，路径列支持横向滚动。",
            style="PanelHint.TLabel",
        ).pack(anchor=W, pady=(6, 2))
        ttk.Label(scan_body, textvariable=self.selected_path_var, style="Path.TLabel").pack(anchor=W, pady=(2, 0))
        self.scan_context_menu = tk.Menu(self.root, tearoff=0)
        self.scan_context_menu.add_command(label="📋 复制路径", command=self.copy_selected_scan_path)
        self.scan_context_menu.add_command(label="👁 查看完整路径", command=self.show_selected_scan_path_dialog)
        self.scan_context_menu.add_command(label="▶ 直接启动", command=self.start_selected_scan_item)

        log_panel = ttk.LabelFrame(container, text="运行日志", style="Section.TLabelframe", padding=8)
        log_panel.pack(fill=BOTH, expand=False)
        log_body = ttk.Frame(log_panel, style="Panel.TFrame", padding=8)
        log_body.pack(fill=BOTH, expand=True)

        self.log_view = ScrolledText(
            log_body,
            wrap="word",
            font=("Consolas", 10),
            height=5,
            background="#f8fbff",
            foreground="#23314d",
            insertbackground="#4f7cff",
            selectbackground="#cfe0ff",
            selectforeground="#23314d",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#d7e1f3",
            highlightcolor="#7da8ff",
            padx=12,
            pady=10,
        )
        self.log_view.pack(fill=BOTH, expand=True)
        self.log_view.insert(END, f"App root: {APP_ROOT}\n")
        self.log_view.insert(END, f"Config: {CONFIG_PATH}\n")
        self.log_view.insert(END, f"Log: {LOG_PATH}\n")
        self.log_view.insert(END, f"Config mode: {config_storage_mode()}\n")
        self.log_view.insert(END, f"Backend: {backend_status()}\n")
        self.log_view.insert(END, "准备完成。\n")
        self.log_view.configure(state="disabled")
        for line in (
            f"App root: {APP_ROOT}",
            f"Config: {CONFIG_PATH}",
            f"Log: {LOG_PATH}",
            f"Config mode: {config_storage_mode()}",
            f"Backend: {backend_status()}",
            "准备完成。",
        ):
            append_gui_log_line(line)

    def _render_hero_banner(self, width: int) -> None:
        width = max(width, 420)
        height = int(self.hero_canvas.cget("height"))
        steps = 96
        self.hero_canvas.delete("all")
        for index in range(steps):
            ratio = index / max(steps - 1, 1)
            color = hero_gradient_color(ratio)
            x0 = width * index / steps
            x1 = width * (index + 1) / steps
            self.hero_canvas.create_rectangle(x0, 0, x1 + 1, height, outline="", fill=color)
        self.hero_canvas.create_text(
            HEADER_TEXT_INSET_X,
            14,
            anchor="nw",
            text="PT-BDtool 小白启动器（Win / macOS / Linux MVP）",
            fill="#ffffff",
            font=("Arial", 15, "bold"),
        )
        self.hero_canvas.create_text(
            HEADER_TEXT_INSET_X,
            46,
            anchor="nw",
            width=max(width - HEADER_TEXT_INSET_X * 2, 180),
            text="先填连接信息，再扫描候选；确认条目后再启动。保存目录、回传和自动清理都在下面分组展示。",
            fill="#18305f",
            font=("Arial", 10),
        )

    def _on_hero_resize(self, event) -> None:
        self._render_hero_banner(event.width)

    def _add_entry(self, parent, label_text: str, key: str, row: int, hint: str, show: str | None = None) -> None:
        ttk.Label(parent, text=label_text, style="Field.TLabel").grid(row=row, column=0, sticky=W, padx=(0, 10), pady=4)
        variable = tk.StringVar()
        self.config_vars[key] = variable
        entry = ttk.Entry(parent, textvariable=variable, show=show or "", style="Cyber.TEntry")
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Label(parent, text=hint, style="Hint.TLabel").grid(row=row, column=2, sticky=W, padx=(10, 0), pady=4)

    def _add_compact_entry(self, parent, label_text: str, key: str, row: int, column: int, hint: str, show: str | None = None) -> None:
        field = ttk.Frame(parent, style="Panel.TFrame", padding=(0, 0, 14 if column == 0 else 0, 0))
        field.grid(row=row, column=column, sticky="nsew", padx=(0, 10 if column == 0 else 0), pady=3)
        field.columnconfigure(0, weight=1)
        ttk.Label(field, text=label_text, style="Field.TLabel").grid(row=0, column=0, sticky=W)
        variable = tk.StringVar()
        self.config_vars[key] = variable
        variable.trace_add("write", lambda *_args: self.refresh_config_summary())
        entry = ttk.Entry(field, textvariable=variable, show=show or "", style="Cyber.TEntry")
        entry.grid(row=1, column=0, sticky="ew", pady=(4, 2))
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
        self.summary_host_var.set(f"VPS：{host_value or '未配置'}")
        self.summary_save_var.set(f"保存目录：{save_value or default_save_dir()}")

    def toggle_form_panel(self, force: bool | None = None) -> None:
        expanded = (not self.form_expanded.get()) if force is None else bool(force)
        self.form_expanded.set(expanded)
        if expanded:
            self.form_details.pack(fill=X, pady=(8, 0))
            self.form_toggle_button.configure(text="▴ 设置")
        else:
            self.form_details.pack_forget()
            self.form_toggle_button.configure(text="▾ 设置")

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
            "auto_cleanup": bool(self.config_vars["auto_cleanup"].get()),
        }

    def normalize_connection_fields(self) -> dict | None:
        data = self.form_data()
        if not data["remote_host"]:
            messagebox.showerror("缺少配置", "请先填写 VPS 地址。")
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

    def task_running(self) -> bool:
        legacy_running = self.process is not None and self.process.poll() is None
        backend_running = self.backend_thread is not None and self.backend_thread.is_alive()
        return legacy_running or backend_running

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

        def worker() -> None:
            try:
                local_path = None
                for index, selected_path in enumerate(selected_paths, start=1):
                    self.log_queue.put(f"[gui] 开始处理 {index}/{len(selected_paths)}：{selected_path}")
                    local_path = backend.process_selected_path(selected_path, save_dir)
                    self.log_queue.put(f"[gui] 完成 {index}/{len(selected_paths)}：{selected_path}")
                if local_path is not None:
                    self.log_queue.put(f"[gui] 如果成功，结果应该已经回到：{local_path.parent}")
                self.log_queue.put("[gui] 任务结束，退出码：0")
            except TaskCancelledError:
                self.log_queue.put("[gui] 任务已取消")
                self.log_queue.put("[gui] 任务结束，退出码：130")
            except Exception as exc:
                self.log_queue.put(f"[gui] 任务失败：{exc}")
                self.log_queue.put("[gui] 任务结束，退出码：1")
            finally:
                self.clear_backend_task(backend)

        self.backend_thread = threading.Thread(target=worker, daemon=True)
        self.backend_thread.start()

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
                messagebox.showinfo("先选条目", "请先在候选列表里选中一个或多个条目，再点“启动所选条目”。")
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

        env = os.environ.copy()
        env.update(
            {
                "PTBD_REMOTE_HOST": data["remote_host"],
                "PTBD_REMOTE_PORT": data["remote_port"],
                "PTBD_REMOTE_PASSWORD": data["remote_password"],
                "PTBD_REMOTE_PT_CMD": data["remote_cmd"],
                "PTBD_REMOTE_BOOTSTRAP": "1" if data["remote_bootstrap"] else "0",
                "PTBD_LOCAL_SAVE_DIR": str(save_dir),
                "PTBD_SCAN_INCLUDE_ROOTS": data["scan_include"],
                "PTBD_SCAN_EXCLUDE_ROOTS": data["scan_exclude"],
                "PTBD_AUTO_CLEANUP": "1" if data["auto_cleanup"] else "0",
            }
        )
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
        self.append_log("")
        self.append_log(f"[gui] 启动命令：{bash_bin} {remote_script}")
        self.append_log(f"[gui] 本机保存目录：{save_dir}")
        real_paths = [path for path in selected_paths if path]
        if real_paths:
            self.append_log(f"[gui] 已选 {len(real_paths)} 个候选，旧版 shell 模式将顺序处理")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        def worker() -> None:
            try:
                for index, selected_path in enumerate(selected_paths, start=1):
                    step_env = env.copy()
                    if selected_path:
                        step_env["PTBD_REMOTE_TARGET_PATH"] = selected_path
                        self.log_queue.put(f"[gui] shell 模式开始处理 {index}/{len(selected_paths)}：{selected_path}")
                    else:
                        step_env.pop("PTBD_REMOTE_TARGET_PATH", None)
                    proc = subprocess.Popen(
                        [bash_bin, str(remote_script)],
                        cwd=str(APP_ROOT),
                        env=step_env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        text=True,
                        bufsize=1,
                        creationflags=creationflags,
                    )
                    self.process = proc
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        self.log_queue.put(line.rstrip("\n"))
                    rc = proc.wait()
                    if rc != 0:
                        self.log_queue.put(f"[gui] 任务结束，退出码：{rc}")
                        return
                self.log_queue.put(f"[gui] 如果成功，结果应该已经回到：{save_dir}")
                self.log_queue.put("[gui] 任务结束，退出码：0")
            finally:
                self.process = None

        self.backend_thread = threading.Thread(target=worker, daemon=True)
        self.backend_thread.start()

    def create_askpass_script(self, password: str) -> str:
        suffix = ".cmd" if os.name == "nt" else ".sh"
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=suffix, encoding="utf-8") as handle:
            script_path = handle.name
            if os.name == "nt":
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
                    ("!", "^^!"),
                ):
                    escaped = escaped.replace(old, new)
                handle.write("@echo off\n")
                handle.write("setlocal DisableDelayedExpansion\n")
                handle.write(f"echo {escaped}\n")
            else:
                handle.write("#!/usr/bin/env bash\n")
                escaped = password.replace("'", "'\\''")
                handle.write(f"printf '%s\\n' '{escaped}'\n")
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
        result = subprocess.run(
            [bash_bin, str(helper_script), "--host", data["remote_host"], "--port", data["remote_port"]],
            cwd=str(APP_ROOT),
            env=helper_env,
            text=True,
            capture_output=True,
            timeout=1800,
            check=False,
            stdin=subprocess.DEVNULL,
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
        remote_script = " ".join(
            [
            f"export BDTOOL_SCAN_INCLUDE_ROOTS={shlex.quote(data['scan_include'])};" if data["scan_include"] else "",
            f"export BDTOOL_SCAN_EXCLUDE_ROOTS={shlex.quote(data['scan_exclude'])};" if data["scan_exclude"] else "",
            f"exec {shlex.quote(remote_cmd)} scan-json --full --lang zh",
            ]
        ).strip()
        return [
            ssh_bin,
            "-p",
            data["remote_port"],
            "-o",
            "StrictHostKeyChecking=accept-new",
            data["remote_host"],
            f"bash -lc {shlex.quote(remote_script)}",
        ]

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
                    self.scan_items = backend.scan_items()
                    self.log_queue.put(f"[gui] scan-json 返回 {len(self.scan_items)} 个候选")
                    self.root.after(0, lambda: self.refresh_scan_items(auto_start=auto_start))
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
                result = subprocess.run(
                    cmd,
                    cwd=str(APP_ROOT),
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=1800,
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
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
                self.scan_items = payload.get("items", [])
                self.log_queue.put(f"[gui] scan-json 返回 {len(self.scan_items)} 个候选")
                self.root.after(0, lambda: self.refresh_scan_items(auto_start=auto_start))
            except Exception as exc:
                self.log_queue.put(f"[gui] 获取候选失败：{exc}")
                self.root.after(0, lambda: self.status_var.set("获取候选失败：请看下方日志或打开日志文件"))
            finally:
                if askpass_path:
                    try:
                        os.remove(askpass_path)
                    except OSError:
                        pass

        threading.Thread(target=worker, daemon=True).start()

    def clear_scan_filters(self) -> None:
        self.filter_type_var.set("全部类型")
        self.filter_keyword_var.set("")
        self.apply_scan_filters()

    def has_active_scan_filter(self) -> bool:
        return bool(self.filter_keyword_var.get().strip()) or self.filter_type_var.get().strip() != "全部类型"

    def visible_scan_items(self) -> list[dict]:
        return self.filtered_scan_items if self.has_active_scan_filter() else self.scan_items

    def apply_scan_filters(self) -> None:
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
        self.refresh_scan_items()

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
            return
        self.on_scan_select()
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
                self.status_var.set("扫描完成：已定位第一项，请先勾选要处理的条目，再点“启动所选条目”")

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
        visible_checked: set[str] = set()
        for item_id in self.scan_tree.get_children():
            values = self.scan_tree.item(item_id, "values")
            if values and len(values) >= 4:
                path = str(values[3]).strip()
                if path in self.checked_scan_paths:
                    visible_checked.add(path)
                if path and path in self.checked_scan_paths and path not in paths:
                    paths.append(path)
        if paths:
            return paths
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
            font=("Consolas", 10),
            background="#f8fbff",
            foreground="#23314d",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#d7e1f3",
            highlightcolor="#7da8ff",
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
            text="📋 复制",
            command=lambda: self.copy_full_path(path, copy_status),
            style="Action.TButton",
        ).grid(row=0, column=1, padx=(10, 0))
        ttk.Button(
            footer,
            text="📂 目录",
            command=lambda: self.open_parent_dir_for_path(path, copy_status),
            style="Action.TButton",
        ).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(
            footer,
            text="✕ 关闭",
            command=dialog.destroy,
            style="Action.TButton",
        ).grid(row=0, column=3, padx=(8, 0))

        dialog.grab_set()
        dialog.focus_set()

    def _start_reader(self, proc: subprocess.Popen[str]) -> None:
        def read_stream() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.log_queue.put(line.rstrip("\n"))
            rc = proc.wait()
            self.log_queue.put(f"[gui] 任务结束，退出码：{rc}")
            self.log_queue.put(f"[gui] 如果成功，结果应该已经回到：{self.form_data()['save_dir']}")

        thread = threading.Thread(target=read_stream, daemon=True)
        thread.start()
        self.reader_threads.append(thread)

    def stop_remote(self) -> None:
        if self.backend_thread and self.backend_thread.is_alive() and self.backend is not None:
            self.backend.cancel()
            self.append_log("[gui] 已请求停止当前独立后端任务")
            self.status_var.set("已请求停止，请稍等。")
            return
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.append_log("[gui] 已请求停止当前任务")
                self.status_var.set("已请求停止，请稍等。")
            except Exception as exc:
                messagebox.showerror("停止失败", str(exc))
            return
        self.status_var.set("当前没有运行中的任务。")

    def append_log(self, text: str) -> None:
        append_gui_log_line(text)
        self.log_view.configure(state="normal")
        self.log_view.insert(END, text + "\n")
        self.log_view.see(END)
        self.log_view.configure(state="disabled")

    def _poll_logs(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.append_log(line)
                if line.startswith("[gui] 任务结束"):
                    self.status_var.set("任务已结束。请查看上面的输出和本机保存目录。")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_logs)

    def on_close(self) -> None:
        if self.task_running():
            if not messagebox.askyesno("退出", "当前任务还在运行。确定要退出并停止它吗？"):
                return
            self.stop_remote()
            time.sleep(0.2)
        self.root.destroy()


def cli_main() -> int:
    if "--print-config-path" in sys.argv:
        print(CONFIG_PATH)
        return 0
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
        return 0

    root = tk.Tk()
    configure_gradient_theme(root)
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
