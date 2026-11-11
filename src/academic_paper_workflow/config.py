"""Resolve per-machine settings and vaults used by the CLI."""

import os
from pathlib import Path

import yaml

from .resources import checkout_root


def selected_vault(value: str, origin: str, base: Path) -> Path:
    if not value.strip():
        raise ValueError(f"{origin} 的 Vault 路径为空")
    path = (base / Path(value).expanduser()).resolve()
    if not (path / ".obsidian").is_dir():
        raise ValueError(f"{origin} 指向的目录缺少 .obsidian：{path}")
    return path


def load_local_config() -> tuple[Path | None, dict]:
    engine = os.environ.get("PAPER_FLOW_ENGINE")
    root = Path(engine).expanduser().resolve() if engine else checkout_root()
    if root is None:
        return None, {}
    config = root / "config.local.yaml"
    if not config.is_file():
        return None, {}
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as error:
        raise ValueError(f"{config} 的 YAML 格式有误") from error
    if data is not None and not isinstance(data, dict):
        raise ValueError(f"{config} 顶层应为配置字段映射")
    return config, data or {}


def resolve_vault(explicit: str | None) -> Path:
    cwd = Path.cwd()
    if explicit is not None:
        return selected_vault(explicit, "--vault", cwd)
    if "PAPER_FLOW_VAULT" in os.environ:
        return selected_vault(os.environ["PAPER_FLOW_VAULT"], "PAPER_FLOW_VAULT", cwd)

    config, data = load_local_config()
    if config and "vault" in data:
        if not isinstance(data["vault"], str):
            raise ValueError(f"{config} 的 vault 应为路径字符串")
        return selected_vault(data["vault"], str(config), config.parent)

    for path in (cwd, *cwd.parents):
        if (path / ".obsidian").is_dir():
            return path
    raise ValueError("未找到 Obsidian Vault，请使用 --vault 或 PAPER_FLOW_VAULT。")
