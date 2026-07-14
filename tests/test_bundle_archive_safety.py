from __future__ import annotations

import importlib.util
import hashlib
import io
import tarfile
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import ptbd_remote_backend as backend
from ptbd_core import bundle_archive as archive_safety


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENSURE_BUNDLE_PATH = PROJECT_ROOT / "scripts" / "ensure-bundle.py"


def load_ensure_bundle_module():
    spec = importlib.util.spec_from_file_location("ensure_bundle_safety_test", ENSURE_BUNDLE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load scripts/ensure-bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ensure_bundle = load_ensure_bundle_module()


def regular_member(name: str, content: bytes) -> tuple[tarfile.TarInfo, bytes]:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = 0o4755
    return member, content


def special_member(name: str, member_type: bytes) -> tuple[tarfile.TarInfo, None]:
    member = tarfile.TarInfo(name)
    member.type = member_type
    member.linkname = "../../outside"
    return member, None


def write_archive(path: Path, members: list[tuple[tarfile.TarInfo, bytes | None]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for member, content in members:
            archive.addfile(member, io.BytesIO(content) if content is not None else None)


@contextmanager
def patched_limits(*, members: int = 10_000, file_size: int = 1024, total_size: int = 4096):
    with (
        mock.patch.object(archive_safety, "MAX_BUNDLE_MEMBERS", members),
        mock.patch.object(archive_safety, "MAX_BUNDLE_FILE_SIZE", file_size),
        mock.patch.object(archive_safety, "MAX_BUNDLE_TOTAL_SIZE", total_size),
    ):
        yield


class BundleArchiveSafetyTests(unittest.TestCase):
    modules = (backend, ensure_bundle)

    def test_delivery_adapters_use_the_shared_archive_guard(self) -> None:
        self.assertIs(backend.extract_bundle_archive, archive_safety.extract_bundle_archive)
        self.assertIs(backend.BundleArchiveError, archive_safety.BundleArchiveError)
        self.assertIs(ensure_bundle.safe_extract_bundle_archive, archive_safety.extract_bundle_archive)
        self.assertIs(ensure_bundle.BundleArchiveError, archive_safety.BundleArchiveError)
        self.assertIs(backend.resolve_bundle_checksum, archive_safety.resolve_bundle_checksum)
        self.assertIs(ensure_bundle.resolve_bundle_checksum, archive_safety.resolve_bundle_checksum)
        for adapter in (backend, ensure_bundle):
            self.assertEqual(adapter.OFFICIAL_BUNDLE_URL, archive_safety.OFFICIAL_BUNDLE_URL)
            self.assertEqual(adapter.OFFICIAL_CHECKSUM_URL, archive_safety.OFFICIAL_CHECKSUM_URL)
            self.assertEqual(
                adapter.OFFICIAL_BOOTSTRAP_SHA256,
                archive_safety.OFFICIAL_BOOTSTRAP_SHA256,
            )

    @staticmethod
    def extract(module, archive_path: Path, destination: Path) -> None:
        if module is backend:
            module.extract_bundle_archive(archive_path, destination)
        else:
            module.extract_bundle_archive(archive_path, destination, quiet=True)

    def test_safe_bundle_extracts_with_sanitized_permissions(self) -> None:
        for module in self.modules:
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive_path = root / "bundle.tar.gz"
                prefix = "release/third_party/bundle/linux-amd64"
                write_archive(
                    archive_path,
                    [
                        regular_member(f"{prefix}/bin/tool", b"binary"),
                        regular_member(f"{prefix}/lib/library.so", b"library"),
                    ],
                )

                destination = root / "installed"
                self.extract(module, archive_path, destination)

                self.assertEqual((destination / "bin/tool").read_bytes(), b"binary")
                self.assertEqual((destination / "lib/library.so").read_bytes(), b"library")
                self.assertEqual((destination / "bin/tool").stat().st_mode & 0o7000, 0)

    def test_unsafe_paths_are_rejected_before_existing_bundle_is_replaced(self) -> None:
        unsafe_names = (
            "/third_party/bundle/linux-amd64/bin/tool",
            "release//third_party/bundle/linux-amd64/bin/tool",
            "release/./third_party/bundle/linux-amd64/bin/tool",
            "release/third_party/bundle/linux-amd64/../../../../outside",
            r"release\third_party\bundle\linux-amd64\..\outside",
            "C:/third_party/bundle/linux-amd64/bin/tool",
        )
        for module in self.modules:
            for unsafe_name in unsafe_names:
                with (
                    self.subTest(module=module.__name__, member=unsafe_name),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    archive_path = root / "bundle.tar.gz"
                    write_archive(archive_path, [regular_member(unsafe_name, b"bad")])
                    destination = root / "installed"
                    destination.mkdir()
                    sentinel = destination / "keep.txt"
                    sentinel.write_text("keep", encoding="utf-8")

                    with self.assertRaises(module.BundleArchiveError):
                        self.extract(module, archive_path, destination)

                    self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
                    self.assertFalse((root / "outside").exists())

    def test_links_and_device_members_are_rejected(self) -> None:
        special_types = (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE)
        for module in self.modules:
            for member_type in special_types:
                with (
                    self.subTest(module=module.__name__, member_type=member_type),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    archive_path = root / "bundle.tar.gz"
                    write_archive(
                        archive_path,
                        [special_member("release/third_party/bundle/linux-amd64/bin/tool", member_type)],
                    )

                    with self.assertRaises(module.BundleArchiveError):
                        self.extract(module, archive_path, root / "installed")

    def test_member_and_size_limits_are_enforced(self) -> None:
        prefix = "release/third_party/bundle/linux-amd64"
        cases = (
            ({"members": 1}, [regular_member(f"{prefix}/one", b"1"), regular_member(f"{prefix}/two", b"2")]),
            ({"file_size": 2}, [regular_member(f"{prefix}/large", b"123")]),
            (
                {"total_size": 4},
                [regular_member(f"{prefix}/one", b"123"), regular_member(f"{prefix}/two", b"456")],
            ),
        )
        for module in self.modules:
            for limits, archive_members in cases:
                with (
                    self.subTest(module=module.__name__, limits=limits),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    archive_path = root / "bundle.tar.gz"
                    write_archive(archive_path, archive_members)

                    with patched_limits(**limits):
                        with self.assertRaises(module.BundleArchiveError):
                            self.extract(module, archive_path, root / "installed")

    def test_activation_failure_restores_existing_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "bundle.tar.gz"
            prefix = "release/third_party/bundle/linux-amd64"
            write_archive(archive_path, [regular_member(f"{prefix}/bin/tool", b"new")])
            destination = root / "installed"
            destination.mkdir()
            sentinel = destination / "keep.txt"
            sentinel.write_text("old", encoding="utf-8")
            original_replace = archive_safety.os.replace
            calls = 0

            def fail_activation(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("activation failed")
                return original_replace(source, target)

            with mock.patch.object(archive_safety.os, "replace", side_effect=fail_activation):
                with self.assertRaises(OSError):
                    archive_safety.extract_bundle_archive(archive_path, destination)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(root.glob(".installed.backup-*")), [])

    def test_bundle_checksum_is_required_to_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "bundle.tar.gz"
            archive_path.write_bytes(b"bundle payload")
            expected = hashlib.sha256(b"bundle payload").hexdigest()

            self.assertEqual(archive_safety.parse_sha256_checksum(f"{expected}  bundle.tar.gz\n"), expected)
            self.assertEqual(archive_safety.verify_bundle_checksum(archive_path, expected), expected)
            with self.assertRaises(archive_safety.BundleArchiveError):
                archive_safety.verify_bundle_checksum(archive_path, "0" * 64)
            with self.assertRaises(archive_safety.BundleArchiveError):
                archive_safety.parse_sha256_checksum("not-a-digest")


if __name__ == "__main__":
    unittest.main()
