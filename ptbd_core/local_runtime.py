from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import default_save_dir, normalize_scan_roots, split_path_roots
from .returns import parse_return_record


REQUIRED_LOCAL_TOOLS = ("ffmpeg", "ffprobe", "mediainfo")
OPTIONAL_LOCAL_TOOLS = ("BDInfo",)


def _resolved_path(value: Any, *, fallback: str | Path) -> Path:
    text = str(value if value is not None else "").strip()
    return Path(text or fallback).expanduser().resolve()


def local_scan_roots(config: Mapping[str, Any]) -> list[Path]:
    primary = _resolved_path(config.get("local_root"), fallback=Path.home())
    roots = [primary]
    seen = {os.path.normcase(os.fspath(primary))}
    for raw in split_path_roots(config.get("scan_include")):
        candidate = _resolved_path(raw, fallback=primary)
        key = os.path.normcase(os.fspath(candidate))
        if key not in seen:
            roots.append(candidate)
            seen.add(key)
    return roots


def local_allowed_roots(config: Mapping[str, Any]) -> list[Path]:
    return local_scan_roots(config)


def ensure_local_path_allowed(config: Mapping[str, Any], selected_path: str | os.PathLike[str]) -> Path:
    target = Path(selected_path).expanduser().resolve()
    for root in local_allowed_roots(config):
        try:
            target.relative_to(root)
        except ValueError:
            continue
        return target
    raise ValueError(f"路径不在允许扫描范围内：{selected_path}")


def build_local_runtime_env(
    config: Mapping[str, Any],
    *,
    base_env: Mapping[str, str] | None = None,
    data_dir: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env.setdefault("HOME", str(Path.home()))
    roots = local_scan_roots(config)
    excludes = [
        _resolved_path(raw, fallback=roots[0])
        for raw in split_path_roots(config.get("scan_exclude"))
    ]
    save_dir = _resolved_path(config.get("save_dir"), fallback=default_save_dir())

    env["BDTOOL_SCAN_FULL_ROOT"] = os.fspath(roots[0])
    # Keep the legacy scalar as "extra roots" while structured formats carry
    # the complete authoritative list, including the primary local root.
    env["BDTOOL_SCAN_INCLUDE_ROOTS"] = normalize_scan_roots(
        [os.fspath(root) for root in roots[1:]]
    )
    env["BDTOOL_SCAN_INCLUDE_ROOTS_JSON"] = json.dumps([os.fspath(root) for root in roots], ensure_ascii=False)
    env["BDTOOL_SCAN_INCLUDE_ROOTS_LINES"] = "\n".join(os.fspath(root) for root in roots)
    env["BDTOOL_SCAN_EXCLUDE_ROOTS"] = normalize_scan_roots([os.fspath(root) for root in excludes])
    if excludes:
        env["BDTOOL_SCAN_EXCLUDE_ROOTS_JSON"] = json.dumps(
            [os.fspath(root) for root in excludes], ensure_ascii=False
        )
        env["BDTOOL_SCAN_EXCLUDE_ROOTS_LINES"] = "\n".join(os.fspath(root) for root in excludes)
    else:
        env.pop("BDTOOL_SCAN_EXCLUDE_ROOTS_JSON", None)
        env.pop("BDTOOL_SCAN_EXCLUDE_ROOTS_LINES", None)

    env["BDTOOL_DOWNLOAD_DIR"] = os.fspath(save_dir)
    env["BDTOOL_RETURN_MODE"] = "local"
    env["BDTOOL_AUTO_CLEANUP"] = "1" if bool(config.get("auto_cleanup", True)) else "0"
    env["BDTOOL_AUDIO_SPECTRUM_MODE"] = str(config.get("audio_spectrum_mode") or "single")
    env["BDTOOL_AUDIO_SPECTRUM_BACKEND"] = str(config.get("audio_spectrum_backend") or "auto")
    env["BDTOOL_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS"] = str(
        config.get("audio_spectrum_combined_track_seconds") or "12"
    )
    env["BDTOOL_POST_ACTION"] = "0"
    env["LANG_CODE"] = "zh"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["LANG"] = env.get("LANG") or "C.UTF-8"
    env["LC_ALL"] = env.get("LC_ALL") or "C.UTF-8"
    if data_dir is not None:
        env["BDTOOL_DATA_DIR"] = os.fspath(_resolved_path(data_dir, fallback=Path.cwd()))
    return env


def build_local_cli_command(
    *,
    frozen: bool | None = None,
    executable: str | os.PathLike[str] | None = None,
) -> list[str]:
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    program = os.fspath(executable or sys.executable)
    if is_frozen:
        return [program, "--core-cli"]
    return [program, "-m", "ptbd_core.cli"]


def parse_local_archive(output: str, save_dir: str | os.PathLike[str]) -> Path:
    records = [record for line in output.splitlines() if (record := parse_return_record(line)) is not None]
    if len(records) != 1:
        raise RuntimeError(f"本机处理未返回唯一归档路径，收到 {len(records)} 条结果记录。")
    record = records[0]
    if record.mode != "local":
        raise RuntimeError(f"本机处理返回了非本地模式：{record.mode}")

    root = Path(save_dir).expanduser().resolve(strict=True)
    archive = Path(record.destination).expanduser()
    if not archive.is_absolute():
        archive = root / archive
    resolved = archive.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"本机处理返回了保存目录之外的归档：{archive}") from exc
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise RuntimeError(f"本机处理返回的归档不存在或为空：{resolved}")
    return resolved


def local_dependency_report() -> dict[str, Any]:
    paths = {
        command: shutil.which(command)
        for command in (*REQUIRED_LOCAL_TOOLS, *OPTIONAL_LOCAL_TOOLS)
    }
    missing_required = [command for command in REQUIRED_LOCAL_TOOLS if not paths[command]]
    missing_optional = [command for command in OPTIONAL_LOCAL_TOOLS if not paths[command]]
    return {
        "ok": not missing_required,
        "paths": paths,
        "missing_required": missing_required,
        "missing_optional": missing_optional,
    }


__all__ = [
    "OPTIONAL_LOCAL_TOOLS",
    "REQUIRED_LOCAL_TOOLS",
    "build_local_cli_command",
    "build_local_runtime_env",
    "ensure_local_path_allowed",
    "local_allowed_roots",
    "local_dependency_report",
    "local_scan_roots",
    "parse_local_archive",
]
