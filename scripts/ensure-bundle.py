#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ptbd_core.bundle_archive import (
    LEGACY_OFFICIAL_BUNDLE_URL,
    LEGACY_OFFICIAL_CHECKSUM_URL,
    OFFICIAL_BOOTSTRAP_SHA256,
    OFFICIAL_BUNDLE_URL,
    OFFICIAL_CHECKSUM_URL,
    BundleArchiveError,
    BundleChecksumSidecarDigestError,
    BundleChecksumSidecarEncodingError,
    BundleChecksumUnavailableError,
    ExplicitBundleChecksumError,
    bundle_member_relative_path,
    resolve_bundle_checksum,
    verify_bundle_checksum,
)
from ptbd_core.bundle_archive import extract_bundle_archive as safe_extract_bundle_archive

BUNDLE_ROOT = PROJECT_ROOT / "third_party" / "bundle" / "linux-amd64"
DEFAULT_URL = os.environ.get(
    "PTBD_BUNDLE_URL",
    OFFICIAL_BUNDLE_URL,
)
DEFAULT_SHA256 = os.environ.get("PTBD_BUNDLE_SHA256", "").strip()
DEFAULT_CHECKSUM_URL = os.environ.get("PTBD_BUNDLE_CHECKSUM_URL", f"{DEFAULT_URL}.sha256")
ALLOW_UNVERIFIED = os.environ.get("PTBD_BUNDLE_ALLOW_UNVERIFIED", "0") == "1"
REQUIRED_RELATIVE_PATHS = (
    Path("bin/ffmpeg"),
    Path("bin/ffprobe"),
    Path("bin/mediainfo"),
    Path("bin/BDInfo"),
    Path("lib"),
)


def log(message: str, *, quiet: bool) -> None:
    if not quiet:
        print(f"[ensure-bundle] {message}")


def bundle_ready(bundle_root: Path) -> bool:
    return all((bundle_root / relative_path).exists() for relative_path in REQUIRED_RELATIVE_PATHS)


def download_bundle_archive(url: str, archive_path: Path, *, quiet: bool) -> None:
    log(f"download: {url}", quiet=quiet)
    try:
        with urllib.request.urlopen(url, timeout=120) as response, archive_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except (OSError, ValueError) as exc:
        raise BundleArchiveError(f"bundle download failed: {exc}") from exc


def expected_bundle_checksum(*, quiet: bool) -> str | None:
    def read_checksum_sidecar(url: str) -> bytes:
        log(f"download checksum: {url}", quiet=quiet)
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read()

    try:
        resolution = resolve_bundle_checksum(
            bundle_url=DEFAULT_URL,
            checksum_url=DEFAULT_CHECKSUM_URL,
            explicit_checksum=DEFAULT_SHA256,
            allow_unverified=ALLOW_UNVERIFIED,
            read_checksum_sidecar=read_checksum_sidecar,
        )
    except ExplicitBundleChecksumError as exc:
        raise BundleArchiveError(f"invalid PTBD_BUNDLE_SHA256: {exc}") from (exc.__cause__ or exc)
    except BundleChecksumUnavailableError as exc:
        raise BundleArchiveError(
            "bundle checksum unavailable; set PTBD_BUNDLE_SHA256, provide a .sha256 sidecar, "
            "or explicitly set PTBD_BUNDLE_ALLOW_UNVERIFIED=1"
        ) from (exc.__cause__ or exc)
    except BundleChecksumSidecarEncodingError as exc:
        raise BundleArchiveError("bundle checksum sidecar is not ASCII") from (exc.__cause__ or exc)
    except BundleChecksumSidecarDigestError as exc:
        raise BundleArchiveError(f"invalid bundle checksum sidecar: {exc}") from (exc.__cause__ or exc)

    if resolution.source == "official-bootstrap":
        log(
            "checksum sidecar unavailable; using pinned checksum for the legacy official asset",
            quiet=quiet,
        )
    elif resolution.source == "unverified":
        log(
            "WARNING: checksum unavailable; explicit unverified mode enabled: "
            f"{resolution.unavailable_error}",
            quiet=quiet,
        )
    return resolution.checksum


def extract_bundle_archive(archive_path: Path, bundle_root: Path, *, quiet: bool) -> None:
    safe_extract_bundle_archive(archive_path, bundle_root)
    log(f"bundle ready: {bundle_root}", quiet=quiet)


def ensure_bundle(*, force: bool, quiet: bool) -> int:
    if bundle_ready(BUNDLE_ROOT) and not force:
        log(f"reuse local bundle: {BUNDLE_ROOT}", quiet=quiet)
        return 0

    with tempfile.TemporaryDirectory(prefix="ptbd-bundle-download-") as temp_dir:
        archive_path = Path(temp_dir) / "PT-ReleaseKit-linux-amd64.tar.gz"
        download_bundle_archive(DEFAULT_URL, archive_path, quiet=quiet)
        expected = expected_bundle_checksum(quiet=quiet)
        if expected is not None:
            verify_bundle_checksum(archive_path, expected)
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
