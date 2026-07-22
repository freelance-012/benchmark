from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import List

import yaml

from slam_benchmark.cli import main
from slam_benchmark.config import load_dataset_config
from slam_benchmark.datasets.contracts import INSTANCE_FILENAME
from slam_benchmark.datasets.parsers import parse_imu_states
from slam_benchmark.datasets.segmentation import segment_by_flight_mode
from slam_benchmark.datasets.service import DatasetManager


class DatasetManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_scan_creates_instance_and_extracts_multiple_segments(self) -> None:
        dataset = self.root / "group" / "flight-001"
        modes = [0] + [3] * 100 + [4] * 100 + [0] + [2] * 200 + [0]
        timestamps = [float(index) for index in range(len(modes))]
        _write_dataset(
            dataset, "rk3399", timestamps, modes, timestamps, home_point=True
        )

        report = self._manager("RK3399").scan()

        self.assertFalse(report.has_errors)
        self.assertEqual(len(report.datasets), 1)
        instance = report.datasets[0]
        self.assertEqual(instance.status, "ready")
        self.assertEqual(instance.valid_segment_count, 2)
        self.assertEqual([item.frame_count for item in instance.segments], [200, 200])
        instance_path = dataset / INSTANCE_FILENAME
        self.assertTrue(instance_path.is_file())
        self.assertFalse((self.root / INSTANCE_FILENAME).exists())

        payload = yaml.safe_load(instance_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["dataset"]["root_path"], str(dataset.resolve()))
        self.assertNotIn("status", payload["dataset"])
        self.assertTrue(
            all(
                Path(value).is_absolute()
                for value in payload["inputs"].values()
                if value is not None
            )
        )

    def test_each_discovered_dataset_gets_its_own_instance(self) -> None:
        timestamps = [float(index) for index in range(202)]
        first = self.root / "group-a" / "flight-001"
        second = self.root / "group-b" / "flight-002"
        for dataset in (first, second):
            _write_dataset(
                dataset, "rk3399", timestamps, [0] + [3] * 200 + [0], timestamps
            )

        report = self._manager("rk3399").scan()

        self.assertEqual(len(report.datasets), 2)
        self.assertFalse((self.root / INSTANCE_FILENAME).exists())
        self.assertTrue((first / INSTANCE_FILENAME).is_file())
        self.assertTrue((second / INSTANCE_FILENAME).is_file())

    def test_segment_validity_counts_image_frames_not_imu_rows(self) -> None:
        dataset = self.root / "flight-short"
        imu_timestamps = [float(index) for index in range(252)]
        modes = [0] + [3] * 250 + [0]
        image_timestamps = [float(index) for index in range(1, 200)]
        _write_dataset(dataset, "rk3399", imu_timestamps, modes, image_timestamps)

        report = self._manager("rk3399").scan()

        self.assertTrue(report.has_errors)
        self.assertEqual(report.datasets[0].segments[0].frame_count, 199)
        self.assertFalse(report.datasets[0].segments[0].valid)
        self.assertEqual(report.datasets[0].status, "unavailable")
        self.assertTrue((dataset / INSTANCE_FILENAME).exists())
        self.assertFalse((self.root / INSTANCE_FILENAME).exists())

    def test_existing_instance_is_reused_until_explicit_refresh(self) -> None:
        dataset = self.root / "flight-reuse"
        timestamps = [float(index) for index in range(202)]
        _write_dataset(dataset, "rk3399", timestamps, [0] + [3] * 200 + [0], timestamps)
        manager = self._manager("rk3399")
        first = manager.scan()
        self.assertFalse(first.has_errors)

        (dataset / "imu.txt").write_text("broken\n", encoding="utf-8")
        reused = manager.scan()
        refreshed = manager.scan(refresh=True, persist=False)

        self.assertFalse(reused.has_errors)
        self.assertEqual(len(reused.datasets), 1)
        self.assertTrue(refreshed.has_errors)
        self.assertEqual(len(refreshed.datasets), 0)

    def test_corrupt_instance_is_rebuilt(self) -> None:
        dataset = self.root / "flight-rebuild"
        timestamps = [float(index) for index in range(202)]
        _write_dataset(dataset, "rk3399", timestamps, [0] + [3] * 200 + [0], timestamps)
        manager = self._manager("rk3399")
        manager.scan()
        instance_path = dataset / INSTANCE_FILENAME
        instance_path.write_text("not: [valid", encoding="utf-8")

        report = manager.scan()

        self.assertFalse(report.has_errors)
        self.assertEqual(len(report.datasets), 1)
        self.assertIn("instance_rebuilt", [item.code for item in report.diagnostics])
        payload = yaml.safe_load(instance_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)

    def test_semantically_tampered_instance_is_rebuilt(self) -> None:
        dataset = self.root / "flight-tampered"
        timestamps = [float(index) for index in range(202)]
        _write_dataset(dataset, "rk3399", timestamps, [0] + [3] * 200 + [0], timestamps)
        manager = self._manager("rk3399")
        manager.scan()
        instance_path = dataset / INSTANCE_FILENAME
        payload = yaml.safe_load(instance_path.read_text(encoding="utf-8"))
        payload["segments"][0]["frame_count"] = 1
        instance_path.write_text(
            yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
        )

        report = manager.scan()

        self.assertFalse(report.has_errors)
        self.assertEqual(report.datasets[0].segments[0].frame_count, 200)

    def test_no_active_segment_is_saved_as_unavailable(self) -> None:
        dataset = self.root / "flight-ground-only"
        timestamps = [float(index) for index in range(250)]
        _write_dataset(dataset, "rk3399", timestamps, [0] * len(timestamps), timestamps)

        report = self._manager("rk3399").scan()

        self.assertTrue(report.has_errors)
        self.assertEqual(report.datasets[0].status, "unavailable")
        self.assertEqual(report.datasets[0].segments, tuple())
        self.assertTrue((dataset / INSTANCE_FILENAME).is_file())
        self.assertFalse((self.root / INSTANCE_FILENAME).exists())

    def test_missing_required_file_is_reported_without_instance(self) -> None:
        dataset = self.root / "flight-missing"
        timestamps = [float(index) for index in range(202)]
        _write_dataset(dataset, "rk3399", timestamps, [0] + [1] * 200 + [0], timestamps)
        (dataset / "img.avi").unlink()

        report = self._manager("rk3399").scan()

        self.assertTrue(report.has_errors)
        self.assertFalse(report.datasets)
        self.assertFalse((dataset / INSTANCE_FILENAME).exists())
        self.assertFalse((self.root / INSTANCE_FILENAME).exists())
        self.assertIn("image_path", report.diagnostics[0].message)

    def test_rk3588_uses_four_h265_streams_and_two_calibrations(self) -> None:
        dataset = self.root / "flight-rk3588"
        timestamps = [float(index) for index in range(202)]
        _write_dataset(dataset, "rk3588", timestamps, [0] + [1] * 200 + [0], timestamps)

        report = self._manager("RK3588").scan()

        self.assertFalse(report.has_errors)
        instance = report.datasets[0]
        self.assertEqual(instance.handler_version, 3)
        self.assertEqual(
            set(instance.input_paths),
            {
                "imu_path",
                "bottom_video_0_path",
                "bottom_video_1_path",
                "front_video_0_path",
                "front_video_1_path",
                "bottom_image_timestamps_path",
                "front_image_timestamps_path",
                "bottom_calibration_path",
                "front_calibration_path",
                "home_point_path",
            },
        )
        self.assertTrue(
            instance.input_paths["bottom_video_0_path"].endswith("video_bottom_0.h265")
        )
        self.assertTrue(
            instance.input_paths["front_calibration_path"].endswith(
                "front_calib_raw.yaml"
            )
        )
        self.assertFalse((dataset / "img.avi").exists())
        self.assertFalse((dataset / "imgts.txt").exists())

    def test_rk3588_missing_h265_stream_is_rejected(self) -> None:
        dataset = self.root / "flight-rk3588-missing-stream"
        timestamps = [float(index) for index in range(202)]
        _write_dataset(dataset, "rk3588", timestamps, [0] + [1] * 200 + [0], timestamps)
        (dataset / "video_front_1.h265").unlink()

        report = self._manager("rk3588").scan()

        self.assertTrue(report.has_errors)
        self.assertFalse(report.datasets)
        self.assertIn("front_video_1_path", report.diagnostics[0].message)

    def test_rk3588_front_and_bottom_timestamps_must_match(self) -> None:
        dataset = self.root / "flight-rk3588-timestamp-mismatch"
        timestamps = [float(index) for index in range(202)]
        _write_dataset(dataset, "rk3588", timestamps, [0] + [1] * 200 + [0], timestamps)
        (dataset / "imgts_front.txt").write_text(
            "\n".join(str(value + 0.1) for value in timestamps) + "\n",
            encoding="utf-8",
        )

        report = self._manager("rk3588").scan()

        self.assertTrue(report.has_errors)
        self.assertFalse(report.datasets)
        self.assertIn("do not match", report.diagnostics[0].message)

    def test_missing_home_point_is_recorded_as_optional_input_warning(self) -> None:
        dataset = self.root / "flight-sfvision-only"
        timestamps = [float(index) for index in range(202)]
        _write_dataset(dataset, "rk3399", timestamps, [0] + [1] * 200 + [0], timestamps)

        report = self._manager("rk3399").scan()

        self.assertFalse(report.has_errors)
        self.assertIsNone(report.datasets[0].input_paths["home_point_path"])
        self.assertIn("vloc_input_missing", [item.code for item in report.diagnostics])

    def test_invalid_home_point_is_recorded_as_optional_input_warning(self) -> None:
        dataset = self.root / "flight-invalid-home"
        timestamps = [float(index) for index in range(202)]
        _write_dataset(dataset, "rk3399", timestamps, [0] + [1] * 200 + [0], timestamps)
        (dataset / "home_point.txt").write_text("invalid\n", encoding="utf-8")

        report = self._manager("rk3399").scan()

        self.assertFalse(report.has_errors)
        self.assertIsNone(report.datasets[0].input_paths["home_point_path"])
        self.assertIn("vloc_input_invalid", [item.code for item in report.diagnostics])

    def test_empty_home_point_is_recorded_as_optional_input_warning(self) -> None:
        dataset = self.root / "flight-empty-home"
        timestamps = [float(index) for index in range(202)]
        _write_dataset(dataset, "rk3399", timestamps, [0] + [1] * 200 + [0], timestamps)
        (dataset / "home_point.txt").write_text("", encoding="utf-8")

        report = self._manager("rk3399").scan()

        self.assertFalse(report.has_errors)
        self.assertIsNone(report.datasets[0].input_paths["home_point_path"])

    def test_symlink_outside_dataset_is_rejected(self) -> None:
        dataset = self.root / "flight-symlink"
        timestamps = [float(index) for index in range(202)]
        _write_dataset(dataset, "rk3399", timestamps, [0] + [1] * 200 + [0], timestamps)
        external = self.root / "external.avi"
        external.write_bytes(b"video")
        (dataset / "img.avi").unlink()
        (dataset / "img.avi").symlink_to(external)

        report = self._manager("rk3399").scan()

        self.assertTrue(report.has_errors)
        self.assertFalse(report.datasets)
        self.assertIn("目录之外", report.diagnostics[0].message)

    def test_fixed_imu_parser_accepts_header_and_keeps_nonzero_modes_together(
        self,
    ) -> None:
        path = self.root / "imu.txt"
        rows = [_imu_row(0.0, 0), _imu_row(1.0, 3), _imu_row(2.0, 4), _imu_row(3.0, 0)]
        path.write_text(
            " ".join(_IMU_COLUMNS) + "\n" + "\n".join(rows) + "\n", encoding="utf-8"
        )

        ranges = segment_by_flight_mode(parse_imu_states(path))

        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0], (1.0, 2.0))

    def test_catalog_and_list_cli(self) -> None:
        dataset = self.root / "flight-cli"
        timestamps = [float(index) for index in range(202)]
        _write_dataset(dataset, "rk3399", timestamps, [0] + [1] * 200 + [0], timestamps)
        manager = self._manager("rk3399")
        instance = manager.scan().datasets[0]

        self.assertEqual(manager.catalog().datasets[0].dataset_id, instance.dataset_id)

        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            exit_code = main(
                [
                    "dataset",
                    "list",
                    "--config",
                    str(self._config_path("rk3399")),
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertIn(instance.dataset_id, output.getvalue())
        self.assertEqual(errors.getvalue(), "")

    def _config_path(self, dataset_type: str) -> Path:
        path = self.root / f"{dataset_type}.yaml"
        path.write_text(
            yaml.safe_dump(
                {"dataset": {"root_path": str(self.root), "type": dataset_type}},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return path

    def _manager(self, dataset_type: str) -> DatasetManager:
        return DatasetManager(load_dataset_config(self._config_path(dataset_type)))


_IMU_COLUMNS = (
    "ts",
    "ts_fcc",
    "status",
    "flight_mode",
    "x",
    "y",
    "z",
    "yaw",
    "pitch",
    "roll",
    "vx",
    "vy",
    "vz",
    "position_reset_count",
    "altitude_reset_count",
    "heading_reset_count",
    "latitude",
    "longitude",
    "altitude",
    "altitude_msl",
    "height",
)


def _write_dataset(
    root: Path,
    dataset_type: str,
    imu_timestamps: List[float],
    modes: List[int],
    image_timestamps: List[float],
    *,
    home_point: bool = False,
) -> None:
    root.mkdir(parents=True)
    imu = "\n".join(
        _imu_row(timestamp, mode) for timestamp, mode in zip(imu_timestamps, modes)
    )
    (root / "imu.txt").write_text(imu + "\n", encoding="utf-8")
    timestamp_text = "\n".join(str(timestamp) for timestamp in image_timestamps) + "\n"
    calibration = """%YAML:1.0
T_imu_body: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
cam0:
  T_cam_imu: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
"""
    if dataset_type == "rk3399":
        (root / "imgts.txt").write_text(timestamp_text, encoding="utf-8")
        (root / "img.avi").write_bytes(b"video")
        (root / "calib_raw.yaml").write_text(calibration, encoding="utf-8")
    else:
        (root / "imgts_bottom.txt").write_text(timestamp_text, encoding="utf-8")
        (root / "imgts_front.txt").write_text(timestamp_text, encoding="utf-8")
        for filename in (
            "video_bottom_0.h265",
            "video_bottom_1.h265",
            "video_front_0.h265",
            "video_front_1.h265",
        ):
            (root / filename).write_bytes(b"\x00\x00\x00\x01video")
        (root / "bottom_calib_raw.yaml").write_text(calibration, encoding="utf-8")
        (root / "front_calib_raw.yaml").write_text(calibration, encoding="utf-8")
    if home_point:
        (root / "home_point.txt").write_text("121.2 31.1 51.0\n", encoding="utf-8")


def _imu_row(timestamp: float, flight_mode: int) -> str:
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


if __name__ == "__main__":
    unittest.main()
