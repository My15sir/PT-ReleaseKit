from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MediaType(str, Enum):
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    AUDIO_DIR = "AUDIO_DIR"
    BDMV = "BDMV"
    ISO = "ISO"


class RunMode(str, Enum):
    REMOTE = "remote"
    LOCAL = "local"


class SpectrumMode(str, Enum):
    SINGLE = "single"
    COMBINED = "combined"


class SpectrumBackend(str, Enum):
    AUTO = "auto"
    SOX = "sox"
    SOX_NG = "sox_ng"
    FFMPEG = "ffmpeg"


@dataclass(frozen=True)
class ScanItem:
    """One item returned by the legacy ``scan-json`` interface."""

    index: int
    type: MediaType
    type_label: str
    path: str

    def __post_init__(self) -> None:
        if self.index < 1:
            raise ValueError("scan item index must be positive")
        if not isinstance(self.type, MediaType):
            object.__setattr__(self, "type", MediaType(self.type))
        if not self.path:
            raise ValueError("scan item path must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "type": self.type.value,
            "type_label": self.type_label,
            "path": self.path,
        }


@dataclass(frozen=True)
class AppConfig:
    """Shared controller configuration with secrets kept out of ``repr``."""

    mode: RunMode
    local_root: str
    remote_host: str
    remote_port: str
    remote_password: str = field(repr=False)
    remote_cmd: str
    remote_bootstrap: bool
    save_dir: str
    scan_include: str
    scan_exclude: str
    scan_full: bool
    audio_spectrum_mode: SpectrumMode
    audio_spectrum_backend: SpectrumBackend
    audio_spectrum_combined_track_seconds: str
    auto_cleanup: bool

    def to_dict(self, *, include_secret: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mode": self.mode.value,
            "local_root": self.local_root,
            "remote_host": self.remote_host,
            "remote_port": self.remote_port,
            "remote_password": self.remote_password if include_secret else "",
            "remote_cmd": self.remote_cmd,
            "remote_bootstrap": self.remote_bootstrap,
            "save_dir": self.save_dir,
            "scan_include": self.scan_include,
            "scan_exclude": self.scan_exclude,
            "scan_full": self.scan_full,
            "audio_spectrum_mode": self.audio_spectrum_mode.value,
            "audio_spectrum_backend": self.audio_spectrum_backend.value,
            "audio_spectrum_combined_track_seconds": self.audio_spectrum_combined_track_seconds,
            "auto_cleanup": self.auto_cleanup,
        }
        if not include_secret:
            data["password_saved"] = bool(self.remote_password)
        return data
