"""Phase 1 public CLI contract; all writes stay in disposable synthetic Vaults.

Run with: uv run --locked python -m unittest discover -s tests -v.
No personal Vault, Zotero library, network service, or model is accessed.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


AGENTS = Path(".agents/skills/paper-note/SKILL.md")
CLAUDE = Path(".claude/skills/paper-note/SKILL.md")
ONE_PAGER_AGENTS = Path(".agents/skills/paper-one-pager/SKILL.md")
ONE_PAGER_CLAUDE = Path(".claude/skills/paper-one-pager/SKILL.md")
TEMPLATE = Path("Templates/文献精读模板.md")
RESOURCES = (
    "spec", "note-template", "checklist", "agent-entry", "one-pager-entry",
    "note-prompt", "colors", "one-pager-prompt", "one-pager-template", "import-template",
)


def snapshot(root: Path) -> dict:
    """Include directory/file timestamps to expose rewrites and transient leftovers."""
    result = {}
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        result[path.relative_to(root).as_posix()] = (
            "dir" if path.is_dir() else "file", stat.st_mtime_ns,
            None if path.is_dir() else path.read_bytes(),
        )
    return result


class PhaseOneCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="paper-flow-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.make_vault("synthetic-vault")
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.env = os.environ.copy()
        self.env.pop("PAPER_FLOW_VAULT", None)
        self.env["PYTHONUTF8"] = "1"
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.env.pop("PYTHONPATH", None)
        self.engine = self.root / "empty-engine"
        self.engine.mkdir()
        self.env["PAPER_FLOW_ENGINE"] = str(self.engine)

    def make_vault(self, name):
        path = self.root / name
        (path / ".obsidian").mkdir(parents=True)
        return path

    def run_cli(self, *args, cwd=None, env=None, expected=0):
        proc = subprocess.run(
            [sys.executable, "-m", "academic_paper_workflow", *map(str, args)],
            cwd=cwd or self.outside, env=env or self.env,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(
            proc.returncode, expected,
            f"args={args!r}\nstdout={proc.stdout}\nstderr={proc.stderr}",
        )
        return proc

    def sync(self, *args, target="all", expected=0):
        return self.run_cli("--vault", self.vault, "sync", "--scope", "project", "--target", target,
                            *args, expected=expected)

    def test_every_packaged_resource_is_available_without_vault(self):
        for name in RESOURCES:
            with self.subTest(resource=name):
                proc = self.run_cli("show", name)
                self.assertTrue(proc.stdout.strip())
                self.assertNotIn("\ufffd", proc.stdout)

    def test_check_reports_changes_without_writing_then_clean_and_idempotent(self):
        before = snapshot(self.vault)
        self.sync("--check", expected=1)
        self.assertEqual(snapshot(self.vault), before)
        self.sync()
        installed = snapshot(self.vault)
        self.sync("--check")
        self.assertEqual(snapshot(self.vault), installed)
        self.sync()
        self.assertEqual(snapshot(self.vault), installed)

    def test_all_installs_both_normal_skill_paths_and_formal_template(self):
        self.sync()
        for relative in (AGENTS, CLAUDE, ONE_PAGER_AGENTS, ONE_PAGER_CLAUDE, TEMPLATE):
            with self.subTest(path=relative):
                data = (self.vault / relative).read_bytes()
                self.assertTrue(data)
                self.assertNotIn(b"\r", data)
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
                data.decode("utf-8")
        self.assertEqual((self.vault / AGENTS).read_bytes(),
                         (self.vault / CLAUDE).read_bytes())
        self.assertEqual((self.vault / ONE_PAGER_AGENTS).read_bytes(),
                         (self.vault / ONE_PAGER_CLAUDE).read_bytes())
        template = self.run_cli("show", "note-template").stdout
        self.assertEqual((self.vault / TEMPLATE).read_text(encoding="utf-8").strip(),
                         template.strip())

    def test_individual_target_paths(self):
        for target, wanted, absent in (
            ("codex", (AGENTS, ONE_PAGER_AGENTS), (CLAUDE, ONE_PAGER_CLAUDE)),
            ("antigravity", (AGENTS, ONE_PAGER_AGENTS), (CLAUDE, ONE_PAGER_CLAUDE)),
            ("claude", (CLAUDE, ONE_PAGER_CLAUDE), (AGENTS, ONE_PAGER_AGENTS)),
        ):
            with self.subTest(target=target):
                vault = self.make_vault(target)
                self.run_cli("--vault", vault, "sync", "--scope", "project", "--target", target)
                for path in wanted:
                    self.assertTrue((vault / path).is_file())
                for path in absent:
                    self.assertFalse((vault / path).exists())
                self.assertTrue((vault / TEMPLATE).is_file())

    def test_client_target_change_does_not_report_shared_entry_as_conflict(self):
        self.sync(target="codex")
        original = (self.vault / AGENTS).read_bytes()
        self.sync(target="antigravity")
        self.assertEqual((self.vault / AGENTS).read_bytes(), original)
        self.sync(target="all")
        self.sync("--check")

    def test_first_install_collision_is_preserved_and_check_is_read_only(self):
        for index, relative in enumerate((AGENTS, CLAUDE, ONE_PAGER_AGENTS, ONE_PAGER_CLAUDE, TEMPLATE)):
            with self.subTest(path=relative):
                vault = self.make_vault("collision-" + str(index))
                path = vault / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# Hand maintained local content\n", encoding="utf-8")
                before = snapshot(vault)
                self.run_cli("--vault", vault, "sync", "--scope", "project", "--target", "all",
                             "--check", expected=2)
                self.assertEqual(snapshot(vault), before)
                self.run_cli("--vault", vault, "sync", "--scope", "project", "--target", "all", expected=2)
                self.assertEqual(path.read_text(encoding="utf-8"),
                                 "# Hand maintained local content\n")

    def test_managed_local_edits_are_preserved(self):
        self.sync()
        for relative in (AGENTS, CLAUDE, ONE_PAGER_AGENTS, ONE_PAGER_CLAUDE, TEMPLATE):
            path = self.vault / relative
            path.write_bytes(path.read_bytes() + b"\nLOCAL USER EDIT\n")
        before = snapshot(self.vault)
        self.sync("--check", expected=2)
        self.assertEqual(snapshot(self.vault), before)
        self.sync(expected=2)
        for relative in (AGENTS, CLAUDE, ONE_PAGER_AGENTS, ONE_PAGER_CLAUDE, TEMPLATE):
            self.assertTrue((self.vault / relative).read_bytes().endswith(b"LOCAL USER EDIT\n"))

    def test_deleted_managed_file_is_recreated(self):
        self.sync()
        expected = (self.vault / CLAUDE).read_bytes()
        (self.vault / CLAUDE).unlink()
        before = snapshot(self.vault)
        self.sync("--check", expected=1)
        self.assertEqual(snapshot(self.vault), before)
        self.sync()
        self.assertEqual((self.vault / CLAUDE).read_bytes(), expected)

    def test_unrelated_vault_files_are_untouched(self):
        files = {
            "AGENTS.md": "# Personal instructions\n",
            "CLAUDE.md": "# Personal Claude instructions\n",
            ".obsidian/settings.json": '{"personal": true}\n',
            "文献研究/文献笔记/Existing.md": "# Old reading note\n",
        }
        for name, content in files.items():
            path = self.vault / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        before = {name: (self.vault / name).stat().st_mtime_ns for name in files}
        self.sync()
        for name, content in files.items():
            self.assertEqual((self.vault / name).read_text(encoding="utf-8"), content)
            self.assertEqual((self.vault / name).stat().st_mtime_ns, before[name])

    def test_explicit_vault_overrides_environment_and_cwd(self):
        other = self.make_vault("environment-vault")
        env = dict(self.env, PAPER_FLOW_VAULT=str(other))
        before = snapshot(other)
        self.run_cli("--vault", self.vault, "sync", "--scope", "project", "--target", "all", cwd=other, env=env)
        self.assertTrue((self.vault / AGENTS).is_file())
        self.assertEqual(snapshot(other), before)

    def test_environment_vault_overrides_upward_scan(self):
        other = self.make_vault("cwd-vault")
        before = snapshot(other)
        env = dict(self.env, PAPER_FLOW_VAULT=str(self.vault))
        self.run_cli("sync", "--scope", "project", "--target", "all", cwd=other, env=env)
        self.assertTrue((self.vault / AGENTS).is_file())
        self.assertEqual(snapshot(other), before)

    def test_upward_scan_uses_nearest_obsidian_directory(self):
        nested = self.vault / "notes" / "deep"
        nested.mkdir(parents=True)
        self.run_cli("sync", "--scope", "project", "--target", "all", cwd=nested)
        self.assertTrue((self.vault / AGENTS).is_file())
        self.assertFalse((nested / AGENTS).exists())

    def test_invalid_explicit_vault_never_falls_back(self):
        env = dict(self.env, PAPER_FLOW_VAULT=str(self.vault))
        for invalid in (self.root / "missing", self.outside):
            with self.subTest(path=invalid):
                before = snapshot(self.vault)
                self.run_cli("--vault", invalid, "sync", "--scope", "project", "--target", "all",
                             cwd=self.vault, env=env, expected=2)
                self.assertEqual(snapshot(self.vault), before)
                self.assertFalse((invalid / AGENTS).exists())

    def test_invalid_environment_vault_never_falls_back(self):
        env = dict(self.env, PAPER_FLOW_VAULT=str(self.root / "missing"))
        before = snapshot(self.vault)
        self.run_cli("sync", "--scope", "project", "--target", "all", cwd=self.vault, env=env, expected=2)
        self.assertEqual(snapshot(self.vault), before)

    def test_config_vault_is_relative_to_engine_and_overrides_upward_scan(self):
        (self.engine / "config.local.yaml").write_text(
            'vault: "../synthetic-vault"\n', encoding="utf-8")
        other = self.make_vault("upward-vault")
        before = snapshot(other)
        self.run_cli("sync", "--scope", "project", "--target", "all", cwd=other)
        self.assertTrue((self.vault / AGENTS).is_file())
        self.assertEqual(snapshot(other), before)

    def test_environment_and_explicit_options_override_invalid_config(self):
        (self.engine / "config.local.yaml").write_text("vault: [broken\n", encoding="utf-8")
        env = dict(self.env, PAPER_FLOW_VAULT=str(self.vault))
        self.run_cli("sync", "--scope", "project", "--target", "all", env=env)
        self.run_cli("--vault", self.vault, "sync", "--scope", "project", "--target", "all")

    def test_invalid_local_config_never_falls_back(self):
        for content in ('vault: "../missing"\n', "vault: [broken\n",
                        "vault: 123\n", "vault: ''\n", "- unexpected-list\n"):
            with self.subTest(config=content):
                (self.engine / "config.local.yaml").write_text(content, encoding="utf-8")
                before = snapshot(self.vault)
                self.run_cli("sync", "--scope", "project", "--target", "all", cwd=self.vault, expected=2)
                self.assertEqual(snapshot(self.vault), before)

    def test_config_without_vault_allows_upward_scan(self):
        (self.engine / "config.local.yaml").write_text("{}\n", encoding="utf-8")
        self.run_cli("sync", "--scope", "project", "--target", "all", cwd=self.vault)
        self.assertTrue((self.vault / AGENTS).is_file())


if __name__ == "__main__":
    unittest.main()
