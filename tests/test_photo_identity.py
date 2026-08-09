import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from photo_identity import ensure_photo_identity_schema


def _create_photo_scores(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE photo_scores (
            path TEXT PRIMARY KEY,
            caption TEXT,
            type TEXT,
            memory_score REAL,
            beauty_score REAL,
            reason TEXT,
            side_caption TEXT,
            analysis_channel TEXT,
            analysis_model TEXT,
            crop_focus_json TEXT,
            raw_json TEXT
        )
        """
    )


def _create_legacy_photos(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE photos (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL UNIQUE,
          file_hash TEXT,
          size_bytes INTEGER,
          mtime REAL,
          exists_on_disk INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'analyzed',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          missing_at TEXT
        )
        """
    )


class PhotoIdentityMigrationTests(unittest.TestCase):
    def test_orphaned_legacy_photo_uses_legacy_file_state_and_pending_analysis(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "photos.db"
            now = "2026-08-09T00:00:00+00:00"
            conn = sqlite3.connect(db_path)
            _create_legacy_photos(conn)
            conn.execute(
                """
                INSERT INTO photos
                (path, exists_on_disk, status, created_at, updated_at, missing_at)
                VALUES ('orphan.jpg', 0, 'missing', ?, ?, ?)
                """,
                (now, now, now),
            )
            conn.commit()
            conn.close()

            ensure_photo_identity_schema(db_path)

            conn = sqlite3.connect(db_path)
            try:
                state = conn.execute(
                    """
                    SELECT file_status, analysis_status, visibility_status
                    FROM photos WHERE path = 'orphan.jpg'
                    """
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(state, ("missing", "pending", "active"))

    def test_analysis_versions_reject_update_and_delete(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "photo.jpg"
            source.write_bytes(b"photo")
            db_path = root / "photos.db"
            conn = sqlite3.connect(db_path)
            _create_photo_scores(conn)
            conn.execute(
                """
                INSERT INTO photo_scores
                (path, caption, type, memory_score, beauty_score, reason)
                VALUES (?, 'caption', 'daily', 80, 70, 'reason')
                """,
                (str(source),),
            )
            conn.commit()
            conn.close()

            ensure_photo_identity_schema(db_path)

            conn = sqlite3.connect(db_path)
            try:
                version_id = conn.execute("SELECT id FROM analysis_versions").fetchone()[0]
                with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    conn.execute(
                        "UPDATE analysis_versions SET caption = 'changed' WHERE id = ?",
                        (version_id,),
                    )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    conn.execute(
                        "DELETE FROM analysis_versions WHERE id = ?",
                        (version_id,),
                    )
            finally:
                conn.close()

    def test_migration_restarts_after_transaction_failure(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "photo.jpg"
            source.write_bytes(b"photo")
            db_path = root / "photos.db"
            now = "2026-08-09T00:00:00+00:00"
            conn = sqlite3.connect(db_path)
            _create_photo_scores(conn)
            conn.execute(
                """
                INSERT INTO photo_scores
                (path, caption, type, memory_score, beauty_score, reason)
                VALUES (?, 'caption', 'daily', 80, 70, 'reason')
                """,
                (str(source),),
            )
            _create_legacy_photos(conn)
            conn.execute(
                """
                INSERT INTO photos
                (path, exists_on_disk, status, created_at, updated_at)
                VALUES (?, 0, 'missing', ?, ?)
                """,
                (str(source), now, now),
            )
            conn.execute(
                """
                CREATE TRIGGER fail_migration
                BEFORE UPDATE ON photos
                BEGIN
                  SELECT RAISE(ABORT, 'injected migration failure');
                END
                """
            )
            conn.commit()
            conn.close()

            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected migration failure"):
                ensure_photo_identity_schema(db_path)

            conn = sqlite3.connect(db_path)
            conn.execute("DROP TRIGGER fail_migration")
            conn.commit()
            conn.close()

            ensure_photo_identity_schema(db_path)

            conn = sqlite3.connect(db_path)
            try:
                photo = conn.execute(
                    """
                    SELECT file_status, analysis_status, current_analysis_version_id
                    FROM photos WHERE path = ?
                    """,
                    (str(source),),
                ).fetchone()
                version_count = conn.execute(
                    "SELECT COUNT(*) FROM analysis_versions"
                ).fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(photo[0:2], ("present", "analyzed"))
            self.assertIsNotNone(photo[2])
            self.assertEqual(version_count, 1)

    def test_old_identity_schema_upgrade_preserves_overrides_and_push_history(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "missing.jpg"
            db_path = root / "photos.db"
            now = "2026-08-09T00:00:00+00:00"

            conn = sqlite3.connect(db_path)
            _create_photo_scores(conn)
            conn.execute(
                """
                INSERT INTO photo_scores
                (path, caption, type, memory_score, beauty_score, reason)
                VALUES (?, 'caption', 'daily', 80, 70, 'reason')
                """,
                (str(source),),
            )
            _create_legacy_photos(conn)
            photo_id = conn.execute(
                """
                INSERT INTO photos
                (path, exists_on_disk, status, created_at, updated_at, missing_at)
                VALUES (?, 0, 'missing', ?, ?, ?)
                """,
                (str(source), now, now, now),
            ).lastrowid
            conn.execute(
                """
                CREATE TABLE photo_overrides (
                  photo_id INTEGER PRIMARY KEY,
                  custom_side_caption TEXT,
                  manual_crop_json TEXT,
                  render_overrides_json TEXT,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO photo_overrides VALUES (?, '保留文案', '{}', '{}', ?)",
                (photo_id, now),
            )
            conn.execute(
                """
                CREATE TABLE push_history (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  source_path TEXT NOT NULL,
                  render_path TEXT NOT NULL,
                  pushed_at TEXT NOT NULL,
                  trigger_type TEXT NOT NULL,
                  slot TEXT,
                  exif_date TEXT,
                  note TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO push_history
                (source_path, render_path, pushed_at, trigger_type)
                VALUES (?, 'latest.bmp', ?, 'manual')
                """,
                (str(source), now),
            )
            conn.commit()
            conn.close()

            ensure_photo_identity_schema(db_path)

            conn = sqlite3.connect(db_path)
            try:
                photo = conn.execute(
                    "SELECT id, file_status FROM photos WHERE path = ?",
                    (str(source),),
                ).fetchone()
                override = conn.execute(
                    "SELECT custom_side_caption FROM photo_overrides WHERE photo_id = ?",
                    (photo_id,),
                ).fetchone()
                push_count = conn.execute("SELECT COUNT(*) FROM push_history").fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(photo, (photo_id, "missing"))
            self.assertEqual(override[0], "保留文案")
            self.assertEqual(push_count, 1)

    def test_migration_creates_phase_two_foundation_tables(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "photos.db"

            ensure_photo_identity_schema(db_path)
            ensure_photo_identity_schema(db_path)

            conn = sqlite3.connect(db_path)
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                columns = {
                    table: {
                        row[1] for row in conn.execute(f"PRAGMA table_info({table})")
                    }
                    for table in (
                        "analysis_tasks",
                        "analysis_task_items",
                        "model_channels",
                        "model_channel_versions",
                        "notifications",
                    )
                }
            finally:
                conn.close()

            self.assertTrue(
                {
                    "analysis_tasks",
                    "analysis_task_items",
                    "model_channels",
                    "model_channel_versions",
                    "notifications",
                }.issubset(tables)
            )
            self.assertTrue(
                {"status", "queue_position", "concurrency", "model_strategy_json"}.issubset(
                    columns["analysis_tasks"]
                )
            )
            self.assertTrue(
                {"task_id", "photo_id", "status", "attempt_count", "analysis_version_id"}.issubset(
                    columns["analysis_task_items"]
                )
            )
            self.assertTrue(
                {"provider_preset", "credential_source", "current_version_id"}.issubset(
                    columns["model_channels"]
                )
            )
            self.assertTrue(
                {"channel_id", "version_number", "base_url", "models_json"}.issubset(
                    columns["model_channel_versions"]
                )
            )
            self.assertTrue(
                {"kind", "message", "target_url", "is_read"}.issubset(
                    columns["notifications"]
                )
            )
    def test_migration_backfills_asset_states_and_one_analysis_version(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "legacy.jpg"
            source.write_bytes(b"legacy photo")
            db_path = root / "photos.db"

            conn = sqlite3.connect(db_path)
            _create_photo_scores(conn)
            conn.execute(
                """
                INSERT INTO photo_scores
                (path, caption, type, memory_score, beauty_score, reason,
                 side_caption, analysis_channel, analysis_model, crop_focus_json,
                 raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(source),
                    "legacy caption",
                    "daily",
                    81,
                    73,
                    "legacy reason",
                    "legacy side caption",
                    "cloud_qwen",
                    "qwen3-vl-plus",
                    '{"x": 0.2, "y": 0.3}',
                    '{"caption": "legacy caption"}',
                ),
            )
            conn.commit()
            conn.close()

            ensure_photo_identity_schema(db_path)
            ensure_photo_identity_schema(db_path)

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                photo = conn.execute(
                    """
                    SELECT file_status, analysis_status, visibility_status,
                           current_analysis_version_id
                    FROM photos
                    WHERE path = ?
                    """,
                    (str(source),),
                ).fetchone()
                versions = conn.execute(
                    """
                    SELECT id, version_number, caption, side_caption, photo_type,
                           memory_score, beauty_score, reason, crop_focus_json,
                           analysis_channel, analysis_model, result_json
                    FROM analysis_versions
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertEqual(photo["file_status"], "present")
            self.assertEqual(photo["analysis_status"], "analyzed")
            self.assertEqual(photo["visibility_status"], "active")
            self.assertEqual(photo["current_analysis_version_id"], versions[0]["id"])
            self.assertEqual(len(versions), 1)
            self.assertEqual(versions[0]["version_number"], 1)
            self.assertEqual(versions[0]["caption"], "legacy caption")
            self.assertEqual(versions[0]["photo_type"], "daily")
            self.assertEqual(versions[0]["memory_score"], 81)
            self.assertEqual(versions[0]["beauty_score"], 73)
            self.assertEqual(versions[0]["reason"], "legacy reason")
            self.assertEqual(versions[0]["side_caption"], "legacy side caption")
            self.assertEqual(versions[0]["crop_focus_json"], '{"x": 0.2, "y": 0.3}')
            self.assertEqual(versions[0]["analysis_channel"], "cloud_qwen")
            self.assertEqual(versions[0]["analysis_model"], "qwen3-vl-plus")
            self.assertEqual(versions[0]["result_json"], '{"caption": "legacy caption"}')

    def test_migration_backfills_stable_photo_ids_from_photo_scores(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "existing.jpg"
            existing.write_bytes(b"not a real image")
            missing = root / "missing.jpg"
            directory_path = root / "not-a-photo"
            directory_path.mkdir()
            db_path = root / "photos.db"

            conn = sqlite3.connect(db_path)
            _create_photo_scores(conn)
            conn.execute(
                """
                INSERT INTO photo_scores
                (path, caption, type, memory_score, beauty_score, reason)
                VALUES (?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(existing),
                    "existing caption",
                    "daily",
                    80,
                    70,
                    "exists",
                    str(missing),
                    "missing caption",
                    "daily",
                    60,
                    50,
                    "missing",
                    str(directory_path),
                    "directory caption",
                    "daily",
                    40,
                    30,
                    "not a file",
                ),
            )
            conn.commit()
            conn.close()

            summary = ensure_photo_identity_schema(db_path)
            again = ensure_photo_identity_schema(db_path)

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, path, exists_on_disk, status, missing_at
                FROM photos
                ORDER BY path
                """
            ).fetchall()
            override_columns = [
                row["name"] for row in conn.execute("PRAGMA table_info(photo_overrides)")
            ]
            render_columns = [
                row["name"] for row in conn.execute("PRAGMA table_info(render_assets)")
            ]
            conn.close()

            self.assertEqual(summary.inserted, 3)
            self.assertEqual(summary.updated, 0)
            self.assertEqual(again.inserted, 0)
            self.assertEqual(again.updated, 0)
            self.assertEqual(len(rows), 3)
            self.assertEqual(
                {row["path"] for row in rows},
                {str(existing), str(missing), str(directory_path)},
            )
            self.assertTrue(all(isinstance(row["id"], int) for row in rows))

            by_path = {row["path"]: row for row in rows}
            self.assertEqual(by_path[str(existing)]["exists_on_disk"], 1)
            self.assertEqual(by_path[str(existing)]["status"], "analyzed")
            self.assertIsNone(by_path[str(existing)]["missing_at"])
            self.assertEqual(by_path[str(missing)]["exists_on_disk"], 0)
            self.assertEqual(by_path[str(missing)]["status"], "missing")
            self.assertIsNotNone(by_path[str(missing)]["missing_at"])
            self.assertEqual(by_path[str(directory_path)]["exists_on_disk"], 0)
            self.assertEqual(by_path[str(directory_path)]["status"], "missing")
            self.assertIsNotNone(by_path[str(directory_path)]["missing_at"])

            self.assertIn("custom_side_caption", override_columns)
            self.assertIn("manual_crop_json", override_columns)
            self.assertIn("render_overrides_json", override_columns)
            self.assertIn("variant_hash", render_columns)
            self.assertIn("preview_png_path", render_columns)

    def test_migration_restores_missing_photo_without_changing_photo_id(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "later.jpg"
            db_path = root / "photos.db"

            conn = sqlite3.connect(db_path)
            _create_photo_scores(conn)
            conn.execute(
                """
                INSERT INTO photo_scores
                (path, caption, type, memory_score, beauty_score, reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(source), "caption", "daily", 80, 70, "missing first"),
            )
            conn.commit()
            conn.close()

            ensure_photo_identity_schema(db_path)
            conn = sqlite3.connect(db_path)
            first = conn.execute(
                "SELECT id, exists_on_disk, status, missing_at FROM photos WHERE path = ?",
                (str(source),),
            ).fetchone()
            conn.close()
            self.assertEqual(first[1], 0)
            self.assertEqual(first[2], "missing")
            self.assertIsNotNone(first[3])

            source.write_bytes(b"now present")
            summary = ensure_photo_identity_schema(db_path)

            conn = sqlite3.connect(db_path)
            restored = conn.execute(
                "SELECT id, exists_on_disk, status, missing_at FROM photos WHERE path = ?",
                (str(source),),
            ).fetchone()
            conn.close()

            self.assertEqual(summary.inserted, 0)
            self.assertEqual(summary.updated, 1)
            self.assertEqual(restored[0], first[0])
            self.assertEqual(restored[1], 1)
            self.assertEqual(restored[2], "analyzed")
            self.assertIsNone(restored[3])


if __name__ == "__main__":
    unittest.main()
