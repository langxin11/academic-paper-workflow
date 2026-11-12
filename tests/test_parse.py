import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import pymupdf

from academic_paper_workflow.cli import main


class AttachmentParseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.zotero = self.root / "Zotero"
        self.folder = self.zotero / "storage" / "SJ552ALW"
        self.folder.mkdir(parents=True)
        self.cache = self.root / "engine cache"
        self.config = self.root / "config.local.yaml"
        self.pdf = self.folder / "Yan 中文.pdf"
        with pymupdf.open() as doc:
            doc.new_page()
            doc.save(self.pdf)
        env = patch.dict(os.environ, {"PAPER_FLOW_ENGINE":str(self.root), "MINERU_API_KEY":""})
        env.start()
        self.addCleanup(env.stop)
        self.config.write_text("parse:\n  zotero_data_dir: Zotero\n  cache_dir: engine cache\n", "utf8")

    def call(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["parse", "--json", *map(str,args)])
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else {}, stderr.getvalue()

    def by_key(self, *args):
        return self.call("--attachment-key", "sj552alw", *args)

    def material(self, root):
        root.mkdir(parents=True)
        note = root / "full.md"
        note.write_text("# 真实来源应在输出中注明", "utf8")
        return note

    def test_attachment_key_and_explicit_pdf_select_same_source_offline(self):
        code, record, _ = self.by_key()
        self.assertEqual(code, 0)
        self.assertEqual(record["source"], "original-pdf")
        self.assertEqual(record["pdf"], str(self.pdf))
        self.assertEqual(record["sha256"], hashlib.sha256(self.pdf.read_bytes()).hexdigest())
        self.assertEqual(self.call("--pdf", self.pdf)[1], record)
        self.assertFalse(self.cache.exists())

    def test_invalid_parent_missing_or_ambiguous_attachment(self):
        for key in ("bad/../../", "VUYKP4P2"):
            self.assertEqual(self.call("--attachment-key", key)[0], 2)
        (self.folder / "second.pdf").write_bytes(self.pdf.read_bytes())
        code, _, msg = self.by_key()
        self.assertEqual(code, 2)
        self.assertIn("second.pdf", msg)
        self.assertIn(self.pdf.name, msg)
        self.assertIsNone(self.call("--pdf", self.pdf)[1]["attachment_key"])

    def test_existing_results_choose_or_disambiguate(self):
        native = self.material(self.folder/"mineru")
        second = self.material(self.root/"external"/"SJ552ALW")
        self.assertEqual(self.by_key("--cloud")[1]["full_md"], str(native))
        code, _, msg = self.by_key("--results-dir", self.root/"external")
        self.assertEqual(code, 2)
        self.assertIn(str(second), msg)
        self.assertEqual(self.by_key("--materials", second.parent)[1]["full_md"], str(second))
        self.assertEqual(self.by_key("--language","ch")[1]["source"],"original-pdf")

    def test_plugin_provenance_and_stale_timestamp(self):
        plugin = self.zotero/"llm-for-zotero-mineru"/"1219"
        note = self.material(plugin)
        marker = plugin/"_llm_source.json"
        pdf_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        os.utime(self.pdf, (pdf_time.timestamp(), pdf_time.timestamp()))
        record={"kind":"llm-for-zotero/mineru-cache-source","attachmentKey":"SJ552ALW",
                "sourceFilename":self.pdf.name,"parsedAt":(pdf_time+timedelta(seconds=1)).isoformat()}
        for field, val in (("attachmentKey","WRONGKEY"),("sourceFilename","other.pdf"),
                           ("parsedAt",(pdf_time-timedelta(days=1)).isoformat())):
            marker.write_text(json.dumps(dict(record, **{field:val})), "utf8")
            self.assertEqual(self.by_key()[1]["source"], "original-pdf")
        marker.write_text(json.dumps(record), "utf8")
        self.assertEqual(self.by_key()[1]["full_md"],str(note))

    def test_unusable_external_materials_and_cache_location(self):
        self.material(self.folder/"mineru").write_text("![](missing.png)", "utf8")
        code, record, msg = self.by_key()
        self.assertEqual((code,record["source"]), (0,"original-pdf"))
        self.assertIn("无效",msg)
        self.assertEqual(self.by_key("--materials",self.folder/"mineru")[0], 2)
        vault=self.root/"Vault"
        (vault/".obsidian").mkdir(parents=True)
        for path in (vault/"cache",self.folder/"cache"):
            self.assertEqual(self.by_key("--cache-dir",path)[0], 2)
            self.assertFalse(path.exists())

    def test_cloud_cache_reuse_and_model_identity(self):
        def transfer(pdf, job, options, token, wait, out, **_):
            self.assertEqual(pdf.read_bytes(), self.pdf.read_bytes())
            with zipfile.ZipFile(out,"w") as zipout:
                zipout.writestr("full.md","本文用于测试")
        with patch.dict(os.environ,{"MINERU_API_KEY":"local-dummy"}), patch(
            "academic_paper_workflow.parsing.fetch_archive",side_effect=transfer
        ) as fetch:
            first=self.by_key("--cloud")[1]
            self.assertEqual(first["source"],"mineru-cloud")
            with patch.dict(os.environ,{"MINERU_API_KEY":""}):
                cached=self.by_key("--cloud")[1]
                self.assertEqual((cached["source"], cached["full_md"]),("cache",first["full_md"]))
                self.assertEqual(self.by_key("--cloud","--model-version","vlm")[0],2)
            self.assertEqual(fetch.call_count,1)
            Path(first["full_md"]).write_text("modified","utf8")
            self.assertEqual(self.by_key()[1]["source"],"original-pdf")
            self.assertEqual(self.by_key("--cloud")[1]["source"],"mineru-cloud")
            self.assertEqual(fetch.call_count,2)

    def test_private_config_token_and_environment_override(self):
        self.config.write_text(self.config.read_text("utf8") + '  api_key: "config-test-token"\n', "utf8")
        with patch("academic_paper_workflow.parsing.fetch_archive", side_effect=RuntimeError("测试拒绝请求")) as fetch:
            for env, expected in [("", "config-test-token"), ("session-test-token", "session-test-token")]:
                with patch.dict(os.environ, {"MINERU_API_KEY": env}):
                    code, _, message = self.by_key("--cloud")
                self.assertEqual(code, 2)
                self.assertEqual(fetch.call_args.args[3], expected)
                self.assertNotIn(expected, message)
            for f in self.cache.rglob("*.json"):
                self.assertNotIn("test-token", f.read_text("utf8"))

    def test_token_configuration_errors_do_not_echo_secrets(self):
        for line in ['api_key: ["private-test-value"]', 'api_key: "private-test-value']:
            self.config.write_text("parse:\n  zotero_data_dir: Zotero\n  " + line, "utf8")
            code, _, error = self.by_key("--cloud")
            self.assertEqual(code, 2)
            self.assertNotIn("private-test-value", error)
