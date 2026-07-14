from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit


class ReturnError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReturnResult:
    mode: str
    destination: str


RETURN_RECORD_TYPE = "ptbd-result"


def serialize_return_record(result: ReturnResult) -> str:
    return json.dumps(
        {
            "type": RETURN_RECORD_TYPE,
            "mode": result.mode,
            "archive": result.destination,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def parse_return_record(line: str) -> ReturnResult | None:
    try:
        payload = json.loads(line)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("type") != RETURN_RECORD_TYPE:
        return None
    mode = payload.get("mode")
    destination = payload.get("archive")
    if mode not in {"local", "http", "scp"} or not isinstance(destination, str) or not destination:
        return None
    return ReturnResult(mode=mode, destination=destination)


def detect_return_mode(env: dict[str, str] | None = None) -> str:
    values = env or os.environ
    mode = values.get("BDTOOL_RETURN_MODE", "").strip().lower()
    if not mode:
        if any(values.get(key) for key in ("BDTOOL_RETURN_SCP_HOST", "BDTOOL_RETURN_SCP_USER", "BDTOOL_RETURN_SCP_REMOTE_DIR")):
            mode = "scp"
        elif values.get("BDTOOL_RETURN_HTTP_URL") or values.get("BDTOOL_CLIENT_UPLOAD_URL"):
            mode = "http"
        else:
            mode = "local"
    if mode not in {"local", "http", "scp"}:
        raise ReturnError(f"invalid BDTOOL_RETURN_MODE: {mode}")
    return mode


def default_download_dir(env: dict[str, str] | None = None) -> Path:
    values = env or os.environ
    explicit = values.get("BDTOOL_DOWNLOAD_DIR", "").strip()
    if explicit:
        target = Path(explicit).expanduser()
    else:
        home = Path(values.get("HOME") or Path.home()).expanduser()
        if values.get("SSH_CONNECTION"):
            target = home / "PT-BDtool-downloads"
        else:
            desktop = next((path for path in (home / "Desktop", home / "桌面") if path.is_dir()), home / "Desktop")
            target = desktop / "PT-BDtool"
    target.mkdir(parents=True, exist_ok=True)
    if not target.is_dir() or not os.access(target, os.W_OK):
        raise ReturnError(f"download directory is not writable: {target}")
    return target


def package_stage_dir(output_dir: Path, env: dict[str, str] | None = None) -> Path:
    values = env or os.environ
    mode = detect_return_mode(values)
    if mode == "local":
        return default_download_dir(values)
    explicit = values.get("BDTOOL_CLIENT_STAGE_DIR", "").strip()
    target = Path(explicit).expanduser() if explicit else output_dir.parent
    target.mkdir(parents=True, exist_ok=True)
    return target


def build_upload_url(base_url: str, filename: str) -> str:
    if not base_url:
        raise ReturnError("missing BDTOOL_RETURN_HTTP_URL")
    if "{filename}" in base_url:
        return base_url.replace("{filename}", quote(filename))
    parts = urlsplit(base_url)
    separator = "&" if parts.query else ""
    query = f"{parts.query}{separator}filename={quote(filename)}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _run(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, env=env, text=True, capture_output=True, check=False)


def upload_http(archive: Path, env: dict[str, str] | None = None) -> str:
    values = dict(os.environ if env is None else env)
    curl = shutil.which("curl", path=values.get("PATH"))
    if not curl:
        raise ReturnError("HTTP return requires curl")
    base_url = values.get("BDTOOL_RETURN_HTTP_URL") or values.get("BDTOOL_CLIENT_UPLOAD_URL") or ""
    url = build_upload_url(base_url, archive.name)
    result = _run(
        [
            curl,
            "--fail",
            "--silent",
            "--show-error",
            "--connect-timeout",
            "10",
            "--max-time",
            "600",
            "--http1.0",
            "-H",
            "Expect:",
            "-X",
            "PUT",
            "--data-binary",
            f"@{archive}",
            url,
        ],
        env=values,
    )
    destination = result.stdout.strip()
    if result.returncode != 0 or not destination:
        detail = result.stderr.strip() or f"curl exited with {result.returncode}"
        raise ReturnError(f"HTTP return failed: {detail}")
    return destination


def _ssh_transport_command(tool: str, env: dict[str, str]) -> list[str]:
    executable = shutil.which(tool, path=env.get("PATH"))
    if not executable:
        raise ReturnError(f"SCP return requires {tool}")
    port = env.get("BDTOOL_RETURN_SCP_PORT", "22")
    strict = env.get("BDTOOL_RETURN_SCP_STRICT_HOST_KEY_CHECKING", "accept-new")
    command = [executable, "-p" if tool == "ssh" else "-P", port, "-o", f"StrictHostKeyChecking={strict}"]
    identity = env.get("BDTOOL_RETURN_SCP_IDENTITY_FILE", "").strip()
    if identity:
        command.extend(["-i", identity])
    password = env.get("BDTOOL_RETURN_SCP_PASSWORD", "")
    if password:
        sshpass = shutil.which("sshpass", path=env.get("PATH"))
        if not sshpass:
            raise ReturnError("SCP password return requires sshpass")
        command = [sshpass, "-e", *command]
    return command


def upload_scp(archive: Path, env: dict[str, str] | None = None) -> str:
    values = dict(os.environ if env is None else env)
    host = values.get("BDTOOL_RETURN_SCP_HOST", "").strip()
    user = values.get("BDTOOL_RETURN_SCP_USER", "").strip()
    remote_dir = values.get("BDTOOL_RETURN_SCP_REMOTE_DIR", "").strip()
    if not host or not user or not remote_dir:
        raise ReturnError("SCP return requires host, user and remote directory")
    if values.get("BDTOOL_RETURN_SCP_PASSWORD"):
        values["SSHPASS"] = values["BDTOOL_RETURN_SCP_PASSWORD"]

    ssh_result = _run(
        [*_ssh_transport_command("ssh", values), f"{user}@{host}", f"mkdir -p -- {shlex.quote(remote_dir)}"],
        env=values,
    )
    if ssh_result.returncode != 0:
        raise ReturnError(ssh_result.stderr.strip() or "failed to create SCP destination")

    remote_path = f"{remote_dir.rstrip('/')}/{archive.name}"
    scp_result = _run(
        [*_ssh_transport_command("scp", values), str(archive), f"{user}@{host}:{shlex.quote(remote_path)}"],
        env=values,
    )
    if scp_result.returncode != 0:
        raise ReturnError(scp_result.stderr.strip() or "SCP upload failed")
    return remote_path


def return_archive(archive: Path, env: dict[str, str] | None = None) -> ReturnResult:
    values = dict(os.environ if env is None else env)
    mode = detect_return_mode(values)
    if mode == "local":
        return ReturnResult(mode=mode, destination=str(archive))
    if mode == "http":
        destination = upload_http(archive, values)
    else:
        destination = upload_scp(archive, values)
    archive.unlink(missing_ok=True)
    return ReturnResult(mode=mode, destination=destination)
