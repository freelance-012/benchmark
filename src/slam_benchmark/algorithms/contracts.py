"""Framework-owned contracts for supported algorithms."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class AlgorithmContract:
    """Information the framework owns instead of asking users to configure."""

    algorithm_id: str
    display_name: str
    contract_version: int
    entrypoint_relative_path: Path
    fixed_output_relative_path: Path
    supported_dataset_types: Tuple[str, ...]


_CONTRACTS = {
    "algorithm1": AlgorithmContract(
        algorithm_id="algorithm1",
        display_name="Mock RK3588 Algorithm",
        contract_version=1,
        entrypoint_relative_path=Path("build/algorithm1"),
        fixed_output_relative_path=Path("mock_output.txt"),
        supported_dataset_types=("rk3588",),
    ),
    "algorithm2": AlgorithmContract(
        algorithm_id="algorithm2",
        display_name="Mock RK3399 Algorithm",
        contract_version=1,
        entrypoint_relative_path=Path("build/algorithm2"),
        fixed_output_relative_path=Path("mock_output.txt"),
        supported_dataset_types=("rk3399",),
    ),
    "algorithm3": AlgorithmContract(
        algorithm_id="algorithm3",
        display_name="Mock KITTI Algorithm",
        contract_version=1,
        entrypoint_relative_path=Path("build/algorithm3"),
        fixed_output_relative_path=Path("mock_output.txt"),
        supported_dataset_types=("kitti",),
    ),
    "orbslam3_mono_inertial_euroc": AlgorithmContract(
        algorithm_id="orbslam3_mono_inertial_euroc",
        display_name="ORB-SLAM3 Mono-Inertial (EuRoC)",
        contract_version=1,
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
