from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ptbd_core.returns import (
    ReturnResult,
    build_upload_url,
    default_download_dir,
    detect_return_mode,
    parse_return_record,
    serialize_return_record,
)


class ReturnTests(unittest.TestCase):
    def test_detects_explicit_and_implicit_modes(self) -> None:
        self.assertEqual(detect_return_mode({}), "local")
        self.assertEqual(detect_return_mode({"BDTOOL_RETURN_HTTP_URL": "http://localhost/upload"}), "http")
        self.assertEqual(detect_return_mode({"BDTOOL_RETURN_SCP_HOST": "host"}), "scp")

    def test_build_upload_url_encodes_filename(self) -> None:
        self.assertEqual(
            build_upload_url("http://localhost/upload", "result file.zip"),
            "http://localhost/upload?filename=result%20file.zip",
        )

    def test_explicit_download_directory_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "downloads"
            self.assertEqual(default_download_dir({"BDTOOL_DOWNLOAD_DIR": str(target)}), target)
            self.assertTrue(target.is_dir())

    def test_machine_readable_result_record_round_trips(self) -> None:
        result = ReturnResult(mode="local", destination="/output/Movie_2.zip")

        encoded = serialize_return_record(result)

        self.assertEqual(parse_return_record(encoded), result)
        self.assertIsNone(parse_return_record("ordinary log line"))
        self.assertIsNone(parse_return_record('{"type":"something-else"}'))


if __name__ == "__main__":
    unittest.main()
