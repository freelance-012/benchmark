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
    parse_kitti_timestamps,
    validate_calibration,
    validate_kitti_calibration,
    validate_kitti_poses,
)
from .paths import is_within, resolve_dataset_directory, resolve_dataset_file
from .segmentation import build_segments, segments_are_current


class DatasetHandler(ABC):
    """Extension boundary for one dataset format family."""

    contract: DatasetContract
    discovery_filename: str

    @abstractmethod
    def register(
        self,
        root: Path,
        collection_root: Path,
    ) -> Tuple[DatasetInstance, List[ScanDiagnostic]]:
        """Parse one dataset directory into the framework-owned model."""

    @abstractmethod
    def can_reuse(
        self,
        instance: DatasetInstance,
        root: Path,
        collection_root: Path,
    ) -> bool:
        """Check whether an existing instance still matches this handler."""


class SfDatasetHandler(DatasetHandler):
    """Shared RK3399/RK3588 handler using the fixed SF file formats."""

    discovery_filename = "imu.txt"

    def __init__(self, contract: DatasetContract):
        self.contract = contract

    def register(
        self,
        root: Path,
        collection_root: Path,
    ) -> Tuple[DatasetInstance, List[ScanDiagnostic]]:
        del collection_root
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

        instance = DatasetInstance(
            dataset_id=dataset_id,
            dataset_type=self.contract.type_id,
            root_path=root,
            handler_version=self.contract.handler_version,
            input_paths={role: str(path) for role, path in inputs.items()},
            segments=tuple(segments),
        )
        return instance, diagnostics

    def can_reuse(
        self,
        instance: DatasetInstance,
        root: Path,
        collection_root: Path,
    ) -> bool:
        del collection_root
        if (
            instance.dataset_type != self.contract.type_id
            or instance.handler_version != self.contract.handler_version
            or instance.root_path != root
            or instance.dataset_id != _dataset_id(root, self.contract.type_id)
        ):
            return False

        expected_roles = set(self.contract.required_roles)
        if set(instance.input_paths) != expected_roles:
            return False
        expected = self.contract.expected_paths(root)
        for role in self.contract.required_roles:
            path = resolve_dataset_file(expected[role], root, role)
            if instance.input_paths.get(role) != str(path):
                return False
        return segments_are_current(instance.dataset_id, instance.segments)


class KittiDatasetHandler(DatasetHandler):
    """KITTI Odometry handler for one official sequence directory."""

    discovery_filename = "times.txt"
    _STEREO_PAIRS = (
        ("image_0", "image_1", ("P0", "P1")),
        ("image_2", "image_3", ("P2", "P3")),
    )
    _IMAGE_FILENAME = re.compile(r"^\d{6}\.png$")

    def __init__(self, contract: DatasetContract):
        self.contract = contract

    def register(
        self,
        root: Path,
        collection_root: Path,
    ) -> Tuple[DatasetInstance, List[ScanDiagnostic]]:
        expected = self.contract.expected_paths(root)
        inputs = {
            role: resolve_dataset_file(expected[role], root, role)
            for role in self.contract.required_roles
        }
        left_dir, right_dir, projection_keys = self._resolve_stereo_pair(root)
        validate_kitti_calibration(inputs["calibration_path"], projection_keys)
        image_names = self._validate_image_pair(left_dir, right_dir, root)
        timestamps = parse_kitti_timestamps(inputs["image_timestamps_path"])
        if len(timestamps) != len(image_names):
            raise ParseError(
                f"{inputs['image_timestamps_path']}: timestamp count "
                f"{len(timestamps)} does not match stereo image count "
                f"{len(image_names)}"
            )

        dataset_id = _dataset_id(root, self.contract.type_id)
        segments, diagnostics = build_segments(
            self.contract.segmentation_rule,
            root,
            dataset_id,
            timestamps,
        )
        ground_truth_path, ground_truth_diagnostics = self._resolve_ground_truth(
            root,
            collection_root,
            len(timestamps),
        )
        diagnostics.extend(ground_truth_diagnostics)
        instance = DatasetInstance(
            dataset_id=dataset_id,
            dataset_type=self.contract.type_id,
            root_path=root,
            handler_version=self.contract.handler_version,
            input_paths={
                **{role: str(path) for role, path in inputs.items()},
                "left_image_dir": str(left_dir),
                "right_image_dir": str(right_dir),
                "ground_truth_path": (
                    None if ground_truth_path is None else str(ground_truth_path)
                ),
            },
            segments=tuple(segments),
        )
        return instance, diagnostics

    def can_reuse(
        self,
        instance: DatasetInstance,
        root: Path,
        collection_root: Path,
    ) -> bool:
        if (
            instance.dataset_type != self.contract.type_id
            or instance.handler_version != self.contract.handler_version
            or instance.root_path != root
            or instance.dataset_id != _dataset_id(root, self.contract.type_id)
        ):
            return False

        expected_roles = set(self.contract.required_roles) | {
            "left_image_dir",
            "right_image_dir",
            "ground_truth_path",
        }
        if set(instance.input_paths) != expected_roles:
            return False
        expected = self.contract.expected_paths(root)
        for role in self.contract.required_roles:
            path = resolve_dataset_file(expected[role], root, role)
            if instance.input_paths.get(role) != str(path):
                return False
        left_dir, right_dir, _ = self._resolve_stereo_pair(root)
        if instance.input_paths.get("left_image_dir") != str(left_dir):
            return False
        if instance.input_paths.get("right_image_dir") != str(right_dir):
            return False

        ground_truth = instance.input_paths.get("ground_truth_path")
        if ground_truth is not None:
            try:
                path = resolve_dataset_file(
                    Path(ground_truth),
                    collection_root,
                    "ground_truth_path",
                )
            except DatasetError:
                return False
            if path != self._ground_truth_candidate(root):
                return False
        return segments_are_current(instance.dataset_id, instance.segments)

    def _resolve_stereo_pair(
        self,
        root: Path,
    ) -> Tuple[Path, Path, Tuple[str, str]]:
        for left_name, right_name, projection_keys in self._STEREO_PAIRS:
            left = root / left_name
            right = root / right_name
            if left.is_dir() and right.is_dir():
                return (
                    resolve_dataset_directory(left, root, "left_image_dir"),
                    resolve_dataset_directory(right, root, "right_image_dir"),
                    projection_keys,
                )
        raise DatasetError(
            f"{root}: KITTI Odometry 需要完整的 image_0/image_1 "
            "或 image_2/image_3 双目目录"
        )

    def _validate_image_pair(
        self,
        left_dir: Path,
        right_dir: Path,
        root: Path,
    ) -> Tuple[str, ...]:
        left_names = self._image_names(left_dir, root, "left_image_dir")
        right_names = self._image_names(right_dir, root, "right_image_dir")
        if left_names != right_names:
            raise ParseError(
                f"{root}: KITTI left and right image filenames do not match"
            )
        expected = tuple(f"{index:06d}.png" for index in range(len(left_names)))
        if left_names != expected:
            raise ParseError(
                f"{root}: KITTI image filenames must be contiguous from 000000.png"
            )
        return left_names

    def _image_names(self, directory: Path, root: Path, role: str) -> Tuple[str, ...]:
        images = sorted(directory.glob("*.png"), key=lambda path: path.name)
        if not images:
            raise ParseError(f"{directory}: KITTI image directory is empty")
        for image in images:
            if not self._IMAGE_FILENAME.fullmatch(image.name):
                raise ParseError(f"{image}: invalid KITTI image filename")
            resolve_dataset_file(image, root, f"{role}/{image.name}")
        return tuple(image.name for image in images)

    def _resolve_ground_truth(
        self,
        root: Path,
        collection_root: Path,
        expected_rows: int,
    ) -> Tuple[Optional[Path], List[ScanDiagnostic]]:
        candidate = self._ground_truth_candidate(root)
        if not is_within(candidate, collection_root) or not candidate.is_file():
            return (
                None,
                [
                    ScanDiagnostic(
                        "warning",
                        "kitti_ground_truth_missing",
                        root,
                        "缺少 KITTI poses/序列号.txt；数据可运行，但暂不能进行真值评估",
                    )
                ],
            )
        try:
            path = resolve_dataset_file(candidate, collection_root, "ground_truth_path")
            validate_kitti_poses(path, expected_rows)
            return path, []
        except DatasetError as exc:
            return (
                None,
                [
                    ScanDiagnostic(
                        "warning",
                        "kitti_ground_truth_invalid",
                        root,
                        str(exc),
                    )
                ],
            )

    @staticmethod
    def _ground_truth_candidate(root: Path) -> Path:
        if root.parent.name == "sequences":
            return (root.parent.parent / "poses" / f"{root.name}.txt").resolve()
        return (root / "poses.txt").resolve()


def get_handler(dataset_type: str) -> DatasetHandler:
    contract = get_contract(dataset_type)
    if contract.type_id in {"rk3399", "rk3588"}:
        return SfDatasetHandler(contract)
    if contract.type_id == "kitti":
        return KittiDatasetHandler(contract)
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


def _dataset_id(root: Path, dataset_type: str) -> str:
    name = (
        re.sub(r"[^\w.-]+", "-", root.name, flags=re.UNICODE).strip("-_") or "dataset"
    )
    digest = hashlib.sha256(f"{dataset_type}\0{root}".encode("utf-8")).hexdigest()[:8]
    return f"{name}-{digest}"
