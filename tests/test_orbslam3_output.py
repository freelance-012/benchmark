from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from slam_benchmark.algorithms.contracts import get_algorithm_contract
from slam_benchmark.datasets.models import DatasetInstance, Segment
from slam_benchmark.execution.command import build_run_command
from slam_benchmark.execution.models import ResolvedRunCommand
from slam_benchmark.execution.runner import validate_fixed_output


class Orbslam3OutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.output = self.root / "f_benchmark_mono_sf.txt"
        self.segment = Segment(
            segment_id="segment-1",
            sequence_no=1,
            start_timestamp=1000.0,
            end_timestamp=1002.0,
            duration_seconds=2.0,
            frame_count=40,
            valid=True,
        )
        self.instance = DatasetInstance(
            dataset_id="dataset-1",
            dataset_type="rk3399",
            root_path=self.root,
            handler_version=2,
            input_paths={},
            segments=(self.segment,),
        )
        self.command = ResolvedRunCommand(
            argv=("entrypoint", str(self.root), "1000.0", "1002.0"),
            input_arguments=(),
        )
        self.contract = get_algorithm_contract("orbslam3_mono_sf")

    def test_valid_sf_vo_trajectory_is_accepted(self) -> None:
        self.output.write_text(
            "1000.100000 42 0 0 0 0 0 0 1 12.5 0\n"
            "1000.200000 40 1 0 0 1 2 3 0 11.0 0\n",
            encoding="utf-8",
        )

        checks, error = validate_fixed_output(
            self.output,
            self.contract,
            self.instance,
            self.segment,
            self.command,
        )

        self.assertIsNone(error)
        self.assertTrue(checks["format_valid"])
        self.assertEqual(checks["row_count"], 2)
        self.assertIsNotNone(checks["sha256"])

    def test_timestamp_outside_segment_is_rejected(self) -> None:
        self.output.write_text(
            "999.900000 42 0 0 0 0 0 0 1 12.5 0\n1000.100000 40 1 0 0 1 2 3 0 11.0 0\n",
            encoding="utf-8",
        )

        checks, error = validate_fixed_output(
            self.output,
            self.contract,
            self.instance,
            self.segment,
            self.command,
        )

        self.assertIsNotNone(error)
        self.assertIn("precedes Segment start", error or "")
        self.assertFalse(checks["format_valid"])

    def test_legacy_eight_column_orb_output_is_rejected(self) -> None:
        self.output.write_text(
            "1000.1 0 0 0 0 0 0 1\n1000.2 1 0 0 0 0 0 1\n",
            encoding="utf-8",
        )

        checks, error = validate_fixed_output(
            self.output,
            self.contract,
            self.instance,
            self.segment,
            self.command,
        )

        self.assertIn("exactly 11 columns", error or "")
        self.assertFalse(checks["format_valid"])

    def test_run_command_passes_rk3399_inputs_directly_to_orbslam3(self) -> None:
        entrypoint = self.root / "mono_sf"
        entrypoint.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        entrypoint.chmod(0o755)
        inputs = {}
        for role, filename in (
            ("imu_path", "imu.txt"),
            ("image_path", "img.avi"),
            ("image_timestamps_path", "imgts.txt"),
            ("calibration_path", "calib_raw.yaml"),
        ):
            path = self.root / filename
            path.write_text("input\n", encoding="utf-8")
            inputs[role] = str(path)
        instance = DatasetInstance(
            dataset_id="dataset-1",
            dataset_type="rk3399",
            root_path=self.root,
            handler_version=3,
            input_paths=inputs,
            segments=(self.segment,),
        )

        command = build_run_command(
            entrypoint,
            self.contract,
            instance,
            self.segment,
        )

        self.assertEqual(command.argv[0], str(entrypoint))
        self.assertEqual(command.argv[1], str(self.root))
        self.assertEqual(command.argv[2:4], ("1000.0", "1002.0"))
        self.assertEqual(
            command.argv[4:],
            (
                inputs["imu_path"],
                inputs["image_path"],
                inputs["image_timestamps_path"],
                inputs["calibration_path"],
            ),
        )

    def test_run_command_template_replaces_default_positional_arguments(self) -> None:
        entrypoint = self.root / "mono_sf"
        entrypoint.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        entrypoint.chmod(0o755)
        dataset_root = self.root / "dataset with spaces"
        dataset_root.mkdir()
        inputs = {}
        for role, filename in (
            ("imu_path", "imu.txt"),
            ("image_path", "img.avi"),
            ("image_timestamps_path", "imgts.txt"),
            ("calibration_path", "calib_raw.yaml"),
        ):
            path = dataset_root / filename
            path.write_text("input\n", encoding="utf-8")
            inputs[role] = str(path)
        instance = DatasetInstance(
            dataset_id="dataset-template",
            dataset_type="rk3399",
            root_path=dataset_root,
            handler_version=3,
            input_paths=inputs,
            segments=(self.segment,),
        )

        command = build_run_command(
            entrypoint,
            self.contract,
            instance,
            self.segment,
            command_template=(
                "{executable}",
                "--log={dataset_path}",
                "--start_time={start_ts:.3f}",
                "--end_time={end_ts:.3f}",
            ),
        )

        self.assertEqual(
            command.argv,
            (
                str(entrypoint),
                f"--log={dataset_root}",
                "--start_time=1000.000",
                "--end_time=1002.000",
            ),
        )
        self.assertEqual(
            dict(command.input_arguments),
            inputs,
        )


if __name__ == "__main__":
    unittest.main()
