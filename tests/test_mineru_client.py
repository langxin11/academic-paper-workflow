import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, call
from urllib.error import HTTPError
from urllib.request import Request

from academic_paper_workflow import mineru_client as client


class MinerUBatchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.job = Path(tmp.name)
        self.pdf = self.job / "input.pdf"
        self.pdf.write_bytes(b"%PDF-test")
        self.output = self.job / "staged.zip"
        self.token = "private-token"
        self.url = "https://storage.example/upload?secret=signed"
        self.options = {"model_version": "pipeline", "language": "en", "is_ocr": False,
                        "enable_formula": True, "enable_table": True}
        self.rows = []
        self.created = []
        self.fetch_api = self.enterContext(patch.object(client, "_api", side_effect=self.api))
        self.upload = self.enterContext(patch.object(client, "_upload"))
        self.download = self.enterContext(patch.object(client, "_download"))
        self.sleep = self.enterContext(patch.object(client.time, "sleep"))

    def api(self, path, token, deadline, body=None):
        self.assertEqual(token, self.token)
        if body:
            self.assertEqual(path, "file-urls/batch")
            self.created.append(body)
            return {"batch_id": f"batch-{len(self.created)}", "file_urls": [self.url]}
        self.assertEqual(path, f"extract-results/batch/batch-{len(self.created)}")
        row = self.rows.pop(0)
        if isinstance(row, BaseException):
            raise row
        if row is None:
            return {"extract_result": []}
        return {"extract_result": [{"data_id": self.created[-1]["files"][0]["data_id"], **row}]}

    def run_fetch(self, **kwargs):
        client.fetch_archive(self.pdf, self.job, self.options, self.token, 60, self.output, **kwargs)

    def state(self):
        raw = (self.job / "state.json").read_text("utf-8")
        for secret in (self.token, self.url, "?secret"):
            self.assertNotIn(secret, raw)
        return json.loads(raw)

    def test_complete_upload_parameters_backoff_and_redownload_without_new_batch(self):
        done = {"state": "done", "full_zip_url": self.url}
        self.rows = [None, {"state": "waiting-file"}, {"state": "pending"}, {"state": "running"},
                     {"state": "converting"}, *[{"state": "running"}] * 3, done, done]
        self.run_fetch()
        self.run_fetch()
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.upload.call_count, 1)
        self.assertEqual(self.download.call_count, 2)
        self.assertEqual(self.sleep.call_args_list[:2], [call(5), call(7.5)])
        self.assertEqual(self.sleep.call_args_list[-1], call(30))
        state = self.state()
        self.assertEqual(state["stage"], "done")
        body = dict(self.created[0])
        self.assertEqual(body.pop("files"), [{"name": "input.pdf", "data_id": state["data_id"],
                                              "is_ocr": False}])
        self.assertEqual(body, {k: v for k, v in self.options.items() if k != "is_ocr"})

    def test_interrupted_after_upload_resumes_without_duplicate(self):
        self.rows = [KeyboardInterrupt(), {"state": "done", "full_zip_url": self.url}]
        with self.assertRaises(client.ParsePending):
            self.run_fetch()
        self.assertTrue(self.state()["upload_confirmed"])
        self.run_fetch()
        self.assertEqual(len(self.created), 1)
        self.upload.assert_called_once()

    def test_interrupted_during_upload_queries_before_explicit_replacement(self):
        self.upload.side_effect = [KeyboardInterrupt(), None]
        with self.assertRaises(client.ParsePending):
            self.run_fetch()
        self.assertEqual(self.state()["stage"], "uploading")
        self.rows = [{"state": "waiting-file"}, {"state": "waiting-file"},
                     {"state": "done", "full_zip_url": self.url}]
        with self.assertRaisesRegex(client.ParsePending, "--replace-batch"):
            self.run_fetch()
        self.assertEqual(len(self.created), 1)
        self.run_fetch(replace_batch=True)
        self.assertEqual(len(self.created), 2)
        self.assertEqual(self.state()["batch_id"], "batch-2")

    def test_lost_put_response_reuses_live_url_only_after_query(self):
        self.upload.side_effect = [client.ParsePending("PUT disconnected"), None]
        self.rows = [{"state": "waiting-file"}, {"state": "done", "full_zip_url": self.url}]
        self.run_fetch()
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.upload.call_args_list[0], self.upload.call_args_list[1])

    def test_unknown_allocation_is_never_replayed_implicitly(self):
        with patch.object(client, "_api", side_effect=client.ParsePending("network")) as request:
            with self.assertRaises(client.ParsePending):
                self.run_fetch()
            with self.assertRaisesRegex(client.ParsePending, "unknown"):
                self.run_fetch()
            request.assert_called_once()
        self.rows = [{"state": "done", "full_zip_url": self.url}]
        self.run_fetch(replace_batch=True)

    def test_deleted_batch_requires_explicit_replacement(self):
        self.rows = [KeyboardInterrupt(), client._MissingBatch("deleted"),
                     client._MissingBatch("deleted"), {"state": "done", "full_zip_url": self.url}]
        with self.assertRaises(client.ParsePending):
            self.run_fetch()
        with self.assertRaisesRegex(RuntimeError, "deleted"):
            self.run_fetch()
        self.assertEqual(len(self.created), 1)
        self.run_fetch(replace_batch=True)
        self.assertEqual(len(self.created), 2)

    def test_failed_remote_reason_and_secrets(self):
        self.rows = [{"state": "failed", "err_msg": "PDF is corrupt"},
                     {"state": "failed", "err_msg": self.url + " " + self.token}]
        with self.assertRaisesRegex(RuntimeError, "PDF is corrupt"):
            self.run_fetch()
        with self.assertRaises(RuntimeError) as ctx:
            self.run_fetch()
        self.assertNotIn(self.token, str(ctx.exception))
        self.assertNotIn(self.url, str(ctx.exception))
        self.assertEqual(self.state()["stage"], "failed")

    def test_interrupted_download_removes_partial_then_retrieves_same_result(self):
        def partial(*args):
            self.output.write_bytes(b"partial")
            raise TimeoutError()

        self.rows = [{"state": "done", "full_zip_url": self.url}] * 2
        self.download.side_effect = partial
        with self.assertRaises(client.ParsePending):
            self.run_fetch()
        self.assertFalse(self.output.exists())
        self.download.side_effect = None
        self.run_fetch()
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.download.call_count, 2)

    def test_deadline_preserves_remote_running_state(self):
        clock = [0.]
        self.sleep.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        def api(path, token, deadline, body=None):
            client._remaining(deadline)
            return self.api(path, token, deadline, body)
        self.fetch_api.side_effect = api
        self.rows = [{"state": "running"}] * 10
        with patch.object(client.time, "monotonic", side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(client.ParsePending, "expired"):
                self.run_fetch()
        self.assertEqual(clock[0], 60)
        self.assertEqual(self.state()["stage"], "running")


class SignedTransportTests(unittest.TestCase):
    def test_signed_requests_are_https_only_and_upload_has_no_bearer_or_content_type(self):
        for url in ("file:///a", "http://example.org", "https://name:password@example.org", None):
            with self.assertRaises(RuntimeError):
                client._https(url)
        request = Request("https://storage.example/a", method="PUT", data=io.BytesIO(b"pdf"),
                          headers={"Content-Length": "3"})
        # The HTTPS opener runs default request normalization before sending.
        handler = client._SignedHTTPS()
        handler.add_parent(Mock(addheaders=[]))
        self.assertEqual(handler.https_request(request).get_header("Content-length"), "3")
        self.assertIsNone(request.get_header("Content-type"))
        self.assertIsNone(request.get_header("Authorization"))
        protected = Request("https://mineru.net/api", headers={"Authorization": "Bearer test"})
        self.assertIsNone(client._NoRedirect().redirect_request(
            protected, None, 302, "", {}, "https://elsewhere.example"))

    def test_declared_and_streamed_download_limits(self):
        for headers in ({"Content-Length": "999"}, {}, {"Content-Length": "1"}):
            response = io.BytesIO(b"abcd")
            response.headers = headers
            with self.subTest(headers=headers), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "zip"
                with patch.object(client, "_open", return_value=response), patch.object(client, "MAX_ARCHIVE_BYTES", 3):
                    with self.assertRaisesRegex(RuntimeError, "size limit"):
                        client._download("https://storage.example/archive", output, client.time.monotonic()+10)
                if output.exists():
                    self.assertLessEqual(output.stat().st_size, 3)

    def test_batch_http_not_found_distinguished_from_auth_and_storage_errors(self):
        for url, code, missing in ((client._API + "extract-results/batch/id", 404, True),
                                   (client._API + "extract-results/batch/id", 401, False),
                                   (client._API + "extract-results/batch/id", 403, False),
                                   ("https://storage.example/archive", 404, False)):
            opener = Mock()
            opener.open.side_effect = HTTPError(url, code, "failure", {}, None)
            with self.subTest(url=url, code=code), self.assertRaises(client._Rejected) as caught:
                client._open(opener, Request(url), client.time.monotonic() + 10)
            self.assertEqual(isinstance(caught.exception, client._MissingBatch), missing)

    def test_truncated_download_detected_and_streaming_upload_checks_deadline(self):
        response = io.BytesIO(b"abc")
        response.headers = {"Content-Length": "999"}
        deadline = client.time.monotonic() + 5
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, "_open", return_value=response):
            with self.assertRaisesRegex(client.ParsePending, "Incomplete archive"):
                client._download("https://storage.example/archive", Path(tmp) / "zip", deadline)
        stream = client._UploadStream(io.BytesIO(b"abcd"), deadline)
        self.assertEqual(stream.read(2), b"ab")
        stream.deadline = 0
        with self.assertRaises(client.ParsePending):
            stream.read(2)
