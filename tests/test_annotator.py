"""Exercise preflight and atomic writeback on disposable, two-column PDFs."""

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pymupdf as pdf

from academic_paper_workflow import annotator as ann


def paper(path):
    with pdf.open() as doc:
        page = doc.new_page(width=620, height=500)
        page.insert_textbox((40, 35, 280, 180),
                            "Left lead. Repeated claim. Left finish.\n"
                            "The new manipu-\nlation policy improves stability.", fontsize=11)
        page.insert_textbox((330, 35, 570, 180),
                            "Right lead. Repeated claim. Right finish.\n"
                            "Two markers: signal / signal. Force 100 N.", fontsize=11)
        manual = page.add_text_annot((20, 180), "Human note")
        manual.set_info(title="Reader", subject="paper-flow:stable")
        manual.update()
        doc.new_page().insert_text((40, 40), "Replacement quote.")
        doc.save(path)


def request(quote="The new manipulation policy improves stability.", **fields):
    entry = dict(id="stable", page=1, quote=quote, color="green", comment="正文的定量条件")
    entry.update(fields)
    return {"page_base": 1, "annotations": [entry]}


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.path = self.folder / "two columns.pdf"
        self.backups = self.folder / "backups"
        paper(self.path)
        self.original = self.path.read_bytes()
        with pdf.open(self.path) as doc:
            _, self.before = ann.inventory(doc)

    def run_annotation(self, data, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return ann.annotate(self.path, data, backup_dir=self.backups, **kwargs)

    def test_multiline_update_noop_and_round_backups(self):
        first = self.run_annotation(request(), check=True)
        self.assertIsNone(first)
        self.assertEqual(list(self.folder.iterdir()), [self.path])
        first = self.run_annotation(request())
        self.assertEqual(first.read_bytes(), self.original)
        with pdf.open(self.path) as doc:
            owned, after = ann.inventory(doc)
            for xref, obj in self.before.items():
                self.assertEqual(after[xref], obj)
            page = doc[0]
            annot = page.load_annot(owned["stable"][1])
            self.assertEqual(annot.type[0], pdf.PDF_ANNOT_UNDERLINE)
            self.assertEqual(annot.info["content"], "正文的定量条件")
            self.assertEqual(len(annot.vertices), 8)
            self.assertTrue(all(point[0] < 300 for point in annot.vertices))
            self.assertTrue(all(abs(a-b) < 1e-6 for a, b in zip(annot.colors["stroke"], (16/255,124/255,65/255))))
        round_one = self.path.read_bytes()
        self.assertIsNone(self.run_annotation(request()))
        self.assertEqual(self.path.read_bytes(), round_one)
        second = self.run_annotation(request(comment="修订", color="violet"))
        self.assertNotEqual(first, second)
        self.assertEqual(second.read_bytes(), round_one)
        round_two = self.path.read_bytes()
        third = self.run_annotation(request("Replacement quote.", page=2))
        self.assertEqual(third.read_bytes(), round_two)
        with pdf.open(self.path) as doc:
            owned, after = ann.inventory(doc)
            self.assertEqual(list(owned), ["stable"])
            self.assertEqual(owned["stable"][0], 1)
            for xref, obj in self.before.items():
                self.assertEqual(after[xref], obj)
        self.assertEqual(len(list(self.backups.iterdir())), 3)

    def test_ambiguous_or_partial_aborts_complete_batch(self):
        for quote, diagnostic in [("Repeated claim.", "限定后 2 处"), ("signal", "限定后 2 处"),
                                  ("The new manipulation policy improves accuracy.", "实际匹配前缀"),
                                  ("stability. Right lead.", "完整匹配 0"),
                                  ("Force 10", "完整匹配 0")]:
            with self.subTest(quote=quote):
                data = request()
                data["annotations"].extend(request(quote, id="bad")["annotations"])
                with self.assertRaisesRegex(ValueError, diagnostic):
                    self.run_annotation(data)
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assertFalse(self.backups.exists())

    def test_context_and_region_each_choose_correct_column(self):
        for qualifiers in [dict(before="Right lead.", after="Right finish."),
                           dict(region=[300, 0, 600, 150])]:
            self.run_annotation(request("Repeated claim.", **qualifiers))
            with pdf.open(self.path) as doc:
                owned, _ = ann.inventory(doc)
                page = doc[0]
                annot = page.load_annot(owned["stable"][1])
                self.assertTrue(all(point[0] > 300 for point in annot.vertices))
        with self.assertRaisesRegex(ValueError, "限定后 0 处"):
            self.run_annotation(request("Repeated claim.", before="Unknown lead."))

    def test_legacy_zero_based_and_validation(self):
        rows = [{"page": 1, "search": "Replacement quote.", "content": "legacy", "color": "yellow"}]
        original_id = ann.read_entries(rows)[0].id
        self.run_annotation(rows)
        rows[0]["content"] = "new"
        self.assertEqual(ann.read_entries(rows)[0].id, original_id)
        self.run_annotation(rows)
        with pdf.open(self.path) as doc:
            self.assertEqual(set(ann.inventory(doc)[0]), {original_id})
        for bad in [dict(annotations=[]), request(color="unknown"), request(id=None),
                    request(region=[-3, 0, -4, 2]), request(page=True)]:
            with self.assertRaises(ValueError):
                self.run_annotation(bad)
        with self.assertRaisesRegex(ValueError, "ID 重复"):
            self.run_annotation(rows * 2)

    def test_commit_failures_leave_source_intact(self):
        for stage in ("verify", "backup"):
            with self.subTest(stage=stage), patch.object(ann, stage, side_effect=OSError(stage)):
                with self.assertRaises(OSError):
                    self.run_annotation(request())
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assertFalse(list(self.folder.glob(".paper-flow-*")))
        real_replace = os.replace
        def fail_final(src, dst):
            if dst == self.path:
                raise PermissionError("locked by reader")
            return real_replace(src, dst)
        with patch.object(ann.os, "replace", side_effect=fail_final), self.assertRaises(PermissionError):
            self.run_annotation(request())
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertTrue(all(b.read_bytes() == self.original for b in self.backups.iterdir()))
        self.assertFalse(list(self.folder.glob(".paper-flow-*")))

    def test_editor_change_during_backup_aborts_replace(self):
        other = self.original + b"\n% externally edited"
        original_backup = ann.backup
        def edited(*args):
            result = original_backup(*args)
            self.path.write_bytes(other)
            return result
        with patch.object(ann, "backup", side_effect=edited), self.assertRaisesRegex(ValueError, "已变化"):
            self.run_annotation(request())
        self.assertEqual(self.path.read_bytes(), other)

    def test_registered_cli_check_and_relative_backup_directory(self):
        parser = argparse.ArgumentParser()
        ann.register(parser)
        data = self.folder / "annotations.json"
        data.write_text(json.dumps(request()), encoding="utf-8")
        config = self.folder / "engine"
        config.mkdir()
        (config / "config.local.yaml").write_text("backup_dir: recovery\n", encoding="utf-8")
        with patch.dict(os.environ, PAPER_FLOW_ENGINE=str(config)), contextlib.redirect_stdout(io.StringIO()) as output:
            check = parser.parse_args(["--pdf", str(self.path), "--json", str(data), "--check"])
            self.assertEqual(check.handler(check), 0)
            self.assertFalse((config / "recovery").exists())
            apply = parser.parse_args(["--pdf", str(self.path), "--json", str(data)])
            self.assertEqual(apply.handler(apply), 0)
        self.assertIn("PDF 物理第 1 页", output.getvalue())
        backups = list((config / "recovery").glob("*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original)

    def test_killed_writer_releases_lock_and_keeps_pdf(self):
        code = ("import sys,time; from pathlib import Path; "
                "from academic_paper_workflow.annotator import writer_lock; "
                "ctx=writer_lock(Path(sys.argv[1])); ctx.__enter__(); print('ready', flush=True); time.sleep(30)")
        with subprocess.Popen([sys.executable, "-c", code, str(self.path)],
                              stdout=subprocess.PIPE, text=True) as proc:
            try:
                self.assertEqual(proc.stdout.readline().strip(), "ready")
                with self.assertRaisesRegex(ValueError, "正在写回"):
                    self.run_annotation(request())
                self.assertEqual(self.path.read_bytes(), self.original)
            finally:
                proc.kill()
        self.assertIsNotNone(self.run_annotation(request()))


if __name__ == "__main__":
    unittest.main()
