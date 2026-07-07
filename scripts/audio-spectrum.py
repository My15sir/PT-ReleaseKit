#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except Exception as exc:  # pragma: no cover - exercised on hosts missing optional deps
    print(f"missing Python spectrum dependency: {exc}", file=sys.stderr)
    raise SystemExit(2)


def parse_size(value: str) -> tuple[int, int]:
    if "x" not in value:
        raise ValueError(f"invalid size: {value}")
    width_text, height_text = value.lower().split("x", 1)
    width = int(width_text)
    height = int(height_text)
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid size: {value}")
    return width, height


def decode_audio(path: Path, seconds: int, sample_rate: int) -> np.ndarray:
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    if seconds > 0:
        cmd.extend(["-t", str(seconds)])
    cmd.extend(
        [
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "s16le",
            "-",
        ]
    )
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg decode failed for {path}: {stderr}")
    if not result.stdout:
        raise RuntimeError(f"ffmpeg decoded no audio for {path}")
    return np.frombuffer(result.stdout, dtype="<i2").astype(np.float32) / 32768.0


def collect_samples(paths: list[Path], seconds: int, sample_rate: int) -> np.ndarray:
    chunks: list[np.ndarray] = []
    for index, path in enumerate(paths, start=1):
        print(f"[audio-spectrum] decode {index}/{len(paths)}: {path.name}", flush=True)
        chunks.append(decode_audio(path, seconds, sample_rate))
    if not chunks:
        raise RuntimeError("no audio files")
    return np.concatenate(chunks)


def apply_colormap(values: np.ndarray) -> np.ndarray:
    stops = np.array([0.0, 0.25, 0.50, 0.75, 1.0], dtype=np.float32)
    colors = np.array(
        [
            [0, 0, 0],
            [0, 34, 100],
            [0, 190, 210],
            [255, 170, 0],
            [255, 255, 255],
        ],
        dtype=np.float32,
    )
    rgb = np.zeros(values.shape + (3,), dtype=np.float32)
    for offset in range(len(stops) - 1):
        low = stops[offset]
        high = stops[offset + 1]
        mask = (values >= low) & (values <= high)
        if not np.any(mask):
            continue
        span = high - low
        factor = ((values[mask] - low) / span)[:, None]
        rgb[mask] = colors[offset] * (1.0 - factor) + colors[offset + 1] * factor
    return np.clip(rgb, 0, 255).astype(np.uint8)


def render_spectrum(samples: np.ndarray, output: Path, size: tuple[int, int]) -> None:
    width, height = size
    n_fft = 4096
    if samples.size < n_fft:
        samples = np.pad(samples, (0, n_fft - samples.size))
    max_start = samples.size - n_fft
    positions = np.linspace(0, max_start, width, dtype=np.int64)
    frame_index = positions[:, None] + np.arange(n_fft, dtype=np.int64)
    window = np.hanning(n_fft).astype(np.float32)
    frames = samples[frame_index] * window
    spectrum = np.abs(np.fft.rfft(frames, axis=1)).T
    spectrum = spectrum[1:, :]
    freq_index = np.geomspace(1, spectrum.shape[0] - 1, height).astype(np.int64)
    image_values = np.flipud(spectrum[freq_index, :])
    db_values = 20.0 * np.log10(image_values + 1e-8)
    high = float(np.percentile(db_values, 99.5))
    low = max(float(np.percentile(db_values, 5.0)), high - 90.0)
    if high <= low:
        normalized = np.zeros_like(db_values, dtype=np.float32)
    else:
        normalized = np.clip((db_values - low) / (high - low), 0.0, 1.0).astype(np.float32)
    rgb = apply_colormap(normalized)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, "RGB").save(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a continuous audio spectrogram from one or more audio files.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--size", default="1280x720")
    parser.add_argument("--seconds", default="12")
    parser.add_argument("--sample-rate", default="44100")
    parser.add_argument("audio", nargs="+", type=Path)
    args = parser.parse_args(argv)

    seconds = int(args.seconds)
    sample_rate = int(args.sample_rate)
    if seconds < 0:
        raise ValueError("--seconds must be >= 0")
    if sample_rate <= 0:
        raise ValueError("--sample-rate must be > 0")

    samples = collect_samples(args.audio, seconds, sample_rate)
    print("[audio-spectrum] render continuous spectrogram", flush=True)
    render_spectrum(samples, args.output, parse_size(args.size))
    print(f"[audio-spectrum] wrote {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
