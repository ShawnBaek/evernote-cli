"""Offline CLI tests: subcommands that don't require an Evernote token."""
from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from typer.testing import CliRunner


class CliOffline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cache_dir = self.tmp / ".cache"
        self.cache_dir.mkdir()
        self._patch = mock.patch("evnote.client.CACHE_DIR", self.cache_dir)
        self._patch.start()
        from evnote import cache, executor
        cache.DB_PATH = self.cache_dir / "inventory.db"
        executor.BACKUPS_DIR = self.cache_dir / "backups"
        executor.AUDIT_LOG = self.cache_dir / "audit.log"

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp)

    def _seed(self):
        from evnote import cache
        ms = lambda y: int(time.mktime((y, 1, 1, 12, 0, 0, 0, 0, 0))) * 1000
        with cache.connect() as conn:
            cache.upsert_notebook(conn, cache.Notebook("nb1", "Inbox-old", None))
            cache.upsert_tag(conn, cache.Tag("t1", "receipt", None))
            cache.upsert_note(conn, cache.Note(
                guid="g1", title="Coffee", notebook_guid="nb1",
                created=ms(2024), updated=ms(2024),
                source_url=None, tag_guids=["t1"]))

    def test_list_by_notebook(self):
        from evnote.cli import app
        self._seed()
        result = CliRunner().invoke(app, ["list", "--by", "notebook"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Inbox-old", result.output)
        self.assertIn("Total notes: 1", result.output)

    def test_plan_dry_run(self):
        from evnote.cli import app
        self._seed()
        result = CliRunner().invoke(app, ["plan", "rules/example.yaml"])
        self.assertEqual(result.exit_code, 0, result.output)
        # The 2024 receipt rule should match our seeded note
        self.assertIn("Receipts/2024", result.output)
        # Notebook rename should appear
        self.assertIn("RENAME notebook 'Inbox-old' -> 'Inbox'", result.output)

    def test_apply_dry_run_no_backup_required(self):
        from evnote.cli import app
        self._seed()
        # Default --dry-run=true should NOT require a backup
        result = CliRunner().invoke(app, ["apply", "rules/example.yaml"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("(dry-run)", result.output)

    def test_apply_no_dry_run_requires_backup(self):
        from evnote.cli import app
        self._seed()
        # --no-dry-run with no backup should fail loudly
        result = CliRunner().invoke(app, ["apply", "rules/example.yaml", "--no-dry-run"])
        self.assertNotEqual(result.exit_code, 0)
        # Error surfaces from executor
        self.assertTrue(
            "backup" in (result.output + str(result.exception or "")).lower(),
            f"expected backup error, got: {result.output} / {result.exception}",
        )


if __name__ == "__main__":
    unittest.main()
