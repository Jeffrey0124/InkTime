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
            reason TEXT
        )
        """
    )


class PhotoIdentityMigrationTests(unittest.TestCase):
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
