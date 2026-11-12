"""Public commands for the academic reading workflow."""

import argparse
import sys

from . import annotator, indexer, parsing
from .config import resolve_vault
from .resources import RESOURCES, VERSION, resource_text
from .syncer import sync
from .user_sync import sync_user


def main(argv: list[str] | None = None) -> int:
    # Preserve Chinese text and LF when another process captures resource output.
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(prog="paper-flow", description="精读工作流资源与客户端入口")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--vault", metavar="PATH", help="目标 Obsidian Vault 根目录")
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("show", help="输出包内规范、提示词或模板")
    show.add_argument("resource", choices=list(RESOURCES))
    deploy = commands.add_parser("sync", help="预览或同步受管入口和正式模板")
    deploy.add_argument("--scope", choices=["user", "project"], default=None, help="默认 user；项目同步必须显式 --scope project")
    deploy.add_argument("--target", choices=["all", "antigravity", "codex", "claude"], default="all")
    deploy.add_argument("--check", action="store_true", help="只预览，待写入时退出码为 1")
    indexer.register(commands.add_parser("index", help="重建文献台账、登记精读笔记"))
    parsing.register(commands.add_parser("parse", help="从 PDF 或 Zotero 附件 key 查找材料，按需使用 MinerU"))
    annotator.register(commands.add_parser("annotate", help="预检引文并安全写入 PDF 内嵌批注"))
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            sys.stdout.write(resource_text(args.resource))
            return 0
        if args.command == "sync":
            scope = args.scope or "user"
            if scope == "user":
                if args.vault:
                    raise ValueError("--scope user 不使用 --vault；项目同步请用 --scope project")
                return sync_user(args.target, args.check)
            return sync(resolve_vault(args.vault), args.target, args.check)
        return args.handler(args)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"paper-flow: {error}", file=sys.stderr)
        return 2
