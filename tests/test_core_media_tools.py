from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable, Sequence

from ptbd_core.media_tools import (
    CommandError,
    CommandResult,
    MediaToolError,
    MediaTools,
    SubprocessCommandRunner,
    bdinfo_raw_report_valid,
    bdinfo_report_valid,
    pick_disc_probe_video,
    screenshot_candidate_count,
)


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


class FakeRunner:
    def __init__(
        self,
        handler: Callable[[tuple[str, ...]], CommandResult],
        available: set[str] | None = None,
    ) -> None:
        self.handler = handler
        self.available_commands = available or {"ffmpeg", "ffprobe", "mediainfo", "BDInfo"}
        self.calls: list[tuple[str, ...]] = []

    def available(self, command: str) -> bool:
        return command in self.available_commands

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
        result = self.handler(command)
        if check and result.returncode != 0:
            raise CommandError(result)
        return result


class TimeoutRunner(FakeRunner):
    def run(
        self,
        args: Sequence[str | Path],
        *,
        check: bool = True,
        cwd: Path | None = None,
        timeout: float | None = None,
        input_text: str | None = None,
    ) -> CommandResult:
        del check, cwd, timeout, input_text
        command = tuple(str(arg) for arg in args)
        self.calls.append(command)
        raise CommandError(CommandResult(command, 124, stderr="command timed out"))


def successful_media_handler(args: tuple[str, ...]) -> CommandResult:
    if args[0] == "ffprobe":
        return CommandResult(args, 0, stdout="100.25\n")
    if args[0] == "ffmpeg" and "signalstats,metadata=print:file=-" in args:
        stats = "\n".join(
            (
                "lavfi.signalstats.YAVG=100",
                "lavfi.signalstats.YMIN=10",
                "lavfi.signalstats.YMAX=220",
            )
        )
        return CommandResult(args, 0, stdout=stats)
    if args[0] == "ffmpeg":
        Path(args[-1]).write_bytes(b"generated-image")
        return CommandResult(args, 0)
    if args[0] == "mediainfo":
        return CommandResult(args, 0, stdout=f"General\nComplete name: {args[-1]}\n")
    if args[0] == "BDInfo":
        return CommandResult(args, 0, stdout=VALID_BDINFO)
    if args[0] == sys.executable:
        output = Path(args[args.index("--output") + 1])
        output.write_bytes(b"combined-spectrum")
        return CommandResult(args, 0)
    return CommandResult(args, 127, stderr="unexpected command")


class CommandRunnerTests(unittest.TestCase):
    def test_subprocess_runner_captures_output(self) -> None:
        result = SubprocessCommandRunner().run(
            [sys.executable, "-c", "print('runner-ok')"]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "runner-ok")


class MediaToolsTests(unittest.TestCase):
    def test_candidate_count_matches_shell_bounds(self) -> None:
        self.assertEqual(screenshot_candidate_count(1), 6)
        self.assertEqual(screenshot_candidate_count(18), 18)
        self.assertEqual(screenshot_candidate_count(99), 48)

    def test_quality_screenshots_create_exactly_six_images(self) -> None:
        runner = FakeRunner(successful_media_handler)
        tools = MediaTools(runner)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "movie.mkv"
            source.write_bytes(b"video")
            images = tools.make_quality_screenshots(source, root / "output", 6)

            self.assertEqual([image.name for image in images], [f"{i}.png" for i in range(1, 7)])
            self.assertTrue(all(image.read_bytes() == b"generated-image" for image in images))
            extraction_calls = [
                args
                for args in runner.calls
                if args[0] == "ffmpeg" and "-ss" in args
            ]
            self.assertEqual(len(extraction_calls), 6)
            self.assertEqual(extraction_calls[0][extraction_calls[0].index("-ss") + 1], "8")
            self.assertEqual(extraction_calls[-1][extraction_calls[-1].index("-ss") + 1], "92")

    def test_mediainfo_and_audio_spectrum_commands_are_external(self) -> None:
        runner = FakeRunner(successful_media_handler)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spectrum_script = root / "audio-spectrum.py"
            spectrum_script.write_text("# test helper\n", encoding="utf-8")
            tools = MediaTools(runner, spectrum_script=spectrum_script)
            first = root / "01.flac"
            second = root / "02.flac"
            first.write_bytes(b"audio")
            second.write_bytes(b"audio")

            report = tools.write_audio_mediainfo_report((first, second), root / "mediainfo.txt")
            single = tools.make_audio_spectrum(first, root / "single.png", seconds=90)
            combined = tools.make_combined_audio_spectrum(
                (first, second), root / "combined.png", seconds_per_track=12
            )

            self.assertIn("===== 01.flac =====", report.read_text(encoding="utf-8"))
            self.assertTrue(single.is_file())
            self.assertTrue(combined.is_file())
            spectrum_call = next(args for args in runner.calls if args[0] == sys.executable)
            self.assertEqual(spectrum_call[spectrum_call.index("--seconds") + 1], "12")
            self.assertEqual(spectrum_call[-2:], (str(first), str(second)))

    def test_audio_spectrum_honors_sox_backend(self) -> None:
        def handler(args: tuple[str, ...]) -> CommandResult:
            if args[0] == "sox":
                Path(args[-1]).write_bytes(b"sox-spectrum")
                return CommandResult(args, 0)
            return successful_media_handler(args)

        runner = FakeRunner(handler, available={"sox", "ffmpeg"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "song.flac"
            audio.write_bytes(b"audio")
            output = MediaTools(runner).make_audio_spectrum(
                audio,
                root / "spectrum.png",
                seconds=90,
                backend="sox",
            )

            self.assertEqual(output.read_bytes(), b"sox-spectrum")
            self.assertEqual(runner.calls[0][0], "sox")
            self.assertIn("1:30", runner.calls[0])

    def test_requested_sox_backend_must_exist(self) -> None:
        runner = FakeRunner(successful_media_handler, available={"ffmpeg"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "song.flac"
            audio.write_bytes(b"audio")
            with self.assertRaises(MediaToolError):
                MediaTools(runner).make_audio_spectrum(
                    audio,
                    root / "spectrum.png",
                    backend="sox_ng",
                )

    @unittest.skipUnless(sys.platform != "win32", "PTY is POSIX-only")
    def test_bdinfo_pty_answers_playlist_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "fake-bdinfo.py"
            script.write_text(
                "import sys\n"
                "print('Select (q when finished):', flush=True)\n"
                "first = input()\n"
                "second = input()\n"
                "print('selected=' + first + ',' + second, flush=True)\n",
                encoding="utf-8",
            )
            result = MediaTools._run_bdinfo_pty([sys.executable, script], timeout=5)

            self.assertEqual(result.returncode, 0)
            self.assertIn("selected=1,q", result.stdout.replace("\r", ""))

    def test_bdinfo_uses_valid_command_output(self) -> None:
        runner = FakeRunner(successful_media_handler)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            iso = root / "disc.iso"
            iso.write_bytes(b"iso")
            report = MediaTools(runner).generate_bdinfo_report(iso, root / "output")

            self.assertTrue(bdinfo_report_valid(report))
            self.assertEqual(report.read_text(encoding="utf-8"), VALID_BDINFO)

    def test_bdinfo_missing_command_creates_valid_fallback(self) -> None:
        runner = FakeRunner(successful_media_handler, available={"ffmpeg", "ffprobe", "mediainfo"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            iso = root / "disc.iso"
            iso.write_bytes(b"iso")
            report = MediaTools(runner).generate_bdinfo_report(iso, root / "output")

            text = report.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("BDInfo: fallback-report"))
            self.assertTrue(bdinfo_raw_report_valid(text))

    def test_bdinfo_timeout_creates_valid_fallback(self) -> None:
        runner = TimeoutRunner(successful_media_handler)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            iso = root / "disc.iso"
            iso.write_bytes(b"iso")

            report = MediaTools(runner).generate_bdinfo_report(iso, root / "output")

            text = report.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("BDInfo: fallback-report"))
            self.assertIn("BDInfo 执行失败或崩溃", text)
            self.assertTrue(bdinfo_raw_report_valid(text))

    def test_disc_probe_uses_largest_m2ts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Movie"
            stream = root / "BDMV" / "STREAM"
            stream.mkdir(parents=True)
            small = stream / "00001.m2ts"
            large = stream / "00002.m2ts"
            small.write_bytes(b"1")
            large.write_bytes(b"12345")

            self.assertEqual(pick_disc_probe_video(root), large)


if __name__ == "__main__":
    unittest.main()
