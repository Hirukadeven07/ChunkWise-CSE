"""Load and check the YAML config for an extraction run."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEVICES = ("auto", "cpu", "cuda", "mps", "xpu")
TABLE_MODES = ("accurate", "fast")


class ConfigError(ValueError):
    """The config file is missing, malformed or has an invalid value."""


@dataclass(frozen=True)
class DoclingSettings:
    """How Docling converts each PDF."""

    device: str = "auto"
    ocr: bool = False
    table_mode: str = "accurate"
    layout_model: str | None = None


@dataclass(frozen=True)
class ValidationSettings:
    """Checks run on each PDF before conversion (see validate.py)."""

    enabled: bool = True
    min_chars_per_page: int = 50
    min_text_ratio: float = 0.5


@dataclass(frozen=True)
class Config:
    """Settings for one extraction run."""

    input_dir: Path
    output_dir: Path
    exclude_dirs: frozenset[str] = frozenset()
    docling: DoclingSettings = field(default_factory=DoclingSettings)
    validation: ValidationSettings = field(default_factory=ValidationSettings)


def load_config(path: str | Path) -> Config:
    """Read a YAML config. Relative paths are resolved from the config file's folder."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a mapping of settings")

    base = path.resolve().parent
    return Config(
        input_dir=_path(raw, "input_dir", base),
        output_dir=_path(raw, "output_dir", base),
        exclude_dirs=frozenset(str(name).lower() for name in raw.get("exclude_dirs") or []),
        docling=_docling(raw.get("docling") or {}),
        validation=_validation(raw.get("validation") or {}),
    )


def _path(raw: dict, key: str, base: Path) -> Path:
    value = raw.get(key)
    if not value:
        raise ConfigError(f"'{key}' is required")
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _docling(raw: dict) -> DoclingSettings:
    if not isinstance(raw, dict):
        raise ConfigError("'docling' must be a mapping of settings")
    settings = DoclingSettings(
        device=str(raw.get("device", "auto")).lower(),
        ocr=bool(raw.get("ocr", False)),
        table_mode=str(raw.get("table_mode", "accurate")).lower(),
        layout_model=raw.get("layout_model") or None,
    )
    if settings.device not in DEVICES:
        raise ConfigError(f"docling.device must be one of {', '.join(DEVICES)}, not '{settings.device}'")
    if settings.table_mode not in TABLE_MODES:
        raise ConfigError(
            f"docling.table_mode must be one of {', '.join(TABLE_MODES)}, not '{settings.table_mode}'"
        )
    return settings


def _validation(raw: dict) -> ValidationSettings:
    if not isinstance(raw, dict):
        raise ConfigError("'validation' must be a mapping of settings")
    try:
        settings = ValidationSettings(
            enabled=bool(raw.get("enabled", True)),
            min_chars_per_page=int(raw.get("min_chars_per_page", 50)),
            min_text_ratio=float(raw.get("min_text_ratio", 0.5)),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid validation setting: {exc}") from exc
    if settings.min_chars_per_page < 0:
        raise ConfigError("validation.min_chars_per_page cannot be negative")
    if not 0 <= settings.min_text_ratio <= 1:
        raise ConfigError("validation.min_text_ratio must be between 0 and 1")
    return settings
