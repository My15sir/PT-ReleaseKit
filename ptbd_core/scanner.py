from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .config import trim_path_root
from .models import MediaType, ScanItem


VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".avi", ".mov", ".m2ts", ".wmv", ".webm", ".mpg", ".mpeg"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus"})
PRUNED_DIR_NAMES = frozenset({"proc", "sys", "dev", "run", "tmp", "node_modules", ".git", ".svn", ".cache", ".npm", ".pnpm-store"})
DEFAULT_REMOTE_ROOTS = ("/home", "/root", "/data", "/mnt", "/media", "/srv")
WALK_PROGRESS_INTERVAL = 0.2
WALK_PROGRESS_DIRECTORY_STEP = 64
WALK_PROGRESS_FILE_STEP = 256
RESOLVE_PROGRESS_INTERVAL = 0.2
RESOLVE_PROGRESS_STEP = 32
SYSTEM_EXCLUDED_ROOTS = (
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/tmp",
    "/var/tmp",
    "/var/cache",
    "/var/lib/docker",
    "/var/lib/containerd",
    "/snap",
    "/nix",
)

TYPE_LABELS = {
    "zh": {
        MediaType.VIDEO: "视频",
        MediaType.AUDIO: "音频",
        MediaType.AUDIO_DIR: "音乐目录",
        MediaType.BDMV: "原盘",
        MediaType.ISO: "ISO",
    },
    "en": {
        MediaType.VIDEO: "video",
        MediaType.AUDIO: "audio",
        MediaType.AUDIO_DIR: "music directory",
        MediaType.BDMV: "bluray",
        MediaType.ISO: "iso",
    },
}

TsProbe = Callable[[Path], bool]
ProgressCallback = Callable[[dict[str, Any]], None]
ShouldCancel = Callable[[], bool]
WalkProgressCallback = Callable[[Path, int, int, int], None]


def type_label(media_type: MediaType, lang: str = "zh") -> str:
    labels = TYPE_LABELS["en" if str(lang).lower() == "en" else "zh"]
    return labels[media_type]


def _default_ts_probe(path: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return False
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "video"


def is_video(path: str | os.PathLike[str], *, ts_probe: TsProbe | None = None) -> bool:
    candidate = Path(path)
    lower_name = candidate.name.lower()
    if lower_name.endswith(".d.ts"):
        return False
    if candidate.suffix.lower() == ".ts":
        return (ts_probe or _default_ts_probe)(candidate)
    return candidate.suffix.lower() in VIDEO_EXTENSIONS


def is_audio(path: str | os.PathLike[str]) -> bool:
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def is_audio_dir(path: str | os.PathLike[str]) -> bool:
    directory = Path(path)
    if not directory.is_dir():
        return False
    count = 0
    try:
        for child in directory.iterdir():
            if not child.is_symlink() and child.is_file() and is_audio(child):
                count += 1
                if count >= 2:
                    return True
    except OSError:
        return False
    return False


def bdmv_root_from_stream_file(path: str | os.PathLike[str]) -> Path | None:
    candidate = Path(path)
    parts = candidate.parts
    for index in range(len(parts) - 2):
        if parts[index : index + 2] != ("BDMV", "STREAM"):
            continue
        root_parts = parts[:index]
        if not root_parts or root_parts == (candidate.anchor,):
            return None
        root = Path(*root_parts)
        if (root / "BDMV" / "PLAYLIST").is_dir():
            return root
    return None


def resolve_candidate(
    path: str | os.PathLike[str],
    *,
    ts_probe: TsProbe | None = None,
) -> tuple[MediaType, Path] | None:
    """Resolve one path using the same precedence as ``bdtool``."""

    candidate = Path(path)
    try:
        if candidate.name == "BDMV" and candidate.is_dir():
            return MediaType.BDMV, candidate.parent
        if candidate.suffix.lower() == ".iso" and candidate.is_file():
            return MediaType.ISO, candidate
        if candidate.is_file() and is_video(candidate, ts_probe=ts_probe):
            bdmv_root = bdmv_root_from_stream_file(candidate)
            if bdmv_root is not None:
                return MediaType.BDMV, bdmv_root
            return MediaType.VIDEO, candidate
        if candidate.is_file() and is_audio(candidate):
            return MediaType.AUDIO, candidate
        if candidate.is_dir() and (candidate / "BDMV").is_dir():
            return MediaType.BDMV, candidate
        if candidate.is_dir() and is_audio_dir(candidate):
            return MediaType.AUDIO_DIR, candidate
    except OSError:
        return None
    return None


def _normalize_root(path: str | os.PathLike[str]) -> Path:
    raw = trim_path_root(os.fspath(path) or os.path.sep)
    return Path(os.path.normpath(os.path.expanduser(raw)))


def _is_within(path: Path, root: Path) -> bool:
    path_text = os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))
    root_text = os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(root))))
    try:
        return os.path.commonpath((path_text, root_text)) == root_text
    except ValueError:
        return False


def _is_excluded(path: Path, excludes: tuple[Path, ...]) -> bool:
    if path.name in PRUNED_DIR_NAMES:
        return True
    return any(_is_within(path, root) for root in excludes)


def _iter_tree_candidates(
    root: Path,
    *,
    excludes: tuple[Path, ...],
    progress_callback: WalkProgressCallback | None = None,
    should_cancel: ShouldCancel | None = None,
) -> tuple[list[Path], set[Path]]:
    candidates: list[Path] = []
    audio_directories: set[Path] = set()
    directories_scanned = 0
    files_scanned = 0
    last_report_at = 0.0
    last_report_directories = 0
    last_report_files = 0

    def cancelled() -> bool:
        return should_cancel is not None and should_cancel()

    def report(current: Path, *, force: bool = False) -> None:
        nonlocal last_report_at, last_report_directories, last_report_files
        if progress_callback is not None:
            now = time.monotonic()
            if not force and last_report_at and (
                now - last_report_at < WALK_PROGRESS_INTERVAL
                and directories_scanned - last_report_directories < WALK_PROGRESS_DIRECTORY_STEP
                and files_scanned - last_report_files < WALK_PROGRESS_FILE_STEP
            ):
                return
            progress_callback(
                current,
                directories_scanned,
                files_scanned,
                len(candidates) + len(audio_directories),
            )
            last_report_at = now
            last_report_directories = directories_scanned
            last_report_files = files_scanned

    if not root.is_dir() or _is_excluded(root, excludes):
        return candidates, audio_directories
    if root.name == "BDMV":
        candidates.append(root)
    last_current = root

    def onerror(_error: OSError) -> None:
        return None

    for current_raw, directories, files in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        if cancelled():
            break
        current = Path(current_raw)
        last_current = current
        directories_scanned += 1
        report(current)
        kept_directories: list[str] = []
        for name in directories:
            if cancelled():
                directories[:] = []
                return candidates, audio_directories
            child = current / name
            if child.is_symlink() or _is_excluded(child, excludes):
                continue
            kept_directories.append(name)
            if name == "BDMV":
                candidates.append(child)
        directories[:] = kept_directories

        direct_audio_count = 0
        for name in files:
            if cancelled():
                directories[:] = []
                return candidates, audio_directories
            child = current / name
            if child.is_symlink() or _is_excluded(child, excludes):
                continue
            files_scanned += 1
            suffix = child.suffix.lower()
            lower_name = name.lower()
            if suffix in AUDIO_EXTENSIONS:
                direct_audio_count += 1
                candidates.append(child)
            elif suffix == ".iso" or suffix == ".ts" or suffix in VIDEO_EXTENSIONS:
                if not lower_name.endswith(".d.ts"):
                    candidates.append(child)
            if files_scanned % 256 == 0:
                report(child)
        if direct_audio_count >= 2:
            audio_directories.add(current)
        report(current)
    report(last_current, force=True)
    return candidates, audio_directories


def _collapse_scan_roots(roots: Iterable[Path]) -> list[Path]:
    """Keep one walk for duplicate roots and roots nested below another root."""

    collapsed: list[Path] = []
    for root in roots:
        if any(_is_within(root, existing) for existing in collapsed):
            continue

        nested_indexes = [
            index for index, existing in enumerate(collapsed) if _is_within(existing, root)
        ]
        if not nested_indexes:
            collapsed.append(root)
            continue

        insertion_index = nested_indexes[0]
        collapsed = [
            existing
            for index, existing in enumerate(collapsed)
            if index not in nested_indexes
        ]
        collapsed.insert(insertion_index, root)
    return collapsed


def _scan_roots(
    root: Path,
    *,
    include_roots: Iterable[str | os.PathLike[str]] | None,
    full: bool,
    remote_session: bool,
) -> list[Path]:
    if not full:
        return _collapse_scan_roots([root])

    if include_roots is not None:
        included = [_normalize_root(item) for item in include_roots]
        return _collapse_scan_roots(item for item in included if item.is_dir())
    if root != Path(os.path.sep) or not remote_session:
        return _collapse_scan_roots([root])

    remote_roots = [Path(item) for item in DEFAULT_REMOTE_ROOTS if Path(item).is_dir()]
    return _collapse_scan_roots(remote_roots or [root])


def scan(
    root: str | os.PathLike[str] = os.path.sep,
    *,
    include_roots: Iterable[str | os.PathLike[str]] | None = None,
    exclude_roots: Iterable[str | os.PathLike[str]] | None = None,
    full: bool = True,
    lang: str = "zh",
    remote_session: bool | None = None,
    ts_probe: TsProbe | None = None,
    progress_callback: ProgressCallback | None = None,
    should_cancel: ShouldCancel | None = None,
) -> list[ScanItem]:
    """Scan media roots and return items compatible with ``scan-json``."""

    target_root = _normalize_root(root)
    roots = _scan_roots(
        target_root,
        include_roots=include_roots,
        full=full,
        remote_session=bool(os.environ.get("SSH_CONNECTION")) if remote_session is None else remote_session,
    )
    excludes = tuple(_normalize_root(item) for item in (*SYSTEM_EXCLUDED_ROOTS, *(exclude_roots or ())))
    raw_candidates: list[Path] = []
    audio_directories: set[Path] = set()
    directories_scanned = 0
    files_scanned = 0
    candidates_found = 0
    processed_candidates = 0
    total_candidates = 0
    root_total = len(roots)
    root_index = 1 if roots else 0
    current_path = os.fspath(roots[0] if roots else target_root)
    last_resolve_report_at = 0.0
    last_resolve_processed = -1

    def cancelled() -> bool:
        return should_cancel is not None and should_cancel()

    def report(
        phase: str,
        operation: str,
        path: str | os.PathLike[str] = "",
        *,
        force: bool = False,
    ) -> None:
        nonlocal last_resolve_report_at, last_resolve_processed
        if progress_callback is None:
            return
        if phase == "resolving" and operation == "classify":
            now = time.monotonic()
            if not force and last_resolve_report_at and (
                processed_candidates < total_candidates
                and now - last_resolve_report_at < RESOLVE_PROGRESS_INTERVAL
                and processed_candidates - last_resolve_processed < RESOLVE_PROGRESS_STEP
            ):
                return
            last_resolve_report_at = now
            last_resolve_processed = processed_candidates
        progress_callback(
            {
                "phase": phase,
                "root_index": root_index,
                "root_total": root_total,
                "directories_scanned": directories_scanned,
                "files_scanned": files_scanned,
                "candidates_found": candidates_found,
                "processed_candidates": processed_candidates,
                "total_candidates": total_candidates,
                "current_path": os.fspath(path) if path else current_path,
                "operation": operation,
            }
        )

    def build_items(resolved: list[tuple[MediaType, Path]]) -> list[ScanItem]:
        return [
            ScanItem(
                index=index,
                type=media_type,
                type_label=type_label(media_type, lang),
                path=os.fspath(path),
            )
            for index, (media_type, path) in enumerate(resolved, start=1)
        ]

    report("walking", "walk", current_path)
    if cancelled():
        return []

    for root_offset, scan_root in enumerate(roots, start=1):
        root_index = root_offset
        current_path = os.fspath(scan_root)
        root_directories_base = directories_scanned
        root_files_base = files_scanned
        root_candidates_base = candidates_found

        def report_walk(
            path: Path,
            root_directories: int,
            root_files: int,
            root_candidates: int,
        ) -> None:
            nonlocal directories_scanned, files_scanned, candidates_found, current_path
            directories_scanned = root_directories_base + root_directories
            files_scanned = root_files_base + root_files
            candidates_found = root_candidates_base + root_candidates
            current_path = os.fspath(path)
            report("walking", "walk", path)

        report("walking", "walk", scan_root)
        root_candidates, root_audio_directories = _iter_tree_candidates(
            scan_root,
            excludes=excludes,
            progress_callback=report_walk,
            should_cancel=should_cancel,
        )
        raw_candidates.extend(root_candidates)
        audio_directories.update(root_audio_directories)
        candidates_found = len(raw_candidates) + len(audio_directories)
        if cancelled():
            return []
    raw_candidates.extend(sorted(audio_directories, key=os.fspath))
    candidates_found = len(raw_candidates)
    total_candidates = candidates_found

    resolved: list[tuple[MediaType, Path]] = []
    seen: set[str] = set()
    current_path = os.fspath(raw_candidates[0]) if raw_candidates else ""
    report("resolving", "classify", current_path, force=True)
    for candidate in raw_candidates:
        if cancelled():
            return build_items(resolved)
        current_path = os.fspath(candidate)
        report("resolving", "classify", candidate)

        def probe_with_progress(path: Path) -> bool:
            nonlocal current_path
            current_path = os.fspath(path)
            report("resolving", "ffprobe", path)
            if cancelled():
                return False
            return (ts_probe or _default_ts_probe)(path)

        result = resolve_candidate(candidate, ts_probe=probe_with_progress)
        if cancelled():
            return build_items(resolved)
        processed_candidates += 1
        if result is None:
            report("resolving", "classify", candidate)
            continue
        media_type, normalized_path = result
        normalized_text = os.fspath(normalized_path)
        if normalized_text in seen:
            report("resolving", "classify", candidate)
            continue
        seen.add(normalized_text)
        resolved.append((media_type, normalized_path))
        report("resolving", "classify", candidate)

    current_path = ""
    report("complete", "complete", force=True)
    return build_items(resolved)


def scan_json(
    root: str | os.PathLike[str] = os.path.sep,
    **kwargs: Any,
) -> dict[str, list[dict[str, Any]]]:
    return {"items": [item.to_dict() for item in scan(root, **kwargs)]}
