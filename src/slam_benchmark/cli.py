"""Command-line interface for the benchmark pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import load_dataset_config
from .datasets.errors import DatasetError
from .datasets.models import ScanReport
from .datasets.service import DatasetManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark", description="SLAM algorithm benchmark pipeline"
    )
    parser.add_argument("--version", action="version", version=__version__)
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
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.module == "dataset":
            return _run_dataset_command(args)
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


def _run_dataset_command(args: argparse.Namespace) -> int:
    manager = DatasetManager(load_dataset_config(args.config))
    if args.dataset_command == "scan":
        report = manager.scan(refresh=args.refresh, persist=not args.dry_run)
        _print_report(report)
        return 1 if report.has_errors or not report.datasets else 0
    if args.dataset_command == "list":
        report = manager.catalog()
        _print_report(report)
        return 1 if report.has_errors or not report.datasets else 0
    return 2


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True, type=Path, help="dataset scan YAML")


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
