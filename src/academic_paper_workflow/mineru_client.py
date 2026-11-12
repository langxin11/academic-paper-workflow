"""MinerU precision API v4, one local file per batch. The caller holds the job lock."""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .parse_cache import MAX_ARCHIVE_BYTES

_API = "https://mineru.net/api/v4/"
_CHUNK = 256 * 1024
_FIELDS = {"model_version", "language", "enable_formula", "enable_table", "is_ocr"}
_ID = re.compile(r"[a-zA-Z0-9_-]{1,128}")
_STATES = {"requesting", "allocated", "uploading", "uploaded", "waiting-file",
           "pending", "running", "converting", "failed", "done"}


class ParsePending(RuntimeError):
    """The foreground session stopped. Preserve the job for a subsequent call."""


class _Rejected(RuntimeError):
    """A definite rejection of an API request."""


class _MissingBatch(_Rejected):
    """MinerU reports that the saved batch no longer exists."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _https(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme == "https" and parts.hostname and not parts.username
                and not parts.password and not parts.fragment and not re.search(r"[\x00-\x20\x7f]", url)):
            return url
    except (TypeError, ValueError):
        pass
    raise RuntimeError("MinerU returned an invalid HTTPS transfer address.")


class _DownloadRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.get_method() != "GET":
            return None
        return super().redirect_request(req, fp, code, msg, headers, _https(newurl))


class _SignedHTTPS(HTTPSHandler):
    def https_request(self, req):
        req = super().https_request(req)
        if req.get_method() == "PUT":
            # urllib otherwise labels the raw PDF application/x-www-form-urlencoded.
            req.remove_header("Content-type")
        return req


def _remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise ParsePending("Foreground wait expired. Run the same parse command to resume.")
    return min(30.0, seconds)


def _reason(value, token):
    if not isinstance(value, str):
        return "unspecified error"
    value = value.replace(token, "[redacted]")
    # Drop the entire detail if it contains a URL, query, credential or header.
    if re.search(r"(?i)://|[?&=]|bearer\s|authorization|signature|access.key|token\s*:", value):
        return "remote error (sensitive details withheld)"
    return " ".join(value.split())[:240] or "unspecified error"


def _open(opener, request, deadline):
    try:
        return opener.open(request, timeout=_remaining(deadline))
    except HTTPError as exc:
        status = exc.code
        exc.close()
        if status == 404 and request.full_url.startswith(_API + "extract-results/batch/"):
            raise _MissingBatch("MinerU cannot find this batch. Use --replace-batch to request a new one.") from None
        if status in {408, 429} or status >= 500:
            raise ParsePending(f"MinerU request returned HTTP {status}. Run again to resume.") from None
        raise _Rejected(f"MinerU request returned HTTP {status}.") from None
    except (OSError, http.client.HTTPException, URLError):
        raise ParsePending("MinerU connection interrupted. Run again to resume.") from None


def _api(path, token, deadline, body=None):
    request = Request(_API + path, headers={"Authorization": "Bearer " + token,
                                            "Content-Type": "application/json"},
                      data=None if body is None else json.dumps(body).encode())
    with _open(build_opener(_NoRedirect()), request, deadline) as response:
        payload = response.read(1024 * 1024 + 1)
    try:
        envelope = json.loads(payload)
    except (ValueError, UnicodeError):
        raise RuntimeError("Invalid response from the MinerU API.") from None
    code = envelope.get("code") if isinstance(envelope, dict) else None
    if type(code) is not int and not (isinstance(code, str) and re.fullmatch(r"A[0-9]{4}", code)):
        raise RuntimeError("MinerU response lacks a valid status code.")
    if code == -60012:
        raise _MissingBatch("MinerU cannot find this batch. Use --replace-batch to request a new one.")
    if code != 0:
        raise _Rejected(f"MinerU error {envelope['code']}: {_reason(envelope.get('msg'), token)}")
    if not isinstance(envelope.get("data"), dict):
        raise RuntimeError("MinerU response lacks task data.")
    return envelope["data"]


class _UploadStream:
    def __init__(self, source, deadline):
        self.source = source
        self.deadline = deadline

    def read(self, size):
        _remaining(self.deadline)
        return self.source.read(size)


def _upload(url, pdf, deadline):
    with pdf.open("rb") as source:
        request = Request(_https(url), method="PUT", data=_UploadStream(source, deadline),
                          headers={"Content-Length": str(os.fstat(source.fileno()).st_size)})
        with _open(build_opener(_SignedHTTPS(), _NoRedirect()), request, deadline):
            pass


def _download(url, output, deadline):
    request = Request(_https(url))
    with _open(build_opener(_DownloadRedirect()), request, deadline) as response:
        length = response.headers.get("Content-Length")
        if length is not None and not re.fullmatch(r"[0-9]{1,20}", length):
            raise RuntimeError("Invalid archive length from MinerU.")
        if length is not None and int(length) > MAX_ARCHIVE_BYTES:
            raise RuntimeError("MinerU archive exceeds the download size limit.")
        total = 0
        with output.open("wb") as target:
            while True:
                _remaining(deadline)
                chunk = response.read1(_CHUNK)
                if not chunk:
                    break
                if total + len(chunk) > MAX_ARCHIVE_BYTES:
                    raise RuntimeError("MinerU archive exceeds the download size limit.")
                total += target.write(chunk)
        if length is not None and total != int(length):
            raise ParsePending("Incomplete archive download. Run again to retrieve the same result.")


def _save(path, state):
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as handle:
            name = handle.name
            json.dump(state, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)


def _load(path, digest, options):
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if (state["version"] == 1 and state["sha256"] == digest and state["options"] == options
                and state["stage"] in _STATES and _ID.fullmatch(state["data_id"])
                and type(state["upload_confirmed"]) is bool
                and (state["stage"] == "requesting" and state["batch_id"] is None
                     or _ID.fullmatch(state["batch_id"]))):
            return state
    except (ValueError, TypeError, KeyError):
        pass
    raise RuntimeError("MinerU state is invalid or belongs to a different input. Inspect the job directory.")


def _allocate(path, digest, options, pdf, token, deadline):
    _remaining(deadline)
    state = {"version": 1, "sha256": digest, "options": options, "stage": "requesting",
             "batch_id": None, "data_id": uuid.uuid4().hex, "upload_confirmed": False}
    _save(path, state)  # Write intent first: a lost POST response cannot be replayed safely.
    body = {key: value for key, value in options.items() if key != "is_ocr"}
    body["files"] = [{"name": pdf.name, "data_id": state["data_id"], "is_ocr": options["is_ocr"]}]
    try:
        result = _api("file-urls/batch", token, deadline, body)
    except _Rejected:
        path.unlink()  # Server definitively refused the request; a corrected invocation can retry.
        raise
    batch = result.get("batch_id")
    if not isinstance(batch, str) or not _ID.fullmatch(batch):
        raise RuntimeError("MinerU returned no valid batch ID. Creation outcome is unknown.")
    state.update(batch_id=batch, stage="allocated")
    _save(path, state)
    urls = result.get("file_urls")
    if not isinstance(urls, list) or len(urls) != 1:
        raise RuntimeError("MinerU allocated a batch without a single upload address.")
    return state, _https(urls[0])


def _poll(state, token, deadline):
    batch = state["batch_id"]
    result = _api("extract-results/batch/" + batch, token, deadline)
    if result.get("batch_id", batch) != batch:
        raise RuntimeError("MinerU returned a different batch.")
    rows = result.get("extract_result")
    if not isinstance(rows, list):
        raise RuntimeError("MinerU returned an invalid batch result.")
    matches = [r for r in rows if isinstance(r, dict) and r.get("data_id") == state["data_id"]]
    if not rows:
        return None
    if len(matches) != 1:
        raise RuntimeError("MinerU result does not identify this file uniquely.")
    row = matches[0]
    if row.get("state") not in {"waiting-file", "pending", "running", "converting", "failed", "done"}:
        raise RuntimeError("MinerU reported an unknown extraction phase.")
    return row


def fetch_archive(pdf: Path, job_dir: Path, options: dict, token: str, wait_seconds: float,
                  output: Path, *, replace_batch: bool = False) -> None:
    """Download to caller-owned staging. Retain state even after successful download."""
    if not token or re.search(r"[^!-~]", token):
        raise RuntimeError("MinerU Token 格式无效，请检查本机配置或环境变量。")
    if set(options) != _FIELDS or not all(isinstance(options[k], bool) for k in
                                          ("enable_formula", "enable_table", "is_ocr")):
        raise ValueError("Invalid MinerU parsing options.")
    if not math.isfinite(wait_seconds) or wait_seconds < 0:
        raise ValueError("Foreground wait must be finite and nonnegative.")
    with pdf.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    path = job_dir / "state.json"
    deadline = time.monotonic() + wait_seconds
    state = _load(path, digest, options)
    url = None
    delay = 5.0
    try:
        if state and state["batch_id"] is None:
            if not replace_batch:
                raise ParsePending("Previous allocation outcome is unknown. Use --replace-batch to request a new batch explicitly.")
            state = None
        while True:
            if state is None:
                state, url = _allocate(path, digest, options, pdf, token, deadline)
                replace_batch = False
                state["stage"] = "uploading"
                _save(path, state)
                try:
                    _upload(url, pdf, deadline)
                except ParsePending:
                    # A disconnected PUT may have reached storage. Query before reusing its URL.
                    _remaining(deadline)
                else:
                    state.update(stage="uploaded", upload_confirmed=True)
                    _save(path, state)
                    url = None
            try:
                row = _poll(state, token, deadline)
            except _MissingBatch:
                if replace_batch:
                    state = None
                    continue
                raise
            if row:
                stage = row["state"]
                if stage != state["stage"]:
                    state["stage"] = stage
                    _save(path, state)
                if stage == "done":
                    try:
                        _download(row.get("full_zip_url"), output, deadline)
                    except BaseException:
                        output.unlink(missing_ok=True)
                        raise
                    return
                if stage == "failed":
                    if replace_batch:
                        state = None
                        continue
                    raise RuntimeError(f"MinerU extraction failed: {_reason(row.get('err_msg'), token)}. Use --replace-batch to retry.")
                if stage == "waiting-file":
                    if url is not None:
                        _upload(url, pdf, deadline)
                        state.update(stage="uploaded", upload_confirmed=True)
                        _save(path, state)
                        url = None
                    elif replace_batch:
                        state = None
                        continue
                    elif not state["upload_confirmed"]:
                        raise ParsePending("Batch is waiting for upload. Its original upload URL cannot be retrieved. "
                                           "Run again to recheck, or use --replace-batch to upload in a new batch.")
            time.sleep(min(delay, _remaining(deadline)))
            delay = min(30.0, delay * 1.5)
    except KeyboardInterrupt:
        raise ParsePending("MinerU interrupted. Run the same parse command to resume.") from None
    except (URLError, http.client.HTTPException, ConnectionError, TimeoutError):
        raise ParsePending("MinerU connection interrupted. Run the same parse command to resume.") from None
