"""Synthetic-only index generation, incremental registration and failure safety."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from academic_paper_workflow import indexer


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="paper-flow-index-")
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve()
        (self.vault / ".obsidian").mkdir()
        self.notes = self.vault / indexer.DEFAULT_NOTES
        self.notes.mkdir(parents=True)
        self.output = self.vault / indexer.DEFAULT_OUTPUT
        self.target = self.vault / indexer.DEFAULT_MAINTAINED[0]
        self.target.parent.mkdir(parents=True)
        self.target.write_text("# 人工入口\n\n## 阅读路线\n\n原有人工作品。\n", encoding="utf-8")
        self.config = {}
        self.loader = patch.object(indexer, "load_local_config", side_effect=lambda: (None, self.config))
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def note(self, name="Paper 2025", metadata="year: 2025\nproject: tactile-grasping\nstatus: done", body="## 一句话总结\n\n核心机制与结果。"):
        path = self.notes / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\n{metadata}\n---\n\n{body}\n", encoding="utf-8")
        return path

    def run_index(self, *flags):
        parser = argparse.ArgumentParser()
        parser.add_argument("--vault", default=str(self.vault))
        indexer.register(parser)
        args = parser.parse_args(flags)
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            code = indexer.handler(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_recursive_history_duplicates_and_stable_check(self):
        first = self.note("sub/相同 2025", body="> [!NOTE] 核心启发与复用价值\n> - **本质创新**：**看板机制** | 2 ms")
        second = self.note("other/相同 2025", metadata="year: '2024'\nproject:\n  - tactile-perception", body="## One-Sentence Take\n\n历史导读。")
        third = self.note("Legacy 2020", metadata="year: 2020", body="## Summary\n\n旧版摘要。")
        before = {p: p.read_bytes() for p in (first, second, third, self.target)}
        self.assertEqual(self.run_index("--check")[0], 1)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.run_index()[0], 0)
        content = self.output.read_text(encoding="utf-8")
        self.assertIn("sub/相同 2025\\|相同 2025", content)
        self.assertIn("other/相同 2025\\|相同 2025", content)
        self.assertIn("看板机制 &#124; 2 ms", content)
        self.assertIn("Summary 首条", content)
        stamp = self.output.stat().st_mtime_ns
        self.assertEqual(self.run_index("--check")[0], 0)
        self.assertEqual(self.run_index()[0], 0)
        self.assertEqual(stamp, self.output.stat().st_mtime_ns)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_frontmatter_bom_unicode_and_malformed(self):
        data, problem = indexer.parse_frontmatter("\ufeff---\r\nyear: 2026\r\nproject: [触觉, control]\r\n---\r\n")
        self.assertIsNone(problem)
        self.assertEqual(data["year"], 2026)
        self.assertEqual(data["project"][0], "触觉")
        for text in ("# old", "---\nx: [\n---\n", "---\n[]\n---\n", "---\nx: 1"):
            self.assertIsNotNone(indexer.parse_frontmatter(text)[1])

    def test_missing_metadata_remains_searchable(self):
        (self.notes / "No YAML 2024.md").write_text("# 历史笔记\n", encoding="utf-8")
        self.assertEqual(self.run_index()[0], 0)
        content = self.output.read_text(encoding="utf-8")
        self.assertIn("No YAML 2024", content)
        self.assertIn("缺少 frontmatter", content)
        self.assertIn("缺少一句话导读", content)

    def test_configured_paths_aliases_and_no_assets_indexing(self):
        self.notes = self.vault / "notes"
        self.notes.mkdir()
        self.note(metadata="year: 2026\nproject: novel")
        self.note("assets/ignored", metadata="project: novel")
        self.config = {"index": {"notes_dir": "notes", "output": "indices/all.md", "group_aliases": {"novel": "专题 A"}, "maintained_indexes": []}}
        self.assertEqual(self.run_index()[0], 0)
        text = (self.vault / "indices/all.md").read_text(encoding="utf-8")
        self.assertIn("专题 A（1 篇）", text)
        self.assertNotIn("ignored", text)

    def test_authored_output_refused_but_registration_independent(self):
        note = self.note()
        self.output.write_text("# 用户维护\n秘密路线\n", encoding="utf-8")
        code, _, errors = self.run_index("--note", note.relative_to(self.vault).as_posix(), "--guide", "入口导读")
        self.assertEqual(code, 2)
        self.assertIn("自动总索引失败", errors)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "# 用户维护\n秘密路线\n")
        self.assertIn("入口导读", self.target.read_text(encoding="utf-8"))

    def test_preserve_authored_surroundings_bom_and_crlf(self):
        self.note()
        self.run_index()
        old = self.output.read_bytes()
        self.output.write_bytes(b"\xef\xbb\xbf# authored\r\n\r\n" + old + b"\r\nauthored tail\r\n")
        self.note("New 2026")
        self.run_index()
        new = self.output.read_bytes()
        self.assertTrue(new.startswith(b"\xef\xbb\xbf# authored\r\n\r\n"))
        self.assertTrue(new.endswith(b"\r\nauthored tail\r\n"))

    def test_registration_check_idempotence_refresh_and_backlog(self):
        note = self.note()
        flags = ("--note", note.relative_to(self.vault).as_posix(), "--guide", "可复用机制 | 量化", "--backlog", "待核 Eq. 4")
        before = self.target.read_bytes()
        self.assertEqual(self.run_index(*flags, "--check")[0], 1)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(self.run_index(*flags)[0], 0)
        after = self.target.read_bytes()
        self.assertEqual(self.run_index(*flags)[0], 0)
        self.assertEqual(after, self.target.read_bytes())
        text = after.decode("utf-8")
        self.assertIn("## 待补收录", text)
        self.assertIn("可复用机制 &#124; 量化", text)
        self.assertIn("待核 Eq. 4", text)
        self.assertTrue(text.startswith(before.decode("utf-8").rstrip("\n")))
        self.assertEqual(self.run_index(*flags[:-1], "待核 Eq. 5")[0], 0)
        self.assertNotIn("待核 Eq. 4", self.target.read_text(encoding="utf-8"))

    def test_existing_authored_registration_preserved(self):
        note = self.note()
        self.target.write_text("# 原文\n- [[Paper 2025]] — 人工解释。\n", encoding="utf-8")
        before = self.target.read_bytes()
        self.run_index("--note", note.relative_to(self.vault).as_posix(), "--guide", "自动解释")
        self.assertEqual(self.target.read_bytes(), before)

    def test_reject_overlap_and_escape_before_writes(self):
        note = self.note()
        for options in ({"output": indexer.DEFAULT_MAINTAINED[0]}, {"output": indexer.DEFAULT_NOTES + "/generated.md"}, {"output": "../outside.md"}, {"output": "C:\\outside.md"}, {"maintained_indexes": [note.relative_to(self.vault).as_posix()]}):
            self.config = {"index": options}
            before = self.target.read_bytes()
            with self.assertRaises(ValueError):
                self.run_index()
            self.assertEqual(self.target.read_bytes(), before)
            self.assertFalse(self.output.exists())

    def test_atomic_failure_preserves_output_and_cleans_temp(self):
        self.note()
        self.run_index()
        before = self.output.read_bytes()
        self.note("New 2026")
        with patch.object(indexer.os, "replace", side_effect=OSError("simulated replacement failure")):
            self.assertEqual(self.run_index()[0], 2)
        self.assertEqual(self.output.read_bytes(), before)
        self.assertFalse(list(self.output.parent.glob("*.tmp")))

    def test_stale_write_preserves_concurrent_version(self):
        self.output.write_text("new concurrent data", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            indexer.atomic_write(self.output, "ours", b"previous version")
        self.assertEqual(self.output.read_text(encoding="utf-8"), "new concurrent data")
        self.assertFalse(list(self.output.parent.glob("*.tmp")))

    def test_invalid_registration_does_not_block_total(self):
        note = self.note(body="# no guide")
        code, _, errors = self.run_index("--note", note.relative_to(self.vault).as_posix())
        self.assertEqual(code, 2)
        self.assertTrue(self.output.exists())
        self.assertIn("人工索引登记失败", errors)

    def test_generated_frontmatter_is_file_leading(self):
        self.note()
        self.run_index()
        text = self.output.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertEqual(indexer.parse_frontmatter(text)[0]["status"], "auto")

    def test_registration_moves_between_backlog_and_routes(self):
        note = self.note()
        flags = ("--note", note.relative_to(self.vault).as_posix(), "--guide", "可用模块")
        self.run_index(*flags)
        self.run_index(*flags, "--backlog", "待核参数")
        text = self.target.read_text(encoding="utf-8")
        self.assertEqual(text.count("paper-flow:entry:"), 2)
        self.assertGreater(text.index("待核参数"), text.index("## 待补收录"))
        self.run_index(*flags)
        text = self.target.read_text(encoding="utf-8")
        self.assertNotIn("待核参数", text)
        self.assertLess(text.index("paper-flow:entry:"), text.index("## 待补收录"))
        self.assertIn("原有人工作品。", text)

    def test_authored_route_does_not_prevent_explicit_backlog(self):
        note = self.note()
        self.target.write_text("# 入口\n\n## 人工路线\n- [[Paper 2025]] — 人工导读\n", encoding="utf-8")
        self.run_index("--note", note.relative_to(self.vault).as_posix(), "--backlog", "待核结论")
        text = self.target.read_text(encoding="utf-8")
        self.assertIn("人工导读", text)
        self.assertIn("待核结论", text)

    def test_duplicate_stems_do_not_match_ambiguous_manual_links(self):
        note = self.note("sub/Same")
        self.note("other/Same")
        self.target.write_text("# 人工索引\n- [[Same]] — 旧模糊链接\n", encoding="utf-8")
        self.run_index("--note", note.relative_to(self.vault).as_posix())
        self.assertIn("文献笔记/sub/Same|Same", self.target.read_text(encoding="utf-8"))

    def test_non_markdown_targets_rejected(self):
        self.note()
        for options in ({"output": "index.json"}, {"maintained_indexes": ["route.txt"]}):
            self.config = {"index": options}
            with self.assertRaises(ValueError):
                self.run_index()
            self.assertFalse(self.output.exists())

    def test_legacy_unmarked_metadata_and_manual_rows_retained(self):
        self.note()
        legacy = "---\ntags: [auto-generated]\nstatus: auto\n---\n\n# 文献笔记总索引（自动生成）\n\n> 由 `scripts/build_literature_index.py` 生成，**请勿手改**；新增/修改笔记后重跑脚本即可。\n\n| 笔记 |\n|---|\n| [[Paper 2025]] |\n"
        self.output.write_text(legacy + "\n人工补充路线\n", encoding="utf-8")
        self.assertEqual(self.run_index()[0], 2)
        self.assertEqual(self.output.read_text(encoding="utf-8"), legacy + "\n人工补充路线\n")
        legacy = legacy.replace("status: auto", "status: auto\ncustom: 人工元数据") + "| [[Manual paper]] | 人工行 |\n"
        self.output.write_text(legacy, encoding="utf-8")
        code, _, errors = self.run_index()
        self.assertEqual(code, 2)
        self.assertIn("快照", errors)
        self.assertEqual(self.output.read_text(encoding="utf-8"), legacy)

    def test_cross_process_lock_held_until_operation_finishes(self):
        with indexer.file_lock(self.output):
            with self.assertRaises(RuntimeError):
                with indexer.file_lock(self.output):
                    self.fail("same target must be exclusive")
        with indexer.file_lock(self.output):
            pass

    def test_check_never_takes_or_creates_lock(self):
        self.note()
        with patch.object(indexer, "file_lock", side_effect=AssertionError("read-only must not lock")):
            self.assertEqual(self.run_index("--check")[0], 1)
        self.assertFalse(self.output.exists())

    def test_assets_and_excalidraw_excluded_from_scan_and_registration(self):
        self.note()
        excluded = [self.note("assets/metadata"), self.note("Drawing.excalidraw")]
        self.assertEqual(self.run_index()[0], 0)
        text = self.output.read_text(encoding="utf-8")
        self.assertIn("共 1 篇", text)
        before = self.output.read_bytes()
        for path in excluded:
            with self.assertRaises(ValueError):
                self.run_index("--note", path.relative_to(self.vault).as_posix())
        self.assertEqual(before, self.output.read_bytes())

    def test_frontmatter_delimiter_spaces_and_tabs(self):
        metadata, problem = indexer.parse_frontmatter("---  \t\r\nyear: 2026\r\nstatus: done\r\n---  \r\n# Title")
        self.assertIsNone(problem)
        self.assertEqual(metadata, {"year": 2026, "status": "done"})

    def test_lock_namespace_shared_with_pdf_writer(self):
        from academic_paper_workflow.annotator import writer_lock
        with writer_lock(self.output):
            with self.assertRaises(RuntimeError):
                with indexer.file_lock(self.output):
                    self.fail("workflow writers must share target lock")


if __name__ == "__main__":
    unittest.main()
