"""User configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Union

import yaml

from .algorithms.contracts import get_algorithm_contract
from .compilation.models import BuildConfig
from .datasets.contracts import get_contract
from .datasets.errors import ConfigError
from .datasets.models import DatasetScanConfig


def load_dataset_config(config_path: Union[str, Path]) -> DatasetScanConfig:
    path = Path(config_path).expanduser()
    if not path.is_file():
        raise ConfigError(f"configuration file does not exist: {path}")

    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read configuration file {path}: {exc}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("dataset"), dict):
        raise ConfigError("configuration must contain a dataset mapping")

    dataset = payload["dataset"]
    try:
        root_value = dataset["root_path"]
        type_value = dataset["type"]
    except KeyError as exc:
        raise ConfigError(f"missing dataset.{exc.args[0]}") from exc

    root = Path(str(root_value)).expanduser().resolve()
    if not root.is_dir():
        raise ConfigError(f"dataset.root_path must be a readable directory: {root}")

    try:
        contract = get_contract(str(type_value))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    return DatasetScanConfig(root_path=root, dataset_type=contract.type_id)


def load_build_config(config_path: Union[str, Path]) -> BuildConfig:
    path = Path(config_path).expanduser()
    if not path.is_file():
        raise ConfigError(f"configuration file does not exist: {path}")

    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read configuration file {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ConfigError("configuration root must be a mapping")
    if not isinstance(payload.get("build"), dict):
        raise ConfigError("configuration must contain a build mapping")
    if "algorithm" not in payload:
        raise ConfigError("missing algorithm")

    algorithm_value = payload["algorithm"]
    if not isinstance(algorithm_value, str) or not algorithm_value.strip():
        raise ConfigError("algorithm must be a non-empty string")
    try:
        contract = get_algorithm_contract(algorithm_value)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    build = payload["build"]
    try:
        algorithm_path = _absolute_config_path(
            build["algorithm_path"],
            "build.algorithm_path",
        )
        script_path = _absolute_config_path(
            build["script_path"],
            "build.script_path",
        )
    except KeyError as exc:
        raise ConfigError(f"missing build.{exc.args[0]}") from exc

    return BuildConfig(
        algorithm_id=contract.algorithm_id,
        algorithm_path=algorithm_path,
        script_path=script_path,
    )


def _absolute_config_path(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConfigError(f"{field} must be an absolute path: {value}")
    return path.resolve()
