from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Sequence

from .config import parse_path_roots_json, parse_path_roots_lines, split_path_roots
from .pipeline import MediaPipeline, ProcessingOptions
from .returns import package_stage_dir, return_archive, serialize_return_record
from .scanner import resolve_candidate, scan, scan_json


VERSION = "0.2.0"


def _split_roots(raw: str) -> list[str]:
    try:
        return split_path_roots(raw)
    except ValueError:
        return []


def _roots_from_env(name: str) -> list[str] | None:
    structured = os.environ.get(f"{name}_JSON", "")
    if structured.strip():
        try:
            return parse_path_roots_json(structured)
        except ValueError as exc:
            raise ValueError(f"{name}_JSON is invalid: {exc}") from exc

    lines = os.environ.get(f"{name}_LINES", "")
    if lines:
        try:
            return parse_path_roots_lines(lines)
        except ValueError as exc:
            raise ValueError(f"{name}_LINES is invalid: {exc}") from exc

    if name not in os.environ:
        return None
    return _split_roots(os.environ[name])


def _media_type_value(value: object) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value)


def _processing_options(args: argparse.Namespace) -> ProcessingOptions:
    explicit_output = getattr(args, "out", None)
    output_dir = Path(explicit_output).expanduser() if explicit_output else None
    explicit_workspace = getattr(args, "work_dir", None)
    workspace_dir = Path(explicit_workspace).expanduser() if explicit_workspace else None
    return ProcessingOptions(
        output_dir=output_dir,
        workspace_dir=workspace_dir,
        media_info=not getattr(args, "no_mediainfo", False),
        screenshots=not getattr(args, "no_shots", False),
        screenshot_candidates=int(os.environ.get("BDTOOL_SCREENSHOT_CANDIDATES", "18")),
        audio_spectrum_mode=getattr(args, "audio_spectrum", None)
        or os.environ.get("BDTOOL_AUDIO_SPECTRUM_MODE", "single"),
        audio_spectrum_backend=getattr(args, "audio_spectrum_backend", None)
        or os.environ.get("BDTOOL_AUDIO_SPECTRUM_BACKEND", "auto"),
        audio_spectrum_seconds=int(os.environ.get("BDTOOL_AUDIO_SPECTRUM_SECONDS", "90")),
        combined_track_seconds=int(
            getattr(args, "audio_spectrum_seconds", None)
            if getattr(args, "audio_spectrum_seconds", None) is not None
            else os.environ.get("BDTOOL_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS", "12")
        ),
        spectrum_size=os.environ.get("BDTOOL_AUDIO_SPECTRUM_SIZE", "1280x720"),
    )


def _scan_kwargs(*, root: str, full: bool, lang: str) -> dict[str, object]:
    return {
        "root": root,
        "include_roots": _roots_from_env("BDTOOL_SCAN_INCLUDE_ROOTS"),
        "exclude_roots": _roots_from_env("BDTOOL_SCAN_EXCLUDE_ROOTS"),
        "full": full,
        "lang": lang,
        "remote_session": bool(os.environ.get("SSH_CONNECTION")),
    }


def command_scan_json(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="bdtool scan-json")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--full", action="store_true")
    group.add_argument("--dir")
    parser.add_argument("--lang", choices=("zh", "en"), default=os.environ.get("LANG_CODE", "zh"))
    parser.add_argument("--progress-json", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(list(argv))
    root = args.dir if args.dir is not None else os.environ.get("BDTOOL_SCAN_FULL_ROOT", "/")
    if args.dir is not None and not Path(root).is_dir():
        parser.error(f"invalid scan directory: {root}")
    try:
        scan_kwargs = _scan_kwargs(root=root, full=args.dir is None, lang=args.lang)
    except ValueError as exc:
        parser.error(str(exc))
    progress_callback = None
    if args.progress_json:
        def emit_progress(progress: dict[str, object]) -> None:
            serialized = json.dumps(progress, ensure_ascii=False, separators=(",", ":"))
            print(f"PTBD_SCAN_PROGRESS\t{serialized}", file=sys.stderr, flush=True)

        progress_callback = emit_progress
    payload = scan_json(**scan_kwargs, progress_callback=progress_callback)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_generate_path(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="bdtool generate-path")
    parser.add_argument("target", nargs="?")
    parser.add_argument("--path", dest="path")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--out")
    output_group.add_argument("--work-dir", help="writable staging root (keeps source-derived package name)")
    parser.add_argument("--lang", choices=("zh", "en"), default="zh")
    parser.add_argument("--audio-spectrum", choices=("single", "combined"), default=None)
    parser.add_argument("--audio-spectrum-seconds", type=int, default=None)
    parser.add_argument("--result-json", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(list(argv))
    target = args.path or args.target
    if not target:
        parser.error("missing --path")
    resolved = resolve_candidate(target)
    if resolved is None:
        parser.error(f"unsupported target: {target}")
    media_type, source = resolved

    pipeline = MediaPipeline()
    result = pipeline.process(source, _media_type_value(media_type), _processing_options(args))
    stage_dir = package_stage_dir(result.output_dir)
    archive = pipeline.package(result.output_dir, stage_dir, cleanup=False)
    returned = return_archive(archive)
    if os.environ.get("BDTOOL_AUTO_CLEANUP", "1") == "1":
        pipeline.cleanup(result.output_dir)
    if args.result_json:
        print(serialize_return_record(returned))
    else:
        print(f"已下载：{returned.destination}")
        print("操作完成。")
    return 0


def _direct_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bdtool", add_help=True)
    parser.add_argument("target")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--out")
    output_group.add_argument("--work-dir")
    parser.add_argument("--log-level", choices=("quiet", "normal", "debug"), default="normal")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--no-mediainfo", action="store_true")
    parser.add_argument("--no-shots", action="store_true")
    parser.add_argument("--mode", choices=("dry",))
    parser.add_argument("--shots", "-s", type=int, default=6)
    parser.add_argument("--jobs", "-j", type=int, default=1)
    parser.add_argument("--audio-spectrum", choices=("single", "combined"), default=None)
    parser.add_argument(
        "--audio-spectrum-backend",
        choices=("auto", "sox", "sox_ng", "ffmpeg"),
        default=os.environ.get("BDTOOL_AUDIO_SPECTRUM_BACKEND", "auto"),
    )
    parser.add_argument("--audio-spectrum-seconds", type=int, default=None)
    return parser


def command_process(argv: Sequence[str]) -> int:
    args = _direct_parser().parse_args(list(argv))
    target = Path(args.target).expanduser()
    if not target.exists():
        print(f"未知命令或路径不存在：{target}", file=sys.stderr)
        return 2
    if args.shots <= 0 or args.jobs <= 0:
        print("--shots and --jobs must be positive integers", file=sys.stderr)
        return 2
    if args.audio_spectrum_seconds is not None and args.audio_spectrum_seconds < 0:
        print("--audio-spectrum-seconds must be non-negative", file=sys.stderr)
        return 2
    if args.mode == "dry":
        args.no_mediainfo = True
        args.no_shots = True
    os.environ["BDTOOL_AUDIO_SPECTRUM_BACKEND"] = args.audio_spectrum_backend

    resolved = resolve_candidate(target)
    candidates: list[tuple[str, Path]] = []
    if resolved is not None:
        media_type, source = resolved
        candidates.append((_media_type_value(media_type), source))
    elif target.is_dir():
        scan_items = scan(root=target, full=False, lang="zh")
        audio_directories = {
            Path(item.path)
            for item in scan_items
            if _media_type_value(item.type) == "AUDIO_DIR"
        }
        for item in scan_items:
            if _media_type_value(item.type) == "AUDIO" and Path(item.path).parent in audio_directories:
                continue
            candidates.append((_media_type_value(item.type), item.path))
    if not candidates:
        print(f"未发现可处理媒体文件：{target}", file=sys.stderr)
        return 1

    pipeline = MediaPipeline()
    options = _processing_options(args)
    try:
        for index, (media_type, source) in enumerate(candidates, start=1):
            if not args.quiet and args.log_level != "quiet":
                print(f"处理媒体 {index}/{len(candidates)}：{source}")
            result = pipeline.process(source, media_type, options)
            if not args.quiet and args.log_level != "quiet":
                print(f"生成阶段输出：{result.output_dir}")
    except Exception as exc:
        print(f"步骤失败：{exc}", file=sys.stderr)
        return 1
    if len(candidates) > 1 and not args.quiet and args.log_level != "quiet":
        print("DONE")
    return 0


def command_doctor(*, status: bool = False) -> int:
    required = ("find", "ffmpeg", "ffprobe", "mediainfo")
    optional = ("BDInfo",)
    missing = 0
    for command in (*required, *optional):
        path = shutil.which(command)
        print(f"{'OK' if path else 'MISS'}: {command}")
        if not path and command in required:
            missing += 1
    return 1 if status and missing else 0


def command_clean() -> int:
    target = Path.cwd() / "bdtool-output"
    if target.is_dir():
        shutil.rmtree(target)
        print(f"cleaned: {target}")
    else:
        print("nothing to clean")
    return 0


def usage() -> None:
    print(
        """bdtool <path> [options]
bdtool scan <path> --out <dir> [options]
bdtool scan-json [--full|--dir PATH]
bdtool generate-path --path TARGET [--work-dir DIR]
bdtool doctor
bdtool status
bdtool version
bdtool clean"""
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        usage()
        return 0
    command = args[0]
    if command == "scan-json":
        return command_scan_json(args[1:])
    if command == "generate-path":
        return command_generate_path(args[1:])
    if command == "scan":
        if len(args) < 2:
            print("bdtool scan requires a path", file=sys.stderr)
            return 2
        return command_process([args[1], *args[2:]])
    if command in {"version", "--version", "-v"}:
        print(f"bdtool {VERSION}")
        return 0
    if command == "doctor":
        return command_doctor(status=False)
    if command == "status":
        return command_doctor(status=True)
    if command == "clean":
        return command_clean()
    if command in {"install", "start"}:
        print(f"{command} is handled by the compatibility launcher", file=sys.stderr)
        return 2
    if command.startswith("-") or not Path(command).expanduser().exists():
        usage()
        print(f"未知命令或路径不存在：{command}", file=sys.stderr)
        return 2
    return command_process(args)


if __name__ == "__main__":
    raise SystemExit(main())
