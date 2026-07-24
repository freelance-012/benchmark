"""Coordinate build, dataset selection, Segment execution, and recovery."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..algorithms.contracts import (
    EVALUATION_WORKFLOW_SF_VLOC,
    AlgorithmContract,
    get_algorithm_contract,
)
from ..compilation.models import BuildReceipt
from ..compilation.service import BuildError, BuildService
from ..compilation.storage import BuildReceiptStore
from ..datasets.contracts import get_contract as get_dataset_contract
from ..datasets.errors import DatasetError
from ..datasets.models import DatasetInstance, ScanDiagnostic, Segment
from ..datasets.paths import resolve_dataset_file
from ..datasets.service import DatasetManager
from .command import CommandError, build_run_command
from .models import (
    FAILURE_POLICIES,
    FAILURE_POLICY_FAIL_FAST,
    DatasetRunReceipt,
    ProcessResult,
    RunCheckpoint,
    RunIssue,
    RunRequest,
    RunSummary,
    SegmentRunReceipt,
)
from .runner import (
    RunnerError,
    prepare_fixed_output,
    resolve_fixed_output,
    run_process,
    validate_fixed_output,
)
from .storage import RunStorageError, RunStore


class ExecutionError(Exception):
    """A full run cannot be started, continued, or safely recorded."""


@dataclass(frozen=True)
class _PreparedDatasets:
    instances: Tuple[DatasetInstance, ...]
    issues: Tuple[RunIssue, ...]


class ExecutionService:
    def __init__(
        self,
        store: Optional[RunStore] = None,
        build_store: Optional[BuildReceiptStore] = None,
    ):
        self.store = store or RunStore()
        self.build_store = build_store or BuildReceiptStore()

    def start(self, request: RunRequest) -> RunSummary:
        self._validate_request(request)
        try:
            contract = get_algorithm_contract(request.build_config.algorithm_id)
        except ValueError as exc:
            raise ExecutionError(str(exc)) from exc

        build_service = BuildService(request.build_config, self.build_store)
        try:
            test_root, allocated_git = build_service.allocate_result_dir(
                request.results_root
            )
        except BuildError as exc:
            raise ExecutionError(str(exc)) from exc

        prepared = self._prepare_datasets(request, contract)
        try:
            self.store.freeze_configuration(
                test_root,
                request,
                contract,
                allocated_git.commit,
                prepared.instances,
                prepared.issues,
            )
        except RunStorageError as exc:
            raise ExecutionError(str(exc)) from exc

        checkpoint = RunCheckpoint(
            test_id=test_root.name,
            algorithm_id=contract.algorithm_id,
            contract_version=contract.contract_version,
            git_commit=allocated_git.commit,
            failure_policy=request.failure_policy,
            failure_threshold=request.failure_threshold,
            timeout_seconds=request.timeout_seconds,
            dataset_order=tuple(item.dataset_id for item in prepared.instances),
            next_dataset_index=0,
            finished_dataset_ids=(),
            dataset_receipt_paths=(),
            preflight_issues=prepared.issues,
            algorithm_failure_count=0,
            status="prepared",
            updated_at=_utc_now(),
        )
        self._save_checkpoint(test_root, checkpoint)

        if prepared.issues and request.failure_policy == FAILURE_POLICY_FAIL_FAST:
            checkpoint = replace(
                checkpoint,
                status="failed",
                updated_at=_utc_now(),
                failure_reason=(
                    "dataset preflight failed in fail-fast mode: "
                    f"{prepared.issues[0].message}"
                ),
            )
            self._save_checkpoint(test_root, checkpoint)
            return self._summarize(test_root, checkpoint)

        if not prepared.instances:
            checkpoint = replace(
                checkpoint,
                status="failed",
                updated_at=_utc_now(),
                failure_reason="no runnable datasets were selected",
            )
            self._save_checkpoint(test_root, checkpoint)
            return self._summarize(test_root, checkpoint)

        build_receipt = build_service.build(
            test_root,
            expected_commit=allocated_git.commit,
        )
        if build_receipt.status != "success":
            checkpoint = replace(
                checkpoint,
                status="failed",
                updated_at=_utc_now(),
                failure_reason=(
                    build_receipt.failure_reason or "algorithm compilation failed"
                ),
            )
            self._save_checkpoint(test_root, checkpoint)
            return self._summarize(test_root, checkpoint)

        try:
            build_service.verify_runtime_context(build_receipt)
        except BuildError as exc:
            checkpoint = replace(
                checkpoint,
                status="failed",
                updated_at=_utc_now(),
                failure_reason=str(exc),
            )
            self._save_checkpoint(test_root, checkpoint)
            return self._summarize(test_root, checkpoint)

        checkpoint = replace(checkpoint, status="running", updated_at=_utc_now())
        self._save_checkpoint(test_root, checkpoint)
        return self._execute(
            request,
            contract,
            prepared.instances,
            build_service,
            build_receipt,
            test_root,
            checkpoint,
        )

    def resume(self, request: RunRequest, result_root: Path) -> RunSummary:
        self._validate_request(request)
        test_root = Path(result_root).expanduser().resolve()
        if not test_root.is_dir():
            raise ExecutionError(f"resume result directory does not exist: {test_root}")

        try:
            checkpoint = self.store.load_checkpoint(test_root)
            frozen_algorithm = self.store.load_mapping(
                test_root / "config" / "algorithm.yaml"
            )
            frozen_run = self.store.load_mapping(test_root / "config" / "run.yaml")
        except RunStorageError as exc:
            raise ExecutionError(str(exc)) from exc

        if checkpoint.test_id != test_root.name:
            raise ExecutionError("checkpoint test_id does not match result directory")
        if checkpoint.next_dataset_index >= len(checkpoint.dataset_order):
            raise ExecutionError("run has no incomplete dataset to resume")

        try:
            contract = get_algorithm_contract(request.build_config.algorithm_id)
        except ValueError as exc:
            raise ExecutionError(str(exc)) from exc
        self._verify_frozen_request(
            request,
            contract,
            checkpoint,
            frozen_algorithm,
            frozen_run,
        )

        prepared = self._prepare_datasets(request, contract)
        self._verify_frozen_datasets(
            test_root,
            checkpoint,
            prepared,
            frozen_run,
        )

        try:
            build_receipt = self.build_store.load(test_root / "build_receipt.yaml")
        except RuntimeError as exc:
            raise ExecutionError(str(exc)) from exc
        if build_receipt.result_root != test_root:
            raise ExecutionError("build receipt result paths do not match result root")
        if (
            build_receipt.git_after is None
            or build_receipt.git_after.commit != checkpoint.git_commit
        ):
            raise ExecutionError("build receipt commit does not match checkpoint")
        build_service = BuildService(request.build_config, self.build_store)
        try:
            build_service.verify_runtime_context(build_receipt)
        except BuildError as exc:
            raise ExecutionError(
                f"cannot resume because build context changed: {exc}"
            ) from exc

        current_dataset_id = checkpoint.dataset_order[checkpoint.next_dataset_index]
        checkpoint = self._remove_incomplete_attempt(
            test_root,
            checkpoint,
            current_dataset_id,
        )
        try:
            self.store.archive_incomplete_dataset(test_root, current_dataset_id)
        except RunStorageError as exc:
            raise ExecutionError(str(exc)) from exc

        checkpoint = replace(
            checkpoint,
            status="running",
            updated_at=_utc_now(),
            failure_reason=None,
        )
        self._save_checkpoint(test_root, checkpoint)
        return self._execute(
            request,
            contract,
            prepared.instances,
            build_service,
            build_receipt,
            test_root,
            checkpoint,
        )

    def _execute(
        self,
        request: RunRequest,
        contract: AlgorithmContract,
        instances: Tuple[DatasetInstance, ...],
        build_service: BuildService,
        build_receipt: BuildReceipt,
        test_root: Path,
        checkpoint: RunCheckpoint,
    ) -> RunSummary:
        instance_by_id = {item.dataset_id: item for item in instances}
        ordered_instances = tuple(
            instance_by_id[dataset_id] for dataset_id in checkpoint.dataset_order
        )

        for index in range(checkpoint.next_dataset_index, len(ordered_instances)):
            instance = ordered_instances[index]
            try:
                entrypoint = build_service.verify_runtime_context(build_receipt)
                dataset_receipt = self._run_dataset(
                    request,
                    contract,
                    instance,
                    entrypoint,
                    build_service,
                    build_receipt,
                    test_root,
                )
                receipt_path = self.store.save_dataset_receipt(
                    test_root, dataset_receipt
                )
            except (BuildError, RunnerError, RunStorageError) as exc:
                checkpoint = replace(
                    checkpoint,
                    status="failed",
                    next_dataset_index=index,
                    updated_at=_utc_now(),
                    failure_reason=str(exc),
                )
                self._save_checkpoint(test_root, checkpoint)
                return self._summarize(test_root, checkpoint)

            relative_receipt = str(receipt_path.relative_to(test_root))
            receipt_paths = checkpoint.dataset_receipt_paths + (relative_receipt,)
            failure_count = (
                checkpoint.algorithm_failure_count
                + dataset_receipt.algorithm_failure_count
            )

            if dataset_receipt.status == "interrupted":
                checkpoint = replace(
                    checkpoint,
                    status="interrupted",
                    next_dataset_index=index,
                    dataset_receipt_paths=receipt_paths,
                    algorithm_failure_count=failure_count,
                    updated_at=_utc_now(),
                    failure_reason=dataset_receipt.failure_reason,
                )
                self._save_checkpoint(test_root, checkpoint)
                return self._summarize(test_root, checkpoint)

            if (
                dataset_receipt.status == "failed"
                and request.failure_policy == FAILURE_POLICY_FAIL_FAST
            ):
                checkpoint = replace(
                    checkpoint,
                    status="failed",
                    next_dataset_index=index,
                    dataset_receipt_paths=receipt_paths,
                    algorithm_failure_count=failure_count,
                    updated_at=_utc_now(),
                    failure_reason=dataset_receipt.failure_reason,
                )
                self._save_checkpoint(test_root, checkpoint)
                return self._summarize(test_root, checkpoint)

            checkpoint = replace(
                checkpoint,
                status="running",
                next_dataset_index=index + 1,
                finished_dataset_ids=(
                    checkpoint.finished_dataset_ids + (instance.dataset_id,)
                ),
                dataset_receipt_paths=receipt_paths,
                algorithm_failure_count=failure_count,
                updated_at=_utc_now(),
                failure_reason=None,
            )
            self._save_checkpoint(test_root, checkpoint)

        final_status = (
            "failed"
            if checkpoint.algorithm_failure_count > request.failure_threshold
            else "success"
        )
        final_reason = (
            (
                "algorithm failure count "
                f"{checkpoint.algorithm_failure_count} exceeded threshold "
                f"{request.failure_threshold}"
            )
            if final_status == "failed"
            else None
        )
        checkpoint = replace(
            checkpoint,
            status=final_status,
            updated_at=_utc_now(),
            failure_reason=final_reason,
        )
        self._save_checkpoint(test_root, checkpoint)
        return self._summarize(test_root, checkpoint)

    def _run_dataset(
        self,
        request: RunRequest,
        contract: AlgorithmContract,
        instance: DatasetInstance,
        entrypoint: Path,
        build_service: BuildService,
        build_receipt: BuildReceipt,
        test_root: Path,
    ) -> DatasetRunReceipt:
        valid_segments = tuple(
            sorted(
                (item for item in instance.segments if item.valid),
                key=lambda item: item.sequence_no,
            )
        )
        successful: List[str] = []
        failed: List[str] = []
        not_run: List[str] = []
        algorithm_failure_count = 0
        failure_reason: Optional[str] = None
        dataset_status = "success"

        for segment_index, segment in enumerate(valid_segments):
            entrypoint = build_service.verify_runtime_context(build_receipt)
            try:
                command = build_run_command(
                    entrypoint,
                    contract,
                    instance,
                    segment,
                )
            except CommandError as exc:
                failed.append(segment.segment_id)
                not_run.extend(
                    item.segment_id for item in valid_segments[segment_index + 1 :]
                )
                failure_reason = str(exc)
                dataset_status = "failed"
                break

            paths = self.store.segment_paths(
                test_root,
                instance.dataset_id,
                segment.segment_id,
            )
            output_source = resolve_fixed_output(
                request.build_config.algorithm_path,
                contract.fixed_output_relative_path,
            )
            prepare_fixed_output(output_source)
            process = run_process(
                command,
                request.build_config.algorithm_path,
                paths.stdout_path,
                paths.stderr_path,
                request.timeout_seconds,
            )

            output_checks: Dict[str, Any]
            output_result: Optional[Path] = None
            segment_status = process.status
            segment_failure = process.failure_reason
            algorithm_failure = process.status in {"failed", "timeout"}

            if process.status == "success":
                output_checks, output_error = validate_fixed_output(
                    output_source,
                    contract,
                    instance,
                    segment,
                    command,
                )
                output_checks["accepted"] = output_error is None
                if output_error is not None:
                    segment_status = "failed"
                    segment_failure = output_error
                    algorithm_failure = True
                else:
                    try:
                        output_result = self.store.copy_fixed_output(
                            output_source,
                            paths.result_dir,
                            contract.fixed_output_relative_path,
                        )
                        self._copy_evaluation_support_files(
                            contract,
                            instance,
                            paths.result_dir,
                        )
                    except RunStorageError as exc:
                        segment_status = "failed"
                        segment_failure = str(exc)
                        algorithm_failure = False
                        receipt = self._segment_receipt(
                            test_root,
                            request,
                            contract,
                            instance,
                            segment,
                            entrypoint,
                            command.argv,
                            process,
                            paths.stdout_path,
                            paths.stderr_path,
                            output_source,
                            output_result,
                            output_checks,
                            segment_status,
                            algorithm_failure,
                            segment_failure,
                        )
                        self.store.save_segment_receipt(paths, receipt)
                        raise
            else:
                output_checks, output_error = validate_fixed_output(
                    output_source,
                    contract,
                    instance,
                    segment,
                    command,
                )
                output_checks["accepted"] = False
                if output_error is not None:
                    output_checks["validation_error"] = output_error

            receipt = self._segment_receipt(
                test_root,
                request,
                contract,
                instance,
                segment,
                entrypoint,
                command.argv,
                process,
                paths.stdout_path,
                paths.stderr_path,
                output_source,
                output_result,
                output_checks,
                segment_status,
                algorithm_failure,
                segment_failure,
            )
            self.store.save_segment_receipt(paths, receipt)
            build_service.verify_runtime_context(build_receipt)

            if segment_status == "success":
                successful.append(segment.segment_id)
                continue

            failed.append(segment.segment_id)
            not_run.extend(
                item.segment_id for item in valid_segments[segment_index + 1 :]
            )
            failure_reason = segment_failure
            if algorithm_failure:
                algorithm_failure_count += 1
            dataset_status = (
                "interrupted" if segment_status == "interrupted" else "failed"
            )
            break

        return DatasetRunReceipt(
            test_id=test_root.name,
            algorithm_id=contract.algorithm_id,
            dataset_id=instance.dataset_id,
            dataset_type=instance.dataset_type,
            dataset_path=instance.root_path,
            status=dataset_status,
            successful_segment_ids=tuple(successful),
            failed_segment_ids=tuple(failed),
            not_run_segment_ids=tuple(not_run),
            algorithm_failure_count=algorithm_failure_count,
            failure_reason=failure_reason,
        )

    def _copy_evaluation_support_files(
        self,
        contract: AlgorithmContract,
        instance: DatasetInstance,
        result_dir: Path,
    ) -> None:
        if contract.evaluation_workflow is None:
            return
        dataset_contract = get_dataset_contract(instance.dataset_type)
        calibration_role = dataset_contract.evaluation_calibration_role
        if calibration_role is None:
            return

        self._copy_dataset_result_file(
            instance,
            calibration_role,
            result_dir,
            required=True,
        )
        if contract.evaluation_workflow == EVALUATION_WORKFLOW_SF_VLOC:
            self._copy_dataset_result_file(
                instance,
                "home_point_path",
                result_dir,
                required=True,
            )

    def _copy_dataset_result_file(
        self,
        instance: DatasetInstance,
        role: str,
        result_dir: Path,
        *,
        required: bool,
    ) -> None:
        raw_path = instance.input_paths.get(role)
        if raw_path is None:
            if required:
                raise RunStorageError(
                    f"dataset instance is missing evaluation input {role}"
                )
            return
        try:
            source = resolve_dataset_file(
                Path(raw_path),
                instance.root_path,
                role,
            )
        except DatasetError as exc:
            raise RunStorageError(
                f"cannot prepare evaluation input {role}: {exc}"
            ) from exc
        self.store.copy_result_file(
            source,
            result_dir,
            Path(source.name),
        )

    @staticmethod
    def _segment_receipt(
        test_root: Path,
        request: RunRequest,
        contract: AlgorithmContract,
        instance: DatasetInstance,
        segment: Segment,
        entrypoint: Path,
        command: Tuple[str, ...],
        process: ProcessResult,
        stdout_path: Path,
        stderr_path: Path,
        output_source: Path,
        output_result: Optional[Path],
        output_checks: Dict[str, Any],
        status: str,
        algorithm_failure: bool,
        failure_reason: Optional[str],
    ) -> SegmentRunReceipt:
        return SegmentRunReceipt(
            test_id=test_root.name,
            algorithm_id=contract.algorithm_id,
            contract_version=contract.contract_version,
            dataset_id=instance.dataset_id,
            dataset_type=instance.dataset_type,
            segment_id=segment.segment_id,
            resolved_entrypoint=entrypoint,
            working_dir_path=request.build_config.algorithm_path,
            command=command,
            started_at=process.started_at,
            finished_at=process.finished_at,
            duration_seconds=process.duration_seconds,
            status=status,
            exit_code=process.exit_code,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            output_source_path=output_source,
            output_result_path=output_result,
            output_checks=dict(output_checks),
            algorithm_failure=algorithm_failure,
            failure_reason=failure_reason,
        )

    def _prepare_datasets(
        self,
        request: RunRequest,
        contract: AlgorithmContract,
    ) -> _PreparedDatasets:
        selected = tuple(
            Path(item).expanduser().resolve() for item in request.selected_dataset_paths
        )
        instances_by_root: Dict[Path, DatasetInstance] = {}
        issues: List[RunIssue] = []

        for config in request.dataset_configs:
            try:
                report = DatasetManager(config).scan(refresh=False, persist=True)
            except DatasetError as exc:
                issues.append(
                    RunIssue(
                        "dataset_scan_failed",
                        config.root_path,
                        str(exc),
                    )
                )
                continue

            for diagnostic in report.diagnostics:
                if diagnostic.level != "error":
                    continue
                if selected and not _diagnostic_matches_selection(diagnostic, selected):
                    continue
                issues.append(
                    RunIssue(
                        diagnostic.code,
                        diagnostic.path.resolve(),
                        diagnostic.message,
                    )
                )

            for instance in report.datasets:
                root = instance.root_path.resolve()
                if selected and not _path_matches_selection(root, selected):
                    continue
                existing = instances_by_root.get(root)
                if existing is not None and existing.to_dict() != instance.to_dict():
                    issues.append(
                        RunIssue(
                            "dataset_contract_conflict",
                            root,
                            "the same dataset path was loaded with conflicting types",
                            instance.dataset_id,
                        )
                    )
                    continue
                instances_by_root[root] = instance

        if selected:
            for path in selected:
                if not any(
                    _path_matches_selection(root, (path,)) for root in instances_by_root
                ):
                    issues.append(
                        RunIssue(
                            "selected_dataset_not_found",
                            path,
                            "selected dataset was not registered by the supplied configs",
                        )
                    )

        runnable: List[DatasetInstance] = []
        for instance in sorted(
            instances_by_root.values(),
            key=lambda item: (str(item.root_path), item.dataset_id),
        ):
            if instance.status != "ready":
                issues.append(
                    RunIssue(
                        "dataset_unavailable",
                        instance.root_path,
                        "dataset has no valid Segment",
                        instance.dataset_id,
                    )
                )
                continue
            try:
                contract.run_contract_for(instance.dataset_type)
            except ValueError as exc:
                issues.append(
                    RunIssue(
                        "dataset_not_supported",
                        instance.root_path,
                        str(exc),
                        instance.dataset_id,
                    )
                )
                continue
            if (
                contract.evaluation_workflow == EVALUATION_WORKFLOW_SF_VLOC
                and instance.input_paths.get("home_point_path") is None
            ):
                issues.append(
                    RunIssue(
                        "missing_vloc_home_point",
                        instance.root_path,
                        "sf_vloc requires a valid home_point.txt",
                        instance.dataset_id,
                    )
                )
                continue
            runnable.append(instance)

        return _PreparedDatasets(
            tuple(runnable),
            tuple(_deduplicate_issues(issues)),
        )

    def _verify_frozen_request(
        self,
        request: RunRequest,
        contract: AlgorithmContract,
        checkpoint: RunCheckpoint,
        frozen_algorithm: Dict[str, object],
        frozen_run: Dict[str, object],
    ) -> None:
        expected_algorithm = {
            "schema_version": 1,
            "algorithm": request.build_config.algorithm_id,
            "build": {
                "algorithm_path": str(request.build_config.algorithm_path),
                "script_path": str(request.build_config.script_path),
            },
            "contract": contract.to_dict(),
        }
        if frozen_algorithm != expected_algorithm:
            raise ExecutionError("algorithm configuration or contract changed")
        if checkpoint.algorithm_id != contract.algorithm_id:
            raise ExecutionError("checkpoint algorithm changed")
        if checkpoint.contract_version != contract.contract_version:
            raise ExecutionError("checkpoint contract version changed")
        if frozen_run.get("test_id") != checkpoint.test_id:
            raise ExecutionError("frozen run test_id changed")
        if frozen_run.get("git_commit") != checkpoint.git_commit:
            raise ExecutionError("frozen run commit changed")
        if checkpoint.failure_policy != request.failure_policy:
            raise ExecutionError("failure policy changed; start a new run")
        if checkpoint.failure_threshold != request.failure_threshold:
            raise ExecutionError("failure threshold changed; start a new run")
        if checkpoint.timeout_seconds != request.timeout_seconds:
            raise ExecutionError("run timeout changed; start a new run")

        frozen_configs = frozen_run.get("dataset_configs")
        current_configs = [
            {
                "root_path": str(item.root_path),
                "dataset_type": item.dataset_type,
            }
            for item in request.dataset_configs
        ]
        if frozen_configs != current_configs:
            raise ExecutionError("dataset configs changed; start a new run")
        frozen_selected = frozen_run.get("selected_dataset_paths")
        current_selected = [
            str(Path(item).expanduser().resolve())
            for item in request.selected_dataset_paths
        ]
        if frozen_selected != current_selected:
            raise ExecutionError("selected dataset paths changed; start a new run")

    def _verify_frozen_datasets(
        self,
        test_root: Path,
        checkpoint: RunCheckpoint,
        prepared: _PreparedDatasets,
        frozen_run: Dict[str, object],
    ) -> None:
        if (
            tuple(item.dataset_id for item in prepared.instances)
            != checkpoint.dataset_order
        ):
            raise ExecutionError("runnable dataset order changed; start a new run")
        if [item.to_dict() for item in prepared.issues] != frozen_run.get(
            "preflight_issues"
        ):
            raise ExecutionError("dataset preflight results changed; start a new run")
        for instance in prepared.instances:
            path = test_root / "config" / "datasets" / f"{instance.dataset_id}.yaml"
            try:
                frozen_instance = self.store.load_mapping(path)
            except RunStorageError as exc:
                raise ExecutionError(str(exc)) from exc
            if frozen_instance != instance.to_dict():
                raise ExecutionError(
                    f"dataset instance changed for {instance.dataset_id}; start a new run"
                )

    def _remove_incomplete_attempt(
        self,
        test_root: Path,
        checkpoint: RunCheckpoint,
        dataset_id: str,
    ) -> RunCheckpoint:
        relative = (Path("datasets") / dataset_id / "dataset_receipt.yaml").as_posix()
        if relative not in checkpoint.dataset_receipt_paths:
            return checkpoint
        try:
            payload = self.store.load_mapping(test_root / relative)
            previous_failures = int(payload.get("algorithm_failure_count", 0))
        except (RunStorageError, TypeError, ValueError) as exc:
            raise ExecutionError(
                f"cannot inspect incomplete dataset receipt: {exc}"
            ) from exc
        return replace(
            checkpoint,
            dataset_receipt_paths=tuple(
                item for item in checkpoint.dataset_receipt_paths if item != relative
            ),
            algorithm_failure_count=max(
                0, checkpoint.algorithm_failure_count - previous_failures
            ),
        )

    def _summarize(
        self,
        test_root: Path,
        checkpoint: RunCheckpoint,
    ) -> RunSummary:
        successful_datasets = 0
        failed_datasets = 0
        successful_segments = 0
        failed_segments = 0

        for relative in checkpoint.dataset_receipt_paths:
            try:
                payload = self.store.load_mapping(test_root / relative)
            except RunStorageError as exc:
                raise ExecutionError(str(exc)) from exc
            status = str(payload.get("status"))
            if status == "success":
                successful_datasets += 1
            else:
                failed_datasets += 1
            successful_segments += len(payload.get("successful_segment_ids", []))
            failed_segments += len(payload.get("failed_segment_ids", []))

        total_segments = 0
        for dataset_id in checkpoint.dataset_order:
            try:
                payload = self.store.load_mapping(
                    test_root / "config" / "datasets" / f"{dataset_id}.yaml"
                )
                raw_segments = payload.get("segments", [])
                total_segments += sum(
                    1
                    for item in raw_segments
                    if isinstance(item, dict) and item.get("valid") is True
                )
            except RunStorageError as exc:
                raise ExecutionError(str(exc)) from exc

        total_datasets = len(checkpoint.dataset_order) + len(
            checkpoint.preflight_issues
        )
        not_run_datasets = max(
            0, total_datasets - successful_datasets - failed_datasets
        )
        not_run_segments = max(
            0, total_segments - successful_segments - failed_segments
        )
        return RunSummary(
            status=checkpoint.status,
            result_root=test_root,
            total_datasets=total_datasets,
            successful_datasets=successful_datasets,
            failed_datasets=failed_datasets,
            not_run_datasets=not_run_datasets,
            successful_segments=successful_segments,
            failed_segments=failed_segments,
            not_run_segments=not_run_segments,
            algorithm_failure_count=checkpoint.algorithm_failure_count,
            failure_threshold=checkpoint.failure_threshold,
            failure_reason=checkpoint.failure_reason,
        )

    def _save_checkpoint(
        self,
        test_root: Path,
        checkpoint: RunCheckpoint,
    ) -> None:
        try:
            self.store.save_checkpoint(test_root, checkpoint)
        except RunStorageError as exc:
            raise ExecutionError(str(exc)) from exc

    @staticmethod
    def _validate_request(request: RunRequest) -> None:
        if request.failure_policy not in FAILURE_POLICIES:
            choices = ", ".join(FAILURE_POLICIES)
            raise ExecutionError(f"failure policy must be one of: {choices}")
        if request.failure_threshold < 0:
            raise ExecutionError("failure threshold must not be negative")
        if request.timeout_seconds <= 0:
            raise ExecutionError("run timeout must be greater than zero")
        if not request.dataset_configs:
            raise ExecutionError("at least one dataset config is required")


def _deduplicate_issues(issues: Sequence[RunIssue]) -> List[RunIssue]:
    merged: Dict[str, RunIssue] = {}
    for issue in issues:
        key = str(issue.path.resolve())
        existing = merged.get(key)
        if existing is None:
            merged[key] = issue
            continue
        messages = existing.message.split(" | ")
        if issue.message not in messages:
            messages.append(issue.message)
        codes = existing.code.split("+")
        if issue.code not in codes:
            codes.append(issue.code)
        merged[key] = RunIssue(
            "+".join(codes),
            existing.path,
            " | ".join(messages),
            existing.dataset_id or issue.dataset_id,
        )
    return sorted(merged.values(), key=lambda item: (str(item.path), item.code))


def _path_matches_selection(path: Path, selected: Sequence[Path]) -> bool:
    resolved = path.resolve()
    return any(
        resolved == item.resolve() or _is_within(resolved, item.resolve())
        for item in selected
    )


def _diagnostic_matches_selection(
    diagnostic: ScanDiagnostic,
    selected: Sequence[Path],
) -> bool:
    path = diagnostic.path.resolve()
    return any(
        path == item.resolve()
        or _is_within(path, item.resolve())
        or _is_within(item.resolve(), path)
        for item in selected
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
