from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from ptbd_core.artifacts import (
    ArtifactError,
    cleanup_output,
    package_output,
    prepare_output_directory,
    resolve_output_layout,
    safe_name,
    unique_directory,
    validate_audio_directory_output,
    validate_video_output,
)


class OutputLayoutTests(unittest.TestCase):
    def test_file_output_uses_source_directory_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "downloads" / "Movie" / "movie.mkv"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"video")

            layout = resolve_output_layout("VIDEO", source)

            self.assertEqual(layout.info_root, root / "downloads" / "信息")
            self.assertEqual(layout.generation_name, "Movie")
            self.assertEqual(layout.output_dir, root / "downloads" / "信息" / "Movie")

    def test_bdmv_and_audio_directory_use_selected_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            disc = root / "Disc"
            (disc / "BDMV").mkdir(parents=True)
            album = root / "Album"
            album.mkdir()

            disc_layout = resolve_output_layout("BDMV", disc / "BDMV")
            album_layout = resolve_output_layout("AUDIO_DIR", album)

            self.assertEqual(disc_layout.output_dir, root / "信息" / "Disc")
            self.assertEqual(album_layout.output_dir, root / "信息" / "Album")

    def test_override_is_cli_root_not_final_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "movie.mkv"
            override = root / "result"
            source.write_bytes(b"video")

            layout = resolve_output_layout("VIDEO", source, override)

            self.assertEqual(layout.output_dir, override / "PT-BDtool" / "信息")
            self.assertTrue(layout.overridden)

    def test_workspace_preserves_source_directory_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "readonly-media" / "Movie" / "movie.mkv"
            workspace = root / "runtime" / "job-1"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"video")

            layout = resolve_output_layout("VIDEO", source, workspace_root=workspace)

            self.assertEqual(layout.info_root, workspace / "信息")
            self.assertEqual(layout.generation_name, "Movie")
            self.assertEqual(layout.output_dir, workspace / "信息" / "Movie")
            self.assertTrue(layout.overridden)

    def test_workspace_and_legacy_override_are_mutually_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "movie.mkv"
            source.write_bytes(b"video")

            with self.assertRaises(ArtifactError):
                resolve_output_layout("VIDEO", source, root / "out", workspace_root=root / "work")

    def test_safe_and_unique_names_match_existing_track_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / safe_name("01 - 开场!")
            first.mkdir()
            self.assertEqual(first.name, "01_-_")
            self.assertEqual(unique_directory(root, first.name).name, "01_-__2")


class ArtifactContractTests(unittest.TestCase):
    def test_prepare_output_rejects_symlink_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            victim = root / "victim"
            victim.mkdir()
            marker = victim / "keep.txt"
            marker.write_text("keep\n", encoding="utf-8")
            output = root / "output"
            output.symlink_to(victim, target_is_directory=True)

            with self.assertRaises(ArtifactError):
                prepare_output_directory(output)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")

    def test_prepare_output_rejects_symlinked_parent_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            victim = root / "victim"
            output = victim / "generated"
            output.mkdir(parents=True)
            marker = output / "keep.txt"
            marker.write_text("keep\n", encoding="utf-8")
            alias = root / "alias"
            alias.symlink_to(victim, target_is_directory=True)

            with self.assertRaises(ArtifactError):
                prepare_output_directory(alias / "generated")

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")

    def test_prepare_output_allows_symlinked_mount_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real_mount = root / "real-mount"
            output = real_mount / "信息" / "Movie"
            output.mkdir(parents=True)
            stale = output / "stale.txt"
            stale.write_text("stale\n", encoding="utf-8")
            mount_alias = root / "media"
            mount_alias.symlink_to(real_mount, target_is_directory=True)

            prepared = prepare_output_directory(mount_alias / "信息" / "Movie")

            self.assertEqual(prepared, mount_alias / "信息" / "Movie")
            self.assertFalse(stale.exists())

    def test_video_contract_removes_unexpected_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Movie"
            output.mkdir()
            (output / "mediainfo.txt").write_text("media\n", encoding="utf-8")
            for index in range(1, 7):
                (output / f"{index}.png").write_bytes(b"png")
            (output / "stale.txt").write_text("stale\n", encoding="utf-8")
            (output / "stale-dir").mkdir()

            files = validate_video_output(output)

            self.assertEqual(len(files), 7)
            self.assertEqual({path.name for path in output.iterdir()}, {"mediainfo.txt", *(f"{i}.png" for i in range(1, 7))})

    def test_single_audio_directory_requires_two_files_per_track(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Album"
            track = output / "01"
            track.mkdir(parents=True)
            (track / "mediainfo.txt").write_text("media\n", encoding="utf-8")
            (track / "频谱图.png").write_bytes(b"png")

            files = validate_audio_directory_output(output)

            self.assertEqual(len(files), 2)
            (track / "频谱图.png").unlink()
            with self.assertRaises(ArtifactError):
                validate_audio_directory_output(output)


class PackageTests(unittest.TestCase):
    def test_zip_and_tar_packages_include_top_level_output_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "generated" / "Movie"
            output.mkdir(parents=True)
            (output / "mediainfo.txt").write_text("media\n", encoding="utf-8")
            destination = root / "packages"

            zip_path = package_output(output, destination)
            tar_path = package_output(output, destination, prefer_zip=False)

            with zipfile.ZipFile(zip_path) as handle:
                self.assertEqual(handle.namelist(), ["Movie/mediainfo.txt"])
            with tarfile.open(tar_path, "r:gz") as handle:
                self.assertIn("Movie/mediainfo.txt", handle.getnames())

    def test_existing_zip_and_tar_are_preserved_with_unique_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "generated" / "Movie"
            output.mkdir(parents=True)
            artifact = output / "mediainfo.txt"
            destination = root / "packages"

            for prefer_zip, expected_names in (
                (True, ("Movie.zip", "Movie_2.zip")),
                (False, ("Movie.tar.gz", "Movie_2.tar.gz")),
            ):
                with self.subTest(prefer_zip=prefer_zip):
                    artifact.write_text("first\n", encoding="utf-8")
                    first = package_output(output, destination, prefer_zip=prefer_zip)
                    artifact.write_text("second\n", encoding="utf-8")
                    second = package_output(output, destination, prefer_zip=prefer_zip)

                    self.assertEqual((first.name, second.name), expected_names)
                    self.assertNotEqual(first, second)
                    if prefer_zip:
                        with zipfile.ZipFile(first) as handle:
                            first_payload = handle.read("Movie/mediainfo.txt")
                        with zipfile.ZipFile(second) as handle:
                            second_payload = handle.read("Movie/mediainfo.txt")
                    else:
                        with tarfile.open(first, "r:gz") as handle:
                            first_payload = handle.extractfile("Movie/mediainfo.txt").read()  # type: ignore[union-attr]
                        with tarfile.open(second, "r:gz") as handle:
                            second_payload = handle.extractfile("Movie/mediainfo.txt").read()  # type: ignore[union-attr]
                    self.assertEqual(first_payload, b"first\n")
                    self.assertEqual(second_payload, b"second\n")

    def test_concurrent_packages_publish_distinct_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "generated" / "Movie"
            output.mkdir(parents=True)
            (output / "mediainfo.txt").write_text("media\n", encoding="utf-8")
            destination = root / "packages"

            with ThreadPoolExecutor(max_workers=8) as executor:
                archives = list(executor.map(lambda _: package_output(output, destination), range(8)))

            self.assertEqual(len(set(archives)), 8)
            self.assertEqual(len(list(destination.glob("Movie*.zip"))), 8)
            for archive in archives:
                with zipfile.ZipFile(archive) as handle:
                    self.assertEqual(handle.read("Movie/mediainfo.txt"), b"media\n")

    def test_package_falls_back_when_destination_does_not_support_hardlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "generated" / "Movie"
            output.mkdir(parents=True)
            artifact = output / "mediainfo.txt"
            artifact.write_text("first\n", encoding="utf-8")
            destination = root / "packages"
            existing = package_output(output, destination)
            artifact.write_text("second\n", encoding="utf-8")

            with mock.patch("ptbd_core.artifacts.os.link", side_effect=OSError(errno.EOPNOTSUPP, "no hardlinks")):
                fallback = package_output(output, destination)

            self.assertEqual(existing.name, "Movie.zip")
            self.assertEqual(fallback.name, "Movie_2.zip")
            with zipfile.ZipFile(existing) as handle:
                self.assertEqual(handle.read("Movie/mediainfo.txt"), b"first\n")
            with zipfile.ZipFile(fallback) as handle:
                self.assertEqual(handle.read("Movie/mediainfo.txt"), b"second\n")

    def test_cleanup_only_removes_generated_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mkv"
            output = root / "generated"
            source.write_bytes(b"source")
            output.mkdir()
            (output / "result.txt").write_text("result\n", encoding="utf-8")

            self.assertTrue(cleanup_output(output))
            self.assertFalse(output.exists())
            self.assertTrue(source.exists())
            self.assertFalse(cleanup_output(output))

    def test_cleanup_rejects_symlinked_parent_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            victim = root / "victim"
            output = victim / "generated"
            output.mkdir(parents=True)
            marker = output / "keep.txt"
            marker.write_text("keep\n", encoding="utf-8")
            alias = root / "alias"
            alias.symlink_to(victim, target_is_directory=True)

            with self.assertRaises(ArtifactError):
                cleanup_output(alias / "generated")

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")


if __name__ == "__main__":
    unittest.main()
