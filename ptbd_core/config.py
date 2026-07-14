from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

from .models import AppConfig, RunMode, SpectrumBackend, SpectrumMode


DEFAULT_REMOTE_HOST = "root@your-vps"
DEFAULT_REMOTE_PORT = "22"
DEFAULT_REMOTE_CMD = "pt"

_REMOTE_TARGET_PATTERN = re.compile(
    r"""
    (?<![A-Za-z0-9_.-])
    (?:(?P<user>[A-Za-z0-9._-]+)@)?
    (?P<host>
        \[[0-9A-Fa-f:.]+\]
        |
        (?:\d{1,3}\.){3}\d{1,3}
        |
        localhost
        |
        [A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*
    )
    (?::(?P<port>\d{1,5}))?
    """,
    re.VERBOSE,
)

_SSH_OPTIONS_WITH_VALUE = {
    "-b",
    "-c",
    "-D",
    "-E",
    "-F",
    "-I",
    "-i",
    "-J",
    "-L",
    "-l",
    "-m",
    "-O",
    "-o",
    "-p",
    "-Q",
    "-R",
    "-S",
    "-W",
    "-w",
}

_IGNORED_HOST_TOKENS = {
    "host",
    "password",
    "passwd",
    "port",
    "ssh",
    "user",
    "vps",
}


def default_save_dir() -> str:
    home = Path.home()
    for candidate in (home / "Desktop", home / "桌面", home / "Downloads"):
        if candidate.is_dir():
            return str(candidate)
    return str(home / "PT-BDtool-downloads")


def default_config() -> AppConfig:
    mode = RunMode.LOCAL if os.environ.get("PTBD_WEB_MODE", "remote").strip().lower() == "local" else RunMode.REMOTE
    local_root = _clean_text(os.environ.get("PTBD_WEB_LOCAL_ROOT", "/"), field="local_root") or "/"
    return AppConfig(
        mode=mode,
        local_root=local_root,
        remote_host=DEFAULT_REMOTE_HOST,
        remote_port=DEFAULT_REMOTE_PORT,
        remote_password="",
        remote_cmd=DEFAULT_REMOTE_CMD,
        remote_bootstrap=True,
        save_dir=default_save_dir(),
        scan_include="",
        scan_exclude="",
        scan_full=False,
        audio_spectrum_mode=SpectrumMode.SINGLE,
        audio_spectrum_backend=SpectrumBackend.AUTO,
        audio_spectrum_combined_track_seconds="12",
        auto_cleanup=True,
    )


def _clean_text(value: Any, *, field: str) -> str:
    text = str(value if value is not None else "").strip()
    if any(ord(character) < 32 for character in text):
        raise ValueError(f"{field} contains a control character")
    return text


def _clean_password(value: Any) -> str:
    text = str(value if value is not None else "")
    if any(character in text for character in ("\x00", "\r", "\n")):
        raise ValueError("remote_password contains a forbidden control character")
    return text


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default


def split_path_roots(raw: Any) -> list[str]:
    """Split roots the same way as ``BDTOOL_SCAN_*_ROOTS``."""

    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        text = str(raw).strip()
        unquoted_fallback = False
        try:
            lexer = shlex.shlex(text, posix=True)
            lexer.whitespace += ","
            lexer.whitespace_split = True
            lexer.commenters = ""
            lexer.escape = ""
            values = list(lexer)
        except ValueError:
            # A literal apostrophe was historically accepted without quoting.
            # Retry without quote semantics so multiple absolute roots still split.
            lexer = shlex.shlex(text, posix=True)
            lexer.whitespace += ","
            lexer.whitespace_split = True
            lexer.commenters = ""
            lexer.escape = ""
            lexer.quotes = ""
            values = list(lexer)
            unquoted_fallback = True
        if (
            len(values) > 1
            and "," not in text
            and (unquoted_fallback or not any(quote in text for quote in ("'", '"')))
            and not all(_looks_like_root_start(value) for value in values)
        ):
            values = [text]

    roots: list[str] = []
    seen: set[str] = set()
    for value in values:
        root = _clean_text(value, field="scan root")
        root = trim_path_root(root)
        if root and root not in seen:
            roots.append(root)
            seen.add(root)
    return roots


def parse_path_roots_json(raw: str) -> list[str]:
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("scan roots JSON must be a valid array") from exc
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise ValueError("scan roots JSON must be an array of strings")
    return split_path_roots(decoded)


def parse_path_roots_lines(raw: str) -> list[str]:
    lines = []
    for line in raw.split("\n"):
        lines.append(line[:-1] if line.endswith("\r") else line)
    return split_path_roots(lines)


def _looks_like_root_start(value: str) -> bool:
    return bool(value.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:[\\/]", value))


def trim_path_root(value: str) -> str:
    root = str(value)
    while root.endswith(("/", "\\")):
        if root in {PurePosixPath(root).anchor, PureWindowsPath(root).anchor}:
            break
        root = root[:-1]
    return root


def normalize_scan_roots(raw: Any) -> str:
    return shlex.join(split_path_roots(raw))


def parse_port(value: str | int | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError(f"SSH 端口格式无效：{value}")
    port = int(text)
    if not 1 <= port <= 65535:
        raise ValueError(f"SSH 端口超出范围：{value}")
    return port


def _clean_host(hostname: str) -> str:
    cleaned = hostname.strip().strip("\"'<>[](){}")
    if not cleaned or any(ord(character) < 32 for character in cleaned):
        raise ValueError("缺少 VPS 地址。")
    return cleaned


def _parse_ssh_command(text: str) -> tuple[str | None, str, int | None] | None:
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    if not tokens or tokens[0] != "ssh":
        return None

    username: str | None = None
    port: int | None = None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in _SSH_OPTIONS_WITH_VALUE:
            if index + 1 >= len(tokens):
                break
            value = tokens[index + 1]
            if token == "-l":
                username = value
            elif token == "-p":
                port = parse_port(value)
            index += 2
            continue
        if token.startswith("-p") and token != "-p":
            port = parse_port(token[2:])
            index += 1
            continue
        if token.startswith("-l") and token != "-l":
            username = token[2:] or None
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue

        target = token
        if "@" in target:
            parsed_user, _, hostname = target.rpartition("@")
            username = parsed_user or username
        else:
            hostname = target
        return username or None, _clean_host(hostname), port
    return None


def parse_remote_target(remote_host: str) -> tuple[str | None, str, int | None]:
    text = _clean_text(remote_host, field="remote_host")
    if not text:
        raise ValueError("缺少 VPS 地址。")

    if text.startswith("ssh://"):
        parsed = urlsplit(text)
        if not parsed.hostname:
            raise ValueError(f"VPS 地址格式无效：{remote_host}")
        return parsed.username or None, parsed.hostname, parse_port(parsed.port)

    ssh_target = _parse_ssh_command(text)
    if ssh_target is not None:
        return ssh_target

    candidates: list[tuple[int, str | None, str, int | None]] = []
    for match in _REMOTE_TARGET_PATTERN.finditer(text):
        hostname = _clean_host(match.group("host"))
        username = match.group("user") or None
        port = parse_port(match.group("port"))
        score = 4 if username else 0
        if "." in hostname or ":" in hostname or hostname.replace(".", "").isdigit():
            score += 3
        if port is not None:
            score += 2
        if hostname.lower() in _IGNORED_HOST_TOKENS:
            score -= 6
        candidates.append((score, username, hostname, port))
    if candidates:
        _, username, hostname, port = max(candidates, key=lambda item: item[0])
        return username, hostname, port

    direct = _clean_host(text)
    if " " in direct:
        raise ValueError(f"VPS 地址格式无效：{remote_host}")
    if "@" not in direct:
        return None, direct, None
    username, _, hostname = direct.rpartition("@")
    if not hostname:
        raise ValueError(f"VPS 地址格式无效：{remote_host}")
    return username or None, _clean_host(hostname), None


def normalize_remote_connection(remote_host: str, remote_port: str | int | None) -> tuple[str, str]:
    username, hostname, embedded_port = parse_remote_target(remote_host)
    port = embedded_port if embedded_port is not None else parse_port(remote_port)
    normalized_port = port if port is not None else 22
    normalized_host = f"{username}@{hostname}" if username else hostname
    return normalized_host, str(normalized_port)


def _mapping(config: AppConfig | Mapping[str, Any] | None) -> dict[str, Any]:
    if config is None:
        return default_config().to_dict()
    if isinstance(config, AppConfig):
        return config.to_dict()
    return dict(config)


def sanitize_config(
    raw: Mapping[str, Any],
    *,
    existing: AppConfig | Mapping[str, Any] | None = None,
) -> AppConfig:
    data = default_config().to_dict()
    data.update(_mapping(existing))

    text_fields = ("local_root", "remote_host", "remote_port", "remote_cmd", "save_dir")
    for field in text_fields:
        if field in raw:
            data[field] = _clean_text(raw.get(field), field=field)

    mode = str(raw.get("mode", data.get("mode", "remote"))).strip().lower()
    data["mode"] = RunMode.LOCAL if mode == RunMode.LOCAL.value else RunMode.REMOTE

    requested_mode = str(raw.get("audio_spectrum_mode", data.get("audio_spectrum_mode", "single"))).strip().lower()
    data["audio_spectrum_mode"] = (
        SpectrumMode(requested_mode) if requested_mode in {item.value for item in SpectrumMode} else SpectrumMode.SINGLE
    )
    requested_backend = str(
        raw.get("audio_spectrum_backend", data.get("audio_spectrum_backend", "auto"))
    ).strip().lower()
    data["audio_spectrum_backend"] = (
        SpectrumBackend(requested_backend)
        if requested_backend in {item.value for item in SpectrumBackend}
        else SpectrumBackend.AUTO
    )

    raw_seconds = raw.get(
        "audio_spectrum_combined_track_seconds",
        data.get("audio_spectrum_combined_track_seconds", "12"),
    )
    seconds = str(raw_seconds if raw_seconds is not None else "").strip()
    data["audio_spectrum_combined_track_seconds"] = seconds if seconds.isdigit() else "12"

    for field in ("scan_include", "scan_exclude"):
        if field in raw:
            data[field] = normalize_scan_roots(raw.get(field))
        else:
            data[field] = normalize_scan_roots(data.get(field))

    bool_defaults = {
        "remote_bootstrap": True,
        "auto_cleanup": True,
        "scan_full": False,
    }
    for field, default in bool_defaults.items():
        if field in raw:
            data[field] = _coerce_bool(raw.get(field), default=bool(data.get(field, default)))
        else:
            data[field] = _coerce_bool(data.get(field), default=default)

    password = raw.get("remote_password")
    if _coerce_bool(raw.get("clear_password"), default=False):
        data["remote_password"] = ""
    elif password is not None and str(password) != "":
        data["remote_password"] = _clean_password(password)
    else:
        data["remote_password"] = _clean_password(data.get("remote_password", ""))

    data["local_root"] = data.get("local_root") or "/"
    data["remote_host"] = data.get("remote_host") or DEFAULT_REMOTE_HOST
    data["remote_port"] = data.get("remote_port") or DEFAULT_REMOTE_PORT
    data["remote_cmd"] = data.get("remote_cmd") or DEFAULT_REMOTE_CMD
    data["save_dir"] = data.get("save_dir") or default_save_dir()
    data["remote_host"], data["remote_port"] = normalize_remote_connection(
        data["remote_host"], data["remote_port"]
    )

    return AppConfig(
        mode=data["mode"],
        local_root=data["local_root"],
        remote_host=data["remote_host"],
        remote_port=data["remote_port"],
        remote_password=data["remote_password"],
        remote_cmd=data["remote_cmd"],
        remote_bootstrap=data["remote_bootstrap"],
        save_dir=data["save_dir"],
        scan_include=data["scan_include"],
        scan_exclude=data["scan_exclude"],
        scan_full=data["scan_full"],
        audio_spectrum_mode=data["audio_spectrum_mode"],
        audio_spectrum_backend=data["audio_spectrum_backend"],
        audio_spectrum_combined_track_seconds=data["audio_spectrum_combined_track_seconds"],
        auto_cleanup=data["auto_cleanup"],
    )


def public_config(config: AppConfig | Mapping[str, Any]) -> dict[str, Any]:
    normalized = config if isinstance(config, AppConfig) else sanitize_config(config)
    return normalized.to_dict(include_secret=False)


def load_config(path: str | os.PathLike[str], *, include_secret: bool = False) -> dict[str, Any]:
    config_path = Path(path)
    raw: Mapping[str, Any] = {}
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, UnicodeError, json.JSONDecodeError):
            raw = {}
    try:
        config = sanitize_config(raw)
    except (TypeError, ValueError):
        config = default_config()
    return config.to_dict(include_secret=include_secret)


def save_config(path: str | os.PathLike[str], config: AppConfig | Mapping[str, Any]) -> None:
    normalized = config if isinstance(config, AppConfig) else sanitize_config(config)
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(normalized.to_dict(), ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.",
        suffix=".tmp",
        dir=config_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        try:
            os.chmod(temporary_path, 0o600)
        except OSError:
            pass
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, config_path)
        try:
            config_path.chmod(0o600)
        except OSError:
            pass
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
