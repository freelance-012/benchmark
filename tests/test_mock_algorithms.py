from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import List, Tuple


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "mock_algorithms"
InputSpec = Tuple[str, str, bool]


class MockAlgorithmTests(unittest.TestCase):
    CASES = (
        (
            "algorithm1",
            "rk3588",
            (
                ("imu_path", "imu.txt", False),
                ("bottom_video_0_path", "video_bottom_0.h265", False),
                ("bottom_video_1_path", "video_bottom_1.h265", False),
                ("front_video_0_path", "video_front_0.h265", False),
                ("front_video_1_path", "video_front_1.h265", False),
                ("bottom_image_timestamps_path", "imgts_bottom.txt", False),
                ("front_image_timestamps_path", "imgts_front.txt", False),
                ("bottom_calibration_path", "bottom_calib_raw.yaml", False),
                ("front_calibration_path", "front_calib_raw.yaml", False),
            ),
        ),
        (
            "algorithm1",
            "rk3399",
            (
                ("imu_path", "imu.txt", False),
                ("image_path", "img.avi", False),
                ("image_timestamps_path", "imgts.txt", False),
                ("calibration_path", "calib_raw.yaml", False),
            ),
        ),
        (
            "algorithm2",
            "rk3399",
            (
                ("imu_path", "imu.txt", False),
                ("image_path", "img.avi", False),
                ("image_timestamps_path", "imgts.txt", False),
                ("calibration_path", "calib_raw.yaml", False),
            ),
        ),
        (
            "algorithm3",
            "kitti",
            (
                ("image_timestamps_path", "times.txt", False),
                ("calibration_path", "calib.txt", False),
                ("left_image_dir", "image_0", True),
                ("right_image_dir", "image_1", True),
                ("ground_truth_path", "poses.txt", False),
            ),
        ),
    )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_build_and_run_contracts_preserve_all_inputs(self) -> None:
        if shutil.which(os.environ.get("CC", "cc")) is None:
            self.skipTest("a C compiler is required for mock algorithm fixtures")

        for algorithm_id, dataset_type, specs in self.CASES:
            with self.subTest(algorithm=algorithm_id):
                self._build_and_verify(algorithm_id, dataset_type, list(specs))

    def _build_and_verify(
        self,
        algorithm_id: str,
        dataset_type: str,
        specs: List[InputSpec],
    ) -> None:
        algorithm_root = (
            self.root / "algorithm fixtures" / f"{algorithm_id}-{dataset_type}"
        )
        algorithm_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(FIXTURE_ROOT / algorithm_id, algorithm_root)

        build = subprocess.run(
            [str(algorithm_root / "build.sh")],
            cwd=algorithm_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(build.returncode, 0, msg=build.stderr)

        entrypoint = algorithm_root / "build" / algorithm_id
        self.assertTrue(entrypoint.is_file())
        self.assertTrue(os.access(entrypoint, os.X_OK))

        dataset_root = (
            self.root
            / "datasets"
            / f"{algorithm_id}-{dataset_type}"
            / "dataset with space"
        )
        dataset_root.mkdir(parents=True)
        input_paths: List[Tuple[str, Path]] = []
        for role, relative_path, is_directory in specs:
            path = dataset_root / relative_path
            if is_directory:
                path.mkdir()
            else:
                path.write_text(f"fixture for {role}\n", encoding="utf-8")
            input_paths.append((role, path.resolve()))

        command = [
            str(entrypoint),
            str(dataset_root.resolve()),
            "10.5",
            "20.5",
            *(str(path) for _, path in input_paths),
        ]
        run = subprocess.run(
            command,
            cwd=algorithm_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(run.returncode, 0, msg=run.stderr)
        self.assertEqual(run.stderr, "")

        expected_lines = [
            f"algorithm={algorithm_id}",
            f"dataset_type={dataset_type}",
            f"dataset_root={dataset_root.resolve()}",
            "segment_start=10.5",
            "segment_end=20.5",
            *(f"input.{role}={path}" for role, path in input_paths),
        ]
        expected = "\n".join(expected_lines) + "\n"
        output = algorithm_root / "mock_output.txt"
        self.assertEqual(run.stdout, expected)
        self.assertTrue(output.is_file())
        self.assertEqual(output.read_text(encoding="utf-8"), expected)


if __name__ == "__main__":
    unittest.main()
