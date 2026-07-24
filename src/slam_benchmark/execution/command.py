"""Resolve framework-owned algorithm contracts into argv lists."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

from ..algorithms.contracts import AlgorithmContract
from ..datasets.models import DatasetInstance, Segment
from .models import ResolvedRunCommand


class CommandError(Exception):
    """One dataset cannot be mapped to the selected algorithm contract."""


def build_run_command(
    entrypoint: Path,
    contract: AlgorithmContract,
    instance: DatasetInstance,
    segment: Segment,
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

    argv: List[str] = [
        str(resolved_entrypoint),
        str(dataset_root),
        _format_timestamp(segment.start_timestamp),
        _format_timestamp(segment.end_timestamp),
    ]
    input_arguments: List[Tuple[str, str]] = []

    for role in run_contract.required_input_roles:
        value = _resolve_input(instance, role, required=True)
        assert value is not None
        argv.append(value)
        input_arguments.append((role, value))

    for role in run_contract.optional_input_roles:
        value = _resolve_input(instance, role, required=False)
        if value is None:
            input_arguments.append((role, "<none>"))
            continue
        argv.append(value)
        input_arguments.append((role, value))

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
