from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ptbd_core.models import MediaType
from ptbd_core.scanner import resolve_candidate, scan, scan_json


class ScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_parent = Path(__file__).resolve().parents[1] / ".tmp"
        temporary_parent.mkdir(exist_ok=True)
        self.temporary_directory = tempfile.TemporaryDirectory(dir=temporary_parent)
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def touch(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def test_scan_supports_all_legacy_media_types(self) -> None:
        video = self.touch(self.root / "movie.mkv")
        audio = self.touch(self.root / "single.flac")
        album = self.root / "album"
        track_one = self.touch(album / "01.mp3")
        track_two = self.touch(album / "02.FLAC")
        image = self.touch(self.root / "disc.iso")
        disc = self.root / "bluray"
        self.touch(disc / "BDMV" / "PLAYLIST" / "00000.mpls")
        stream = self.touch(disc / "BDMV" / "STREAM" / "00000.m2ts")

        items = scan(self.root, full=False)
        by_path = {item.path: item.type for item in items}

        self.assertEqual(by_path[str(video)], MediaType.VIDEO)
        self.assertEqual(by_path[str(audio)], MediaType.AUDIO)
        self.assertEqual(by_path[str(track_one)], MediaType.AUDIO)
        self.assertEqual(by_path[str(track_two)], MediaType.AUDIO)
        self.assertEqual(by_path[str(album)], MediaType.AUDIO_DIR)
        self.assertEqual(by_path[str(image)], MediaType.ISO)
        self.assertEqual(by_path[str(disc)], MediaType.BDMV)
        self.assertNotIn(str(stream), by_path)
        self.assertEqual(sum(item.type == MediaType.BDMV for item in items), 1)

    def test_resolve_candidate_accepts_disc_root_and_bdmv_directory(self) -> None:
        disc = self.root / "disc"
        bdmv = disc / "BDMV"
        bdmv.mkdir(parents=True)

        self.assertEqual(resolve_candidate(disc), (MediaType.BDMV, disc))
        self.assertEqual(resolve_candidate(bdmv), (MediaType.BDMV, disc))

        items = scan(bdmv, full=False)
        self.assertEqual([(item.type, item.path) for item in items], [(MediaType.BDMV, str(disc))])

    def test_transport_stream_requires_probe_and_d_ts_is_always_rejected(self) -> None:
        transport_stream = self.touch(self.root / "capture.ts")
        declaration = self.touch(self.root / "types.d.ts")

        accepted = resolve_candidate(transport_stream, ts_probe=lambda _path: True)
        rejected = resolve_candidate(transport_stream, ts_probe=lambda _path: False)
        declaration_result = resolve_candidate(declaration, ts_probe=lambda _path: True)

        self.assertEqual(accepted, (MediaType.VIDEO, transport_stream))
        self.assertIsNone(rejected)
        self.assertIsNone(declaration_result)

    def test_fixed_and_custom_excludes_are_pruned(self) -> None:
        visible = self.touch(self.root / "visible" / "movie.mp4")
        self.touch(self.root / "node_modules" / "hidden.mkv")
        excluded_root = self.root / "cache"
        self.touch(excluded_root / "hidden.flac")

        items = scan(self.root, full=False, exclude_roots=[excluded_root])
        self.assertEqual([item.path for item in items], [str(visible)])

    def test_full_scan_include_roots_replace_target_and_deduplicate(self) -> None:
        included = self.root / "included"
        media = self.touch(included / "movie.mkv")
        self.touch(self.root / "outside.mp4")

        items = scan(
            self.root,
            full=True,
            include_roots=[included, included, self.root / "missing"],
        )
        self.assertEqual([item.path for item in items], [str(media)])

    def test_invalid_explicit_include_fails_closed(self) -> None:
        self.touch(self.root / "movie.mkv")

        items = scan(
            self.root,
            full=True,
            include_roots=[self.root / "missing"],
            remote_session=True,
        )
        self.assertEqual(items, [])

    def test_explicit_empty_include_fails_closed(self) -> None:
        self.touch(self.root / "movie.mkv")

        self.assertEqual(scan(self.root, full=True, include_roots=[]), [])

    def test_scan_json_has_legacy_shape_and_english_labels(self) -> None:
        media = self.touch(self.root / "movie.mp4")
        payload = scan_json(self.root, full=False, lang="en")

        self.assertEqual(
            payload,
            {
                "items": [
                    {
                        "index": 1,
                        "type": "VIDEO",
                        "type_label": "video",
                        "path": str(media),
                    }
                ]
            },
        )

    def test_audio_directory_requires_two_direct_tracks(self) -> None:
        album = self.root / "album"
        self.touch(album / "one.mp3")
        self.touch(album / "disc-two" / "two.mp3")

        self.assertIsNone(resolve_candidate(album))
        self.touch(album / "three.flac")
        self.assertEqual(resolve_candidate(album), (MediaType.AUDIO_DIR, album))


if __name__ == "__main__":
    unittest.main()
