from __future__ import annotations

import json
import tempfile
import tarfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import ptbd_remote_backend as backend


class FakeSSHException(Exception):
    pass


class FakeBadHostKeyException(FakeSSHException):
    pass


class FakeKey:
    def get_name(self) -> str:
        return "ssh-ed25519"

    def asbytes(self) -> bytes:
        return b"server-public-key"


class FakeSFTP:
    def __init__(self) -> None:
        self.closed = False
        self.put_calls: list[tuple[str, str]] = []
        self.get_calls: list[tuple[str, str]] = []

    def close(self) -> None:
        self.closed = True

    def put(self, local_path: str, remote_path: str) -> None:
        self.put_calls.append((local_path, remote_path))

    def get(self, remote_path: str, local_path: str) -> None:
        self.get_calls.append((remote_path, local_path))


class FakeTransferChannel:
    def __init__(self, download: bytes = b"") -> None:
        self.download = bytearray(download)
        self.sent = bytearray()
        self.command = ""
        self.timeout = None
        self.combine_stderr = None
        self.closed = False

    def settimeout(self, timeout: int) -> None:
        self.timeout = timeout

    def set_combine_stderr(self, combine: bool) -> None:
        self.combine_stderr = combine

    def exec_command(self, command: str) -> None:
        self.command = command

    def sendall(self, chunk: bytes) -> None:
        self.sent.extend(chunk)

    def shutdown_write(self) -> None:
        pass

    def exit_status_ready(self) -> bool:
        return not self.download

    def recv_ready(self) -> bool:
        return bool(self.download)

    def recv(self, size: int) -> bytes:
        chunk = bytes(self.download[:size])
        del self.download[:size]
        return chunk

    def recv_stderr_ready(self) -> bool:
        return False

    def recv_stderr(self, _size: int) -> bytes:
        return b""

    def recv_exit_status(self) -> int:
        return 0

    def close(self) -> None:
        self.closed = True


class FakeScanChannel(FakeTransferChannel):
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", *, completes: bool = True) -> None:
        super().__init__()
        self.stdout = bytearray(stdout)
        self.stderr = bytearray(stderr)
        self.completes = completes

    def recv_ready(self) -> bool:
        return bool(self.stdout)

    def recv(self, size: int) -> bytes:
        chunk = bytes(self.stdout[:size])
        del self.stdout[:size]
        return chunk

    def recv_stderr_ready(self) -> bool:
        return bool(self.stderr)

    def recv_stderr(self, size: int) -> bytes:
        chunk = bytes(self.stderr[:size])
        del self.stderr[:size]
        return chunk

    def exit_status_ready(self) -> bool:
        return self.completes and not self.stdout and not self.stderr


class FakeTransport:
    def __init__(self, channel: FakeTransferChannel | None = None) -> None:
        self.channel = channel or FakeTransferChannel()
        self.open_session_calls = 0

    def open_session(self, timeout=None) -> FakeTransferChannel:
        self.open_session_calls += 1
        self.channel.timeout = timeout
        return self.channel


class FakeSSHClient:
    def __init__(self, *, outcome: str = "success") -> None:
        self.outcome = outcome
        self.loaded_host_key_files: list[str | None] = []
        self.policy = None
        self.connect_kwargs = {}
        self.closed = False
        self.sftp = FakeSFTP()
        self.on_connect = None
        self.on_load_host_keys = None
        self.on_open_sftp = None
        self.sftp_error: Exception | None = None
        self.open_sftp_calls = 0
        self.transport = FakeTransport()

    def load_system_host_keys(self, filename: str | None = None) -> None:
        self.loaded_host_key_files.append(filename)
        if self.on_load_host_keys is not None:
            callback = self.on_load_host_keys
            self.on_load_host_keys = None
            callback()

    def set_missing_host_key_policy(self, policy) -> None:
        self.policy = policy

    def connect(self, **kwargs) -> None:
        self.connect_kwargs = kwargs
        if self.on_connect is not None:
            self.on_connect()
        if self.outcome == "unknown":
            self.policy.missing_host_key(self, "[example.test]:2202", FakeKey())
        if self.outcome == "changed":
            raise FakeBadHostKeyException("host key does not match")

    def open_sftp(self):
        self.open_sftp_calls += 1
        if self.on_open_sftp is not None:
            self.on_open_sftp()
        if self.sftp_error is not None:
            raise self.sftp_error
        return self.sftp

    def get_transport(self) -> FakeTransport:
        return self.transport

    def close(self) -> None:
        self.closed = True


class FakeParamiko:
    BadHostKeyException = FakeBadHostKeyException

    def __init__(self, client: FakeSSHClient) -> None:
        self.client = client

    def SSHClient(self) -> FakeSSHClient:
        return self.client


class RemoteBackendHostKeyTests(unittest.TestCase):
    def make_backend(self) -> backend.PTBDRemoteBackend:
        return backend.PTBDRemoteBackend(
            Path(__file__).resolve().parents[1],
            {
                "remote_host": "deploy@example.test",
                "remote_port": "2202",
                "remote_password": "secret",
            },
        )

    def connect_with_client(self, client: FakeSSHClient, system_file: Path | None = None):
        known_hosts = () if system_file is None else (system_file,)
        with (
            mock.patch.object(backend, "paramiko", FakeParamiko(client)),
            mock.patch.object(backend, "PARAMIKO_IMPORT_ERROR", None),
            mock.patch.object(backend, "system_known_hosts_files", return_value=known_hosts),
        ):
            controller = self.make_backend()
            controller.connect()
            return controller

    def test_full_scan_uses_explicit_root_in_remote_environment(self) -> None:
        controller = self.make_backend()
        controller.config["scan_full"] = True

        self.assertEqual(backend.build_effective_scan_include(controller.config), "/")
        self.assertEqual(controller.build_scan_env()["BDTOOL_SCAN_INCLUDE_ROOTS"], "/")

    def test_python_spectrum_modules_are_not_base_bundle_dependencies(self) -> None:
        info = backend.RemoteSystemInfo(
            has_tar=True,
            has_bash=True,
            has_python3=True,
            has_curl=True,
            has_ffmpeg=True,
            has_ffprobe=True,
            has_mediainfo=True,
            has_numpy=False,
            has_pil=False,
        )

        self.assertTrue(info.core_deps_ready())
        self.assertFalse(info.spectrum_python_deps_ready())

    def test_non_bootstrap_mode_uses_configured_remote_command(self) -> None:
        controller = self.make_backend()
        controller.config.update({"remote_bootstrap": False, "remote_cmd": "/srv/ptbd/bdtool"})
        self.assertEqual(controller.resolve_remote_command(), "/srv/ptbd/bdtool")

    def test_ready_remote_skips_dependency_install(self) -> None:
        controller = self.make_backend()
        controller.remote_cache_root = "/tmp/ptbd-test-cache"
        info = backend.RemoteSystemInfo(
            os_name="Linux",
            distro_id="debian",
            arch="x86_64",
            has_tar=True,
            has_bash=True,
            has_python3=True,
            has_curl=True,
            has_ffmpeg=True,
            has_ffprobe=True,
            has_mediainfo=True,
            has_numpy=True,
            has_pil=True,
        )
        archive = Path(tempfile.gettempdir()) / "ptbd-test-runtime.tar.gz"
        with (
            mock.patch.object(controller, "probe_remote_system", return_value=info),
            mock.patch.object(controller, "ensure_remote_system_deps") as install,
            mock.patch.object(controller, "build_runtime_archive", return_value=(archive, "hash")),
            mock.patch.object(controller, "remote_file_ready", return_value=True),
        ):
            self.assertEqual(controller.ensure_runtime(), "/tmp/ptbd-test-cache/runtime-hash/ptbd-runtime")
        install.assert_not_called()

    def test_preferred_scan_remains_bounded_when_full_scan_is_disabled(self) -> None:
        controller = self.make_backend()
        controller.config["scan_full"] = False

        self.assertEqual(backend.preferred_scan_roots_text(), "/home")
        self.assertEqual(backend.build_effective_scan_include(controller.config), "/home")

    def test_diagnose_reports_safe_default_root(self) -> None:
        controller = self.make_backend()
        remote_info = backend.RemoteSystemInfo(os_name="Linux", distro_id="debian", arch="x86_64")

        with (
            mock.patch.object(controller, "connect"),
            mock.patch.object(controller, "probe_remote_system", return_value=remote_info),
            mock.patch.object(controller, "ensure_sftp"),
        ):
            report = controller.diagnose_connection()

        self.assertTrue(report["ok"])
        self.assertEqual(report["scan_mode"], "preferred")
        self.assertEqual(report["scan_roots"], "/home")
        self.assertTrue(any("默认扫描优先目录：/home" in hint for hint in report["hints"]))

    def test_explicit_vps_roots_remain_available(self) -> None:
        controller = self.make_backend()
        controller.config.update(
            {
                "scan_full": False,
                "scan_include": "/root /data /mnt /media /srv",
            }
        )

        self.assertEqual(
            backend.build_effective_scan_include(controller.config),
            "/root /data /mnt /media /srv",
        )

    def test_diagnose_reports_explicit_whitelist_over_full_scan(self) -> None:
        controller = self.make_backend()
        controller.config.update({"scan_full": True, "scan_include": "/data/private"})
        remote_info = backend.RemoteSystemInfo(os_name="Linux", distro_id="debian", arch="x86_64")

        with (
            mock.patch.object(controller, "connect"),
            mock.patch.object(controller, "probe_remote_system", return_value=remote_info),
            mock.patch.object(
                controller,
                "ensure_sftp",
                side_effect=backend.RemoteCommandError("SFTP disabled"),
            ),
        ):
            report = controller.diagnose_connection()

        self.assertTrue(report["ok"])
        self.assertEqual(report["scan_mode"], "whitelist")
        self.assertEqual(report["scan_roots"], "/data/private")
        self.assertTrue(any("显式白名单" in hint for hint in report["hints"]))

    def test_connect_loads_user_and_system_known_hosts_and_uses_reject_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            system_file = Path(temporary) / "ssh_known_hosts"
            system_file.write_text("example.test ssh-ed25519 AAAA\n", encoding="utf-8")
            client = FakeSSHClient()

            controller = self.connect_with_client(client, system_file)

            self.assertEqual(client.loaded_host_key_files, [None, str(system_file)])
            self.assertIsInstance(client.policy, backend.RejectUnknownHostKeyPolicy)
            self.assertNotIn("AutoAdd", type(client.policy).__name__)
            self.assertEqual(client.connect_kwargs["hostname"], "example.test")
            self.assertEqual(client.connect_kwargs["port"], 2202)
            self.assertIs(controller.client, client)
            self.assertIsNone(controller.sftp)
            self.assertEqual(client.open_sftp_calls, 0)

    def test_sftp_is_opened_only_when_requested(self) -> None:
        client = FakeSSHClient()
        with (
            mock.patch.object(backend, "paramiko", FakeParamiko(client)),
            mock.patch.object(backend, "PARAMIKO_IMPORT_ERROR", None),
            mock.patch.object(backend, "system_known_hosts_files", return_value=()),
        ):
            controller = self.make_backend()
            controller.connect()
            controller.ensure_sftp()

        self.assertEqual(client.open_sftp_calls, 1)
        self.assertIs(controller.sftp, client.sftp)

    def test_unknown_host_key_is_rejected_with_first_connection_instructions(self) -> None:
        client = FakeSSHClient(outcome="unknown")
        with self.assertRaises(backend.RemoteCommandError) as raised:
            self.connect_with_client(client)

        message = str(raised.exception)
        self.assertIn("known_hosts", message)
        self.assertIn("ssh-ed25519 SHA256:", message)
        self.assertIn("ssh -p 2202 deploy@example.test", message)
        self.assertIn("核对", message)
        self.assertTrue(client.closed)

    def test_changed_host_key_is_rejected_without_suggesting_silent_replacement(self) -> None:
        client = FakeSSHClient(outcome="changed")
        with self.assertRaises(backend.RemoteCommandError) as raised:
            self.connect_with_client(client)

        message = str(raised.exception)
        self.assertIn("不一致", message)
        self.assertIn("中间人攻击", message)
        self.assertIn("不要直接删除", message)
        self.assertTrue(client.closed)

    def test_pre_cancelled_backend_never_starts_connection(self) -> None:
        client = FakeSSHClient()
        with (
            mock.patch.object(backend, "paramiko", FakeParamiko(client)),
            mock.patch.object(backend, "PARAMIKO_IMPORT_ERROR", None),
            mock.patch.object(backend, "system_known_hosts_files", return_value=()),
        ):
            controller = self.make_backend()
            controller.cancel()
            with self.assertRaises(backend.TaskCancelledError):
                controller.connect()

        self.assertEqual(client.connect_kwargs, {})

    def test_cancel_during_connect_closes_inflight_client(self) -> None:
        client = FakeSSHClient()
        with (
            mock.patch.object(backend, "paramiko", FakeParamiko(client)),
            mock.patch.object(backend, "PARAMIKO_IMPORT_ERROR", None),
            mock.patch.object(backend, "system_known_hosts_files", return_value=()),
        ):
            controller = self.make_backend()
            client.on_connect = controller.cancel
            with self.assertRaises(backend.TaskCancelledError):
                controller.connect()

        self.assertTrue(client.closed)
        self.assertIsNone(controller.client)
        self.assertIsNone(controller.sftp)

    def test_sftp_open_cancellation_does_not_enter_pipe_fallback(self) -> None:
        client = FakeSSHClient()
        with (
            mock.patch.object(backend, "paramiko", FakeParamiko(client)),
            mock.patch.object(backend, "PARAMIKO_IMPORT_ERROR", None),
            mock.patch.object(backend, "system_known_hosts_files", return_value=()),
            tempfile.TemporaryDirectory() as temporary,
        ):
            controller = self.make_backend()
            controller.connect()
            client.on_open_sftp = controller.cancel
            local_path = Path(temporary) / "upload.bin"
            local_path.write_bytes(b"payload")

            with self.assertRaises(backend.TaskCancelledError):
                controller.put_file(local_path, "/tmp/upload.bin")

        self.assertEqual(client.transport.open_session_calls, 0)
        self.assertTrue(client.sftp.closed)

    def test_diagnose_propagates_cancel_during_connect(self) -> None:
        controller = self.make_backend()

        with mock.patch.object(
            controller,
            "connect",
            side_effect=backend.TaskCancelledError("任务已取消。"),
        ):
            with self.assertRaises(backend.TaskCancelledError):
                controller.diagnose_connection()

    def test_diagnose_propagates_cancel_during_sftp_probe(self) -> None:
        controller = self.make_backend()
        remote_info = backend.RemoteSystemInfo(
            os_name="Linux",
            distro_id="debian",
            arch="x86_64",
            home="/home/deploy",
            has_tar=True,
            has_bash=True,
            has_python3=True,
            has_curl=True,
            has_ffmpeg=True,
            has_ffprobe=True,
            has_mediainfo=True,
            has_numpy=True,
            has_pil=True,
            has_bdinfo=True,
            has_bd_info=False,
        )

        with (
            mock.patch.object(controller, "connect"),
            mock.patch.object(controller, "probe_remote_system", return_value=remote_info),
            mock.patch.object(
                controller,
                "ensure_sftp",
                side_effect=backend.TaskCancelledError("任务已取消。"),
            ),
        ):
            with self.assertRaises(backend.TaskCancelledError):
                controller.diagnose_connection()

    def test_run_script_converts_closed_channel_eof_to_cancellation(self) -> None:
        controller = self.make_backend()
        channel = mock.Mock()
        stream = mock.Mock()
        stream.readline.side_effect = lambda: (controller.cancel(), "")[1]
        channel.makefile.return_value = stream
        channel.recv_exit_status.return_value = -1
        transport = mock.Mock()
        transport.open_session.return_value = channel
        client = mock.Mock()
        client.get_transport.return_value = transport
        controller.client = client

        with mock.patch.object(controller, "connect"):
            with self.assertRaises(backend.TaskCancelledError):
                controller.run_script("sleep 30", check=False)

    def test_run_script_converts_closed_channel_read_error_to_cancellation(self) -> None:
        controller = self.make_backend()
        channel = mock.Mock()
        stream = mock.Mock()

        def cancelled_readline() -> str:
            controller.cancel()
            raise OSError("channel closed")

        stream.readline.side_effect = cancelled_readline
        channel.makefile.return_value = stream
        transport = mock.Mock()
        transport.open_session.return_value = channel
        client = mock.Mock()
        client.get_transport.return_value = transport
        controller.client = client

        with mock.patch.object(controller, "connect"):
            with self.assertRaises(backend.TaskCancelledError):
                controller.run_script("sleep 30", check=False)

    def test_scan_stream_separates_progress_from_result_json(self) -> None:
        controller = self.make_backend()
        progress = {"phase": "walking", "directories_scanned": 3}
        channel = FakeScanChannel(
            stdout=b'{"items": []}\n',
            stderr=(
                backend.SCAN_PROGRESS_PREFIX.encode("utf-8")
                + json.dumps(progress).encode("utf-8")
                + b"\nremote warning\n"
            ),
        )
        client = FakeSSHClient()
        client.transport = FakeTransport(channel)
        controller.client = client
        events: list[dict] = []
        logs: list[str] = []
        controller.logger = logs.append

        with mock.patch.object(controller, "connect"):
            result = controller.run_scan_script(
                "bdtool scan-json --progress-json",
                progress_callback=events.append,
                overall_timeout=1,
                idle_timeout=1,
            )

        self.assertEqual(result.output, '{"items": []}')
        self.assertEqual(events, [progress])
        self.assertEqual(logs, ["remote warning"])
        self.assertFalse(channel.combine_stderr)
        self.assertTrue(channel.closed)

    def test_scan_stream_idle_timeout_closes_channel(self) -> None:
        controller = self.make_backend()
        channel = FakeScanChannel(completes=False)
        client = FakeSSHClient()
        client.transport = FakeTransport(channel)
        controller.client = client

        with mock.patch.object(controller, "connect"):
            with self.assertRaisesRegex(backend.RemoteCommandError, "没有任何进度"):
                controller.run_scan_script(
                    "bdtool scan-json --progress-json",
                    overall_timeout=1,
                    idle_timeout=0.05,
                )

        self.assertTrue(channel.closed)

    def test_scan_stream_timeout_interrupts_hung_exec_request(self) -> None:
        class HungExecChannel(FakeScanChannel):
            def __init__(self) -> None:
                super().__init__(completes=False)
                self.release = threading.Event()

            def exec_command(self, command: str) -> None:
                self.command = command
                self.release.wait(5)

            def close(self) -> None:
                self.release.set()
                super().close()

        controller = self.make_backend()
        channel = HungExecChannel()
        client = FakeSSHClient()
        client.transport = FakeTransport(channel)
        controller.client = client

        with mock.patch.object(controller, "connect"):
            with self.assertRaisesRegex(backend.RemoteCommandError, "没有任何进度"):
                controller.run_scan_script(
                    "bdtool scan-json --progress-json",
                    overall_timeout=1,
                    idle_timeout=0.05,
                )

        self.assertTrue(channel.closed)
        self.assertTrue(channel.release.wait(1))

    def test_scan_preparation_is_bounded_by_overall_timeout(self) -> None:
        controller = self.make_backend()

        def blocked_resolve() -> str:
            while not controller._cancelled:
                time.sleep(0.01)
            raise backend.TaskCancelledError("cancelled")

        with mock.patch.object(controller, "resolve_remote_command", side_effect=blocked_resolve):
            with self.assertRaisesRegex(backend.RemoteCommandError, "准备超过总时限"):
                controller.scan_items(overall_timeout=0.05, idle_timeout=0.05)

        self.assertTrue(controller._cancelled)

    def test_dependency_install_receives_audio_spectrum_mode(self) -> None:
        controller = self.make_backend()
        controller.config["audio_spectrum_mode"] = "combined"

        with (
            mock.patch.object(backend, "read_shared_asset", return_value="install-deps") as read_asset,
            mock.patch.object(
                controller,
                "run_script",
                return_value=backend.CommandResult(0, "status=ready"),
            ) as run_script,
        ):
            controller.ensure_remote_system_deps()

        read_asset.assert_called_once_with(controller.app_root, "ptbd_core/assets/remote-install-deps.sh")
        run_script.assert_called_once_with(
            "install-deps",
            env={"PTBD_AUDIO_SPECTRUM_MODE": "combined"},
            stream_output=True,
            check=False,
        )

    @staticmethod
    def checksum_response(payload: bytes):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = payload
        return response

    def test_explicit_bundle_digest_has_priority_over_sidecar(self) -> None:
        digest = "1" * 64
        controller = self.make_backend()

        with (
            mock.patch.object(backend, "BUNDLE_SHA256", digest),
            mock.patch.object(backend.urllib.request, "urlopen") as urlopen,
        ):
            self.assertEqual(controller.expected_bundle_checksum(), digest)

        urlopen.assert_not_called()

    def test_invalid_explicit_bundle_digest_cannot_be_bypassed(self) -> None:
        controller = self.make_backend()

        with (
            mock.patch.object(backend, "BUNDLE_SHA256", "invalid"),
            mock.patch.object(backend, "BUNDLE_ALLOW_UNVERIFIED", True),
            mock.patch.object(backend.urllib.request, "urlopen") as urlopen,
        ):
            with self.assertRaisesRegex(backend.RemoteCommandError, "PTBD_BUNDLE_SHA256 无效") as raised:
                controller.expected_bundle_checksum()

        self.assertIsInstance(raised.exception.__cause__, backend.BundleArchiveError)
        urlopen.assert_not_called()

    def test_valid_sidecar_is_preferred_over_official_bootstrap_digest(self) -> None:
        digest = "2" * 64
        controller = self.make_backend()

        with (
            mock.patch.object(backend, "BUNDLE_SHA256", ""),
            mock.patch.object(backend, "BUNDLE_DOWNLOAD_URL", backend.OFFICIAL_BUNDLE_URL),
            mock.patch.object(backend, "BUNDLE_CHECKSUM_URL", backend.OFFICIAL_CHECKSUM_URL),
            mock.patch.object(
                backend.urllib.request,
                "urlopen",
                return_value=self.checksum_response(f"{digest}  bundle.tar.gz\n".encode("ascii")),
            ),
        ):
            self.assertEqual(controller.expected_bundle_checksum(), digest)

    def test_missing_renamed_official_sidecar_fails_closed(self) -> None:
        unavailable = urllib.error.URLError("offline")
        controller = self.make_backend()

        with (
            mock.patch.object(backend, "BUNDLE_SHA256", ""),
            mock.patch.object(backend, "BUNDLE_DOWNLOAD_URL", backend.OFFICIAL_BUNDLE_URL),
            mock.patch.object(backend, "BUNDLE_CHECKSUM_URL", backend.OFFICIAL_CHECKSUM_URL),
            mock.patch.object(backend.urllib.request, "urlopen", side_effect=unavailable),
        ):
            with self.assertRaisesRegex(backend.RemoteCommandError, "checksum 不可用") as raised:
                controller.expected_bundle_checksum()
        self.assertIs(raised.exception.__cause__, unavailable)

    def test_missing_legacy_official_sidecar_uses_pinned_digest(self) -> None:
        unavailable = urllib.error.URLError("offline")
        controller = self.make_backend()

        with (
            mock.patch.object(backend, "BUNDLE_SHA256", ""),
            mock.patch.object(
                backend,
                "BUNDLE_DOWNLOAD_URL",
                backend.LEGACY_OFFICIAL_BUNDLE_URL,
            ),
            mock.patch.object(
                backend,
                "BUNDLE_CHECKSUM_URL",
                backend.LEGACY_OFFICIAL_CHECKSUM_URL,
            ),
            mock.patch.object(backend.urllib.request, "urlopen", side_effect=unavailable),
        ):
            self.assertEqual(
                controller.expected_bundle_checksum(),
                backend.OFFICIAL_BOOTSTRAP_SHA256,
            )

    def test_custom_bundle_cannot_reuse_official_bootstrap_digest(self) -> None:
        unavailable = urllib.error.URLError("offline")
        controller = self.make_backend()

        with (
            mock.patch.object(backend, "BUNDLE_SHA256", ""),
            mock.patch.object(backend, "BUNDLE_DOWNLOAD_URL", "https://example.invalid/custom.tar.gz"),
            mock.patch.object(
                backend,
                "BUNDLE_CHECKSUM_URL",
                "https://example.invalid/custom.tar.gz.sha256",
            ),
            mock.patch.object(backend, "BUNDLE_ALLOW_UNVERIFIED", False),
            mock.patch.object(backend.urllib.request, "urlopen", side_effect=unavailable),
        ):
            with self.assertRaisesRegex(backend.RemoteCommandError, "checksum 不可用") as raised:
                controller.expected_bundle_checksum()

        self.assertIs(raised.exception.__cause__, unavailable)

    def test_custom_checksum_url_disables_official_bootstrap_digest(self) -> None:
        unavailable = urllib.error.URLError("offline")
        controller = self.make_backend()

        with (
            mock.patch.object(backend, "BUNDLE_SHA256", ""),
            mock.patch.object(backend, "BUNDLE_DOWNLOAD_URL", backend.OFFICIAL_BUNDLE_URL),
            mock.patch.object(backend, "BUNDLE_CHECKSUM_URL", "https://example.invalid/custom.sha256"),
            mock.patch.object(backend, "BUNDLE_ALLOW_UNVERIFIED", False),
            mock.patch.object(backend.urllib.request, "urlopen", side_effect=unavailable),
        ):
            with self.assertRaisesRegex(backend.RemoteCommandError, "checksum 不可用"):
                controller.expected_bundle_checksum()

    def test_malformed_sidecar_cannot_be_bypassed_by_unverified_mode(self) -> None:
        controller = self.make_backend()

        with (
            mock.patch.object(backend, "BUNDLE_SHA256", ""),
            mock.patch.object(backend, "BUNDLE_ALLOW_UNVERIFIED", True),
            mock.patch.object(
                backend.urllib.request,
                "urlopen",
                return_value=self.checksum_response(b"not-a-digest\n"),
            ),
        ):
            with self.assertRaisesRegex(backend.RemoteCommandError, "checksum sidecar 无效") as raised:
                controller.expected_bundle_checksum()

        self.assertIsInstance(raised.exception.__cause__, backend.BundleArchiveError)

    def test_non_ascii_sidecar_preserves_decode_error_as_cause(self) -> None:
        controller = self.make_backend()

        with (
            mock.patch.object(backend, "BUNDLE_SHA256", ""),
            mock.patch.object(
                backend.urllib.request,
                "urlopen",
                return_value=self.checksum_response(b"\xff"),
            ),
        ):
            with self.assertRaisesRegex(backend.RemoteCommandError, "不是 ASCII") as raised:
                controller.expected_bundle_checksum()

        self.assertIsInstance(raised.exception.__cause__, UnicodeDecodeError)

    def test_custom_bundle_can_explicitly_allow_missing_checksum(self) -> None:
        unavailable = urllib.error.URLError("offline")
        controller = self.make_backend()

        with (
            mock.patch.object(backend, "BUNDLE_SHA256", ""),
            mock.patch.object(backend, "BUNDLE_DOWNLOAD_URL", "https://example.invalid/custom.tar.gz"),
            mock.patch.object(
                backend,
                "BUNDLE_CHECKSUM_URL",
                "https://example.invalid/custom.tar.gz.sha256",
            ),
            mock.patch.object(backend, "BUNDLE_ALLOW_UNVERIFIED", True),
            mock.patch.object(backend.urllib.request, "urlopen", side_effect=unavailable),
        ):
            self.assertIsNone(controller.expected_bundle_checksum())

    def test_bundle_archive_download_error_is_wrapped_with_original_cause(self) -> None:
        unavailable = urllib.error.URLError("offline")
        controller = self.make_backend()

        with mock.patch.object(backend.urllib.request, "urlopen", side_effect=unavailable):
            with self.assertRaisesRegex(backend.RemoteCommandError, "bundle 下载失败") as raised:
                controller.download_local_bundle()

        self.assertIs(raised.exception.__cause__, unavailable)

    def test_bundle_checksum_mismatch_is_wrapped_with_original_cause(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.side_effect = [b"archive-payload", b""]
        controller = self.make_backend()

        with (
            mock.patch.object(backend, "BUNDLE_SHA256", "0" * 64),
            mock.patch.object(backend.urllib.request, "urlopen", return_value=response),
        ):
            with self.assertRaisesRegex(backend.RemoteCommandError, "SHA256 校验失败") as raised:
                controller.download_local_bundle()

        self.assertIsInstance(raised.exception.__cause__, backend.BundleArchiveError)

    def test_file_transfer_falls_back_to_ssh_pipes_without_sftp(self) -> None:
        client = FakeSSHClient()
        logs: list[str] = []
        with (
            mock.patch.object(backend, "paramiko", FakeParamiko(client)),
            mock.patch.object(backend, "PARAMIKO_IMPORT_ERROR", None),
            mock.patch.object(backend, "system_known_hosts_files", return_value=()),
            tempfile.TemporaryDirectory() as temporary,
        ):
            controller = self.make_backend()
            controller.logger = logs.append
            controller.connect()
            client.sftp_error = OSError("SFTP disabled")

            upload = Path(temporary) / "upload.bin"
            upload.write_bytes(b"upload-payload")
            upload_channel = FakeTransferChannel()
            client.transport = FakeTransport(upload_channel)
            with mock.patch.object(
                controller,
                "run_script",
                return_value=backend.CommandResult(0, ""),
            ) as run_script:
                controller.put_file(upload, "/remote/cache/upload.bin")

            download = Path(temporary) / "download.bin"
            download_channel = FakeTransferChannel(b"download-payload")
            client.transport = FakeTransport(download_channel)
            controller.get_file("/remote/cache/download.bin", download)
            downloaded_payload = download.read_bytes()

        self.assertEqual(bytes(upload_channel.sent), b"upload-payload")
        self.assertEqual(upload_channel.command, "cat > '/remote/cache/upload.bin'")
        run_script.assert_called_once_with("mkdir -p '/remote/cache'", check=False)
        self.assertEqual(downloaded_payload, b"download-payload")
        self.assertEqual(download_channel.command, "cat '/remote/cache/download.bin'")
        self.assertTrue(any("回退 SSH 管道" in line for line in logs))

    def test_failed_download_removes_partial_file(self) -> None:
        class PartialSFTP:
            @staticmethod
            def get(_remote_path: str, local_path: str) -> None:
                Path(local_path).write_bytes(b"partial")
                raise OSError("SFTP interrupted")

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "result.zip"
            controller = self.make_backend()
            client = mock.Mock()
            client.get_transport.return_value = None
            controller.client = client
            controller.sftp = PartialSFTP()

            with (
                mock.patch.object(controller, "connect"),
                mock.patch.object(controller, "ensure_sftp"),
            ):
                with self.assertRaises(backend.RemoteCommandError):
                    controller.get_file("/remote/result.zip", destination)

            self.assertFalse(destination.exists())
            self.assertEqual(list(destination.parent.glob(".result.zip.*.part")), [])

    def test_runtime_archive_normalizes_text_members_to_lf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "bdtool"
            core_module = root / "core.py"
            binary = root / "payload.bin"
            launcher.write_bytes(b"#!/bin/sh\r\necho ok\r\n")
            core_module.write_bytes(b"value = 1\r\n")
            binary.write_bytes(b"\x00\r\n")
            controller = self.make_backend()
            with (
                mock.patch.object(controller, "require_local_runtime_files"),
                mock.patch.object(
                    controller,
                    "runtime_members",
                    return_value=[
                        (launcher, "bdtool"),
                        (core_module, "ptbd_core/core.py"),
                        (binary, "payload.bin"),
                    ],
                ),
            ):
                archive_path, _ = controller.build_runtime_archive("minimal")

            try:
                with tarfile.open(archive_path, "r:gz") as archive:
                    self.assertEqual(archive.extractfile("bdtool").read(), b"#!/bin/sh\necho ok\n")
                    self.assertEqual(archive.extractfile("ptbd_core/core.py").read(), b"value = 1\n")
                    self.assertEqual(archive.extractfile("payload.bin").read(), b"\x00\r\n")
                    self.assertTrue(archive.getmember("bdtool").mode & 0o111)
            finally:
                archive_path.unlink(missing_ok=True)
                archive_path.parent.rmdir()

    def test_cancel_during_known_hosts_load_never_starts_connection(self) -> None:
        client = FakeSSHClient()
        with (
            mock.patch.object(backend, "paramiko", FakeParamiko(client)),
            mock.patch.object(backend, "PARAMIKO_IMPORT_ERROR", None),
            mock.patch.object(backend, "system_known_hosts_files", return_value=()),
        ):
            controller = self.make_backend()
            client.on_load_host_keys = controller.cancel
            with self.assertRaises(backend.TaskCancelledError):
                controller.connect()

        self.assertEqual(client.connect_kwargs, {})
        self.assertTrue(client.closed)
        self.assertIsNone(controller.client)
        self.assertIsNone(controller.sftp)


if __name__ == "__main__":
    unittest.main()
