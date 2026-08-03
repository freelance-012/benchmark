"""Framework-owned contracts for supported algorithms."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

EVALUATION_WORKFLOW_SF_VO = "sf_vo"
EVALUATION_WORKFLOW_SF_VLOC = "sf_vloc"


@dataclass(frozen=True)
class EvaluationWorkflowConfig:
    """Configuration for one evaluation workflow."""

    workflow: str
    vo_filename: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"workflow": self.workflow}
        if self.vo_filename is not None:
            result["vo_filename"] = self.vo_filename
        return result

    @property
    def directory_name(self) -> str:
        if self.vo_filename is not None:
            return f"{self.workflow}_{self.vo_filename}"
        return self.workflow


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
    numbered_output_counter_relative_path: Optional[Path] = None
    progress_parser: Optional[str] = None
    dataset_run_contracts: Tuple[DatasetRunContract, ...] = ()
    evaluation_workflow: Optional[str] = None
    evaluation_workflows: Tuple[EvaluationWorkflowConfig, ...] = ()
    supported_dataset_types: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        output_paths = self.output_relative_paths
        if len(set(output_paths)) != len(output_paths):
            raise ValueError("algorithm output paths must be unique")
        counter_path = self.numbered_output_counter_relative_path
        if counter_path is not None and (
            counter_path.is_absolute() or ".." in counter_path.parts
        ):
            raise ValueError("numbered output counter path must be relative")
        run_dataset_types = tuple(
            item.dataset_type for item in self.dataset_run_contracts
        )
        if self.supported_dataset_types:
            if run_dataset_types and self.supported_dataset_types != run_dataset_types:
                raise ValueError(
                    "supported_dataset_types must match dataset_run_contracts"
                )
        else:
            object.__setattr__(self, "supported_dataset_types", run_dataset_types)
        if self.evaluation_workflows and self.evaluation_workflow is None:
            object.__setattr__(
                self, "evaluation_workflow", self.evaluation_workflows[0].workflow
            )

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
        payload: Dict[str, Any] = {
            "algorithm_id": self.algorithm_id,
            "display_name": self.display_name,
            "contract_version": self.contract_version,
            "entrypoint_relative_path": str(self.entrypoint_relative_path),
            "fixed_output_relative_paths": [
                str(item) for item in self.output_relative_paths
            ],
            "evaluation_workflow": self.evaluation_workflow,
            "supported_dataset_types": list(self.supported_dataset_types),
            "dataset_run_contracts": [
                item.to_dict() for item in self.dataset_run_contracts
            ],
        }
        if self.numbered_output_counter_relative_path is not None:
            payload["numbered_output_counter_relative_path"] = str(
                self.numbered_output_counter_relative_path
            )
        if self.progress_parser is not None:
            payload["progress_parser"] = self.progress_parser
        if self.evaluation_workflows:
            payload["evaluation_workflows"] = [
                item.to_dict() for item in self.evaluation_workflows
            ]
        return payload


_CONTRACTS = {
    "algorithm1": AlgorithmContract(
        algorithm_id="algorithm1",
        display_name="Mock SF VO Algorithm",
        contract_version=8,
        entrypoint_relative_path=Path("build/algorithm1"),
        fixed_output_relative_path=Path("mock_output.txt"),
        numbered_output_counter_relative_path=Path("log_count.txt"),
        progress_parser="benchmark_json_v1",
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
    "orbslam3_mono_sf": AlgorithmContract(
        algorithm_id="orbslam3_mono_sf",
        display_name="ORB-SLAM3 Monocular SF VO (RK3399)",
        contract_version=2,
        entrypoint_relative_path=Path("Examples/Monocular/mono_sf"),
        fixed_output_relative_path=Path("vo.txt"),
        evaluation_workflow=EVALUATION_WORKFLOW_SF_VO,
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
    "sfvision": AlgorithmContract(
        algorithm_id="sfvision",
        display_name="sfvision",
        contract_version=3,
        entrypoint_relative_path=Path("bin/sfvision"),
        numbered_output_counter_relative_path=Path("log_count.txt"),
        fixed_output_relative_path=Path("vloc.txt"),
        evaluation_workflow=EVALUATION_WORKFLOW_SF_VLOC,
        evaluation_workflows=(
            EvaluationWorkflowConfig(workflow=EVALUATION_WORKFLOW_SF_VLOC),
            EvaluationWorkflowConfig(
                workflow=EVALUATION_WORKFLOW_SF_VO, vo_filename="sf_slam.txt"
            ),
            EvaluationWorkflowConfig(
                workflow=EVALUATION_WORKFLOW_SF_VO, vo_filename="sift_vo.txt"
            ),
        ),
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
    "sf_slam": AlgorithmContract(
        algorithm_id="sf_slam",
        display_name="sf_slam",
        contract_version=0,
        entrypoint_relative_path=Path("bin/sf_slam_cli"),
        numbered_output_counter_relative_path=Path("log_count.txt"),
        fixed_output_relative_path=Path("sf_slam.txt"),
        evaluation_workflows=(
            EvaluationWorkflowConfig(
                workflow=EVALUATION_WORKFLOW_SF_VO, vo_filename="sf_slam.txt"
            ),
        ),
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
