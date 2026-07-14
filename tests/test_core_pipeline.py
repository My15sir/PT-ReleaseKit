from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Sequence

from ptbd_core.media_tools import CommandError, CommandResult
from ptbd_core.pipeline import MediaPipeline, PipelineError, ProcessingOptions


VALID_BDINFO = """DISC INFO:
Disc Title: Example
Disc Label: EXAMPLE
Disc Size: 1 bytes
Protection: AACS
Extras: none
PLAYLIST REPORT:
Name: 00001.MPLS
Length: 01:30:00
Size: 1 bytes
Total Bitrate: 10 Mbps
Chapters: 10
VIDEO:
MPEG-4 AVC Video / 1080p
Codec: AVC
Bitrate: 8 Mbps
AUDIO:
English / DTS-HD Master Audio
Codec: DTS-HD MA
Channels: 5.1
SUBTITLES:
English / PGS
FILES:
00001.m2ts  1 bytes
00002.m2ts  1 bytes
End of report
"""


class PipelineRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def available(self, command: str) -> bool:
        return command in {"ffmpeg", "ffprobe", "mediainfo", "BDInfo"}

    def run(
        self,
        args: Sequence[str | Path],
        *,
        check: bool = True,
        cwd: Path | None = None,
        timeout: float | None = None,
        input_text: str | None = None,
    ) -> CommandResult:
        del cwd, timeout, input_text
        command = tuple(str(arg) for arg in args)
        self.calls.append(command)
        if command[0] == "mediainfo":
            result = CommandResult(command, 0, stdout=f"MediaInfo for {command[-1]}\n")
        elif command[0] == "ffprobe":
            result = CommandResult(command, 0, stdout="120.0\n")
        elif command[0] == "ffmpeg" and "signalstats,metadata=print:file=-" in command:
            result = CommandResult(
                command,
                0,
                stdout="lavfi.signalstats.YAVG=100\nlavfi.signalstats.YMIN=10\nlavfi.signalstats.YMAX=220\n",
            )
        elif command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"image")
            result = CommandResult(command, 0)
        elif command[0] == "BDInfo":
            result = CommandResult(command, 0, stdout=VALID_BDINFO)
        elif command[0] == sys.executable:
            output = Path(command[command.index("--output") + 1])
            output.write_bytes(b"combined")
            result = CommandResult(command, 0)
        else:
            result = CommandResult(command, 127, stderr="unexpected command")
        if check and result.returncode != 0:
            raise CommandError(result)
        return result


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.spectrum_script = self.root / "audio-spectrum.py"
        self.spectrum_script.write_text("# injected helper\n", encoding="utf-8")
        self.runner = PipelineRunner()
        self.pipeline = MediaPipeline(self.runner, spectrum_script=self.spectrum_script)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_video_generates_six_images_and_mediainfo(self) -> None:
        source = self.root / "downloads" / "Movie" / "movie.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video")

        result = self.pipeline.process(source, "VIDEO")

        self.assertEqual(result.output_dir, self.root / "downloads" / "信息" / "Movie")
        self.assertEqual({path.name for path in result.files}, {"mediainfo.txt", *(f"{i}.png" for i in range(1, 7))})
        self.assertTrue(source.exists())

    def test_video_dry_mode_writes_compatibility_readme(self) -> None:
        source = self.root / "Movie" / "movie.mkv"
        source.parent.mkdir()
        source.write_bytes(b"video")
        options = ProcessingOptions(media_info=False, screenshots=False)

        result = self.pipeline.process(source, "VIDEO", options)

        self.assertEqual([path.name for path in result.files], ["README.txt"])
        self.assertIn("这是预期行为", result.files[0].read_text(encoding="utf-8"))

    def test_workspace_keeps_generated_files_away_from_source(self) -> None:
        source = self.root / "readonly-media" / "Movie" / "movie.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video")
        workspace = self.root / "runtime" / "job-1"

        result = self.pipeline.process(
            source,
            "VIDEO",
            ProcessingOptions(workspace_dir=workspace),
        )

        self.assertEqual(result.output_dir, workspace / "信息" / "Movie")
        self.assertFalse((source.parent.parent / "信息").exists())
        archive = self.pipeline.package(result, self.root / "packages")
        self.assertEqual(archive.name, "Movie.zip")

    def test_audio_generates_mediainfo_and_spectrum(self) -> None:
        source = self.root / "music" / "Album" / "song.flac"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"audio")

        result = self.pipeline.process(source, "audio")

        self.assertEqual({path.name for path in result.files}, {"mediainfo.txt", "频谱图.png"})

    def test_audio_directory_supports_single_and_combined_layouts(self) -> None:
        source = self.root / "music" / "Album"
        source.mkdir(parents=True)
        (source / "01 - Intro.flac").write_bytes(b"audio")
        (source / "02 - Main.flac").write_bytes(b"audio")

        single = self.pipeline.process(source, "AUDIO_DIR")
        single_dirs = {path.name for path in single.output_dir.iterdir()}
        self.assertEqual(single_dirs, {"01_-_Intro", "02_-_Main"})
        self.assertEqual(len(single.files), 4)

        combined = self.pipeline.process(
            source,
            "AUDIO_DIR",
            ProcessingOptions(audio_spectrum_mode="combined"),
        )
        self.assertEqual({path.name for path in combined.files}, {"mediainfo.txt", "频谱图.png"})
        helper_call = next(call for call in self.runner.calls if call[0] == sys.executable)
        self.assertEqual(helper_call[helper_call.index("--seconds") + 1], "12")

    def test_bdmv_and_iso_generate_bdinfo_and_six_images(self) -> None:
        disc = self.root / "discs" / "MovieDisc"
        stream = disc / "BDMV" / "STREAM"
        stream.mkdir(parents=True)
        (disc / "BDMV" / "PLAYLIST").mkdir()
        (stream / "00001.m2ts").write_bytes(b"video")
        iso = self.root / "images" / "ImageDisc" / "disc.iso"
        iso.parent.mkdir(parents=True)
        iso.write_bytes(b"iso")

        bdmv_result = self.pipeline.process(disc / "BDMV", "BDMV")
        iso_result = self.pipeline.process(iso, "ISO")

        expected = {"BDInfo.txt", *(f"{i}.png" for i in range(1, 7))}
        self.assertEqual({path.name for path in bdmv_result.files}, expected)
        self.assertEqual({path.name for path in iso_result.files}, expected)
        self.assertEqual(bdmv_result.source, disc)

    def test_package_can_cleanup_output_without_touching_source(self) -> None:
        source = self.root / "Movie" / "movie.mkv"
        source.parent.mkdir()
        source.write_bytes(b"video")
        result = self.pipeline.process(source, "VIDEO")

        archive = self.pipeline.package(result, self.root / "packages", cleanup=True)

        self.assertTrue(archive.is_file())
        self.assertFalse(result.output_dir.exists())
        self.assertTrue(source.exists())
        with zipfile.ZipFile(archive) as handle:
            self.assertIn("Movie/mediainfo.txt", handle.namelist())

    def test_rejects_audio_directory_with_fewer_than_two_tracks(self) -> None:
        source = self.root / "Album"
        source.mkdir()
        (source / "only.flac").write_bytes(b"audio")

        with self.assertRaises(PipelineError):
            self.pipeline.process(source, "AUDIO_DIR")

    def test_rejects_output_directory_that_would_contain_source(self) -> None:
        override = self.root / "unsafe"
        source = override / "PT-BDtool" / "信息" / "movie.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video")

        with self.assertRaises(PipelineError):
            self.pipeline.process(source, "VIDEO", ProcessingOptions(output_dir=override))
        self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
