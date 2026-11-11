"""Public paper-flow commands for phase one."""

import argparse
import sys

from .config import resolve_vault
from .resources import RESOURCES, VERSION, resource_text
from .syncer import sync


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
    deploy.add_argument("--target", choices=["all", "antigravity", "codex", "claude"], default="all")
    deploy.add_argument("--check", action="store_true", help="只预览，待写入时退出码为 1")
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            sys.stdout.write(resource_text(args.resource))
            return 0
        return sync(resolve_vault(args.vault), args.target, args.check)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"paper-flow: {error}", file=sys.stderr)
        return 2
