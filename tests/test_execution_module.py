from __future__ import annotations

import io
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Callable, Optional, Tuple

import yaml

from slam_benchmark.cli import main
from slam_benchmark.compilation.models import BuildConfig
from slam_benchmark.datasets.models import DatasetScanConfig
from slam_benchmark.execution.models import (
    FAILURE_POLICY_FAIL_FAST,
    RunRequest,
)
from slam_benchmark.execution.service import ExecutionService
from tests.test_dataset_manager import _write_dataset, _write_kitti_sequence

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "mock_algorithms"
REPOSITORY_ROOT = Path(__file__).parent.parent.resolve()
SourceMutation = Callable[[str], str]


class ExecutionModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.results_root = self.root / "results with spaces"

    def test_three_mock_algorithms_compile_and_run_registered_datasets(self) -> None:
        if shutil.which("cc") is None:
            self.skipTest("a C compiler is required for mock algorithm fixtures")

        cases = (
            ("algorithm1", "rk3588"),
            ("algorithm2", "rk3399"),
            ("algorithm3", "kitti"),
        )
        for algorithm_id, dataset_type in cases:
            with self.subTest(algorithm=algorithm_id):
                algorithm_root = self._copy_git_algorithm(algorithm_id)
                collection, dataset = self._create_collection(
                    dataset_type,
                    f"{algorithm_id}-dataset",
                )
                summary = ExecutionService().start(
                    self._request(
                        algorithm_id,
                        algorithm_root,
                        collection,
                        dataset_type,
                    )
                )

                self.assertEqual(summary.status, "success")
                self.assertEqual(summary.successful_datasets, 1)
                self.assertEqual(summary.successful_segments, 1)
                self.assertEqual(summary.algorithm_failure_count, 0)
                copied = list(summary.result_root.glob("dataset/*/mock_output.txt"))
                self.assertEqual(len(copied), 1)
                output = copied[0].read_text(encoding="utf-8")
                self.assertIn(f"algorithm={algorithm_id}", output)
                self.assertIn(f"dataset_type={dataset_type}", output)
                self.assertIn(f"dataset_root={dataset.resolve()}", output)
                self.assertTrue((summary.result_root / "build_receipt.yaml").is_file())
                self.assertTrue(
                    (summary.result_root / "config" / "algorithm.yaml").is_file()
                )
                self.assertTrue((summary.result_root / "checkpoint.yaml").is_file())

    def test_algorithm1_runs_rk3399_with_vo_contract(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm1")
        collection, dataset = self._create_collection(
            "rk3399",
            "algorithm1-rk3399-dataset",
        )

        summary = ExecutionService().start(
            self._request(
                "algorithm1",
                algorithm_root,
                collection,
                "rk3399",
            )
        )

        self.assertEqual(summary.status, "success")
        self.assertEqual(summary.successful_datasets, 1)
        self.assertEqual(summary.successful_segments, 1)
        result_dir = next(summary.result_root.glob("dataset/*"))
        output = (result_dir / "mock_output.txt").read_text(encoding="utf-8")
        self.assertIn("algorithm=algorithm1", output)
        self.assertIn("dataset_type=rk3399", output)
        self.assertIn(f"dataset_root={dataset}", output)
        self.assertTrue((result_dir / "calib_raw.yaml").is_file())
        self.assertFalse((result_dir / "home_point.txt").exists())

    def test_default_mode_skips_failed_dataset_and_continues(self) -> None:
        algorithm_root = self._copy_git_algorithm(
            "algorithm2",
            _fail_for_bad_dataset,
        )
        collection = self.root / "default-mode datasets"
        bad = self._create_sf_dataset(
            collection,
            "01-bad-dataset",
            "rk3399",
            multiple_segments=True,
        )
        good = self._create_sf_dataset(
            collection,
            "02-good-dataset",
            "rk3399",
        )

        summary = ExecutionService().start(
            self._request("algorithm2", algorithm_root, collection, "rk3399")
        )

        self.assertEqual(summary.status, "success")
        self.assertEqual(summary.successful_datasets, 1)
        self.assertEqual(summary.failed_datasets, 1)
        self.assertEqual(summary.algorithm_failure_count, 1)
        self.assertEqual(summary.successful_segments, 1)
        self.assertEqual(summary.failed_segments, 1)
        self.assertEqual(summary.not_run_segments, 1)
        receipts = self._dataset_results(summary.result_root)
        by_path = {item["dataset_path"]: item for item in receipts}
        self.assertEqual(by_path[str(bad)]["status"], "failed")
        self.assertEqual(len(by_path[str(bad)]["not_run_segment_ids"]), 1)
        self.assertEqual(by_path[str(good)]["status"], "success")
        checkpoint = self._yaml(summary.result_root / "checkpoint.yaml")
        self.assertEqual(checkpoint["failure_policy"], "continue")
        self.assertEqual(checkpoint["next_dataset_index"], 2)
        self.assertEqual(checkpoint["next_segment_index"], 3)
        self.assertFalse(any(summary.result_root.rglob("dataset_receipt.yaml")))

    def test_multiple_successful_segments_have_isolated_results(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm2")
        collection = self.root / "multiple Segment datasets"
        self._create_sf_dataset(
            collection,
            "two-segments",
            "rk3399",
            multiple_segments=True,
        )

        summary = ExecutionService().start(
            self._request("algorithm2", algorithm_root, collection, "rk3399")
        )

        self.assertEqual(summary.status, "success")
        self.assertEqual(summary.successful_segments, 2)
        outputs = sorted(summary.result_root.glob("dataset/*/mock_output.txt"))
        self.assertEqual(len(outputs), 2)
        self.assertEqual([item.parent.name for item in outputs], ["0", "1"])
        contents = [item.read_text(encoding="utf-8") for item in outputs]
        self.assertEqual(len(set(contents)), 2)

    def test_result_tree_is_flat_and_segment_indexed(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm2")
        collection = self.root / "result tree datasets"
        self._create_sf_dataset(
            collection,
            "two-segments",
            "rk3399",
            multiple_segments=True,
        )

        summary = ExecutionService().start(
            self._request("algorithm2", algorithm_root, collection, "rk3399")
        )

        self.assertEqual(
            summary.result_root,
            self.results_root / "algorithm2" / "test-000",
        )
        self.assertEqual(
            {item.name for item in summary.result_root.iterdir()},
            {
                "config",
                "logs",
                "build_receipt.yaml",
                "checkpoint.yaml",
                "dataset",
            },
        )
        segment_root = summary.result_root / "dataset"
        self.assertEqual(
            {item.name for item in segment_root.iterdir()},
            {"0", "1"},
        )
        for run_index in (0, 1):
            segment_dir = segment_root / str(run_index)
            self.assertTrue((segment_dir / "receipt.yaml").is_file())
            self.assertTrue((segment_dir / "stdout.log").is_file())
            self.assertTrue((segment_dir / "stderr.log").is_file())
            self.assertTrue((segment_dir / "mock_output.txt").is_file())
            self.assertTrue((segment_dir / "home_point.txt").is_file())
            self.assertTrue((segment_dir / "calib_raw.yaml").is_file())
            self.assertTrue((segment_dir / "evaluation").is_dir())
            receipt = self._yaml(segment_dir / "receipt.yaml")
            self.assertEqual(receipt["run_index"], run_index)
        self.assertFalse(any(summary.result_root.rglob("dataset_receipt.yaml")))
        self.assertFalse(any(summary.result_root.rglob("result")))

    def test_successful_run_copies_voeval_log_dir_support_files(self) -> None:
        cases = (
            (
                "algorithm1",
                "rk3588",
                "bottom_calib_raw.yaml",
                "front_calib_raw.yaml",
                False,
            ),
            (
                "algorithm2",
                "rk3399",
                "calib_raw.yaml",
                None,
                True,
            ),
        )
        for (
            algorithm_id,
            dataset_type,
            calibration_name,
            excluded_name,
            expects_home_point,
        ) in cases:
            with self.subTest(dataset_type=dataset_type):
                algorithm_root = self._copy_git_algorithm(algorithm_id)
                collection, dataset = self._create_collection(
                    dataset_type,
                    f"{dataset_type}-evaluation-files",
                )
                home_point = dataset / "home_point.txt"
                home_point.write_text(
                    "dataset copy must not be used\n", encoding="utf-8"
                )

                summary = ExecutionService().start(
                    self._request(
                        algorithm_id,
                        algorithm_root,
                        collection,
                        dataset_type,
                    )
                )

                result_dir = next(summary.result_root.glob("dataset/*"))
                copied_calibration = result_dir / calibration_name
                copied_home_point = result_dir / "home_point.txt"
                self.assertEqual(
                    copied_calibration.read_bytes(),
                    (dataset / calibration_name).read_bytes(),
                )
                if expects_home_point:
                    self.assertEqual(
                        copied_home_point.read_text(encoding="utf-8"),
                        "121.2 31.1 51.0\n",
                    )
                    self.assertNotEqual(
                        copied_home_point.read_bytes(),
                        home_point.read_bytes(),
                    )
                else:
                    self.assertFalse(copied_home_point.exists())
                if excluded_name is not None:
                    self.assertFalse((result_dir / excluded_name).exists())
                self.assertTrue((result_dir / "evaluation").is_dir())

    def test_sf_vloc_does_not_require_dataset_home_point(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm2")
        collection, dataset = self._create_collection(
            "rk3399",
            "vloc-missing-home-point",
        )
        (dataset / "home_point.txt").unlink()

        summary = ExecutionService().start(
            self._request("algorithm2", algorithm_root, collection, "rk3399")
        )

        self.assertEqual(summary.status, "success")
        self.assertEqual(summary.successful_datasets, 1)
        self.assertEqual(
            (summary.result_root / "dataset" / "0" / "home_point.txt").read_text(
                encoding="utf-8"
            ),
            "121.2 31.1 51.0\n",
        )

    def test_sf_vloc_requires_algorithm_home_point_output(self) -> None:
        algorithm_root = self._copy_git_algorithm(
            "algorithm2",
            _return_without_home_point,
        )
        collection, _ = self._create_collection(
            "rk3399",
            "vloc-missing-output-home-point",
        )

        summary = ExecutionService().start(
            self._request(
                "algorithm2",
                algorithm_root,
                collection,
                "rk3399",
                failure_threshold=0,
            )
        )

        self.assertEqual(summary.status, "failed")
        self.assertEqual(summary.algorithm_failure_count, 1)
        receipt = self._yaml(next(summary.result_root.glob("dataset/*/receipt.yaml")))
        self.assertIn("home_point.txt", receipt["failure_reason"])
        self.assertFalse(receipt["output_checks"]["home_point.txt"]["accepted"])

    def test_fail_fast_stops_before_next_dataset_regardless_of_threshold(self) -> None:
        algorithm_root = self._copy_git_algorithm(
            "algorithm2",
            _fail_for_bad_dataset,
        )
        collection = self.root / "fail-fast datasets"
        self._create_sf_dataset(collection, "01-bad-dataset", "rk3399")
        self._create_sf_dataset(collection, "02-good-dataset", "rk3399")
        request = self._request(
            "algorithm2",
            algorithm_root,
            collection,
            "rk3399",
            failure_policy=FAILURE_POLICY_FAIL_FAST,
            failure_threshold=99,
        )

        summary = ExecutionService().start(request)

        self.assertEqual(summary.status, "failed")
        self.assertEqual(summary.failed_datasets, 1)
        self.assertEqual(summary.not_run_datasets, 1)
        self.assertEqual(summary.algorithm_failure_count, 1)
        self.assertEqual(len(self._dataset_results(summary.result_root)), 1)
        checkpoint = self._yaml(summary.result_root / "checkpoint.yaml")
        self.assertEqual(checkpoint["failure_policy"], "fail_fast")
        self.assertEqual(checkpoint["next_dataset_index"], 0)

    def test_default_mode_records_invalid_dataset_and_runs_valid_dataset(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm2")
        collection = self.root / "mixed validity datasets"
        bad = self._create_sf_dataset(
            collection,
            "01-invalid",
            "rk3399",
        )
        (bad / "img.avi").unlink()
        self._create_sf_dataset(collection, "02-valid", "rk3399")

        summary = ExecutionService().start(
            self._request("algorithm2", algorithm_root, collection, "rk3399")
        )

        self.assertEqual(summary.status, "success")
        self.assertEqual(summary.successful_datasets, 1)
        self.assertEqual(summary.not_run_datasets, 1)
        checkpoint = self._yaml(summary.result_root / "checkpoint.yaml")
        self.assertEqual(len(checkpoint["preflight_issues"]), 1)
        self.assertEqual(checkpoint["preflight_issues"][0]["path"], str(bad))
        self.assertEqual(checkpoint["preflight_issues"][0]["status"], "not_run")

    def test_fail_fast_preflight_error_stops_before_build(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm2")
        collection = self.root / "preflight fail-fast"
        bad = self._create_sf_dataset(collection, "01-invalid", "rk3399")
        (bad / "img.avi").unlink()
        self._create_sf_dataset(collection, "02-valid", "rk3399")

        summary = ExecutionService().start(
            self._request(
                "algorithm2",
                algorithm_root,
                collection,
                "rk3399",
                failure_policy=FAILURE_POLICY_FAIL_FAST,
            )
        )

        self.assertEqual(summary.status, "failed")
        self.assertFalse((summary.result_root / "build_receipt.yaml").exists())
        self.assertTrue((summary.result_root / "checkpoint.yaml").is_file())
        self.assertEqual(summary.successful_datasets, 0)
        self.assertEqual(summary.not_run_datasets, 2)

    def test_build_failure_stops_all_modes_before_algorithm_execution(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm2")
        (algorithm_root / "build.sh").write_text(
            "#!/usr/bin/env bash\nexit 7\n",
            encoding="utf-8",
        )
        (algorithm_root / "build.sh").chmod(0o755)
        self._git(algorithm_root, "add", "build.sh")
        self._git(algorithm_root, "commit", "-q", "-m", "break build")
        collection, _ = self._create_collection("rk3399", "build-failure")

        summary = ExecutionService().start(
            self._request("algorithm2", algorithm_root, collection, "rk3399")
        )

        self.assertEqual(summary.status, "failed")
        self.assertIn("exited with code 7", summary.failure_reason or "")
        self.assertTrue((summary.result_root / "build_receipt.yaml").is_file())
        self.assertFalse((summary.result_root / "dataset").exists())

    def test_selected_dataset_path_runs_only_selected_dataset(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm2")
        collection = self.root / "selected datasets"
        first = self._create_sf_dataset(collection, "01-first", "rk3399")
        selected = self._create_sf_dataset(collection, "02-selected", "rk3399")
        request = self._request(
            "algorithm2",
            algorithm_root,
            collection,
            "rk3399",
            selected_paths=(selected,),
        )

        summary = ExecutionService().start(request)

        self.assertEqual(summary.status, "success")
        self.assertEqual(summary.total_datasets, 1)
        receipt = self._dataset_results(summary.result_root)[0]
        self.assertEqual(receipt["dataset_path"], str(selected))
        self.assertNotEqual(receipt["dataset_path"], str(first))

    def test_zero_exit_without_output_is_an_algorithm_failure(self) -> None:
        algorithm_root = self._copy_git_algorithm(
            "algorithm2",
            _return_without_output,
        )
        (algorithm_root / "mock_output.txt").write_text(
            "stale output must not be accepted\n",
            encoding="utf-8",
        )
        collection, _ = self._create_collection("rk3399", "missing-output")

        summary = ExecutionService().start(
            self._request(
                "algorithm2",
                algorithm_root,
                collection,
                "rk3399",
                failure_threshold=0,
            )
        )

        self.assertEqual(summary.status, "failed")
        self.assertEqual(summary.algorithm_failure_count, 1)
        receipt_path = next(summary.result_root.glob("dataset/*/receipt.yaml"))
        receipt = self._yaml(receipt_path)
        self.assertEqual(receipt["status"], "failed")
        self.assertTrue(receipt["algorithm_failure"])
        self.assertIn("does not exist", receipt["failure_reason"])
        self.assertFalse((receipt_path.parent / "calib_raw.yaml").exists())

    def test_wrong_fixed_output_content_is_rejected(self) -> None:
        algorithm_root = self._copy_git_algorithm(
            "algorithm2",
            _write_wrong_dataset_type,
        )
        collection, _ = self._create_collection("rk3399", "wrong-output")

        summary = ExecutionService().start(
            self._request(
                "algorithm2",
                algorithm_root,
                collection,
                "rk3399",
                failure_threshold=0,
            )
        )

        self.assertEqual(summary.status, "failed")
        receipt = self._yaml(next(summary.result_root.glob("dataset/*/receipt.yaml")))
        self.assertFalse(receipt["output_checks"]["mock_output.txt"]["format_valid"])
        self.assertIn("does not match run inputs", receipt["failure_reason"])

    def test_timeout_is_an_algorithm_failure(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm2", _sleep_before_output)
        collection, _ = self._create_collection("rk3399", "timeout")

        summary = ExecutionService().start(
            self._request(
                "algorithm2",
                algorithm_root,
                collection,
                "rk3399",
                timeout_seconds=0.05,
                failure_threshold=0,
            )
        )

        self.assertEqual(summary.status, "failed")
        receipt = self._yaml(next(summary.result_root.glob("dataset/*/receipt.yaml")))
        self.assertEqual(receipt["status"], "timeout")
        self.assertIn("exceeded timeout", receipt["failure_reason"])

    def test_fail_fast_result_can_resume_same_dataset_when_context_is_unchanged(
        self,
    ) -> None:
        algorithm_root = self._copy_git_algorithm(
            "algorithm2",
            _require_untracked_marker,
        )
        collection = self.root / "resume datasets"
        self._create_sf_dataset(collection, "01-first", "rk3399")
        self._create_sf_dataset(collection, "02-second", "rk3399")
        request = self._request(
            "algorithm2",
            algorithm_root,
            collection,
            "rk3399",
            failure_policy=FAILURE_POLICY_FAIL_FAST,
        )
        service = ExecutionService()

        first = service.start(request)
        self.assertEqual(first.status, "failed")
        (algorithm_root / "allow_run").write_text("ready\n", encoding="utf-8")

        resumed = service.resume(request, first.result_root)

        self.assertEqual(resumed.status, "success")
        self.assertEqual(resumed.successful_datasets, 2)
        self.assertEqual(resumed.algorithm_failure_count, 0)
        segment_receipts = sorted(
            resumed.result_root.glob("dataset/*/receipt.yaml"),
            key=lambda item: int(item.parent.name),
        )
        self.assertEqual(len(segment_receipts), 2)
        self.assertEqual(
            [self._yaml(item)["status"] for item in segment_receipts],
            ["success", "success"],
        )
        checkpoint = self._yaml(resumed.result_root / "checkpoint.yaml")
        self.assertEqual(checkpoint["next_dataset_index"], 2)
        self.assertEqual(checkpoint["next_segment_index"], 2)
        self.assertEqual(len(checkpoint["dataset_results"]), 2)

    def test_tracked_source_change_during_algorithm_run_stops_entire_run(self) -> None:
        algorithm_root = self._copy_git_algorithm(
            "algorithm2",
            _modify_tracked_source_during_run,
        )
        collection, _ = self._create_collection("rk3399", "source-change")

        summary = ExecutionService().start(
            self._request("algorithm2", algorithm_root, collection, "rk3399")
        )

        self.assertEqual(summary.status, "failed")
        self.assertIn(
            "tracked source files changed",
            summary.failure_reason or "",
        )
        self.assertEqual(summary.algorithm_failure_count, 0)
        self.assertTrue(any(summary.result_root.glob("dataset/*/receipt.yaml")))

    def test_cli_run_builds_and_executes_without_user_run_yaml(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm2")
        collection, _ = self._create_collection("rk3399", "cli-dataset")
        algorithm_config = self.root / "algorithm config.yaml"
        dataset_config = self.root / "dataset config.yaml"
        algorithm_config.write_text(
            yaml.safe_dump(
                {
                    "algorithm": "algorithm2",
                    "build": {
                        "algorithm_path": str(algorithm_root),
                        "script_path": str(algorithm_root / "build.sh"),
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        dataset_config.write_text(
            yaml.safe_dump(
                {
                    "dataset": {
                        "root_path": str(collection),
                        "type": "rk3399",
                    }
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        output = io.StringIO()
        errors = io.StringIO()
        previous_cwd = Path.cwd()
        try:
            os.chdir(self.root)
            with redirect_stdout(output), redirect_stderr(errors):
                exit_code = main(
                    [
                        "run",
                        "--algorithm-config",
                        str(algorithm_config),
                        "--dataset-config",
                        str(dataset_config),
                    ]
                )
        finally:
            os.chdir(previous_cwd)

        self.assertEqual(exit_code, 0)
        self.assertIn("[SUCCESS]", output.getvalue())
        self.assertIn("1 success", output.getvalue())
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(
            len(
                list((self.root / "result").glob("algorithm2/test-000/checkpoint.yaml"))
            ),
            1,
        )

    def test_cli_fail_fast_returns_error_after_first_algorithm_failure(self) -> None:
        algorithm_root = self._copy_git_algorithm(
            "algorithm2",
            _fail_for_bad_dataset,
        )
        collection = self.root / "cli fail-fast datasets"
        self._create_sf_dataset(collection, "01-bad-dataset", "rk3399")
        self._create_sf_dataset(collection, "02-good-dataset", "rk3399")
        algorithm_config = self.root / "fail-fast algorithm.yaml"
        dataset_config = self.root / "fail-fast dataset.yaml"
        algorithm_config.write_text(
            yaml.safe_dump(
                {
                    "algorithm": "algorithm2",
                    "build": {
                        "algorithm_path": str(algorithm_root),
                        "script_path": str(algorithm_root / "build.sh"),
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        dataset_config.write_text(
            yaml.safe_dump(
                {
                    "dataset": {
                        "root_path": str(collection),
                        "type": "rk3399",
                    }
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        output = io.StringIO()
        errors = io.StringIO()
        previous_cwd = Path.cwd()
        try:
            os.chdir(self.root)
            with redirect_stdout(output), redirect_stderr(errors):
                exit_code = main(
                    [
                        "run",
                        "--algorithm-config",
                        str(algorithm_config),
                        "--dataset-config",
                        str(dataset_config),
                        "--fail-fast",
                    ]
                )
        finally:
            os.chdir(previous_cwd)

        self.assertEqual(exit_code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("[FAILED]", errors.getvalue())
        self.assertIn("1 not run", errors.getvalue())

    def test_ctrl_c_stops_default_mode_and_saves_interrupted_checkpoint(self) -> None:
        if os.name != "posix":
            self.skipTest("process-group interruption test requires POSIX")
        algorithm_root = self._copy_git_algorithm("algorithm2", _sleep_before_output)
        collection, _ = self._create_collection("rk3399", "interrupt")
        algorithm_config = self.root / "interrupt algorithm.yaml"
        dataset_config = self.root / "interrupt dataset.yaml"
        algorithm_config.write_text(
            yaml.safe_dump(
                {
                    "algorithm": "algorithm2",
                    "build": {
                        "algorithm_path": str(algorithm_root),
                        "script_path": str(algorithm_root / "build.sh"),
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        dataset_config.write_text(
            yaml.safe_dump(
                {
                    "dataset": {
                        "root_path": str(collection),
                        "type": "rk3399",
                    }
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "slam_benchmark",
                "run",
                "--algorithm-config",
                str(algorithm_config),
                "--dataset-config",
                str(dataset_config),
            ],
            cwd=self.root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if list(
                    (self.root / "result").glob(
                        "algorithm2/test-000/dataset/*/stdout.log"
                    )
                ):
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.02)
            self.assertIsNone(
                process.poll(),
                msg="run ended before the interruption could be sent",
            )
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

        self.assertEqual(process.returncode, 130)
        self.assertEqual(stdout, "")
        self.assertIn("[INTERRUPTED]", stderr)
        checkpoint_path = next(
            (self.root / "result").glob("algorithm2/test-000/checkpoint.yaml")
        )
        checkpoint = self._yaml(checkpoint_path)
        self.assertEqual(checkpoint["status"], "interrupted")
        self.assertEqual(checkpoint["next_dataset_index"], 0)
        self.assertEqual(checkpoint["next_segment_index"], 0)

    def _copy_git_algorithm(
        self,
        algorithm_id: str,
        mutation: Optional[SourceMutation] = None,
    ) -> Path:
        destination = self.root / "algorithm repositories with spaces" / algorithm_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(FIXTURE_ROOT / algorithm_id, destination)
        if mutation is not None:
            source_path = destination / "main.c"
            source_path.write_text(
                mutation(source_path.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
        self._git(destination, "init", "-q")
        self._git(destination, "config", "user.email", "tests@example.com")
        self._git(destination, "config", "user.name", "Benchmark Tests")
        self._git(destination, "add", "build.sh", "main.c")
        self._git(destination, "commit", "-q", "-m", "fixture")
        return destination

    def _request(
        self,
        algorithm_id: str,
        algorithm_root: Path,
        collection_root: Path,
        dataset_type: str,
        *,
        selected_paths: Tuple[Path, ...] = (),
        failure_policy: str = "continue",
        failure_threshold: int = 1,
        timeout_seconds: float = 30.0,
    ) -> RunRequest:
        return RunRequest(
            build_config=BuildConfig(
                algorithm_id=algorithm_id,
                algorithm_path=algorithm_root,
                script_path=algorithm_root / "build.sh",
            ),
            dataset_configs=(
                DatasetScanConfig(
                    root_path=collection_root,
                    dataset_type=dataset_type,
                ),
            ),
            selected_dataset_paths=selected_paths,
            failure_policy=failure_policy,
            failure_threshold=failure_threshold,
            timeout_seconds=timeout_seconds,
            results_root=self.results_root,
        )

    def _create_collection(
        self,
        dataset_type: str,
        name: str,
    ) -> Tuple[Path, Path]:
        collection = self.root / f"{dataset_type} collection with spaces" / name
        if dataset_type == "kitti":
            dataset = collection / "sequences" / "00"
            _write_kitti_sequence(
                dataset,
                [index * 0.1 for index in range(200)],
            )
        else:
            dataset = self._create_sf_dataset(
                collection,
                "dataset with spaces",
                dataset_type,
            )
        return collection, dataset

    @staticmethod
    def _create_sf_dataset(
        collection: Path,
        name: str,
        dataset_type: str,
        *,
        multiple_segments: bool = False,
    ) -> Path:
        dataset = collection / name
        if multiple_segments:
            modes = [0] + [1] * 200 + [0] + [2] * 200 + [0]
        else:
            modes = [0] + [1] * 200 + [0]
        timestamps = [float(index) for index in range(len(modes))]
        _write_dataset(
            dataset,
            dataset_type,
            timestamps,
            modes,
            timestamps,
            home_point=True,
        )
        return dataset.resolve()

    @staticmethod
    def _git(root: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @staticmethod
    def _yaml(path: Path):
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def _dataset_results(self, result_root: Path):
        checkpoint = self._yaml(result_root / "checkpoint.yaml")
        return checkpoint["dataset_results"]


def _fail_for_bad_dataset(source: str) -> str:
    source = source.replace(
        "#include <stdlib.h>\n",
        "#include <stdlib.h>\n#include <string.h>\n",
    )
    return source.replace(
        '    FILE *output = fopen(OUTPUT_FILENAME, "w");',
        (
            '    if (strstr(argv[1], "bad-dataset") != NULL) {\n'
            "        return 9;\n"
            "    }\n\n"
            '    FILE *output = fopen(OUTPUT_FILENAME, "w");'
        ),
    )


def _return_without_output(source: str) -> str:
    return source.replace(
        '    FILE *output = fopen(OUTPUT_FILENAME, "w");',
        ('    return 0;\n\n    FILE *output = fopen(OUTPUT_FILENAME, "w");'),
    )


def _return_without_home_point(source: str) -> str:
    return source.replace("    if (ok) {\n", "    if (0) {\n", 1)


def _sleep_before_output(source: str) -> str:
    source = source.replace(
        "#include <stdlib.h>\n",
        "#include <stdlib.h>\n#include <unistd.h>\n",
    )
    return source.replace(
        '    FILE *output = fopen(OUTPUT_FILENAME, "w");',
        ('    sleep(1);\n\n    FILE *output = fopen(OUTPUT_FILENAME, "w");'),
    )


def _write_wrong_dataset_type(source: str) -> str:
    return source.replace(
        'emit(output, "dataset_type", "rk3399")',
        'emit(output, "dataset_type", "wrong")',
    )


def _require_untracked_marker(source: str) -> str:
    source = source.replace(
        "#include <stdlib.h>\n",
        "#include <stdlib.h>\n#include <unistd.h>\n",
    )
    return source.replace(
        '    FILE *output = fopen(OUTPUT_FILENAME, "w");',
        (
            '    if (access("allow_run", F_OK) != 0) {\n'
            "        return 9;\n"
            "    }\n\n"
            '    FILE *output = fopen(OUTPUT_FILENAME, "w");'
        ),
    )


def _modify_tracked_source_during_run(source: str) -> str:
    return source.replace(
        "    return ok ? 0 : 5;",
        (
            '    FILE *tracked = fopen("main.c", "a");\n'
            "    if (tracked != NULL) {\n"
            '        fputs("\\n/* changed by algorithm */\\n", tracked);\n'
            "        fclose(tracked);\n"
            "    }\n"
            "    return ok ? 0 : 5;"
        ),
    )


if __name__ == "__main__":
    unittest.main()
