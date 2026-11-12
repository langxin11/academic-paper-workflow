"""Resolve one Zotero attachment or PDF and prepare its reading materials."""
import hashlib
import json
import math
import os
from datetime import datetime
from pathlib import Path
import re
import shutil
import sys
import tempfile

import pymupdf

from ._locking import local_lock
from .config import load_local_config
from .mineru_client import ParsePending, fetch_archive
from .parse_cache import MaterialValidationError, cached_result, publish_archive, validate_materials


DEFAULT_OPTIONS = {"model_version": "pipeline", "language": "en", "enable_formula": True,
                   "enable_table": True, "is_ocr": False}
KEY = re.compile(r"[A-Z0-9]{8}")


def register(parser):
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--attachment-key", help="Zotero 附件的 8 位 key")
    source.add_argument("--pdf", type=Path, help="明确的 PDF 文件")
    parser.add_argument("--zotero-data-dir", help="含 storage/ 的 Zotero 数据目录")
    parser.add_argument("--cache-dir", help="Vault 和 Zotero 之外的引擎缓存目录")
    parser.add_argument("--results-dir", help="已有材料的总目录，按 PDF SHA-256 或附件 key 分目录")
    parser.add_argument("--materials", type=Path, help="由调用者确认属于这篇 PDF 的已有解析目录")
    parser.add_argument("--skip-existing", action="store_true", help="跳过外部插件材料，仍会复用引擎的指纹缓存")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--cloud", dest="mode", action="store_const", const="cloud", help="缺少有效材料时上传至 MinerU")
    mode.add_argument("--local-only", "--offline", dest="mode", action="store_const", const="local", help="只使用本地材料")
    parser.add_argument("--model-version", choices=("pipeline", "vlm"))
    parser.add_argument("--language", help="MinerU 文档语言代码，默认 en")
    parser.add_argument("--wait-seconds", type=float, help="本轮最多等待的秒数，默认 600")
    parser.add_argument("--replace-batch", action="store_true", help="遇到无法恢复的云批次时显式重新申请")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出来源和文件的绝对路径")
    parser.set_defaults(handler=run)


def _path(explicit, cfg, field, config_base, default=None):
    if explicit is not None:
        raw, base = explicit, Path.cwd()
    else:
        raw, base = cfg.get(field, default), config_base
    if raw is None:
        return None
    if not isinstance(raw, (str, Path)) or not str(raw).strip():
        raise ValueError(f"parse.{field} 需要非空路径")
    return (base / Path(raw).expanduser()).resolve()


def _pdfs(folder):
    return sorted(p for p in folder.iterdir() if p.suffix.lower() == ".pdf" and p.is_file())


def _source(args, storage):
    if args.attachment_key is None:
        path = args.pdf.expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ValueError("--pdf 需要 PDF 文件")
        folder = path.parent
        key = folder.name if folder.parent == storage and KEY.fullmatch(folder.name) else None
        if key and len(_pdfs(folder)) != 1:
            key = None
        return path, key
    key = args.attachment_key.strip().upper()
    if not KEY.fullmatch(key):
        raise ValueError("Zotero 附件 key 应为 8 位字母或数字；也可用 --pdf 指定文件")
    folder = storage / key
    if not folder.is_dir() or folder.resolve() != folder:
        raise ValueError(f"找不到存储型附件 {key}；父文献 key 或链接附件请改用 --pdf")
    candidates = _pdfs(folder)
    if len(candidates) != 1:
        found = "、".join(f.name for f in candidates) or "无 PDF"
        raise ValueError(f"{key} 包含 {found}；请用 --pdf 明确指定附件")
    path = candidates[0].resolve(strict=True)
    if path.parent != folder.resolve():
        raise ValueError("附件指向其他目录；请用 --pdf 指定实际文件")
    return path, key


def _digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _plugin_results(plugin, pdf, key, fingerprint):
    if not plugin.is_dir() or not key:
        return
    for record in plugin.glob("*/_llm_source.json"):
        try:
            if record.stat().st_size > 64 * 1024:
                continue
            data = json.loads(record.read_text("utf-8-sig"))
            if not isinstance(data, dict) or (data.get("kind"), data.get("attachmentKey"), data.get("sourceFilename")) != (
                "llm-for-zotero/mineru-cache-source", key, pdf.name
            ):
                continue
            if data.get("pdf_sha256"):
                if data["pdf_sha256"] != fingerprint:
                    continue
            elif not data.get("parsedAt") or datetime.fromisoformat(data["parsedAt"]).timestamp() < pdf.stat().st_mtime:
                continue
        except (OSError, UnicodeError, ValueError, TypeError):
            continue
        yield record.parent


def _local_materials(pdf, key, fingerprint, library, plugin, options):
    # External plugin results usually carry no parameter manifest. Custom settings
    # must not silently reuse the results of a different OCR/model configuration.
    if options != DEFAULT_OPTIONS:
        return None
    roots = []
    if library:
        roots.append(library / fingerprint)
        if key:
            roots.append(library / key)
    if key:
        roots.append(pdf.parent / "mineru")
        roots.extend(_plugin_results(plugin, pdf, key, fingerprint))
    if not key and (pdf.parent / "mineru").is_dir() and len(_pdfs(pdf.parent)) == 1:
        roots.append(pdf.parent / "mineru")
    candidates = {}
    for root in roots:
        if root.is_dir():
            for p in root.rglob("full.md"):
                candidates.setdefault(p.absolute(), root)
    unique = sorted(candidates)
    if len(unique) > 1:
        raise ValueError("存在多个已有解析，请用 --materials 选择：" + "；".join(map(str, unique)))
    if unique:
        try:
            return validate_materials(candidates[unique[0]])
        except MaterialValidationError as error:
            print(f"忽略无效的已有解析：{error}", file=sys.stderr)
    return None


def _options(args, cfg):
    options = {k: cfg.get(k,v) for k,v in DEFAULT_OPTIONS.items()}
    if args.model_version:
        options["model_version"] = args.model_version
    if args.language is not None:
        options["language"] = args.language
    if options["model_version"] not in ("pipeline", "vlm"):
        raise ValueError("parse.model_version 需要 pipeline 或 vlm")
    if not isinstance(options["language"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", options["language"]):
        raise ValueError("parse.language 需要 MinerU 语言代码")
    for field in ("enable_formula", "enable_table", "is_ocr"):
        if type(options[field]) is not bool:
            raise ValueError(f"parse.{field} 应为布尔值")
    return options


def _remember(job, fingerprint, options):
    path = job / "input.json"
    identity = {"pdf_sha256": fingerprint, "parameters": options}
    if path.is_file():
        if json.loads(path.read_bytes()) != identity:
            raise ValueError("缓存输入指纹或解析参数不一致")
        return
    with tempfile.NamedTemporaryFile(dir=job, delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            f.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def run(args):
    config, settings = load_local_config()
    cfg = settings.get("parse", {})
    if not isinstance(cfg, dict):
        raise ValueError("parse 配置应为字段映射")
    config_base = config.parent if config else Path.cwd()
    storage = (_path(args.zotero_data_dir, cfg, "zotero_data_dir", config_base, Path.home() / "Zotero") / "storage").resolve()
    pdf, key = _source(args, storage)
    with pymupdf.open(pdf) as opened:
        if not opened.is_pdf or opened.needs_pass or not opened.page_count:
            raise ValueError("需要可读取且已解锁的有效 PDF")
        pages = opened.page_count
    sha = _digest(pdf)
    options = _options(args, cfg)
    cache = _path(args.cache_dir, cfg, "cache_dir", config_base, Path.home() / ".cache/paper-flow/mineru")
    if cache.is_relative_to(storage) or any((p / ".obsidian").is_dir() for p in (cache, *cache.parents)):
        raise ValueError("解析缓存必须位于 Zotero storage 与 Obsidian Vault 之外")
    library = _path(args.results_dir, cfg, "results_dir", config_base)
    mode = args.mode or cfg.get("mode", "local")
    if mode not in ("cloud", "local") or (args.replace_batch and mode != "cloud"):
        raise ValueError("parse.mode 需要 local 或 cloud；--replace-batch 仅用于云解析")
    wait = args.wait_seconds if args.wait_seconds is not None else cfg.get("wait_seconds", 600)
    if type(wait) not in (int, float) or not math.isfinite(wait) or wait < 0:
        raise ValueError("等待秒数必须是有限的非负数")
    opt_key = hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()
    job = cache / sha / opt_key
    if job.resolve() != job:
        raise ValueError("缓存任务目录经过了重定向")
    info = {"attachment_key": key, "pdf": str(pdf), "sha256": sha, "pages": pages}

    def valid_cache():
        identity = job / "input.json"
        if not identity.is_file():
            return None
        if json.loads(identity.read_bytes()) != {"pdf_sha256": sha, "parameters": options}:
            raise ValueError("缓存身份与当前 PDF 或解析参数不一致")
        return cached_result(job)

    def report(source, full=None, **extra):
        image_dir = full.parent / "images" if full else None
        result = dict(info, source=source, full_md=str(full) if full else None,
                      images_dir=str(image_dir) if image_dir and image_dir.is_dir() else None, **extra)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"{source} · {full or pdf}")
            if "message" in extra:
                print(extra["message"])
        return 3 if source == "pending" else 0

    if args.materials is not None:
        if args.skip_existing:
            raise ValueError("--materials 不能与 --skip-existing 同用")
        return report("existing", validate_materials(args.materials.expanduser().absolute()))
    full = valid_cache()
    if full:
        return report("cache", full)
    plugin = storage.parent / "llm-for-zotero-mineru"
    full = None if args.skip_existing else _local_materials(pdf, key, sha, library, plugin, options)
    if full:
        return report("existing", full)
    if mode == "local":
        return report("original-pdf", message="未找到可复用的解析，使用原 PDF。")
    token = os.environ.get("MINERU_API_KEY", "").strip()
    if not token:
        configured = cfg.get("api_key")
        if configured is not None and not isinstance(configured, str):
            raise ValueError("config.local.yaml 的 parse.api_key 应为带引号的字符串")
        token = (configured or "").strip()
    if not token:
        raise ValueError("云解析需要在 config.local.yaml 填写 parse.api_key，或设置 MINERU_API_KEY")
    if pages > 600 or pdf.stat().st_size > 200 * 1024 * 1024:
        raise ValueError("超出 MinerU 精准解析单文件 200 MB 或 600 页限制")
    with local_lock(job, message="同一文件与参数已有解析进程，请稍后重试", error_type=RuntimeError):
        job.mkdir(parents=True, exist_ok=True)
        full = valid_cache()
        if full:
            return report("cache", full)
        _remember(job, sha, options)
        # Pin the exact bytes named by the cache identity, even if Zotero replaces
        # the attachment during an upload. Temporary input/output are never assets.
        with tempfile.TemporaryDirectory(prefix="transfer-", dir=job) as transfer:
            snapshot = Path(transfer) / pdf.name
            shutil.copyfile(pdf, snapshot)
            if _digest(snapshot) != sha:
                raise ValueError("PDF 在准备上传时发生变化；请重试")
            archive = Path(transfer) / "result.zip"
            try:
                fetch_archive(snapshot, job, options, token, wait, archive,
                              replace_batch=args.replace_batch)
            except (ParsePending, KeyboardInterrupt) as exc:
                return report("pending", message=str(exc) or "已中断，重跑相同输入可续查。",
                              state=str(job / "state.json") if (job / "state.json").is_file() else None)
            full = publish_archive(archive, job)
            return report("mineru-cloud", full)
