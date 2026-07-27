"""Resolve framework-owned algorithm contracts into argv lists."""

from __future__ import annotations

import os
from pathlib import Path
from string import Formatter
from typing import Dict, List, Optional, Sequence, Tuple

from ..algorithms.contracts import AlgorithmContract
from ..datasets.models import DatasetInstance, Segment
from .models import ResolvedRunCommand


class CommandError(Exception):
    """One dataset cannot be mapped to the selected algorithm contract."""


_BASE_TEMPLATE_FIELDS = {
    "executable",
    "dataset_path",
    "start_ts",
    "end_ts",
}
_TIMESTAMP_TEMPLATE_FIELDS = {"start_ts", "end_ts"}


def validate_command_template(
    command_template: Sequence[str],
    contract: AlgorithmContract,
) -> Tuple[str, ...]:
    """Validate one algorithm-owned argv template without executing it."""

    template = tuple(command_template)
    if not template:
        raise ValueError("run.command_template must not be empty")
    if template[0] != "{executable}":
        raise ValueError(
            "run.command_template must start with the exact token {executable}"
        )
    if len(template) < 2:
        raise ValueError(
            "run.command_template must include at least one argument after {executable}"
        )

    allowed_fields = set(_BASE_TEMPLATE_FIELDS)
    for run_contract in contract.dataset_run_contracts:
        allowed_fields.update(run_contract.required_input_roles)
        allowed_fields.update(run_contract.optional_input_roles)

    formatter = Formatter()
    for index, token in enumerate(template):
        if not isinstance(token, str) or not token:
            raise ValueError(
                f"run.command_template item {index} must be a non-empty string"
            )
        try:
            parsed = tuple(formatter.parse(token))
        except ValueError as exc:
            raise ValueError(
                f"run.command_template item {index} has invalid braces: {token!r}"
            ) from exc
        for _, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            if field_name not in allowed_fields:
                choices = ", ".join(sorted(allowed_fields))
                raise ValueError(
                    "run.command_template contains unsupported placeholder "
                    f"{field_name!r}; allowed: {choices}"
                )
            if conversion is not None:
                raise ValueError(
                    "run.command_template does not support placeholder conversions"
                )
            if format_spec:
                if field_name not in _TIMESTAMP_TEMPLATE_FIELDS:
                    raise ValueError(
                        "only start_ts and end_ts may use a format specifier"
                    )
                if "{" in format_spec or "}" in format_spec:
                    raise ValueError(
                        "run.command_template format specifiers must be static"
                    )
                try:
                    format(0.0, format_spec)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "run.command_template contains an invalid timestamp "
                        f"format specifier: {format_spec!r}"
                    ) from exc
    return template


def build_run_command(
    entrypoint: Path,
    contract: AlgorithmContract,
    instance: DatasetInstance,
    segment: Segment,
    *,
    command_template: Optional[Tuple[str, ...]] = None,
) -> ResolvedRunCommand:
    return _build_run_command(
        entrypoint,
        contract,
        instance,
        segment,
        command_template=command_template,
    )


def _build_run_command(
    entrypoint: Path,
    contract: AlgorithmContract,
    instance: DatasetInstance,
    segment: Segment,
    *,
    command_template: Optional[Tuple[str, ...]],
) -> ResolvedRunCommand:
    if not segment.valid:
        raise CommandError(f"Segment is not valid: {segment.segment_id}")
    if segment.end_timestamp < segment.start_timestamp:
        raise CommandError(f"Segment timestamp range is invalid: {segment.segment_id}")

    resolved_entrypoint = Path(entrypoint).expanduser().resolve()
    if not resolved_entrypoint.is_file():
        raise CommandError(
            f"algorithm entrypoint does not exist: {resolved_entrypoint}"
        )
    if not os.access(resolved_entrypoint, os.X_OK):
        raise CommandError(
            f"algorithm entrypoint is not executable: {resolved_entrypoint}"
        )

    dataset_root = instance.root_path.expanduser().resolve()
    if not dataset_root.is_dir():
        raise CommandError(f"dataset root is not a directory: {dataset_root}")

    try:
        run_contract = contract.run_contract_for(instance.dataset_type)
    except ValueError as exc:
        raise CommandError(str(exc)) from exc

    duplicate_roles = set(run_contract.required_input_roles).intersection(
        run_contract.optional_input_roles
    )
    if duplicate_roles:
        names = ", ".join(sorted(duplicate_roles))
        raise CommandError(f"algorithm contract repeats input roles: {names}")

    input_arguments: List[Tuple[str, str]] = []
    resolved_inputs: Dict[str, str] = {}

    for role in run_contract.required_input_roles:
        value = _resolve_input(instance, role, required=True)
        assert value is not None
        resolved_inputs[role] = value
        input_arguments.append((role, value))

    for role in run_contract.optional_input_roles:
        value = _resolve_input(instance, role, required=False)
        if value is None:
            input_arguments.append((role, "<none>"))
            continue
        resolved_inputs[role] = value
        input_arguments.append((role, value))

    if command_template is None:
        argv: List[str] = [
            str(resolved_entrypoint),
            str(dataset_root),
            _format_timestamp(segment.start_timestamp),
            _format_timestamp(segment.end_timestamp),
            *resolved_inputs.values(),
        ]
    else:
        template = validate_command_template(command_template, contract)
        values: Dict[str, object] = {
            "executable": str(resolved_entrypoint),
            "dataset_path": str(dataset_root),
            "start_ts": segment.start_timestamp,
            "end_ts": segment.end_timestamp,
            **dict(input_arguments),
        }
        argv = []
        for index, token in enumerate(template):
            try:
                argv.append(token.format_map(values))
            except KeyError as exc:
                raise CommandError(
                    "run.command_template placeholder is unavailable for "
                    f"dataset type {instance.dataset_type}: {exc.args[0]}"
                ) from exc
            except (TypeError, ValueError) as exc:
                raise CommandError(
                    f"cannot render run.command_template item {index}: {exc}"
                ) from exc

    return ResolvedRunCommand(tuple(argv), tuple(input_arguments))


def _resolve_input(
    instance: DatasetInstance,
    role: str,
    *,
    required: bool,
) -> Optional[str]:
    if role not in instance.input_paths:
        if required:
            raise CommandError(f"dataset is missing required input role: {role}")
        return None

    raw_value = instance.input_paths[role]
    if raw_value is None:
        if required:
            raise CommandError(f"required dataset input is unavailable: {role}")
        return None

    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        raise CommandError(f"dataset input path is not absolute for {role}: {path}")
    resolved = path.resolve()
    if not resolved.exists():
        raise CommandError(f"dataset input does not exist for {role}: {resolved}")
    return str(resolved)


def _format_timestamp(value: float) -> str:
    return repr(float(value))
