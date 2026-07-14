#!/usr/bin/env python3
"""Canonical runtime asset manifest used by every delivery adapter."""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


REMOTE_RUNTIME_FILES = (
    "bdtool",
    "bdtool-legacy.sh",
    "bdtool.sh",
    "lib/ui.sh",
    "scripts/audio-spectrum.py",
)

CONTROLLER_FILES = REMOTE_RUNTIME_FILES + (
    "ptbd-remote.sh",
    "scripts/ensure-bundle.py",
    "scripts/prepare-remote-runtime.sh",
    "scripts/remote-upload-server.py",
)

INSTALL_FILES = REMOTE_RUNTIME_FILES + (
    "ptbd",
    "ptbd-gui",
    "ptbd-gui.py",
    "ptbd-web",
    "ptbd-web.py",
    "ptbd_remote_backend.py",
    "ptbd-start.sh",
    "ptbd-remote.sh",
    "ptbd-remote-start.sh",
    "PT-ReleaseKit.sh",
    "PT-ReleaseKit.desktop",
    "PT-ReleaseKit.command",
    "PT-ReleaseKit.bat",
    "PT-BDtool.sh",
    "PT-BDtool.desktop",
    "PT-BDtool.command",
    "PT-BDtool.bat",
    "install.sh",
    "README.md",
    "scripts/ensure-bundle.py",
    "scripts/prepare-remote-runtime.sh",
    "scripts/remote-upload-server.py",
)

BUNDLE_FILES = INSTALL_FILES + (
    "scripts/build-bundle.sh",
    "scripts/deps.env",
    "scripts/fetch-deps.sh",
    "scripts/update-deps.sh",
)

DOCKER_FILES = REMOTE_RUNTIME_FILES + (
    "ptbd-web.py",
    "ptbd_remote_backend.py",
    "docker/entrypoint.sh",
    "docker/healthcheck.py",
)

PROFILE_FILES = {
    "remote": REMOTE_RUNTIME_FILES,
    "controller": CONTROLLER_FILES,
    "install": INSTALL_FILES,
    "bundle": BUNDLE_FILES,
    "docker": DOCKER_FILES,
}

SHARED_ASSET_TREES = ("ptbd_core",)


class AssetManifestError(RuntimeError):
    """Raised when a delivery profile cannot be assembled."""


@dataclass(frozen=True)
class AssetEntry:
    source: Path
    relative_path: str


def _validate_relative_path(relative_path: str) -> str:
    normalized = PurePosixPath(relative_path)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise AssetManifestError(f"unsafe runtime asset path: {relative_path}")
    return normalized.as_posix()


def _iter_tree(root: Path, relative_root: str) -> list[AssetEntry]:
    tree_root = root / relative_root
    if not tree_root.is_dir():
        raise AssetManifestError(f"missing runtime asset tree: {tree_root}")
    entries: list[AssetEntry] = []
    for source in sorted(tree_root.rglob("*")):
        relative = source.relative_to(root)
        if "__pycache__" in relative.parts or source.suffix in {".pyc", ".pyo"}:
            continue
        if source.is_file() or source.is_symlink():
            entries.append(AssetEntry(source, relative.as_posix()))
    if not entries:
        raise AssetManifestError(f"runtime asset tree is empty: {tree_root}")
    return entries


def profile_entries(source_root: Path | str, profile: str) -> list[AssetEntry]:
    root = Path(source_root).resolve()
    if profile not in PROFILE_FILES:
        choices = ", ".join(sorted(PROFILE_FILES))
        raise AssetManifestError(f"unknown runtime asset profile {profile!r}; choose: {choices}")

    entries = [
        AssetEntry(root / relative, _validate_relative_path(relative))
        for relative in PROFILE_FILES[profile]
    ]
    for relative_root in SHARED_ASSET_TREES:
        entries.extend(_iter_tree(root, relative_root))

    unique: dict[str, AssetEntry] = {}
    for entry in entries:
        unique[entry.relative_path] = entry
    return [unique[path] for path in sorted(unique)]


def validate_profile(source_root: Path | str, profile: str) -> list[AssetEntry]:
    entries = profile_entries(source_root, profile)
    missing = [entry.relative_path for entry in entries if not entry.source.exists()]
    if missing:
        formatted = "\n".join(f"  - {path}" for path in missing)
        raise AssetManifestError(f"missing assets for {profile} profile:\n{formatted}")
    return entries


def copy_profile(source_root: Path | str, destination_root: Path | str, profile: str) -> list[AssetEntry]:
    entries = validate_profile(source_root, profile)
    destination = Path(destination_root).resolve()
    for entry in entries:
        target = destination / entry.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.source.is_symlink():
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(entry.source.readlink())
        else:
            shutil.copy2(entry.source, target)
    return entries


def read_shared_asset(source_root: Path | str, relative_path: str) -> str:
    relative = _validate_relative_path(relative_path)
    if not relative.startswith("ptbd_core/assets/"):
        raise AssetManifestError(f"not a shared script asset: {relative}")
    path = Path(source_root).resolve() / relative
    if not path.is_file():
        raise AssetManifestError(f"missing shared script asset: {path}")
    return path.read_text(encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "list"):
        child = subparsers.add_parser(command)
        child.add_argument("--profile", required=True, choices=sorted(PROFILE_FILES))
        child.add_argument("--source-root", type=Path, required=True)
    copy_parser = subparsers.add_parser("copy")
    copy_parser.add_argument("--profile", required=True, choices=sorted(PROFILE_FILES))
    copy_parser.add_argument("--source-root", type=Path, required=True)
    copy_parser.add_argument("--destination-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "copy":
        entries = copy_profile(args.source_root, args.destination_root, args.profile)
    else:
        entries = validate_profile(args.source_root, args.profile)
    if args.command == "list":
        for entry in entries:
            print(entry.relative_path)
    else:
        print(f"[runtime-assets] profile={args.profile} files={len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
