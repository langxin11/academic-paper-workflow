"""Install thin agent entries in this machine's user-level discovery roots."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import sys

from .resources import VERSION, resource_text
from .syncer import SyncError, _canonical, _lock, _run

MANIFEST = ".paper-flow/user-manifest.json"
ROOTS = {
    "codex": ".agents/skills",
    "antigravity": ".gemini/config/skills",
    "claude": ".claude/skills",
}
MANAGED = {f"{root}/paper-note/SKILL.md" for root in ROOTS.values()}


def _command() -> str:
    # Bind to the Python that owns this installation, independent of agent PATH.
    executable = str(Path(sys.executable).resolve())
    if os.name == "nt":
        return '& "' + executable.replace('`', '``').replace('$', '`$').replace('"', '`"') + '" -m academic_paper_workflow'
    return shlex.quote(executable) + " -m academic_paper_workflow"


def _entry() -> bytes:
    text = resource_text("agent-entry").replace("{{VERSION}}", str(VERSION))
    # Keep the shared entry unchanged; per-machine launch instructions belong here.
    text += "\n## 本机已安装引擎\n\n本入口由 `paper-flow sync --scope user` 生成。所有 `paper-flow` 命令均可用以下本机启动前缀替换，在任意目录读取已安装包的资源，无需进入 Vault 或引擎源码目录：\n\n```" + ("powershell" if os.name == "nt" else "sh") + "\n" + _command() + " show spec\n```\n\n将 `show spec` 替换为所需子命令；涉及写入 Vault 时显式传入 `--vault` 或使用本机配置。更换机器或 Python 安装后重新同步此用户入口。\n"
    return _canonical(text.encode("utf-8"))


def sync_user(target: str = "all", check: bool = False, *, home: Path | None = None) -> int:
    """Install paper-note independently of separately installed skills."""
    try:
        root = (Path.home() if home is None else Path(home)).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise SyncError(f"User home is not a directory: {root}")
        if target != "all" and target not in ROOTS:
            raise SyncError(f"Unknown sync target: {target}")
        desired = {}
        for name, skills_root in ROOTS.items():
            if target not in {"all", name}:
                continue
            desired[f"{skills_root}/paper-note/SKILL.md"] = _entry()
        options = dict(manifest_name=MANIFEST, managed=MANAGED)
        if check:
            return _run(root, desired, True, **options)
        with _lock(root, ".paper-flow/user-sync.lock"):
            return _run(root, desired, False, **options)
    except (SyncError, OSError, UnicodeError, ValueError) as exc:
        print(f"Sync error: {exc}")
        return 2
