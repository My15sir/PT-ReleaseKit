from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import ptbd_core.image_hosts as image_hosts
from ptbd_core.image_hosts import (
    HTTP_MAX_ATTEMPTS,
    HTTP_TIMEOUT_SECONDS,
    ImageHostCancelledError,
    ImageHostError,
    upload_archive_images,
    upload_image,
)


class FakeResponse(io.BytesIO):
    status = 200


def image_host_config(provider: str, *, token: str = "private-token") -> dict[str, object]:
    return {
        "image_host_enabled": True,
        "image_host_provider": provider,
        "image_host_endpoint": "https://images.example.test/upload",
        "image_host_token": token,
    }


class ImageHostUploadTests(unittest.TestCase):
    def test_cross_origin_redirect_is_not_followed_with_authorization(self) -> None:
        redirected_requests: list[str | None] = []

        class TargetHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                redirected_requests.append(self.headers.get("Authorization"))
                payload = b'{"data":{"url":"https://cdn.example.test/leaked.png"}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_port}/steal")
                self.end_headers()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        threads = [
            threading.Thread(target=target.serve_forever, daemon=True),
            threading.Thread(target=redirect.serve_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            config = image_host_config("custom")
            config["image_host_endpoint"] = f"http://127.0.0.1:{redirect.server_port}/upload"
            with self.assertRaisesRegex(ImageHostError, "HTTP 302"):
                upload_image(b"frame", "frame.png", config)
        finally:
            redirect.shutdown()
            target.shutdown()
            redirect.server_close()
            target.server_close()
            for thread in threads:
                thread.join(timeout=5)

        self.assertEqual(redirected_requests, [])

    def test_non_loopback_plain_http_endpoint_is_rejected_by_default(self) -> None:
        config = image_host_config("custom")
        config["image_host_endpoint"] = "http://images.example.test/upload"
        with mock.patch.dict("ptbd_core.image_hosts.os.environ", {}, clear=True):
            with self.assertRaisesRegex(ImageHostError, "HTTPS"):
                upload_image(b"frame", "frame.png", config)

    def test_non_loopback_plain_http_requires_explicit_environment_opt_in(self) -> None:
        config = image_host_config("custom")
        config["image_host_endpoint"] = "http://192.168.10.5/upload"
        response = FakeResponse(b'{"data":{"url":"https://cdn.example.test/frame.png"}}')
        with (
            mock.patch.dict(
                "ptbd_core.image_hosts.os.environ",
                {"PTBD_ALLOW_INSECURE_IMAGE_HOST": "1"},
                clear=True,
            ),
            mock.patch("ptbd_core.image_hosts._open_request", return_value=response) as opener,
        ):
            url = upload_image(b"frame", "frame.png", config)

        self.assertEqual(url, "https://cdn.example.test/frame.png")
        self.assertEqual(opener.call_args.args[0].full_url, "http://192.168.10.5/upload")

    def test_real_http_multipart_round_trip_against_local_mock_host(self) -> None:
        captured: dict[str, object] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                captured["path"] = self.path
                captured["authorization"] = self.headers.get("Authorization")
                captured["content_type"] = self.headers.get("Content-Type")
                captured["body"] = self.rfile.read(length)
                payload = b'{"data":{"url":"https://cdn.example.test/live.png"}}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = image_host_config("custom")
            config["image_host_endpoint"] = f"http://127.0.0.1:{server.server_port}/upload"
            url = upload_image(b"real-image", "frame.png", config)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(url, "https://cdn.example.test/live.png")
        self.assertEqual(captured["path"], "/upload")
        self.assertEqual(captured["authorization"], "Bearer private-token")
        self.assertIn("multipart/form-data; boundary=", str(captured["content_type"]))
        self.assertIn(b'name="file"; filename="frame.png"', captured["body"])
        self.assertIn(b"real-image", captured["body"])

    def test_see_uses_recommended_default_but_accepts_smms_endpoint_override(self) -> None:
        config = image_host_config("see")
        config["image_host_endpoint"] = ""
        response = FakeResponse(b'{"data":{"url":"https://cdn.example.test/frame.png"}}')
        with mock.patch("ptbd_core.image_hosts._open_request", return_value=response) as urlopen:
            upload_image(b"frame", "frame.png", config)
        self.assertEqual(urlopen.call_args.args[0].full_url, "https://s.ee/api/v1/file/upload")

        config["image_host_endpoint"] = "https://sm.ms/api/v2/upload"
        response = FakeResponse(b'{"data":{"url":"https://cdn.example.test/frame.png"}}')
        with mock.patch("ptbd_core.image_hosts._open_request", return_value=response) as urlopen:
            upload_image(b"frame", "frame.png", config)
        self.assertEqual(urlopen.call_args.args[0].full_url, "https://sm.ms/api/v2/upload")

    def test_provider_protocols_use_expected_auth_fields_and_response_paths(self) -> None:
        cases = (
            ("imgbb", {"data": {"url": "https://cdn.example.test/imgbb.png"}}, "image", None),
            (
                "lsky_v2",
                {"data": {"links": {"url": "https://cdn.example.test/lsky.png"}}},
                "file",
                "Bearer private-token",
            ),
            ("see", {"data": {"url": "https://cdn.example.test/see.png"}}, "smfile", "private-token"),
            (
                "custom",
                {"result": [{"nested": {"link": "https://cdn.example.test/custom.png"}}]},
                "file",
                "Bearer private-token",
            ),
        )

        for provider, payload, field_name, authorization in cases:
            with self.subTest(provider=provider):
                response = FakeResponse(json.dumps(payload).encode())
                with mock.patch("ptbd_core.image_hosts._open_request", return_value=response) as urlopen:
                    result = upload_image(b"image-data", "frame.png", image_host_config(provider))

                self.assertTrue(result.startswith("https://cdn.example.test/"))
                request = urlopen.call_args.args[0]
                self.assertEqual(urlopen.call_args.kwargs["timeout"], HTTP_TIMEOUT_SECONDS)
                self.assertIn(f'name="{field_name}"'.encode(), request.data)
                self.assertEqual(request.get_header("Authorization"), authorization)
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
                if provider == "imgbb":
                    self.assertEqual(query["key"], ["private-token"])
                else:
                    self.assertNotIn("key", query)

    def test_transient_network_errors_retry_a_fixed_number_of_times(self) -> None:
        response = FakeResponse(b'{"data":{"url":"https://cdn.example.test/frame.png"}}')
        unavailable = urllib.error.URLError("temporary failure")
        with (
            mock.patch(
                "ptbd_core.image_hosts._open_request",
                side_effect=[unavailable, unavailable, response],
            ) as urlopen,
            mock.patch("ptbd_core.image_hosts.time.sleep") as sleep,
        ):
            url = upload_image(b"frame", "frame.png", image_host_config("imgbb"))

        self.assertEqual(url, "https://cdn.example.test/frame.png")
        self.assertEqual(urlopen.call_count, HTTP_MAX_ATTEMPTS)
        self.assertEqual(sleep.call_count, HTTP_MAX_ATTEMPTS - 1)

    def test_cancellation_after_response_arrives_closes_response(self) -> None:
        cancelled = False
        response = FakeResponse(b'{"data":{"url":"https://cdn.example.test/frame.png"}}')

        def open_request(_request: object, *, timeout: float) -> FakeResponse:
            nonlocal cancelled
            self.assertEqual(timeout, HTTP_TIMEOUT_SECONDS)
            cancelled = True
            return response

        with mock.patch("ptbd_core.image_hosts._open_request", side_effect=open_request):
            with self.assertRaises(ImageHostCancelledError):
                upload_image(
                    b"frame",
                    "frame.png",
                    image_host_config("custom"),
                    should_cancel=lambda: cancelled,
                )

        self.assertTrue(response.closed)

    def test_errors_and_custom_urls_never_expose_token_or_unsafe_schemes(self) -> None:
        token = "do-not-log-this"
        error = urllib.error.HTTPError(
            f"https://images.example.test/upload?key={token}",
            400,
            f"bad token {token}",
            None,
            None,
        )
        with mock.patch("ptbd_core.image_hosts._open_request", side_effect=error):
            with self.assertRaises(ImageHostError) as raised:
                upload_image(b"frame", "frame.png", image_host_config("imgbb", token=token))
        self.assertNotIn(token, str(raised.exception))

        response = FakeResponse(b'{"data":{"url":"javascript:alert(1)"}}')
        with mock.patch("ptbd_core.image_hosts._open_request", return_value=response):
            with self.assertRaisesRegex(ImageHostError, "safe image URL"):
                upload_image(b"frame", "frame.png", image_host_config("custom"))

        response = FakeResponse(b'{"data":{"url":"https://cdn.example.test/a.png][url=https://evil.test"}}')
        with mock.patch("ptbd_core.image_hosts._open_request", return_value=response):
            with self.assertRaisesRegex(ImageHostError, "safe image URL"):
                upload_image(b"frame", "frame.png", image_host_config("custom"))


class ImageHostArchiveTests(unittest.TestCase):
    @staticmethod
    def make_archive(root: Path) -> Path:
        archive = root / "Movie.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
            handle.writestr("Movie/1.png", b"first-image")
            handle.writestr("Movie/2.jpg", b"second-image")
            handle.writestr("Movie/mediainfo.txt", b"media report")
        return archive

    def test_disabled_upload_is_a_closed_noop(self) -> None:
        missing = Path("/does/not/exist.zip")
        report = upload_archive_images(
            missing,
            {
                "image_host_enabled": False,
                "image_host_provider": "not-configured",
                "image_host_token": "ignored\nwhile-disabled",
            },
        )

        self.assertFalse(report.enabled)
        self.assertFalse(report.archive_updated)
        self.assertEqual(report.attempted_count, 0)
        self.assertEqual(report.error, "")

    def test_pre_cancelled_upload_makes_no_request_and_reports_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = self.make_archive(Path(temporary))
            with mock.patch("ptbd_core.image_hosts.upload_image") as upload:
                report = upload_archive_images(
                    archive,
                    image_host_config("custom"),
                    should_cancel=lambda: True,
                )

        self.assertTrue(report.cancelled)
        self.assertEqual(report.attempted_count, 0)
        upload.assert_not_called()

    def test_cancellation_between_images_keeps_partial_report_without_rewriting_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = self.make_archive(Path(temporary))
            original = archive.read_bytes()
            cancelled = False
            progress: list[tuple[int, int, str]] = []

            def upload(
                _content: bytes,
                filename: str,
                _config: object,
                **_kwargs: object,
            ) -> str:
                return f"https://cdn.example.test/{Path(filename).name}"

            def on_progress(completed: int, total: int, message: str) -> None:
                nonlocal cancelled
                progress.append((completed, total, message))
                if completed == 1:
                    cancelled = True

            with mock.patch("ptbd_core.image_hosts.upload_image", side_effect=upload) as uploader:
                report = upload_archive_images(
                    archive,
                    image_host_config("custom"),
                    should_cancel=lambda: cancelled,
                    progress_callback=on_progress,
                )

            self.assertTrue(report.cancelled)
            self.assertFalse(report.archive_updated)
            self.assertEqual(report.success_count, 1)
            self.assertEqual(uploader.call_count, 1)
            self.assertEqual(archive.read_bytes(), original)
            self.assertTrue(any(completed == 1 and total == 2 for completed, total, _ in progress))

    def test_cancellation_before_metadata_replace_preserves_original_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = self.make_archive(Path(temporary))
            original = archive.read_bytes()
            cancelled = False

            def on_progress(_completed: int, _total: int, message: str) -> None:
                nonlocal cancelled
                if "写入图床链接" in message:
                    cancelled = True

            with mock.patch(
                "ptbd_core.image_hosts.upload_image",
                return_value="https://cdn.example.test/frame.png",
            ):
                report = upload_archive_images(
                    archive,
                    image_host_config("custom"),
                    should_cancel=lambda: cancelled,
                    progress_callback=on_progress,
                )

            self.assertTrue(report.cancelled)
            self.assertFalse(report.archive_updated)
            self.assertEqual(report.success_count, 2)
            self.assertEqual(archive.read_bytes(), original)
            self.assertEqual(list(archive.parent.glob(".Movie.zip.*.tmp")), [])

    def test_cancellation_during_metadata_copy_preserves_original_and_removes_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = self.make_archive(Path(temporary))
            original = archive.read_bytes()
            cancellation_checks = 0

            def should_cancel() -> bool:
                nonlocal cancellation_checks
                cancellation_checks += 1
                # The fourth check occurs after the first temporary ZIP chunk
                # is written, while the original archive is still untouched.
                return cancellation_checks >= 4

            with (
                mock.patch("ptbd_core.image_hosts.COPY_BUFFER_BYTES", 4),
                self.assertRaises(ImageHostCancelledError),
            ):
                image_hosts._atomic_add_metadata(
                    archive,
                    ("image-host.json", "image-host-links.txt", "image-host-bbcode.txt"),
                    (b"{}\n", b"https://cdn.example.test/frame.png\n", b"[img]url[/img]\n"),
                    should_cancel=should_cancel,
                )

            self.assertGreaterEqual(cancellation_checks, 4)
            self.assertEqual(archive.read_bytes(), original)
            self.assertEqual(list(archive.parent.glob(".Movie.zip.*.tmp")), [])

    def test_upload_results_and_link_files_are_atomically_added_to_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = self.make_archive(Path(temporary))

            def upload(
                _content: bytes,
                filename: str,
                _config: object,
                **_kwargs: object,
            ) -> str:
                if filename.endswith("2.jpg"):
                    raise ImageHostError("image-host service returned HTTP 400")
                return "https://cdn.example.test/1.png"

            with mock.patch("ptbd_core.image_hosts.upload_image", side_effect=upload):
                report = upload_archive_images(archive, image_host_config("custom"))

            self.assertTrue(report.archive_updated)
            self.assertEqual(report.attempted_count, 2)
            self.assertEqual(report.success_count, 1)
            self.assertEqual(report.failed_count, 1)
            self.assertEqual(report.urls, ("https://cdn.example.test/1.png",))
            self.assertEqual(report.to_dict()["failed_count"], 1)
            with zipfile.ZipFile(archive) as handle:
                self.assertEqual(handle.read("Movie/1.png"), b"first-image")
                self.assertEqual(handle.read("Movie/mediainfo.txt"), b"media report")
                metadata = json.loads(handle.read("Movie/image-host.json"))
                links = handle.read("Movie/image-host-links.txt").decode()
                bbcode = handle.read("Movie/image-host-bbcode.txt").decode()

            self.assertTrue(metadata["archive_updated"])
            self.assertEqual(metadata["archive"], "Movie.zip")
            self.assertEqual(metadata["success_count"], 1)
            self.assertEqual(links, "https://cdn.example.test/1.png\n")
            self.assertEqual(bbcode, "[img]https://cdn.example.test/1.png[/img]\n")

    def test_atomic_replace_failure_preserves_original_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = self.make_archive(Path(temporary))
            original = archive.read_bytes()
            with (
                mock.patch("ptbd_core.image_hosts.upload_image", return_value="https://cdn.example.test/x.png"),
                mock.patch("ptbd_core.image_hosts.os.replace", side_effect=OSError("replace failed")),
            ):
                report = upload_archive_images(archive, image_host_config("custom"))

            self.assertFalse(report.archive_updated)
            self.assertIn("failed to update", report.error)
            self.assertEqual(archive.read_bytes(), original)
            self.assertEqual(list(archive.parent.glob(".Movie.zip.*.tmp")), [])

    def test_unsafe_archive_is_reported_without_upload_or_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../frame.png", b"image")
            original = archive.read_bytes()

            with mock.patch("ptbd_core.image_hosts.upload_image") as upload:
                report = upload_archive_images(archive, image_host_config("custom"))

            self.assertFalse(report.archive_updated)
            self.assertTrue(report.error)
            self.assertEqual(archive.read_bytes(), original)
            upload.assert_not_called()

    def test_oversized_image_is_recorded_without_becoming_a_primary_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "large.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("Movie/large.webp", b"12345")

            with (
                mock.patch("ptbd_core.image_hosts.MAX_IMAGE_BYTES", 4),
                mock.patch("ptbd_core.image_hosts.upload_image") as upload,
            ):
                report = upload_archive_images(archive, image_host_config("custom"))

            self.assertTrue(report.archive_updated)
            self.assertEqual(report.failed_count, 1)
            self.assertEqual(report.error, "")
            upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
