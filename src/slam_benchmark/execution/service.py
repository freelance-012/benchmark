"""Coordinate build, dataset selection, Segment execution, and recovery."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..algorithms.contracts import AlgorithmContract, get_algorithm_contract
from ..compilation.models import BuildReceipt
from ..compilation.service import BuildError, BuildService
from ..compilation.storage import BuildReceiptStore
from ..datasets.contracts import get_contract as get_dataset_contract
from ..datasets.errors import DatasetError
from ..datasets.models import DatasetInstance, ScanDiagnostic, Segment
from ..datasets.paths import resolve_dataset_file
from ..datasets.service import DatasetManager
from ..debug import debug_enabled, debug_output
from ..evaluation import (
    DEFAULT_RPE_DELTA_UNIT,
    DEFAULT_RPE_DELTA_VALUE,
    EvaluationError,
    EvaluationRequest,
    EvaluationService,
    SummaryWorkbookWriter,
    normalize_rpe_delta,
)
from ..progress import (
    MODULE_BUILD,
    MODULE_DATASET,
    MODULE_EVALUATE,
    MODULE_REPORT,
    MODULE_RUN,
    MODULE_TOTAL,
    NullProgressReporter,
    ProgressReporter,
)
from .command import (
    CommandError,
    DatasetUnavailableError,
    build_run_command,
    verify_run_inputs_available,
)
from .models import (
    FAILURE_POLICIES,
    FAILURE_POLICY_FAIL_FAST,
    RUN_CONFIG_SCHEMA_VERSION,
    DatasetRunReceipt,
    ProcessResult,
    ResolvedRunCommand,
    RunCheckpoint,
    RunIssue,
    RunRequest,
    RunSummary,
    SegmentPaths,
    SegmentRunReceipt,
)
from .runner import (
    NumberedOutputSnapshot,
    RunnerError,
    prepare_fixed_output,
    read_numbered_output_snapshot,
    resolve_fixed_output,
    resolve_numbered_output_sources,
    run_process,
    validate_additional_output,
    validate_fixed_output,
)
from .runtime_progress import (
    ProgressParser,
    RunEtaEstimator,
    RuntimeEstimate,
    progress_parser as resolve_progress_parser,
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
        evaluation_service: Optional[EvaluationService] = None,
        summary_writer: Optional[SummaryWorkbookWriter] = None,
        progress: Optional[ProgressReporter] = None,
    ):
        self.store = store or RunStore()
        self.build_store = build_store or BuildReceiptStore()
        self.evaluation_service = evaluation_service or EvaluationService()
        self.summary_writer = summary_writer or SummaryWorkbookWriter()
        self.progress = progress or NullProgressReporter()

    def start(self, request: RunRequest) -> RunSummary:
        self._validate_request(request)
        try:
            contract = get_algorithm_contract(request.build_config.algorithm_id)
        except ValueError as exc:
            raise ExecutionError(str(exc)) from exc
        self._validate_output_configuration(request, contract)

        build_service = BuildService(request.build_config, self.build_store)
        try:
            test_root, allocated_git = build_service.allocate_result_dir(
                request.results_root
            )
        except BuildError as exc:
            raise ExecutionError(str(exc)) from exc

        self._begin_pipeline_progress("准备并校验数据集")
        prepared = self._prepare_datasets(request, contract)
        ordered_segments = _ordered_valid_segments(prepared.instances)
        total_segments = len(ordered_segments)
        eta_estimator = RunEtaEstimator(
            tuple(segment for _, segment in ordered_segments)
        )
        self._complete_dataset_progress(prepared)
        self._prepare_segment_progress(total_segments, completed=0)
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
            next_segment_index=0,
            dataset_results=(),
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
            self._finish_before_execution(
                checkpoint.failure_reason or "数据集预检失败",
                build_started=False,
            )
            return self._summarize(test_root, checkpoint)

        if not prepared.instances:
            checkpoint = replace(
                checkpoint,
                status="failed",
                updated_at=_utc_now(),
                failure_reason="no runnable datasets were selected",
            )
            self._save_checkpoint(test_root, checkpoint)
            self._finish_before_execution(
                checkpoint.failure_reason or "没有可运行数据集",
                build_started=False,
            )
            return self._summarize(test_root, checkpoint)

        self.progress.begin(
            MODULE_BUILD,
            detail=f"执行 {request.build_config.script_path.name}",
        )
        build_receipt = build_service.build(
            test_root,
            expected_commit=allocated_git.commit,
        )
        self.progress.finish(
            MODULE_BUILD,
            status=_progress_status(build_receipt.status),
            detail=f"耗时 {build_receipt.duration_seconds:.1f}s",
            complete=True,
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
            self._finish_before_execution(
                checkpoint.failure_reason or "算法构建失败",
                build_started=True,
            )
            return self._summarize(test_root, checkpoint)

        try:
            build_service.verify_runtime_context(build_receipt)
        except BuildError as exc:
            self.progress.finish(
                MODULE_BUILD,
                status="failed",
                detail=str(exc),
                complete=True,
            )
            checkpoint = replace(
                checkpoint,
                status="failed",
                updated_at=_utc_now(),
                failure_reason=str(exc),
            )
            self._save_checkpoint(test_root, checkpoint)
            self._finish_before_execution(str(exc), build_started=True)
            return self._summarize(test_root, checkpoint)

        checkpoint = replace(checkpoint, status="running", updated_at=_utc_now())
        self._save_checkpoint(test_root, checkpoint)
        self._begin_segment_progress(
            contract,
            total_segments,
            completed=0,
        )
        return self._execute(
            request,
            contract,
            prepared.instances,
            build_service,
            build_receipt,
            test_root,
            checkpoint,
            eta_estimator,
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
        self._validate_output_configuration(request, contract)
        self._verify_frozen_request(
            request,
            contract,
            checkpoint,
            frozen_algorithm,
            frozen_run,
        )

        self._begin_pipeline_progress("重新校验数据集")
        prepared = self._prepare_datasets(request, contract)
        self._complete_dataset_progress(prepared)
        self._verify_frozen_datasets(
            test_root,
            checkpoint,
            prepared,
            frozen_run,
        )

        self.progress.begin(MODULE_BUILD, detail="校验并复用已有构建")
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
            self.progress.finish(
                MODULE_BUILD,
                status="failed",
                detail=str(exc),
                complete=True,
            )
            raise ExecutionError(
                f"cannot resume because build context changed: {exc}"
            ) from exc
        self.progress.finish(
            MODULE_BUILD,
            status="success",
            detail="复用已有构建",
            complete=True,
        )

        current_dataset_id = checkpoint.dataset_order[checkpoint.next_dataset_index]
        run_indexes_by_dataset = _dataset_run_indexes(prepared.instances)
        checkpoint = self._remove_incomplete_attempt(
            checkpoint,
            current_dataset_id,
        )
        try:
            self.store.reset_segment_directories(
                test_root,
                run_indexes_by_dataset[current_dataset_id],
            )
        except RunStorageError as exc:
            raise ExecutionError(str(exc)) from exc
        if contract.evaluation_workflow is not None:
            self.progress.begin(MODULE_REPORT, detail="恢复前重建汇总")
            self._update_summary(test_root, contract)
            self.progress.wait(MODULE_REPORT, detail="等待评估结果")
        ordered_segments = _ordered_valid_segments(prepared.instances)
        total_segments = len(ordered_segments)
        eta_estimator = RunEtaEstimator(
            tuple(segment for _, segment in ordered_segments)
        )
        completed_segments = _completed_segment_count(checkpoint)
        self._prepare_segment_progress(
            total_segments,
            completed=completed_segments,
        )

        checkpoint = replace(
            checkpoint,
            status="running",
            updated_at=_utc_now(),
            failure_reason=None,
        )
        self._save_checkpoint(test_root, checkpoint)
        self._begin_segment_progress(
            contract,
            total_segments,
            completed=completed_segments,
        )
        return self._execute(
            request,
            contract,
            prepared.instances,
            build_service,
            build_receipt,
            test_root,
            checkpoint,
            eta_estimator,
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
        eta_estimator: RunEtaEstimator,
    ) -> RunSummary:
        instance_by_id = {item.dataset_id: item for item in instances}
        ordered_instances = tuple(
            instance_by_id[dataset_id] for dataset_id in checkpoint.dataset_order
        )
        run_indexes_by_dataset = _dataset_run_indexes(ordered_instances)
        output_parser = resolve_progress_parser(contract.progress_parser)
        total_segments = len(_ordered_valid_segments(ordered_instances))

        for index in range(checkpoint.next_dataset_index, len(ordered_instances)):
            instance = ordered_instances[index]
            run_indexes = run_indexes_by_dataset[instance.dataset_id]
            dataset_start_index = run_indexes[0]
            next_segment_index = run_indexes[-1] + 1
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
                    run_indexes,
                    eta_estimator,
                    output_parser,
                    total_segments,
                )
            except DatasetUnavailableError as exc:
                checkpoint = replace(
                    checkpoint,
                    status="paused",
                    next_dataset_index=index,
                    next_segment_index=dataset_start_index,
                    updated_at=_utc_now(),
                    failure_reason=(
                        "dataset unavailable during run; reconnect storage and "
                        f"resume this test: {exc}"
                    ),
                )
                self._save_checkpoint(test_root, checkpoint)
                self._finish_execution_progress(contract, checkpoint)
                return self._summarize(test_root, checkpoint)
            except (BuildError, RunnerError, RunStorageError) as exc:
                checkpoint = replace(
                    checkpoint,
                    status="failed",
                    next_dataset_index=index,
                    next_segment_index=dataset_start_index,
                    updated_at=_utc_now(),
                    failure_reason=str(exc),
                )
                self._save_checkpoint(test_root, checkpoint)
                self._finish_execution_progress(contract, checkpoint)
                return self._summarize(test_root, checkpoint)

            dataset_results = checkpoint.dataset_results + (dataset_receipt,)
            failure_count = (
                checkpoint.algorithm_failure_count
                + dataset_receipt.algorithm_failure_count
            )

            if dataset_receipt.status == "interrupted":
                checkpoint = replace(
                    checkpoint,
                    status="interrupted",
                    next_dataset_index=index,
                    next_segment_index=dataset_start_index,
                    dataset_results=dataset_results,
                    algorithm_failure_count=failure_count,
                    updated_at=_utc_now(),
                    failure_reason=dataset_receipt.failure_reason,
                )
                self._save_checkpoint(test_root, checkpoint)
                self._finish_execution_progress(contract, checkpoint)
                return self._summarize(test_root, checkpoint)

            if (
                dataset_receipt.status == "failed"
                and request.failure_policy == FAILURE_POLICY_FAIL_FAST
            ):
                checkpoint = replace(
                    checkpoint,
                    status="failed",
                    next_dataset_index=index,
                    next_segment_index=dataset_start_index,
                    dataset_results=dataset_results,
                    algorithm_failure_count=failure_count,
                    updated_at=_utc_now(),
                    failure_reason=dataset_receipt.failure_reason,
                )
                self._save_checkpoint(test_root, checkpoint)
                self._finish_execution_progress(contract, checkpoint)
                return self._summarize(test_root, checkpoint)

            checkpoint = replace(
                checkpoint,
                status="running",
                next_dataset_index=index + 1,
                next_segment_index=next_segment_index,
                dataset_results=dataset_results,
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
        self._finish_execution_progress(contract, checkpoint)
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
        run_indexes: Tuple[int, ...],
        eta_estimator: RunEtaEstimator,
        output_parser: Optional[ProgressParser],
        total_segments: int,
    ) -> DatasetRunReceipt:
        valid_segments = tuple(
            sorted(
                (item for item in instance.segments if item.valid),
                key=lambda item: item.sequence_no,
            )
        )
        if len(run_indexes) != len(valid_segments):
            raise RunStorageError(
                f"Segment index plan changed for dataset {instance.dataset_id}"
            )
        successful: List[str] = []
        failed: List[str] = []
        not_run: List[str] = []
        algorithm_failure_count = 0
        failure_reason: Optional[str] = None
        dataset_status = "success"

        for segment_index, segment in enumerate(valid_segments):
            run_index = run_indexes[segment_index]
            progress_detail = _segment_progress_detail(instance, segment)
            self.progress.describe(MODULE_RUN, progress_detail)
            entrypoint = build_service.verify_runtime_context(build_receipt)
            try:
                command = build_run_command(
                    entrypoint,
                    contract,
                    instance,
                    segment,
                    command_template=request.build_config.command_template,
                )
            except DatasetUnavailableError:
                raise
            except CommandError as exc:
                failed.append(segment.segment_id)
                not_run.extend(
                    item.segment_id for item in valid_segments[segment_index + 1 :]
                )
                failure_reason = str(exc)
                dataset_status = "failed"
                self.progress.describe(
                    MODULE_RUN,
                    f"{progress_detail}：命令生成失败",
                )
                break

            numbered_before: Optional[NumberedOutputSnapshot] = None
            counter_relative_path = contract.numbered_output_counter_relative_path
            if counter_relative_path is not None:
                assert request.build_config.output_root_path is not None
                numbered_before = read_numbered_output_snapshot(
                    request.build_config.output_root_path,
                    counter_relative_path,
                    allow_uninitialized=True,
                )
                output_sources: Tuple[Path, ...] = ()
            else:
                output_sources = tuple(
                    resolve_fixed_output(
                        request.build_config.algorithm_path,
                        relative_path,
                    )
                    for relative_path in contract.output_relative_paths
                )
                for output_source in output_sources:
                    prepare_fixed_output(output_source)

            paths = self.store.segment_paths(
                test_root,
                run_index,
            )
            initial_estimate = eta_estimator.start_segment(run_index)
            self._publish_runtime_estimate(
                progress_detail,
                run_index,
                total_segments,
                initial_estimate,
            )

            def observe_output(stream_name: str, line: str) -> None:
                del stream_name
                if output_parser is None:
                    return
                sample = output_parser(line)
                if sample is None:
                    return
                estimate = eta_estimator.observe(sample)
                if estimate is not None:
                    self._publish_runtime_estimate(
                        progress_detail,
                        run_index,
                        total_segments,
                        estimate,
                    )

            process = run_process(
                command,
                request.build_config.algorithm_path,
                paths.stdout_path,
                paths.stderr_path,
                request.timeout_seconds,
                observe_output,
            )
            remaining_estimate = eta_estimator.finish_segment(
                successful=process.status == "success",
                duration_seconds=process.duration_seconds,
            )
            self._publish_runtime_estimate(
                progress_detail,
                run_index,
                total_segments,
                remaining_estimate,
            )
            try:
                verify_run_inputs_available(instance, command)
            except DatasetUnavailableError as exc:
                self._save_paused_segment_attempt(
                    request,
                    contract,
                    instance,
                    segment,
                    run_index,
                    entrypoint,
                    command,
                    process,
                    paths,
                    output_sources,
                    test_root,
                    str(exc),
                )
                raise

            numbered_after: Optional[NumberedOutputSnapshot] = None
            numbered_output_directory: Optional[Path] = None
            if numbered_before is not None and process.status == "success":
                assert counter_relative_path is not None
                try:
                    (
                        numbered_after,
                        numbered_output_directory,
                        output_sources,
                    ) = resolve_numbered_output_sources(
                        numbered_before,
                        counter_relative_path,
                        contract.output_relative_paths,
                    )
                except RunnerError as exc:
                    output_checks = {
                        counter_relative_path.name: {
                            "validator": "numbered_output_counter",
                            "accepted": False,
                            "validation_error": str(exc),
                        }
                    }
                    output_error: Optional[str] = str(exc)
                else:
                    output_checks, output_error = _validate_output_sources(
                        output_sources,
                        contract,
                        instance,
                        segment,
                        command,
                        accept=True,
                    )
                    output_checks[counter_relative_path.name] = {
                        "validator": "numbered_output_counter",
                        "exists": True,
                        "format_valid": True,
                        "counter_before": numbered_before.last_completed,
                        "counter_after": numbered_after.last_completed,
                        "source_directory": str(numbered_output_directory),
                        "accepted": True,
                    }
            else:
                output_checks, output_error = _validate_output_sources(
                    output_sources,
                    contract,
                    instance,
                    segment,
                    command,
                    accept=process.status == "success",
                )
            output_results: List[Path] = []
            segment_status = process.status
            segment_failure = process.failure_reason
            algorithm_failure = process.status in {"failed", "timeout"}

            if process.status == "success":
                if output_error is not None:
                    segment_status = "failed"
                    segment_failure = output_error
                    algorithm_failure = True
                else:
                    try:
                        for (
                            output_source,
                            relative_path,
                        ) in zip(
                            output_sources,
                            contract.output_relative_paths,
                        ):
                            output_results.append(
                                self.store.copy_fixed_output(
                                    output_source,
                                    paths.segment_dir,
                                    relative_path,
                                )
                            )
                        if numbered_after is not None:
                            self.store.copy_result_file(
                                numbered_after.counter_path,
                                paths.segment_dir,
                                Path(counter_relative_path.name),
                            )
                        self._copy_evaluation_support_files(
                            contract,
                            instance,
                            paths.segment_dir,
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
                            run_index,
                            entrypoint,
                            command.argv,
                            process,
                            paths.stdout_path,
                            paths.stderr_path,
                            output_sources,
                            tuple(output_results),
                            output_checks,
                            segment_status,
                            algorithm_failure,
                            segment_failure,
                        )
                        self.store.save_segment_receipt(paths, receipt)
                        _debug_run_output(paths, receipt)
                        self.progress.advance(
                            MODULE_RUN,
                            detail=f"{progress_detail}：{segment_status}",
                        )
                        raise

            receipt = self._segment_receipt(
                test_root,
                request,
                contract,
                instance,
                segment,
                run_index,
                entrypoint,
                command.argv,
                process,
                paths.stdout_path,
                paths.stderr_path,
                output_sources,
                tuple(output_results),
                output_checks,
                segment_status,
                algorithm_failure,
                segment_failure,
            )
            self.store.save_segment_receipt(paths, receipt)
            _debug_run_output(paths, receipt)
            self.progress.advance(
                MODULE_RUN,
                detail=f"{progress_detail}：{segment_status}",
            )
            evaluation_status, evaluation_failure = self._evaluate_and_update_summary(
                request,
                contract,
                instance,
                segment,
                paths,
                receipt,
                test_root,
            )
            if evaluation_status == "interrupted":
                successful.append(segment.segment_id)
                not_run.extend(
                    item.segment_id for item in valid_segments[segment_index + 1 :]
                )
                failure_reason = evaluation_failure
                dataset_status = "interrupted"
                break
            build_service.verify_runtime_context(build_receipt)

            if segment_status == "success":
                successful.append(segment.segment_id)
                continue

            failed.append(segment.segment_id)
            failure_reason = segment_failure
            if algorithm_failure:
                algorithm_failure_count += 1
            dataset_status = (
                "interrupted" if segment_status == "interrupted" else "failed"
            )
            if (
                segment_status == "interrupted"
                or request.failure_policy == FAILURE_POLICY_FAIL_FAST
            ):
                not_run.extend(
                    item.segment_id for item in valid_segments[segment_index + 1 :]
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

    def _publish_runtime_estimate(
        self,
        progress_detail: str,
        run_index: int,
        total_segments: int,
        estimate: RuntimeEstimate,
    ) -> None:
        percent = min(100.0, max(0.0, estimate.fraction * 100.0))
        self.progress.estimate(
            MODULE_RUN,
            eta_seconds=estimate.eta_seconds,
            detail=(
                f"{progress_detail}：当前 {percent:.0f}%，"
                f"处理速度 {estimate.data_rate:.2f}×"
            ),
        )
        self.progress.estimate(
            MODULE_TOTAL,
            eta_seconds=estimate.eta_seconds,
            detail=(f"Segment {run_index + 1}/{total_segments}：当前 {percent:.0f}%"),
        )

    def _evaluate_and_update_summary(
        self,
        request: RunRequest,
        contract: AlgorithmContract,
        instance: DatasetInstance,
        segment: Segment,
        paths: SegmentPaths,
        run_receipt: SegmentRunReceipt,
        test_root: Path,
    ) -> Tuple[Optional[str], Optional[str]]:
        workflow = contract.evaluation_workflow
        progress_detail = _segment_progress_detail(instance, segment)
        if workflow is None:
            self.progress.advance(
                MODULE_TOTAL,
                detail=f"{progress_detail}：结果已保存",
            )
            return None, None
        evaluation_status = "not_run"
        evaluation_failure: Optional[str] = None
        if run_receipt.status == "success":
            self.progress.begin(MODULE_EVALUATE, detail=progress_detail)
            try:
                evaluation_receipt = self.evaluation_service.evaluate(
                    EvaluationRequest(
                        test_id=test_root.name,
                        algorithm_id=contract.algorithm_id,
                        run_index=run_receipt.run_index,
                        dataset_id=instance.dataset_id,
                        dataset_type=instance.dataset_type,
                        segment_id=segment.segment_id,
                        workflow=workflow,
                        data_dir=instance.root_path,
                        log_dir=paths.segment_dir,
                        evaluation_dir=paths.evaluation_dir,
                        rpe_delta_value=request.rpe_delta_value,
                        rpe_delta_unit=request.rpe_delta_unit,
                    )
                )
            except EvaluationError as exc:
                self.progress.describe(
                    MODULE_EVALUATE,
                    f"{progress_detail}：评估保存失败",
                )
                raise RunStorageError(str(exc)) from exc
            evaluation_status = evaluation_receipt.status
            evaluation_failure = evaluation_receipt.failure_reason
            evaluation_detail = f"{progress_detail}：{evaluation_status}"
        else:
            evaluation_detail = f"{progress_detail}：跳过"
        self.progress.advance(
            MODULE_EVALUATE,
            detail=evaluation_detail,
        )
        self.progress.wait(
            MODULE_EVALUATE,
            detail=f"等待下一段运行结果；{evaluation_detail}",
        )
        self.progress.begin(MODULE_REPORT, detail=progress_detail)
        self._update_summary(test_root, contract)
        self.progress.advance(
            MODULE_REPORT,
            detail=f"{progress_detail}：已更新",
        )
        self.progress.wait(
            MODULE_REPORT,
            detail=f"等待下一次汇总；{progress_detail}：已更新",
        )
        self.progress.advance(
            MODULE_TOTAL,
            detail=f"{progress_detail}：全部保存",
        )
        return evaluation_status, evaluation_failure

    def _update_summary(
        self,
        test_root: Path,
        contract: AlgorithmContract,
    ) -> None:
        workflow = contract.evaluation_workflow
        if workflow is None:
            return
        try:
            self.summary_writer.update(test_root, workflow)
        except EvaluationError as exc:
            raise RunStorageError(str(exc)) from exc

    def _begin_pipeline_progress(self, dataset_detail: str) -> None:
        self.progress.begin(
            MODULE_TOTAL,
            detail="准备本次测试",
        )
        self.progress.begin(
            MODULE_DATASET,
            detail=dataset_detail,
        )
        self.progress.prepare(
            MODULE_BUILD,
            total=1,
            detail="等待数据集检查",
        )
        for module in (MODULE_RUN, MODULE_EVALUATE, MODULE_REPORT):
            self.progress.prepare(
                module,
                total=0,
                detail="等待",
            )

    def _complete_dataset_progress(self, prepared: _PreparedDatasets) -> None:
        dataset_count = len(prepared.instances)
        issue_count = len(prepared.issues)
        handled_count = max(1, dataset_count + issue_count)
        self.progress.prepare(
            MODULE_DATASET,
            total=handled_count,
            completed=handled_count,
            detail=f"可运行 {dataset_count}，异常 {issue_count}",
        )
        if issue_count and not dataset_count:
            status = "failed"
        elif issue_count:
            status = "warning"
        else:
            status = "success"
        self.progress.finish(
            MODULE_DATASET,
            status=status,
            detail=f"可运行 {dataset_count}，异常 {issue_count}",
        )

    def _prepare_segment_progress(
        self,
        total: int,
        *,
        completed: int,
    ) -> None:
        display_total = max(1, total)
        display_completed = min(max(0, completed), display_total)
        self.progress.begin(
            MODULE_TOTAL,
            total=display_total,
            completed=display_completed,
            detail=f"{completed}/{total} 个 Segment 已完成",
        )
        for module in (MODULE_RUN, MODULE_EVALUATE, MODULE_REPORT):
            self.progress.prepare(
                module,
                total=display_total,
                completed=display_completed,
                detail=f"{completed}/{total} 个 Segment 已处理",
            )

    def _begin_segment_progress(
        self,
        contract: AlgorithmContract,
        total: int,
        *,
        completed: int,
    ) -> None:
        display_total = max(1, total)
        display_completed = min(max(0, completed), display_total)
        self.progress.begin(
            MODULE_RUN,
            total=display_total,
            completed=display_completed,
            detail="等待下一个 Segment",
        )
        if contract.evaluation_workflow is None:
            for module in (MODULE_EVALUATE, MODULE_REPORT):
                self.progress.finish(
                    module,
                    status="skipped",
                    detail="算法未配置评估流程",
                    complete=True,
                )
            return
        self.progress.prepare(
            MODULE_EVALUATE,
            total=display_total,
            completed=display_completed,
            detail="等待运行结果",
        )
        self.progress.prepare(
            MODULE_REPORT,
            total=display_total,
            completed=display_completed,
            detail="等待评估结果",
        )

    def _finish_before_execution(
        self,
        reason: str,
        *,
        build_started: bool,
    ) -> None:
        if not build_started:
            self.progress.finish(
                MODULE_BUILD,
                status="skipped",
                detail="未执行",
                complete=True,
            )
        for module in (MODULE_RUN, MODULE_EVALUATE, MODULE_REPORT):
            self.progress.finish(
                module,
                status="skipped",
                detail="未执行",
            )
        self.progress.finish(
            MODULE_TOTAL,
            status="failed",
            detail=reason,
        )

    def _finish_execution_progress(
        self,
        contract: AlgorithmContract,
        checkpoint: RunCheckpoint,
    ) -> None:
        completed = _completed_segment_count(checkpoint)
        not_run = _not_run_segment_count(checkpoint)
        processed_all_datasets = checkpoint.next_dataset_index >= len(
            checkpoint.dataset_order
        )

        if checkpoint.status == "paused":
            module_status = "paused"
        elif checkpoint.status == "interrupted":
            module_status = "interrupted"
        elif not processed_all_datasets:
            module_status = "failed"
        elif checkpoint.algorithm_failure_count or not_run:
            module_status = "warning"
        else:
            module_status = "success"

        self.progress.finish(
            MODULE_RUN,
            status=module_status,
            detail=(
                f"已处理 {completed}，算法失败 "
                f"{checkpoint.algorithm_failure_count}，未运行 {not_run}"
            ),
        )
        if contract.evaluation_workflow is not None:
            self.progress.finish(
                MODULE_EVALUATE,
                status=module_status,
                detail=f"随运行处理 {completed} 个 Segment",
            )
            self.progress.finish(
                MODULE_REPORT,
                status=module_status,
                detail=f"已汇总 {completed} 个 Segment",
            )

        total_status = _progress_status(checkpoint.status)
        if total_status == "success" and module_status == "warning":
            total_status = "warning"
        self.progress.finish(
            MODULE_TOTAL,
            status=total_status,
            detail=(
                f"完成 {completed}，失败 "
                f"{checkpoint.algorithm_failure_count}，未运行 {not_run}"
            ),
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

    def _save_paused_segment_attempt(
        self,
        request: RunRequest,
        contract: AlgorithmContract,
        instance: DatasetInstance,
        segment: Segment,
        run_index: int,
        entrypoint: Path,
        command: ResolvedRunCommand,
        process: ProcessResult,
        paths: SegmentPaths,
        output_sources: Tuple[Path, ...],
        test_root: Path,
        failure_reason: str,
    ) -> None:
        output_checks = {
            str(relative_path): {
                "accepted": False,
                "validation_error": (
                    "output was not accepted because dataset storage became "
                    f"unavailable: {failure_reason}"
                ),
            }
            for relative_path in contract.output_relative_paths
        }
        receipt = self._segment_receipt(
            test_root,
            request,
            contract,
            instance,
            segment,
            run_index,
            entrypoint,
            command.argv,
            process,
            paths.stdout_path,
            paths.stderr_path,
            output_sources,
            (),
            output_checks,
            "paused",
            False,
            failure_reason,
        )
        self.store.save_segment_receipt(paths, receipt)
        _debug_run_output(paths, receipt)
        self.progress.describe(
            MODULE_RUN,
            f"{_segment_progress_detail(instance, segment)}：数据不可用，已暂停",
        )
        if contract.evaluation_workflow is not None:
            self._update_summary(test_root, contract)

    @staticmethod
    def _segment_receipt(
        test_root: Path,
        request: RunRequest,
        contract: AlgorithmContract,
        instance: DatasetInstance,
        segment: Segment,
        run_index: int,
        entrypoint: Path,
        command: Tuple[str, ...],
        process: ProcessResult,
        stdout_path: Path,
        stderr_path: Path,
        output_sources: Tuple[Path, ...],
        output_results: Tuple[Path, ...],
        output_checks: Dict[str, Any],
        status: str,
        algorithm_failure: bool,
        failure_reason: Optional[str],
    ) -> SegmentRunReceipt:
        return SegmentRunReceipt(
            test_id=test_root.name,
            algorithm_id=contract.algorithm_id,
            contract_version=contract.contract_version,
            run_index=run_index,
            dataset_id=instance.dataset_id,
            dataset_type=instance.dataset_type,
            segment_id=segment.segment_id,
            segment_start_timestamp=segment.start_timestamp,
            segment_end_timestamp=segment.end_timestamp,
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
            output_source_paths=output_sources,
            output_result_paths=output_results,
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
            "schema_version": RUN_CONFIG_SCHEMA_VERSION,
            "algorithm": request.build_config.algorithm_id,
            "build": {
                "algorithm_path": str(request.build_config.algorithm_path),
                "script_path": str(request.build_config.script_path),
            },
            "contract": contract.to_dict(),
        }
        expected_run: Dict[str, object] = {}
        if request.build_config.command_template is not None:
            expected_run["command_template"] = list(
                request.build_config.command_template
            )
        if request.build_config.output_root_path is not None:
            expected_run["output_root_path"] = str(
                request.build_config.output_root_path
            )
        if expected_run:
            expected_algorithm["run"] = expected_run
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
        raw_evaluation = frozen_run.get("evaluation")
        if raw_evaluation is None:
            frozen_delta_value = DEFAULT_RPE_DELTA_VALUE
            frozen_delta_unit = DEFAULT_RPE_DELTA_UNIT
        elif isinstance(raw_evaluation, dict):
            try:
                frozen_delta_value, frozen_delta_unit = normalize_rpe_delta(
                    raw_evaluation["rpe_delta_value"],
                    raw_evaluation["rpe_delta_unit"],
                )
            except (KeyError, ValueError) as exc:
                raise ExecutionError(
                    f"frozen run RPE delta configuration is invalid: {exc}"
                ) from exc
        else:
            raise ExecutionError("frozen run evaluation configuration is invalid")
        try:
            current_delta_value, current_delta_unit = normalize_rpe_delta(
                request.rpe_delta_value,
                request.rpe_delta_unit,
            )
        except ValueError as exc:
            raise ExecutionError(str(exc)) from exc
        if (
            frozen_delta_value != current_delta_value
            or frozen_delta_unit != current_delta_unit
        ):
            raise ExecutionError("RPE delta changed; start a new run")

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
        current_segment_order = [
            {
                "run_index": run_index,
                "dataset_id": instance.dataset_id,
                "segment_id": segment.segment_id,
                "start_timestamp": segment.start_timestamp,
                "end_timestamp": segment.end_timestamp,
            }
            for run_index, (instance, segment) in enumerate(
                _ordered_valid_segments(prepared.instances)
            )
        ]
        if current_segment_order != frozen_run.get("segment_order"):
            raise ExecutionError("Segment order changed; start a new run")
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
        checkpoint: RunCheckpoint,
        dataset_id: str,
    ) -> RunCheckpoint:
        incomplete_results = tuple(
            item for item in checkpoint.dataset_results if item.dataset_id == dataset_id
        )
        if not incomplete_results:
            return checkpoint
        previous_failures = sum(
            item.algorithm_failure_count for item in incomplete_results
        )
        return replace(
            checkpoint,
            dataset_results=tuple(
                item
                for item in checkpoint.dataset_results
                if item.dataset_id != dataset_id
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

        for result in checkpoint.dataset_results:
            if result.status == "success":
                successful_datasets += 1
            else:
                failed_datasets += 1
            successful_segments += len(result.successful_segment_ids)
            failed_segments += len(result.failed_segment_ids)

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
        try:
            normalize_rpe_delta(
                request.rpe_delta_value,
                request.rpe_delta_unit,
            )
        except ValueError as exc:
            raise ExecutionError(str(exc)) from exc
        if not request.dataset_configs:
            raise ExecutionError("at least one dataset config is required")

    @staticmethod
    def _validate_output_configuration(
        request: RunRequest,
        contract: AlgorithmContract,
    ) -> None:
        counter_path = contract.numbered_output_counter_relative_path
        output_root = request.build_config.output_root_path
        if counter_path is not None and output_root is None:
            raise ExecutionError(
                f"run.output_root_path is required for {contract.algorithm_id}"
            )
        if counter_path is None and output_root is not None:
            raise ExecutionError(
                f"{contract.algorithm_id} does not use a numbered external output"
            )


def _progress_status(status: str) -> str:
    if status == "success":
        return "success"
    if status == "paused":
        return "paused"
    if status == "interrupted":
        return "interrupted"
    return "failed"


def _completed_segment_count(checkpoint: RunCheckpoint) -> int:
    return sum(
        len(item.successful_segment_ids) + len(item.failed_segment_ids)
        for item in checkpoint.dataset_results
    )


def _not_run_segment_count(checkpoint: RunCheckpoint) -> int:
    return sum(len(item.not_run_segment_ids) for item in checkpoint.dataset_results)


def _segment_progress_detail(
    instance: DatasetInstance,
    segment: Segment,
) -> str:
    return f"{instance.dataset_id} / Segment {segment.sequence_no}"


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


def _ordered_valid_segments(
    instances: Sequence[DatasetInstance],
) -> Tuple[Tuple[DatasetInstance, Segment], ...]:
    return tuple(
        (instance, segment)
        for instance in instances
        for segment in sorted(
            (item for item in instance.segments if item.valid),
            key=lambda item: item.sequence_no,
        )
    )


def _dataset_run_indexes(
    instances: Sequence[DatasetInstance],
) -> Dict[str, Tuple[int, ...]]:
    indexes: Dict[str, List[int]] = {}
    for run_index, (instance, _) in enumerate(_ordered_valid_segments(instances)):
        indexes.setdefault(instance.dataset_id, []).append(run_index)
    return {
        dataset_id: tuple(dataset_indexes)
        for dataset_id, dataset_indexes in indexes.items()
    }


def _validate_output_sources(
    output_sources: Tuple[Path, ...],
    contract: AlgorithmContract,
    instance: DatasetInstance,
    segment: Segment,
    command: ResolvedRunCommand,
    *,
    accept: bool,
) -> Tuple[Dict[str, Any], Optional[str]]:
    checks_by_path: Dict[str, Any] = {}
    first_error: Optional[str] = None
    for output_index, (relative_path, output_source) in enumerate(
        zip(contract.output_relative_paths, output_sources)
    ):
        if output_index == 0:
            checks, error = validate_fixed_output(
                output_source,
                contract,
                instance,
                segment,
                command,
            )
        else:
            checks, error = validate_additional_output(output_source)
        checks["accepted"] = accept and error is None
        if error is not None:
            checks["validation_error"] = error
            if first_error is None:
                first_error = error
        checks_by_path[relative_path.as_posix()] = checks
    return checks_by_path, first_error


def _debug_run_output(
    paths: SegmentPaths,
    receipt: SegmentRunReceipt,
) -> None:
    if not debug_enabled():
        return
    saved = sorted(str(path) for path in paths.segment_dir.rglob("*") if path.is_file())
    debug_output(
        "RUN",
        status=receipt.status,
        exit_code=receipt.exit_code,
        failure_reason=receipt.failure_reason,
        saved=saved,
    )


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
