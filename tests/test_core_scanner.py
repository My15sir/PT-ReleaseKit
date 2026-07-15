from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ptbd_core.models import MediaType
from ptbd_core.scanner import _scan_roots, resolve_candidate, scan, scan_json


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

    def test_remote_fallback_scans_only_regular_user_homes(self) -> None:
        with mock.patch.object(
            Path,
            "is_dir",
            autospec=True,
            side_effect=lambda path: path in {Path("/home"), Path("/data")},
        ):
            roots = _scan_roots(
                Path("/"),
                include_roots=None,
                full=True,
                remote_session=True,
            )

        self.assertEqual(roots, [Path("/home")])

    def test_remote_explicit_include_can_enable_non_default_root(self) -> None:
        with mock.patch.object(
            Path,
            "is_dir",
            autospec=True,
            side_effect=lambda path: path == Path("/data"),
        ):
            roots = _scan_roots(
                Path("/"),
                include_roots=["/data"],
                full=True,
                remote_session=True,
            )

        self.assertEqual(roots, [Path("/data")])

    def test_remote_default_does_not_fall_back_to_root_when_home_is_missing(self) -> None:
        with mock.patch.object(Path, "is_dir", autospec=True, return_value=False):
            roots = _scan_roots(
                Path("/"),
                include_roots=None,
                full=True,
                remote_session=True,
            )

        self.assertEqual(roots, [])

    def test_remote_explicit_root_enables_full_filesystem_scan(self) -> None:
        with mock.patch.object(
            Path,
            "is_dir",
            autospec=True,
            side_effect=lambda path: path == Path("/"),
        ):
            roots = _scan_roots(
                Path("/"),
                include_roots=["/"],
                full=True,
                remote_session=True,
            )

        self.assertEqual(roots, [Path("/")])

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

    def test_scan_reports_walk_resolve_and_complete_progress(self) -> None:
        transport_stream = self.touch(self.root / "capture.ts")
        self.touch(self.root / "nested" / "movie.mkv")
        events: list[dict] = []

        def probe(path: Path) -> bool:
            self.assertEqual(path, transport_stream)
            self.assertEqual(events[-1]["phase"], "resolving")
            self.assertEqual(events[-1]["operation"], "ffprobe")
            self.assertEqual(events[-1]["current_path"], str(transport_stream))
            return True

        items = scan(
            self.root,
            full=False,
            ts_probe=probe,
            progress_callback=events.append,
        )

        phases = [event["phase"] for event in events]
        self.assertEqual(phases[0], "walking")
        self.assertIn("resolving", phases)
        self.assertEqual(phases[-1], "complete")
        self.assertGreaterEqual(events[-1]["directories_scanned"], 2)
        self.assertGreaterEqual(events[-1]["files_scanned"], 2)
        self.assertEqual(events[-1]["processed_candidates"], events[-1]["total_candidates"])
        self.assertEqual(len(items), 2)

    def test_nested_include_roots_are_scanned_once(self) -> None:
        nested = self.root / "nested"
        self.touch(nested / "movie.mkv")

        with mock.patch(
            "ptbd_core.scanner._iter_tree_candidates",
            wraps=__import__("ptbd_core.scanner", fromlist=["_iter_tree_candidates"])._iter_tree_candidates,
        ) as walk:
            items = scan(self.root, full=True, include_roots=[self.root, nested])

        self.assertEqual([item.path for item in items], [str(nested / "movie.mkv")])
        self.assertEqual(walk.call_count, 1)

    def test_dotdot_include_root_is_normalized_before_collapsing(self) -> None:
        nested = self.root / "nested"
        nested.mkdir()
        parent_movie = self.touch(self.root / "parent.mkv")
        nested_movie = self.touch(nested / "nested.mkv")

        items = scan(
            self.root,
            full=True,
            include_roots=[nested / "..", nested],
        )

        self.assertEqual(
            {item.path for item in items},
            {str(parent_movie), str(nested_movie)},
        )

    def test_dotdot_exclude_root_cannot_bypass_pruning(self) -> None:
        private_movie = self.touch(self.root / "private" / "movie.mkv")
        visible_movie = self.touch(self.root / "visible" / "movie.mkv")

        items = scan(
            self.root,
            full=False,
            exclude_roots=[self.root / "public" / ".." / "private"],
        )

        self.assertEqual([item.path for item in items], [str(visible_movie)])
        self.assertNotIn(str(private_movie), [item.path for item in items])

    def test_fast_directory_walk_throttles_progress_transport(self) -> None:
        for index in range(130):
            (self.root / f"directory-{index:03d}").mkdir()
        events: list[dict] = []

        with mock.patch("ptbd_core.scanner.time.monotonic", return_value=1.0):
            scan(self.root, full=False, progress_callback=events.append)

        walking_events = [event for event in events if event["phase"] == "walking"]
        self.assertLess(len(walking_events), 10)
        self.assertEqual(events[-1]["phase"], "complete")
        self.assertEqual(events[-1]["directories_scanned"], 131)

    def test_fast_candidate_resolution_throttles_progress_transport(self) -> None:
        for index in range(200):
            self.touch(self.root / f"movie-{index:03d}.mkv")
        events: list[dict] = []

        with mock.patch("ptbd_core.scanner.time.monotonic", return_value=1.0):
            scan(self.root, full=False, progress_callback=events.append)

        resolving_events = [event for event in events if event["phase"] == "resolving"]
        self.assertLess(len(resolving_events), 12)
        self.assertEqual(resolving_events[-1]["processed_candidates"], 200)
        self.assertEqual(resolving_events[-1]["total_candidates"], 200)


if __name__ == "__main__":
    unittest.main()
