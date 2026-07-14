from __future__ import annotations

import threading
import unittest

from ptbd_core.jobs import JobRegistry


class JobRegistryTests(unittest.TestCase):
    def test_reserve_is_atomic_for_single_active_slot(self) -> None:
        registry = JobRegistry()
        first, active = registry.reserve("scan")
        self.assertIsNotNone(first)
        self.assertIsNone(active)

        second, active = registry.reserve("process")
        self.assertIsNone(second)
        self.assertIs(active, first)

        assert first is not None
        first.finish("success", "done")
        second, active = registry.reserve("process")
        self.assertIsNotNone(second)
        self.assertIsNone(active)

    def test_cancel_callbacks_are_invoked(self) -> None:
        registry = JobRegistry()
        job, _ = registry.reserve("scan")
        assert job is not None
        calls: list[str] = []
        job.add_cancel_callback(lambda: calls.append("cancelled"))

        job.cancel()

        self.assertTrue(job.cancel_event.is_set())
        self.assertEqual(calls, ["cancelled"])

    def test_callback_added_after_cancel_runs_immediately_once(self) -> None:
        registry = JobRegistry()
        job, _ = registry.reserve("scan")
        assert job is not None
        calls: list[str] = []

        def callback() -> None:
            calls.append("cancelled")

        job.cancel()
        job.add_cancel_callback(callback)

        self.assertEqual(calls, ["cancelled"])
        job.add_cancel_callback(callback)
        job.cancel()
        self.assertEqual(calls, ["cancelled"])

    def test_concurrent_cancel_is_idempotent(self) -> None:
        registry = JobRegistry()
        job, _ = registry.reserve("scan")
        assert job is not None
        calls = 0
        calls_lock = threading.Lock()
        worker_count = 16
        barrier = threading.Barrier(worker_count)

        def callback() -> None:
            nonlocal calls
            with calls_lock:
                calls += 1

        def cancel() -> None:
            barrier.wait()
            job.cancel()

        job.add_cancel_callback(callback)
        workers = [threading.Thread(target=cancel) for _ in range(worker_count)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(calls, 1)

    def test_concurrent_registration_after_cancel_invokes_callback_once(self) -> None:
        registry = JobRegistry()
        job, _ = registry.reserve("scan")
        assert job is not None
        calls = 0
        calls_lock = threading.Lock()
        worker_count = 16
        barrier = threading.Barrier(worker_count)

        def callback() -> None:
            nonlocal calls
            with calls_lock:
                calls += 1

        def register() -> None:
            barrier.wait()
            job.add_cancel_callback(callback)

        job.cancel()
        workers = [threading.Thread(target=register) for _ in range(worker_count)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(calls, 1)

    def test_batch_failures_and_summary_are_public_snapshots(self) -> None:
        registry = JobRegistry()
        job, _ = registry.reserve("process")
        assert job is not None
        job.add_output("/output/one.zip")
        job.add_failure("/media/two.mkv", "ffprobe failed")

        summary = job.summarize_batch(2)
        public = job.to_public()

        self.assertEqual(
            summary,
            {
                "success": 1,
                "failed": 1,
                "total": 2,
                "outputs": ["/output/one.zip"],
                "failed_items": [{"path": "/media/two.mkv", "error": "ffprobe failed"}],
            },
        )
        self.assertEqual(public["failed"], summary["failed_items"])
        self.assertEqual(public["result_summary"], summary)

        public["failed"][0]["error"] = "changed"
        public["result_summary"]["success"] = 99
        public["result_summary"]["failed_items"][0]["error"] = "also changed"
        fresh = job.to_public()
        self.assertEqual(fresh["failed"][0]["error"], "ffprobe failed")
        self.assertEqual(fresh["result_summary"]["success"], 1)
        self.assertEqual(fresh["result_summary"]["failed_items"][0]["error"], "ffprobe failed")

    def test_result_summary_is_copied_on_write(self) -> None:
        registry = JobRegistry()
        job, _ = registry.reserve("diagnose")
        assert job is not None
        report = {"ok": True, "message": "connected", "checks": [{"name": "ssh"}]}

        job.set_result_summary(report)
        report["ok"] = False
        report["checks"][0]["name"] = "changed"

        self.assertEqual(
            job.to_public()["result_summary"],
            {"ok": True, "message": "connected", "checks": [{"name": "ssh"}]},
        )

    def test_scan_progress_is_copied_on_write_and_read(self) -> None:
        registry = JobRegistry()
        job, _ = registry.reserve("scan")
        assert job is not None
        progress = {
            "phase": "walking",
            "directories_scanned": 3,
            "current_path": "/media/movies",
        }

        job.set_progress(progress)
        progress["directories_scanned"] = 99
        public = job.to_public()
        public["progress"]["directories_scanned"] = 100

        self.assertEqual(job.to_public()["progress"]["directories_scanned"], 3)

    def test_failures_can_be_recorded_concurrently(self) -> None:
        registry = JobRegistry()
        job, _ = registry.reserve("process")
        assert job is not None
        worker_count = 32

        workers = [
            threading.Thread(target=job.add_failure, args=(f"/media/{index}", f"error {index}"))
            for index in range(worker_count)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(len(job.to_public()["failed"]), worker_count)

    def test_batch_summary_rejects_negative_total(self) -> None:
        registry = JobRegistry()
        job, _ = registry.reserve("process")
        assert job is not None

        with self.assertRaises(ValueError):
            job.summarize_batch(-1)

    def test_registration_during_cancel_dispatch_runs_without_waiting_for_callback(self) -> None:
        registry = JobRegistry()
        job, _ = registry.reserve("scan")
        assert job is not None
        blocker_started = threading.Event()
        release_blocker = threading.Event()
        registration_done = threading.Event()
        late_calls: list[str] = []

        def blocker() -> None:
            blocker_started.set()
            release_blocker.wait(timeout=5)

        def late_callback() -> None:
            late_calls.append("cancelled")

        def register_late_callback() -> None:
            job.add_cancel_callback(late_callback)
            registration_done.set()

        job.add_cancel_callback(blocker)
        cancel_worker = threading.Thread(target=job.cancel)
        cancel_worker.start()
        self.assertTrue(blocker_started.wait(timeout=5))

        register_worker = threading.Thread(target=register_late_callback)
        register_worker.start()
        registered_without_waiting = registration_done.wait(timeout=2)
        release_blocker.set()
        register_worker.join(timeout=5)
        cancel_worker.join(timeout=5)

        self.assertTrue(registered_without_waiting)
        self.assertFalse(register_worker.is_alive())
        self.assertFalse(cancel_worker.is_alive())
        self.assertEqual(late_calls, ["cancelled"])


if __name__ == "__main__":
    unittest.main()
