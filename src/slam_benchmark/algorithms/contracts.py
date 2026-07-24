"""Framework-owned contracts for supported algorithms."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

EVALUATION_WORKFLOW_SF_VO = "sf_vo"
EVALUATION_WORKFLOW_SF_VLOC = "sf_vloc"


@dataclass(frozen=True)
class DatasetRunContract:
    """Ordered algorithm inputs for one supported dataset type."""

    dataset_type: str
    required_input_roles: Tuple[str, ...]
    optional_input_roles: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_type": self.dataset_type,
            "required_input_roles": list(self.required_input_roles),
            "optional_input_roles": list(self.optional_input_roles),
        }


@dataclass(frozen=True)
class AlgorithmContract:
    """Information the framework owns instead of asking users to configure."""

    algorithm_id: str
    display_name: str
    contract_version: int
    entrypoint_relative_path: Path
    fixed_output_relative_path: Path
    additional_output_relative_paths: Tuple[Path, ...] = ()
    output_validator: str = "not_configured"
    dataset_run_contracts: Tuple[DatasetRunContract, ...] = ()
    evaluation_workflow: Optional[str] = None
    supported_dataset_types: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        output_paths = self.output_relative_paths
        if len(set(output_paths)) != len(output_paths):
            raise ValueError("algorithm output paths must be unique")
        run_dataset_types = tuple(
            item.dataset_type for item in self.dataset_run_contracts
        )
        if self.supported_dataset_types:
            if run_dataset_types and self.supported_dataset_types != run_dataset_types:
                raise ValueError(
                    "supported_dataset_types must match dataset_run_contracts"
                )
            return
        object.__setattr__(self, "supported_dataset_types", run_dataset_types)

    def run_contract_for(self, dataset_type: str) -> DatasetRunContract:
        normalized = str(dataset_type).strip().lower()
        for item in self.dataset_run_contracts:
            if item.dataset_type == normalized:
                return item
        if normalized in self.supported_dataset_types:
            raise ValueError(
                f"{self.algorithm_id} supports {normalized} compilation only; "
                "its run contract is not implemented"
            )
        choices = ", ".join(self.supported_dataset_types)
        raise ValueError(
            f"{self.algorithm_id} supports dataset types: {choices}; "
            f"got {dataset_type!r}"
        )

    @property
    def output_relative_paths(self) -> Tuple[Path, ...]:
        return (self.fixed_output_relative_path,) + tuple(
            self.additional_output_relative_paths
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "algorithm_id": self.algorithm_id,
            "display_name": self.display_name,
            "contract_version": self.contract_version,
            "entrypoint_relative_path": str(self.entrypoint_relative_path),
            "fixed_output_relative_paths": [
                str(item) for item in self.output_relative_paths
            ],
            "output_validator": self.output_validator,
            "evaluation_workflow": self.evaluation_workflow,
            "supported_dataset_types": list(self.supported_dataset_types),
            "dataset_run_contracts": [
                item.to_dict() for item in self.dataset_run_contracts
            ],
        }


_CONTRACTS = {
    "algorithm1": AlgorithmContract(
        algorithm_id="algorithm1",
        display_name="Mock SF VO Algorithm",
        contract_version=5,
        entrypoint_relative_path=Path("build/algorithm1"),
        fixed_output_relative_path=Path("mock_output.txt"),
        output_validator="mock_key_value",
        evaluation_workflow=EVALUATION_WORKFLOW_SF_VO,
        dataset_run_contracts=(
            DatasetRunContract(
                dataset_type="rk3588",
                required_input_roles=(
                    "imu_path",
                    "bottom_video_0_path",
                    "bottom_video_1_path",
                    "front_video_0_path",
                    "front_video_1_path",
                    "bottom_image_timestamps_path",
                    "front_image_timestamps_path",
                    "bottom_calibration_path",
                    "front_calibration_path",
                ),
            ),
            DatasetRunContract(
                dataset_type="rk3399",
                required_input_roles=(
                    "imu_path",
                    "image_path",
                    "image_timestamps_path",
                    "calibration_path",
                ),
            ),
        ),
    ),
    "algorithm2": AlgorithmContract(
        algorithm_id="algorithm2",
        display_name="Mock RK3399 Algorithm",
        contract_version=4,
        entrypoint_relative_path=Path("build/algorithm2"),
        fixed_output_relative_path=Path("mock_output.txt"),
        additional_output_relative_paths=(Path("home_point.txt"),),
        output_validator="mock_key_value",
        evaluation_workflow=EVALUATION_WORKFLOW_SF_VLOC,
        dataset_run_contracts=(
            DatasetRunContract(
                dataset_type="rk3399",
                required_input_roles=(
                    "imu_path",
                    "image_path",
                    "image_timestamps_path",
                    "calibration_path",
                ),
            ),
        ),
    ),
    "algorithm3": AlgorithmContract(
        algorithm_id="algorithm3",
        display_name="Mock KITTI Algorithm",
        contract_version=4,
        entrypoint_relative_path=Path("build/algorithm3"),
        fixed_output_relative_path=Path("mock_output.txt"),
        output_validator="mock_key_value",
        dataset_run_contracts=(
            DatasetRunContract(
                dataset_type="kitti",
                required_input_roles=(
                    "image_timestamps_path",
                    "calibration_path",
                    "left_image_dir",
                    "right_image_dir",
                ),
                optional_input_roles=("ground_truth_path",),
            ),
        ),
    ),
    "orbslam3_mono_inertial_euroc": AlgorithmContract(
        algorithm_id="orbslam3_mono_inertial_euroc",
        display_name="ORB-SLAM3 Mono-Inertial (EuRoC)",
        contract_version=2,
        entrypoint_relative_path=Path(
            "Examples/Monocular-Inertial/mono_inertial_euroc"
        ),
        fixed_output_relative_path=Path("f_vo.txt"),
        supported_dataset_types=("euroc",),
    ),
}


def get_algorithm_contract(algorithm_id: str) -> AlgorithmContract:
    normalized = str(algorithm_id).strip().lower()
    try:
        return _CONTRACTS[normalized]
    except KeyError as exc:
        choices = ", ".join(sorted(_CONTRACTS))
        raise ValueError(
            f"algorithm must be one of: {choices}; got {algorithm_id!r}"
        ) from exc


def supported_algorithm_ids() -> Tuple[str, ...]:
    return tuple(sorted(_CONTRACTS))
