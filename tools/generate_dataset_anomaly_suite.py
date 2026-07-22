#!/usr/bin/env python3
"""Generate compact dataset fixtures that exercise dataset-manager boundaries."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path(tempfile.gettempdir()) / "slam_benchmark_dataset_anomaly_suite"
MARKER = ".generated_by_slam_benchmark"

VALID_CALIBRATION = """%YAML:1.0
T_imu_body: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
cam0:
  T_cam_imu: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
"""
VALID_KITTI_PROJECTION = "1 0 0 0 0 1 0 0 0 0 1 0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="suite output directory",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove the generated suite instead of creating it",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output.expanduser().resolve()
    if args.clean:
        _clean_output(output)
        print(f"removed anomaly suite: {output}")
        return 0
    _prepare_output(output)

    manifest: Dict[str, Any] = {
        "suite_version": 1,
        "purpose": "SLAM Benchmark 数据集管理异常识别测试",
        "notes": [
            "每个目录只引入一个主要异常。",
            "视频使用极小占位文件，只测试当前数据集管理模块，不用于算法运行。",
            "按要求不生成文件末尾 NUL 填充案例。",
            "missing_imu 案例用于暴露以 imu.txt 发现候选目录的当前行为。",
        ],
        "runs": {},
        "config_cases": [],
    }

    _generate_rk3399_cases(output, manifest)
    _generate_rk3588_cases(output, manifest)
    _generate_kitti_cases(output, manifest)
    _generate_instance_cases(output, manifest)
    _generate_config_cases(output, manifest)
    _write_manifest(output, manifest)
    _write_readme(output)
    print(f"generated anomaly suite: {output}")
    return 0


def _prepare_output(output: Path) -> None:
    if output.exists():
        if not (output / MARKER).is_file():
            raise RuntimeError(f"refusing to replace unmarked directory: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    (output / MARKER).write_text("generated; safe to replace\n", encoding="utf-8")


def _clean_output(output: Path) -> None:
    if not output.exists():
        return
    if not (output / MARKER).is_file():
        raise RuntimeError(f"refusing to remove unmarked directory: {output}")
    shutil.rmtree(output)


def _generate_rk3399_cases(output: Path, manifest: Dict[str, Any]) -> None:
    group = output / "raw_data_cases" / "rk3399"
    cases: List[Dict[str, Any]] = []

    def add(
        name: str,
        *,
        status: str,
        diagnostics: Tuple[str, ...] = (),
        note: str = "",
    ) -> Path:
        root = group / name
        _write_base_dataset(root, "rk3399")
        cases.append(
            _case_record(name, status=status, diagnostics=diagnostics, note=note)
        )
        return root

    add("00_valid_exactly_200", status="ready")

    root = add("01_valid_multiple_segments", status="ready")
    modes = [0] + [1] * 200 + [0] + [2] * 200 + [0]
    timestamps = [float(index) for index in range(len(modes))]
    image_timestamps = [float(index) for index in range(1, 201)] + [
        float(index) for index in range(202, 402)
    ]
    _write_motion_files(root, modes, timestamps, image_timestamps, "rk3399")

    root = add("02_valid_mode_change_and_eof", status="ready")
    modes = [0] + [1] * 100 + [4] * 100
    timestamps = [float(index) for index in range(len(modes))]
    _write_motion_files(
        root,
        modes,
        timestamps,
        [float(index) for index in range(1, 201)],
        "rk3399",
    )

    root = add(
        "03_nonempty_corrupt_video_currently_accepted",
        status="ready",
        note="当前只校验视频文件非空，不解码 AVI。",
    )
    (root / "img.avi").write_bytes(b"not-an-avi")

    root = add(
        "10_missing_imu_not_discovered",
        status="ignored",
        note="当前候选发现依赖 imu.txt，因此不会产生缺失 IMU 诊断。",
    )
    (root / "imu.txt").unlink()

    for name, filename in (
        ("11_missing_image", "img.avi"),
        ("12_missing_imgts", "imgts.txt"),
        ("13_missing_calibration", "calib_raw.yaml"),
    ):
        root = add(name, status="invalid", diagnostics=("dataset_invalid",))
        (root / filename).unlink()

    for name, filename in (
        ("14_empty_imu", "imu.txt"),
        ("15_empty_image", "img.avi"),
        ("16_empty_imgts", "imgts.txt"),
        ("17_empty_calibration", "calib_raw.yaml"),
    ):
        root = add(name, status="invalid", diagnostics=("dataset_invalid",))
        (root / filename).write_bytes(b"")

    shared = output / "shared"
    shared.mkdir(exist_ok=True)
    (shared / "external.avi").write_bytes(b"external-video")
    root = add(
        "18_required_symlink_outside",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    (root / "img.avi").unlink()
    (root / "img.avi").symlink_to("../../../shared/external.avi")

    root = add(
        "20_imu_no_numeric_rows", status="invalid", diagnostics=("dataset_invalid",)
    )
    (root / "imu.txt").write_text("# comment\nIMU HEADER\n", encoding="utf-8")

    root = add(
        "21_imu_too_few_columns", status="invalid", diagnostics=("dataset_invalid",)
    )
    (root / "imu.txt").write_text(
        "0 0 1 3 0 0\n" + _imu_row(2.0, 0) + "\n",
        encoding="utf-8",
    )

    root = add(
        "22_imu_non_numeric_middle", status="invalid", diagnostics=("dataset_invalid",)
    )
    (root / "imu.txt").write_text(
        _imu_row(0.0, 0) + "\nBROKEN DATA\n" + _imu_row(2.0, 0) + "\n",
        encoding="utf-8",
    )

    root = add("23_imu_nan_or_inf", status="invalid", diagnostics=("dataset_invalid",))
    values = _imu_row(0.0, 0).split()
    values[4] = "nan"
    (root / "imu.txt").write_text(
        " ".join(values) + "\n" + _imu_row(2.0, 0) + "\n",
        encoding="utf-8",
    )

    root = add(
        "24_imu_non_integer_flight_mode",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    (root / "imu.txt").write_text(
        _imu_row(0.0, 1.5) + "\n" + _imu_row(2.0, 0) + "\n",
        encoding="utf-8",
    )

    root = add(
        "25_imu_timestamp_decreasing",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    (root / "imu.txt").write_text(
        _imu_row(1.0, 0) + "\n" + _imu_row(0.9, 1) + "\n" + _imu_row(2.0, 0) + "\n",
        encoding="utf-8",
    )

    root = add(
        "30_imgts_no_numeric_rows", status="invalid", diagnostics=("dataset_invalid",)
    )
    (root / "imgts.txt").write_text("# comment\nTIMESTAMP\n", encoding="utf-8")

    root = add(
        "31_imgts_non_numeric_middle",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    (root / "imgts.txt").write_text("1.0\nBROKEN\n", encoding="utf-8")

    root = add(
        "32_imgts_nan_or_inf", status="invalid", diagnostics=("dataset_invalid",)
    )
    (root / "imgts.txt").write_text("nan\n", encoding="utf-8")

    root = add(
        "33_imgts_timestamp_decreasing",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    (root / "imgts.txt").write_text("1.0\n0.9\n", encoding="utf-8")

    matrix = "1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1"
    root = add(
        "40_calib_missing_T_imu_body",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    (root / "calib_raw.yaml").write_text(
        f"cam0:\n  T_cam_imu: [{matrix}]\n", encoding="utf-8"
    )

    root = add(
        "41_calib_missing_T_cam_imu", status="invalid", diagnostics=("dataset_invalid",)
    )
    (root / "calib_raw.yaml").write_text(f"T_imu_body: [{matrix}]\n", encoding="utf-8")

    root = add(
        "42_calib_wrong_matrix_size", status="invalid", diagnostics=("dataset_invalid",)
    )
    (root / "calib_raw.yaml").write_text(
        f"T_imu_body: [{matrix}]\ncam0:\n  T_cam_imu: [1, 0, 0]\n",
        encoding="utf-8",
    )

    root = add(
        "43_calib_nan_or_inf", status="invalid", diagnostics=("dataset_invalid",)
    )
    (root / "calib_raw.yaml").write_text(
        f"T_imu_body: [{matrix}]\ncam0:\n  T_cam_imu: [nan, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]\n",
        encoding="utf-8",
    )

    root = add(
        "44_malformed_yaml_with_matrices_currently_accepted",
        status="ready",
        note="当前只提取两个矩阵文本，不解析完整 YAML。",
    )
    (root / "calib_raw.yaml").write_text(
        f"this is not valid yaml [\nT_imu_body: [{matrix}]\nT_cam_imu: [{matrix}]\n",
        encoding="utf-8",
    )

    root = add(
        "50_segment_no_flight",
        status="unavailable",
        diagnostics=("no_active_segment",),
    )
    timestamps = [float(index) for index in range(250)]
    _write_motion_files(root, [0] * 250, timestamps, timestamps, "rk3399")

    root = add(
        "51_segment_zero_image_frames",
        status="unavailable",
        diagnostics=("segment_too_short", "no_valid_segment"),
    )
    _write_motion_files(
        root,
        [0] + [1] * 200 + [0],
        [float(index) for index in range(202)],
        [float(index) for index in range(1000, 1200)],
        "rk3399",
    )

    root = add(
        "52_segment_199_frames",
        status="unavailable",
        diagnostics=("segment_too_short", "no_valid_segment"),
    )
    _write_motion_files(
        root,
        [0] + [1] * 200 + [0],
        [float(index) for index in range(202)],
        [float(index) for index in range(1, 200)],
        "rk3399",
    )

    root = add(
        "53_segment_one_valid_one_invalid",
        status="ready",
        diagnostics=("segment_too_short",),
    )
    modes = [0] + [1] * 200 + [0] + [2] * 200 + [0]
    timestamps = [float(index) for index in range(len(modes))]
    image_timestamps = [float(index) for index in range(1, 201)] + [
        float(index) for index in range(202, 401)
    ]
    _write_motion_files(root, modes, timestamps, image_timestamps, "rk3399")

    root = add(
        "60_home_point_missing",
        status="ready",
        diagnostics=("vloc_input_missing",),
    )
    (root / "home_point.txt").unlink()

    home_mutations: Tuple[Tuple[str, str], ...] = (
        ("61_home_point_empty", ""),
        ("62_home_point_non_numeric", "not-a-home-point\n"),
        ("63_home_point_too_few_columns", "121.2 31.1\n"),
        ("64_home_point_too_many_columns", "121.2 31.1 50.0 9.0\n"),
        ("65_home_point_multiple_rows", "121.2 31.1 50.0\n121.3 31.2 51.0\n"),
        ("66_home_point_nan", "nan 31.1 50.0\n"),
    )
    for name, content in home_mutations:
        root = add(
            name,
            status="ready",
            diagnostics=("vloc_input_invalid",),
        )
        (root / "home_point.txt").write_text(content, encoding="utf-8")

    (shared / "external_home_point.txt").write_text(
        "121.2 31.1 50.0\n", encoding="utf-8"
    )
    root = add(
        "67_home_point_symlink_outside",
        status="ready",
        diagnostics=("vloc_input_invalid",),
    )
    (root / "home_point.txt").unlink()
    (root / "home_point.txt").symlink_to("../../../shared/external_home_point.txt")

    config = output / "configs" / "rk3399_raw.yaml"
    _write_config(config, group, "RK3399")
    manifest["runs"]["rk3399_raw"] = {
        "config": str(config.relative_to(output)),
        "refresh": True,
        "cases": cases,
    }


def _generate_rk3588_cases(output: Path, manifest: Dict[str, Any]) -> None:
    group = output / "raw_data_cases" / "rk3588"
    cases: List[Dict[str, Any]] = []

    def add(
        name: str,
        *,
        status: str,
        diagnostics: Tuple[str, ...] = (),
        note: str = "",
    ) -> Path:
        root = group / name
        _write_base_dataset(root, "rk3588")
        cases.append(
            _case_record(name, status=status, diagnostics=diagnostics, note=note)
        )
        return root

    add("00_valid_complete_four_streams", status="ready")

    root = add(
        "10_missing_imu_not_discovered",
        status="ignored",
        note="当前候选发现依赖 imu.txt。",
    )
    (root / "imu.txt").unlink()

    required_missing = (
        ("11_missing_video_bottom_0", "video_bottom_0.h265"),
        ("12_missing_video_bottom_1", "video_bottom_1.h265"),
        ("13_missing_video_front_0", "video_front_0.h265"),
        ("14_missing_video_front_1", "video_front_1.h265"),
        ("15_missing_imgts_bottom", "imgts_bottom.txt"),
        ("16_missing_imgts_front", "imgts_front.txt"),
        ("17_missing_bottom_calibration", "bottom_calib_raw.yaml"),
        ("18_missing_front_calibration", "front_calib_raw.yaml"),
    )
    for name, filename in required_missing:
        root = add(name, status="invalid", diagnostics=("dataset_invalid",))
        (root / filename).unlink()

    root = add("19_empty_h265", status="invalid", diagnostics=("dataset_invalid",))
    (root / "video_bottom_0.h265").write_bytes(b"")

    root = add(
        "20_front_bottom_timestamp_mismatch",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    timestamps = [float(index) + 0.1 for index in range(1, 201)]
    (root / "imgts_front.txt").write_text(
        "\n".join(str(value) for value in timestamps) + "\n",
        encoding="utf-8",
    )

    root = add(
        "21_invalid_bottom_calibration",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    (root / "bottom_calib_raw.yaml").write_text(
        "T_imu_body: [1, 0]\n", encoding="utf-8"
    )

    root = add(
        "22_invalid_front_calibration",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    (root / "front_calib_raw.yaml").write_text("T_imu_body: [1, 0]\n", encoding="utf-8")

    root = add(
        "23_rk3399_filenames_only",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    for filename in (
        "video_bottom_0.h265",
        "video_bottom_1.h265",
        "video_front_0.h265",
        "video_front_1.h265",
        "imgts_bottom.txt",
        "imgts_front.txt",
        "bottom_calib_raw.yaml",
        "front_calib_raw.yaml",
    ):
        (root / filename).unlink()
    (root / "img.avi").write_bytes(b"legacy-video")
    (root / "imgts.txt").write_text("1.0\n", encoding="utf-8")
    (root / "calib_raw.yaml").write_text(VALID_CALIBRATION, encoding="utf-8")

    root = add(
        "24_dataset_0_like_incomplete",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    for filename in (
        "video_bottom_0.h265",
        "video_bottom_1.h265",
        "video_front_0.h265",
        "video_front_1.h265",
        "imgts_bottom.txt",
        "imgts_front.txt",
        "front_calib_raw.yaml",
    ):
        (root / filename).unlink()

    root = add(
        "25_home_point_missing",
        status="ready",
        diagnostics=("vloc_input_missing",),
    )
    (root / "home_point.txt").unlink()

    config = output / "configs" / "rk3588_raw.yaml"
    _write_config(config, group, "RK3588")
    manifest["runs"]["rk3588_raw"] = {
        "config": str(config.relative_to(output)),
        "refresh": True,
        "cases": cases,
    }


def _generate_kitti_cases(output: Path, manifest: Dict[str, Any]) -> None:
    group = output / "raw_data_cases" / "kitti"
    cases: List[Dict[str, Any]] = []

    def add(
        name: str,
        *,
        status: str,
        diagnostics: Tuple[str, ...] = (),
        color: bool = False,
        frame_count: int = 200,
        note: str = "",
    ) -> Path:
        root = group / name
        _write_kitti_sequence(root, frame_count=frame_count, color=color)
        cases.append(
            _case_record(name, status=status, diagnostics=diagnostics, note=note)
        )
        return root

    add(
        "00_valid_grayscale",
        status="ready",
        diagnostics=("kitti_ground_truth_missing",),
    )
    add(
        "01_valid_color",
        status="ready",
        diagnostics=("kitti_ground_truth_missing",),
        color=True,
    )
    add(
        "02_short_199_frames",
        status="unavailable",
        diagnostics=(
            "segment_too_short",
            "no_valid_segment",
            "kitti_ground_truth_missing",
        ),
        frame_count=199,
    )

    root = add(
        "10_missing_times_not_discovered",
        status="ignored",
        note="KITTI 候选发现依赖 times.txt。",
    )
    (root / "times.txt").unlink()

    root = add(
        "11_missing_calibration", status="invalid", diagnostics=("dataset_invalid",)
    )
    (root / "calib.txt").unlink()

    root = add(
        "12_missing_right_images", status="invalid", diagnostics=("dataset_invalid",)
    )
    shutil.rmtree(root / "image_1")

    root = add(
        "13_empty_left_images", status="invalid", diagnostics=("dataset_invalid",)
    )
    for image in (root / "image_0").iterdir():
        image.unlink()

    root = add(
        "14_stereo_filename_mismatch",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    (root / "image_1" / "000199.png").rename(root / "image_1" / "000200.png")

    root = add(
        "15_timestamp_image_count_mismatch",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    timestamps = [index * 0.1 for index in range(199)]
    (root / "times.txt").write_text(
        "\n".join(str(value) for value in timestamps) + "\n",
        encoding="utf-8",
    )

    root = add(
        "16_duplicate_timestamp",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    timestamps = [index * 0.1 for index in range(200)]
    timestamps[-1] = timestamps[-2]
    (root / "times.txt").write_text(
        "\n".join(str(value) for value in timestamps) + "\n",
        encoding="utf-8",
    )

    root = add(
        "17_invalid_calibration",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    (root / "calib.txt").write_text(
        f"P0: {VALID_KITTI_PROJECTION}\n",
        encoding="utf-8",
    )

    shared = group / "shared_image_0"
    shared.mkdir(parents=True)
    (shared / "000000.png").write_bytes(b"png")
    root = add(
        "18_image_directory_symlink_outside",
        status="invalid",
        diagnostics=("dataset_invalid",),
    )
    shutil.rmtree(root / "image_0")
    (root / "image_0").symlink_to("../shared_image_0")

    root = add(
        "19_color_fallback_with_partial_grayscale",
        status="ready",
        diagnostics=("kitti_ground_truth_missing",),
        color=True,
    )
    (root / "image_0").mkdir()

    config = output / "configs" / "kitti_raw.yaml"
    _write_config(config, group, "KITTI")
    manifest["runs"]["kitti_raw"] = {
        "config": str(config.relative_to(output)),
        "refresh": True,
        "cases": cases,
    }


def _generate_instance_cases(output: Path, manifest: Dict[str, Any]) -> None:
    group = output / "instance_yaml_cases" / "rk3399"
    cases: List[Dict[str, Any]] = []

    def add(
        name: str,
        *,
        diagnostics: Tuple[str, ...] = (),
        note: str = "",
    ) -> Tuple[Path, Dict[str, Any]]:
        root = group / name
        _write_base_dataset(root, "rk3399")
        payload = _valid_instance_payload(root)
        cases.append(
            _case_record(
                name,
                status="ready",
                diagnostics=diagnostics,
                note=note,
            )
        )
        return root, payload

    root, _ = add("00_invalid_yaml", diagnostics=("instance_rebuilt",))
    (root / "benchmark_dataset.yaml").write_text("not: [valid", encoding="utf-8")

    root, payload = add("01_wrong_schema_version", diagnostics=("instance_rebuilt",))
    payload["schema_version"] = 999
    _write_yaml(root / "benchmark_dataset.yaml", payload)

    root, payload = add("02_missing_dataset_section", diagnostics=("instance_rebuilt",))
    payload.pop("dataset")
    _write_yaml(root / "benchmark_dataset.yaml", payload)

    root, payload = add(
        "03_wrong_dataset_id",
        note="当前会重建，但不会额外输出 instance_rebuilt 警告。",
    )
    payload["dataset"]["id"] = "wrong-id"
    _write_yaml(root / "benchmark_dataset.yaml", payload)

    root, payload = add(
        "04_wrong_root_path",
        note="当前会重建，但不会额外输出 instance_rebuilt 警告。",
    )
    payload["dataset"]["root_path"] = str((root.parent / "elsewhere").resolve())
    _write_yaml(root / "benchmark_dataset.yaml", payload)

    root, payload = add(
        "05_wrong_handler_version",
        note="当前会按契约版本重建。",
    )
    payload["dataset"]["handler_version"] = 999
    _write_yaml(root / "benchmark_dataset.yaml", payload)

    root, payload = add(
        "06_tampered_segment",
        note="当前会按 Segment 语义校验重建。",
    )
    payload["segments"][0]["frame_count"] = 1
    _write_yaml(root / "benchmark_dataset.yaml", payload)

    config = output / "configs" / "instance_yaml_cases.yaml"
    _write_config(config, group, "RK3399")
    manifest["runs"]["instance_yaml_cases"] = {
        "config": str(config.relative_to(output)),
        "refresh": False,
        "cases": cases,
    }


def _generate_config_cases(output: Path, manifest: Dict[str, Any]) -> None:
    cases = output / "config_cases"
    cases.mkdir(parents=True)
    empty_root = cases / "empty_root"
    empty_root.mkdir()
    (empty_root / ".keep").write_text(
        "keeps this intentionally dataset-free directory in Git\n",
        encoding="utf-8",
    )
    root_file = cases / "not_a_directory.txt"
    root_file.write_text("file, not directory\n", encoding="utf-8")

    records: List[Dict[str, Any]] = []

    def write(name: str, content: str, expected: str) -> None:
        path = cases / f"{name}.yaml"
        path.write_text(content, encoding="utf-8")
        records.append(
            {
                "name": name,
                "config": str(path.relative_to(output)),
                "expected": expected,
            }
        )

    write("invalid_yaml", "dataset: [\n", "configuration read error")
    write("missing_dataset", "other: value\n", "dataset mapping error")
    write(
        "missing_root_path",
        "dataset:\n  type: RK3399\n",
        "missing dataset.root_path",
    )
    write(
        "missing_type",
        f"dataset:\n  root_path: {empty_root}\n",
        "missing dataset.type",
    )
    write(
        "unsupported_type",
        f"dataset:\n  root_path: {empty_root}\n  type: UNKNOWN\n",
        "unsupported dataset.type",
    )
    write(
        "root_not_found",
        f"dataset:\n  root_path: {cases / 'does_not_exist'}\n  type: RK3399\n",
        "root path is not a directory",
    )
    write(
        "root_is_file",
        f"dataset:\n  root_path: {root_file}\n  type: RK3399\n",
        "root path is not a directory",
    )
    write(
        "empty_root",
        f"dataset:\n  root_path: {empty_root}\n  type: RK3399\n",
        "no_datasets_found",
    )
    manifest["config_cases"] = records


def _write_base_dataset(root: Path, dataset_type: str) -> None:
    modes = [0] + [1] * 200 + [0]
    timestamps = [float(index) for index in range(len(modes))]
    image_timestamps = [float(index) for index in range(1, 201)]
    root.mkdir(parents=True)
    _write_motion_files(root, modes, timestamps, image_timestamps, dataset_type)
    if dataset_type == "rk3399":
        (root / "img.avi").write_bytes(b"synthetic-avi")
        (root / "calib_raw.yaml").write_text(VALID_CALIBRATION, encoding="utf-8")
    else:
        for filename in (
            "video_bottom_0.h265",
            "video_bottom_1.h265",
            "video_front_0.h265",
            "video_front_1.h265",
        ):
            (root / filename).write_bytes(b"\x00\x00\x00\x01synthetic-h265")
        (root / "bottom_calib_raw.yaml").write_text(VALID_CALIBRATION, encoding="utf-8")
        (root / "front_calib_raw.yaml").write_text(VALID_CALIBRATION, encoding="utf-8")
    (root / "home_point.txt").write_text("121.2 31.1 51.0\n", encoding="utf-8")


def _write_kitti_sequence(
    root: Path,
    *,
    frame_count: int,
    color: bool,
) -> None:
    root.mkdir(parents=True)
    left_name, right_name = ("image_2", "image_3") if color else ("image_0", "image_1")
    left = root / left_name
    right = root / right_name
    left.mkdir()
    right.mkdir()
    for index in range(frame_count):
        filename = f"{index:06d}.png"
        (left / filename).write_bytes(b"png")
        (right / filename).write_bytes(b"png")
    timestamps = [index * 0.1 for index in range(frame_count)]
    (root / "times.txt").write_text(
        "\n".join(str(value) for value in timestamps) + "\n",
        encoding="utf-8",
    )
    projection_keys = ("P2", "P3") if color else ("P0", "P1")
    (root / "calib.txt").write_text(
        "\n".join(f"{key}: {VALID_KITTI_PROJECTION}" for key in projection_keys) + "\n",
        encoding="utf-8",
    )


def _write_motion_files(
    root: Path,
    modes: List[int],
    imu_timestamps: List[float],
    image_timestamps: List[float],
    dataset_type: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    imu_text = "\n".join(
        _imu_row(timestamp, mode) for timestamp, mode in zip(imu_timestamps, modes)
    )
    (root / "imu.txt").write_text(imu_text + "\nIGNORED LAST LINE\n", encoding="utf-8")
    timestamp_text = "\n".join(str(timestamp) for timestamp in image_timestamps) + "\n"
    if dataset_type == "rk3399":
        (root / "imgts.txt").write_text(timestamp_text, encoding="utf-8")
    else:
        timestamp_text += "IGNORED LAST LINE\n"
        (root / "imgts_bottom.txt").write_text(timestamp_text, encoding="utf-8")
        (root / "imgts_front.txt").write_text(timestamp_text, encoding="utf-8")


def _imu_row(timestamp: float, flight_mode: float) -> str:
    values = [
        timestamp,
        timestamp,
        1,
        flight_mode,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        31.1,
        121.2,
        50,
        51,
        5,
    ]
    return " ".join(str(value) for value in values)


def _valid_instance_payload(root: Path) -> Dict[str, Any]:
    dataset_id = _dataset_id(root, "rk3399")
    start = 1.0
    end = 200.0
    return {
        "schema_version": 1,
        "dataset": {
            "id": dataset_id,
            "type": "rk3399",
            "root_path": str(root.resolve()),
            "handler_version": 2,
        },
        "inputs": {
            "imu_path": str((root / "imu.txt").resolve()),
            "image_path": str((root / "img.avi").resolve()),
            "image_timestamps_path": str((root / "imgts.txt").resolve()),
            "calibration_path": str((root / "calib_raw.yaml").resolve()),
            "home_point_path": str((root / "home_point.txt").resolve()),
        },
        "segments": [
            {
                "id": _segment_id(dataset_id, start, end),
                "sequence_no": 1,
                "start_timestamp": start,
                "end_timestamp": end,
                "duration_seconds": end - start,
                "frame_count": 200,
                "valid": True,
                "invalid_reason": None,
            }
        ],
    }


def _dataset_id(root: Path, dataset_type: str) -> str:
    digest = hashlib.sha256(
        f"{dataset_type}\0{root.resolve()}".encode("utf-8")
    ).hexdigest()[:8]
    return f"{root.name}-{digest}"


def _segment_id(dataset_id: str, start: float, end: float) -> str:
    identity = f"{dataset_id}\0{start:.9f}\0{end:.9f}"
    return "seg-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _case_record(
    name: str,
    *,
    status: str,
    diagnostics: Tuple[str, ...],
    note: str,
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "name": name,
        "expected_status": status,
        "expected_diagnostics": list(diagnostics),
    }
    if note:
        record["note"] = note
    return record


def _write_config(path: Path, root: Path, dataset_type: str) -> None:
    _write_yaml(
        path,
        {
            "dataset": {
                "root_path": str(root.resolve()),
                "type": dataset_type,
            }
        },
    )


def _write_manifest(output: Path, manifest: Dict[str, Any]) -> None:
    _write_yaml(output / "expected_results.yaml", manifest)


def _write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _write_readme(output: Path) -> None:
    text = """# 数据集异常识别测试套件

本目录由 `tools/generate_dataset_anomaly_suite.py` 生成。每个数据集目录只引入一个主要异常，预期结果见 `expected_results.yaml`。

## 重新生成

```bash
python3 tools/generate_dataset_anomaly_suite.py
```

## 自动核对

```bash
python3 tools/verify_dataset_anomaly_suite.py
```

## 清理

```bash
python3 tools/generate_dataset_anomaly_suite.py --clean
```

说明：套件默认生成在系统临时目录，不写入源码仓库。视频只是非空的最小占位文件，本套件只测试数据集管理，不可用于实际算法运行。按当前确认边界，本套件不包含末尾 NUL 填充案例。
"""
    (output / "README.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
