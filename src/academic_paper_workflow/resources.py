"""Locate the same canonical resources in an editable checkout or a wheel."""

from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

VERSION = version("academic-paper-workflow")
RESOURCES = {
    "agent-entry": "core/agent-entry.md",
    "spec": "core/spec.md",
    "colors": "core/color_system.md",
    "checklist": "core/checklist.md",
    "note-prompt": "core/prompts/paper_note.md",
    "one-pager-prompt": "core/prompts/paper_one_pager.md",
    "note-template": "templates/obsidian/literature_note.md",
    "import-template": "templates/obsidian/zotero_quick_import.md",
    "one-pager-template": "templates/typst/one_pager.typ",
}


def checkout_root() -> Path | None:
    source = Path(__file__).resolve().parent
    root = source.parent.parent
    if source.parent.name == "src" and (root / "pyproject.toml").is_file():
        return root
    return None


def resource_text(name: str) -> str:
    relative = RESOURCES[name].split("/")
    checkout = checkout_root()
    root = checkout if checkout else files("academic_paper_workflow").joinpath("data")
    return root.joinpath(*relative).read_text(encoding="utf-8")
