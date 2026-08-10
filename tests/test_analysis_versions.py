import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from analysis_versions import AnalysisVersionService
from photo_identity import ensure_photo_identity_schema


class AnalysisVersionServiceTests(unittest.TestCase):
    def test_compare_and_restore_keeps_immutable_history(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "photos.db"
            ensure_photo_identity_schema(db_path)
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO photos(path, filename, exists_on_disk, status, file_status, analysis_status, visibility_status, created_at, updated_at) "
                "VALUES ('one.jpg', 'one.jpg', 1, 'present', 'present', 'analyzed', 'active', 'now', 'now')"
            )
            photo_id = conn.execute("SELECT id FROM photos").fetchone()[0]
            conn.executemany(
                "INSERT INTO analysis_versions(photo_id, version_number, caption, side_caption, photo_type, memory_score, beauty_score, reason, analysis_channel, analysis_model, result_json, created_at) "
                "VALUES (?, ?, ?, ?, '日常', ?, ?, ?, ?, ?, '{}', ?)",
                [
                    (photo_id, 1, '旧描述', '旧短文案', 70, 60, '旧理由', 'local', 'one', '2026-01-01T00:00:00+00:00'),
                    (photo_id, 2, '新描述', '新短文案', 90, 80, '新理由', 'cloud', 'two', '2026-02-01T00:00:00+00:00'),
                ],
            )
            latest_id = conn.execute("SELECT id FROM analysis_versions WHERE photo_id=? AND version_number=2", (photo_id,)).fetchone()[0]
            first_id = conn.execute("SELECT id FROM analysis_versions WHERE photo_id=? AND version_number=1", (photo_id,)).fetchone()[0]
            conn.execute("UPDATE photos SET current_analysis_version_id=? WHERE id=?", (latest_id, photo_id))
            conn.commit()
            conn.close()

            service = AnalysisVersionService(db_path)
            comparison = service.compare(photo_id, first_id, latest_id)
            self.assertEqual(comparison["caption"], {"left": "旧描述", "right": "新描述"})
            restored = service.restore(photo_id, first_id)
            self.assertEqual(restored["current_version_id"], first_id)

            conn = sqlite3.connect(db_path)
            current = conn.execute("SELECT current_analysis_version_id FROM photos WHERE id=?", (photo_id,)).fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM analysis_versions WHERE photo_id=?", (photo_id,)).fetchone()[0]
            conn.close()
            self.assertEqual(current, first_id)
            self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
