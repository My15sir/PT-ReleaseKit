from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable


CancelCallback = Callable[[], None]


@dataclass
class Job:
    kind: str
    max_log_lines: int = 2000
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "queued"
    message: str = ""
    logs: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    image_uploads: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    result_summary: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _cancel_callbacks: list[CancelCallback] = field(default_factory=list, repr=False)
    _claimed_cancel_callbacks: list[CancelCallback] = field(default_factory=list, repr=False)

    def log(self, message: str) -> None:
        line = str(message).rstrip("\r\n")
        if not line:
            return
        stamp = time.strftime("%H:%M:%S")
        with self._lock:
            self.logs.append(f"[{stamp}] {line}")
            if len(self.logs) > self.max_log_lines:
                self.logs = self.logs[-self.max_log_lines :]

    def start(self) -> None:
        with self._lock:
            self.status = "running"
            self.started_at = time.time()
        self.log(f"{self.kind} task started")

    def finish(self, status: str, message: str) -> None:
        with self._lock:
            if self.cancel_event.is_set() and status != "cancelled":
                status = "cancelled"
                message = "任务已取消"
            self.status = status
            self.message = message
            self.ended_at = time.time()
            self._cancel_callbacks.clear()
        self.log(message)

    def add_cancel_callback(self, callback: CancelCallback) -> None:
        run_immediately = False
        with self._lock:
            if callback in self._cancel_callbacks or callback in self._claimed_cancel_callbacks:
                return
            if self.cancel_event.is_set():
                self._claimed_cancel_callbacks.append(callback)
                run_immediately = True
            else:
                self._cancel_callbacks.append(callback)
        if run_immediately:
            self._invoke_cancel_callback(callback)

    def remove_cancel_callback(self, callback: CancelCallback) -> None:
        with self._lock:
            if callback in self._cancel_callbacks:
                self._cancel_callbacks.remove(callback)

    def cancel(self) -> None:
        with self._lock:
            if self.status not in {"queued", "running"}:
                return
            if self.cancel_event.is_set():
                return
            self.cancel_event.set()
            callbacks = list(reversed(self._cancel_callbacks))
            self._cancel_callbacks.clear()
            self._claimed_cancel_callbacks.extend(callbacks)
        for callback in callbacks:
            self._invoke_cancel_callback(callback)

    @staticmethod
    def _invoke_cancel_callback(callback: CancelCallback) -> None:
        try:
            callback()
        except Exception:
            pass

    def set_items(self, items: list[dict[str, Any]]) -> None:
        with self._lock:
            self.items = list(items)

    def add_output(self, output: str) -> None:
        with self._lock:
            self.outputs.append(str(output))

    def add_image_upload(self, report: Mapping[str, Any]) -> None:
        with self._lock:
            self.image_uploads.append(deepcopy(dict(report)))

    def add_failure(self, path: str, error: str) -> None:
        with self._lock:
            self.failed.append({"path": str(path), "error": str(error)})

    def set_result_summary(self, summary: Mapping[str, Any]) -> None:
        with self._lock:
            self.result_summary = deepcopy(dict(summary))

    def set_progress(self, progress: Mapping[str, Any]) -> None:
        with self._lock:
            self.progress = deepcopy(dict(progress))

    def summarize_batch(self, total: int) -> dict[str, Any]:
        if total < 0:
            raise ValueError("batch total must not be negative")
        with self._lock:
            summary = {
                "success": len(self.outputs),
                "failed": len(self.failed),
                "total": total,
                "outputs": list(self.outputs),
                "failed_items": [dict(item) for item in self.failed],
            }
            self.result_summary = summary
            return deepcopy(summary)

    def to_public(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "message": self.message,
                "logs": list(self.logs),
                "items": deepcopy(self.items),
                "outputs": list(self.outputs),
                "image_uploads": deepcopy(self.image_uploads),
                "failed": deepcopy(self.failed),
                "result_summary": deepcopy(self.result_summary),
                "progress": deepcopy(self.progress),
                "created_at": self.created_at,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
            }


class JobRegistry:
    def __init__(self, *, max_completed: int = 100) -> None:
        self.max_completed = max(1, max_completed)
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.RLock()

    def reserve(self, kind: str) -> tuple[Job | None, Job | None]:
        """Atomically reserve the single active job slot."""
        with self._lock:
            active = self._active_locked()
            if active is not None:
                return None, active
            job = Job(kind=kind)
            self._jobs[job.id] = job
            self._prune_locked()
            return job, None

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active(self) -> Job | None:
        with self._lock:
            return self._active_locked()

    def active_jobs(self) -> list[Job]:
        with self._lock:
            return [job for job in self._jobs.values() if job.status in {"queued", "running"}]

    def _active_locked(self) -> Job | None:
        for job in self._jobs.values():
            if job.status in {"queued", "running"}:
                return job
        return None

    def _prune_locked(self) -> None:
        completed = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status not in {"queued", "running"}
        ]
        for job_id in completed[: -self.max_completed]:
            self._jobs.pop(job_id, None)
