from __future__ import annotations

import errno
import os
import re
import shutil
import tarfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .media_tools import bdinfo_report_valid


VIDEO_IMAGES = tuple(f"{index}.png" for index in range(1, 7))
AUDIO_FILES = ("mediainfo.txt", "频谱图.png")
DISC_FILES = (*VIDEO_IMAGES, "BDInfo.txt")
DRY_RUN_TEXT = "本次已关闭 mediainfo 与 screenshots，因此该目录为空（这是预期行为）。\n"


class ArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class OutputLayout:
    info_root: Path
    generation_name: str
    output_dir: Path
    overridden: bool = False


def _absolute(path: Path) -> Path:
    return path.expanduser().absolute()


def normalise_bdmv_source(source: str | Path) -> Path:
    path = _absolute(Path(source))
    if path.name == "BDMV" and path.is_dir():
        return path.parent
    return path


def resolve_output_layout(
    media_type: str,
    source: str | Path,
    output_override: str | Path | None = None,
    *,
    workspace_root: str | Path | None = None,
) -> OutputLayout:
    kind = media_type.upper()
    path = normalise_bdmv_source(source) if kind == "BDMV" else _absolute(Path(source))
    if output_override is not None and workspace_root is not None:
        raise ArtifactError("output override and workspace root are mutually exclusive")
    if output_override is not None:
        job_root = _absolute(Path(output_override)) / "PT-BDtool"
        output = job_root / "信息"
        return OutputLayout(job_root, "信息", output, overridden=True)

    if kind in {"VIDEO", "AUDIO", "ISO"}:
        base_dir = path.parent
    elif kind in {"BDMV", "AUDIO_DIR"}:
        base_dir = path
    else:
        raise ArtifactError(f"unsupported media type: {media_type}")
    if not base_dir.name:
        raise ArtifactError(f"cannot derive output name from source: {source}")
    if workspace_root is not None:
        info_root = _absolute(Path(workspace_root)) / "信息"
        return OutputLayout(info_root, base_dir.name, info_root / base_dir.name, overridden=True)
    info_root = base_dir.parent / "信息"
    return OutputLayout(info_root, base_dir.name, info_root / base_dir.name)


def resolve_output_dir(
    media_type: str,
    source: str | Path,
    output_override: str | Path | None = None,
    *,
    workspace_root: str | Path | None = None,
) -> Path:
    return resolve_output_layout(
        media_type,
        source,
        output_override,
        workspace_root=workspace_root,
    ).output_dir


def safe_name(value: str) -> str:
    cleaned = value.replace(" ", "_")
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", cleaned)
    return (cleaned or "unknown")[:64]


def unique_directory(root: Path, base: str) -> Path:
    candidate = root / base
    index = 2
    while candidate.exists():
        candidate = root / f"{base}_{index}"
        index += 1
    return candidate


def _reject_output_symlinks(path: Path, *, include_leaf: bool = True) -> None:
    # Higher ancestors may intentionally be symlinked mount aliases (for example
    # macOS /var). Only the directory being cleared and its immediate parent are
    # under this operation's trust boundary.
    components = (path.parent, path) if include_leaf else (path.parent,)
    for component in components:
        if component.is_symlink():
            raise ArtifactError(f"refusing to use a symlinked output path component: {component}")


def prepare_output_directory(directory: Path) -> Path:
    directory = _absolute(directory)
    _reject_output_symlinks(directory)
    if directory.exists() and not directory.is_dir():
        raise ArtifactError(f"output path is not a directory: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    _reject_output_symlinks(directory)
    for child in directory.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
    return directory


def _require_nonempty(path: Path) -> None:
    try:
        valid = not path.is_symlink() and path.is_file() and path.stat().st_size > 0
    except OSError:
        valid = False
    if not valid:
        raise ArtifactError(f"required artifact is missing or empty: {path}")


def _keep_only(directory: Path, names: Iterable[str]) -> None:
    expected = set(names)
    for child in directory.iterdir():
        if child.name in expected:
            continue
        if child.is_symlink() or child.is_file():
            child.unlink()
        else:
            shutil.rmtree(child)


def validate_video_output(
    directory: Path,
    *,
    media_info: bool = True,
    screenshots: bool = True,
) -> tuple[Path, ...]:
    expected: list[str] = []
    if media_info:
        expected.append("mediainfo.txt")
    if screenshots:
        expected.extend(VIDEO_IMAGES)
    if not expected:
        expected.append("README.txt")
    for name in expected:
        _require_nonempty(directory / name)
    _keep_only(directory, expected)
    return tuple(sorted((directory / name for name in expected), key=lambda path: path.name))


def validate_audio_output(directory: Path) -> tuple[Path, ...]:
    for name in AUDIO_FILES:
        _require_nonempty(directory / name)
    _keep_only(directory, AUDIO_FILES)
    return tuple(directory / name for name in AUDIO_FILES)


def validate_audio_directory_output(directory: Path) -> tuple[Path, ...]:
    if any(child.is_file() or child.is_symlink() for child in directory.iterdir()):
        raise ArtifactError(f"single-track audio directory has unexpected root files: {directory}")
    track_dirs = sorted((child for child in directory.iterdir() if child.is_dir()), key=lambda p: p.name)
    if not track_dirs:
        raise ArtifactError(f"audio directory produced no track outputs: {directory}")
    files: list[Path] = []
    for track_dir in track_dirs:
        files.extend(validate_audio_output(track_dir))
    return tuple(files)


def validate_disc_output(directory: Path) -> tuple[Path, ...]:
    for name in DISC_FILES:
        _require_nonempty(directory / name)
    if not bdinfo_report_valid(directory / "BDInfo.txt"):
        raise ArtifactError(f"invalid BDInfo report: {directory / 'BDInfo.txt'}")
    _keep_only(directory, DISC_FILES)
    return tuple(directory / name for name in DISC_FILES)


def list_artifact_files(directory: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                path
                for path in directory.rglob("*")
                if not path.is_symlink() and path.is_file()
            ),
            key=lambda path: path.relative_to(directory).as_posix(),
        )
    )


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _publish_unique_archive(
    temporary: Path,
    destination: Path,
    base_name: str,
    suffix: str,
) -> Path:
    hardlink_fallback_errors = {
        errno.EACCES,
        errno.EPERM,
        errno.EXDEV,
        errno.ENOSYS,
        errno.ENOTSUP,
        errno.EOPNOTSUPP,
    }
    index = 1
    while True:
        candidate_name = f"{base_name}{suffix}" if index == 1 else f"{base_name}_{index}{suffix}"
        candidate = destination / candidate_name
        try:
            # The temporary file lives in the same directory, so a hard-link publishes
            # the completed inode atomically without ever replacing an existing name.
            os.link(temporary, candidate)
        except FileExistsError:
            index += 1
            continue
        except OSError as exc:
            if exc.errno not in hardlink_fallback_errors:
                raise
            try:
                descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                index += 1
                continue
            os.close(descriptor)
            try:
                os.replace(temporary, candidate)
            except Exception:
                candidate.unlink(missing_ok=True)
                raise
        return candidate


def package_output(
    output_dir: str | Path,
    destination_dir: str | Path,
    *,
    prefer_zip: bool = True,
) -> Path:
    source = _absolute(Path(output_dir))
    destination = _absolute(Path(destination_dir))
    if not source.is_dir():
        raise ArtifactError(f"cannot package missing output directory: {source}")
    if _is_within(destination, source):
        raise ArtifactError("package destination cannot be inside the generated output directory")
    destination.mkdir(parents=True, exist_ok=True)
    formats = ((".zip", True), (".tar.gz", False)) if prefer_zip else ((".tar.gz", False),)
    failures: list[tuple[str, Exception]] = []
    for suffix, use_zip in formats:
        temporary = destination / f".{source.name}.{uuid.uuid4().hex}{suffix}.tmp"
        try:
            if use_zip:
                with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as handle:
                    for file_path in list_artifact_files(source):
                        arcname = Path(source.name) / file_path.relative_to(source)
                        handle.write(file_path, arcname.as_posix())
            else:
                with tarfile.open(temporary, "w:gz") as handle:
                    for file_path in list_artifact_files(source):
                        arcname = Path(source.name) / file_path.relative_to(source)
                        handle.add(file_path, arcname=arcname.as_posix(), recursive=False)
            return _publish_unique_archive(temporary, destination, source.name, suffix)
        except Exception as exc:
            failures.append((suffix, exc))
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    details = "; ".join(f"{suffix}: {error}" for suffix, error in failures)
    cause = failures[-1][1] if failures else None
    raise ArtifactError(f"failed to create package for {source} in {destination}: {details}") from cause


def cleanup_output(output_dir: str | Path) -> bool:
    target = _absolute(Path(output_dir))
    if not target.exists() and not target.is_symlink():
        return False
    if target == Path(target.anchor):
        raise ArtifactError("refusing to clean a filesystem root")
    _reject_output_symlinks(target, include_leaf=False)
    if target.is_symlink() or target.is_file():
        target.unlink()
    else:
        shutil.rmtree(target)
    return True


__all__ = [
    "AUDIO_FILES",
    "DISC_FILES",
    "DRY_RUN_TEXT",
    "ArtifactError",
    "OutputLayout",
    "VIDEO_IMAGES",
    "cleanup_output",
    "list_artifact_files",
    "normalise_bdmv_source",
    "package_output",
    "prepare_output_directory",
    "resolve_output_dir",
    "resolve_output_layout",
    "safe_name",
    "unique_directory",
    "validate_audio_directory_output",
    "validate_audio_output",
    "validate_disc_output",
    "validate_video_output",
]
