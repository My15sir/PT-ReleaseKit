from __future__ import annotations

import importlib.util
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest import mock

from ptbd_core.bundle_archive import BundleArchiveError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENSURE_BUNDLE_PATH = PROJECT_ROOT / "scripts" / "ensure-bundle.py"


def load_ensure_bundle_module():
    spec = importlib.util.spec_from_file_location("ensure_bundle_checksum_test", ENSURE_BUNDLE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load scripts/ensure-bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ensure_bundle = load_ensure_bundle_module()


def response_with(payload: bytes) -> mock.MagicMock:
    response = mock.MagicMock()
    response.__enter__.return_value.read.return_value = payload
    return response


class EnsureBundleChecksumTests(unittest.TestCase):
    def test_archive_download_error_is_wrapped_with_original_cause(self) -> None:
        unavailable = urllib.error.URLError("offline")
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(ensure_bundle.urllib.request, "urlopen", side_effect=unavailable),
        ):
            with self.assertRaisesRegex(BundleArchiveError, "bundle download failed") as raised:
                ensure_bundle.download_bundle_archive(
                    "https://example.invalid/bundle.tar.gz",
                    Path(temporary) / "bundle.tar.gz",
                    quiet=True,
                )
        self.assertIs(raised.exception.__cause__, unavailable)

    def test_explicit_digest_has_priority_over_sidecar(self) -> None:
        digest = "1" * 64
        with (
            mock.patch.object(ensure_bundle, "DEFAULT_SHA256", digest),
            mock.patch.object(ensure_bundle.urllib.request, "urlopen") as urlopen,
        ):
            self.assertEqual(ensure_bundle.expected_bundle_checksum(quiet=True), digest)
        urlopen.assert_not_called()

    def test_invalid_explicit_digest_has_configuration_context(self) -> None:
        with mock.patch.object(ensure_bundle, "DEFAULT_SHA256", "invalid"):
            with self.assertRaisesRegex(BundleArchiveError, "invalid PTBD_BUNDLE_SHA256") as raised:
                ensure_bundle.expected_bundle_checksum(quiet=True)
        self.assertIsInstance(raised.exception.__cause__, BundleArchiveError)

    def test_valid_sidecar_is_preferred_for_official_asset(self) -> None:
        digest = "2" * 64
        with (
            mock.patch.object(ensure_bundle, "DEFAULT_SHA256", ""),
            mock.patch.object(
                ensure_bundle.urllib.request,
                "urlopen",
                return_value=response_with(f"{digest}  bundle.tar.gz\n".encode("ascii")),
            ),
        ):
            self.assertEqual(ensure_bundle.expected_bundle_checksum(quiet=True), digest)

    def test_missing_renamed_official_sidecar_fails_closed(self) -> None:
        missing = urllib.error.HTTPError(
            ensure_bundle.OFFICIAL_CHECKSUM_URL,
            404,
            "Not Found",
            hdrs=None,
            fp=None,
        )
        with (
            mock.patch.object(ensure_bundle, "DEFAULT_SHA256", ""),
            mock.patch.object(ensure_bundle, "DEFAULT_URL", ensure_bundle.OFFICIAL_BUNDLE_URL),
            mock.patch.object(
                ensure_bundle,
                "DEFAULT_CHECKSUM_URL",
                ensure_bundle.OFFICIAL_CHECKSUM_URL,
            ),
            mock.patch.object(ensure_bundle.urllib.request, "urlopen", side_effect=missing),
        ):
            with self.assertRaisesRegex(BundleArchiveError, "bundle checksum unavailable") as raised:
                ensure_bundle.expected_bundle_checksum(quiet=True)
        self.assertIs(raised.exception.__cause__, missing)

    def test_missing_legacy_official_sidecar_uses_pinned_digest(self) -> None:
        missing = urllib.error.HTTPError(
            ensure_bundle.LEGACY_OFFICIAL_CHECKSUM_URL,
            404,
            "Not Found",
            hdrs=None,
            fp=None,
        )
        with (
            mock.patch.object(ensure_bundle, "DEFAULT_SHA256", ""),
            mock.patch.object(
                ensure_bundle,
                "DEFAULT_URL",
                ensure_bundle.LEGACY_OFFICIAL_BUNDLE_URL,
            ),
            mock.patch.object(
                ensure_bundle,
                "DEFAULT_CHECKSUM_URL",
                ensure_bundle.LEGACY_OFFICIAL_CHECKSUM_URL,
            ),
            mock.patch.object(ensure_bundle.urllib.request, "urlopen", side_effect=missing),
        ):
            self.assertEqual(
                ensure_bundle.expected_bundle_checksum(quiet=True),
                ensure_bundle.OFFICIAL_BOOTSTRAP_SHA256,
            )

    def test_custom_asset_cannot_use_the_official_bootstrap_digest(self) -> None:
        unavailable = urllib.error.URLError("offline")
        with (
            mock.patch.object(ensure_bundle, "DEFAULT_SHA256", ""),
            mock.patch.object(ensure_bundle, "DEFAULT_URL", "https://example.invalid/custom.tar.gz"),
            mock.patch.object(
                ensure_bundle,
                "DEFAULT_CHECKSUM_URL",
                "https://example.invalid/custom.tar.gz.sha256",
            ),
            mock.patch.object(ensure_bundle, "ALLOW_UNVERIFIED", False),
            mock.patch.object(ensure_bundle.urllib.request, "urlopen", side_effect=unavailable),
        ):
            with self.assertRaisesRegex(BundleArchiveError, "bundle checksum unavailable") as raised:
                ensure_bundle.expected_bundle_checksum(quiet=True)
        self.assertIs(raised.exception.__cause__, unavailable)

    def test_custom_checksum_url_disables_the_official_bootstrap_digest(self) -> None:
        unavailable = urllib.error.URLError("offline")
        with (
            mock.patch.object(ensure_bundle, "DEFAULT_SHA256", ""),
            mock.patch.object(ensure_bundle, "DEFAULT_URL", ensure_bundle.OFFICIAL_BUNDLE_URL),
            mock.patch.object(
                ensure_bundle,
                "DEFAULT_CHECKSUM_URL",
                "https://example.invalid/custom.sha256",
            ),
            mock.patch.object(ensure_bundle, "ALLOW_UNVERIFIED", False),
            mock.patch.object(ensure_bundle.urllib.request, "urlopen", side_effect=unavailable),
        ):
            with self.assertRaisesRegex(BundleArchiveError, "bundle checksum unavailable"):
                ensure_bundle.expected_bundle_checksum(quiet=True)

    def test_custom_asset_can_explicitly_allow_an_unverified_download(self) -> None:
        unavailable = urllib.error.URLError("offline")
        with (
            mock.patch.object(ensure_bundle, "DEFAULT_SHA256", ""),
            mock.patch.object(ensure_bundle, "DEFAULT_URL", "https://example.invalid/custom.tar.gz"),
            mock.patch.object(
                ensure_bundle,
                "DEFAULT_CHECKSUM_URL",
                "https://example.invalid/custom.tar.gz.sha256",
            ),
            mock.patch.object(ensure_bundle, "ALLOW_UNVERIFIED", True),
            mock.patch.object(ensure_bundle.urllib.request, "urlopen", side_effect=unavailable),
        ):
            self.assertIsNone(ensure_bundle.expected_bundle_checksum(quiet=True))

    def test_malformed_sidecar_is_never_treated_as_unavailable(self) -> None:
        with (
            mock.patch.object(ensure_bundle, "DEFAULT_SHA256", ""),
            mock.patch.object(ensure_bundle, "ALLOW_UNVERIFIED", True),
            mock.patch.object(
                ensure_bundle.urllib.request,
                "urlopen",
                return_value=response_with(b"not-a-digest\n"),
            ),
        ):
            with self.assertRaisesRegex(BundleArchiveError, "invalid bundle checksum sidecar") as raised:
                ensure_bundle.expected_bundle_checksum(quiet=True)
        self.assertIsInstance(raised.exception.__cause__, BundleArchiveError)

    def test_non_ascii_sidecar_preserves_decode_error_as_cause(self) -> None:
        with (
            mock.patch.object(ensure_bundle, "DEFAULT_SHA256", ""),
            mock.patch.object(
                ensure_bundle.urllib.request,
                "urlopen",
                return_value=response_with(b"\xff"),
            ),
        ):
            with self.assertRaisesRegex(BundleArchiveError, "sidecar is not ASCII") as raised:
                ensure_bundle.expected_bundle_checksum(quiet=True)
        self.assertIsInstance(raised.exception.__cause__, UnicodeDecodeError)


if __name__ == "__main__":
    unittest.main()
