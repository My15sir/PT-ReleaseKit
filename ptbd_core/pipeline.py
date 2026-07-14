from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .artifacts import (
    DRY_RUN_TEXT,
    ArtifactError,
    cleanup_output,
    normalise_bdmv_source,
    package_output,
    prepare_output_directory,
    resolve_output_dir,
    safe_name,
    unique_directory,
    validate_audio_directory_output,
    validate_audio_output,
    validate_disc_output,
    validate_video_output,
)
from .media_tools import CommandRunner, MediaToolError, MediaTools


SUPPORTED_MEDIA_TYPES = frozenset({"VIDEO", "AUDIO", "AUDIO_DIR", "BDMV", "ISO"})


class PipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessingOptions:
    # This matches the existing CLI's --out root, not the final generated directory.
    output_dir: Path | None = None
    # A writable staging root that preserves the source-derived artifact name.
    workspace_dir: Path | None = None
    media_info: bool = True
    screenshots: bool = True
    screenshot_candidates: int = 18
    audio_spectrum_mode: str = "single"
    audio_spectrum_backend: str = "auto"
    audio_spectrum_seconds: int = 90
    combined_track_seconds: int = 12
    spectrum_size: str = "1280x720"

    def __post_init__(self) -> None:
        if self.output_dir is not None:
            object.__setattr__(self, "output_dir", Path(self.output_dir))
        if self.workspace_dir is not None:
            object.__setattr__(self, "workspace_dir", Path(self.workspace_dir))
        if self.output_dir is not None and self.workspace_dir is not None:
            raise ValueError("output_dir and workspace_dir are mutually exclusive")
        mode = self.audio_spectrum_mode.lower()
        if mode not in {"single", "combined"}:
            raise ValueError("audio_spectrum_mode must be 'single' or 'combined'")
        object.__setattr__(self, "audio_spectrum_mode", mode)
        backend = self.audio_spectrum_backend.lower()
        if backend not in {"auto", "sox", "sox_ng", "ffmpeg"}:
            raise ValueError("audio_spectrum_backend must be auto, sox, sox_ng or ffmpeg")
        object.__setattr__(self, "audio_spectrum_backend", backend)
        if self.screenshot_candidates <= 0:
            raise ValueError("screenshot_candidates must be greater than zero")
        if self.audio_spectrum_seconds < 0:
            raise ValueError("audio_spectrum_seconds must be zero or greater")
        if self.combined_track_seconds < 0:
            raise ValueError("combined_track_seconds must be zero or greater")
        match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", self.spectrum_size)
        if not match:
            raise ValueError("spectrum_size must use WIDTHxHEIGHT with positive integers")


@dataclass(frozen=True)
class ProcessingResult:
    media_type: str
    source: Path
    output_dir: Path
    files: tuple[Path, ...]
    warnings: tuple[str, ...] = ()


class MediaPipeline:
    def __init__(
        self,
        runner: CommandRunner | None = None,
        spectrum_script: Path | None = None,
    ) -> None:
        self.tools = MediaTools(runner=runner, spectrum_script=spectrum_script)

    def process(
        self,
        path: str | Path,
        media_type: str,
        options: ProcessingOptions | None = None,
    ) -> ProcessingResult:
        settings = options or ProcessingOptions()
        kind = media_type.upper()
        if kind not in SUPPORTED_MEDIA_TYPES:
            raise PipelineError(f"unsupported media type: {media_type}")

        source = Path(path).expanduser().absolute()
        if kind == "BDMV":
            source = normalise_bdmv_source(source)
        self._validate_source(kind, source)
        output = resolve_output_dir(
            kind,
            source,
            settings.output_dir,
            workspace_root=settings.workspace_dir,
        )
        source_resolved = source.resolve()
        output_resolved = output.resolve(strict=False)
        if output_resolved == source_resolved or output_resolved in source_resolved.parents:
            raise PipelineError("generated output directory cannot replace or contain the source")

        try:
            prepare_output_directory(output)
            warnings: tuple[str, ...] = ()
            if kind == "VIDEO":
                files = self._process_video(source, output, settings)
            elif kind == "AUDIO":
                files = self._process_audio(source, output, settings)
            elif kind == "AUDIO_DIR":
                files = self._process_audio_directory(source, output, settings)
            else:
                files, warnings = self._process_disc(source, output, settings)
        except (ArtifactError, MediaToolError, OSError) as exc:
            raise PipelineError(f"failed to process {kind} source {source}: {exc}") from exc

        return ProcessingResult(
            media_type=kind,
            source=source,
            output_dir=output,
            files=tuple(files),
            warnings=warnings,
        )

    def package(
        self,
        output_dir: str | Path | ProcessingResult,
        destination_dir: str | Path,
        *,
        prefer_zip: bool = True,
        cleanup: bool = False,
    ) -> Path:
        source = output_dir.output_dir if isinstance(output_dir, ProcessingResult) else output_dir
        try:
            archive = package_output(source, destination_dir, prefer_zip=prefer_zip)
            if cleanup:
                cleanup_output(source)
            return archive
        except (ArtifactError, OSError) as exc:
            raise PipelineError(f"failed to package output {source}: {exc}") from exc

    def cleanup(self, output_dir: str | Path | ProcessingResult) -> bool:
        source = output_dir.output_dir if isinstance(output_dir, ProcessingResult) else output_dir
        try:
            return cleanup_output(source)
        except (ArtifactError, OSError) as exc:
            raise PipelineError(f"failed to clean output {source}: {exc}") from exc

    @staticmethod
    def _validate_source(media_type: str, source: Path) -> None:
        if media_type in {"VIDEO", "AUDIO", "ISO"}:
            if not source.is_file():
                raise PipelineError(f"source file does not exist: {source}")
            return
        if not source.is_dir():
            raise PipelineError(f"source directory does not exist: {source}")
        if media_type == "BDMV" and not (source / "BDMV").is_dir():
            raise PipelineError(f"BDMV directory is missing below source: {source}")

    def _process_video(
        self,
        source: Path,
        output: Path,
        options: ProcessingOptions,
    ) -> tuple[Path, ...]:
        if not options.media_info and not options.screenshots:
            (output / "README.txt").write_text(DRY_RUN_TEXT, encoding="utf-8")
        else:
            if options.media_info:
                self.tools.write_mediainfo(source, output / "mediainfo.txt")
            if options.screenshots:
                self.tools.make_quality_screenshots(
                    source,
                    output,
                    options.screenshot_candidates,
                )
        return validate_video_output(
            output,
            media_info=options.media_info,
            screenshots=options.screenshots,
        )

    def _process_audio(
        self,
        source: Path,
        output: Path,
        options: ProcessingOptions,
    ) -> tuple[Path, ...]:
        self.tools.write_mediainfo(source, output / "mediainfo.txt")
        self.tools.make_audio_spectrum(
            source,
            output / "频谱图.png",
            size=options.spectrum_size,
            seconds=options.audio_spectrum_seconds,
            backend=options.audio_spectrum_backend,
        )
        return validate_audio_output(output)

    @staticmethod
    def _audio_files(source: Path) -> tuple[Path, ...]:
        extensions = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus"}
        return tuple(
            sorted(
                (
                    child
                    for child in source.iterdir()
                    if child.is_file() and child.suffix.lower() in extensions
                ),
                key=lambda path: path.name,
            )
        )

    def _process_audio_directory(
        self,
        source: Path,
        output: Path,
        options: ProcessingOptions,
    ) -> tuple[Path, ...]:
        audio_files = self._audio_files(source)
        if len(audio_files) < 2:
            raise PipelineError(f"audio directory requires at least two files: {source}")
        if options.audio_spectrum_mode == "combined":
            self.tools.write_audio_mediainfo_report(audio_files, output / "mediainfo.txt")
            self.tools.make_combined_audio_spectrum(
                audio_files,
                output / "频谱图.png",
                size=options.spectrum_size,
                seconds_per_track=options.combined_track_seconds,
            )
            return validate_audio_output(output)

        for audio in audio_files:
            track_output = unique_directory(output, safe_name(audio.stem))
            track_output.mkdir(parents=True)
            self.tools.write_audio_mediainfo_report((audio,), track_output / "mediainfo.txt")
            self.tools.make_audio_spectrum(
                audio,
                track_output / "频谱图.png",
                size=options.spectrum_size,
                seconds=options.audio_spectrum_seconds,
                backend=options.audio_spectrum_backend,
            )
        return validate_audio_directory_output(output)

    def _process_disc(
        self,
        source: Path,
        output: Path,
        options: ProcessingOptions,
    ) -> tuple[tuple[Path, ...], tuple[str, ...]]:
        report = self.tools.generate_bdinfo_report(source, output)
        self.tools.make_disc_screenshots(source, output, options.screenshot_candidates)
        files = validate_disc_output(output)
        warnings: tuple[str, ...] = ()
        if report.read_text(encoding="utf-8", errors="replace").startswith(
            "BDInfo: fallback-report"
        ):
            warnings = ("BDInfo unavailable or invalid; generated a fallback report",)
        return files, warnings


__all__ = [
    "MediaPipeline",
    "PipelineError",
    "ProcessingOptions",
    "ProcessingResult",
    "SUPPORTED_MEDIA_TYPES",
]
