#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ptbd_remote_backend import (
    PTBDRemoteBackend,
    TaskCancelledError,
    backend_available,
    backend_status,
    normalize_remote_connection,
)


APP_NAME = "PT-BDtool Web"
CONFIG_PATH = Path(os.environ.get("PTBD_WEB_CONFIG", Path.home() / ".config/ptbd-web/config.json"))
DEFAULT_PORT = 8899
MAX_LOG_LINES = 2000


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
        Path(os.environ.get("PTBDTOOL_ROOT", "")),
        Path(os.environ.get("PTBD_INSTALL_ROOT", "")),
        script_dir,
        script_dir.parent,
        Path("/opt/PT-BDtool"),
        Path.home() / ".local/share/pt-bdtool/PT-BDtool-app",
    ]
    for candidate in candidates:
        if str(candidate) and is_app_root(candidate):
            return candidate.resolve()
    return script_dir


APP_ROOT = find_app_root()


def default_save_dir() -> str:
    home = Path.home()
    for candidate in (home / "Desktop", home / "桌面", home / "Downloads"):
        if candidate.is_dir():
            return str(candidate)
    return str(home / "PT-BDtool-downloads")


DEFAULT_CONFIG: dict[str, Any] = {
    "mode": os.environ.get("PTBD_WEB_MODE", "remote"),
    "local_root": os.environ.get("PTBD_WEB_LOCAL_ROOT", "/"),
    "remote_host": "root@your-vps",
    "remote_port": "22",
    "remote_password": "",
    "remote_cmd": "pt",
    "remote_bootstrap": True,
    "save_dir": default_save_dir(),
    "scan_include": "",
    "scan_exclude": "",
    "audio_spectrum_mode": "single",
    "auto_cleanup": True,
}


def load_config(*, include_secret: bool = False) -> dict[str, Any]:
    data = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.is_file():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update(raw)
        except Exception:
            pass
    if not include_secret:
        data["remote_password"] = ""
        data["password_saved"] = bool(load_config(include_secret=True).get("remote_password"))
    return data


def sanitize_config(raw: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = existing or load_config(include_secret=True)
    data = DEFAULT_CONFIG.copy()
    data.update(existing)

    for key in ("remote_host", "remote_port", "remote_cmd", "save_dir", "scan_include", "scan_exclude", "local_root"):
        if key in raw:
            data[key] = str(raw.get(key) or "").strip()

    if "mode" in raw:
        data["mode"] = "local" if str(raw.get("mode") or "").strip().lower() == "local" else "remote"
    if "audio_spectrum_mode" in raw:
        requested_spectrum_mode = str(raw.get("audio_spectrum_mode") or "").strip().lower()
        data["audio_spectrum_mode"] = requested_spectrum_mode if requested_spectrum_mode in {"single", "combined"} else "single"

    data["mode"] = "local" if str(data.get("mode") or "").strip().lower() == "local" else "remote"
    data["audio_spectrum_mode"] = (
        str(data.get("audio_spectrum_mode") or "single").strip().lower()
        if str(data.get("audio_spectrum_mode") or "").strip().lower() in {"single", "combined"}
        else "single"
    )
    data["local_root"] = data["local_root"] or DEFAULT_CONFIG["local_root"]
    data["remote_host"] = data["remote_host"] or DEFAULT_CONFIG["remote_host"]
    data["remote_port"] = data["remote_port"] or "22"
    data["remote_cmd"] = data["remote_cmd"] or "pt"
    data["save_dir"] = data["save_dir"] or default_save_dir()

    for key in ("remote_bootstrap", "auto_cleanup"):
        if key in raw:
            data[key] = bool(raw.get(key))

    password = raw.get("remote_password", None)
    if raw.get("clear_password"):
        data["remote_password"] = ""
    elif password is not None and str(password) != "":
        data["remote_password"] = str(password)

    normalized_host, normalized_port = normalize_remote_connection(data["remote_host"], data["remote_port"])
    data["remote_host"] = normalized_host
    data["remote_port"] = normalized_port
    return data


def save_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def public_config(data: dict[str, Any]) -> dict[str, Any]:
    visible = data.copy()
    visible["password_saved"] = bool(data.get("remote_password"))
    visible["remote_password"] = ""
    return visible


def find_bash() -> str | None:
    if os.name == "nt":
        return shutil.which("bash")
    candidates = [shutil.which("bash"), "/bin/bash", "/usr/bin/bash"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def create_askpass_script(password: str) -> Path:
    handle = tempfile.NamedTemporaryFile("w", delete=False, prefix="ptbd-web-askpass-", encoding="utf-8")
    with handle:
        handle.write("#!/usr/bin/env sh\n")
        handle.write("printf '%s\\n' " + shlex.quote(password) + "\n")
    path = Path(handle.name)
    os.chmod(path, 0o700)
    return path


def build_ssh_env(config: dict[str, Any]) -> tuple[dict[str, str], Path | None]:
    env = os.environ.copy()
    askpass_path = None
    password = str(config.get("remote_password") or "")
    if password:
        askpass_path = create_askpass_script(password)
        env["SSH_ASKPASS"] = str(askpass_path)
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env.setdefault("DISPLAY", "ptbd-web-askpass:0")
    return env, askpass_path


def cleanup_askpass(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def normalize_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for offset, item in enumerate(items, start=1):
        path = str(item.get("path") or "")
        if not path:
            continue
        item_type = str(item.get("type") or "UNKNOWN")
        normalized.append(
            {
                "index": int(item.get("index") or offset),
                "type": item_type,
                "type_label": str(item.get("type_label") or item_type),
                "path": path,
            }
        )
    return normalized


@dataclass
class WebTask:
    kind: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "queued"
    message: str = ""
    logs: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    backend: PTBDRemoteBackend | None = None
    process: subprocess.Popen[str] | None = None

    def log(self, message: str) -> None:
        line = str(message).rstrip("\r\n")
        if not line:
            return
        stamp = time.strftime("%H:%M:%S")
        with TASK_LOCK:
            self.logs.append(f"[{stamp}] {line}")
            if len(self.logs) > MAX_LOG_LINES:
                self.logs = self.logs[-MAX_LOG_LINES:]

    def start(self) -> None:
        self.status = "running"
        self.started_at = time.time()
        self.log(f"{self.kind} 任务开始")

    def finish(self, status: str, message: str) -> None:
        with TASK_LOCK:
            self.status = status
            self.message = message
            self.ended_at = time.time()
        self.log(message)

    def cancel(self) -> None:
        self.cancel_event.set()
        if self.backend is not None:
            self.backend.cancel()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()

    def to_public(self) -> dict[str, Any]:
        with TASK_LOCK:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "message": self.message,
                "logs": list(self.logs),
                "items": list(self.items),
                "outputs": list(self.outputs),
                "created_at": self.created_at,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
            }


TASKS: dict[str, WebTask] = {}
TASK_LOCK = threading.RLock()


def running_task() -> WebTask | None:
    with TASK_LOCK:
        for task in TASKS.values():
            if task.status in {"queued", "running"}:
                return task
    return None


def register_task(task: WebTask) -> None:
    with TASK_LOCK:
        TASKS[task.id] = task


def prepare_remote_runtime(config: dict[str, Any], task: WebTask) -> str:
    bash_bin = find_bash()
    if not bash_bin:
        raise RuntimeError("本机缺少 bash，无法使用 shell 回退后端。")
    helper_script = APP_ROOT / "scripts" / "prepare-remote-runtime.sh"
    if not helper_script.is_file():
        raise RuntimeError(f"找不到自举脚本：{helper_script}")
    env = os.environ.copy()
    env.update(
        {
            "PTBD_REMOTE_HOST": str(config["remote_host"]),
            "PTBD_REMOTE_PORT": str(config["remote_port"]),
            "PTBD_REMOTE_PASSWORD": str(config.get("remote_password") or ""),
        }
    )
    cmd = [bash_bin, str(helper_script), "--host", str(config["remote_host"]), "--port", str(config["remote_port"])]
    password = str(config.get("remote_password") or "")
    if password:
        cmd.extend(["--password", password])
    task.log("开始准备远端运行包")
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
    for line in result.stderr.strip().splitlines():
        task.log(line)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"bootstrap rc={result.returncode}")
    remote_cmd = result.stdout.strip()
    if not remote_cmd:
        raise RuntimeError("远端自举成功了，但没有返回远端 bdtool 路径。")
    task.log(f"远端运行包就绪：{remote_cmd}")
    return remote_cmd


def build_scan_command(config: dict[str, Any], remote_cmd: str) -> list[str]:
    ssh_bin = shutil.which("ssh")
    if not ssh_bin:
        raise RuntimeError("本机缺少 ssh。")
    include_roots = scan_include_env_value(config)
    remote_script = " ".join(
        part
        for part in (
            f"export BDTOOL_SCAN_FULL_ROOT={shlex.quote(effective_scan_root(config))};",
            f"export BDTOOL_SCAN_INCLUDE_ROOTS={shlex.quote(include_roots)};"
            if include_roots
            else "",
            f"export BDTOOL_SCAN_EXCLUDE_ROOTS={shlex.quote(str(config['scan_exclude']))};"
            if config.get("scan_exclude")
            else "",
            f"exec {shlex.quote(remote_cmd)} scan-json --full --lang zh",
        )
        if part
    )
    return [
        ssh_bin,
        "-p",
        str(config["remote_port"]),
        "-o",
        "StrictHostKeyChecking=accept-new",
        str(config["remote_host"]),
        f"bash -lc {shlex.quote(remote_script)}",
    ]


def shell_scan_items(config: dict[str, Any], task: WebTask) -> list[dict[str, Any]]:
    remote_cmd = str(config.get("remote_cmd") or "bdtool")
    if config.get("remote_bootstrap"):
        task.log("空白 VPS 自举已开启，会优先尝试系统依赖自动安装")
        remote_cmd = prepare_remote_runtime(config, task)
    cmd = build_scan_command(config, remote_cmd)
    env, askpass_path = build_ssh_env(config)
    try:
        task.log("通过 ssh 执行 scan-json")
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
    finally:
        cleanup_askpass(askpass_path)
    if result.stderr.strip():
        task.log("scan-json stderr:")
        for line in result.stderr.strip().splitlines():
            task.log(line)
    if result.returncode != 0:
        if result.stdout.strip():
            task.log("scan-json stdout:")
            for line in result.stdout.strip().splitlines()[:40]:
                task.log(line)
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"ssh rc={result.returncode}")
    if not result.stdout.strip():
        raise RuntimeError("scan-json 没有返回任何内容，通常是远端命令没有真正执行。")
    payload = json.loads(result.stdout)
    return normalize_items(payload.get("items", []))


def local_runtime_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("HOME", str(Path.home()))
    local_root = effective_scan_root(config)
    include_roots = scan_include_env_value(config)
    env["BDTOOL_SCAN_FULL_ROOT"] = local_root
    if include_roots:
        env["BDTOOL_SCAN_INCLUDE_ROOTS"] = include_roots
    else:
        env.pop("BDTOOL_SCAN_INCLUDE_ROOTS", None)
    env["BDTOOL_SCAN_EXCLUDE_ROOTS"] = str(config.get("scan_exclude") or "")
    env["BDTOOL_DOWNLOAD_DIR"] = str(config.get("save_dir") or default_save_dir())
    env["BDTOOL_AUTO_CLEANUP"] = "1" if config.get("auto_cleanup", True) else "0"
    env["BDTOOL_AUDIO_SPECTRUM_MODE"] = str(config.get("audio_spectrum_mode") or "single")
    env["BDTOOL_POST_ACTION"] = "0"
    env["LANG_CODE"] = "zh"
    return env


def split_path_roots(raw: str) -> list[str]:
    return [item for item in raw.replace(",", " ").split() if item]


def effective_scan_root(config: dict[str, Any]) -> str:
    return str(config.get("local_root") or DEFAULT_CONFIG["local_root"] or "/").strip() or "/"


def extra_scan_roots(config: dict[str, Any]) -> list[str]:
    root = effective_scan_root(config)
    seen = {root}
    extras: list[str] = []
    for item in split_path_roots(str(config.get("scan_include") or "")):
        if item and item not in seen:
            seen.add(item)
            extras.append(item)
    return extras


def scan_include_env_value(config: dict[str, Any]) -> str:
    extras = extra_scan_roots(config)
    if not extras:
        # Keep the main scan root out of BDTOOL_SCAN_INCLUDE_ROOTS so paths with spaces still work.
        return ""
    return " ".join([effective_scan_root(config), *extras])


def local_allowed_roots(config: dict[str, Any]) -> list[Path]:
    roots = [effective_scan_root(config), *extra_scan_roots(config)]
    resolved: list[Path] = []
    for root in roots:
        try:
            resolved.append(Path(root).expanduser().resolve())
        except OSError:
            continue
    return resolved or [Path("/")]


def ensure_local_path_allowed(config: dict[str, Any], selected_path: str) -> None:
    target = Path(selected_path).expanduser().resolve()
    for root in local_allowed_roots(config):
        if target == root or root in target.parents:
            return
    raise ValueError(f"路径不在允许扫描范围内：{selected_path}")


def local_scan_items(config: dict[str, Any], task: WebTask) -> list[dict[str, Any]]:
    bash_bin = find_bash()
    if not bash_bin:
        raise RuntimeError("本机缺少 bash，无法执行本地扫描。")
    bdtool = APP_ROOT / "bdtool"
    if not bdtool.is_file():
        raise RuntimeError(f"找不到 bdtool：{bdtool}")
    env = local_runtime_env(config)
    if env.get("BDTOOL_SCAN_INCLUDE_ROOTS"):
        task.log(f"本地扫描根目录：{env['BDTOOL_SCAN_FULL_ROOT']}；额外目录：{env['BDTOOL_SCAN_INCLUDE_ROOTS']}")
    else:
        task.log(f"本地扫描根目录：{env['BDTOOL_SCAN_FULL_ROOT']}")
    result = subprocess.run(
        [bash_bin, str(bdtool), "scan-json", "--full", "--lang", "zh"],
        cwd=str(APP_ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=1800,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if result.stderr.strip():
        task.log("scan-json stderr:")
        for line in result.stderr.strip().splitlines():
            task.log(line)
    if result.returncode != 0:
        if result.stdout.strip():
            task.log("scan-json stdout:")
            for line in result.stdout.strip().splitlines()[:40]:
                task.log(line)
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"local scan rc={result.returncode}")
    if not result.stdout.strip():
        raise RuntimeError("本地 scan-json 没有返回任何内容。")
    payload = json.loads(result.stdout)
    return normalize_items(payload.get("items", []))


def run_scan_task(task: WebTask, config: dict[str, Any]) -> None:
    task.start()
    backend: PTBDRemoteBackend | None = None
    try:
        if config.get("mode") == "local":
            task.log("启动方式：本机模式，直接扫描当前服务器文件系统")
            items = local_scan_items(config, task)
        elif backend_available():
            task.log(f"启动方式：内置 Python 后端，{backend_status()}")
            backend = PTBDRemoteBackend(APP_ROOT, config, logger=task.log)
            task.backend = backend
            items = normalize_items(backend.scan_items())
        else:
            task.log(f"启动方式：shell 回退后端，{backend_status()}")
            items = shell_scan_items(config, task)
        with TASK_LOCK:
            task.items = items
        task.finish("success", f"扫描完成，共发现 {len(items)} 个候选")
    except TaskCancelledError:
        task.finish("cancelled", "扫描已取消")
    except Exception as exc:
        task.finish("error", f"扫描失败：{exc}")
    finally:
        if backend is not None:
            backend.close()
        task.backend = None


def run_process_stream(cmd: list[str], env: dict[str, str], task: WebTask) -> int:
    process = subprocess.Popen(
        cmd,
        cwd=str(APP_ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        bufsize=1,
    )
    task.process = process
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if task.cancel_event.is_set():
                process.terminate()
                break
            task.log(line.rstrip("\r\n"))
        return process.wait()
    finally:
        task.process = None


def shell_process_paths(config: dict[str, Any], paths: list[str], task: WebTask) -> None:
    bash_bin = find_bash()
    if not bash_bin:
        raise RuntimeError("本机缺少 bash，无法使用 shell 回退后端。")
    remote_script = APP_ROOT / "ptbd-remote.sh"
    if not remote_script.is_file():
        raise RuntimeError(f"找不到远端流程脚本：{remote_script}")

    for index, selected_path in enumerate(paths, start=1):
        if task.cancel_event.is_set():
            raise TaskCancelledError("任务已取消。")
        task.log(f"开始处理 {index}/{len(paths)}：{selected_path}")
        env = os.environ.copy()
        env.update(
            {
                "PTBD_REMOTE_HOST": str(config["remote_host"]),
                "PTBD_REMOTE_PORT": str(config["remote_port"]),
                "PTBD_REMOTE_PASSWORD": str(config.get("remote_password") or ""),
                "PTBD_REMOTE_PT_CMD": str(config.get("remote_cmd") or "pt"),
                "PTBD_REMOTE_BOOTSTRAP": "1" if config.get("remote_bootstrap") else "0",
                "PTBD_LOCAL_SAVE_DIR": str(config["save_dir"]),
                "PTBD_SCAN_INCLUDE_ROOTS": scan_include_env_value(config),
                "PTBD_SCAN_EXCLUDE_ROOTS": str(config.get("scan_exclude") or ""),
                "PTBD_AUTO_CLEANUP": "1" if config.get("auto_cleanup", True) else "0",
                "PTBD_AUDIO_SPECTRUM_MODE": str(config.get("audio_spectrum_mode") or "single"),
                "PTBD_REMOTE_TARGET_PATH": selected_path,
            }
        )
        cmd = [
            bash_bin,
            str(remote_script),
            "--host",
            str(config["remote_host"]),
            "--port",
            str(config["remote_port"]),
            "--path",
            selected_path,
            "--save-dir",
            str(config["save_dir"]),
            "--bootstrap",
            "1" if config.get("remote_bootstrap") else "0",
            "--audio-spectrum",
            str(config.get("audio_spectrum_mode") or "single"),
        ]
        password = str(config.get("remote_password") or "")
        if password:
            cmd.extend(["--password", password])
        rc = run_process_stream(cmd, env, task)
        if rc != 0:
            if task.cancel_event.is_set():
                raise TaskCancelledError("任务已取消。")
            raise RuntimeError(f"远端处理失败，退出码：{rc}")
        task.outputs.append(str(config["save_dir"]))
        task.log(f"完成处理 {index}/{len(paths)}")


def local_process_paths(config: dict[str, Any], paths: list[str], task: WebTask) -> None:
    bash_bin = find_bash()
    if not bash_bin:
        raise RuntimeError("本机缺少 bash，无法执行本地生成。")
    bdtool = APP_ROOT / "bdtool"
    if not bdtool.is_file():
        raise RuntimeError(f"找不到 bdtool：{bdtool}")

    save_dir = Path(str(config.get("save_dir") or default_save_dir())).expanduser()
    save_dir.mkdir(parents=True, exist_ok=True)
    env = local_runtime_env(config)

    for index, selected_path in enumerate(paths, start=1):
        if task.cancel_event.is_set():
            raise TaskCancelledError("任务已取消。")
        ensure_local_path_allowed(config, selected_path)
        task.log(f"开始本地处理 {index}/{len(paths)}：{selected_path}")
        cmd = [
            bash_bin,
            str(bdtool),
            "generate-path",
            "--path",
            selected_path,
            "--lang",
            "zh",
            "--audio-spectrum",
            str(config.get("audio_spectrum_mode") or "single"),
        ]
        rc = run_process_stream(cmd, env, task)
        if rc != 0:
            if task.cancel_event.is_set():
                raise TaskCancelledError("任务已取消。")
            raise RuntimeError(f"本地处理失败，退出码：{rc}")
        task.outputs.append(str(save_dir))
        task.log(f"完成本地处理 {index}/{len(paths)}，输出目录：{save_dir}")


def run_process_task(task: WebTask, config: dict[str, Any], paths: list[str]) -> None:
    task.start()
    backend: PTBDRemoteBackend | None = None
    try:
        if not paths:
            raise ValueError("请至少选择一个候选路径。")
        save_dir = Path(str(config["save_dir"])).expanduser()
        if config.get("mode") == "local":
            task.log("启动方式：本机模式，直接在当前服务器生成素材")
            local_process_paths(config, paths, task)
        elif backend_available():
            task.log(f"启动方式：内置 Python 后端，{backend_status()}")
            backend = PTBDRemoteBackend(APP_ROOT, config, logger=task.log)
            task.backend = backend
            for index, selected_path in enumerate(paths, start=1):
                if task.cancel_event.is_set():
                    raise TaskCancelledError("任务已取消。")
                task.log(f"开始处理 {index}/{len(paths)}：{selected_path}")
                local_path = backend.process_selected_path(selected_path, save_dir)
                task.outputs.append(str(local_path))
                task.log(f"完成处理 {index}/{len(paths)}：{local_path}")
        else:
            task.log(f"启动方式：shell 回退后端，{backend_status()}")
            shell_process_paths(config, paths, task)
        task.finish("success", f"处理完成，共处理 {len(paths)} 个条目")
    except TaskCancelledError:
        task.finish("cancelled", "任务已取消")
    except Exception as exc:
        task.finish("error", f"处理失败：{exc}")
    finally:
        if backend is not None:
            backend.close()
        task.backend = None


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PT-BDtool Web</title>
  <style>
    :root {
      color-scheme: light;
      --bg: oklch(0.985 0.004 230);
      --surface: oklch(1 0 0);
      --surface-strong: oklch(0.955 0.008 230);
      --surface-soft: oklch(0.973 0.006 230);
      --ink: oklch(0.19 0.018 235);
      --muted: oklch(0.45 0.023 235);
      --faint: oklch(0.62 0.018 235);
      --primary: oklch(0.44 0.105 246);
      --primary-strong: oklch(0.34 0.11 246);
      --accent: oklch(0.56 0.12 154);
      --danger: oklch(0.52 0.15 28);
      --warning: oklch(0.68 0.13 76);
      --success: oklch(0.50 0.11 150);
      --line: oklch(0.88 0.012 235);
      --line-strong: oklch(0.78 0.018 235);
      --shadow: 0 1px 3px oklch(0.19 0.018 235 / 0.11);
      --radius: 12px;
      --font: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      font-family: var(--font);
      font-size: 15px;
      line-height: 1.5;
    }

    button,
    input,
    textarea,
    select {
      font: inherit;
    }

    .shell {
      width: min(1440px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 24px 0 42px;
    }

    .topbar {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 18px;
      align-items: start;
      margin-bottom: 16px;
    }

    h1 {
      margin: 0 0 6px;
      font-size: 1.68rem;
      line-height: 1.12;
      letter-spacing: -0.025em;
      text-wrap: balance;
    }

    .lede {
      max-width: 72ch;
      margin: 0;
      color: var(--muted);
    }

    .status-strip {
      min-width: 300px;
      padding: 11px 13px;
      background: var(--surface);
      color: var(--ink);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }

    .status-strip strong {
      display: block;
      font-size: 0.86rem;
    }

    .status-strip span {
      display: block;
      margin-top: 2px;
      color: var(--muted);
      font-size: 0.82rem;
    }

    .flow {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }

    .step {
      display: grid;
      gap: 3px;
      padding: 10px 12px;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
    }

    .step strong {
      font-size: 0.86rem;
    }

    .step span {
      color: var(--muted);
      font-size: 0.78rem;
    }

    .workbench {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 16px;
      align-items: start;
    }

    .side-stack,
    .main-stack {
      display: grid;
      gap: 16px;
    }

    .side-stack {
      position: sticky;
      top: 16px;
    }

    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .panel-head {
      padding: 15px 16px 12px;
      border-bottom: 1px solid var(--line);
    }

    .panel-head h2 {
      margin: 0;
      font-size: 1.02rem;
      letter-spacing: -0.01em;
    }

    .panel-head p {
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 0.88rem;
    }

    .section-kicker {
      display: block;
      margin-bottom: 4px;
      color: var(--primary-strong);
      font-size: 0.76rem;
      font-weight: 800;
    }

    .panel-body {
      padding: 15px 16px 16px;
    }

    .form-grid {
      display: grid;
      gap: 12px;
    }

    .quick-config {
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr);
      gap: 10px;
      align-items: end;
    }

    .advanced-config {
      border-top: 1px solid var(--line);
      padding-top: 10px;
    }

    .advanced-config summary {
      cursor: pointer;
      color: var(--primary-strong);
      font-weight: 750;
      list-style-position: inside;
    }

    .advanced-grid {
      display: grid;
      gap: 12px;
      margin-top: 12px;
    }

    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 0.84rem;
      font-weight: 650;
    }

    input[type="text"],
    input[type="password"],
    input[type="number"],
    textarea,
    select {
      width: 100%;
      min-height: 40px;
      padding: 9px 11px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--bg);
      color: var(--ink);
      outline: none;
      transition: border-color 160ms ease, box-shadow 160ms ease;
    }

    textarea {
      resize: vertical;
      min-height: 68px;
    }

    input:focus,
    textarea:focus,
    select:focus {
      border-color: var(--primary);
      box-shadow: 0 0 0 3px oklch(0.45 0.074 200 / 0.16);
    }

    .row {
      display: grid;
      grid-template-columns: 1fr 92px;
      gap: 10px;
    }

    .checks {
      display: grid;
      gap: 8px;
      padding-top: 2px;
    }

    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--ink);
      font-weight: 500;
    }

    .check input {
      width: 16px;
      height: 16px;
    }

    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 14px;
    }

    .button {
      min-height: 40px;
      border: 1px solid transparent;
      border-radius: 10px;
      padding: 0 16px;
      color: var(--ink);
      background: var(--surface-strong);
      cursor: pointer;
      font-weight: 700;
      transition: transform 140ms ease, background 140ms ease, opacity 140ms ease;
    }

    .button:hover {
      background: oklch(0.93 0.012 235);
    }

    .button:disabled {
      cursor: not-allowed;
      opacity: 0.55;
      transform: none;
    }

    .button.primary {
      background: var(--primary);
      color: white;
    }

    .button.danger {
      background: var(--danger);
      color: white;
    }

    .button.secondary {
      background: var(--surface);
      border-color: var(--line-strong);
      color: var(--ink);
    }

    .toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    .filters {
      display: grid;
      grid-template-columns: 150px minmax(210px, 1fr) minmax(220px, 1fr);
      gap: 10px;
      align-items: center;
    }

    .filters input,
    .filters select {
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--bg);
      color: var(--ink);
      padding: 0 12px;
      outline: none;
    }

    .candidate-list {
      display: grid;
      gap: 6px;
      max-height: 560px;
      overflow: auto;
      padding-right: 4px;
    }

    .list-tools {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      margin-bottom: 12px;
    }

    .bulk-actions,
    .pager {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }

    .pager {
      justify-content: flex-end;
      color: var(--muted);
      font-size: 0.84rem;
    }

    .pager select {
      min-height: 34px;
      width: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--bg);
      color: var(--ink);
      padding: 0 8px;
    }

    .inline-check {
      display: inline-flex;
      grid-template-columns: none;
      align-items: center;
      gap: 7px;
      color: var(--ink);
      font-size: 0.86rem;
      font-weight: 600;
    }

    .inline-check input {
      width: 16px;
      height: 16px;
    }

    .candidate {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 12px;
      align-items: start;
      padding: 11px 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--bg);
    }

    .candidate:has(input:checked) {
      border-color: var(--primary);
      background: oklch(0.96 0.018 246);
    }

    .candidate input {
      width: 16px;
      height: 16px;
    }

    .type {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 26px;
      border-radius: 8px;
      background: oklch(0.92 0.035 246);
      color: var(--primary-strong);
      font-size: 0.78rem;
      font-weight: 800;
      padding: 0 8px;
    }

    .candidate-main {
      min-width: 0;
      display: grid;
      gap: 2px;
    }

    .candidate-title {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 760;
    }

    .candidate-title.dir {
      font-family: var(--mono);
      font-size: 0.88rem;
    }

    .path {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-family: var(--mono);
      font-size: 0.84rem;
      color: var(--muted);
    }

    .candidate-materials {
      color: var(--faint);
      font-size: 0.82rem;
    }

    .empty {
      padding: 34px 18px;
      color: var(--muted);
      text-align: center;
      background: var(--surface);
      border-radius: 12px;
    }

    .material-options {
      display: grid;
      gap: 8px;
    }

    .material-card {
      width: 100%;
      display: grid;
      gap: 3px;
      text-align: left;
      padding: 11px 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--bg);
      color: var(--ink);
      cursor: pointer;
    }

    .material-card strong {
      font-size: 0.92rem;
    }

    .material-card span {
      color: var(--muted);
      font-size: 0.82rem;
    }

    .material-card.active {
      border-color: var(--primary);
      background: oklch(0.96 0.018 246);
    }

    .run-summary {
      display: grid;
      gap: 8px;
      padding: 12px;
      border-radius: 10px;
      background: var(--surface-soft);
      color: var(--muted);
      font-size: 0.88rem;
    }

    .run-summary strong {
      color: var(--ink);
    }

    .logbox {
      min-height: 240px;
      max-height: 420px;
      overflow: auto;
      margin: 0;
      padding: 14px;
      border-radius: 12px;
      background: oklch(0.15 0.016 235);
      color: oklch(0.9 0.01 210);
      font-family: var(--mono);
      font-size: 0.82rem;
      white-space: pre-wrap;
    }

    .outputs {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }

    .output {
      padding: 9px 11px;
      border-radius: 10px;
      background: oklch(0.92 0.03 154);
      color: oklch(0.26 0.075 154);
      font-family: var(--mono);
      font-size: 0.82rem;
      overflow-wrap: anywhere;
    }

    @media (max-width: 900px) {
      .shell {
        width: min(100vw - 20px, 720px);
        padding-top: 18px;
      }

      .topbar,
      .workbench,
      .flow,
      .quick-config,
      .filters,
      .list-tools {
        display: grid;
        grid-template-columns: 1fr;
      }

      .pager {
        justify-content: flex-start;
      }

      .status-strip {
        min-width: 0;
      }

      .candidate {
        grid-template-columns: auto minmax(0, 1fr);
      }

      .candidate .type {
        grid-column: 2;
        justify-self: start;
      }
    }

    @media (prefers-reduced-motion: reduce) {
      * {
        transition-duration: 1ms !important;
        scroll-behavior: auto !important;
      }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="topbar">
      <div>
        <h1>PT-BDtool 发种材料工作台</h1>
        <p class="lede">按资源目录生成 PT 发布需要的 MediaInfo、BDInfo、截图和音乐频谱图。主流程只处理资源，连接和扫描细节放在高级配置。</p>
      </div>
      <div class="status-strip">
        <strong id="runtimeMode">读取运行状态中</strong>
        <span id="runtimeDetail">等待本地服务响应</span>
      </div>
    </section>

    <section class="flow" aria-label="发种材料生成流程">
      <div class="step"><strong>1. 连接位置</strong><span>本机或 VPS，只在高级配置里改一次</span></div>
      <div class="step"><strong>2. 扫描资源</strong><span>列出视频、音乐目录、原盘和 ISO</span></div>
      <div class="step"><strong>3. 选择方案</strong><span>按资源类型决定要生成哪些材料</span></div>
      <div class="step"><strong>4. 生成下载</strong><span>日志和结果包固定显示</span></div>
    </section>

    <form id="configForm" class="main-stack">
      <section class="panel">
        <div class="panel-head">
          <div class="toolbar">
            <div>
              <span class="section-kicker">连接与路径</span>
              <h2>工作区</h2>
              <p>先确认处理位置，再扫描资源。常用路径直接显示，SSH 和排除目录放到高级配置。</p>
            </div>
            <div class="actions" style="margin-top: 0">
              <button class="button secondary" type="submit">保存设置</button>
              <button class="button primary" type="button" id="scanBtn">扫描资源</button>
            </div>
          </div>
        </div>
        <div class="panel-body">
          <div class="quick-config">
            <label>处理位置
              <select name="mode">
                <option value="local">本机服务器</option>
                <option value="remote">远端 VPS</option>
              </select>
            </label>
            <label>资源根目录
              <input name="local_root" type="text" placeholder="/data/downloads">
            </label>
          </div>
          <input name="audio_spectrum_mode" type="hidden" value="single">
          <details class="advanced-config">
            <summary>高级配置</summary>
            <div class="advanced-grid">
              <label>VPS 地址
                <input name="remote_host" type="text" autocomplete="off" placeholder="root@1.2.3.4">
              </label>
              <div class="row">
                <label>SSH 端口
                  <input name="remote_port" type="number" min="1" max="65535">
                </label>
                <label>远端命令
                  <input name="remote_cmd" type="text" placeholder="pt">
                </label>
              </div>
              <label>SSH 密码
                <input name="remote_password" type="password" autocomplete="new-password" placeholder="留空保留已保存密码">
              </label>
              <label>结果保存目录
                <input name="save_dir" type="text">
              </label>
              <label>额外扫描目录
                <textarea name="scan_include" placeholder="可留空。需要同时扫描多个根目录时再填，例如 /data,/mnt/media"></textarea>
              </label>
              <label>额外排除目录
                <textarea name="scan_exclude" placeholder="/mnt/cache /data/tmp，可留空"></textarea>
              </label>
              <div class="checks">
                <label class="check"><input name="remote_bootstrap" type="checkbox"> 空白 VPS 自动自举</label>
                <label class="check"><input name="auto_cleanup" type="checkbox"> 成功后清理远端临时结果</label>
                <label class="check"><input name="clear_password" type="checkbox"> 清空已保存密码</label>
              </div>
            </div>
          </details>
        </div>
      </section>

      <section class="workbench">
        <div class="main-stack">
          <div class="panel">
            <div class="panel-head">
              <div class="toolbar">
                <div>
                  <span class="section-kicker">资源池</span>
                  <h2>选择资源</h2>
                  <p id="candidateSummary">点击“扫描资源”后，从这里选择要发种的资源。音乐目录会作为文件夹资源显示。</p>
                </div>
                <div class="filters">
                  <select id="typeFilter">
                    <option value="">全部资源（音乐按目录合并）</option>
                    <option value="VIDEO">视频</option>
                    <option value="AUDIO_DIR">音乐目录</option>
                    <option value="AUDIO">单曲音频（高级）</option>
                    <option value="BDMV">原盘</option>
                    <option value="ISO">ISO</option>
                  </select>
                  <select id="directoryFilter">
                    <option value="">全部目录</option>
                  </select>
                  <input id="keywordFilter" type="text" placeholder="按路径或名称过滤">
                </div>
              </div>
            </div>
            <div class="panel-body">
              <div class="list-tools">
                <div class="bulk-actions">
                  <button class="button secondary" type="button" id="selectPageBtn">选择本页</button>
                  <button class="button secondary" type="button" id="selectFilteredBtn">选择全部匹配</button>
                  <button class="button secondary" type="button" id="clearSelectionBtn">清空选择</button>
                  <label class="inline-check"><input id="selectedOnlyToggle" type="checkbox"> 只看已选</label>
                </div>
                <div class="pager" aria-label="候选分页">
                  <button class="button secondary" type="button" id="prevPageBtn">上一页</button>
                  <span id="pageInfo">第 1 / 1 页</span>
                  <button class="button secondary" type="button" id="nextPageBtn">下一页</button>
                  <label class="inline-check">每页
                    <select id="pageSizeSelect">
                      <option value="50">50</option>
                      <option value="100">100</option>
                      <option value="200">200</option>
                    </select>
                  </label>
                </div>
              </div>
              <div id="candidateList" class="candidate-list">
                <div class="empty">还没有资源。先确认工作区，然后点击“扫描资源”。</div>
              </div>
            </div>
          </div>
        </div>

        <aside class="side-stack">
          <div class="panel">
            <div class="panel-head">
              <span class="section-kicker">材料方案</span>
              <h2>发布材料方案</h2>
              <p>默认用自动推荐。音乐目录可以一键切换整包总频谱或单曲频谱。</p>
            </div>
            <div class="panel-body">
              <div class="material-options" role="group" aria-label="发布材料方案">
                <button class="material-card active" type="button" data-plan="auto" data-spectrum="auto">
                  <strong>自动推荐</strong>
                  <span>视频生成 MediaInfo 和截图；原盘生成 BDInfo；音乐目录默认整包总频谱。</span>
                </button>
                <button class="material-card" type="button" data-plan="music-combined" data-spectrum="combined">
                  <strong>音乐整包总频谱</strong>
                  <span>适合专辑或合集，输出一个总频谱图和多段 MediaInfo。</span>
                </button>
                <button class="material-card" type="button" data-plan="music-single" data-spectrum="single">
                  <strong>音乐单曲频谱</strong>
                  <span>每首歌单独生成频谱图，保留旧逻辑。</span>
                </button>
              </div>
            </div>
          </div>

          <div class="panel">
            <div class="panel-head">
              <span class="section-kicker">执行</span>
              <h2>生成确认</h2>
              <p>开始前确认将处理什么资源，以及会生成什么材料。</p>
            </div>
            <div class="panel-body">
              <div id="runSummary" class="run-summary">
                <strong>还没有选择资源。</strong>
                <span>先扫描并选择一个资源，系统会给出材料预览。</span>
              </div>
              <div class="actions">
                <button class="button primary" type="button" id="processBtn">生成发布材料</button>
                <button class="button danger" type="button" id="cancelBtn">停止任务</button>
              </div>
            </div>
          </div>

          <div class="panel">
            <div class="panel-head">
              <div class="toolbar">
                <div>
                  <span class="section-kicker">运行状态</span>
                  <h2>任务日志</h2>
                  <p id="taskState">当前没有任务。</p>
                </div>
                <div class="actions" style="margin-top: 0">
                  <button class="button secondary" type="button" id="copyLogBtn">复制日志</button>
                  <button class="button secondary" type="button" id="clearLogBtn">清空日志</button>
                </div>
              </div>
            </div>
            <div class="panel-body">
              <pre id="logBox" class="logbox">等待任务。</pre>
              <div id="outputs" class="outputs"></div>
            </div>
          </div>
        </aside>
      </section>
    </form>
  </main>

  <script>
    const form = document.querySelector("#configForm");
    const logBox = document.querySelector("#logBox");
    const candidateList = document.querySelector("#candidateList");
    const candidateSummary = document.querySelector("#candidateSummary");
    const taskState = document.querySelector("#taskState");
    const runtimeMode = document.querySelector("#runtimeMode");
    const runtimeDetail = document.querySelector("#runtimeDetail");
    const outputsEl = document.querySelector("#outputs");
    const runSummary = document.querySelector("#runSummary");
    const materialCards = Array.from(document.querySelectorAll(".material-card"));
    const pageInfo = document.querySelector("#pageInfo");
    const pageSizeSelect = document.querySelector("#pageSizeSelect");
    const directoryFilter = document.querySelector("#directoryFilter");
    const selectedOnlyToggle = document.querySelector("#selectedOnlyToggle");
    const prevPageBtn = document.querySelector("#prevPageBtn");
    const nextPageBtn = document.querySelector("#nextPageBtn");

    let candidates = [];
    let rawCandidateCount = 0;
    let selectedPaths = new Set();
    let selectedMaterialPlan = "auto";
    let loadedConfig = {};
    let currentPage = 1;
    let pageSize = Number(pageSizeSelect.value || 50);
    let showSelectedOnly = false;
    let activeTaskId = null;
    let pollTimer = null;
    const PTBD_BASE_PATH = __PTBD_BASE_PATH_JSON__;

    function apiUrl(path) {
      const suffix = path.startsWith("/") ? path : "/" + path;
      return PTBD_BASE_PATH ? PTBD_BASE_PATH + suffix : suffix;
    }

    function formData() {
      const data = Object.fromEntries(new FormData(form).entries());
      if (
        loadedConfig.local_root !== undefined &&
        data.local_root !== loadedConfig.local_root &&
        data.scan_include === (loadedConfig.scan_include || "")
      ) {
        data.scan_include = "";
        form.scan_include.value = "";
      }
      data.remote_bootstrap = form.remote_bootstrap.checked;
      data.auto_cleanup = form.auto_cleanup.checked;
      data.clear_password = form.clear_password.checked;
      return data;
    }

    async function api(path, options = {}) {
      const response = await fetch(apiUrl(path), {
        headers: {"Content-Type": "application/json"},
        ...options,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }
      return payload;
    }

    function isLocalMode() {
      return form.mode.value === "local";
    }

    function basename(path) {
      return String(path || "").split(/[\\/]/).filter(Boolean).pop() || String(path || "");
    }

    function compactPath(path, keep = 3) {
      const text = String(path || "");
      const parts = text.split(/[\\/]/).filter(Boolean);
      if (parts.length <= keep) return text || "/";
      return "…/" + parts.slice(-keep).join("/");
    }

    function dirname(path) {
      const parts = String(path || "").split(/[\\/]/).filter(Boolean);
      if (parts.length <= 1) return "";
      const prefix = String(path || "").startsWith("/") ? "/" : "";
      return prefix + parts.slice(0, -1).join("/");
    }

    function normalizePath(path) {
      return String(path || "").replace(/\/+$/, "");
    }

    function resourceDirectory(item) {
      if (item.type === "AUDIO_DIR" || item.type === "BDMV") return item.path;
      return dirname(item.path);
    }

    function prepareCandidates(items) {
      rawCandidateCount = items.length;
      const audioDirPaths = new Set(
        items
          .filter((item) => item.type === "AUDIO_DIR")
          .map((item) => normalizePath(item.path))
      );
      return items.map((item) => {
        const normalized = {...item};
        normalized.path = String(normalized.path || "");
        normalized.parent_audio_dir = dirname(normalized.path);
        normalized.resource_dir = resourceDirectory(normalized);
        normalized.collapsed_into_album =
          normalized.type === "AUDIO" && audioDirPaths.has(normalizePath(normalized.parent_audio_dir));
        if (normalized.collapsed_into_album) {
          normalized.resource_dir = normalized.parent_audio_dir;
        }
        return normalized;
      });
    }

    function primaryResources() {
      return candidates.filter((item) => !item.collapsed_into_album);
    }

    function rebuildDirectoryFilter() {
      const current = directoryFilter.value;
      const counts = new Map();
      for (const item of primaryResources()) {
        const dir = item.resource_dir || dirname(item.path);
        if (!dir) continue;
        counts.set(dir, (counts.get(dir) || 0) + 1);
      }
      const dirs = Array.from(counts.keys()).sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));
      directoryFilter.innerHTML = "";
      const all = document.createElement("option");
      all.value = "";
      all.textContent = `全部目录（${dirs.length}）`;
      directoryFilter.appendChild(all);
      for (const dir of dirs) {
        const option = document.createElement("option");
        option.value = dir;
        option.textContent = `${compactPath(dir)} (${counts.get(dir)})`;
        directoryFilter.appendChild(option);
      }
      directoryFilter.value = dirs.includes(current) ? current : "";
    }

    function resetResourceView() {
      currentPage = 1;
      rebuildDirectoryFilter();
    }

    function selectedItems() {
      return candidates.filter((item) => selectedPaths.has(item.path));
    }

    function typeName(type) {
      return {
        VIDEO: "视频",
        AUDIO: "单曲音频",
        AUDIO_DIR: "音乐目录",
        BDMV: "原盘",
        ISO: "ISO",
      }[type] || type || "未知";
    }

    function materialText(item, spectrumMode = "single") {
      if (item.type === "VIDEO") return "MediaInfo + 6 张截图";
      if (item.type === "BDMV" || item.type === "ISO") return "BDInfo + 6 张截图";
      if (item.type === "AUDIO_DIR") {
        return spectrumMode === "combined" ? "整包 MediaInfo + 1 张总频谱图" : "每首歌 MediaInfo + 单曲频谱图";
      }
      if (item.type === "AUDIO") return "MediaInfo + 单曲频谱图";
      return "按资源类型自动生成";
    }

    function displayTitle(item) {
      return item.type === "AUDIO_DIR" ? item.path : basename(item.path);
    }

    function displayPath(item) {
      if (item.type === "AUDIO_DIR") return "文件夹路径，生成时直接传入该目录";
      if (item.collapsed_into_album) return `已归入音乐目录：${item.parent_audio_dir}`;
      return item.resource_dir ? `所在目录：${item.resource_dir}` : item.path;
    }

    function recommendedSpectrumMode(items = selectedItems()) {
      if (selectedMaterialPlan === "music-combined") return "combined";
      if (selectedMaterialPlan === "music-single") return "single";
      return items.some((item) => item.type === "AUDIO_DIR") ? "combined" : "single";
    }

    function syncPlanCards() {
      for (const card of materialCards) {
        card.classList.toggle("active", card.dataset.plan === selectedMaterialPlan);
      }
    }

    function updateRunSummary() {
      const items = selectedItems();
      const spectrumMode = recommendedSpectrumMode(items);
      form.audio_spectrum_mode.value = spectrumMode;
      if (!items.length) {
        runSummary.innerHTML = "<strong>还没有选择资源。</strong><span>先扫描并选择一个资源，系统会给出材料预览。</span>";
        return;
      }
      const typeCounts = items.reduce((acc, item) => {
        acc[item.type] = (acc[item.type] || 0) + 1;
        return acc;
      }, {});
      const types = Object.entries(typeCounts)
        .map(([type, count]) => `${typeName(type)} ${count} 个`)
        .join("，");
      const materialLines = Array.from(new Set(items.map((item) => materialText(item, spectrumMode))));
      runSummary.innerHTML = [
        `<strong>已选择 ${items.length} 个资源。</strong>`,
        `<span>${types}</span>`,
        `<span>将生成：${materialLines.join("；")}</span>`,
        `<span>音乐频谱模式：${spectrumMode === "combined" ? "整包总频谱" : "单曲频谱"}</span>`,
      ].join("");
    }

    function updateModeCopy() {
      const scanBtn = document.querySelector("#scanBtn");
      scanBtn.textContent = isLocalMode() ? "扫描本机资源" : "扫描 VPS 资源";
      if (!candidates.length) {
        candidateSummary.textContent = isLocalMode()
          ? "先扫描本机服务器，资源会显示在这里。"
          : "先扫描 VPS，资源会显示在这里。";
      }
    }

    function appendFrontendLog(line) {
      const previous = logBox.textContent === "等待任务。" ? "" : logBox.textContent + "\n";
      logBox.textContent = previous + line;
      logBox.scrollTop = logBox.scrollHeight;
    }

    async function loadStatus() {
      const payload = await api("/api/status");
      runtimeMode.textContent = payload.backend_available ? "Python 后端可用" : "Shell 回退后端";
      runtimeDetail.textContent = payload.backend_status + "，配置文件：" + payload.config_path;
    }

    async function loadConfig() {
      const payload = await api("/api/config");
      const config = payload.config;
      loadedConfig = {...config};
      for (const [key, value] of Object.entries(config)) {
        if (!(key in form) || key === "remote_password") continue;
        const field = form[key];
        if (field.type === "checkbox") {
          field.checked = Boolean(value);
        } else {
          field.value = value ?? "";
        }
      }
      form.remote_password.placeholder = config.password_saved ? "已保存密码，留空不修改" : "未保存密码";
      selectedMaterialPlan = config.audio_spectrum_mode === "combined" ? "music-combined" : "auto";
      syncPlanCards();
      updateRunSummary();
      updateModeCopy();
    }

    async function saveConfig() {
      const payload = await api("/api/config", {
        method: "POST",
        body: JSON.stringify(formData()),
      });
      form.remote_password.value = "";
      form.clear_password.checked = false;
      form.remote_password.placeholder = payload.config.password_saved ? "已保存密码，留空不修改" : "未保存密码";
      appendFrontendLog("[web] 配置已保存");
      loadedConfig = {...payload.config};
      return payload.config;
    }

    function filteredCandidates() {
      const type = document.querySelector("#typeFilter").value;
      const directory = directoryFilter.value;
      const keyword = document.querySelector("#keywordFilter").value.trim().toLowerCase();
      let items = candidates.filter((item) => {
        if (!type && item.collapsed_into_album && !selectedPaths.has(item.path)) return false;
        if (type && item.type !== type) return false;
        if (directory && item.resource_dir !== directory) return false;
        if (keyword && !`${item.path} ${basename(item.path)} ${item.parent_audio_dir || ""}`.toLowerCase().includes(keyword)) return false;
        return true;
      });
      if (showSelectedOnly) {
        items = items.filter((item) => selectedPaths.has(item.path));
      }
      return items;
    }

    function pageState(items) {
      const total = items.length;
      const totalPages = Math.max(1, Math.ceil(total / pageSize));
      if (currentPage > totalPages) currentPage = totalPages;
      if (currentPage < 1) currentPage = 1;
      const start = total ? (currentPage - 1) * pageSize : 0;
      const end = Math.min(start + pageSize, total);
      return {total, totalPages, start, end, pageItems: items.slice(start, end)};
    }

    function updatePager(state) {
      pageInfo.textContent = state.total
        ? `第 ${currentPage} / ${state.totalPages} 页，${state.start + 1}-${state.end} / ${state.total}`
        : "第 1 / 1 页，0 / 0";
      prevPageBtn.disabled = currentPage <= 1;
      nextPageBtn.disabled = currentPage >= state.totalPages;
    }

    function renderCandidates() {
      const filtered = filteredCandidates();
      const state = pageState(filtered);
      const visible = state.pageItems;
      candidateList.innerHTML = "";
      const collapsedCount = candidates.filter((item) => item.collapsed_into_album).length;
      const rawSuffix = collapsedCount
        ? `，已把 ${collapsedCount} 首目录内单曲归入音乐目录`
        : "";
      candidateSummary.textContent = candidates.length
        ? `扫描 ${rawCandidateCount} 个条目，主列表 ${candidates.length - collapsedCount} 个资源，匹配 ${state.total} 个，已选择 ${selectedPaths.size} 个${rawSuffix}。`
        : isLocalMode()
          ? "先扫描本机服务器，资源会显示在这里。"
          : "先扫描 VPS，资源会显示在这里。";
      updatePager(state);
      if (!visible.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = candidates.length
          ? (showSelectedOnly ? "当前没有已选资源匹配过滤条件。" : "当前过滤条件没有匹配项。")
          : "还没有资源。确认工作区后点击“扫描资源”。";
        candidateList.appendChild(empty);
        updateRunSummary();
        return;
      }
      const spectrumMode = recommendedSpectrumMode();
      for (const item of visible) {
        const row = document.createElement("label");
        row.className = "candidate";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = selectedPaths.has(item.path);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selectedPaths.add(item.path);
          else selectedPaths.delete(item.path);
          renderCandidates();
        });
        const main = document.createElement("span");
        main.className = "candidate-main";
        const title = document.createElement("span");
        title.className = item.type === "AUDIO_DIR" ? "candidate-title dir" : "candidate-title";
        title.title = item.path;
        title.textContent = displayTitle(item);
        const path = document.createElement("span");
        path.className = "path";
        path.title = item.path;
        path.textContent = displayPath(item);
        const materials = document.createElement("span");
        materials.className = "candidate-materials";
        materials.textContent = materialText(item, spectrumMode);
        main.append(title, path, materials);
        const type = document.createElement("span");
        type.className = "type";
        type.textContent = item.type_label || item.type;
        row.append(checkbox, main, type);
        candidateList.appendChild(row);
      }
      updateRunSummary();
    }

    function setTask(payload) {
      activeTaskId = payload.task.id;
      pollTask();
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(pollTask, 1200);
    }

    async function pollTask() {
      if (!activeTaskId) return;
      const payload = await api(`/api/tasks/${activeTaskId}`);
      const task = payload.task;
      taskState.textContent = `${task.kind}：${task.status}${task.message ? "，" + task.message : ""}`;
      logBox.textContent = task.logs.length ? task.logs.join("\n") : "等待任务输出。";
      logBox.scrollTop = logBox.scrollHeight;
      outputsEl.innerHTML = "";
      for (const output of task.outputs || []) {
        const item = document.createElement("div");
        item.className = "output";
        item.textContent = output;
        outputsEl.appendChild(item);
      }
      if (task.kind === "scan" && task.status === "success") {
        candidates = prepareCandidates(task.items || []);
        selectedPaths.clear();
        currentPage = 1;
        showSelectedOnly = false;
        selectedOnlyToggle.checked = false;
        rebuildDirectoryFilter();
        renderCandidates();
        updateRunSummary();
      }
      if (!["queued", "running"].includes(task.status) && pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    async function startScan() {
      await saveConfig();
      const payload = await api("/api/scan", {method: "POST", body: JSON.stringify({})});
      logBox.textContent = "扫描任务已提交。";
      setTask(payload);
    }

    async function startProcess() {
      const paths = Array.from(selectedPaths);
      if (!paths.length) {
        appendFrontendLog("[web] 请先选择至少一个资源。");
        return;
      }
      form.audio_spectrum_mode.value = recommendedSpectrumMode();
      await saveConfig();
      const payload = await api("/api/process", {
        method: "POST",
        body: JSON.stringify({paths}),
      });
      logBox.textContent = "生成任务已提交。";
      setTask(payload);
    }

    async function cancelTask() {
      if (!activeTaskId) return;
      await api(`/api/tasks/${activeTaskId}/cancel`, {method: "POST", body: "{}"});
      appendFrontendLog("[web] 已发送停止请求。");
    }

    async function copyLog() {
      const text = logBox.textContent || "";
      if (!text.trim()) {
        appendFrontendLog("[web] 当前没有可复制的日志。");
        return;
      }
      try {
        if (!navigator.clipboard || !window.isSecureContext) {
          throw new Error("clipboard unavailable");
        }
        await navigator.clipboard.writeText(text);
        appendFrontendLog("[web] 日志已复制到剪贴板。");
      } catch (_) {
        const range = document.createRange();
        range.selectNodeContents(logBox);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        appendFrontendLog("[web] 浏览器禁止直接复制，已选中日志，请按 Ctrl+C。");
      }
    }

    document.querySelector("#scanBtn").addEventListener("click", startScan);
    document.querySelector("#processBtn").addEventListener("click", startProcess);
    document.querySelector("#cancelBtn").addEventListener("click", cancelTask);
    document.querySelector("#selectPageBtn").addEventListener("click", () => {
      const state = pageState(filteredCandidates());
      for (const item of state.pageItems) selectedPaths.add(item.path);
      renderCandidates();
    });
    document.querySelector("#selectFilteredBtn").addEventListener("click", () => {
      for (const item of filteredCandidates()) selectedPaths.add(item.path);
      renderCandidates();
    });
    document.querySelector("#clearSelectionBtn").addEventListener("click", () => {
      selectedPaths.clear();
      renderCandidates();
    });
    document.querySelector("#copyLogBtn").addEventListener("click", copyLog);
    document.querySelector("#clearLogBtn").addEventListener("click", () => {
      logBox.textContent = "前端日志已清空。";
      outputsEl.innerHTML = "";
    });
    document.querySelector("#typeFilter").addEventListener("change", () => {
      resetResourceView();
      renderCandidates();
    });
    directoryFilter.addEventListener("change", () => {
      currentPage = 1;
      renderCandidates();
    });
    document.querySelector("#keywordFilter").addEventListener("input", () => {
      currentPage = 1;
      renderCandidates();
    });
    selectedOnlyToggle.addEventListener("change", () => {
      showSelectedOnly = selectedOnlyToggle.checked;
      currentPage = 1;
      renderCandidates();
    });
    pageSizeSelect.addEventListener("change", () => {
      pageSize = Number(pageSizeSelect.value || 50);
      currentPage = 1;
      renderCandidates();
    });
    prevPageBtn.addEventListener("click", () => {
      currentPage = Math.max(1, currentPage - 1);
      renderCandidates();
    });
    nextPageBtn.addEventListener("click", () => {
      currentPage += 1;
      renderCandidates();
    });
    for (const card of materialCards) {
      card.addEventListener("click", () => {
        selectedMaterialPlan = card.dataset.plan || "auto";
        syncPlanCards();
        renderCandidates();
      });
    }
    form.mode.addEventListener("change", () => {
      updateModeCopy();
      renderCandidates();
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await saveConfig();
      } catch (error) {
        appendFrontendLog("[web] 保存失败：" + error.message);
      }
    });

    Promise.all([loadStatus(), loadConfig()])
      .then(() => renderCandidates())
      .catch((error) => appendFrontendLog("[web] 初始化失败：" + error.message));
  </script>
</body>
</html>
"""


def normalize_base_path(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text or text == "/":
        return ""
    if not text.startswith("/"):
        text = "/" + text
    while len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    return text


def strip_base_path(path: str, base_path: str) -> str | None:
    if not base_path:
        return path
    if path == base_path:
        return "/"
    if path.startswith(base_path + "/"):
        stripped = path[len(base_path) :]
        return stripped or "/"
    if path == "/":
        return "/"
    return None


def render_index_html(base_path: str) -> bytes:
    html = INDEX_HTML.replace("__PTBD_BASE_PATH_JSON__", json.dumps(base_path, ensure_ascii=False))
    return html.encode("utf-8")


class WebHandler(BaseHTTPRequestHandler):
    server_version = "PTBDWeb/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[ptbd-web] " + fmt % args + "\n")

    def send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_bytes(body, "application/json; charset=utf-8", status)

    def send_error_json(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self.send_json({"ok": False, "error": message}, status)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON object。")
        return payload

    def do_GET(self) -> None:
        base_path = getattr(self.server, "ptbd_base_path", "")
        path = strip_base_path(urlparse(self.path).path, base_path)
        if path is None:
            self.send_error_json("接口不存在。", HTTPStatus.NOT_FOUND)
            return
        if path == "/":
            self.send_bytes(render_index_html(base_path), "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self.send_json(
                {
                    "ok": True,
                    "app_root": str(APP_ROOT),
                    "config_path": str(CONFIG_PATH),
                    "backend_available": backend_available(),
                    "backend_status": backend_status(),
                }
            )
            return
        if path == "/api/config":
            self.send_json({"ok": True, "config": load_config(include_secret=False)})
            return
        if path.startswith("/api/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            with TASK_LOCK:
                task = TASKS.get(task_id)
            if task is None:
                self.send_error_json("任务不存在。", HTTPStatus.NOT_FOUND)
                return
            self.send_json({"ok": True, "task": task.to_public()})
            return
        self.send_error_json("接口不存在。", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        base_path = getattr(self.server, "ptbd_base_path", "")
        path = strip_base_path(urlparse(self.path).path, base_path)
        if path is None:
            self.send_error_json("接口不存在。", HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self.read_json_body()
            if path == "/api/config":
                data = sanitize_config(payload)
                save_config(data)
                self.send_json({"ok": True, "config": public_config(data)})
                return

            if path in {"/api/scan", "/api/process"}:
                active = running_task()
                if active is not None:
                    self.send_error_json(f"已有任务正在运行：{active.id}", HTTPStatus.CONFLICT)
                    return
                config = sanitize_config(payload.get("config", {}) if isinstance(payload.get("config"), dict) else {})
                save_config(config)
                if path == "/api/scan":
                    task = WebTask(kind="scan")
                    register_task(task)
                    threading.Thread(target=run_scan_task, args=(task, config), daemon=True).start()
                    self.send_json({"ok": True, "task": task.to_public()})
                    return
                paths = payload.get("paths", [])
                if not isinstance(paths, list):
                    raise ValueError("paths 必须是数组。")
                selected_paths = [str(item) for item in paths if str(item).strip()]
                if not selected_paths:
                    self.send_error_json("请至少选择一个候选路径。", HTTPStatus.BAD_REQUEST)
                    return
                task = WebTask(kind="process")
                register_task(task)
                threading.Thread(target=run_process_task, args=(task, config, selected_paths), daemon=True).start()
                self.send_json({"ok": True, "task": task.to_public()})
                return

            if path.startswith("/api/tasks/") and path.endswith("/cancel"):
                task_id = path.split("/")[-2]
                with TASK_LOCK:
                    task = TASKS.get(task_id)
                if task is None:
                    self.send_error_json("任务不存在。", HTTPStatus.NOT_FOUND)
                    return
                task.cancel()
                self.send_json({"ok": True, "task": task.to_public()})
                return

            self.send_error_json("接口不存在。", HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_REQUEST)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PT-BDtool local web controller")
    parser.add_argument("--host", default=os.environ.get("PTBD_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PTBD_WEB_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--base-path", default=os.environ.get("PTBD_WEB_BASE_PATH", ""), help="URL prefix when served behind a reverse proxy, e.g. /ptbd")
    parser.add_argument("--open", action="store_true", help="open the local web page after start")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    base_path = normalize_base_path(args.base_path)
    address = (args.host, args.port)
    server = ThreadingHTTPServer(address, WebHandler)
    server.ptbd_base_path = base_path  # type: ignore[attr-defined]
    url_path = f"{base_path}/" if base_path else "/"
    url = f"http://{args.host}:{args.port}{url_path}"
    print(f"{APP_NAME} listening on {url}")
    print(f"app root: {APP_ROOT}")
    print(f"config:   {CONFIG_PATH}")
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPT-BDtool Web stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
