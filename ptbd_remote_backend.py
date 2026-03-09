from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

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
    "https://github.com/My15sir/PT-BDtool/releases/download/bundle-latest/PT-BDtool-linux-amd64.tar.gz",
)
BUNDLE_MARKER_PARTS = ("third_party", "bundle", "linux-amd64")


def backend_available() -> bool:
    return PARAMIKO_IMPORT_ERROR is None


def backend_status() -> str:
    if backend_available():
        return "standalone-python"
    return f"legacy-shell ({PARAMIKO_IMPORT_ERROR})"


def quote_sh(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def parse_remote_host(remote_host: str) -> tuple[str | None, str]:
    host = remote_host.strip()
    if not host:
        raise ValueError("缺少 VPS 地址。")
    if "@" not in host:
        return None, host
    username, _, hostname = host.rpartition("@")
    if not hostname:
        raise ValueError(f"VPS 地址格式无效：{remote_host}")
    return username or None, hostname


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


def bundle_member_relative_path(member_name: str) -> Path | None:
    parts = PurePosixPath(member_name).parts
    for index in range(len(parts) - len(BUNDLE_MARKER_PARTS) + 1):
        if parts[index : index + len(BUNDLE_MARKER_PARTS)] == BUNDLE_MARKER_PARTS:
            remainder = parts[index + len(BUNDLE_MARKER_PARTS) :]
            if not remainder:
                return None
            return Path(*remainder)
    return None


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


@dataclass
class CommandResult:
    exit_code: int
    output: str


class BackendUnavailableError(RuntimeError):
    pass


class RemoteCommandError(RuntimeError):
    pass


class TaskCancelledError(RuntimeError):
    pass


PROBE_REMOTE_SYSTEM_SCRIPT = r"""
set -eu

if [ -r /etc/os-release ]; then
  . /etc/os-release
fi

has_cmd() {
  if command -v "$1" >/dev/null 2>&1; then
    printf '1'
  else
    printf '0'
  fi
}

printf 'REMOTE_OS=%s\n' "$(uname -s 2>/dev/null || echo unknown)"
printf 'REMOTE_ARCH=%s\n' "$(uname -m 2>/dev/null || echo unknown)"
printf 'REMOTE_HOME=%s\n' "${HOME:-}"
printf 'REMOTE_ID=%s\n' "${ID:-}"
printf 'REMOTE_ID_LIKE=%s\n' "${ID_LIKE:-}"
printf 'REMOTE_VERSION_ID=%s\n' "${VERSION_ID:-}"
printf 'REMOTE_HAS_TAR=%s\n' "$(has_cmd tar)"
printf 'REMOTE_HAS_BASH=%s\n' "$(has_cmd bash)"
printf 'REMOTE_HAS_PYTHON3=%s\n' "$(has_cmd python3)"
printf 'REMOTE_HAS_CURL=%s\n' "$(has_cmd curl)"
printf 'REMOTE_HAS_FFMPEG=%s\n' "$(has_cmd ffmpeg)"
printf 'REMOTE_HAS_FFPROBE=%s\n' "$(has_cmd ffprobe)"
printf 'REMOTE_HAS_MEDIAINFO=%s\n' "$(has_cmd mediainfo)"
printf 'REMOTE_HAS_BDINFO=%s\n' "$(has_cmd BDInfo)"
printf 'REMOTE_HAS_BD_INFO=%s\n' "$(has_cmd bd_info)"
"""


ENSURE_REMOTE_SYSTEM_DEPS_SCRIPT = r"""
set -eu

if [ -r /etc/os-release ]; then
  . /etc/os-release
fi

id="${ID:-}"
id_like="${ID_LIKE:-}"

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

word_match() {
  case " $1 " in
    *" $2 "*) return 0 ;;
    *) return 1 ;;
  esac
}

is_debian_like() {
  word_match "$id $id_like" debian || word_match "$id $id_like" ubuntu
}

is_alpine_like() {
  word_match "$id $id_like" alpine
}

as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
    return $?
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo -n "$@"
    return $?
  fi
  return 97
}

enable_ubuntu_universe() {
  if [ "$id" != "ubuntu" ]; then
    return 0
  fi
  if apt-cache show mediainfo >/dev/null 2>&1 && apt-cache show ffmpeg >/dev/null 2>&1; then
    return 0
  fi
  echo "[remote] Ubuntu universe repo looks unavailable; trying to enable it" >&2
  if ! command -v add-apt-repository >/dev/null 2>&1; then
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y software-properties-common >/dev/null
  fi
  if command -v add-apt-repository >/dev/null 2>&1; then
    as_root add-apt-repository -y universe >/dev/null 2>&1 || true
  fi
}

enable_alpine_community() {
  if grep -Eq '^[[:space:]]*[^#].*/community/?[[:space:]]*$' /etc/apk/repositories 2>/dev/null; then
    return 0
  fi
  echo "[remote] Alpine community repo looks unavailable; trying to enable it" >&2
  as_root sh -eu <<'EOS'
tmp_file="$(mktemp)"
cleanup() {
  rm -f "$tmp_file"
}
trap cleanup EXIT

if grep -Eq '^[[:space:]]*#.*\/community/?[[:space:]]*$' /etc/apk/repositories 2>/dev/null; then
  awk '
    /^[[:space:]]*#/ && /\/community\/?[[:space:]]*$/ { sub(/^[[:space:]]*#[[:space:]]*/, "", $0) }
    { print }
  ' /etc/apk/repositories > "$tmp_file"
else
  awk '
    { print }
    /^[[:space:]]*[^#].*\/main\/?[[:space:]]*$/ {
      line=$0
      sub(/\/main\/?[[:space:]]*$/, "/community", line)
      print line
    }
  ' /etc/apk/repositories > "$tmp_file"
fi

cp "$tmp_file" /etc/apk/repositories
EOS
}

missing_required=""
for cmd in tar bash python3 curl ffmpeg ffprobe mediainfo; do
  if ! has_cmd "$cmd"; then
    missing_required="$missing_required $cmd"
  fi
done

need_optional_bd="0"
if ! has_cmd BDInfo && ! has_cmd bd_info; then
  need_optional_bd="1"
fi

if [ -z "${missing_required# }" ] && [ "$need_optional_bd" = "0" ]; then
  echo "status=ready"
  exit 0
fi

if is_debian_like; then
  if ! as_root true >/dev/null 2>&1; then
    echo "status=missing-required-no-privilege"
    exit 0
  fi
  echo "[remote] Debian/Ubuntu detected; installing system packages for PT-BDtool" >&2
  as_root apt-get update >/dev/null
  if ! as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y bash curl python3 tar ffmpeg mediainfo zip >/dev/null 2>&1; then
    enable_ubuntu_universe
    as_root apt-get update >/dev/null
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y bash curl python3 tar ffmpeg mediainfo zip >/dev/null
  fi
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y libbluray-bin >/dev/null 2>&1 || true
  echo "status=installed"
  exit 0
fi

if is_alpine_like; then
  if ! as_root true >/dev/null 2>&1; then
    echo "status=missing-required-no-privilege"
    exit 0
  fi
  echo "[remote] Alpine detected; installing system packages for PT-BDtool" >&2
  if ! as_root apk add --no-cache bash curl python3 tar ffmpeg mediainfo zip >/dev/null 2>&1; then
    enable_alpine_community
    as_root apk update >/dev/null
    as_root apk add --no-cache bash curl python3 tar ffmpeg mediainfo zip >/dev/null
  fi
  as_root apk add --no-cache libbluray >/dev/null 2>&1 || true
  echo "status=installed"
  exit 0
fi

echo "status=unsupported"
"""


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
        if self.client is not None:
            return
        username, hostname = parse_remote_host(self.config["remote_host"])
        password = self.config.get("remote_password") or None
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=hostname,
            port=int(self.config.get("remote_port") or 22),
            username=username,
            password=password,
            allow_agent=not password,
            look_for_keys=not password,
            compress=True,
            timeout=20,
            banner_timeout=20,
            auth_timeout=20,
        )
        self.client = client
        self.sftp = client.open_sftp()

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
        self.connect()
        self.ensure_not_cancelled()
        transport = self.client.get_transport()
        if transport is None:
            raise RemoteCommandError("SSH 连接已断开。")
        channel = transport.open_session()
        channel.set_combine_stderr(True)
        command = self.build_shell_command(script, use_bash=use_bash, env=env)
        self._active_channel = channel
        raw_output = io.StringIO()
        try:
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
        except TaskCancelledError:
            try:
                channel.close()
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                channel.close()
            except Exception:
                pass
            raise RemoteCommandError(str(exc)) from exc
        finally:
            self._active_channel = None
        output = raw_output.getvalue().strip()
        if check and exit_code != 0:
            raise RemoteCommandError(output or f"远端命令失败，退出码：{exit_code}")
        return CommandResult(exit_code=exit_code, output=output)

    def probe_remote_system(self) -> RemoteSystemInfo:
        result = self.run_script(PROBE_REMOTE_SYSTEM_SCRIPT)
        info = RemoteSystemInfo.from_output(result.output)
        self.remote_info = info
        if not self.remote_cache_root:
            remote_home = info.home or "~"
            self.remote_cache_root = f"{remote_home}/.cache/ptbd-remote"
        return info

    def ensure_remote_system_deps(self) -> None:
        result = self.run_script(ENSURE_REMOTE_SYSTEM_DEPS_SCRIPT, stream_output=True, check=False)
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

    def download_local_bundle(self) -> None:
        bundle_root = self.app_root / "third_party" / "bundle" / "linux-amd64"
        self.log(f"[gui] 本地 linux bundle 缺失，尝试下载：{BUNDLE_DOWNLOAD_URL}")
        with tempfile.TemporaryDirectory(prefix="ptbd-bundle-download-") as temp_dir:
            archive_path = Path(temp_dir) / "PT-BDtool-linux-amd64.tar.gz"
            with urllib.request.urlopen(BUNDLE_DOWNLOAD_URL, timeout=120) as response, archive_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)

            stage_root = Path(temp_dir) / "linux-amd64"
            with tarfile.open(archive_path, "r:gz") as archive:
                extracted = 0
                for member in archive.getmembers():
                    relative_path = bundle_member_relative_path(member.name)
                    if relative_path is None:
                        continue
                    target_path = stage_root / relative_path
                    if member.isdir():
                        target_path.mkdir(parents=True, exist_ok=True)
                        extracted += 1
                        continue
                    if not member.isfile():
                        continue
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    source_handle = archive.extractfile(member)
                    if source_handle is None:
                        raise RemoteCommandError(f"bundle 资产内容损坏：{member.name}")
                    with source_handle, target_path.open("wb") as output_handle:
                        while True:
                            chunk = source_handle.read(1024 * 1024)
                            if not chunk:
                                break
                            output_handle.write(chunk)
                    try:
                        os.chmod(target_path, member.mode)
                    except OSError:
                        pass
                    extracted += 1
            if extracted == 0:
                raise RemoteCommandError("bundle 资产里没有 third_party/bundle/linux-amd64")
            bundle_root.parent.mkdir(parents=True, exist_ok=True)
            if bundle_root.exists():
                shutil.rmtree(bundle_root, ignore_errors=True)
            stage_root.rename(bundle_root)
        self.log(f"[gui] 本地 linux bundle 已就绪：{bundle_root}")

    def ensure_local_bundle(self) -> None:
        if self.local_bundle_ready():
            return
        self.download_local_bundle()
        if not self.local_bundle_ready():
            raise RemoteCommandError("本地 bundle 拉取结束，但文件仍不完整。")

    def require_local_runtime_files(self, archive_mode: str) -> None:
        required = [
            self.app_root / "bdtool",
            self.app_root / "bdtool.sh",
            self.app_root / "lib" / "ui.sh",
        ]
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
            (self.app_root / "bdtool", "bdtool"),
            (self.app_root / "bdtool.sh", "bdtool.sh"),
            (self.app_root / "lib" / "ui.sh", "lib/ui.sh"),
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
        digest.update(b"\0")
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
                with source_path.open("rb") as handle:
                    while True:
                        chunk = handle.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
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
                handle.add(source_path, arcname=relative_path, recursive=False, filter=normalize_tar_info)
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
            self.sftp.put(str(archive_path), remote_archive)
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
chmod +x "$work_dir/bdtool" "$work_dir/bdtool.sh"

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

cat > "$work_dir/ptbd-runtime" <<EOS
#!/usr/bin/env bash
set -euo pipefail
RUNTIME_DIR=$runtime_dir
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
        scan_include = self.config.get("scan_include", "")
        scan_exclude = self.config.get("scan_exclude", "")
        if scan_include:
            env["BDTOOL_SCAN_INCLUDE_ROOTS"] = scan_include
        if scan_exclude:
            env["BDTOOL_SCAN_EXCLUDE_ROOTS"] = scan_exclude
        return env

    def scan_items(self) -> list[dict]:
        remote_cmd = self.resolve_remote_command()
        result = self.run_script(
            f"exec {quote_sh(remote_cmd)} scan-json --full --lang zh",
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
        self.sftp.get(remote_package, str(local_path))
        self.log(f"[gui] 本机已收到结果：{local_path}")
        return local_path

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
            f"exec {quote_sh(remote_cmd)} generate-path --path {quote_sh(selected_path)} --lang zh",
            use_bash=True,
            env=env,
            stream_output=True,
            check=False,
        )
        if result.exit_code != 0:
            raise RemoteCommandError(result.output or f"远端处理失败，退出码：{result.exit_code}")
        remote_package = self.extract_download_path(result.output) or self.find_remote_package(stage_dir)
        if not remote_package:
            raise RemoteCommandError("远端任务成功结束了，但没有找到可下载结果包。")
        local_path = self.download_result(remote_package, save_dir)
        self.cleanup_remote_package(remote_package, stage_dir)
        return local_path
