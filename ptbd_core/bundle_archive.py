from __future__ import annotations

import os
import hashlib
import hmac
import re
import shutil
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable


OFFICIAL_BUNDLE_URL = (
    "https://github.com/My15sir/PT-ReleaseKit/releases/download/"
    "bundle-latest/PT-ReleaseKit-linux-amd64.tar.gz"
)
OFFICIAL_CHECKSUM_URL = f"{OFFICIAL_BUNDLE_URL}.sha256"
LEGACY_OFFICIAL_BUNDLE_URL = (
    "https://github.com/My15sir/PT-ReleaseKit/releases/download/"
    "bundle-latest/PT-BDtool-linux-amd64.tar.gz"
)
LEGACY_OFFICIAL_CHECKSUM_URL = f"{LEGACY_OFFICIAL_BUNDLE_URL}.sha256"
# This pin authenticates only the legacy filename during migration. The renamed
# asset must always provide its own sidecar or an explicit configured digest.
OFFICIAL_BOOTSTRAP_SHA256 = "ef648fd25c474618616c4f88088835dfb1c38bd86994c751d41c780cb02b85cc"
BUNDLE_MARKER_PARTS = ("third_party", "bundle", "linux-amd64")
MAX_BUNDLE_MEMBERS = 10_000
MAX_BUNDLE_FILE_SIZE = 1024 * 1024 * 1024
MAX_BUNDLE_TOTAL_SIZE = 4 * 1024 * 1024 * 1024


class BundleArchiveError(RuntimeError):
    pass


class ExplicitBundleChecksumError(BundleArchiveError):
    pass


class BundleChecksumUnavailableError(BundleArchiveError):
    pass


class BundleChecksumSidecarEncodingError(BundleArchiveError):
    pass


class BundleChecksumSidecarDigestError(BundleArchiveError):
    pass


@dataclass(frozen=True)
class BundleChecksumResolution:
    checksum: str | None
    source: str
    unavailable_error: Exception | None = None


def parse_sha256_checksum(raw: str) -> str:
    token = str(raw).strip().split(maxsplit=1)[0].lower() if str(raw).strip() else ""
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise BundleArchiveError("bundle checksum is not a valid SHA256 digest")
    return token


def resolve_bundle_checksum(
    *,
    bundle_url: str,
    checksum_url: str,
    explicit_checksum: str,
    allow_unverified: bool,
    read_checksum_sidecar: Callable[[str], bytes],
    unavailable_errors: tuple[type[Exception], ...] = (OSError, ValueError),
) -> BundleChecksumResolution:
    if explicit_checksum:
        try:
            checksum = parse_sha256_checksum(explicit_checksum)
        except BundleArchiveError as exc:
            raise ExplicitBundleChecksumError(str(exc)) from exc
        return BundleChecksumResolution(checksum, "explicit")

    try:
        checksum_payload = read_checksum_sidecar(checksum_url)
    except unavailable_errors as exc:
        if (
            bundle_url == LEGACY_OFFICIAL_BUNDLE_URL
            and checksum_url == LEGACY_OFFICIAL_CHECKSUM_URL
        ):
            return BundleChecksumResolution(
                OFFICIAL_BOOTSTRAP_SHA256,
                "official-bootstrap",
                exc,
            )
        if allow_unverified:
            return BundleChecksumResolution(None, "unverified", exc)
        raise BundleChecksumUnavailableError("bundle checksum sidecar is unavailable") from exc

    if not isinstance(checksum_payload, bytes):
        exc = TypeError("bundle checksum sidecar reader must return bytes")
        raise BundleChecksumSidecarEncodingError(str(exc)) from exc
    try:
        checksum_text = checksum_payload.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise BundleChecksumSidecarEncodingError("bundle checksum sidecar is not ASCII") from exc
    try:
        checksum = parse_sha256_checksum(checksum_text)
    except BundleArchiveError as exc:
        raise BundleChecksumSidecarDigestError(str(exc)) from exc
    return BundleChecksumResolution(checksum, "sidecar")


def verify_bundle_checksum(archive_path: Path, expected: str) -> str:
    expected_digest = parse_sha256_checksum(expected)
    hasher = hashlib.sha256()
    with archive_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if not hmac.compare_digest(actual, expected_digest):
        raise BundleArchiveError(
            f"bundle SHA256 mismatch: expected={expected_digest} actual={actual}"
        )
    return actual


def bundle_member_relative_path(member_name: str) -> Path | None:
    if not member_name or "\x00" in member_name or "\\" in member_name:
        raise BundleArchiveError(f"unsafe bundle member path: {member_name!r}")
    raw_parts = member_name.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise BundleArchiveError(f"unsafe bundle member path: {member_name!r}")
    normalized = PurePosixPath(member_name)
    if normalized.is_absolute() or PurePosixPath(*raw_parts) != normalized:
        raise BundleArchiveError(f"unsafe bundle member path: {member_name!r}")
    if any(":" in part for part in raw_parts):
        raise BundleArchiveError(f"unsafe bundle member path: {member_name!r}")

    parts = normalized.parts
    for index in range(len(parts) - len(BUNDLE_MARKER_PARTS) + 1):
        if parts[index : index + len(BUNDLE_MARKER_PARTS)] == BUNDLE_MARKER_PARTS:
            remainder = parts[index + len(BUNDLE_MARKER_PARTS) :]
            return Path(*remainder) if remainder else None
    return None


def _contained_bundle_target(stage_root: Path, relative_path: Path) -> Path:
    root = stage_root.resolve()
    target = (root / relative_path).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise BundleArchiveError(f"bundle member escapes extraction root: {relative_path}") from exc
    return target


def extract_bundle_archive(archive_path: Path, bundle_root: Path) -> int:
    bundle_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{bundle_root.name}.stage-",
        dir=bundle_root.parent,
    ) as temp_dir:
        stage_root = Path(temp_dir) / bundle_root.name
        stage_root.mkdir()
        member_count = 0
        total_size = 0
        extracted_files = 0
        seen_targets: set[Path] = set()

        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive:
                member_count += 1
                if member_count > MAX_BUNDLE_MEMBERS:
                    raise BundleArchiveError(
                        f"bundle archive contains more than {MAX_BUNDLE_MEMBERS} members"
                    )
                if not member.isdir() and not member.isfile():
                    raise BundleArchiveError(
                        f"unsupported bundle member type: {member.name}"
                    )
                relative_path = bundle_member_relative_path(member.name)
                if relative_path is None:
                    continue

                target_path = _contained_bundle_target(stage_root, relative_path)
                if target_path in seen_targets:
                    raise BundleArchiveError(f"duplicate bundle member: {member.name}")
                seen_targets.add(target_path)
                if member.isdir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue

                if member.size < 0 or member.size > MAX_BUNDLE_FILE_SIZE:
                    raise BundleArchiveError(
                        f"bundle member exceeds size limit: {member.name} ({member.size} bytes)"
                    )
                total_size += member.size
                if total_size > MAX_BUNDLE_TOTAL_SIZE:
                    raise BundleArchiveError(
                        f"bundle archive exceeds {MAX_BUNDLE_TOTAL_SIZE} extracted bytes"
                    )
                target_path.parent.mkdir(parents=True, exist_ok=True)
                source_handle = archive.extractfile(member)
                if source_handle is None:
                    raise BundleArchiveError(f"bundle member is unreadable: {member.name}")
                with source_handle, target_path.open("xb") as output_handle:
                    shutil.copyfileobj(source_handle, output_handle, length=1024 * 1024)
                if target_path.stat().st_size != member.size:
                    raise BundleArchiveError(f"bundle member size mismatch: {member.name}")
                try:
                    os.chmod(target_path, member.mode & 0o755)
                except OSError:
                    pass
                extracted_files += 1

        if extracted_files == 0:
            raise BundleArchiveError(
                "archive does not contain files below third_party/bundle/linux-amd64"
            )
        backup_root = bundle_root.parent / f".{bundle_root.name}.backup-{uuid.uuid4().hex}"
        had_existing = bundle_root.exists() or bundle_root.is_symlink()
        if had_existing:
            os.replace(bundle_root, backup_root)
        try:
            if bundle_root.exists() or bundle_root.is_symlink():
                raise BundleArchiveError(f"bundle destination is still occupied: {bundle_root}")
            os.replace(stage_root, bundle_root)
        except Exception as exc:
            if had_existing:
                try:
                    if bundle_root.exists() or bundle_root.is_symlink():
                        raise BundleArchiveError(f"cannot restore over occupied destination: {bundle_root}")
                    os.replace(backup_root, bundle_root)
                except Exception as rollback_exc:
                    raise BundleArchiveError(
                        f"bundle activation failed and rollback is at {backup_root}: {rollback_exc}"
                    ) from exc
            raise
        if had_existing:
            if backup_root.is_symlink() or backup_root.is_file():
                backup_root.unlink(missing_ok=True)
            elif backup_root.is_dir():
                shutil.rmtree(backup_root)
        return extracted_files


__all__ = [
    "BUNDLE_MARKER_PARTS",
    "MAX_BUNDLE_FILE_SIZE",
    "MAX_BUNDLE_MEMBERS",
    "MAX_BUNDLE_TOTAL_SIZE",
    "LEGACY_OFFICIAL_BUNDLE_URL",
    "LEGACY_OFFICIAL_CHECKSUM_URL",
    "OFFICIAL_BOOTSTRAP_SHA256",
    "OFFICIAL_BUNDLE_URL",
    "OFFICIAL_CHECKSUM_URL",
    "BundleArchiveError",
    "BundleChecksumResolution",
    "BundleChecksumSidecarDigestError",
    "BundleChecksumSidecarEncodingError",
    "BundleChecksumUnavailableError",
    "ExplicitBundleChecksumError",
    "bundle_member_relative_path",
    "extract_bundle_archive",
    "parse_sha256_checksum",
    "resolve_bundle_checksum",
    "verify_bundle_checksum",
]
