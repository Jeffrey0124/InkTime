import sqlite3
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from analysis_tasks import AnalysisTaskError, AnalysisTaskService
from analysis_worker import AnalysisWorker
from library_scanner import LibraryScanner
from settings_store import SettingsStore


class AnalysisQueueControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.library = self.root / "library"
        self.library.mkdir()
        self.db_path = self.root / "photos.db"
        self.store = SettingsStore(self.db_path)
        channel = self.store.save_channel(
            {
                "name": "Primary",
                "provider": "custom",
                "base_url": "https://example.com/v1",
                "credential": {"source": "none"},
                "models": [{"model_id": "vision-a", "is_default": True}],
            }
        )
        self.store.save_fallback_chain(
            [{"channel_id": channel["id"], "model_id": "vision-a"}]
        )
        self.tasks = AnalysisTaskService(self.db_path, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def add_photo(self, name: str) -> int:
        source = self.library / name
        Image.new("RGB", (32, 24), "green").save(source)
        LibraryScanner(self.db_path, self.library).scan(trigger="manual")
        conn = sqlite3.connect(self.db_path)
        photo_id = int(
            conn.execute("SELECT id FROM photos WHERE filename=?", (name,)).fetchone()[0]
        )
        conn.close()
        return photo_id

    def create_task(self, photo_ids: list[int]) -> dict:
        preview = self.tasks.preview_selection(
            {
                "task_type": "incremental",
                "selection": {"kind": "manual", "photo_ids": photo_ids},
            }
        )
        return self.tasks.create_task(
            {
                "task_type": "incremental",
                "photo_ids": preview["photo_ids"],
                "strategy_snapshot": {
                    "execution_levels": preview["execution_levels"],
                    "max_request_rounds": preview["max_request_rounds"],
                },
            }
        )

    def set_task_status(self, task_id: int, status: str) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE analysis_tasks SET status=? WHERE id=?", (status, task_id))
        conn.commit()
        conn.close()

    @staticmethod
    def analysis_result(name: str) -> dict:
        return {
            "caption": f"AI 描述 {name}",
            "side_caption": f"记住 {name}",
            "type": "家庭/日常",
            "memory_score": 88.0,
            "beauty_score": 76.0,
            "reason": "人物互动自然",
            "analysis_channel": "Primary",
            "analysis_model": "vision-a",
            "raw_json": '{"source":"fake"}',
        }

    def test_cancelling_a_queued_task_releases_all_photos_without_results(self):
        photo_id = self.add_photo("cancel.jpg")
        created = self.create_task([photo_id])

        cancelled = self.tasks.control_task(created["task_id"], "cancel")

        self.assertEqual(cancelled["status"], "cancelled")
        conn = sqlite3.connect(self.db_path)
        item = conn.execute(
            "SELECT status, analysis_version_id FROM analysis_task_items WHERE task_id=?",
            (created["task_id"],),
        ).fetchone()
        occupied = conn.execute(
            "SELECT COUNT(*) FROM analysis_task_occupancy WHERE photo_id=?", (photo_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(item, ("cancelled", None))
        self.assertEqual(occupied, 0)
        self.assertEqual(self.create_task([photo_id])["status"], "queued")

    def test_pause_waits_for_inflight_photo_and_blocks_queue_until_resumed(self):
        first_id = self.add_photo("first.jpg")
        second_id = self.add_photo("second.jpg")
        later_id = self.add_photo("later.jpg")
        current = self.create_task([first_id, second_id])
        following = self.create_task([later_id])
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def executor(source, _level):
            calls.append(source.name)
            if source.name == "first.jpg":
                entered.set()
                release.wait(5)
            return self.analysis_result(source.stem)

        worker = AnalysisWorker(self.db_path, executor)
        thread = threading.Thread(target=worker.run_once)
        thread.start()
        self.assertTrue(entered.wait(2))
        try:
            pausing = self.tasks.control_task(current["task_id"], "pause")
            self.assertEqual(pausing["status"], "pausing")
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())

        paused = self.tasks.get_task(current["task_id"])
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(calls, ["first.jpg"])
        self.assertIsNone(worker.run_once())
        conn = sqlite3.connect(self.db_path)
        occupied = conn.execute(
            "SELECT COUNT(*) FROM analysis_task_occupancy WHERE task_id=?",
            (current["task_id"],),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(occupied, 2)

        resumed = self.tasks.control_task(current["task_id"], "resume")
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(worker.run_once()["task_id"], current["task_id"])
        self.assertEqual(worker.run_once()["task_id"], following["task_id"])

    def test_resume_prioritizes_the_paused_task_over_reordered_work(self):
        blocker = self.create_task([self.add_photo("resume-blocker.jpg")])
        current = self.create_task(
            [self.add_photo("resume-current.jpg"), self.add_photo("resume-later.jpg")]
        )
        following = self.create_task([self.add_photo("resume-following.jpg")])
        entered = threading.Event()
        release = threading.Event()

        def executor(source, _level):
            if source.name == "resume-current.jpg":
                entered.set()
                release.wait(5)
            return self.analysis_result(source.stem)

        worker = AnalysisWorker(self.db_path, executor)
        self.assertEqual(worker.run_once()["task_id"], blocker["task_id"])
        thread = threading.Thread(target=worker.run_once)
        thread.start()
        self.assertTrue(entered.wait(2))
        try:
            self.tasks.control_task(current["task_id"], "pause")
            self.tasks.reorder_task(following["task_id"], 1)
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())

        resumed = self.tasks.control_task(current["task_id"], "resume")
        self.assertEqual(resumed["queue_position"], 0)
        self.assertEqual(worker.run_once()["task_id"], current["task_id"])
        self.assertEqual(worker.run_once()["task_id"], following["task_id"])

    def test_stop_preserves_inflight_result_releases_unprocessed_and_continues_queue(self):
        first_id = self.add_photo("stop-first.jpg")
        second_id = self.add_photo("stop-second.jpg")
        later_id = self.add_photo("after-stop.jpg")
        current = self.create_task([first_id, second_id])
        following = self.create_task([later_id])
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def executor(source, _level):
            calls.append(source.name)
            if source.name == "stop-first.jpg":
                entered.set()
                release.wait(5)
            return self.analysis_result(source.stem)

        worker = AnalysisWorker(self.db_path, executor)
        thread = threading.Thread(target=worker.run_once)
        thread.start()
        self.assertTrue(entered.wait(2))
        try:
            stopping = self.tasks.control_task(current["task_id"], "stop")
            self.assertEqual(stopping["status"], "stopping")
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())

        stopped = self.tasks.get_task(current["task_id"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["processed_count"], 1)
        self.assertEqual(stopped["remaining_count"], 1)
        conn = sqlite3.connect(self.db_path)
        items = conn.execute(
            "SELECT status FROM analysis_task_items WHERE task_id=? ORDER BY position",
            (current["task_id"],),
        ).fetchall()
        occupied = conn.execute(
            "SELECT COUNT(*) FROM analysis_task_occupancy WHERE task_id=?",
            (current["task_id"],),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(items, [("completed",), ("stopped",)])
        self.assertEqual(occupied, 0)
        self.assertEqual(calls, ["stop-first.jpg"])
        self.assertEqual(worker.run_once()["task_id"], following["task_id"])

    def test_only_queued_tasks_can_be_reordered_and_worker_uses_new_order(self):
        first = self.create_task([self.add_photo("queue-first.jpg")])
        second = self.create_task([self.add_photo("queue-second.jpg")])
        third = self.create_task([self.add_photo("queue-third.jpg")])

        reordered = self.tasks.reorder_task(third["task_id"], 1)

        self.assertEqual(reordered["queue_position"], 1)
        calls = []

        def executor(source, _level):
            calls.append(source.name)
            return self.analysis_result(source.stem)

        worker = AnalysisWorker(self.db_path, executor)
        self.assertEqual(worker.run_once()["task_id"], third["task_id"])
        self.assertEqual(worker.run_once()["task_id"], first["task_id"])
        self.assertEqual(worker.run_once()["task_id"], second["task_id"])
        self.assertEqual(
            calls, ["queue-third.jpg", "queue-first.jpg", "queue-second.jpg"]
        )
        with self.assertRaises(AnalysisTaskError) as error:
            self.tasks.reorder_task(first["task_id"], 1)
        self.assertEqual(error.exception.code, "invalid_task_transition")

    def test_retry_creates_new_task_from_failed_and_unprocessed_items(self):
        failed_id = self.add_photo("retry-failed.jpg")
        unprocessed_id = self.add_photo("retry-unprocessed.jpg")
        original = self.create_task([failed_id, unprocessed_id])
        entered = threading.Event()
        release = threading.Event()

        def executor(source, _level):
            if source.name == "retry-failed.jpg":
                entered.set()
                release.wait(5)
                raise RuntimeError("temporary failure")
            return self.analysis_result(source.stem)

        worker = AnalysisWorker(self.db_path, executor)
        thread = threading.Thread(target=worker.run_once)
        thread.start()
        self.assertTrue(entered.wait(2))
        try:
            self.tasks.control_task(original["task_id"], "stop")
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())

        retry = self.tasks.retry_task(original["task_id"])

        self.assertNotEqual(retry["task_id"], original["task_id"])
        self.assertEqual(retry["status"], "queued")
        conn = sqlite3.connect(self.db_path)
        original_items = conn.execute(
            "SELECT photo_id, status FROM analysis_task_items WHERE task_id=? ORDER BY position",
            (original["task_id"],),
        ).fetchall()
        retry_items = conn.execute(
            "SELECT photo_id, status FROM analysis_task_items WHERE task_id=? ORDER BY position",
            (retry["task_id"],),
        ).fetchall()
        conn.close()
        self.assertEqual(
            original_items, [(failed_id, "failed"), (unprocessed_id, "stopped")]
        )
        self.assertEqual(
            retry_items, [(failed_id, "queued"), (unprocessed_id, "queued")]
        )

    def test_control_state_machine_allows_only_documented_transitions(self):
        allowed = {
            ("queued", "cancel"): "cancelled",
            ("running", "pause"): "pausing",
            ("running", "stop"): "stopping",
            ("pausing", "stop"): "stopping",
            ("paused", "resume"): "queued",
            ("paused", "stop"): "stopped",
        }
        all_statuses = {
            "queued",
            "running",
            "pausing",
            "paused",
            "stopping",
            "stopped",
            "cancelled",
            "completed",
            "completed_with_failures",
            "failed",
        }
        actions = {"pause", "resume", "stop", "cancel"}

        for index, ((status, action), expected) in enumerate(allowed.items()):
            with self.subTest(status=status, action=action):
                task = self.create_task([self.add_photo(f"allowed-{index}.jpg")])
                self.set_task_status(task["task_id"], status)
                result = self.tasks.control_task(task["task_id"], action)
                self.assertEqual(result["status"], expected)

        guarded = self.create_task([self.add_photo("guarded.jpg")])
        for status in all_statuses:
            for action in actions:
                if (status, action) in allowed:
                    continue
                with self.subTest(rejected_status=status, rejected_action=action):
                    self.set_task_status(guarded["task_id"], status)
                    with self.assertRaises(AnalysisTaskError) as error:
                        self.tasks.control_task(guarded["task_id"], action)
                    self.assertEqual(error.exception.code, "invalid_task_transition")


if __name__ == "__main__":
    unittest.main()
