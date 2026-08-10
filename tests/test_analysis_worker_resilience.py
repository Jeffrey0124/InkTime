import sqlite3
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from analysis_tasks import AnalysisTaskService
from analysis_worker import AnalysisExecutionError, AnalysisWorker
from library_scanner import LibraryScanner
from settings_store import SettingsStore


class AnalysisWorkerResilienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.library = self.root / "library"
        self.library.mkdir()
        self.db_path = self.root / "photos.db"
        self.store = SettingsStore(self.db_path)
        self.channel = self.store.save_channel({"name": "Primary", "provider": "custom", "base_url": "https://one.test/v1", "credential": {"source": "none"}, "models": [{"model_id": "one", "is_default": True}]})
        self.backup = self.store.save_channel({"name": "Backup", "provider": "custom", "base_url": "https://two.test/v1", "credential": {"source": "none"}, "models": [{"model_id": "two", "is_default": True}]})
        self.store.save_fallback_chain([{"channel_id": self.channel["id"], "model_id": "one"}, {"channel_id": self.backup["id"], "model_id": "two"}])
        self.tasks = AnalysisTaskService(self.db_path, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def add_photo(self, name):
        Image.new("RGB", (32, 24), "green").save(self.library / name)
        LibraryScanner(self.db_path, self.library).scan(trigger="manual")
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("SELECT id FROM photos WHERE filename=?", (name,)).fetchone()[0]
        finally:
            conn.close()

    def create_task(self, photo_ids, concurrency=1):
        preview = self.tasks.preview_selection({"task_type": "incremental", "selection": {"kind": "manual", "photo_ids": photo_ids}})
        return self.tasks.create_task({"task_type": "incremental", "photo_ids": preview["photo_ids"], "concurrency": concurrency, "strategy_snapshot": {"execution_levels": preview["execution_levels"], "max_request_rounds": 2}})

    @staticmethod
    def result(source, level):
        return {"caption": source.stem, "type": "日常", "memory_score": 80, "beauty_score": 70, "reason": "测试", "analysis_channel": level["channel_name"], "analysis_model": level["model_id"]}

    def test_task_honors_frozen_concurrency_limit(self):
        task = self.create_task([self.add_photo(f"parallel-{index}.jpg") for index in range(4)], concurrency=2)
        active = 0
        peak = 0
        lock = threading.Lock()

        def executor(source, level):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return self.result(source, level)

        outcome = AnalysisWorker(self.db_path, executor).run_once()
        self.assertEqual(outcome["task_id"], task["task_id"])
        self.assertEqual(outcome["status"], "completed")
        self.assertEqual(peak, 2)

    def test_deterministic_level_failure_opens_task_circuit_for_later_photos(self):
        task = self.create_task([self.add_photo("first.jpg"), self.add_photo("second.jpg")])
        calls = []

        def executor(source, level):
            calls.append((source.name, level["model_id"]))
            if level["model_id"] == "one":
                raise AnalysisExecutionError("invalid credentials", retryable=False)
            return self.result(source, level)

        outcome = AnalysisWorker(self.db_path, executor).run_once()
        self.assertEqual(outcome["status"], "completed")
        self.assertEqual(calls, [("first.jpg", "one"), ("first.jpg", "two"), ("second.jpg", "two")])

    def test_temporary_failures_repeat_the_frozen_fallback_chain_for_two_rounds(self):
        task = self.create_task([self.add_photo("temporary.jpg")])
        calls = []

        def executor(source, level):
            calls.append(level["model_id"])
            raise AnalysisExecutionError("timeout", retryable=True)

        outcome = AnalysisWorker(self.db_path, executor).run_once()
        self.assertEqual(outcome["status"], "completed_with_failures")
        self.assertEqual(calls, ["one", "two", "one", "two"])

    def test_all_execution_levels_unavailable_pauses_the_queue_with_reason(self):
        task = self.create_task([self.add_photo("offline.jpg")])

        def executor(_source, _level):
            raise AnalysisExecutionError("model missing", retryable=False)

        AnalysisWorker(self.db_path, executor).run_once()
        paused = self.tasks.get_task(task["task_id"])
        self.assertEqual(paused["status"], "paused")
        self.assertIn("全部模型", paused["pause_reason"])
        conn = sqlite3.connect(self.db_path)
        try:
            notification = conn.execute(
                "SELECT kind, target_url FROM notifications WHERE kind='analysis_channels_paused'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(notification, ("analysis_channels_paused", f"/analysis-tasks/{task['task_id']}"))
        self.tasks.control_task(task["task_id"], "resume")
        resumed = AnalysisWorker(self.db_path, self.result).run_once()
        self.assertEqual(resumed["status"], "completed")

    def test_restart_requeues_uncommitted_running_item_without_duplication(self):
        photo_id = self.add_photo("recover.jpg")
        task = self.create_task([photo_id])
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE analysis_tasks SET status='running' WHERE id=?", (task["task_id"],))
            conn.execute("UPDATE analysis_task_items SET status='running', attempt_count=1 WHERE task_id=?", (task["task_id"],))
            conn.commit()
        finally:
            conn.close()

        outcome = AnalysisWorker(self.db_path, self.result).run_once()
        self.assertEqual(outcome["status"], "completed")
        conn = sqlite3.connect(self.db_path)
        try:
            item = conn.execute("SELECT attempt_count, status FROM analysis_task_items WHERE task_id=?", (task["task_id"],)).fetchone()
            versions = conn.execute("SELECT COUNT(*) FROM analysis_versions WHERE photo_id=?", (photo_id,)).fetchone()[0]
            recoveries = conn.execute("SELECT recovery_failures FROM analysis_task_runtime WHERE task_id=?", (task["task_id"],)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(item, (2, "completed"))
        self.assertEqual(versions, 1)
        self.assertEqual(recoveries, 0)

    def test_third_interrupted_recovery_pauses_instead_of_looping_forever(self):
        task = self.create_task([self.add_photo("repeat-recovery.jpg")])
        for _ in range(2):
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("UPDATE analysis_tasks SET status='running' WHERE id=?", (task["task_id"],))
                conn.execute("UPDATE analysis_task_items SET status='running' WHERE task_id=?", (task["task_id"],))
                conn.commit()
            finally:
                conn.close()
            AnalysisWorker(self.db_path, self.result)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE analysis_tasks SET status='running' WHERE id=?", (task["task_id"],))
            conn.commit()
        finally:
            conn.close()

        AnalysisWorker(self.db_path, self.result)
        paused = self.tasks.get_task(task["task_id"])
        self.assertEqual(paused["status"], "paused")
        self.assertIn("连续恢复失败", paused["pause_reason"])


if __name__ == "__main__":
    unittest.main()
