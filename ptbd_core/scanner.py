from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .config import trim_path_root
from .models import MediaType, ScanItem


VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".avi", ".mov", ".m2ts", ".wmv", ".webm", ".mpg", ".mpeg"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus"})
PRUNED_DIR_NAMES = frozenset({"proc", "sys", "dev", "run", "tmp", "node_modules", ".git", ".svn", ".cache", ".npm", ".pnpm-store"})
DEFAULT_REMOTE_ROOTS = ("/home", "/root", "/data", "/mnt", "/media", "/srv")
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
    return Path(raw)


def _is_within(path: Path, root: Path) -> bool:
    path_text = os.fspath(path)
    root_text = os.fspath(root)
    if path_text == root_text:
        return True
    separator = "" if root_text.endswith(("/", "\\")) else os.sep
    return path_text.startswith(root_text + separator)


def _is_excluded(path: Path, excludes: tuple[Path, ...]) -> bool:
    if path.name in PRUNED_DIR_NAMES:
        return True
    return any(_is_within(path, root) for root in excludes)


def _iter_tree_candidates(
    root: Path,
    *,
    excludes: tuple[Path, ...],
) -> tuple[list[Path], set[Path]]:
    candidates: list[Path] = []
    audio_directories: set[Path] = set()
    if not root.is_dir() or _is_excluded(root, excludes):
        return candidates, audio_directories
    if root.name == "BDMV":
        candidates.append(root)

    def onerror(_error: OSError) -> None:
        return None

    for current_raw, directories, files in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        current = Path(current_raw)
        kept_directories: list[str] = []
        for name in directories:
            child = current / name
            if child.is_symlink() or _is_excluded(child, excludes):
                continue
            kept_directories.append(name)
            if name == "BDMV":
                candidates.append(child)
        directories[:] = kept_directories

        direct_audio_count = 0
        for name in files:
            child = current / name
            if child.is_symlink() or _is_excluded(child, excludes):
                continue
            suffix = child.suffix.lower()
            lower_name = name.lower()
            if suffix in AUDIO_EXTENSIONS:
                direct_audio_count += 1
                candidates.append(child)
            elif suffix == ".iso" or suffix == ".ts" or suffix in VIDEO_EXTENSIONS:
                if not lower_name.endswith(".d.ts"):
                    candidates.append(child)
        if direct_audio_count >= 2:
            audio_directories.add(current)
    return candidates, audio_directories


def _scan_roots(
    root: Path,
    *,
    include_roots: Iterable[str | os.PathLike[str]] | None,
    full: bool,
    remote_session: bool,
) -> list[Path]:
    if not full:
        return [root]

    if include_roots is not None:
        included = [_normalize_root(item) for item in include_roots]
        return [item for item in included if item.is_dir()]
    if root != Path(os.path.sep) or not remote_session:
        return [root]

    remote_roots = [Path(item) for item in DEFAULT_REMOTE_ROOTS if Path(item).is_dir()]
    return remote_roots or [root]


def scan(
    root: str | os.PathLike[str] = os.path.sep,
    *,
    include_roots: Iterable[str | os.PathLike[str]] | None = None,
    exclude_roots: Iterable[str | os.PathLike[str]] | None = None,
    full: bool = True,
    lang: str = "zh",
    remote_session: bool | None = None,
    ts_probe: TsProbe | None = None,
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
    for scan_root in roots:
        root_candidates, root_audio_directories = _iter_tree_candidates(scan_root, excludes=excludes)
        raw_candidates.extend(root_candidates)
        audio_directories.update(root_audio_directories)
    raw_candidates.extend(sorted(audio_directories, key=os.fspath))

    resolved: list[tuple[MediaType, Path]] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        result = resolve_candidate(candidate, ts_probe=ts_probe)
        if result is None:
            continue
        media_type, normalized_path = result
        normalized_text = os.fspath(normalized_path)
        if normalized_text in seen:
            continue
        seen.add(normalized_text)
        resolved.append((media_type, normalized_path))

    return [
        ScanItem(
            index=index,
            type=media_type,
            type_label=type_label(media_type, lang),
            path=os.fspath(path),
        )
        for index, (media_type, path) in enumerate(resolved, start=1)
    ]


def scan_json(
    root: str | os.PathLike[str] = os.path.sep,
    **kwargs: Any,
) -> dict[str, list[dict[str, Any]]]:
    return {"items": [item.to_dict() for item in scan(root, **kwargs)]}
