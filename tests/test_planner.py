"""End-to-end test of rules → planner with a synthesized SQLite cache.

Runs offline; no Evernote API calls. Seeds the cache with three notebooks,
three tags, six notes (one with ENML content), then runs the example rules
and asserts the plan matches expectations.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


class PlannerE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cache_dir = self.tmp / ".cache"
        self.cache_dir.mkdir()
        self._patcher = mock.patch("evnote.client.CACHE_DIR", self.cache_dir)
        self._patcher.start()
        # cache.DB_PATH was bound at import time; re-bind it to the tmp path.
        from evnote import cache
        cache.DB_PATH = self.cache_dir / "inventory.db"

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self.tmp)

    def _seed(self):
        from evnote import cache
        with cache.connect() as conn:
            for nb in [
                cache.Notebook("nb-inbox-old", "Inbox-old", None),
                cache.Notebook("nb-receipts", "Receipts", None),
                cache.Notebook("nb-misc", "Misc", None),
            ]:
                cache.upsert_notebook(conn, nb)
            for t in [
                cache.Tag("tg-receipt", "receipt", None),
                cache.Tag("tg-web", "web-clip", None),
                cache.Tag("tg-archived", "archived", None),
            ]:
                cache.upsert_tag(conn, t)

            ms = lambda y, m=1, d=1: int(time.mktime((y, m, d, 12, 0, 0, 0, 0, 0))) * 1000

            cache.upsert_note(conn, cache.Note(
                guid="n-receipt-2024", title="Coffee receipt", notebook_guid="nb-misc",
                created=ms(2024, 5, 1), updated=ms(2024, 5, 1),
                source_url=None, tag_guids=["tg-receipt"]))
            cache.upsert_note(conn, cache.Note(
                guid="n-receipt-2023", title="Old receipt", notebook_guid="nb-misc",
                created=ms(2023, 1, 1), updated=ms(2023, 1, 1),
                source_url=None, tag_guids=["tg-receipt"]))
            cache.upsert_note(conn, cache.Note(
                guid="n-webclip", title="Some article", notebook_guid="nb-misc",
                created=ms(2025, 6, 1), updated=ms(2025, 6, 1),
                source_url="https://example.com/article", tag_guids=[]))
            cache.upsert_note(conn, cache.Note(
                guid="n-ancient", title="Ancient note", notebook_guid="nb-misc",
                created=ms(2018, 1, 1), updated=ms(2019, 6, 1),
                source_url=None, tag_guids=[]))
            cache.upsert_note(conn, cache.Note(
                guid="n-keep", title="Recent note", notebook_guid="nb-misc",
                created=ms(2025, 1, 1), updated=ms(2025, 8, 1),
                source_url=None, tag_guids=[]))
            cache.upsert_note(conn, cache.Note(
                guid="n-secret", title="Has the magic word", notebook_guid="nb-misc",
                created=ms(2025, 1, 1), updated=ms(2025, 8, 1),
                source_url=None, tag_guids=[]))
            cache.upsert_content(conn, "n-secret", "<en-note>contains MAGIC inside</en-note>", None)

    def test_example_rules(self):
        from evnote import planner, rules
        self._seed()
        rule_list = rules.load(Path("rules/example.yaml"))
        plan = planner.build(rule_list)

        by_guid = {a.note_guid: a for a in plan.note_actions}

        # 2024 receipt → Receipts/2024
        self.assertEqual(by_guid["n-receipt-2024"].move_to_notebook, "Receipts/2024")
        self.assertEqual(by_guid["n-receipt-2024"].rule_name, "Move 2024 receipts into Receipts/2024")

        # web clip → +web-clip tag
        self.assertEqual(by_guid["n-webclip"].add_tags, ["web-clip"])
        self.assertEqual(by_guid["n-webclip"].rule_name, "Tag web clips")

        # Ancient note → Archive (updated 2019 < 2020-01-01)
        self.assertEqual(by_guid["n-ancient"].move_to_notebook, "Archive")
        self.assertEqual(by_guid["n-ancient"].add_tags, ["archived"])

        # 2023 receipt does NOT match created_year:2024 — should fall through to nothing
        # (the archive rule needs updated_before:2020 — 2023 doesn't match either)
        self.assertNotIn("n-receipt-2023", by_guid)
        self.assertNotIn("n-keep", by_guid)
        self.assertNotIn("n-secret", by_guid)

        # Notebook rename present
        self.assertEqual(len(plan.notebook_actions), 1)
        self.assertEqual(plan.notebook_actions[0].rename_from, "Inbox-old")
        self.assertEqual(plan.notebook_actions[0].rename_to, "Inbox")

    def test_content_contains(self):
        from evnote import planner, rules
        self._seed()
        # Manually build a rule that uses content_contains
        rule = rules.Rule(
            name="magic", match=rules.Match(content_contains="MAGIC"),
            action=rules.Action(add_tags=["magic-found"]),
        )
        plan = planner.build([rule])
        guids = {a.note_guid for a in plan.note_actions}
        self.assertEqual(guids, {"n-secret"})

    def test_first_match_wins(self):
        from evnote import planner, rules
        self._seed()
        r1 = rules.Rule("r1", rules.Match(notebook="Misc"), rules.Action(add_tags=["t1"]))
        r2 = rules.Rule("r2", rules.Match(notebook="Misc"), rules.Action(add_tags=["t2"]))
        plan = planner.build([r1, r2])
        # Every Misc note should match r1 only
        self.assertTrue(plan.note_actions)
        for a in plan.note_actions:
            self.assertEqual(a.rule_name, "r1")
            self.assertEqual(a.add_tags, ["t1"])


if __name__ == "__main__":
    unittest.main()
