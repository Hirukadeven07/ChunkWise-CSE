from pathlib import Path

import pytest

from cse_extract.config import ConfigError, load_config


def test_loads_settings_and_resolves_relative_paths(config_file: Path, report_dir: Path):
    config = load_config(config_file)
    assert config.input_dir == report_dir
    assert config.output_dir == config_file.parent / "output"
    assert config.exclude_dirs == frozenset({"interim"})
    assert config.docling.device == "auto"
    assert config.docling.table_mode == "accurate"
    assert config.docling.ocr is False


def test_docling_settings_are_read(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "input_dir: in\noutput_dir: out\n"
        "docling: {device: CUDA, table_mode: fast, layout_model: layout_egret_large}\n"
    )
    settings = load_config(path).docling
    assert settings.device == "cuda"
    assert settings.table_mode == "fast"
    assert settings.layout_model == "layout_egret_large"


def test_default_config_in_repo_loads():
    config = load_config(Path(__file__).parents[1] / "configs" / "default.yaml")
    assert config.input_dir.name == "Annual Reports"


@pytest.mark.parametrize(
    "text, message",
    [
        ("output_dir: out\n", "'input_dir' is required"),
        ("input_dir: in\n", "'output_dir' is required"),
        ("input_dir: in\noutput_dir: out\ndocling: {device: tpu}\n", "docling.device"),
        ("input_dir: in\noutput_dir: out\ndocling: {table_mode: slow}\n", "docling.table_mode"),
        ("- just\n- a list\n", "mapping"),
        ("input_dir: [unclosed\n", "not valid YAML"),
    ],
)
def test_bad_config_is_rejected(tmp_path: Path, text: str, message: str):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_missing_file_is_rejected(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")
