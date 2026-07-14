from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from ptbd_core.bundle_archive import (
    OFFICIAL_BOOTSTRAP_SHA256,
    OFFICIAL_BUNDLE_URL,
    OFFICIAL_CHECKSUM_URL,
    BundleArchiveError,
    BundleChecksumSidecarDigestError,
    BundleChecksumSidecarEncodingError,
    BundleChecksumUnavailableError,
    ExplicitBundleChecksumError,
    extract_bundle_archive,
    resolve_bundle_checksum,
    verify_bundle_checksum,
)
from ptbd_core.config import (
    normalize_remote_connection,
    normalize_scan_roots,
    parse_port,
    parse_remote_target,
    split_path_roots,
)
from ptbd_core.runtime_assets import read_shared_asset, validate_profile

try:
    import paramiko
except Exception as exc:  # pragma: no cover
    paramiko = None
    PARAMIKO_IMPORT_ERROR = exc
else:  # pragma: no cover
    PARAMIKO_IMPORT_ERROR = None


LogFunc = Callable[[str], None]
BUNDLE_DOWNLOAD_URL = os.environ.get(
    "PTBD_BUNDLE_URL",
    OFFICIAL_BUNDLE_URL,
)
BUNDLE_SHA256 = os.environ.get("PTBD_BUNDLE_SHA256", "").strip()
BUNDLE_CHECKSUM_URL = os.environ.get("PTBD_BUNDLE_CHECKSUM_URL", f"{BUNDLE_DOWNLOAD_URL}.sha256")
BUNDLE_ALLOW_UNVERIFIED = os.environ.get("PTBD_BUNDLE_ALLOW_UNVERIFIED", "0") == "1"
PREFERRED_SCAN_ROOTS = ("/home", "/root", "/data", "/mnt", "/media", "/srv")


def preferred_scan_roots_text() -> str:
    return " ".join(PREFERRED_SCAN_ROOTS)


def bool_config(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def split_path_list(raw: object) -> list[str]:
    return split_path_roots(raw)


def build_effective_scan_include(config: dict) -> str:
    explicit = split_path_list(config.get("scan_include"))
    if explicit:
        return normalize_scan_roots(explicit)
    if bool_config(config.get("scan_full"), default=False):
        # An explicit root bypasses the remote-session preferred-root fallback.
        return "/"
    return preferred_scan_roots_text()


def backend_available() -> bool:
    return PARAMIKO_IMPORT_ERROR is None


def backend_status() -> str:
    if backend_available():
        return "standalone-python"
    return f"legacy-shell ({PARAMIKO_IMPORT_ERROR})"


def quote_sh(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


parse_port_value = parse_port


def parse_remote_host(remote_host: str) -> tuple[str | None, str]:
    username, hostname, _ = parse_remote_target(remote_host)
    return username, hostname


def bool_flag(value: str) -> bool:
    return str(value).strip() == "1"


def unique_local_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    index = 2
    while True:
        trial = directory / f"{stem}_{index}{suffix}"
        if not trial.exists():
            return trial
        index += 1


@dataclass
class RemoteSystemInfo:
    os_name: str = ""
    arch: str = ""
    home: str = ""
    distro_id: str = ""
    distro_like: str = ""
    version_id: str = ""
    has_tar: bool = False
    has_bash: bool = False
    has_python3: bool = False
    has_curl: bool = False
    has_ffmpeg: bool = False
    has_ffprobe: bool = False
    has_mediainfo: bool = False
    has_numpy: bool = False
    has_pil: bool = False
    has_bdinfo: bool = False
    has_bd_info: bool = False

    @classmethod
    def from_output(cls, raw: str) -> "RemoteSystemInfo":
        values: dict[str, str] = {}
        for line in raw.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
        return cls(
            os_name=values.get("REMOTE_OS", ""),
            arch=values.get("REMOTE_ARCH", ""),
            home=values.get("REMOTE_HOME", ""),
            distro_id=values.get("REMOTE_ID", ""),
            distro_like=values.get("REMOTE_ID_LIKE", ""),
            version_id=values.get("REMOTE_VERSION_ID", ""),
            has_tar=bool_flag(values.get("REMOTE_HAS_TAR", "0")),
            has_bash=bool_flag(values.get("REMOTE_HAS_BASH", "0")),
            has_python3=bool_flag(values.get("REMOTE_HAS_PYTHON3", "0")),
            has_curl=bool_flag(values.get("REMOTE_HAS_CURL", "0")),
            has_ffmpeg=bool_flag(values.get("REMOTE_HAS_FFMPEG", "0")),
            has_ffprobe=bool_flag(values.get("REMOTE_HAS_FFPROBE", "0")),
            has_mediainfo=bool_flag(values.get("REMOTE_HAS_MEDIAINFO", "0")),
            has_numpy=bool_flag(values.get("REMOTE_HAS_NUMPY", "0")),
            has_pil=bool_flag(values.get("REMOTE_HAS_PIL", "0")),
            has_bdinfo=bool_flag(values.get("REMOTE_HAS_BDINFO", "0")),
            has_bd_info=bool_flag(values.get("REMOTE_HAS_BD_INFO", "0")),
        )

    def auto_install_supported(self) -> bool:
        tokens = f" {self.distro_id} {self.distro_like} "
        return any(token in tokens for token in (" debian ", " ubuntu ", " alpine "))

    def core_deps_ready(self) -> bool:
        return all(
            (
                self.has_tar,
                self.has_bash,
                self.has_python3,
                self.has_curl,
                self.has_ffmpeg,
                self.has_ffprobe,
                self.has_mediainfo,
            )
        )

    def spectrum_python_deps_ready(self) -> bool:
        return self.has_numpy and self.has_pil


@dataclass
class CommandResult:
    exit_code: int
    output: str


class BackendUnavailableError(RuntimeError):
    pass


class RemoteCommandError(RuntimeError):
    pass


class UnknownHostKeyError(RuntimeError):
    def __init__(self, hostname: str, key_type: str, fingerprint: str) -> None:
        self.hostname = hostname
        self.key_type = key_type
        self.fingerprint = fingerprint
        super().__init__(f"unknown SSH host key for {hostname}: {key_type} {fingerprint}")


class RejectUnknownHostKeyPolicy:
    def missing_host_key(self, _client, hostname: str, key) -> None:
        try:
            key_type = key.get_name()
        except Exception:
            key_type = "unknown"
        try:
            digest = hashlib.sha256(key.asbytes()).digest()
            fingerprint = "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
        except Exception:
            fingerprint = "unavailable"
        raise UnknownHostKeyError(hostname, key_type, fingerprint)


class TaskCancelledError(RuntimeError):
    pass


def system_known_hosts_files() -> tuple[Path, ...]:
    candidates = [Path("/etc/ssh/ssh_known_hosts"), Path("/etc/ssh/ssh_known_hosts2")]
    program_data = os.environ.get("PROGRAMDATA", "").strip()
    if program_data:
        candidates.append(Path(program_data) / "ssh" / "ssh_known_hosts")
    return tuple(path for path in candidates if path.is_file())


def host_key_help(remote_host: str, hostname: str, port: int) -> str:
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    return (
        "请先通过可信渠道核对服务器 SSH 主机密钥指纹，然后在系统终端执行：\n"
        f"  ssh -p {port} {remote_host}\n"
        "确认终端显示的指纹无误并接受后，再重试 PT ReleaseKit。也可将经核验的公钥写入：\n"
        f"  {known_hosts}\n"
        f"采集候选公钥可使用：ssh-keyscan -p {port} {hostname}\n"
        "不要在未核对指纹时直接信任 ssh-keyscan 的输出。"
    )


class PTBDRemoteBackend:
    def __init__(self, app_root: Path, config: dict, logger: LogFunc | None = None) -> None:
        self.app_root = Path(app_root).resolve()
        self.config = config
        self.logger = logger
        self.client = None
        self.sftp = None
        self.remote_info: RemoteSystemInfo | None = None
        self.remote_cache_root: str | None = None
        self.runtime_launcher: str | None = None
        self._cancelled = False
        self._active_channel = None

    def log(self, message: str) -> None:
        if self.logger:
            self.logger(message)

    def ensure_available(self) -> None:
        if backend_available():
            return
        raise BackendUnavailableError(
            "当前环境缺少 paramiko，独立控制后端不可用。"
            f" 原因：{PARAMIKO_IMPORT_ERROR}"
        )

    def connect(self) -> None:
        self.ensure_available()
        self.ensure_not_cancelled()
        if self.client is not None:
            return
        normalized_host, normalized_port = normalize_remote_connection(
            self.config["remote_host"],
            self.config.get("remote_port") or 22,
        )
        username, hostname = parse_remote_host(normalized_host)
        password = self.config.get("remote_password") or None
        client = paramiko.SSHClient()
        port = int(normalized_port)
        try:
            self.client = client
            self.ensure_not_cancelled()
            # Paramiko's default path is the current user's ~/.ssh/known_hosts.
            client.load_system_host_keys()
            for known_hosts_file in system_known_hosts_files():
                client.load_system_host_keys(str(known_hosts_file))
            client.set_missing_host_key_policy(RejectUnknownHostKeyPolicy())
            self.ensure_not_cancelled()
            client.connect(
                hostname=hostname,
                port=port,
                username=username,
                password=password,
                allow_agent=not password,
                look_for_keys=not password,
                compress=True,
                timeout=20,
                banner_timeout=20,
                auth_timeout=20,
            )
            self.ensure_not_cancelled()
        except UnknownHostKeyError as exc:
            self._discard_connect_attempt(client)
            raise RemoteCommandError(
                "VPS 主机密钥不在 known_hosts 中，已拒绝连接。\n"
                f"服务器展示的候选密钥：{exc.key_type} {exc.fingerprint}\n"
                f"{host_key_help(normalized_host, hostname, port)}"
            ) from exc
        except paramiko.BadHostKeyException as exc:
            self._discard_connect_attempt(client)
            raise RemoteCommandError(
                "VPS 主机密钥与 known_hosts 记录不一致，已拒绝连接。"
                "这可能是服务器密钥已变更，也可能是中间人攻击。\n"
                "不要直接删除原记录；请先通过可信渠道确认服务器的新指纹。\n"
                f"连接目标：{normalized_host}:{port}\n原始错误：{exc}"
            ) from exc
        except TaskCancelledError:
            self._discard_connect_attempt(client)
            raise
        except Exception:
            self._discard_connect_attempt(client)
            if self._cancelled:
                raise TaskCancelledError("任务已取消。")
            raise

    def ensure_sftp(self) -> None:
        self.connect()
        self.ensure_not_cancelled()
        if self.sftp is not None:
            return
        client = self.client
        if client is None:
            raise RemoteCommandError("SSH 连接已断开，无法打开 SFTP。")
        sftp = None
        try:
            sftp = client.open_sftp()
            self.ensure_not_cancelled()
            if self.client is not client:
                raise RemoteCommandError("SSH 连接在打开 SFTP 时已变更。")
            self.sftp = sftp
            self.ensure_not_cancelled()
        except TaskCancelledError:
            self._discard_sftp_attempt(sftp)
            raise
        except Exception as exc:
            self._discard_sftp_attempt(sftp)
            if self._cancelled:
                raise TaskCancelledError("任务已取消。") from exc
            raise RemoteCommandError(
                "SSH 已连通，但 SFTP 子系统不可用（Channel closed）。"
                "将尝试用 SSH 管道传输文件；若仍失败，请在 VPS 启用 Subsystem sftp。"
                f" 原始错误：{exc}"
            ) from exc

    def _discard_sftp_attempt(self, sftp) -> None:
        if sftp is not None:
            try:
                sftp.close()
            except Exception:
                pass
        if self.sftp is sftp:
            self.sftp = None

    def put_file(self, local_path: Path | str, remote_path: str) -> None:
        """Upload a local file. Prefer SFTP; fall back to an SSH stdin pipe."""
        self.connect()
        self.ensure_not_cancelled()
        local = Path(local_path)
        try:
            self.ensure_sftp()
            self.sftp.put(str(local), remote_path)
            self.ensure_not_cancelled()
            return
        except TaskCancelledError:
            raise
        except Exception as sftp_exc:
            self.ensure_not_cancelled()
            self.log(f"[gui] SFTP 上传不可用，回退 SSH 管道：{sftp_exc}")

        parent = str(PurePosixPath(remote_path).parent)
        if parent and parent not in {".", "/"}:
            self.run_script(f"mkdir -p {quote_sh(parent)}", check=False)
        self.ensure_not_cancelled()
        client = self.client
        if client is None:
            raise RemoteCommandError("SSH 连接已断开，无法上传文件。")
        transport = client.get_transport()
        if transport is None:
            raise RemoteCommandError("SSH 连接已断开，无法上传文件。")
        channel = None
        try:
            channel = transport.open_session()
            channel.settimeout(600)
            self._active_channel = channel
            self.ensure_not_cancelled()
            channel.exec_command(f"cat > {quote_sh(remote_path)}")
            with local.open("rb") as handle:
                while True:
                    self.ensure_not_cancelled()
                    chunk = handle.read(65536)
                    if not chunk:
                        break
                    channel.sendall(chunk)
            channel.shutdown_write()
            while not channel.exit_status_ready():
                self.ensure_not_cancelled()
                if channel.recv_ready():
                    channel.recv(4096)
                if channel.recv_stderr_ready():
                    channel.recv_stderr(4096)
                time.sleep(0.05)
            rc = channel.recv_exit_status()
            if rc != 0:
                err = b""
                while channel.recv_stderr_ready():
                    err += channel.recv_stderr(4096)
                raise RemoteCommandError(
                    f"SSH 管道上传失败 rc={rc}: {err.decode('utf-8', errors='replace') or remote_path}"
                )
        except TaskCancelledError:
            raise
        except Exception as exc:
            if self._cancelled:
                raise TaskCancelledError("任务已取消。") from exc
            if isinstance(exc, RemoteCommandError):
                raise
            raise RemoteCommandError(f"SSH 管道上传失败：{exc}") from exc
        finally:
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass
                if self._active_channel is channel:
                    self._active_channel = None

    def get_file(self, remote_path: str, local_path: Path | str) -> None:
        """Download a remote file. Prefer SFTP; fall back to an SSH stdout pipe."""
        self.connect()
        self.ensure_not_cancelled()
        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        partial = local.with_name(f".{local.name}.{uuid.uuid4().hex}.part")
        try:
            try:
                self.ensure_sftp()
                self.sftp.get(remote_path, str(partial))
                self.ensure_not_cancelled()
                if not partial.is_file() or partial.stat().st_size <= 0:
                    raise RemoteCommandError("SFTP 下载结果为空")
                os.replace(partial, local)
                return
            except TaskCancelledError:
                raise
            except Exception as sftp_exc:
                partial.unlink(missing_ok=True)
                self.ensure_not_cancelled()
                self.log(f"[gui] SFTP 下载不可用，回退 SSH 管道：{sftp_exc}")

            client = self.client
            if client is None:
                raise RemoteCommandError("SSH 连接已断开，无法下载文件。")
            transport = client.get_transport()
            if transport is None:
                raise RemoteCommandError("SSH 连接已断开，无法下载文件。")
            channel = None
            try:
                channel = transport.open_session()
                channel.settimeout(600)
                self._active_channel = channel
                self.ensure_not_cancelled()
                channel.exec_command(f"cat {quote_sh(remote_path)}")
                with partial.open("xb") as handle:
                    while True:
                        self.ensure_not_cancelled()
                        if channel.recv_ready():
                            chunk = channel.recv(65536)
                            if not chunk:
                                break
                            handle.write(chunk)
                            continue
                        if channel.exit_status_ready():
                            while channel.recv_ready():
                                handle.write(channel.recv(65536))
                            break
                        time.sleep(0.05)
                rc = channel.recv_exit_status()
                if rc != 0 or not partial.exists() or partial.stat().st_size <= 0:
                    err = b""
                    while channel.recv_stderr_ready():
                        err += channel.recv_stderr(4096)
                    raise RemoteCommandError(
                        f"SSH 管道下载失败 rc={rc}: {err.decode('utf-8', errors='replace') or remote_path}"
                    )
                os.replace(partial, local)
            except TaskCancelledError:
                raise
            except Exception as exc:
                if self._cancelled:
                    raise TaskCancelledError("任务已取消。") from exc
                if isinstance(exc, RemoteCommandError):
                    raise
                raise RemoteCommandError(f"SSH 管道下载失败：{exc}") from exc
            finally:
                if channel is not None:
                    try:
                        channel.close()
                    except Exception:
                        pass
                    if self._active_channel is channel:
                        self._active_channel = None
        finally:
            partial.unlink(missing_ok=True)

    def _discard_connect_attempt(self, client, sftp=None) -> None:
        self._discard_sftp_attempt(sftp)
        try:
            client.close()
        except Exception:
            pass
        if self.client is client:
            self.client = None

    def close(self) -> None:
        channel = self._active_channel
        self._active_channel = None
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
        if self.sftp is not None:
            try:
                self.sftp.close()
            except Exception:
                pass
            self.sftp = None
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def cancel(self) -> None:
        self._cancelled = True
        self.close()

    def ensure_not_cancelled(self) -> None:
        if self._cancelled:
            raise TaskCancelledError("任务已取消。")

    def build_shell_command(self, script: str, *, use_bash: bool = False, env: dict[str, str] | None = None) -> str:
        prefix = ""
        if env:
            exports = [f"export {key}={quote_sh(value)};" for key, value in env.items() if value is not None]
            prefix = " ".join(exports)
        body = f"{prefix} {script}".strip()
        shell_name = "bash" if use_bash else "sh"
        return f"{shell_name} -lc {quote_sh(body)}"

    def run_script(
        self,
        script: str,
        *,
        use_bash: bool = False,
        env: dict[str, str] | None = None,
        stream_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        self.ensure_not_cancelled()
        self.connect()
        self.ensure_not_cancelled()
        command = self.build_shell_command(script, use_bash=use_bash, env=env)
        channel = None
        raw_output = io.StringIO()
        try:
            client = self.client
            if client is None:
                raise RemoteCommandError("SSH 连接已断开。")
            transport = client.get_transport()
            if transport is None:
                raise RemoteCommandError("SSH 连接已断开。")
            channel = transport.open_session()
            channel.set_combine_stderr(True)
            self._active_channel = channel
            self.ensure_not_cancelled()
            channel.exec_command(command)
            stream = channel.makefile("r", -1)
            while True:
                self.ensure_not_cancelled()
                line = stream.readline()
                if not line:
                    break
                raw_output.write(line)
                if stream_output:
                    self.log(line.rstrip("\r\n"))
            exit_code = channel.recv_exit_status()
            self.ensure_not_cancelled()
        except TaskCancelledError:
            raise
        except Exception as exc:
            if self._cancelled:
                raise TaskCancelledError("任务已取消。") from exc
            raise RemoteCommandError(str(exc)) from exc
        finally:
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass
                if self._active_channel is channel:
                    self._active_channel = None
        output = raw_output.getvalue().strip()
        self.ensure_not_cancelled()
        if check and exit_code != 0:
            raise RemoteCommandError(output or f"远端命令失败，退出码：{exit_code}")
        return CommandResult(exit_code=exit_code, output=output)

    def diagnose_connection(self) -> dict:
        """Probe SSH + remote deps without starting a full scan/generate job."""
        explicit_scan_roots = split_path_list(self.config.get("scan_include"))
        full_scan = bool_config(self.config.get("scan_full"), default=False) and not explicit_scan_roots
        scan_mode = "whitelist" if explicit_scan_roots else ("full" if full_scan else "preferred")
        report: dict = {
            "ok": False,
            "remote_host": self.config.get("remote_host"),
            "remote_port": self.config.get("remote_port"),
            "os_name": "",
            "distro_id": "",
            "arch": "",
            "auto_install_supported": False,
            "core_deps_ready": False,
            "missing_core_deps": [],
            "has_bdinfo": False,
            "scan_mode": scan_mode,
            "scan_roots": build_effective_scan_include(self.config),
            "bootstrap": bool_config(self.config.get("remote_bootstrap"), default=True),
            "message": "",
            "hints": [],
        }
        try:
            self.connect()
            info = self.probe_remote_system()
        except TaskCancelledError:
            raise
        except Exception as exc:
            report["message"] = f"连接失败：{exc}"
            report["hints"] = [
                "检查 VPS 地址/端口是否正确",
                "检查密码或 SSH 密钥是否可用",
                "确认本机网络能访问该 VPS",
            ]
            return report

        report["os_name"] = info.os_name
        report["distro_id"] = info.distro_id
        report["arch"] = info.arch
        report["auto_install_supported"] = info.auto_install_supported()
        report["core_deps_ready"] = info.core_deps_ready()
        report["has_bdinfo"] = bool(info.has_bdinfo or info.has_bd_info)
        missing = []
        required_checks = [
            ("tar", info.has_tar),
            ("bash", info.has_bash),
            ("python3", info.has_python3),
            ("curl", info.has_curl),
            ("ffmpeg", info.has_ffmpeg),
            ("ffprobe", info.has_ffprobe),
            ("mediainfo", info.has_mediainfo),
        ]
        if str(self.config.get("audio_spectrum_mode") or "single") == "combined":
            required_checks.extend((("numpy", info.has_numpy), ("PIL", info.has_pil)))
        for label, ready in required_checks:
            if not ready:
                missing.append(label)
        report["missing_core_deps"] = missing

        if info.os_name != "Linux":
            report["message"] = f"已连通，但远端系统暂不支持：{info.os_name or 'unknown'}"
            report["hints"] = ["当前只支持 Linux VPS"]
            return report

        hints: list[str] = []
        if not report["core_deps_ready"]:
            if report["auto_install_supported"] and report["bootstrap"]:
                hints.append("核心依赖未齐；扫描/生成时会尝试自动安装，不够再上传运行包")
            elif report["bootstrap"]:
                hints.append("核心依赖未齐，且发行版不在自动安装优先列表；将更依赖上传运行包")
            else:
                hints.append("核心依赖未齐，且未开启空白 VPS 自举；请手动安装依赖或开启自举")
        if not report["has_bdinfo"]:
            hints.append("未检测到 BDInfo/bd_info：原盘/ISO 可能走降级报告")
        if report["scan_mode"] == "preferred":
            hints.append(f"默认扫描优先目录：{report['scan_roots'] or preferred_scan_roots_text()}")
        elif report["scan_mode"] == "full":
            hints.append("当前为全盘扫描模式，媒体多时可能较慢")
        else:
            hints.append(f"当前仅扫描显式白名单：{report['scan_roots']}")

        # Probe SFTP availability without failing diagnose.
        sftp_ok = False
        try:
            self.ensure_sftp()
            sftp_ok = True
        except TaskCancelledError:
            raise
        except Exception as sftp_exc:
            hints.append(f"SFTP 不可用，将回退 SSH 管道传输：{sftp_exc}")
        report["sftp_available"] = sftp_ok

        report["ok"] = True
        if report["core_deps_ready"]:
            report["message"] = "连接成功，核心依赖已就绪"
        else:
            report["message"] = "连接成功，但核心依赖未齐"
        if not sftp_ok:
            report["message"] += "（SFTP 不可用，已启用管道回退）"
        report["hints"] = hints
        return report

    def probe_remote_system(self) -> RemoteSystemInfo:
        script = read_shared_asset(self.app_root, "ptbd_core/assets/remote-probe.sh")
        result = self.run_script(script)
        info = RemoteSystemInfo.from_output(result.output)
        self.remote_info = info
        if not self.remote_cache_root:
            remote_home = info.home or "~"
            self.remote_cache_root = f"{remote_home}/.cache/ptbd-remote"
        return info

    def ensure_remote_system_deps(self) -> None:
        script = read_shared_asset(self.app_root, "ptbd_core/assets/remote-install-deps.sh")
        spectrum_mode = str(self.config.get("audio_spectrum_mode") or "single")
        result = self.run_script(
            script,
            env={"PTBD_AUDIO_SPECTRUM_MODE": spectrum_mode},
            stream_output=True,
            check=False,
        )
        if result.exit_code != 0:
            self.log(f"[gui] 远端依赖自动安装返回退出码：{result.exit_code}，继续按回退逻辑处理")
        status_line = ""
        for line in result.output.splitlines():
            if line.startswith("status="):
                status_line = line
        if status_line:
            self.log(f"[gui] VPS 依赖检查结果：{status_line}")

    def remote_file_ready(self, remote_path: str) -> bool:
        result = self.run_script(
            f"if [ -x {quote_sh(remote_path)} ]; then printf '%s\\n' ready; else printf '%s\\n' missing; fi"
        )
        return result.output.strip() == "ready"

    def local_bundle_ready(self) -> bool:
        bundle_root = self.app_root / "third_party" / "bundle" / "linux-amd64"
        required = [
            bundle_root / "bin" / "ffmpeg",
            bundle_root / "bin" / "ffprobe",
            bundle_root / "bin" / "mediainfo",
            bundle_root / "bin" / "BDInfo",
            bundle_root / "lib",
        ]
        return all(path.exists() for path in required)

    def expected_bundle_checksum(self) -> str | None:
        def read_checksum_sidecar(url: str) -> bytes:
            with urllib.request.urlopen(url, timeout=30) as response:
                return response.read()

        try:
            resolution = resolve_bundle_checksum(
                bundle_url=BUNDLE_DOWNLOAD_URL,
                checksum_url=BUNDLE_CHECKSUM_URL,
                explicit_checksum=BUNDLE_SHA256,
                allow_unverified=BUNDLE_ALLOW_UNVERIFIED,
                read_checksum_sidecar=read_checksum_sidecar,
            )
        except ExplicitBundleChecksumError as exc:
            raise RemoteCommandError(f"PTBD_BUNDLE_SHA256 无效：{exc}") from (exc.__cause__ or exc)
        except BundleChecksumUnavailableError as exc:
            raise RemoteCommandError(
                "bundle checksum 不可用；请提供 PTBD_BUNDLE_SHA256、可用的 .sha256 sidecar，"
                "或显式设置 PTBD_BUNDLE_ALLOW_UNVERIFIED=1。"
            ) from (exc.__cause__ or exc)
        except BundleChecksumSidecarEncodingError as exc:
            raise RemoteCommandError("bundle checksum sidecar 不是 ASCII 文本。") from (
                exc.__cause__ or exc
            )
        except BundleChecksumSidecarDigestError as exc:
            raise RemoteCommandError(f"bundle checksum sidecar 无效：{exc}") from (
                exc.__cause__ or exc
            )

        if resolution.source == "official-bootstrap":
            self.log("[gui] bundle checksum sidecar 不可用，使用官方旧资产固定摘要")
        elif resolution.source == "unverified":
            self.log(f"[gui] 警告：已显式允许未校验 bundle：{resolution.unavailable_error}")
        return resolution.checksum

    def download_local_bundle(self) -> None:
        bundle_root = self.app_root / "third_party" / "bundle" / "linux-amd64"
        self.log(f"[gui] 本地 linux bundle 缺失，尝试下载：{BUNDLE_DOWNLOAD_URL}")
        with tempfile.TemporaryDirectory(prefix="ptbd-bundle-download-") as temp_dir:
            archive_path = Path(temp_dir) / "PT-BDtool-linux-amd64.tar.gz"
            try:
                with urllib.request.urlopen(BUNDLE_DOWNLOAD_URL, timeout=120) as response, archive_path.open(
                    "wb"
                ) as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
            except (OSError, ValueError) as exc:
                raise RemoteCommandError(f"bundle 下载失败：{exc}") from exc
            expected = self.expected_bundle_checksum()
            if expected:
                try:
                    verify_bundle_checksum(archive_path, expected)
                except (BundleArchiveError, OSError) as exc:
                    raise RemoteCommandError(f"bundle SHA256 校验失败：{exc}") from exc
            try:
                extract_bundle_archive(archive_path, bundle_root)
            except (BundleArchiveError, OSError, tarfile.TarError) as exc:
                raise RemoteCommandError(f"bundle 资产校验失败：{exc}") from exc
        self.log(f"[gui] 本地 linux bundle 已就绪：{bundle_root}")

    def ensure_local_bundle(self) -> None:
        if self.local_bundle_ready():
            return
        self.download_local_bundle()
        if not self.local_bundle_ready():
            raise RemoteCommandError("本地 bundle 拉取结束，但文件仍不完整。")

    def require_local_runtime_files(self, archive_mode: str) -> None:
        validate_profile(self.app_root, "remote")
        required: list[Path] = []
        if archive_mode == "bundle":
            self.ensure_local_bundle()
            required.extend(
                [
                    self.app_root / "third_party" / "bundle" / "linux-amd64" / "bin",
                    self.app_root / "third_party" / "bundle" / "linux-amd64" / "lib",
                ]
            )
        for item in required:
            if not item.exists():
                raise FileNotFoundError(f"缺少运行时文件：{item}")

    def runtime_members(self, archive_mode: str) -> list[tuple[Path, str]]:
        members = [
            (entry.source, entry.relative_path)
            for entry in validate_profile(self.app_root, "remote")
        ]
        if archive_mode == "bundle":
            bundle_root = self.app_root / "third_party" / "bundle" / "linux-amd64"
            for branch in ("bin", "lib"):
                root = bundle_root / branch
                entries = sorted(root.rglob("*"), key=lambda item: item.relative_to(self.app_root).as_posix())
                for entry in entries:
                    members.append((entry, entry.relative_to(self.app_root).as_posix()))
        return members

    def build_runtime_archive(self, archive_mode: str) -> tuple[Path, str]:
        self.require_local_runtime_files(archive_mode)
        members = self.runtime_members(archive_mode)
        digest = hashlib.sha256()
        digest.update(f"mode:{archive_mode}".encode("utf-8"))
        digest.update(b"\0text-normalize-lf-v1\0")
        text_suffixes = {".sh", ".py", ".env", ".txt", ".md", ".json", ".yml", ".yaml", ".desktop", ".command"}
        text_names = {"bdtool", "ptbd", "ptbd-gui", "ptbd-web", "ptbd-remote.sh", "Dockerfile"}

        def is_text_member(source_path: Path, relative_path: str) -> bool:
            name = PurePosixPath(relative_path).name
            if name in text_names or name.startswith("bdtool"):
                return True
            suffix = source_path.suffix.lower()
            if suffix in text_suffixes:
                return True
            # Shebang scripts without extension (e.g. bdtool)
            try:
                with source_path.open("rb") as handle:
                    head = handle.read(2)
                return head == b"#!"
            except OSError:
                return False

        def read_member_bytes(source_path: Path, relative_path: str) -> bytes | None:
            if source_path.is_symlink() or source_path.is_dir():
                return None
            data = source_path.read_bytes()
            if is_text_member(source_path, relative_path):
                # Avoid Windows CRLF breaking Linux shebang: "bash\r"
                data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            return data

        for source_path, relative_path in members:
            digest.update(relative_path.encode("utf-8"))
            digest.update(b"\0")
            stat_result = os.lstat(source_path)
            mode = stat.S_IMODE(stat_result.st_mode)
            if source_path.is_symlink():
                digest.update(b"L")
                digest.update(os.readlink(source_path).encode("utf-8"))
            elif source_path.is_dir():
                digest.update(b"D")
            else:
                digest.update(b"F")
                payload = read_member_bytes(source_path, relative_path) or b""
                digest.update(payload)
            digest.update(str(mode).encode("ascii"))
            digest.update(b"\0")
        runtime_hash = digest.hexdigest()
        temp_dir = Path(tempfile.mkdtemp(prefix="ptbd-runtime-"))
        archive_path = temp_dir / "ptbd-runtime.tar.gz"

        def normalize_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            return info

        with tarfile.open(archive_path, "w:gz", compresslevel=6, format=tarfile.PAX_FORMAT) as handle:
            for source_path, relative_path in members:
                if source_path.is_dir() or source_path.is_symlink():
                    handle.add(source_path, arcname=relative_path, recursive=False, filter=normalize_tar_info)
                    continue
                payload = read_member_bytes(source_path, relative_path)
                if payload is None:
                    handle.add(source_path, arcname=relative_path, recursive=False, filter=normalize_tar_info)
                    continue
                info = tarfile.TarInfo(name=relative_path)
                info.size = len(payload)
                info.mode = stat.S_IMODE(source_path.stat().st_mode)
                # Ensure scripts remain executable on remote.
                if is_text_member(source_path, relative_path) or source_path.name in {"bdtool", "bdtool.sh"}:
                    info.mode |= 0o755
                info = normalize_tar_info(info)
                handle.addfile(info, io.BytesIO(payload))
        return archive_path, runtime_hash

    def normalize_remote_arch(self, remote_arch: str) -> str:
        if remote_arch in {"x86_64", "amd64"}:
            return "linux-amd64"
        raise RemoteCommandError(
            f"远端架构 {remote_arch or 'unknown'} 既缺系统依赖，又不能使用内置 linux-amd64 回退包。"
        )

    def ensure_runtime(self) -> str:
        if self.runtime_launcher:
            return self.runtime_launcher
        info = self.probe_remote_system()
        if info.os_name != "Linux":
            raise RemoteCommandError(f"暂不支持该远端系统：{info.os_name or 'unknown'}")
        self.log(
            f"[gui] 远端系统：os={info.os_name or 'unknown'} id={info.distro_id or 'unknown'} "
            f"version={info.version_id or 'unknown'} arch={info.arch or 'unknown'}"
        )
        if info.auto_install_supported():
            self.log("[gui] 已识别 Debian / Ubuntu / Alpine，先尝试自动装依赖")
            self.ensure_remote_system_deps()
            info = self.probe_remote_system()
        if (
            str(self.config.get("audio_spectrum_mode") or "single") == "combined"
            and not info.spectrum_python_deps_ready()
        ):
            raise RemoteCommandError(
                "组合音频频谱需要远端 Python numpy 和 Pillow；自动安装未能补齐，请先手动安装。"
            )
        archive_mode = "minimal"
        if info.core_deps_ready():
            self.log("[gui] 远端系统依赖已齐，使用轻量运行包")
        else:
            self.normalize_remote_arch(info.arch)
            archive_mode = "bundle"
            self.log("[gui] 远端依赖仍不完整，回退上传内置 linux-amd64 运行包")

        archive_path, runtime_hash = self.build_runtime_archive(archive_mode)
        try:
            remote_runtime_dir = f"{self.remote_cache_root}/runtime-{runtime_hash}"
            remote_launcher = f"{remote_runtime_dir}/ptbd-runtime"
            remote_archive = f"{self.remote_cache_root}/runtime-{runtime_hash}-{os.getpid()}.tar.gz"
            remote_tmp_dir = f"{self.remote_cache_root}/.runtime-{runtime_hash}-{os.getpid()}"

            if self.remote_file_ready(remote_launcher):
                self.log(f"[gui] 远端运行包缓存命中：{remote_runtime_dir}")
                self.runtime_launcher = remote_launcher
                return remote_launcher

            self.log(f"[gui] 正在上传 {archive_mode} 运行包到 {self.config['remote_host']}:{self.remote_cache_root}")
            self.run_script(f"mkdir -p {quote_sh(self.remote_cache_root)}")
            self.put_file(archive_path, remote_archive)
            self.run_script(
                self.render_prepare_runtime_script(
                    remote_runtime_dir=remote_runtime_dir,
                    remote_launcher=remote_launcher,
                    remote_archive=remote_archive,
                    remote_tmp_dir=remote_tmp_dir,
                    archive_mode=archive_mode,
                )
            )
            self.log(f"[gui] 远端运行包就绪：{remote_launcher}")
            self.runtime_launcher = remote_launcher
            return remote_launcher
        finally:
            try:
                archive_path.unlink()
            except OSError:
                pass
            try:
                archive_path.parent.rmdir()
            except OSError:
                pass

    def render_prepare_runtime_script(
        self,
        *,
        remote_runtime_dir: str,
        remote_launcher: str,
        remote_archive: str,
        remote_tmp_dir: str,
        archive_mode: str,
    ) -> str:
        runtime_dir_literal = quote_sh(remote_runtime_dir)
        return f"""
set -eu

runtime_dir={quote_sh(remote_runtime_dir)}
launcher_path={quote_sh(remote_launcher)}
archive_path={quote_sh(remote_archive)}
tmp_dir={quote_sh(remote_tmp_dir)}
archive_mode={quote_sh(archive_mode)}
work_dir="$runtime_dir"

if [ -f "$archive_path" ]; then
  rm -rf "$tmp_dir"
  mkdir -p "$tmp_dir"
  tar -xzf "$archive_path" -C "$tmp_dir"
  work_dir="$tmp_dir"
fi

mkdir -p "$work_dir/bin" "$(dirname "$runtime_dir")"
chmod +x "$work_dir/bdtool" "$work_dir/bdtool-legacy.sh" "$work_dir/bdtool.sh"

rm -f "$work_dir/bin/BDInfo"
if [ "$archive_mode" = "minimal" ] && ! command -v BDInfo >/dev/null 2>&1 && command -v bd_info >/dev/null 2>&1; then
  cat > "$work_dir/bin/BDInfo" <<'EOS'
#!/usr/bin/env sh
set -eu
if [ "${{1:-}}" = "-w" ]; then
  shift
fi
target="${{1:-}}"
if [ -z "$target" ]; then
  echo "usage: BDInfo <scan_target> [out_dir]" >&2
  exit 2
fi
exec bd_info "$target"
EOS
  chmod +x "$work_dir/bin/BDInfo"
fi

cat > "$work_dir/ptbd-runtime" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
RUNTIME_DIR={runtime_dir_literal}
export PTBDTOOL_ROOT="$RUNTIME_DIR"
export PTBD_INSTALL_ROOT="$RUNTIME_DIR"
export PATH="$RUNTIME_DIR/bin:$PATH"
exec "$RUNTIME_DIR/bdtool" "$@"
EOS
chmod +x "$work_dir/ptbd-runtime"

if [ "$work_dir" != "$runtime_dir" ]; then
  if [ -d "$runtime_dir" ]; then
    rm -rf "$work_dir"
  else
    mv "$work_dir" "$runtime_dir"
  fi
fi

rm -f "$archive_path"
[ -x "$launcher_path" ]
"""

    def resolve_remote_command(self) -> str:
        if self.config.get("remote_bootstrap"):
            return self.ensure_runtime()
        return "bdtool"

    def build_scan_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        scan_root = str(self.config.get("local_root") or "/").strip() or "/"
        scan_include = build_effective_scan_include(self.config)
        scan_exclude = self.config.get("scan_exclude", "")
        env["BDTOOL_SCAN_FULL_ROOT"] = scan_root
        if scan_include:
            roots = split_path_list(scan_include)
            env["BDTOOL_SCAN_INCLUDE_ROOTS"] = normalize_scan_roots(roots)
            env["BDTOOL_SCAN_INCLUDE_ROOTS_JSON"] = json.dumps(roots, ensure_ascii=False)
            env["BDTOOL_SCAN_INCLUDE_ROOTS_LINES"] = "\n".join(roots)
        if scan_exclude:
            excluded = split_path_list(scan_exclude)
            env["BDTOOL_SCAN_EXCLUDE_ROOTS"] = normalize_scan_roots(excluded)
            env["BDTOOL_SCAN_EXCLUDE_ROOTS_JSON"] = json.dumps(excluded, ensure_ascii=False)
            env["BDTOOL_SCAN_EXCLUDE_ROOTS_LINES"] = "\n".join(excluded)
        env["BDTOOL_AUDIO_SPECTRUM_MODE"] = str(self.config.get("audio_spectrum_mode") or "single")
        env["BDTOOL_AUDIO_SPECTRUM_BACKEND"] = str(self.config.get("audio_spectrum_backend") or "auto")
        env["BDTOOL_AUDIO_SPECTRUM_COMBINED_TRACK_SECONDS"] = str(
            self.config.get("audio_spectrum_combined_track_seconds") or "12"
        )
        return env

    def scan_command_suffix(self) -> str:
        # Always call scan-json --full so include-root logic in bdtool is used;
        # preferred mode is enforced via BDTOOL_SCAN_INCLUDE_ROOTS.
        return "scan-json --full --lang zh"

    def scan_items(self) -> list[dict]:
        remote_cmd = self.resolve_remote_command()
        scan_include = build_effective_scan_include(self.config)
        explicit_scan_roots = split_path_list(self.config.get("scan_include"))
        if explicit_scan_roots:
            mode = "白名单"
        elif bool_config(self.config.get("scan_full"), default=False):
            mode = "全盘"
        else:
            mode = "优先目录"
        self.log(f"[gui] 扫描模式：{mode}；根目录：{scan_include or '(系统默认全盘候选根)'}")
        result = self.run_script(
            f"exec {quote_sh(remote_cmd)} {self.scan_command_suffix()}",
            use_bash=True,
            env=self.build_scan_env(),
        )
        import json

        if not result.output.strip():
            raise RemoteCommandError("scan-json 没有返回任何内容。")
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError as exc:
            preview = result.output.strip().splitlines()[:20]
            if preview:
                self.log("[gui] scan-json 原始输出预览：")
                for line in preview:
                    self.log(line)
            raise RemoteCommandError(f"scan-json 返回的不是合法 JSON：{exc}") from exc
        return payload.get("items", [])

    def extract_download_path(self, output: str) -> str | None:
        for line in reversed(output.splitlines()):
            for prefix in ("已下载：", "Downloaded: "):
                if line.startswith(prefix):
                    candidate = line[len(prefix) :].strip()
                    if candidate:
                        return candidate
        return None

    def find_remote_package(self, stage_dir: str) -> str | None:
        result = self.run_script(
            f"ls -1t {quote_sh(stage_dir)}/*.zip {quote_sh(stage_dir)}/*.tar.gz 2>/dev/null | head -n 1",
            check=False,
        )
        candidate = result.output.strip()
        return candidate or None

    def cleanup_remote_package(self, remote_package: str, stage_dir: str) -> None:
        self.run_script(
            f"rm -f {quote_sh(remote_package)}; rmdir {quote_sh(stage_dir)} >/dev/null 2>&1 || true",
            check=False,
        )

    def download_result(self, remote_package: str, save_dir: Path) -> Path:
        save_dir.mkdir(parents=True, exist_ok=True)
        local_path = unique_local_path(save_dir, Path(remote_package).name)
        self.log(f"[gui] 正在下载结果到本机：{local_path}")
        self.get_file(remote_package, local_path)
        self.log(f"[gui] 本机已收到结果：{local_path}")
        return local_path

    def extract_quality_status(self, output: str) -> str:
        for line in reversed(output.splitlines()):
            text = line.strip()
            if text.startswith("QUALITY_STATUS="):
                return text[len("QUALITY_STATUS=") :].strip()
            if "质量状态：" in text:
                return text.split("质量状态：", 1)[-1].strip()
        return ""

    def process_selected_path(self, selected_path: str, save_dir: Path) -> Path:
        remote_cmd = self.resolve_remote_command()
        task_id = f"{int(time.time())}-{os.getpid()}"
        stage_dir = f"{self.remote_cache_root}/downloads-{task_id}"
        env = self.build_scan_env()
        env.update(
            {
                "BDTOOL_RETURN_MODE": "local",
                "BDTOOL_DOWNLOAD_DIR": stage_dir,
                "BDTOOL_AUTO_CLEANUP": "1" if self.config.get("auto_cleanup", True) else "0",
            }
        )
        self.run_script(f"mkdir -p {quote_sh(stage_dir)}")
        result = self.run_script(
            f"exec {quote_sh(remote_cmd)} generate-path --path {quote_sh(selected_path)} --lang zh --audio-spectrum {quote_sh(str(self.config.get('audio_spectrum_mode') or 'single'))}",
            use_bash=True,
            env=env,
            stream_output=True,
            check=False,
        )
        if result.exit_code != 0:
            raise RemoteCommandError(result.output or f"远端处理失败，退出码：{result.exit_code}")
        quality = self.extract_quality_status(result.output)
        if quality:
            self.log(f"[gui] 质量状态：{quality}")
        remote_package = self.extract_download_path(result.output) or self.find_remote_package(stage_dir)
        if not remote_package:
            raise RemoteCommandError("远端任务成功结束了，但没有找到可下载结果包。")
        local_path = self.download_result(remote_package, save_dir)
        self.cleanup_remote_package(remote_package, stage_dir)
        self.log(f"[gui] 成功：结果已保存到 {local_path}")
        return local_path
