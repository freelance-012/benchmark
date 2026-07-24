from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional

import yaml

from slam_benchmark.cli import main
from slam_benchmark.compilation.service import BuildError, BuildService
from slam_benchmark.config import load_build_config
from slam_benchmark.datasets.errors import ConfigError


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "mock_algorithms"


class BuildModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_three_mock_algorithms_compile_and_record_receipts(self) -> None:
        if shutil.which("cc") is None:
            self.skipTest("a C compiler is required for mock algorithm fixtures")

        for algorithm_id in ("algorithm1", "algorithm2", "algorithm3"):
            with self.subTest(algorithm=algorithm_id):
                algorithm_root = self._copy_git_algorithm(algorithm_id)
                result_dir = self.root / "build results" / algorithm_id

                receipt = self._build(algorithm_id, algorithm_root, result_dir)

                self.assertEqual(receipt.status, "success")
                self.assertEqual(receipt.exit_code, 0)
                self.assertIsNone(receipt.failure_reason)
                self.assertEqual(
                    receipt.resolved_entrypoint,
                    algorithm_root / "build" / algorithm_id,
                )
                self.assertTrue(receipt.resolved_entrypoint.is_file())
                self.assertTrue(os.access(receipt.resolved_entrypoint, os.X_OK))
                self.assertEqual(len(receipt.script_digest or ""), 64)
                self.assertIsNotNone(receipt.git_before)
                self.assertIsNotNone(receipt.git_after)
                self.assertEqual(receipt.git_before.commit, receipt.git_after.commit)
                self.assertEqual(
                    receipt.git_before.tracked_state_digest,
                    receipt.git_after.tracked_state_digest,
                )
                self.assertEqual(receipt.git_after.tracked_changes, tuple())
                self.assertTrue(receipt.stdout_path.is_file())
                self.assertTrue(receipt.stderr_path.is_file())

                payload = yaml.safe_load(
                    (result_dir / "build_receipt.yaml").read_text(encoding="utf-8")
                )
                self.assertEqual(payload["schema_version"], 1)
                self.assertEqual(payload["algorithm"], algorithm_id)
                self.assertEqual(payload["status"], "success")
                self.assertEqual(
                    payload["resolved_entrypoint"],
                    str(algorithm_root / "build" / algorithm_id),
                )

    def test_nonzero_script_exit_is_a_failed_receipt(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm1")
        self._replace_script(algorithm_root, "#!/usr/bin/env bash\nexit 7\n")

        receipt = self._build(
            "algorithm1",
            algorithm_root,
            self.root / "nonzero result",
        )

        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.exit_code, 7)
        self.assertIn("exited with code 7", receipt.failure_reason or "")

    def test_zero_exit_without_entrypoint_is_a_failed_receipt(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm2")
        self._replace_script(algorithm_root, "#!/usr/bin/env bash\nexit 0\n")

        receipt = self._build(
            "algorithm2",
            algorithm_root,
            self.root / "missing entrypoint result",
        )

        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.exit_code, 0)
        self.assertIn("entrypoint does not exist", receipt.failure_reason or "")

    def test_timeout_terminates_build_and_records_timeout(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm3")
        self._replace_script(
            algorithm_root,
            "#!/usr/bin/env bash\nset -euo pipefail\nsleep 5\n",
        )

        receipt = self._build(
            "algorithm3",
            algorithm_root,
            self.root / "timeout result",
            timeout_seconds=0.1,
        )

        self.assertEqual(receipt.status, "timeout")
        self.assertIn("exceeded timeout", receipt.failure_reason or "")

    def test_tracked_source_change_during_build_is_rejected(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm1")
        self._replace_script(
            algorithm_root,
            """#!/usr/bin/env bash
set -euo pipefail
printf '\n/* changed during build */\n' >> main.c
mkdir -p build
compiler="${CC:-cc}"
"${compiler}" -std=c11 main.c -o build/algorithm1
""",
        )

        receipt = self._build(
            "algorithm1",
            algorithm_root,
            self.root / "tracked change result",
        )

        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.exit_code, 0)
        self.assertIn("tracked source files changed", receipt.failure_reason or "")

    def test_head_change_during_build_is_rejected(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm2")
        self._replace_script(
            algorithm_root,
            """#!/usr/bin/env bash
set -euo pipefail
git commit --allow-empty -m build-time-change >/dev/null
mkdir -p build
compiler="${CC:-cc}"
"${compiler}" -std=c11 main.c -o build/algorithm2
""",
        )

        receipt = self._build(
            "algorithm2",
            algorithm_root,
            self.root / "head change result",
        )

        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.exit_code, 0)
        self.assertIn("Git HEAD changed", receipt.failure_reason or "")

    def test_script_outside_algorithm_root_is_rejected_with_receipt(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm1")
        external_script = self.root / "outside build.sh"
        external_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        external_script.chmod(0o755)
        config = self._write_config(
            "algorithm1",
            algorithm_root,
            script_path=external_script,
        )
        result_dir = self.root / "outside script result"

        receipt = BuildService(load_build_config(config)).build(result_dir)

        self.assertEqual(receipt.status, "failed")
        self.assertIn("must be inside algorithm_path", receipt.failure_reason or "")
        self.assertTrue((result_dir / "build_receipt.yaml").is_file())

    def test_non_executable_script_is_rejected_with_receipt(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm1")
        script_path = algorithm_root / "build.sh"
        script_path.chmod(0o644)

        receipt = self._build(
            "algorithm1",
            algorithm_root,
            self.root / "non executable result",
        )

        self.assertEqual(receipt.status, "failed")
        self.assertIn("not executable", receipt.failure_reason or "")

    def test_existing_build_output_is_not_overwritten(self) -> None:
        algorithm_root = self._copy_git_algorithm("algorithm1")
        result_dir = self.root / "existing result"
        result_dir.mkdir()
        (result_dir / "build_receipt.yaml").write_text(
            "existing\n",
            encoding="utf-8",
        )
        config = load_build_config(self._write_config("algorithm1", algorithm_root))

        with self.assertRaisesRegex(BuildError, "refusing to overwrite"):
            BuildService(config).build(result_dir)

        self.assertEqual(
            (result_dir / "build_receipt.yaml").read_text(encoding="utf-8"),
            "existing\n",
        )

    def test_build_config_rejects_unknown_algorithm_and_relative_paths(self) -> None:
        unknown = self.root / "unknown.yaml"
        unknown.write_text(
            yaml.safe_dump(
                {
                    "algorithm": "unknown",
                    "build": {
                        "algorithm_path": "/tmp/algorithm",
                        "script_path": "/tmp/algorithm/build.sh",
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ConfigError, "algorithm must be one of"):
            load_build_config(unknown)

        relative = self.root / "relative.yaml"
        relative.write_text(
            yaml.safe_dump(
                {
                    "algorithm": "algorithm1",
                    "build": {
                        "algorithm_path": "relative/algorithm1",
                        "script_path": "relative/algorithm1/build.sh",
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ConfigError, "must be an absolute path"):
            load_build_config(relative)

    def test_build_cli_writes_receipt_and_returns_success(self) -> None:
        if shutil.which("cc") is None:
            self.skipTest("a C compiler is required for mock algorithm fixtures")
        algorithm_root = self._copy_git_algorithm("algorithm3")
        config = self._write_config("algorithm3", algorithm_root)
        result_dir = self.root / "cli result"
        output = io.StringIO()
        errors = io.StringIO()

        with redirect_stdout(output), redirect_stderr(errors):
            exit_code = main(
                [
                    "build",
                    "--config",
                    str(config),
                    "--result-dir",
                    str(result_dir),
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("[SUCCESS] algorithm3", output.getvalue())
        self.assertEqual(errors.getvalue(), "")
        self.assertTrue((result_dir / "build_receipt.yaml").is_file())

    def test_build_cli_allocates_algorithm_and_incrementing_test_ids(self) -> None:
        if shutil.which("cc") is None:
            self.skipTest("a C compiler is required for mock algorithm fixtures")
        algorithm_root = self._copy_git_algorithm("algorithm1")
        config = self._write_config("algorithm1", algorithm_root)
        full_commit = self._git_output(algorithm_root, "rev-parse", "HEAD").strip()
        algorithm_root_result = self.root / "result" / "algorithm1"

        original_cwd = Path.cwd()
        self.addCleanup(os.chdir, original_cwd)
        os.chdir(self.root)
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            first_exit = main(["build", "--config", str(config)])
            second_exit = main(["build", "--config", str(config)])

        self.assertEqual(first_exit, 0)
        self.assertEqual(second_exit, 0)
        self.assertEqual(errors.getvalue(), "")
        for test_id in ("test-000", "test-001"):
            receipt_path = algorithm_root_result / test_id / "build_receipt.yaml"
            self.assertTrue(receipt_path.is_file())
            payload = yaml.safe_load(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["git_before"]["commit"], full_commit)
            self.assertEqual(payload["git_after"]["commit"], full_commit)
        self.assertIn(
            str(algorithm_root_result / "test-000" / "build_receipt.yaml"),
            output.getvalue(),
        )
        self.assertIn(
            str(algorithm_root_result / "test-001" / "build_receipt.yaml"),
            output.getvalue(),
        )

    def _copy_git_algorithm(self, algorithm_id: str) -> Path:
        destination = self.root / "algorithm repositories" / algorithm_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(FIXTURE_ROOT / algorithm_id, destination)
        self._git(destination, "init", "--quiet")
        self._git(destination, "config", "user.name", "Build Fixture")
        self._git(destination, "config", "user.email", "fixture@example.invalid")
        self._git(destination, "add", "build.sh", "main.c")
        self._git(destination, "commit", "--quiet", "-m", "initial fixture")
        return destination.resolve()

    def _write_config(
        self,
        algorithm_id: str,
        algorithm_root: Path,
        *,
        script_path: Optional[Path] = None,
    ) -> Path:
        path = self.root / f"{algorithm_id}-{len(list(self.root.glob('*.yaml')))}.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "algorithm": algorithm_id,
                    "build": {
                        "algorithm_path": str(algorithm_root),
                        "script_path": str(script_path or algorithm_root / "build.sh"),
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return path

    def _build(
        self,
        algorithm_id: str,
        algorithm_root: Path,
        result_dir: Path,
        *,
        timeout_seconds: float = 30.0,
    ):
        config = load_build_config(self._write_config(algorithm_id, algorithm_root))
        return BuildService(config).build(
            result_dir,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _replace_script(algorithm_root: Path, content: str) -> None:
        script = algorithm_root / "build.sh"
        script.write_text(content, encoding="utf-8")
        script.chmod(0o755)

    @staticmethod
    def _git(root: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )

    @staticmethod
    def _git_output(root: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        return result.stdout


if __name__ == "__main__":
    unittest.main()
