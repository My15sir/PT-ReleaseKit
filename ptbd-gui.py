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
from tkinter.scrolledtext import ScrolledText

from ptbd_remote_backend import (
    PTBDRemoteBackend,
    TaskCancelledError,
    backend_available,
    backend_status,
)


APP_NAME = "PT-BDtool"


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


def configure_cyberpunk_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    colors = {
        "bg": "#050816",
        "bg_alt": "#0b1023",
        "panel": "#0c1328",
        "panel_alt": "#101935",
        "panel_edge": "#172348",
        "entry": "#09101f",
        "text": "#d7e9ff",
        "muted": "#7d9cc0",
        "accent": "#24f2ff",
        "accent_soft": "#1889b8",
        "magenta": "#ff2ed1",
        "warning": "#f9ff6c",
        "danger": "#ff668f",
        "button": "#111b35",
        "button_hover": "#16264b",
        "button_pressed": "#08111f",
    }

    root.configure(bg=colors["bg"])

    style.configure(
        ".",
        background=colors["bg"],
        foreground=colors["text"],
        bordercolor=colors["accent_soft"],
        darkcolor=colors["panel_edge"],
        lightcolor=colors["accent_soft"],
        troughcolor=colors["panel"],
        fieldbackground=colors["entry"],
        focuscolor=colors["accent"],
    )
    style.configure("TFrame", background=colors["bg"])
    style.configure(
        "Panel.TFrame",
        background=colors["panel"],
        borderwidth=1,
        relief="solid",
        bordercolor=colors["accent_soft"],
        lightcolor=colors["accent_soft"],
        darkcolor=colors["panel_edge"],
    )
    style.configure(
        "Hero.TFrame",
        background=colors["panel_alt"],
        borderwidth=1,
        relief="solid",
        bordercolor=colors["magenta"],
        lightcolor=colors["accent"],
        darkcolor=colors["panel_edge"],
    )
    style.configure("TLabel", background=colors["bg"], foreground=colors["text"])
    style.configure(
        "Title.TLabel",
        background=colors["panel_alt"],
        foreground=colors["accent"],
        font=("Arial", 16, "bold"),
    )
    style.configure(
        "Subtitle.TLabel",
        background=colors["panel_alt"],
        foreground=colors["muted"],
        font=("Arial", 10),
    )
    style.configure(
        "Field.TLabel",
        background=colors["panel"],
        foreground=colors["accent"],
        font=("Arial", 10, "bold"),
    )
    style.configure(
        "Hint.TLabel",
        background=colors["panel"],
        foreground=colors["muted"],
        font=("Arial", 9),
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
        padding=(12, 8),
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
        borderwidth=1,
        relief="solid",
        bordercolor=colors["accent_soft"],
        lightcolor=colors["accent_soft"],
        darkcolor=colors["panel_edge"],
    )
    style.configure(
        "Section.TLabelframe.Label",
        background=colors["bg"],
        foreground=colors["magenta"],
        font=("Arial", 10, "bold"),
    )
    style.configure(
        "Cyber.TEntry",
        fieldbackground=colors["entry"],
        foreground=colors["text"],
        bordercolor=colors["accent_soft"],
        lightcolor=colors["accent_soft"],
        darkcolor=colors["panel_edge"],
        padding=6,
    )
    style.map(
        "Cyber.TEntry",
        bordercolor=[("focus", colors["accent"]), ("!focus", colors["accent_soft"])],
        lightcolor=[("focus", colors["accent"]), ("!focus", colors["accent_soft"])],
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
            ("active", colors["accent"]),
            ("!disabled", colors["text"]),
        ],
        background=[("active", colors["panel"]), ("!disabled", colors["panel"])],
    )
    style.configure(
        "Action.TButton",
        background=colors["button"],
        foreground=colors["accent"],
        bordercolor=colors["accent_soft"],
        lightcolor=colors["accent_soft"],
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
            ("pressed", colors["accent"]),
            ("active", colors["warning"]),
            ("!disabled", colors["accent"]),
        ],
        bordercolor=[("active", colors["accent"]), ("!disabled", colors["accent_soft"])],
    )
    style.configure(
        "Accent.TButton",
        background=colors["accent"],
        foreground=colors["bg"],
        bordercolor=colors["accent"],
        lightcolor=colors["accent"],
        darkcolor=colors["accent_soft"],
        padding=(10, 7),
        font=("Arial", 10, "bold"),
    )
    style.map(
        "Accent.TButton",
        background=[
            ("pressed", colors["accent_soft"]),
            ("active", colors["warning"]),
        ],
        foreground=[("!disabled", colors["bg"])],
    )
    style.configure(
        "Danger.TButton",
        background=colors["panel_alt"],
        foreground=colors["danger"],
        bordercolor=colors["magenta"],
        lightcolor=colors["danger"],
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
            ("active", colors["warning"]),
            ("!disabled", colors["danger"]),
        ],
    )
    style.configure(
        "Cyber.Treeview",
        background=colors["entry"],
        fieldbackground=colors["entry"],
        foreground=colors["text"],
        bordercolor=colors["accent_soft"],
        lightcolor=colors["accent_soft"],
        darkcolor=colors["panel_edge"],
        rowheight=28,
    )
    style.map(
        "Cyber.Treeview",
        background=[("selected", colors["magenta"])],
        foreground=[("selected", colors["bg"])],
    )
    style.configure(
        "Cyber.Treeview.Heading",
        background=colors["panel_alt"],
        foreground=colors["accent"],
        bordercolor=colors["magenta"],
        lightcolor=colors["magenta"],
        darkcolor=colors["panel_edge"],
        font=("Arial", 10, "bold"),
        padding=(6, 6),
    )
    style.map(
        "Cyber.Treeview.Heading",
        background=[("active", colors["button_hover"])],
        foreground=[("active", colors["warning"]), ("!disabled", colors["accent"])],
    )


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("PT-BDtool 小白启动器")
        self.root.geometry("920x720")
        self.root.minsize(920, 720)
        self.process: subprocess.Popen[str] | None = None
        self.reader_threads: list[threading.Thread] = []
        self.backend: PTBDRemoteBackend | None = None
        self.backend_thread: threading.Thread | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.status_var = tk.StringVar(value="就绪：先填 VPS，扫描候选后双击条目或点“一步到位启动”")
        self.selected_path_var = tk.StringVar(value="")
        self.config_vars = {}
        self.scan_items: list[dict] = []
        self.auto_start_after_scan = False
        self._build_ui()
        self._load_into_form(load_config())
        self._poll_logs()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill=BOTH, expand=True)

        hero = ttk.Frame(container, style="Hero.TFrame", padding=(18, 16))
        hero.pack(fill=X, pady=(0, 12))

        title = ttk.Label(
            hero,
            text="PT-BDtool 小白启动器（Win / macOS / Linux MVP）",
            style="Title.TLabel",
        )
        title.pack(anchor=W)

        subtitle = ttk.Label(
            hero,
            text="第一次填好 VPS 和保存目录。扫描目录留空时，会自动优先扫常见媒体目录；扫到候选后可直接双击开跑。",
            style="Subtitle.TLabel",
            wraplength=840,
            justify="left",
        )
        subtitle.pack(anchor=W, pady=(4, 12))

        form_panel = ttk.LabelFrame(container, text="连接配置", style="Section.TLabelframe", padding=8)
        form_panel.pack(fill=X, pady=(0, 10))
        form = ttk.Frame(form_panel, style="Panel.TFrame", padding=12)
        form.pack(fill=X)

        self._add_entry(form, "VPS 地址", "remote_host", 0, "例如：root@1.2.3.4")
        self._add_entry(form, "SSH 端口", "remote_port", 1, "默认 22")
        self._add_entry(form, "SSH 密码", "remote_password", 2, "留空表示走密钥", show="*")
        self._add_entry(form, "远端命令", "remote_cmd", 3, "源码旧模式才需要，一般别改")
        self._add_entry(form, "扫描白名单", "scan_include", 4, "留空=自动扫描 /home /root /data /mnt /media /srv")
        self._add_entry(form, "额外排除", "scan_exclude", 5, "可留空")
        self._add_entry(form, "本机保存目录", "save_dir", 6, "结果回到本机这里")

        save_row = ttk.Frame(form, style="Panel.TFrame")
        save_row.grid(row=7, column=1, sticky="ew", pady=(2, 8))
        ttk.Button(save_row, text="选择目录", command=self.pick_save_dir, style="Action.TButton").pack(side=LEFT)
        self.config_vars["auto_cleanup"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            save_row,
            text="成功后自动清理 VPS 生成目录",
            variable=self.config_vars["auto_cleanup"],
        ).pack(side=LEFT, padx=(12, 0))
        self.config_vars["remote_bootstrap"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            save_row,
            text="空白 VPS 自动上传运行包（推荐）",
            variable=self.config_vars["remote_bootstrap"],
        ).pack(side=LEFT, padx=(12, 0))

        form.columnconfigure(1, weight=1)

        tips_panel = ttk.LabelFrame(container, text="运行说明", style="Section.TLabelframe", padding=8)
        tips_panel.pack(fill=X, pady=(0, 10))
        tips_body = ttk.Frame(tips_panel, style="Panel.TFrame", padding=12)
        tips_body.pack(fill=X)

        tips = ttk.Label(
            tips_body,
            text=(
                f"说明：当前优先走 {standalone_backend_label()}。打包后的 Windows / macOS 独立版不再依赖本机 Python、Git、bash、ssh；"
                "源码直跑时若缺少内置后端，才会回退旧版 shell 模式。空白 VPS 会优先尝试 Debian / Ubuntu / Alpine 自动装依赖，"
                "只有不够时才回退内置运行包。"
            ),
            style="Tips.TLabel",
            wraplength=840,
            justify="left",
        )
        tips.pack(anchor=W)

        actions = ttk.Frame(container, style="Panel.TFrame", padding=12)
        actions.pack(fill=X, pady=(0, 10))
        ttk.Button(actions, text="保存配置", command=self.save_form, style="Action.TButton").pack(side=LEFT)
        ttk.Button(
            actions,
            text="打开配置目录",
            command=self.open_config_dir,
            style="Action.TButton",
        ).pack(side=LEFT, padx=(8, 0))
        ttk.Button(
            actions,
            text="打开日志文件",
            command=self.open_log_file,
            style="Action.TButton",
        ).pack(side=LEFT, padx=(8, 0))
        ttk.Button(
            actions,
            text="扫描 VPS 候选",
            command=self.scan_remote,
            style="Accent.TButton",
        ).pack(side=LEFT, padx=(8, 0))
        ttk.Button(
            actions,
            text="一步到位启动",
            command=self.start_remote,
            style="Accent.TButton",
        ).pack(side=LEFT, padx=(8, 0))
        ttk.Button(
            actions,
            text="停止当前任务",
            command=self.stop_remote,
            style="Danger.TButton",
        ).pack(side=LEFT, padx=(8, 0))

        status = ttk.Label(container, textvariable=self.status_var, style="Status.TLabel", wraplength=860, justify="left")
        status.pack(anchor=W, pady=(4, 8))

        scan_panel = ttk.LabelFrame(container, text="VPS 候选列表（新接口预览）", style="Section.TLabelframe", padding=8)
        scan_panel.pack(fill=BOTH, expand=False, pady=(0, 10))
        scan_body = ttk.Frame(scan_panel, style="Panel.TFrame", padding=8)
        scan_body.pack(fill=BOTH, expand=True)
        columns = ("index", "type", "path")
        self.scan_tree = ttk.Treeview(scan_body, columns=columns, show="headings", height=8, style="Cyber.Treeview")
        self.scan_tree.heading("index", text="#")
        self.scan_tree.heading("type", text="类型")
        self.scan_tree.heading("path", text="路径")
        self.scan_tree.column("index", width=56, anchor="center")
        self.scan_tree.column("type", width=90, anchor="center")
        self.scan_tree.column("path", width=720, anchor="w")
        self.scan_tree.pack(fill=BOTH, expand=True)
        self.scan_tree.bind("<<TreeviewSelect>>", self.on_scan_select)
        self.scan_tree.bind("<Double-1>", self.on_scan_double_click)
        ttk.Label(scan_body, textvariable=self.selected_path_var, style="Path.TLabel").pack(anchor=W, pady=(6, 0))

        log_panel = ttk.LabelFrame(container, text="运行日志", style="Section.TLabelframe", padding=8)
        log_panel.pack(fill=BOTH, expand=True)
        log_body = ttk.Frame(log_panel, style="Panel.TFrame", padding=10)
        log_body.pack(fill=BOTH, expand=True)

        self.log_view = ScrolledText(
            log_body,
            wrap="word",
            font=("Consolas", 10),
            background="#09101f",
            foreground="#d7e9ff",
            insertbackground="#24f2ff",
            selectbackground="#ff2ed1",
            selectforeground="#050816",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#1889b8",
            highlightcolor="#24f2ff",
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

    def _add_entry(self, parent, label_text: str, key: str, row: int, hint: str, show: str | None = None) -> None:
        ttk.Label(parent, text=label_text, style="Field.TLabel").grid(row=row, column=0, sticky=W, padx=(0, 10), pady=4)
        variable = tk.StringVar()
        self.config_vars[key] = variable
        entry = ttk.Entry(parent, textvariable=variable, show=show or "", style="Cyber.TEntry")
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Label(parent, text=hint, style="Hint.TLabel").grid(row=row, column=2, sticky=W, padx=(10, 0), pady=4)

    def _load_into_form(self, data: dict) -> None:
        for key, value in data.items():
            var = self.config_vars.get(key)
            if isinstance(var, tk.BooleanVar):
                var.set(bool(value))
            elif var is not None:
                var.set(str(value))

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

    def save_form(self) -> bool:
        data = self.form_data()
        if not data["remote_host"]:
            messagebox.showerror("缺少配置", "请先填写 VPS 地址。")
            return False
        try:
            save_config(data)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            return False
        self.status_var.set(f"已保存配置：{CONFIG_PATH}")
        self.append_log(f"[gui] 配置已保存到 {CONFIG_PATH}")
        return True

    def pick_save_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.config_vars["save_dir"].get() or default_save_dir())
        if chosen:
            self.config_vars["save_dir"].set(chosen)

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
        backend = PTBDRemoteBackend(APP_ROOT, data, logger=self.log_queue.put)
        self.backend = backend
        self.append_log("")
        self.append_log("[gui] 启动方式：内置独立控制后端（本机不再依赖 bash / ssh / scp）")
        self.append_log(f"[gui] 本机保存目录：{save_dir}")
        self.append_log(f"[gui] 自动处理选中候选：{selected_path}")
        if data["remote_bootstrap"]:
            self.append_log("[gui] 空白 VPS 自举已开启：先尝试系统依赖自动安装，不够时才回退上传内置运行包")
        self.status_var.set("运行中：已直接下发生成/下载/清理任务")

        def worker() -> None:
            try:
                local_path = backend.process_selected_path(selected_path, save_dir)
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
        selected_path = self.current_selected_path()
        if not selected_path and not self.scan_items:
            self.append_log("[gui] 尚未选择候选，先自动扫描一次 VPS")
            self.status_var.set("扫描中：先自动获取 VPS 候选列表")
            self.scan_remote(auto_start=True)
            return

        if backend_available():
            if not selected_path:
                messagebox.showinfo("先选条目", "请先在候选列表里选中或双击一个条目，再点“一步到位启动”。")
                self.status_var.set("等待选择：先在候选列表里选中要处理的条目")
                return
            self.start_remote_with_backend(data, save_dir, selected_path)
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
        if selected_path:
            env["PTBD_REMOTE_TARGET_PATH"] = selected_path

        cmd = [bash_bin, str(remote_script)]
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW

        self.append_log("")
        self.append_log(f"[gui] 启动命令：{' '.join(cmd)}")
        self.append_log(f"[gui] 本机保存目录：{save_dir}")
        if selected_path:
            self.append_log(f"[gui] 自动处理选中候选：{selected_path}")
            self.status_var.set("运行中：已直接下发生成/下载/清理任务")
        else:
            self.status_var.set("运行中：未选中候选，将回退到远端菜单模式")

        self.process = subprocess.Popen(
            cmd,
            cwd=str(APP_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        self._start_reader(self.process)

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

    def refresh_scan_items(self, auto_start: bool = False) -> None:
        for item in self.scan_tree.get_children():
            self.scan_tree.delete(item)
        for item in self.scan_items:
            self.scan_tree.insert("", END, values=(item["index"], item.get("type_label", item["type"]), item["path"]))
        self.status_var.set(f"扫描完成：共 {len(self.scan_items)} 个候选")
        if self.scan_items:
            first = self.scan_tree.get_children()[0]
            self.scan_tree.selection_set(first)
            self.scan_tree.focus(first)
        if auto_start:
            self.auto_start_after_scan = False
            if len(self.scan_items) == 1:
                self.append_log("[gui] 只发现 1 个候选，自动开始处理")
                self.root.after(0, self.start_remote)
            elif len(self.scan_items) == 0:
                self.status_var.set("扫描完成：没有发现可处理候选")
            else:
                self.status_var.set("扫描完成：已自动选中第一项，请双击或点“一步到位启动”继续")

    def on_scan_select(self, _event=None) -> None:
        selection = self.scan_tree.selection()
        if not selection:
            self.selected_path_var.set("")
            return
        values = self.scan_tree.item(selection[0], "values")
        if values:
            self.selected_path_var.set(f"当前选中：{values[2]}")

    def current_selected_path(self) -> str:
        selection = self.scan_tree.selection()
        if not selection:
            return ""
        values = self.scan_tree.item(selection[0], "values")
        if not values or len(values) < 3:
            return ""
        return str(values[2]).strip()

    def on_scan_double_click(self, _event=None) -> None:
        if self.current_selected_path():
            self.start_remote()

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
    configure_cyberpunk_theme(root)
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
