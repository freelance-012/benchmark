"""Built-in dataset type contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

INSTANCE_FILENAME = "benchmark_dataset.yaml"
MIN_SEGMENT_FRAMES = 200
MIN_SEGMENT_DURATION_SECONDS = 10.0


@dataclass(frozen=True)
class DatasetContract:
    """Fixed file contract for one supported dataset type."""

    type_id: str
    handler_version: int
    required_files: Tuple[Tuple[str, str], ...]
    image_timestamp_roles: Tuple[str, ...]
    calibration_roles: Tuple[str, ...]
    segmentation_rule: str

    def expected_paths(self, root: Path) -> Dict[str, Path]:
        return {
            **{role: root / filename for role, filename in self.required_files},
            "home_point_path": root / "home_point.txt",
        }

    @property
    def required_roles(self) -> Tuple[str, ...]:
        return tuple(role for role, _ in self.required_files)


_CONTRACTS = {
    "rk3399": DatasetContract(
        type_id="rk3399",
        handler_version=2,
        required_files=(
            ("imu_path", "imu.txt"),
            ("image_path", "img.avi"),
            ("image_timestamps_path", "imgts.txt"),
            ("calibration_path", "calib_raw.yaml"),
        ),
        image_timestamp_roles=("image_timestamps_path",),
        calibration_roles=("calibration_path",),
        segmentation_rule="flight_mode",
    ),
    "rk3588": DatasetContract(
        type_id="rk3588",
        handler_version=3,
        required_files=(
            ("imu_path", "imu.txt"),
            ("bottom_video_0_path", "video_bottom_0.h265"),
            ("bottom_video_1_path", "video_bottom_1.h265"),
            ("front_video_0_path", "video_front_0.h265"),
            ("front_video_1_path", "video_front_1.h265"),
            ("bottom_image_timestamps_path", "imgts_bottom.txt"),
            ("front_image_timestamps_path", "imgts_front.txt"),
            ("bottom_calibration_path", "bottom_calib_raw.yaml"),
            ("front_calibration_path", "front_calib_raw.yaml"),
        ),
        image_timestamp_roles=(
            "bottom_image_timestamps_path",
            "front_image_timestamps_path",
        ),
        calibration_roles=(
            "bottom_calibration_path",
            "front_calibration_path",
        ),
        segmentation_rule="flight_mode",
    ),
    "kitti": DatasetContract(
        type_id="kitti",
        handler_version=1,
        required_files=(
            ("image_timestamps_path", "times.txt"),
            ("calibration_path", "calib.txt"),
        ),
        image_timestamp_roles=("image_timestamps_path",),
        calibration_roles=("calibration_path",),
        segmentation_rule="timestamp",
    ),
}


def get_contract(dataset_type: str) -> DatasetContract:
    normalized = str(dataset_type).strip().lower()
    try:
        return _CONTRACTS[normalized]
    except KeyError as exc:
        choices = ", ".join(sorted(_CONTRACTS))
        raise ValueError(
            f"dataset.type must be one of: {choices}; got {dataset_type!r}"
        ) from exc


def supported_dataset_types() -> Tuple[str, ...]:
    return tuple(sorted(_CONTRACTS))
