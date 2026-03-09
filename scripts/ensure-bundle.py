#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_ROOT = PROJECT_ROOT / "third_party" / "bundle" / "linux-amd64"
DEFAULT_URL = os.environ.get(
    "PTBD_BUNDLE_URL",
    "https://github.com/My15sir/PT-BDtool/releases/download/bundle-latest/PT-BDtool-linux-amd64.tar.gz",
)
REQUIRED_RELATIVE_PATHS = (
    Path("bin/ffmpeg"),
    Path("bin/ffprobe"),
    Path("bin/mediainfo"),
    Path("bin/BDInfo"),
    Path("lib"),
)
MARKER_PARTS = ("third_party", "bundle", "linux-amd64")


def log(message: str, *, quiet: bool) -> None:
    if not quiet:
        print(f"[ensure-bundle] {message}")


def bundle_ready(bundle_root: Path) -> bool:
    return all((bundle_root / relative_path).exists() for relative_path in REQUIRED_RELATIVE_PATHS)


def bundle_member_relative_path(member_name: str) -> Path | None:
    parts = PurePosixPath(member_name).parts
    for index in range(len(parts) - len(MARKER_PARTS) + 1):
        if parts[index : index + len(MARKER_PARTS)] == MARKER_PARTS:
            remainder = parts[index + len(MARKER_PARTS) :]
            if not remainder:
                return None
            return Path(*remainder)
    return None


def download_bundle_archive(url: str, archive_path: Path, *, quiet: bool) -> None:
    log(f"download: {url}", quiet=quiet)
    with urllib.request.urlopen(url, timeout=120) as response, archive_path.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def extract_bundle_archive(archive_path: Path, bundle_root: Path, *, quiet: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="ptbd-bundle-stage-") as temp_dir:
        stage_root = Path(temp_dir) / "linux-amd64"
        with tarfile.open(archive_path, "r:gz") as archive:
            extracted = 0
            for member in archive.getmembers():
                relative_path = bundle_member_relative_path(member.name)
                if relative_path is None:
                    continue
                target_path = stage_root / relative_path
                if member.isdir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    extracted += 1
                    continue
                if not member.isfile():
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                source_handle = archive.extractfile(member)
                if source_handle is None:
                    raise SystemExit(f"archive member unreadable: {member.name}")
                with source_handle, target_path.open("wb") as output_handle:
                    shutil.copyfileobj(source_handle, output_handle)
                try:
                    os.chmod(target_path, member.mode)
                except OSError:
                    pass
                extracted += 1
        if extracted == 0:
            raise SystemExit("archive does not contain third_party/bundle/linux-amd64")
        bundle_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(bundle_root, ignore_errors=True)
        shutil.move(str(stage_root), str(bundle_root))
    log(f"bundle ready: {bundle_root}", quiet=quiet)


def ensure_bundle(*, force: bool, quiet: bool) -> int:
    if bundle_ready(BUNDLE_ROOT) and not force:
        log(f"reuse local bundle: {BUNDLE_ROOT}", quiet=quiet)
        return 0

    with tempfile.TemporaryDirectory(prefix="ptbd-bundle-download-") as temp_dir:
        archive_path = Path(temp_dir) / "PT-BDtool-linux-amd64.tar.gz"
        download_bundle_archive(DEFAULT_URL, archive_path, quiet=quiet)
        extract_bundle_archive(archive_path, BUNDLE_ROOT, quiet=quiet)

    if not bundle_ready(BUNDLE_ROOT):
        raise SystemExit(f"bundle extract finished but files are incomplete: {BUNDLE_ROOT}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure linux-amd64 bundle exists in third_party/bundle")
    parser.add_argument("--force", action="store_true", help="Re-download even if local bundle already exists")
    parser.add_argument("--quiet", action="store_true", help="Reduce log output")
    args = parser.parse_args()
    return ensure_bundle(force=args.force, quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
