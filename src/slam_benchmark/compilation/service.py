"""Validate, execute, and record one algorithm compilation."""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence, Tuple

from ..algorithms.contracts import AlgorithmContract, get_algorithm_contract
from .models import BuildConfig, BuildReceipt, GitSnapshot
from .storage import BuildReceiptStore

DEFAULT_BUILD_TIMEOUT_SECONDS = 30 * 60.0
_PROCESS_TERMINATION_GRACE_SECONDS = 2.0
_GIT_COMMAND_TIMEOUT_SECONDS = 15.0


class BuildError(Exception):
    """The build request cannot be executed or persisted."""


class BuildService:
    def __init__(
        self,
        config: BuildConfig,
        store: Optional[BuildReceiptStore] = None,
    ):
        self.config = config
        try:
            self.contract = get_algorithm_contract(config.algorithm_id)
        except ValueError as exc:
            raise BuildError(str(exc)) from exc
        self.store = store or BuildReceiptStore()

    def allocate_result_dir(
        self,
        results_root: Path = Path("results"),
    ) -> Tuple[Path, GitSnapshot]:
        """Allocate one immutable algorithm/commit/test result directory."""

        algorithm_path, _ = self._validate_paths()
        git_snapshot = _capture_git_snapshot(algorithm_path)
        short_commit = _abbreviate_commit(algorithm_path, git_snapshot.commit)
        commit_root = (
            Path(results_root).expanduser().resolve()
            / "algorithms"
            / self.contract.algorithm_id
            / f"commit-{short_commit}"
        )
        return _allocate_test_directory(commit_root), git_snapshot

    def build_auto(
        self,
        results_root: Path = Path("results"),
        *,
        timeout_seconds: float = DEFAULT_BUILD_TIMEOUT_SECONDS,
    ) -> BuildReceipt:
        """Allocate ALGORITHM_ID/COMMIT_ID/TEST_ID and build into it."""

        if timeout_seconds <= 0:
            raise BuildError("build timeout must be greater than zero")

        result_dir, git_snapshot = self.allocate_result_dir(results_root)
        return self.build(
            result_dir,
            timeout_seconds=timeout_seconds,
            expected_commit=git_snapshot.commit,
        )

    def build(
        self,
        result_dir: Path,
        *,
        timeout_seconds: float = DEFAULT_BUILD_TIMEOUT_SECONDS,
        expected_commit: Optional[str] = None,
    ) -> BuildReceipt:
        if timeout_seconds <= 0:
            raise BuildError("build timeout must be greater than zero")

        result_root = Path(result_dir).expanduser().resolve()
        receipt_path = result_root / "build_receipt.yaml"
        stdout_path = result_root / "logs" / "build.stdout.log"
        stderr_path = result_root / "logs" / "build.stderr.log"
        self._prepare_output_paths(receipt_path, stdout_path, stderr_path)

        started_at = _utc_now()
        started_clock = time.monotonic()
        status = "failed"
        exit_code: Optional[int] = None
        failure_reason: Optional[str] = None
        script_digest: Optional[str] = None
        resolved_entrypoint: Optional[Path] = None
        git_before: Optional[GitSnapshot] = None
        git_after: Optional[GitSnapshot] = None
        algorithm_path = self.config.algorithm_path.expanduser().resolve()
        script_path = self.config.script_path.expanduser().resolve()

        try:
            algorithm_path, script_path = self._validate_paths()
            script_digest = _sha256_file(script_path)
            git_before = _capture_git_snapshot(algorithm_path)
            if expected_commit is not None and git_before.commit != expected_commit:
                raise BuildError(
                    "Git HEAD changed while allocating the build result directory"
                )
            status, exit_code, failure_reason = _execute_script(
                script_path,
                algorithm_path,
                stdout_path,
                stderr_path,
                timeout_seconds,
            )

            try:
                git_after = _capture_git_snapshot(algorithm_path)
            except BuildError as exc:
                if status == "success":
                    status = "failed"
                    failure_reason = str(exc)

            if status == "success":
                resolved_entrypoint = _resolve_entrypoint(
                    algorithm_path,
                    self.contract,
                )
                current_script_digest = _sha256_file(script_path)
                if current_script_digest != script_digest:
                    raise BuildError(
                        "build script changed while compilation was running"
                    )
                if git_after is None:
                    raise BuildError("cannot verify Git state after compilation")
                _verify_git_stability(git_before, git_after)
        except BuildError as exc:
            status = "failed"
            failure_reason = str(exc)
        except OSError as exc:
            status = "failed"
            failure_reason = f"build preparation failed: {exc}"

        receipt = BuildReceipt(
            algorithm_id=self.contract.algorithm_id,
            contract_version=self.contract.contract_version,
            status=status,
            started_at=started_at,
            finished_at=_utc_now(),
            duration_seconds=round(time.monotonic() - started_clock, 9),
            exit_code=exit_code,
            algorithm_path=algorithm_path,
            script_path=script_path,
            script_digest=script_digest,
            resolved_entrypoint=resolved_entrypoint,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            git_before=git_before,
            git_after=git_after,
            failure_reason=failure_reason,
        )
        try:
            self.store.save(receipt_path, receipt)
        except RuntimeError as exc:
            raise BuildError(str(exc)) from exc
        return receipt

    def verify_runtime_context(self, receipt: BuildReceipt) -> Path:
        """Confirm that a successful build still matches the algorithm checkout."""

        if receipt.status != "success":
            raise BuildError("cannot run an unsuccessful build receipt")
        if receipt.algorithm_id != self.contract.algorithm_id:
            raise BuildError("build receipt algorithm does not match configuration")
        if receipt.contract_version != self.contract.contract_version:
            raise BuildError("algorithm contract changed after compilation")

        algorithm_path, script_path = self._validate_paths()
        if receipt.algorithm_path != algorithm_path:
            raise BuildError("algorithm path changed after compilation")
        if receipt.script_path != script_path:
            raise BuildError("build script path changed after compilation")
        if receipt.script_digest is None:
            raise BuildError("build receipt does not contain a script digest")
        if _sha256_file(script_path) != receipt.script_digest:
            raise BuildError("build script changed after compilation")
        if receipt.git_after is None:
            raise BuildError("build receipt does not contain a final Git snapshot")

        current_git = _capture_git_snapshot(algorithm_path)
        try:
            _verify_git_stability(receipt.git_after, current_git)
        except BuildError as exc:
            raise BuildError(
                f"algorithm Git context no longer matches the build: {exc}"
            ) from exc

        entrypoint = _resolve_entrypoint(algorithm_path, self.contract)
        if (
            receipt.resolved_entrypoint is None
            or receipt.resolved_entrypoint != entrypoint
        ):
            raise BuildError("compiled entrypoint changed after compilation")
        return entrypoint

    def _validate_paths(self) -> Tuple[Path, Path]:
        algorithm_path = self.config.algorithm_path.expanduser().resolve()
        if not algorithm_path.is_dir():
            raise BuildError(f"algorithm_path is not a directory: {algorithm_path}")

        script_path = self.config.script_path.expanduser().resolve()
        if not script_path.is_file():
            raise BuildError(f"build script does not exist: {script_path}")
        if not _is_within(script_path, algorithm_path):
            raise BuildError(
                f"build script must be inside algorithm_path: {script_path}"
            )
        if not os.access(script_path, os.X_OK):
            raise BuildError(f"build script is not executable: {script_path}")

        repository_root = _git_repository_root(algorithm_path)
        if repository_root != algorithm_path:
            raise BuildError(
                "algorithm_path must be the Git repository root: "
                f"expected {repository_root}, got {algorithm_path}"
            )
        return algorithm_path, script_path

    @staticmethod
    def _prepare_output_paths(
        receipt_path: Path,
        stdout_path: Path,
        stderr_path: Path,
    ) -> None:
        for path in (receipt_path, stdout_path, stderr_path):
            if path.exists():
                raise BuildError(f"refusing to overwrite existing build output: {path}")
        try:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_path.touch(exist_ok=False)
            stderr_path.touch(exist_ok=False)
        except OSError as exc:
            raise BuildError(f"cannot prepare build result directory: {exc}") from exc


def _execute_script(
    script_path: Path,
    algorithm_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: float,
) -> Tuple[str, Optional[int], Optional[str]]:
    process: Optional[subprocess.Popen[bytes]] = None
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                [str(script_path)],
                cwd=algorithm_path,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            exit_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            if process is not None:
                _terminate_process_group(process)
                exit_code = process.returncode
            else:
                exit_code = None
            return (
                "timeout",
                exit_code,
                f"build exceeded timeout of {timeout_seconds:g} seconds",
            )
        except KeyboardInterrupt:
            if process is not None:
                _terminate_process_group(process)
                exit_code = process.returncode
            else:
                exit_code = None
            return "interrupted", exit_code, "build interrupted by user"
        except OSError as exc:
            return "failed", None, f"cannot start build script: {exc}"

    if exit_code != 0:
        return "failed", exit_code, f"build script exited with code {exit_code}"
    return "success", exit_code, None


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=_PROCESS_TERMINATION_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait()


def _resolve_entrypoint(
    algorithm_path: Path,
    contract: AlgorithmContract,
) -> Path:
    entrypoint = (algorithm_path / contract.entrypoint_relative_path).resolve()
    if not _is_within(entrypoint, algorithm_path):
        raise BuildError(f"resolved entrypoint is outside algorithm_path: {entrypoint}")
    if not entrypoint.is_file():
        raise BuildError(f"compiled entrypoint does not exist: {entrypoint}")
    if not os.access(entrypoint, os.X_OK):
        raise BuildError(f"compiled entrypoint is not executable: {entrypoint}")
    return entrypoint


def _git_repository_root(algorithm_path: Path) -> Path:
    output = _run_git(algorithm_path, ("rev-parse", "--show-toplevel"))
    try:
        return Path(output.decode("utf-8").strip()).resolve()
    except UnicodeDecodeError as exc:
        raise BuildError("Git repository root is not valid UTF-8") from exc


def _capture_git_snapshot(algorithm_path: Path) -> GitSnapshot:
    commit = _decode_git_text(
        _run_git(algorithm_path, ("rev-parse", "HEAD")),
        "Git commit",
    ).strip()
    branch_result = _run_git_process(
        algorithm_path,
        ("symbolic-ref", "--quiet", "--short", "HEAD"),
    )
    if branch_result.returncode == 0:
        branch = _decode_git_text(branch_result.stdout, "Git branch").strip()
    elif branch_result.returncode == 1:
        branch = None
    else:
        raise BuildError(_git_failure_message(branch_result))

    tracked_raw = _run_git(
        algorithm_path,
        ("status", "--porcelain=v1", "--untracked-files=no", "-z"),
    )
    untracked_raw = _run_git(
        algorithm_path,
        ("ls-files", "--others", "--exclude-standard", "-z"),
    )
    tracked_diff = _run_git(
        algorithm_path,
        ("diff", "--binary", "HEAD", "--"),
    )
    submodules_text = _decode_git_text(
        _run_git(algorithm_path, ("submodule", "status", "--recursive")),
        "Git submodule status",
    )
    tracked_changes = _decode_nul_records(tracked_raw, "Git tracked status")
    untracked_paths = _decode_nul_records(untracked_raw, "Git untracked paths")
    submodules = tuple(line for line in submodules_text.splitlines() if line.strip())
    return GitSnapshot(
        commit=commit,
        branch=branch,
        detached=branch is None,
        dirty=bool(tracked_changes or untracked_paths),
        tracked_changes=tracked_changes,
        untracked_paths=untracked_paths,
        tracked_state_digest=hashlib.sha256(tracked_diff).hexdigest(),
        submodules=submodules,
    )


def _verify_git_stability(before: GitSnapshot, after: GitSnapshot) -> None:
    if before.commit != after.commit:
        raise BuildError("Git HEAD changed while compilation was running")
    if before.branch != after.branch or before.detached != after.detached:
        raise BuildError("Git branch state changed while compilation was running")
    if before.tracked_state_digest != after.tracked_state_digest:
        raise BuildError("tracked source files changed while compilation was running")
    if before.submodules != after.submodules:
        raise BuildError("Git submodule state changed while compilation was running")


def _abbreviate_commit(algorithm_path: Path, commit: str) -> str:
    return _decode_git_text(
        _run_git(algorithm_path, ("rev-parse", "--short=12", commit)),
        "abbreviated Git commit",
    ).strip()


def _allocate_test_directory(commit_root: Path) -> Path:
    try:
        commit_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BuildError(f"cannot create commit result directory: {exc}") from exc

    index = 1
    while True:
        candidate = commit_root / f"test-{index:03d}"
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            index += 1
        except OSError as exc:
            raise BuildError(f"cannot allocate test result directory: {exc}") from exc


def _run_git(
    algorithm_path: Path,
    arguments: Sequence[str],
) -> bytes:
    result = _run_git_process(algorithm_path, arguments)
    if result.returncode != 0:
        raise BuildError(_git_failure_message(result))
    return result.stdout


def _run_git_process(
    algorithm_path: Path,
    arguments: Sequence[str],
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=algorithm_path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"cannot inspect algorithm Git repository: {exc}") from exc


def _git_failure_message(result: subprocess.CompletedProcess[bytes]) -> str:
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    return f"Git inspection failed: {stderr or f'exit code {result.returncode}'}"


def _decode_git_text(value: bytes, label: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError(f"{label} is not valid UTF-8") from exc


def _decode_nul_records(value: bytes, label: str) -> Tuple[str, ...]:
    text = _decode_git_text(value, label)
    return tuple(item for item in text.split("\0") if item)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BuildError(f"cannot hash build script {path}: {exc}") from exc
    return digest.hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
