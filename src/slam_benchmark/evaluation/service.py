"""Invoke the user-provided voeval command for one successful Segment."""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import yaml

from ..algorithms.contracts import (
    EVALUATION_WORKFLOW_SF_VLOC,
    EVALUATION_WORKFLOW_SF_VO,
)
from ..debug import debug_command
from .models import (
    DEFAULT_RPE_DELTA_UNIT,
    DEFAULT_RPE_DELTA_VALUE,
    EvaluationReceipt,
    EvaluationRequest,
    normalize_rpe_delta,
)

DEFAULT_EVALUATION_TIMEOUT_SECONDS = 30 * 60.0
VO_RPE_METRIC_KEYS = ("rmse", "mean", "median", "max", "min", "count")
VO_METRIC_KEYS = VO_RPE_METRIC_KEYS + ("segment_count",)
VLOC_METRIC_KEYS = (
    "trajectory_length_m",
    "mean_error_pos_xy",
    "mean_error_pos_z",
    "mean_error_euler",
    "max_error_pos_xy",
    "max_error_pos_z",
    "max_error_euler",
)
_TERMINATION_GRACE_SECONDS = 2.0


class EvaluationError(Exception):
    """Evaluation facts cannot be produced or persisted safely."""


class EvaluationService:
    def __init__(
        self,
        voeval_command: Sequence[str] = ("voeval",),
        timeout_seconds: float = DEFAULT_EVALUATION_TIMEOUT_SECONDS,
    ):
        command = tuple(str(item) for item in voeval_command)
        if not command or any(not item for item in command):
            raise ValueError("voeval_command must contain at least one command item")
        if timeout_seconds <= 0:
            raise ValueError("evaluation timeout must be greater than zero")
        self.voeval_command = command
        self.timeout_seconds = float(timeout_seconds)

    def evaluate(self, request: EvaluationRequest) -> EvaluationReceipt:
        if request.workflow not in {
            EVALUATION_WORKFLOW_SF_VO,
            EVALUATION_WORKFLOW_SF_VLOC,
        }:
            raise EvaluationError(
                f"unsupported evaluation workflow: {request.workflow}"
            )
        try:
            rpe_delta_value, rpe_delta_unit = normalize_rpe_delta(
                request.rpe_delta_value,
                request.rpe_delta_unit,
            )
        except ValueError as exc:
            raise EvaluationError(str(exc)) from exc

        evaluation_dir = request.evaluation_dir.expanduser().resolve()
        metrics_path = evaluation_dir / "metrics.json"
        receipt_path = evaluation_dir / "receipt.yaml"
        log_path = evaluation_dir / "voeval.log"
        try:
            evaluation_dir.mkdir(parents=True, exist_ok=True)
            for generated in (metrics_path, receipt_path, log_path):
                if generated.exists():
                    if generated.is_symlink() or not generated.is_file():
                        raise EvaluationError(
                            f"evaluation output path is not a regular file: {generated}"
                        )
                    generated.unlink()
        except OSError as exc:
            raise EvaluationError(
                f"cannot prepare evaluation output directory {evaluation_dir}: {exc}"
            ) from exc

        command = self._build_command(
            request,
            metrics_path,
            rpe_delta_value,
            rpe_delta_unit,
        )
        with debug_command(
            "EVALUATE",
            command,
            cwd=request.log_dir,
        ) as trace:
            (
                process_status,
                exit_code,
                started_at,
                finished_at,
                duration_seconds,
                process_failure,
            ) = self._run_process(command, request.log_dir, log_path)

            invalid_metrics: Tuple[str, ...] = ()
            status = process_status
            failure_reason = process_failure
            if process_status == "success":
                try:
                    _, invalid_metrics = load_metrics(
                        metrics_path,
                        request.workflow,
                        expected_delta_value=rpe_delta_value,
                        expected_delta_unit=rpe_delta_unit,
                    )
                except EvaluationError as exc:
                    status = "failed"
                    failure_reason = str(exc)

            receipt = EvaluationReceipt(
                test_id=request.test_id,
                algorithm_id=request.algorithm_id,
                run_index=request.run_index,
                dataset_id=request.dataset_id,
                dataset_type=request.dataset_type,
                segment_id=request.segment_id,
                workflow=request.workflow,
                command=command,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration_seconds,
                status=status,
                exit_code=exit_code,
                metrics_path=metrics_path,
                log_path=log_path,
                invalid_metrics=invalid_metrics,
                failure_reason=failure_reason,
            )
            _save_yaml_atomic(receipt_path, receipt.to_dict())
            trace.complete(
                status=receipt.status,
                exit_code=receipt.exit_code,
                failure_reason=receipt.failure_reason,
                saved=tuple(
                    str(path)
                    for path in (metrics_path, receipt_path, log_path)
                    if path.is_file()
                ),
            )
            return receipt

    def _build_command(
        self,
        request: EvaluationRequest,
        metrics_path: Path,
        rpe_delta_value: float,
        rpe_delta_unit: str,
    ) -> Tuple[str, ...]:
        return self.voeval_command + (
            request.workflow,
            str(request.data_dir.expanduser().resolve()),
            str(request.log_dir.expanduser().resolve()),
            "--dataset",
            request.dataset_type,
            "--delta",
            _format_command_number(rpe_delta_value),
            "--unit",
            rpe_delta_unit,
            "--output",
            str(metrics_path),
            "--verbose",
        )

    def _run_process(
        self,
        command: Tuple[str, ...],
        working_dir: Path,
        log_path: Path,
    ) -> Tuple[str, Optional[int], str, str, float, Optional[str]]:
        started_at = _utc_now()
        started_clock = time.monotonic()
        process: Optional[subprocess.Popen[bytes]] = None
        status = "failed"
        exit_code: Optional[int] = None
        failure_reason: Optional[str] = None

        try:
            with log_path.open("wb") as log:
                try:
                    process = subprocess.Popen(
                        list(command),
                        cwd=working_dir,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                except OSError as exc:
                    failure_reason = f"cannot start voeval: {exc}"
                    log.write((failure_reason + "\n").encode("utf-8"))
                else:
                    try:
                        exit_code = process.wait(timeout=self.timeout_seconds)
                    except subprocess.TimeoutExpired:
                        _terminate_process_group(process)
                        exit_code = process.returncode
                        status = "timeout"
                        failure_reason = (
                            "voeval exceeded timeout of "
                            f"{self.timeout_seconds:g} seconds"
                        )
                    except KeyboardInterrupt:
                        _terminate_process_group(process)
                        exit_code = process.returncode
                        status = "interrupted"
                        failure_reason = "voeval interrupted by user"
                    else:
                        if exit_code == 0:
                            status = "success"
                        else:
                            failure_reason = f"voeval exited with code {exit_code}"
        except OSError as exc:
            raise EvaluationError(f"cannot write voeval log {log_path}: {exc}") from exc

        return (
            status,
            exit_code,
            started_at,
            _utc_now(),
            max(0.0, time.monotonic() - started_clock),
            failure_reason,
        )


def load_metrics(
    metrics_path: Path,
    workflow: str,
    *,
    expected_delta_value: float = DEFAULT_RPE_DELTA_VALUE,
    expected_delta_unit: str = DEFAULT_RPE_DELTA_UNIT,
) -> Tuple[Dict[str, Any], Tuple[str, ...]]:
    try:
        payload: Any = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"cannot read voeval metrics {metrics_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvaluationError("voeval metrics root must be a JSON object")
    if payload.get("mode") != workflow:
        raise EvaluationError(
            "voeval metrics mode does not match the requested workflow"
        )

    if workflow == EVALUATION_WORKFLOW_SF_VO:
        try:
            normalized_delta_value, normalized_delta_unit = normalize_rpe_delta(
                expected_delta_value,
                expected_delta_unit,
            )
        except ValueError as exc:
            raise EvaluationError(str(exc)) from exc
        group_name = "rpe_translation_m"
        metric_keys = VO_RPE_METRIC_KEYS
        group = payload.get(group_name)
        if not isinstance(group, dict):
            raise EvaluationError(f"voeval metrics is missing {group_name}")
        missing = tuple(key for key in metric_keys if key not in group)
        if missing:
            raise EvaluationError(
                "voeval metrics is missing required values: " + ", ".join(missing)
            )
        try:
            delta_value = float(group["delta_value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise EvaluationError("voeval metrics has an invalid RPE delta") from exc
        if not math.isclose(delta_value, normalized_delta_value):
            raise EvaluationError(
                "voeval metrics uses RPE delta "
                f"{delta_value:g}, expected {normalized_delta_value:g}"
            )
        if group.get("delta_unit") != normalized_delta_unit:
            raise EvaluationError(
                "voeval metrics RPE delta unit is "
                f"{group.get('delta_unit')!r}, expected {normalized_delta_unit!r}"
            )
    elif workflow == EVALUATION_WORKFLOW_SF_VLOC:
        group_name = "vloc_metrics"
        metric_keys = VLOC_METRIC_KEYS
        group = payload.get(group_name)
        if not isinstance(group, dict):
            raise EvaluationError(f"voeval metrics is missing {group_name}")
        missing = tuple(key for key in metric_keys if key not in group)
        if missing:
            raise EvaluationError(
                "voeval metrics is missing required values: " + ", ".join(missing)
            )
    else:
        raise EvaluationError(f"unsupported evaluation workflow: {workflow}")

    invalid = [
        key for key in metric_keys if _finite_number_or_none(group.get(key)) is None
    ]
    if (
        workflow == EVALUATION_WORKFLOW_SF_VO
        and _non_negative_integer_or_none(payload.get("segment_count")) is None
    ):
        invalid.append("segment_count")
    return payload, tuple(invalid)


def metric_values(
    metrics: Mapping[str, Any],
    workflow: str,
) -> Tuple[Optional[float], ...]:
    if workflow == EVALUATION_WORKFLOW_SF_VO:
        group = metrics.get("rpe_translation_m")
        keys = VO_RPE_METRIC_KEYS
    elif workflow == EVALUATION_WORKFLOW_SF_VLOC:
        group = metrics.get("vloc_metrics")
        keys = VLOC_METRIC_KEYS
    else:
        raise EvaluationError(f"unsupported evaluation workflow: {workflow}")
    if not isinstance(group, Mapping):
        raise EvaluationError("voeval metrics group is not a JSON object")
    values = tuple(_finite_number_or_none(group.get(key)) for key in keys)
    if workflow == EVALUATION_WORKFLOW_SF_VO:
        return values + (
            _non_negative_integer_or_none(metrics.get("segment_count")),
        )
    return values


def _finite_number_or_none(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _non_negative_integer_or_none(value: Any) -> Optional[float]:
    number = _finite_number_or_none(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return number


def _format_command_number(value: float) -> str:
    return f"{value:.15g}"


def _save_yaml_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    temporary: Optional[Path] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            yaml.safe_dump(
                dict(payload),
                handle,
                allow_unicode=True,
                sort_keys=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, yaml.YAMLError) as exc:
        raise EvaluationError(f"cannot save evaluation receipt {path}: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=_TERMINATION_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()
    process.wait()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
