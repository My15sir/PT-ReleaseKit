from __future__ import annotations

import ipaddress
import json
import os
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from .models import AppConfig, ImageHostProvider


HTTP_TIMEOUT_SECONDS = 10
HTTP_MAX_ATTEMPTS = 3
HTTP_RETRY_DELAY_SECONDS = 0.5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_IMAGE_COUNT = 256
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 256 * 1024 * 1024
COPY_BUFFER_BYTES = 1024 * 1024
ALLOW_INSECURE_HTTP_ENV = "PTBD_ALLOW_INSECURE_IMAGE_HOST"

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
DEFAULT_ENDPOINTS = {
    ImageHostProvider.IMGBB: "https://api.imgbb.com/1/upload",
    ImageHostProvider.LSKY_V2: "",
    ImageHostProvider.SEE: "https://s.ee/api/v1/file/upload",
    ImageHostProvider.CUSTOM: "",
}

METADATA_JSON_NAME = "image-host.json"
METADATA_LINKS_NAME = "image-host-links.txt"
METADATA_BBCODE_NAME = "image-host-bbcode.txt"


class ImageHostError(RuntimeError):
    """A sanitized image-host error that never contains credentials or response bodies."""


class ImageHostCancelledError(ImageHostError):
    """Raised when an image-host upload is cancelled by the controller."""


@dataclass(frozen=True)
class ImageUploadResult:
    archive_path: str
    url: str = ""
    error: str = ""

    @property
    def success(self) -> bool:
        return bool(self.url) and not self.error

    def to_dict(self) -> dict[str, Any]:
        return {
            "archive_path": self.archive_path,
            "success": self.success,
            "url": self.url,
            "error": self.error,
        }


@dataclass(frozen=True)
class ImageHostReport:
    enabled: bool
    provider: str
    archive: str
    results: tuple[ImageUploadResult, ...] = ()
    archive_updated: bool = False
    error: str = ""
    cancelled: bool = False

    @property
    def urls(self) -> tuple[str, ...]:
        return tuple(item.url for item in self.results if item.success)

    @property
    def attempted_count(self) -> int:
        return len(self.results)

    @property
    def success_count(self) -> int:
        return len(self.urls)

    @property
    def failed_count(self) -> int:
        return sum(not item.success for item in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "archive": self.archive,
            "archive_updated": self.archive_updated,
            "error": self.error,
            "cancelled": self.cancelled,
            "attempted_count": self.attempted_count,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "urls": list(self.urls),
            "results": [item.to_dict() for item in self.results],
        }


@dataclass(frozen=True)
class _ImageHostSettings:
    enabled: bool
    provider: ImageHostProvider
    endpoint: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class _ArchiveImage:
    info: zipfile.ZipInfo
    error: str = ""


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _settings_from_config(config: AppConfig | Mapping[str, Any]) -> _ImageHostSettings:
    if isinstance(config, AppConfig):
        return _ImageHostSettings(
            enabled=config.image_host_enabled,
            provider=config.image_host_provider,
            endpoint=config.image_host_endpoint,
            token=config.image_host_token,
        )

    enabled = _as_bool(config.get("image_host_enabled", False))
    raw_provider = str(config.get("image_host_provider", "imgbb")).strip().lower()
    if not enabled and raw_provider not in {item.value for item in ImageHostProvider}:
        raw_provider = ImageHostProvider.IMGBB.value
    try:
        provider = ImageHostProvider(raw_provider)
    except ValueError as exc:
        raise ImageHostError("unsupported image-host provider") from exc
    endpoint = str(config.get("image_host_endpoint", "")).strip()
    token = str(config.get("image_host_token", ""))
    if not enabled:
        return _ImageHostSettings(False, provider, endpoint, token)
    if any(character in token for character in ("\x00", "\r", "\n")):
        raise ImageHostError("image-host token contains a forbidden control character")
    return _ImageHostSettings(enabled, provider, endpoint, token)


def _safe_endpoint(settings: _ImageHostSettings) -> str:
    endpoint = settings.endpoint or DEFAULT_ENDPOINTS[settings.provider]
    if not endpoint:
        raise ImageHostError("image-host endpoint is required")
    if any(character.isspace() or ord(character) < 32 for character in endpoint) or len(endpoint) > 4096:
        raise ImageHostError("image-host endpoint is invalid")
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ImageHostError("image-host endpoint is invalid") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ImageHostError("image-host endpoint must be an HTTP(S) URL without credentials or fragments")
    allow_insecure_http = _as_bool(os.environ.get(ALLOW_INSECURE_HTTP_ENV, ""))
    if parsed.scheme.lower() == "http" and not _is_loopback_hostname(hostname) and not allow_insecure_http:
        raise ImageHostError("image-host endpoint must use HTTPS unless it targets loopback")
    return endpoint


def _is_loopback_hostname(hostname: str) -> bool:
    normalized = hostname.lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _safe_public_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip()
    if (
        not url
        or len(url) > 4096
        or "[" in url
        or "]" in url
        or any(character.isspace() or ord(character) < 32 for character in url)
    ):
        return ""
    try:
        parsed = urllib.parse.urlsplit(url)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return url


def _value_at_path(payload: Any, path: tuple[str, ...]) -> Any:
    current = payload
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _find_safe_url(payload: Any) -> str:
    remaining = 2048

    def visit(value: Any, depth: int) -> str:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 12:
            return ""
        direct = _safe_public_url(value)
        if direct:
            return direct
        if isinstance(value, Mapping):
            priority = ("url", "link", "src", "image", "data")
            keys = [key for key in priority if key in value]
            keys.extend(key for key in value if key not in priority)
            for key in keys:
                found = visit(value[key], depth + 1)
                if found:
                    return found
        elif isinstance(value, (list, tuple)):
            for item in value:
                found = visit(item, depth + 1)
                if found:
                    return found
        return ""

    return visit(payload, 0)


def _multipart_body(field_name: str, filename: str, content_type: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"ptbd-{uuid.uuid4().hex}"
    safe_filename = Path(filename).name.replace('"', "_")
    safe_filename = "".join(character if 32 <= ord(character) < 127 else "_" for character in safe_filename)
    safe_filename = safe_filename or "image"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("ascii")
    body = header + content + f"\r\n--{boundary}--\r\n".encode("ascii")
    return body, f"multipart/form-data; boundary={boundary}"


def _request_for_image(
    settings: _ImageHostSettings,
    filename: str,
    content: bytes,
) -> urllib.request.Request:
    endpoint = _safe_endpoint(settings)
    if not settings.token:
        raise ImageHostError("image-host token is required")

    headers = {"Accept": "application/json", "User-Agent": "PT-ReleaseKit/1"}
    if settings.provider == ImageHostProvider.IMGBB:
        parsed = urllib.parse.urlsplit(endpoint)
        query = [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query) if key != "key"]
        query.append(("key", settings.token))
        endpoint = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
        field_name = "image"
    elif settings.provider == ImageHostProvider.SEE:
        field_name = "smfile"
        headers["Authorization"] = settings.token
    else:
        field_name = "file"
        headers["Authorization"] = f"Bearer {settings.token}"

    suffix = Path(filename).suffix.lower()
    body, multipart_type = _multipart_body(
        field_name,
        filename,
        IMAGE_CONTENT_TYPES.get(suffix, "application/octet-stream"),
        content,
    )
    headers["Content-Type"] = multipart_type
    headers["Content-Length"] = str(len(body))
    return urllib.request.Request(endpoint, data=body, headers=headers, method="POST")


def _read_response(response: Any) -> Any:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ImageHostError("image-host response is too large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ImageHostError("image-host response is not valid JSON") from exc


def _response_url(provider: ImageHostProvider, payload: Any) -> str:
    paths = {
        ImageHostProvider.IMGBB: ("data", "url"),
        ImageHostProvider.LSKY_V2: ("data", "links", "url"),
        ImageHostProvider.SEE: ("data", "url"),
    }
    if provider == ImageHostProvider.CUSTOM:
        url = _find_safe_url(payload)
    else:
        url = _safe_public_url(_value_at_path(payload, paths[provider]))
    if not url:
        raise ImageHostError("image-host response does not contain a safe image URL")
    return url


def _is_retryable_status(status: int) -> bool:
    return status in {408, 425, 429} or 500 <= status <= 599


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _open_request(request: urllib.request.Request, *, timeout: float) -> Any:
    # Never forward bearer headers or query-string API keys to a redirect target.
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def _raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise ImageHostCancelledError("image-host upload was cancelled")


def _sleep_before_retry(should_cancel: Callable[[], bool] | None) -> None:
    _raise_if_cancelled(should_cancel)
    time.sleep(HTTP_RETRY_DELAY_SECONDS)
    _raise_if_cancelled(should_cancel)


def upload_image(
    content: bytes,
    filename: str,
    config: AppConfig | Mapping[str, Any],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    """Upload one image and return a validated public HTTP(S) URL.

    All raised errors are deliberately sanitized; request URLs, response bodies and
    credentials are never included in exception messages.
    """

    settings = _settings_from_config(config)
    if not settings.enabled:
        raise ImageHostError("image-host uploading is disabled")
    _raise_if_cancelled(should_cancel)
    if not isinstance(content, bytes) or not content:
        raise ImageHostError("image is empty")
    if len(content) > MAX_IMAGE_BYTES:
        raise ImageHostError("image exceeds the upload size limit")
    try:
        request = _request_for_image(settings, filename, content)
    except ImageHostError:
        raise
    except Exception:
        raise ImageHostError("image-host request could not be created") from None

    for attempt in range(HTTP_MAX_ATTEMPTS):
        response = None
        try:
            _raise_if_cancelled(should_cancel)
            response = _open_request(request, timeout=HTTP_TIMEOUT_SECONDS)
            _raise_if_cancelled(should_cancel)
            status = int(getattr(response, "status", 200) or 200)
            if status >= 400:
                if _is_retryable_status(status) and attempt + 1 < HTTP_MAX_ATTEMPTS:
                    _sleep_before_retry(should_cancel)
                    continue
                raise ImageHostError(f"image-host service returned HTTP {status}")
            url = _response_url(settings.provider, _read_response(response))
            _raise_if_cancelled(should_cancel)
            return url
        except urllib.error.HTTPError as exc:
            _raise_if_cancelled(should_cancel)
            status = int(exc.code)
            if _is_retryable_status(status) and attempt + 1 < HTTP_MAX_ATTEMPTS:
                _sleep_before_retry(should_cancel)
                continue
            raise ImageHostError(f"image-host service returned HTTP {status}") from None
        except ImageHostCancelledError:
            raise
        except ImageHostError:
            raise
        except (TimeoutError, urllib.error.URLError, OSError):
            _raise_if_cancelled(should_cancel)
            if attempt + 1 < HTTP_MAX_ATTEMPTS:
                _sleep_before_retry(should_cancel)
                continue
            raise ImageHostError("image-host service is unavailable") from None
        except Exception:
            raise ImageHostError("image-host request failed") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    raise ImageHostError("image-host service is unavailable")


def _safe_archive_name(name: str) -> bool:
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or any(ord(character) < 32 for character in name)
    ):
        return False
    raw_parts = name[:-1].split("/") if name.endswith("/") else name.split("/")
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        return False
    return not raw_parts[0].endswith(":")


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK


def _archive_images(handle: zipfile.ZipFile) -> tuple[_ArchiveImage, ...]:
    infos = handle.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise ImageHostError("archive contains too many entries")
    names: set[str] = set()
    candidates: list[_ArchiveImage] = []
    archive_size = 0
    total_size = 0
    for info in infos:
        if not _safe_archive_name(info.filename):
            raise ImageHostError("archive contains an unsafe entry name")
        if info.filename in names:
            raise ImageHostError("archive contains duplicate entry names")
        names.add(info.filename)
        archive_size += max(info.file_size, 0)
        if archive_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ImageHostError("archive exceeds the uncompressed size limit")
        if info.is_dir() or PurePosixPath(info.filename).suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if len(candidates) >= MAX_IMAGE_COUNT:
            raise ImageHostError("archive contains too many images")
        total_size += max(info.file_size, 0)
        if total_size > MAX_TOTAL_IMAGE_BYTES:
            raise ImageHostError("archive images exceed the total size limit")
        error = ""
        if _is_symlink(info):
            error = "image entry is a symbolic link"
        elif info.flag_bits & 0x1:
            error = "image entry is encrypted"
        elif info.file_size <= 0:
            error = "image is empty"
        elif info.file_size > MAX_IMAGE_BYTES:
            error = "image exceeds the upload size limit"
        candidates.append(_ArchiveImage(info, error))
    return tuple(candidates)


def _read_archive_image(handle: zipfile.ZipFile, image: _ArchiveImage) -> bytes:
    try:
        with handle.open(image.info, "r") as stream:
            content = stream.read(MAX_IMAGE_BYTES + 1)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ImageHostError("image could not be read from the archive") from exc
    if not content:
        raise ImageHostError("image is empty")
    if len(content) > MAX_IMAGE_BYTES:
        raise ImageHostError("image exceeds the upload size limit")
    return content


def _metadata_names(images: tuple[_ArchiveImage, ...]) -> tuple[str, str, str]:
    parents = {PurePosixPath(image.info.filename).parts[0] for image in images}
    nested = all(len(PurePosixPath(image.info.filename).parts) > 1 for image in images)
    prefix = f"{next(iter(parents))}/" if len(parents) == 1 and nested else ""
    return (
        prefix + METADATA_JSON_NAME,
        prefix + METADATA_LINKS_NAME,
        prefix + METADATA_BBCODE_NAME,
    )


def _metadata_payloads(report: ImageHostReport) -> tuple[bytes, bytes, bytes]:
    metadata = report.to_dict()
    # The archive may be shared publicly; do not embed the controller's local
    # absolute filesystem path in its metadata.
    metadata["archive"] = Path(report.archive).name
    json_payload = json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    links = "".join(f"{url}\n" for url in report.urls).encode("utf-8")
    bbcode = "".join(f"[img]{url}[/img]\n" for url in report.urls).encode("utf-8")
    return json_payload, links, bbcode


def _atomic_add_metadata(
    archive: Path,
    names: tuple[str, str, str],
    payloads: tuple[bytes, bytes, bytes],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    _raise_if_cancelled(should_cancel)
    archive_mode = stat.S_IMODE(archive.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{archive.name}.", suffix=".tmp", dir=archive.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(archive, "r") as source, zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as destination:
            destination.comment = source.comment
            replaced = set(names)
            for info in source.infolist():
                _raise_if_cancelled(should_cancel)
                if info.filename in replaced:
                    continue
                if info.is_dir():
                    destination.writestr(info, b"")
                    continue
                with source.open(info, "r") as input_stream, destination.open(info, "w") as output_stream:
                    while True:
                        _raise_if_cancelled(should_cancel)
                        chunk = input_stream.read(COPY_BUFFER_BYTES)
                        if not chunk:
                            break
                        output_stream.write(chunk)
            for name, payload in zip(names, payloads):
                _raise_if_cancelled(should_cancel)
                destination.writestr(name, payload, compress_type=zipfile.ZIP_DEFLATED)

        _raise_if_cancelled(should_cancel)
        os.chmod(temporary, archive_mode)
        # Windows rejects fsync on a read-only descriptor (EBADF).  The
        # temporary archive is ours, so open it read/write before syncing.
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        _raise_if_cancelled(should_cancel)
        os.replace(temporary, archive)
        try:
            directory_descriptor = os.open(archive.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def upload_archive_images(
    archive: str | os.PathLike[str],
    config: AppConfig | Mapping[str, Any],
    *,
    should_cancel: Callable[[], bool] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> ImageHostReport:
    """Upload generated images and atomically embed links in the same ZIP.

    The feature is closed by default. Individual upload/read failures are returned
    in ``results`` and never abort the other images. Fatal configuration or archive
    errors are returned in ``error`` without replacing the original archive.
    """

    archive_path = Path(archive).expanduser().absolute()
    provider_name = "imgbb"
    try:
        settings = _settings_from_config(config)
        provider_name = settings.provider.value
    except ImageHostError as exc:
        enabled = (
            config.image_host_enabled
            if isinstance(config, AppConfig)
            else _as_bool(config.get("image_host_enabled", False))
        )
        return ImageHostReport(enabled, provider_name, str(archive_path), error=str(exc))

    report = ImageHostReport(settings.enabled, provider_name, str(archive_path))
    if not settings.enabled:
        return report
    results: list[ImageUploadResult] = []
    try:
        _raise_if_cancelled(should_cancel)
        _safe_endpoint(settings)
        if not settings.token:
            raise ImageHostError("image-host token is required")
        if archive_path.is_symlink() or not archive_path.is_file() or archive_path.suffix.lower() != ".zip":
            raise ImageHostError("image-host metadata requires a regular ZIP archive")
        with zipfile.ZipFile(archive_path, "r") as handle:
            images = _archive_images(handle)
            if not images:
                raise ImageHostError("archive contains no supported images")
            total = len(images)
            _emit_progress(progress_callback, 0, total, f"准备上传 {total} 张图片")
            for index, image in enumerate(images, start=1):
                _raise_if_cancelled(should_cancel)
                name = image.info.filename
                _emit_progress(progress_callback, index - 1, total, f"正在上传 {index}/{total}: {name}")
                if image.error:
                    results.append(ImageUploadResult(name, error=image.error))
                    _emit_progress(progress_callback, index, total, f"上传失败 {index}/{total}: {name}")
                    continue
                try:
                    content = _read_archive_image(handle, image)
                    _raise_if_cancelled(should_cancel)
                    url = upload_image(content, name, config, should_cancel=should_cancel)
                except ImageHostCancelledError:
                    return replace(report, results=tuple(results), cancelled=True)
                except ImageHostError as exc:
                    _raise_if_cancelled(should_cancel)
                    results.append(ImageUploadResult(name, error=str(exc)))
                    _emit_progress(progress_callback, index, total, f"上传失败 {index}/{total}: {name}")
                except Exception:
                    _raise_if_cancelled(should_cancel)
                    results.append(ImageUploadResult(name, error="unexpected image-host upload failure"))
                    _emit_progress(progress_callback, index, total, f"上传失败 {index}/{total}: {name}")
                else:
                    _raise_if_cancelled(should_cancel)
                    results.append(ImageUploadResult(name, url=url))
                    _emit_progress(progress_callback, index, total, f"上传完成 {index}/{total}: {name}")
    except ImageHostCancelledError:
        return replace(report, results=tuple(results), cancelled=True)
    except ImageHostError as exc:
        return replace(report, error=str(exc))
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
        return replace(report, error="archive could not be processed safely")

    report = replace(report, results=tuple(results))
    try:
        _raise_if_cancelled(should_cancel)
    except ImageHostCancelledError:
        return replace(report, cancelled=True)
    embedded_report = replace(report, archive_updated=True)
    try:
        _emit_progress(progress_callback, len(results), len(results), "正在写入图床链接到结果 ZIP")
        _atomic_add_metadata(
            archive_path,
            _metadata_names(images),
            _metadata_payloads(embedded_report),
            should_cancel=should_cancel,
        )
    except ImageHostCancelledError:
        return replace(report, cancelled=True)
    except Exception:
        return replace(report, error="failed to update image-host metadata in the archive")
    if should_cancel is not None and should_cancel():
        return replace(embedded_report, cancelled=True)
    return embedded_report


def _emit_progress(
    callback: Callable[[int, int, str], None] | None,
    completed: int,
    total: int,
    message: str,
) -> None:
    if callback is None:
        return
    try:
        callback(completed, total, message)
    except Exception:
        # Progress reporting is advisory and must not corrupt an otherwise valid ZIP.
        pass


__all__ = [
    "ALLOW_INSECURE_HTTP_ENV",
    "DEFAULT_ENDPOINTS",
    "HTTP_MAX_ATTEMPTS",
    "HTTP_RETRY_DELAY_SECONDS",
    "HTTP_TIMEOUT_SECONDS",
    "IMAGE_SUFFIXES",
    "ImageHostError",
    "ImageHostCancelledError",
    "ImageHostReport",
    "ImageUploadResult",
    "upload_archive_images",
    "upload_image",
]
