#!/usr/bin/env python3
"""Run the generated dataset anomaly suite and compare it with its manifest."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from slam_benchmark.config import load_dataset_config  # noqa: E402
from slam_benchmark.datasets.errors import DatasetError  # noqa: E402
from slam_benchmark.datasets.models import ScanDiagnostic  # noqa: E402
from slam_benchmark.datasets.service import DatasetManager  # noqa: E402

DEFAULT_SUITE = Path(tempfile.gettempdir()) / "slam_benchmark_dataset_anomaly_suite"

_CONFIG_ERROR_FRAGMENTS = {
    "configuration read error": "cannot read configuration file",
    "dataset mapping error": "dataset mapping",
    "missing dataset.root_path": "missing dataset.root_path",
    "missing dataset.type": "missing dataset.type",
    "unsupported dataset.type": "dataset.type must be one of",
    "root path is not a directory": "dataset.root_path must be a readable directory",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        type=Path,
        default=DEFAULT_SUITE,
        help="generated anomaly-suite directory",
    )
    return parser.parse_args()


def main() -> int:
    suite = parse_args().suite.expanduser().resolve()
    try:
        manifest = _load_manifest(suite / "expected_results.yaml")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"FAIL: cannot load suite manifest: {exc}", file=sys.stderr)
        return 2

    failures: List[str] = []
    verified_cases = 0
    runs = manifest.get("runs")
    if not isinstance(runs, dict):
        print("FAIL: manifest.runs must be a mapping", file=sys.stderr)
        return 2

    for run_name, run_payload in runs.items():
        if not isinstance(run_payload, dict):
            failures.append(f"{run_name}: run definition must be a mapping")
            continue
        count, run_failures, summary = _verify_run(suite, str(run_name), run_payload)
        verified_cases += count
        failures.extend(run_failures)
        print(summary)

    config_payload = manifest.get("config_cases", [])
    if not isinstance(config_payload, list):
        failures.append("config_cases: must be a list")
        config_payload = []
    config_failures = _verify_config_cases(suite, config_payload)
    failures.extend(config_failures)
    verified_cases += len(config_payload)
    print(f"[CHECK] config_cases: {len(config_payload)} cases")

    if failures:
        print(f"\nFAILED: {len(failures)} mismatches", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"\nPASS: {verified_cases} anomaly cases match expected_results.yaml")
    return 0


def _load_manifest(path: Path) -> Dict[str, Any]:
    payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("document root must be a mapping")
    return payload


def _verify_run(
    suite: Path,
    run_name: str,
    payload: Dict[str, Any],
) -> Tuple[int, List[str], str]:
    cases = payload.get("cases")
    config_value = payload.get("config")
    if not isinstance(cases, list) or not isinstance(config_value, str):
        return (
            0,
            [f"{run_name}: config must be a path and cases must be a list"],
            f"[FAIL] {run_name}: invalid manifest entry",
        )

    try:
        config = load_dataset_config(suite / config_value)
        report = DatasetManager(config).scan(
            refresh=bool(payload.get("refresh")),
            persist=False,
        )
    except DatasetError as exc:
        return (
            len(cases),
            [f"{run_name}: scan setup failed: {exc}"],
            f"[FAIL] {run_name}: scan setup failed",
        )

    failures: List[str] = []
    expected: Dict[str, Dict[str, Any]] = {}
    for item in cases:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            failures.append(f"{run_name}: malformed case record: {item!r}")
            continue
        expected[item["name"]] = item

    datasets_by_case: Dict[str, List[str]] = {}
    for dataset in report.datasets:
        case_name = _direct_child_name(dataset.root_path, config.root_path)
        if case_name is None:
            failures.append(
                f"{run_name}: unexpected dataset root outside case directories: "
                f"{dataset.root_path}"
            )
            continue
        datasets_by_case.setdefault(case_name, []).append(dataset.status)

    diagnostics_by_case: Dict[str, List[str]] = {}
    case_roots = {
        name: _absolute_without_resolving(config.root_path / name) for name in expected
    }
    for diagnostic in report.diagnostics:
        case_name = _case_for_diagnostic(diagnostic, case_roots)
        if case_name is None:
            failures.append(
                f"{run_name}: diagnostic is not associated with a case: "
                f"{diagnostic.code} at {diagnostic.path}"
            )
            continue
        diagnostics_by_case.setdefault(case_name, []).append(diagnostic.code)

    for case_name, item in expected.items():
        prefix = f"{run_name}/{case_name}"
        expected_status = item.get("expected_status")
        expected_codes = sorted(
            str(code) for code in item.get("expected_diagnostics", [])
        )
        actual_statuses = datasets_by_case.get(case_name, [])
        actual_codes = sorted(diagnostics_by_case.get(case_name, []))

        if expected_status in {"invalid", "ignored"}:
            if actual_statuses:
                failures.append(
                    f"{prefix}: expected no registered dataset, got {actual_statuses}"
                )
        elif expected_status in {"ready", "unavailable"}:
            if actual_statuses != [expected_status]:
                failures.append(
                    f"{prefix}: expected status {expected_status}, got "
                    f"{actual_statuses or 'no dataset'}"
                )
        else:
            failures.append(f"{prefix}: unknown expected status {expected_status!r}")

        if actual_codes != expected_codes:
            failures.append(
                f"{prefix}: expected diagnostics {expected_codes}, got {actual_codes}"
            )

    for case_name in sorted(set(datasets_by_case) - set(expected)):
        failures.append(f"{run_name}: unlisted dataset was registered: {case_name}")
    for case_name in sorted(set(diagnostics_by_case) - set(expected)):
        failures.append(f"{run_name}: unlisted case produced diagnostics: {case_name}")

    label = "PASS" if not failures else "FAIL"
    summary = (
        f"[{label}] {run_name}: {len(cases)} cases, "
        f"{len(report.datasets)} registered, {len(report.diagnostics)} diagnostics"
    )
    return len(cases), failures, summary


def _verify_config_cases(
    suite: Path,
    cases: List[Any],
) -> List[str]:
    failures: List[str] = []
    for item in cases:
        if not isinstance(item, dict):
            failures.append(f"config_cases: malformed case record: {item!r}")
            continue
        name = str(item.get("name", "unnamed"))
        expected = str(item.get("expected", ""))
        config_value = item.get("config")
        if not isinstance(config_value, str):
            failures.append(f"config_cases/{name}: missing config path")
            continue
        config_path = suite / config_value

        try:
            config = load_dataset_config(config_path)
        except DatasetError as exc:
            fragment = _CONFIG_ERROR_FRAGMENTS.get(expected)
            if fragment is None:
                failures.append(
                    f"config_cases/{name}: unexpected configuration error: {exc}"
                )
            elif fragment not in str(exc):
                failures.append(
                    f"config_cases/{name}: expected error containing {fragment!r}, "
                    f"got {str(exc)!r}"
                )
            continue

        if expected != "no_datasets_found":
            failures.append(
                f"config_cases/{name}: expected configuration rejection {expected!r}, "
                "but loading succeeded"
            )
            continue

        report = DatasetManager(config).scan(refresh=True, persist=False)
        actual_codes = [item.code for item in report.diagnostics]
        if report.datasets or actual_codes != ["no_datasets_found"]:
            failures.append(
                f"config_cases/{name}: expected only no_datasets_found, got "
                f"datasets={len(report.datasets)}, diagnostics={actual_codes}"
            )
    return failures


def _direct_child_name(path: Path, parent: Path) -> Optional[str]:
    try:
        relative = path.relative_to(parent)
    except ValueError:
        return None
    return relative.parts[0] if len(relative.parts) == 1 else None


def _case_for_diagnostic(
    diagnostic: ScanDiagnostic,
    roots: Dict[str, Path],
) -> Optional[str]:
    path = _absolute_without_resolving(diagnostic.path)
    for name, root in roots.items():
        if path == root or root in path.parents:
            return name
    return None


def _absolute_without_resolving(path: Path) -> Path:
    return path.expanduser().absolute()


if __name__ == "__main__":
    raise SystemExit(main())
