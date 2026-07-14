from __future__ import annotations

import base64
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, Sequence


PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII="
)
BDINFO_REQUIRED_SECTIONS = (
    "DISC INFO:",
    "PLAYLIST REPORT:",
    "VIDEO:",
    "AUDIO:",
    "FILES:",
)
_BDINFO_HEADER_RE = re.compile(
    r"^(?:[A-Z][A-Z0-9 _/-]+:\s*|BDInfo:\s.*|扫描(?:文件|时间):\s.*)$"
)
FFPROBE_TIMEOUT = 60
FRAME_TIMEOUT = 60
MEDIAINFO_TIMEOUT = 300
FFMPEG_TIMEOUT = 600
COMBINED_SPECTRUM_TIMEOUT = 1800
BDINFO_TIMEOUT = 1800


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class MediaToolError(RuntimeError):
    pass


class CommandError(MediaToolError):
    def __init__(self, result: CommandResult):
        self.result = result
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        super().__init__(
            f"command failed with exit code {result.returncode}: "
            f"{' '.join(result.args)}: {detail}"
        )


class CommandRunner(Protocol):
    def available(self, command: str) -> bool:
        ...

    def run(
        self,
        args: Sequence[str | Path],
        *,
        check: bool = True,
        cwd: Path | None = None,
        timeout: float | None = None,
        input_text: str | None = None,
    ) -> CommandResult:
        ...


class SubprocessCommandRunner:
    def available(self, command: str) -> bool:
        return shutil.which(command) is not None

    def run(
        self,
        args: Sequence[str | Path],
        *,
        check: bool = True,
        cwd: Path | None = None,
        timeout: float | None = None,
        input_text: str | None = None,
    ) -> CommandResult:
        command = tuple(str(arg) for arg in args)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                input=input_text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            detail = f"command timed out after {timeout} seconds"
            result = CommandResult(command, 124, stdout=stdout, stderr=f"{stderr}\n{detail}".strip())
            raise CommandError(result) from exc
        result = CommandResult(
            args=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if check and result.returncode != 0:
            raise CommandError(result)
        return result


def _normalise_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _write_nonempty_text(path: Path, value: str, label: str) -> Path:
    text = _normalise_text(value)
    if not text.strip():
        raise MediaToolError(f"{label} returned an empty report")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    return path


def _nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def screenshot_candidate_count(value: int) -> int:
    return max(6, min(48, value if value > 0 else 18))


def ensure_six_images(directory: Path) -> tuple[Path, ...]:
    directory.mkdir(parents=True, exist_ok=True)
    images = tuple(directory / f"{index}.png" for index in range(1, 7))
    source = next((image for image in images if _nonempty_file(image)), None)
    for image in images:
        if _nonempty_file(image):
            continue
        if source is None:
            image.write_bytes(PLACEHOLDER_PNG)
            source = image
        else:
            shutil.copyfile(source, image)
    return images


def pick_disc_probe_video(source: Path) -> Path | None:
    if source.is_file():
        return source
    if (source / "BDMV" / "STREAM").is_dir():
        stream_dir = source / "BDMV" / "STREAM"
    elif (source / "STREAM").is_dir():
        stream_dir = source / "STREAM"
    else:
        return None

    candidates: list[tuple[int, str, Path]] = []
    for candidate in stream_dir.rglob("*"):
        if not candidate.is_file() or candidate.suffix.lower() != ".m2ts":
            continue
        try:
            candidates.append((candidate.stat().st_size, str(candidate), candidate))
        except OSError:
            continue
    if not candidates:
        return None
    return max(candidates)[2]


def _section_lines(lines: list[str], section: str) -> list[str] | None:
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == section:
            start = index + 1
            break
    if start is None:
        return None

    result: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if _BDINFO_HEADER_RE.match(stripped):
            break
        if stripped and not re.fullmatch(r"-+", stripped):
            result.append(stripped)
    return result


def bdinfo_raw_report_valid(value: str) -> bool:
    lines = _normalise_text(value).splitlines()
    if len(lines) < 20:
        return False
    fallback_report = any(line.strip() == "BDInfo: fallback-report" for line in lines[:4])
    for section in BDINFO_REQUIRED_SECTIONS:
        content = _section_lines(lines, section)
        if not content:
            return False
        if section in {"VIDEO:", "AUDIO:"} and not any(
            re.search(r"[A-Za-z0-9]", line) for line in content
        ):
            return False
        if section == "FILES:":
            has_disc_file = any(
                re.search(r"\.m2ts(?:$|\s)", line, re.IGNORECASE) for line in content
            )
            has_fallback_iso = fallback_report and any(
                re.search(r"\.iso(?:$|\s)", line, re.IGNORECASE) for line in content
            )
            if not has_disc_file and not has_fallback_iso:
                return False
    if any(line.strip() == "SUBTITLES:" for line in lines):
        if not _section_lines(lines, "SUBTITLES:"):
            return False
    return True


def bdinfo_report_valid(path: Path) -> bool:
    if not _nonempty_file(path):
        return False
    try:
        return bdinfo_raw_report_valid(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False


def write_bdinfo_fallback_report(
    scan_target: Path,
    output: Path,
    reason: str,
    probe_video: Path | None = None,
) -> Path:
    probe_label = str(probe_video) if probe_video else "未找到可用主影片"
    if probe_video:
        file_entry = str(probe_video)
    elif scan_target.suffix.lower() == ".iso":
        file_entry = str(scan_target)
    else:
        file_entry = str(scan_target / "BDMV" / "STREAM" / "unknown.m2ts")
    scan_time = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    report = f"""BDInfo: fallback-report
扫描文件: {scan_target}
扫描时间: {scan_time}
DISC INFO:
  原盘扫描已进入降级模式。
  原因: {reason}
  输入: {scan_target}
  说明: 当前环境中的 BDInfo 未返回可归档的完整原始报告。
  影响: 结果包保留截图与说明文本，但不含精确盘结构明细。
PLAYLIST REPORT:
  BDInfo 未能产出完整原始报告。
  建议: 可在其他机器上重试，或直接选择主影片文件处理。
  当前截图来源: {probe_label}
  回退策略: 已优先选择检测到的主影片作为截图来源。
  手动处理: 如需精确播放列表，请在支持的环境中重新扫描原盘。
VIDEO:
  Fallback stream: {probe_label}
  Screenshots generated from the detected main feature when available.
  视频章节信息: 不可用（依赖 BDInfo 原始输出）。
  编码明细: 请参考截图来源文件或重新运行 BDInfo。
AUDIO:
  音轨信息不可用：BDInfo 本次执行失败。
  如需精确信息，请在 BDInfo 可运行环境中重试。
  当前结果包仍可用于交付截图与基础说明。
  若只关心主影片，也可以直接选择主片文件单独处理。
SUBTITLES:
  字幕轨信息不可用：BDInfo 本次执行失败。
  这不影响本次结果包导出。
  如需字幕轨明细，请在可稳定运行 BDInfo 的环境中重试。
  当前文件仅保留失败说明，不伪造字幕数据。
FILES:
  {file_entry}
  Source root: {scan_target}
  Probe source: {probe_label}
  Generated by PT-BDtool fallback mode.
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    return output


class MediaTools:
    def __init__(
        self,
        runner: CommandRunner | None = None,
        spectrum_script: Path | None = None,
    ) -> None:
        self.runner = runner or SubprocessCommandRunner()
        self.spectrum_script = spectrum_script or (
            Path(__file__).resolve().parent.parent / "scripts" / "audio-spectrum.py"
        )

    def _require(self, command: str) -> None:
        if not self.runner.available(command):
            raise MediaToolError(f"required command is unavailable: {command}")

    def write_mediainfo(self, source: Path, output: Path) -> Path:
        self._require("mediainfo")
        try:
            result = self.runner.run(["mediainfo", source], timeout=MEDIAINFO_TIMEOUT)
        except CommandError as exc:
            raise MediaToolError(f"mediainfo failed for {source}: {exc}") from exc
        return _write_nonempty_text(output, result.stdout, "mediainfo")

    def write_audio_mediainfo_report(
        self,
        audio_files: Sequence[Path],
        output: Path,
    ) -> Path:
        if not audio_files:
            raise MediaToolError("cannot create MediaInfo report without audio files")
        self._require("mediainfo")
        parts: list[str] = []
        for audio in audio_files:
            try:
                result = self.runner.run(["mediainfo", audio], timeout=MEDIAINFO_TIMEOUT)
            except CommandError as exc:
                raise MediaToolError(f"mediainfo failed for {audio}: {exc}") from exc
            if not result.stdout.strip():
                raise MediaToolError(f"mediainfo returned an empty report for {audio}")
            parts.append(f"===== {audio.name} =====\n{_normalise_text(result.stdout).rstrip()}\n")
        return _write_nonempty_text(output, "\n".join(parts), "mediainfo")

    def probe_duration(self, source: Path) -> float:
        self._require("ffprobe")
        result = self.runner.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                source,
            ],
            check=False,
            timeout=FFPROBE_TIMEOUT,
        )
        try:
            duration = float(result.stdout.strip().splitlines()[0])
        except (ValueError, IndexError):
            return 60.0
        return duration if math.isfinite(duration) and duration >= 0 else 60.0

    def _frame_usable(self, image: Path) -> bool:
        result = self.runner.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                image,
                "-vf",
                "signalstats,metadata=print:file=-",
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            check=False,
            timeout=FRAME_TIMEOUT,
        )
        stats = result.stdout + "\n" + result.stderr
        values: dict[str, int] = {}
        for key in ("YAVG", "YMIN", "YMAX"):
            match = re.search(rf"lavfi\.signalstats\.{key}=([-+]?\d+(?:\.\d+)?)", stats)
            if not match:
                return False
            values[key] = int(float(match.group(1)))
        return (
            18 <= values["YAVG"] <= 238
            and values["YMAX"] - values["YMIN"] >= 20
        )

    def make_quality_screenshots(
        self,
        source: Path,
        output_dir: Path,
        candidate_count: int = 18,
    ) -> tuple[Path, ...]:
        self._require("ffmpeg")
        self._require("ffprobe")
        output_dir.mkdir(parents=True, exist_ok=True)
        count = screenshot_candidate_count(candidate_count)
        duration = int(self.probe_duration(source))
        valid: list[Path] = []
        fallback: list[Path] = []

        with tempfile.TemporaryDirectory(prefix="ptbd-shots-") as temporary:
            temp_dir = Path(temporary)
            for index in range(1, count + 1):
                if duration < 2:
                    second = 0
                else:
                    permille = 80 + (index - 1) * 840 // (count - 1)
                    second = max(1, duration * permille // 1000)
                    second = min(second, duration - 1)
                candidate = temp_dir / f"{index}.png"
                result = self.runner.run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        str(second),
                        "-i",
                        source,
                        "-frames:v",
                        "1",
                        "-y",
                        candidate,
                    ],
                    check=False,
                    timeout=FFMPEG_TIMEOUT,
                )
                if result.returncode != 0 or not _nonempty_file(candidate):
                    continue
                fallback.append(candidate)
                if self._frame_usable(candidate):
                    valid.append(candidate)

            if valid:
                selected = [valid[(len(valid) - 1) * index // 5] for index in range(6)]
            else:
                selected = fallback[:6]
            if not selected:
                raise MediaToolError(f"ffmpeg produced no screenshots for {source}")

            for index in range(1, 7):
                (output_dir / f"{index}.png").unlink(missing_ok=True)
            for index, candidate in enumerate(selected, start=1):
                shutil.copyfile(candidate, output_dir / f"{index}.png")
            return ensure_six_images(output_dir)

    def make_audio_spectrum(
        self,
        audio: Path,
        output: Path,
        *,
        size: str = "1280x720",
        seconds: int = 90,
        backend: str = "auto",
    ) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        requested = backend.strip().lower() or "auto"
        if requested not in {"auto", "sox", "sox_ng", "ffmpeg"}:
            requested = "ffmpeg"
        sox_command = next(
            (
                command
                for command in (("sox_ng", "sox") if requested == "auto" else (requested,))
                if command in {"sox", "sox_ng"} and self.runner.available(command)
            ),
            None,
        )
        if requested in {"sox", "sox_ng"} and sox_command is None:
            raise MediaToolError(f"requested audio spectrum backend is unavailable: {requested}")
        if sox_command is not None:
            width, height = size.lower().split("x", 1)
            duration_args: list[str] = []
            if seconds > 0:
                hours, remainder = divmod(seconds, 3600)
                minutes, remaining_seconds = divmod(remainder, 60)
                duration = (
                    f"{hours}:{minutes:02d}:{remaining_seconds:02d}"
                    if hours
                    else f"{minutes}:{remaining_seconds:02d}"
                )
                duration_args = ["-S", "0", "-d", duration]
            command: list[str | Path] = [
                sox_command,
                audio,
                "-n",
                "remix",
                "1",
                "spectrogram",
                "-x",
                width,
                "-y",
                height,
                "-z",
                "120",
                "-w",
                "Kaiser",
                *duration_args,
                "-t",
                audio.name,
                "-c",
                "PT-BDtool",
                "-o",
                output,
            ]
            try:
                self.runner.run(command)
                if _nonempty_file(output):
                    return output
            except CommandError as exc:
                if requested != "auto":
                    raise MediaToolError(f"audio spectrum generation failed for {audio}: {exc}") from exc
            if requested != "auto":
                raise MediaToolError(f"SoX did not create an audio spectrum for {audio}")

        self._require("ffmpeg")
        tail = f"showspectrumpic=s={size}:legend=disabled"
        if seconds > 0:
            tail = f"atrim=end={seconds},{tail}"
        try:
            self.runner.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    audio,
                    "-filter_complex",
                    f"[0:a]aformat=channel_layouts=mono,{tail}",
                    "-frames:v",
                    "1",
                    output,
                ],
                timeout=FFMPEG_TIMEOUT,
            )
        except CommandError as exc:
            raise MediaToolError(f"audio spectrum generation failed for {audio}: {exc}") from exc
        if not _nonempty_file(output):
            raise MediaToolError(f"ffmpeg did not create an audio spectrum for {audio}")
        return output

    @staticmethod
    def _run_bdinfo_pty(args: Sequence[str | Path], timeout: float = 1800) -> CommandResult:
        if os.name != "posix":
            raise OSError("PTY execution is only available on POSIX")
        import pty
        import select
        import time

        command = tuple(str(arg) for arg in args)
        pid, master = pty.fork()
        if pid == 0:
            os.execvp(command[0], command)

        output = bytearray()
        prompt_buffer = ""
        sent_choice = False
        started = time.monotonic()
        status = 1 << 8
        try:
            while True:
                if time.monotonic() - started > timeout:
                    os.kill(pid, 9)
                    os.waitpid(pid, 0)
                    raise subprocess.TimeoutExpired(command, timeout)
                readable, _, _ = select.select([master], [], [], 0.25)
                if master in readable:
                    try:
                        chunk = os.read(master, 4096)
                    except OSError:
                        chunk = b""
                    if chunk:
                        output.extend(chunk)
                        prompt_buffer = (prompt_buffer + chunk.decode("utf-8", errors="ignore"))[-16384:]
                        if not sent_choice and "Select (q when finished):" in prompt_buffer:
                            os.write(master, b"1\n")
                            time.sleep(0.15)
                            os.write(master, b"q\n")
                            sent_choice = True
                waited_pid, status = os.waitpid(pid, os.WNOHANG)
                if waited_pid == pid:
                    break
            returncode = os.waitstatus_to_exitcode(status)
        finally:
            try:
                os.close(master)
            except OSError:
                pass
        return CommandResult(command, returncode, output.decode("utf-8", errors="replace"), "")

    def make_combined_audio_spectrum(
        self,
        audio_files: Sequence[Path],
        output: Path,
        *,
        size: str = "1280x720",
        seconds_per_track: int = 12,
    ) -> Path:
        if not audio_files:
            raise MediaToolError("cannot create combined spectrum without audio files")
        self._require("ffmpeg")
        if not self.spectrum_script.is_file():
            raise MediaToolError(f"audio spectrum helper is missing: {self.spectrum_script}")
        output.parent.mkdir(parents=True, exist_ok=True)
        command: list[str | Path] = [
            sys.executable,
            self.spectrum_script,
            "--output",
            output,
            "--size",
            size,
            "--seconds",
            str(seconds_per_track),
            *audio_files,
        ]
        try:
            self.runner.run(command, timeout=COMBINED_SPECTRUM_TIMEOUT)
        except CommandError as exc:
            raise MediaToolError(f"combined audio spectrum generation failed: {exc}") from exc
        if not _nonempty_file(output):
            raise MediaToolError("audio spectrum helper did not create an output image")
        return output

    def generate_bdinfo_report(self, source: Path, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "BDInfo.txt"
        output.unlink(missing_ok=True)
        probe_video = pick_disc_probe_video(source)
        attempts: list[CommandResult] = []

        if self.runner.available("BDInfo"):
            if isinstance(self.runner, SubprocessCommandRunner) and os.name == "posix":
                try:
                    interactive = self._run_bdinfo_pty(["BDInfo", source, output_dir])
                except (OSError, subprocess.TimeoutExpired):
                    interactive = None
                if interactive is not None:
                    attempts.append(interactive)
                    if interactive.returncode == 0 and bdinfo_raw_report_valid(interactive.stdout):
                        _write_nonempty_text(output, interactive.stdout, "BDInfo")
                        return output
            for command in (["BDInfo", source, output_dir], ["BDInfo", "-w", source, output_dir]):
                try:
                    result = self.runner.run(command, check=False, timeout=BDINFO_TIMEOUT)
                except CommandError as exc:
                    attempts.append(exc.result)
                    continue
                except (OSError, subprocess.TimeoutExpired):
                    continue
                attempts.append(result)
                if result.returncode == 0:
                    break

            for result in attempts:
                if result.returncode == 0 and bdinfo_raw_report_valid(result.stdout):
                    _write_nonempty_text(output, result.stdout, "BDInfo")
                    return output

            if bdinfo_report_valid(output):
                return output

            reports = sorted(
                (
                    candidate
                    for candidate in output_dir.glob("*.txt")
                    if candidate != output and bdinfo_report_valid(candidate)
                ),
                key=lambda candidate: candidate.stat().st_mtime,
                reverse=True,
            )
            if reports:
                text = reports[0].read_text(encoding="utf-8", errors="replace")
                _write_nonempty_text(output, text, "BDInfo")
                return output

            reason = (
                "BDInfo 输出无效：缺少完整区块"
                if any(result.returncode == 0 for result in attempts)
                else "BDInfo 执行失败或崩溃"
            )
        else:
            reason = "缺少 BDInfo 命令"

        return write_bdinfo_fallback_report(source, output, reason, probe_video)

    def make_disc_screenshots(
        self,
        source: Path,
        output_dir: Path,
        candidate_count: int = 18,
    ) -> tuple[Path, ...]:
        probe_video = pick_disc_probe_video(source)
        if (
            probe_video is not None
            and self.runner.available("ffmpeg")
            and self.runner.available("ffprobe")
        ):
            try:
                self.make_quality_screenshots(probe_video, output_dir, candidate_count)
            except MediaToolError:
                pass
        return ensure_six_images(output_dir)


__all__ = [
    "CommandError",
    "CommandResult",
    "CommandRunner",
    "MediaToolError",
    "MediaTools",
    "SubprocessCommandRunner",
    "bdinfo_raw_report_valid",
    "bdinfo_report_valid",
    "ensure_six_images",
    "pick_disc_probe_video",
    "screenshot_candidate_count",
    "write_bdinfo_fallback_report",
]
