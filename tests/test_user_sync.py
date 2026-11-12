"""User sync isolation and ownership contract, using disposable homes only."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from academic_paper_workflow import cli
from academic_paper_workflow.user_sync import MANIFEST, ROOTS, sync_user


class UserSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def sync(self, target="all", check=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return sync_user(target, check, home=self.home)

    def test_check_writes_nothing_then_install_is_idempotent(self):
        self.assertEqual(self.sync(check=True), 1)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(self.sync(), 0)
        for root in ROOTS.values():
            entry = self.home / root / "paper-note/SKILL.md"
            self.assertTrue(entry.is_file())
            self.assertIn("-m academic_paper_workflow", entry.read_text(encoding="utf-8"))
        self.assertFalse((self.home / "Templates").exists())
        self.assertFalse((self.home / ".paper-flow/manifest.json").exists())
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob("*") if p.is_file()}
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.sync(check=True), 0)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob("*") if p.is_file()})
        self.assertEqual(len(json.loads((self.home / MANIFEST).read_text())["files"]), 3)

    def test_existing_one_pager_is_independent(self):
        entry = self.home / ROOTS["codex"] / "paper-one-pager/SKILL.md"
        entry.parent.mkdir(parents=True)
        entry.write_text("my custom entry", encoding="utf-8")
        self.assertEqual(self.sync(), 0)
        self.assertEqual(entry.read_text(), "my custom entry")
        self.assertTrue((entry.parent.parent / "paper-note/SKILL.md").is_file())

    def test_owned_entry_local_edits_are_preserved(self):
        self.assertEqual(self.sync("antigravity"), 0)
        entry = self.home / ROOTS["antigravity"] / "paper-note/SKILL.md"
        entry.write_text("local changes", encoding="utf-8")
        self.assertEqual(self.sync("antigravity"), 2)
        self.assertEqual(entry.read_text(), "local changes")
        self.assertFalse((self.home / ROOTS["codex"]).exists())

    def test_cli_defaults_and_explicit_project_compatibility(self):
        with patch.object(cli, "sync_user", return_value=0) as user, patch.object(cli, "sync", return_value=0) as project, patch.object(cli, "resolve_vault", return_value=self.home):
            self.assertEqual(cli.main(["sync"]), 0)
            user.assert_called_once_with("all", False)
            self.assertEqual(cli.main(["--vault", str(self.home), "sync"]), 2)
            self.assertEqual(cli.main(["--vault", str(self.home), "sync", "--scope", "project"]), 0)
            project.assert_called_once_with(self.home, "all", False)
            self.assertEqual(cli.main(["--vault", str(self.home), "sync", "--scope", "user"]), 2)
            self.assertEqual(user.call_count, 1)


if __name__ == "__main__":
    unittest.main()
