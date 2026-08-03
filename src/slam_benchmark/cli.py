"""Command-line interface for the benchmark pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .compilation.service import BuildError, BuildService
from .config import load_build_config, load_dataset_config
from .datasets.errors import DatasetError
from .datasets.models import ScanReport
from .datasets.service import DatasetManager
from .debug import debug_mode
from .execution.models import (
    FAILURE_POLICY_CONTINUE,
    FAILURE_POLICY_FAIL_FAST,
    RunRequest,
)
from .execution.service import ExecutionError, ExecutionService
from .evaluation import (
    DEFAULT_RPE_DELTA_UNIT,
    DEFAULT_RPE_DELTA_VALUE,
    RPE_DELTA_UNITS,
    EvaluationError,
    ReevaluationError,
    ReevaluationRequest,
    ReevaluationService,
    SummaryWorkbookWriter,
)
from .progress import (
    MODULE_BUILD,
    MODULE_DATASET,
    MODULE_EVALUATE,
    MODULE_REPORT,
    MODULE_TOTAL,
    PIPELINE_MODULES,
    ProgressReporter,
    TerminalProgress,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark", description="SLAM algorithm benchmark pipeline"
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--debug",
        action="store_true",
        help="print concise dataset, build, and run inputs and outputs",
    )
    modules = parser.add_subparsers(dest="module", required=True)

    dataset = modules.add_parser("dataset", help="manage local datasets")
    commands = dataset.add_subparsers(dest="dataset_command", required=True)

    scan = commands.add_parser("scan", help="discover and register datasets")
    _add_config_argument(scan)
    scan.add_argument(
        "--refresh",
        action="store_true",
        help="re-read source files and replace instance YAML",
    )
    scan.add_argument(
        "--dry-run", action="store_true", help="validate without writing instance YAML"
    )

    list_command = commands.add_parser("list", help="list registered datasets")
    _add_config_argument(list_command)

    build = modules.add_parser("build", help="compile one configured algorithm")
    _add_config_argument(build)
    build.add_argument(
        "--result-dir",
        type=Path,
        help=(
            "optional exact directory for build receipt and logs; "
            "default: allocate under result/ALGORITHM_ID"
        ),
    )

    run = modules.add_parser(
        "run",
        help="compile and execute one algorithm on selected datasets",
    )
    run.add_argument(
        "--algorithm-config",
        required=True,
        type=Path,
        help="algorithm build configuration YAML",
    )
    run.add_argument(
        "--dataset-config",
        required=True,
        action="append",
        type=Path,
        help="dataset collection configuration YAML; may be repeated",
    )
    run.add_argument(
        "--dataset-path",
        action="append",
        type=Path,
        default=[],
        help="optional dataset directory or subtree to select; may be repeated",
    )
    run.add_argument(
        "--failure-threshold",
        type=int,
        default=1,
        help="overall failure threshold after all datasets; default: 1",
    )
    run.add_argument(
        "--timeout-seconds",
        type=float,
        default=30 * 60.0,
        help="timeout for each Segment algorithm process; default: 1800",
    )
    run.add_argument(
        "--rpe-delta",
        type=float,
        default=DEFAULT_RPE_DELTA_VALUE,
        help="RPE interval value; default: 100",
    )
    run.add_argument(
        "--rpe-unit",
        choices=RPE_DELTA_UNITS,
        default=DEFAULT_RPE_DELTA_UNIT,
        help="RPE interval unit: m=meters, f=frames; default: m",
    )
    run.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop after the first dataset or algorithm failure",
    )
    run.add_argument(
        "--resume",
        type=Path,
        help="resume an incomplete test result directory",
    )

    report = modules.add_parser(
        "report",
        help="rebuild run_summary.xlsx from one saved test directory",
    )
    report.add_argument(
        "--test-dir",
        required=True,
        type=Path,
        help="saved result/ALGORITHM_ID/TEST_ID directory",
    )

    evaluate = modules.add_parser(
        "evaluate",
        help="re-evaluate existing test results and generate timestamped report",
    )
    evaluate.add_argument(
        "--test-dir",
        required=True,
        type=Path,
        help="saved result/ALGORITHM_ID/TEST_ID directory",
    )
    evaluate.add_argument(
        "--rpe-delta",
        type=float,
        help="RPE interval value; if not provided, uses frozen configuration",
    )
    evaluate.add_argument(
        "--rpe-unit",
        choices=RPE_DELTA_UNITS,
        help="RPE interval unit: m=meters, f=frames; if not provided, uses frozen configuration",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    debug = "--debug" in raw_arguments
    parsed_arguments = [item for item in raw_arguments if item != "--debug"]
    args = build_parser().parse_args(parsed_arguments)
    modules = {
        "dataset": (MODULE_TOTAL, MODULE_DATASET),
        "build": (MODULE_TOTAL, MODULE_BUILD),
        "run": PIPELINE_MODULES,
        "report": (MODULE_TOTAL, MODULE_REPORT),
        "evaluate": (MODULE_TOTAL, MODULE_EVALUATE, MODULE_REPORT),
    }[args.module]
    with debug_mode(debug), TerminalProgress(
        modules,
        enabled=False if debug else None,
    ) as progress:
        try:
            if args.module == "dataset":
                return _run_dataset_command(args, progress)
            if args.module == "build":
                return _run_build_command(args, progress)
            if args.module == "run":
                return _run_execution_command(args, progress)
            if args.module == "report":
                return _run_report_command(args, progress)
            if args.module == "evaluate":
                return _run_evaluate_command(args, progress)
        except (
            DatasetError,
            BuildError,
            ExecutionError,
            EvaluationError,
            ReevaluationError,
        ) as exc:
            progress.close()
            print(f"error: {exc}", file=sys.stderr)
            return 2
    return 2


def _run_dataset_command(
    args: argparse.Namespace,
    progress: ProgressReporter,
) -> int:
    manager = DatasetManager(load_dataset_config(args.config))
    progress.begin(MODULE_TOTAL, total=1, detail="数据集管理")
    progress.begin(MODULE_DATASET, detail="扫描并校验数据集")
    if args.dataset_command == "scan":
        report = manager.scan(refresh=args.refresh, persist=not args.dry_run)
    elif args.dataset_command == "list":
        progress.describe(MODULE_DATASET, "读取已登记数据集")
        report = manager.catalog()
    else:
        return 2

    failed = report.has_errors or not report.datasets
    status = "failed" if failed else "success"
    item_count = len(report.datasets) + sum(
        item.level == "error" for item in report.diagnostics
    )
    display_count = max(1, item_count)
    progress.prepare(
        MODULE_DATASET,
        total=display_count,
        completed=display_count,
    )
    progress.finish(
        MODULE_DATASET,
        status=status,
        detail=(
            f"{len(report.datasets)} 个数据集，"
            f"{sum(item.level == 'error' for item in report.diagnostics)} 个异常"
        ),
        complete=True,
    )
    progress.advance(MODULE_TOTAL, detail="数据集管理完成")
    progress.finish(MODULE_TOTAL, status=status, complete=True)
    progress.close()
    _print_report(report)
    return 1 if failed else 0


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True, type=Path, help="configuration YAML")


def _run_build_command(
    args: argparse.Namespace,
    progress: ProgressReporter,
) -> int:
    service = BuildService(load_build_config(args.config))
    progress.begin(MODULE_TOTAL, total=1, detail="算法构建")
    progress.begin(MODULE_BUILD, detail="执行编译脚本")
    if args.result_dir is None:
        receipt = service.build_auto()
    else:
        receipt = service.build(args.result_dir)
    progress.finish(
        MODULE_BUILD,
        status=_progress_status(receipt.status),
        detail=f"耗时 {receipt.duration_seconds:.1f}s",
        complete=True,
    )
    progress.advance(MODULE_TOTAL, detail="构建完成")
    progress.finish(
        MODULE_TOTAL,
        status=_progress_status(receipt.status),
        complete=True,
    )
    progress.close()
    receipt_path = receipt.stdout_path.parent.parent / "build_receipt.yaml"
    message = (
        f"[{receipt.status.upper()}] {receipt.algorithm_id}  receipt: {receipt_path}"
    )
    if receipt.status == "success":
        print(message)
        return 0
    print(message, file=sys.stderr)
    if receipt.failure_reason:
        print(f"reason: {receipt.failure_reason}", file=sys.stderr)
    return 130 if receipt.status == "interrupted" else 1


def _run_execution_command(
    args: argparse.Namespace,
    progress: ProgressReporter,
) -> int:
    policy = FAILURE_POLICY_FAIL_FAST if args.fail_fast else FAILURE_POLICY_CONTINUE
    request = RunRequest(
        build_config=load_build_config(args.algorithm_config),
        dataset_configs=tuple(
            load_dataset_config(path) for path in args.dataset_config
        ),
        selected_dataset_paths=tuple(
            path.expanduser().resolve() for path in args.dataset_path
        ),
        failure_policy=policy,
        failure_threshold=args.failure_threshold,
        timeout_seconds=args.timeout_seconds,
        rpe_delta_value=args.rpe_delta,
        rpe_delta_unit=args.rpe_unit,
    )
    service = ExecutionService(progress=progress)
    if args.resume is None:
        summary = service.start(request)
    else:
        summary = service.resume(request, args.resume)

    progress.close()
    message = (
        f"[{summary.status.upper()}] "
        f"datasets {summary.successful_datasets} success, "
        f"{summary.failed_datasets} failed, "
        f"{summary.not_run_datasets} not run; "
        f"Segments {summary.successful_segments} success, "
        f"{summary.failed_segments} failed, "
        f"{summary.not_run_segments} not run; "
        f"result: {summary.result_root}"
    )
    if summary.status == "success":
        print(message)
        return 0
    print(message, file=sys.stderr)
    if summary.failure_reason:
        print(f"reason: {summary.failure_reason}", file=sys.stderr)
    return 130 if summary.status == "interrupted" else 1


def _run_report_command(
    args: argparse.Namespace,
    progress: ProgressReporter,
) -> int:
    progress.begin(MODULE_TOTAL, total=1, detail="生成 Excel 汇总")
    progress.begin(MODULE_REPORT, total=1, detail="读取 test 数据")
    output_path = SummaryWorkbookWriter().generate_from_test(args.test_dir)
    progress.advance(MODULE_REPORT, detail="Excel 已生成")
    progress.finish(
        MODULE_REPORT,
        status="success",
        detail=str(output_path),
        complete=True,
    )
    progress.advance(MODULE_TOTAL, detail="报告生成完成")
    progress.finish(MODULE_TOTAL, status="success", complete=True)
    progress.close()
    print(f"[SUCCESS] report: {output_path}")
    return 0


def _run_evaluate_command(
    args: argparse.Namespace,
    progress: ProgressReporter,
) -> int:
    request = ReevaluationRequest(
        test_dir=args.test_dir,
        rpe_delta_value=args.rpe_delta,
        rpe_delta_unit=args.rpe_unit,
    )
    service = ReevaluationService(progress=progress)
    summary = service.evaluate(request)

    progress.close()
    message = (
        f"[{summary.status.upper()}] "
        f"segments {summary.successful_segments} success, "
        f"{summary.failed_segments} failed, "
        f"{summary.skipped_segments} skipped; "
        f"report: {summary.report_path}"
    )
    if summary.status == "success":
        print(message)
        return 0
    print(message, file=sys.stderr)
    if summary.failure_reason:
        print(f"reason: {summary.failure_reason}", file=sys.stderr)
    return 1


def _progress_status(status: str) -> str:
    if status == "success":
        return "success"
    if status == "interrupted":
        return "interrupted"
    return "failed"


def _print_report(report: ScanReport) -> None:
    print(f"数据集数量: {len(report.datasets)}")
    for item in report.datasets:
        print(
            f"[{item.status.upper():11}] {item.dataset_id}  {item.dataset_type}  "
            f"Segment {item.valid_segment_count}/{len(item.segments)}  {item.root_path}"
        )
    for diagnostic in report.diagnostics:
        print(
            f"{diagnostic.level.upper()}: {diagnostic.code}: "
            f"{diagnostic.path}: {diagnostic.message}",
            file=sys.stderr if diagnostic.level == "error" else sys.stdout,
        )
