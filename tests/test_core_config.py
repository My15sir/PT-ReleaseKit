from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ptbd_core.config import (
    default_config,
    load_config,
    normalize_scan_roots,
    parse_path_roots_json,
    parse_path_roots_lines,
    public_config,
    sanitize_config,
    save_config,
    split_path_roots,
    trim_path_root,
)
from ptbd_core.models import ImageHostProvider, RunMode, SpectrumBackend, SpectrumMode


class ConfigTests(unittest.TestCase):
    def test_local_root_defaults_to_home_and_environment_remains_authoritative(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("ptbd_core.config.Path.home", return_value=Path("/home/media")):
            self.assertEqual(default_config().local_root, "/home/media")
            self.assertEqual(sanitize_config({"local_root": ""}).local_root, "/home/media")

        with mock.patch.dict(os.environ, {"PTBD_WEB_LOCAL_ROOT": "/mnt/videos"}, clear=True):
            self.assertEqual(default_config().local_root, "/mnt/videos")

    def test_normalize_scan_roots_keeps_safe_unicode_paths_readable(self) -> None:
        self.assertEqual(normalize_scan_roots(["/home/发种"]), "/home/发种")
        self.assertEqual(normalize_scan_roots(["/home/My Movies", "/home/发种"]), "'/home/My Movies' /home/发种")

    def test_image_host_config_is_closed_by_default_and_preserves_secret_updates(self) -> None:
        self.assertFalse(default_config().image_host_enabled)
        existing = sanitize_config(
            {
                "image_host_enabled": "true",
                "image_host_provider": "lsky_v2",
                "image_host_endpoint": " https://img.example.test/api/v1/upload ",
                "image_host_token": "top-secret",
            }
        )
        updated = sanitize_config({"image_host_token": ""}, existing=existing)

        self.assertTrue(updated.image_host_enabled)
        self.assertEqual(updated.image_host_provider, ImageHostProvider.LSKY_V2)
        self.assertEqual(updated.image_host_endpoint, "https://img.example.test/api/v1/upload")
        self.assertEqual(updated.image_host_token, "top-secret")
        self.assertNotIn("top-secret", repr(updated))

        visible = public_config(updated)
        self.assertEqual(visible["image_host_token"], "")
        self.assertTrue(visible["image_host_token_saved"])

        cleared = sanitize_config({"clear_image_host_token": True}, existing=updated)
        self.assertEqual(cleared.image_host_token, "")

        changed_provider = sanitize_config(
            {"image_host_provider": "imgbb", "image_host_token": ""},
            existing=updated,
        )
        self.assertEqual(changed_provider.image_host_provider, ImageHostProvider.IMGBB)
        self.assertEqual(changed_provider.image_host_token, "")

        changed_with_token = sanitize_config(
            {"image_host_provider": "imgbb", "image_host_token": "new-secret"},
            existing=updated,
        )
        self.assertEqual(changed_with_token.image_host_token, "new-secret")

    def test_image_host_secret_round_trips_privately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "config.json"
            save_config(path, sanitize_config({"image_host_token": "secret"}))

            public = load_config(path, include_secret=False)
            private = load_config(path, include_secret=True)

        self.assertEqual(public["image_host_token"], "")
        self.assertTrue(public["image_host_token_saved"])
        self.assertEqual(private["image_host_token"], "secret")

    def test_sanitize_covers_local_and_spectrum_settings(self) -> None:
        config = sanitize_config(
            {
                "mode": " LOCAL ",
                "local_root": " /media ",
                "scan_include": "/media/movies/, /media/music /media/movies",
                "scan_exclude": "/media/cache/",
                "scan_full": "true",
                "audio_spectrum_mode": "COMBINED",
                "audio_spectrum_backend": "SOX_NG",
                "audio_spectrum_combined_track_seconds": 0,
                "remote_bootstrap": "false",
                "auto_cleanup": "1",
            }
        )

        self.assertEqual(config.mode, RunMode.LOCAL)
        self.assertEqual(config.local_root, "/media")
        self.assertEqual(config.scan_include, "/media/movies /media/music")
        self.assertEqual(config.scan_exclude, "/media/cache")
        self.assertTrue(config.scan_full)
        self.assertEqual(config.audio_spectrum_mode, SpectrumMode.COMBINED)
        self.assertEqual(config.audio_spectrum_backend, SpectrumBackend.SOX_NG)
        self.assertEqual(config.audio_spectrum_combined_track_seconds, "0")
        self.assertFalse(config.remote_bootstrap)
        self.assertTrue(config.auto_cleanup)

    def test_remote_target_and_password_update_are_normalized(self) -> None:
        existing = sanitize_config({"remote_password": "old-secret"})
        config = sanitize_config(
            {
                "remote_host": "ssh -p 2202 deploy@example.test",
                "remote_port": "22",
                "remote_password": "",
            },
            existing=existing,
        )

        self.assertEqual(config.remote_host, "deploy@example.test")
        self.assertEqual(config.remote_port, "2202")
        self.assertEqual(config.remote_password, "old-secret")
        self.assertNotIn("old-secret", repr(config))

        cleared = sanitize_config({"clear_password": True}, existing=config)
        self.assertEqual(cleared.remote_password, "")

    def test_public_config_never_exposes_password(self) -> None:
        config = sanitize_config({"remote_password": "secret", "scan_full": "false"})
        visible = public_config(config)
        self.assertEqual(visible["remote_password"], "")
        self.assertTrue(visible["password_saved"])
        self.assertFalse(visible["scan_full"])

    def test_scan_full_defaults_false_and_round_trips(self) -> None:
        self.assertFalse(sanitize_config({}).scan_full)

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "config.json"
            save_config(path, sanitize_config({"scan_full": "yes"}))

            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertIs(persisted["scan_full"], True)
            loaded = load_config(path, include_secret=True)
            self.assertIs(loaded["scan_full"], True)

    def test_invalid_port_and_control_characters_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            sanitize_config({"remote_port": "70000"})
        with self.assertRaises(ValueError):
            sanitize_config({"local_root": "/media\nexport BAD=1"})
        with self.assertRaises(ValueError):
            sanitize_config({"remote_password": "secret\r\nwhoami"})

    def test_failed_atomic_save_preserves_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "config.json"
            save_config(path, sanitize_config({"remote_host": "old.example", "remote_password": "old"}))
            original = path.read_bytes()

            with mock.patch("ptbd_core.config.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    save_config(path, sanitize_config({"remote_host": "new.example", "remote_password": "new"}))

            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob(".config.json.*.tmp")), [])

    def test_invalid_spectrum_values_fall_back_to_legacy_defaults(self) -> None:
        config = sanitize_config(
            {
                "audio_spectrum_mode": "all",
                "audio_spectrum_backend": "unknown",
                "audio_spectrum_combined_track_seconds": -1,
            }
        )
        self.assertEqual(config.audio_spectrum_mode, SpectrumMode.SINGLE)
        self.assertEqual(config.audio_spectrum_backend, SpectrumBackend.AUTO)
        self.assertEqual(config.audio_spectrum_combined_track_seconds, "12")

    def test_config_round_trip_uses_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "config.json"
            save_config(path, sanitize_config({"remote_password": "secret"}))

            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["remote_password"], "secret")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            visible = load_config(path, include_secret=False)
            self.assertEqual(visible["remote_password"], "")
            secret = load_config(path, include_secret=True)
            self.assertEqual(secret["remote_password"], "secret")

    def test_split_path_roots_matches_shell_comma_and_space_rules(self) -> None:
        self.assertEqual(split_path_roots("/data,/mnt/media /srv"), ["/data", "/mnt/media", "/srv"])

    def test_scan_roots_preserve_windows_quotes_apostrophes_and_commas(self) -> None:
        roots = [
            r"C:\Media\Movies",
            r"C:\Other",
            "\\\\server\\share\\",
            "/media/Movies, 2024",
            "/media/O'Brien Movies, 2024",
        ]
        encoded = normalize_scan_roots(roots)

        self.assertEqual(split_path_roots(encoded), roots)
        self.assertEqual(split_path_roots(r"C:\Media,C:\Other"), [r"C:\Media", r"C:\Other"])
        self.assertEqual(split_path_roots("/media/O'Brien"), ["/media/O'Brien"])
        self.assertEqual(split_path_roots("/media/O'Brien /mnt/More"), ["/media/O'Brien", "/mnt/More"])
        self.assertEqual(split_path_roots("/media/O'Brien Movies"), ["/media/O'Brien Movies"])
        self.assertEqual(trim_path_root("C:\\"), "C:\\")
        self.assertEqual(trim_path_root("\\\\server\\share\\"), "\\\\server\\share\\")

    def test_structured_scan_root_formats_preserve_each_path(self) -> None:
        roots = [r"C:\Media\Movies", "\\\\server\\share\\", "/media/Movies, 2024", "/media/O'Brien Movies"]

        self.assertEqual(parse_path_roots_json(json.dumps(roots)), roots)
        self.assertEqual(parse_path_roots_lines("\r\n".join(roots)), roots)
        with self.assertRaises(ValueError):
            parse_path_roots_json('{"root": "/media"}')
        with self.assertRaises(ValueError):
            parse_path_roots_json('["/media", 42]')

    def test_invalid_persisted_config_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "config.json"
            path.write_text('{"remote_port": "70000", "scan_include": "/media"}\n', encoding="utf-8")

            loaded = load_config(path, include_secret=True)

        self.assertEqual(loaded["remote_port"], "22")
        self.assertEqual(loaded["scan_include"], "")


if __name__ == "__main__":
    unittest.main()
