"""Pluggable handlers that translate dataset formats into common instances."""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .contracts import DatasetContract, get_contract
from .errors import DatasetError, ParseError
from .models import DatasetInstance, ScanDiagnostic
from .parsers import (
    parse_image_timestamps,
    parse_imu_states,
    validate_calibration,
    validate_home_point,
)
from .paths import resolve_dataset_file
from .segmentation import build_segments, segments_are_current


class DatasetHandler(ABC):
    """Extension boundary for one dataset format family."""

    contract: DatasetContract
    discovery_filename: str

    @abstractmethod
    def register(self, root: Path) -> Tuple[DatasetInstance, List[ScanDiagnostic]]:
        """Parse one dataset directory into the framework-owned model."""

    @abstractmethod
    def can_reuse(self, instance: DatasetInstance, root: Path) -> bool:
        """Check whether an existing instance still matches this handler."""


class SfDatasetHandler(DatasetHandler):
    """Shared RK3399/RK3588 handler using the fixed SF file formats."""

    discovery_filename = "imu.txt"

    def __init__(self, contract: DatasetContract):
        self.contract = contract

    def register(self, root: Path) -> Tuple[DatasetInstance, List[ScanDiagnostic]]:
        expected = self.contract.expected_paths(root)
        inputs = {
            role: resolve_dataset_file(expected[role], root, role)
            for role in self.contract.required_roles
        }
        for role in self.contract.calibration_roles:
            validate_calibration(inputs[role])

        imu_records = parse_imu_states(inputs["imu_path"])
        image_timestamps = _load_image_timestamps(self.contract, inputs)
        dataset_id = _dataset_id(root, self.contract.type_id)
        segments, diagnostics = build_segments(
            self.contract.segmentation_rule,
            root,
            dataset_id,
            image_timestamps,
            imu_records,
        )
        home_path, home_diagnostics = _resolve_home_point(
            root, expected["home_point_path"]
        )
        diagnostics.extend(home_diagnostics)

        instance = DatasetInstance(
            dataset_id=dataset_id,
            dataset_type=self.contract.type_id,
            root_path=root,
            handler_version=self.contract.handler_version,
            input_paths={
                **{role: str(path) for role, path in inputs.items()},
                "home_point_path": None if home_path is None else str(home_path),
            },
            segments=tuple(segments),
        )
        return instance, diagnostics

    def can_reuse(self, instance: DatasetInstance, root: Path) -> bool:
        if (
            instance.dataset_type != self.contract.type_id
            or instance.handler_version != self.contract.handler_version
            or instance.root_path != root
            or instance.dataset_id != _dataset_id(root, self.contract.type_id)
        ):
            return False

        expected = self.contract.expected_paths(root)
        for role in self.contract.required_roles:
            path = resolve_dataset_file(expected[role], root, role)
            if instance.input_paths.get(role) != str(path):
                return False
        home = instance.input_paths.get("home_point_path")
        if home is not None:
            resolve_dataset_file(Path(home), root, "home_point_path")
        return segments_are_current(instance.dataset_id, instance.segments)


def get_handler(dataset_type: str) -> DatasetHandler:
    contract = get_contract(dataset_type)
    if contract.type_id in {"rk3399", "rk3588"}:
        return SfDatasetHandler(contract)
    raise ValueError(f"no handler registered for dataset type: {dataset_type}")


def _load_image_timestamps(
    contract: DatasetContract,
    inputs: Dict[str, Path],
) -> List[float]:
    primary_role = contract.image_timestamp_roles[0]
    primary = parse_image_timestamps(inputs[primary_role])
    for role in contract.image_timestamp_roles[1:]:
        current = parse_image_timestamps(inputs[role])
        if current != primary:
            raise ParseError(
                f"{inputs[role]}: image timestamps do not match {inputs[primary_role]}"
            )
    return primary


def _resolve_home_point(
    root: Path,
    expected_home_path: Path,
) -> Tuple[Optional[Path], List[ScanDiagnostic]]:
    if not expected_home_path.exists():
        return (
            None,
            [
                ScanDiagnostic(
                    "warning",
                    "vloc_input_missing",
                    root,
                    "缺少 home_point.txt；该数据集仍可用于 SFVision，但不能用于 VLOC",
                )
            ],
        )
    try:
        home_path = resolve_dataset_file(expected_home_path, root, "home_point_path")
        validate_home_point(home_path)
        return home_path, []
    except DatasetError as exc:
        return (
            None,
            [
                ScanDiagnostic(
                    "warning", "vloc_input_invalid", expected_home_path, str(exc)
                )
            ],
        )


def _dataset_id(root: Path, dataset_type: str) -> str:
    name = (
        re.sub(r"[^\w.-]+", "-", root.name, flags=re.UNICODE).strip("-_") or "dataset"
    )
    digest = hashlib.sha256(f"{dataset_type}\0{root}".encode("utf-8")).hexdigest()[:8]
    return f"{name}-{digest}"
