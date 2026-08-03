"""Re-evaluation service for existing test results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from ..algorithms.contracts import (
    AlgorithmContract,
    EvaluationWorkflowConfig,
    get_algorithm_contract,
)
from ..datasets.models import DatasetInstance, Segment
from ..debug import debug_output
from ..progress import (
    MODULE_EVALUATE,
    MODULE_REPORT,
    MODULE_TOTAL,
    NullProgressReporter,
    ProgressReporter,
)
from .models import EvaluationRequest
from .service import EvaluationError, EvaluationService
from .workbook import SummaryWorkbookWriter


class ReevaluationError(Exception):
    """Re-evaluation cannot be started or completed."""


@dataclass(frozen=True)
class ReevaluationRequest:
    test_dir: Path
    rpe_delta_value: Optional[float] = None
    rpe_delta_unit: Optional[str] = None


@dataclass(frozen=True)
class ReevaluationSummary:
    test_id: str
    test_dir: Path
    total_segments: int
    successful_segments: int
    failed_segments: int
    skipped_segments: int
    report_path: Path
    status: str
    failure_reason: Optional[str] = None


class ReevaluationService:
    def __init__(
        self,
        evaluation_service: Optional[EvaluationService] = None,
        summary_writer: Optional[SummaryWorkbookWriter] = None,
        progress: Optional[ProgressReporter] = None,
    ):
        self.evaluation_service = evaluation_service or EvaluationService()
        self.summary_writer = summary_writer or SummaryWorkbookWriter()
        self.progress = progress or NullProgressReporter()

    def evaluate(self, request: ReevaluationRequest) -> ReevaluationSummary:
        test_dir = request.test_dir.expanduser().resolve()
        if not test_dir.is_dir():
            raise ReevaluationError(f"test directory does not exist: {test_dir}")

        from ..execution.storage import RunStore

        store = RunStore()

        try:
            frozen_algorithm = store.load_mapping(test_dir / "config" / "algorithm.yaml")
            frozen_run = store.load_mapping(test_dir / "config" / "run.yaml")
        except Exception as exc:
            raise ReevaluationError(f"cannot load frozen configuration: {exc}") from exc

        algorithm_id = frozen_algorithm.get("algorithm")
        if not isinstance(algorithm_id, str):
            raise ReevaluationError("frozen algorithm configuration has no algorithm ID")

        try:
            contract = get_algorithm_contract(algorithm_id)
        except ValueError as exc:
            raise ReevaluationError(str(exc)) from exc

        workflows = _get_workflows(contract)
        if not workflows:
            raise ReevaluationError("algorithm has no evaluation workflows configured")

        rpe_delta_value, rpe_delta_unit = _get_rpe_delta(
            frozen_run, request.rpe_delta_value, request.rpe_delta_unit
        )

        segment_order = frozen_run.get("segment_order")
        if not isinstance(segment_order, list):
            raise ReevaluationError("frozen run configuration has no segment_order")

        dataset_order = frozen_run.get("dataset_order")
        if not isinstance(dataset_order, list):
            raise ReevaluationError("frozen run configuration has no dataset_order")

        dataset_paths = {
            item["dataset_id"]: Path(item["root_path"])
            for item in dataset_order
            if isinstance(item, dict) and "dataset_id" in item and "root_path" in item
        }

        self.progress.begin(MODULE_TOTAL, total=len(segment_order), detail="重新评估")
        self.progress.begin(MODULE_EVALUATE, total=len(segment_order), detail="等待评估")

        successful = 0
        failed = 0
        skipped = 0
        failure_reason: Optional[str] = None

        for segment_info in segment_order:
            if not isinstance(segment_info, dict):
                raise ReevaluationError("segment_order contains an invalid item")

            run_index = segment_info.get("run_index")
            dataset_id = segment_info.get("dataset_id")
            segment_id = segment_info.get("segment_id")

            if run_index is None or dataset_id is None or segment_id is None:
                raise ReevaluationError("segment_order item is missing required fields")

            dataset_path = dataset_paths.get(dataset_id)
            if dataset_path is None:
                raise ReevaluationError(
                    f"segment references unknown dataset: {dataset_id}"
                )

            segment_dir = test_dir / "dataset" / str(run_index)
            receipt_path = segment_dir / "receipt.yaml"

            if not receipt_path.is_file():
                skipped += 1
                self.progress.advance(
                    MODULE_EVALUATE, detail=f"Segment {run_index}：跳过（无运行结果）"
                )
                self.progress.advance(
                    MODULE_TOTAL, detail=f"Segment {run_index}：跳过"
                )
                continue

            try:
                run_receipt = store.load_mapping(receipt_path)
            except Exception as exc:
                failed += 1
                failure_reason = f"cannot load receipt for segment {run_index}: {exc}"
                self.progress.advance(
                    MODULE_EVALUATE, detail=f"Segment {run_index}：失败"
                )
                self.progress.advance(
                    MODULE_TOTAL, detail=f"Segment {run_index}：失败"
                )
                continue

            if run_receipt.get("status") != "success":
                skipped += 1
                self.progress.advance(
                    MODULE_EVALUATE, detail=f"Segment {run_index}：跳过（运行未成功）"
                )
                self.progress.advance(
                    MODULE_TOTAL, detail=f"Segment {run_index}：跳过"
                )
                continue

            output_source_paths = run_receipt.get("output_source_paths", [])
            if not output_source_paths:
                failed += 1
                failure_reason = (
                    f"segment {run_index} has no output_source_paths in receipt"
                )
                self.progress.advance(
                    MODULE_EVALUATE, detail=f"Segment {run_index}：失败"
                )
                continue

            algorithm_output_dir = Path(output_source_paths[0]).parent

            if not algorithm_output_dir.is_dir():
                failed += 1
                failure_reason = (
                    f"segment {run_index} has no valid algorithm output directory"
                )
                self.progress.advance(
                    MODULE_EVALUATE, detail=f"Segment {run_index}：失败"
                )
                self.progress.advance(
                    MODULE_TOTAL, detail=f"Segment {run_index}：失败"
                )
                continue

            instance = DatasetInstance(
                dataset_id=dataset_id,
                dataset_type=run_receipt.get("dataset_type", ""),
                root_path=dataset_path,
                handler_version=run_receipt.get("contract_version", 0),
                input_paths={},
                segments=(),
            )

            segment = Segment(
                segment_id=segment_id,
                sequence_no=run_index,
                start_timestamp=run_receipt.get("segment_start_timestamp", 0.0),
                end_timestamp=run_receipt.get("segment_end_timestamp", 0.0),
                duration_seconds=0.0,
                frame_count=0,
                valid=True,
            )

            progress_detail = f"{dataset_id} / Segment {segment.sequence_no}"
            self.progress.describe(MODULE_EVALUATE, detail=progress_detail)

            segment_failed = False
            for wf_config in workflows:
                eval_subdir = segment_dir / "evaluation" / wf_config.directory_name
                try:
                    evaluation_receipt = self.evaluation_service.evaluate(
                        EvaluationRequest(
                            test_id=test_dir.name,
                            algorithm_id=algorithm_id,
                            run_index=run_index,
                            dataset_id=dataset_id,
                            dataset_type=instance.dataset_type,
                            segment_id=segment_id,
                            workflow=wf_config.workflow,
                            data_dir=dataset_path,
                            log_dir=algorithm_output_dir,
                            evaluation_dir=eval_subdir,
                            rpe_delta_value=rpe_delta_value,
                            rpe_delta_unit=rpe_delta_unit,
                            vo_filename=wf_config.vo_filename,
                        )
                    )
                    if evaluation_receipt.status != "success":
                        segment_failed = True
                        failure_reason = evaluation_receipt.failure_reason
                except EvaluationError as exc:
                    segment_failed = True
                    failure_reason = str(exc)

            if segment_failed:
                failed += 1
                self.progress.advance(MODULE_EVALUATE, detail=f"{progress_detail}：失败")
            else:
                successful += 1
                self.progress.advance(
                    MODULE_EVALUATE, detail=f"{progress_detail}：成功"
                )
            self.progress.advance(
                MODULE_TOTAL,
                detail=f"{progress_detail}：{'失败' if segment_failed else '成功'}",
            )

        self.progress.finish(
            MODULE_EVALUATE,
            status="success" if failed == 0 else "failed",
            detail=f"成功 {successful}，失败 {failed}，跳过 {skipped}",
            complete=True,
        )

        self.progress.begin(MODULE_REPORT, detail="生成报告")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_filename = f"run_summary_{timestamp}.xlsx"
        report_path = test_dir / report_filename

        try:
            self.summary_writer.update(test_dir, workflows)
            default_report = test_dir / "run_summary.xlsx"
            if default_report.exists() and default_report != report_path:
                import shutil

                shutil.copy2(default_report, report_path)
            self.progress.finish(
                MODULE_REPORT,
                status="success",
                detail=str(report_path),
                complete=True,
            )
        except EvaluationError as exc:
            failure_reason = f"cannot generate report: {exc}"
            self.progress.finish(
                MODULE_REPORT, status="failed", detail=str(exc), complete=True
            )

        status = "success" if failed == 0 else "failed"
        self.progress.finish(
            MODULE_TOTAL,
            status=status,
            detail=f"完成 {successful}，失败 {failed}，跳过 {skipped}",
            complete=True,
        )

        debug_output(
            "REEVAL",
            test_dir=str(test_dir),
            successful=successful,
            failed=failed,
            skipped=skipped,
            report=str(report_path),
        )

        return ReevaluationSummary(
            test_id=test_dir.name,
            test_dir=test_dir,
            total_segments=len(segment_order),
            successful_segments=successful,
            failed_segments=failed,
            skipped_segments=skipped,
            report_path=report_path,
            status=status,
            failure_reason=failure_reason,
        )


def _get_workflows(contract: AlgorithmContract) -> Tuple[EvaluationWorkflowConfig, ...]:
    if contract.evaluation_workflows:
        return contract.evaluation_workflows
    if contract.evaluation_workflow is not None:
        return (
            EvaluationWorkflowConfig(
                workflow=contract.evaluation_workflow, vo_filename=None
            ),
        )
    return ()


def _get_rpe_delta(
    frozen_run: dict,
    override_value: Optional[float],
    override_unit: Optional[str],
) -> Tuple[float, str]:
    if override_value is not None and override_unit is not None:
        return override_value, override_unit

    evaluation = frozen_run.get("evaluation", {})
    if isinstance(evaluation, dict):
        value = evaluation.get("rpe_delta_value", 100.0)
        unit = evaluation.get("rpe_delta_unit", "m")
        return float(value), str(unit)

    return 100.0, "m"
