"""User configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Union

import yaml

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
