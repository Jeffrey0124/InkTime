import sqlite3
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from analysis_tasks import AnalysisTaskService
from analysis_worker import AnalysisWorker, AnalysisWorkerRunner
from library_scanner import LibraryScanner
from settings_store import SettingsStore
from web_queries import load_photos


class FakeAnalysisExecutor:
    def __init__(self, *, failing_names=()):
        self.failing_names = set(failing_names)
        self.calls = []

    def __call__(self, source: Path, execution_level: dict):
        self.calls.append((source.name, dict(execution_level)))
        if source.name in self.failing_names:
            raise RuntimeError(f"cannot analyze {source.name}")
        return {
            "caption": f"AI 描述 {source.stem}",
            "side_caption": f"记住 {source.stem}",
            "type": "家庭/日常",
            "memory_score": 88.0,
            "beauty_score": 76.0,
            "reason": "人物互动自然",
            "crop_focus_json": '{"x":0.2,"y":0.1,"w":0.5,"h":0.6}',
            "analysis_channel": execution_level["channel_name"],
            "analysis_model": execution_level["model_id"],
            "raw_json": '{"source":"fake"}',
        }


class AnalysisWorkerTests(unittest.TestCase):
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

    def test_run_once_completes_task_persists_version_and_updates_gallery(self):
        photo_id = self.add_photo("family.jpg")
        created = self.create_task([photo_id])
        executor = FakeAnalysisExecutor()

        result = AnalysisWorker(self.db_path, executor).run_once()

        self.assertEqual(result["task_id"], created["task_id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(executor.calls[0][0], "family.jpg")
        self.assertEqual(executor.calls[0][1]["model_id"], "vision-a")
        task = self.tasks.get_task(created["task_id"])
        self.assertEqual(task["processed_count"], 1)
        self.assertEqual(task["succeeded_count"], 1)
        self.assertEqual(task["failed_count"], 0)
        self.assertEqual(task["remaining_count"], 0)

        conn = sqlite3.connect(self.db_path)
        version = conn.execute(
            "SELECT photo_id, version_number, source_task_item_id FROM analysis_versions"
        ).fetchone()
        current = conn.execute(
            "SELECT analysis_status, current_analysis_version_id FROM photos WHERE id=?",
            (photo_id,),
        ).fetchone()
        item = conn.execute(
            "SELECT status, analysis_version_id FROM analysis_task_items WHERE task_id=?",
            (created["task_id"],),
        ).fetchone()
        conn.close()
        self.assertEqual(version[0:2], (photo_id, 1))
        self.assertIsNotNone(version[2])
        self.assertEqual(current, ("analyzed", item[1]))
        self.assertEqual(item[0], "completed")

        gallery = load_photos(self.db_path, limit=10)
        self.assertEqual([photo["photo_id"] for photo in gallery], [photo_id])
        self.assertEqual(gallery[0]["caption"], "AI 描述 family")
        self.assertFalse((self.root / "output").exists())

    def test_one_photo_failure_does_not_stop_following_items(self):
        first_id = self.add_photo("broken.jpg")
        second_id = self.add_photo("good.jpg")
        created = self.create_task([first_id, second_id])

        result = AnalysisWorker(
            self.db_path, FakeAnalysisExecutor(failing_names={"broken.jpg"})
        ).run_once()

        self.assertEqual(result["status"], "completed_with_failures")
        self.assertEqual(result["processed_count"], 2)
        self.assertEqual(result["succeeded_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        conn = sqlite3.connect(self.db_path)
        statuses = conn.execute(
            "SELECT status FROM analysis_task_items WHERE task_id=? ORDER BY position",
            (created["task_id"],),
        ).fetchall()
        occupied = conn.execute(
            "SELECT COUNT(*) FROM analysis_task_occupancy WHERE task_id=?",
            (created["task_id"],),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(statuses, [("failed",), ("completed",)])
        self.assertEqual(occupied, 0)
        self.assertEqual([item["photo_id"] for item in load_photos(self.db_path)], [second_id])

    def test_running_task_blocks_claiming_another_task(self):
        photo_id = self.add_photo("busy.jpg")
        created = self.create_task([photo_id])
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE analysis_tasks SET status='running' WHERE id=?", (created["task_id"],)
        )
        conn.commit()
        conn.close()

        self.assertIsNone(AnalysisWorker(self.db_path, FakeAnalysisExecutor()).run_once())

    def test_task_level_failure_releases_queue_for_the_next_task(self):
        broken_id = self.add_photo("bad-strategy.jpg")
        next_id = self.add_photo("next-task.jpg")
        broken = self.create_task([broken_id])
        following = self.create_task([next_id])
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE analysis_tasks SET model_strategy_json='{}' WHERE id=?",
            (broken["task_id"],),
        )
        conn.commit()
        conn.close()
        executor = FakeAnalysisExecutor()
        worker = AnalysisWorker(self.db_path, executor)

        failed = worker.run_once()
        completed = worker.run_once()

        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failed_count"], 1)
        self.assertEqual(completed["task_id"], following["task_id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual([call[0] for call in executor.calls], ["next-task.jpg"])
        conn = sqlite3.connect(self.db_path)
        occupancy = conn.execute(
            "SELECT COUNT(*) FROM analysis_task_occupancy WHERE task_id=?",
            (broken["task_id"],),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(occupancy, 0)

    def test_task_progress_exposes_current_photo_while_executor_runs(self):
        photo_id = self.add_photo("waiting.jpg")
        created = self.create_task([photo_id])
        entered = threading.Event()
        release = threading.Event()

        def blocking_executor(source, level):
            entered.set()
            release.wait(5)
            return FakeAnalysisExecutor()(source, level)

        worker = AnalysisWorker(self.db_path, blocking_executor)
        thread = threading.Thread(target=worker.run_once)
        thread.start()
        self.assertTrue(entered.wait(2))
        progress = self.tasks.get_task(created["task_id"])
        self.assertEqual(progress["status"], "running")
        self.assertEqual(progress["current_photo_id"], photo_id)
        self.assertEqual(progress["current_filename"], "waiting.jpg")
        self.assertEqual(progress["remaining_count"], 1)
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_background_runner_automatically_claims_a_new_task(self):
        photo_id = self.add_photo("automatic.jpg")
        runner = AnalysisWorkerRunner(
            AnalysisWorker(self.db_path, FakeAnalysisExecutor()), poll_interval=0.01
        )
        runner.start()
        try:
            created = self.create_task([photo_id])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                task = self.tasks.get_task(created["task_id"])
                if task["status"] == "completed":
                    break
                time.sleep(0.01)
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["processed_count"], 1)
        finally:
            runner.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
