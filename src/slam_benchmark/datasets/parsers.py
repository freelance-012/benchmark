"""Fixed-format parsing used during dataset registration."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .errors import ParseError

IMU_FIXED_COLUMNS = (
    "ts",
    "ts_fcc",
    "status",
    "flight_mode",
    "x",
    "y",
    "z",
    "yaw",
    "pitch",
    "roll",
    "vx",
    "vy",
    "vz",
    "position_reset_count",
    "altitude_reset_count",
    "heading_reset_count",
    "latitude",
    "longitude",
    "altitude",
    "altitude_msl",
    "height",
)

_INTEGER_COLUMNS = {
    2: "status",
    3: "flight_mode",
    13: "position_reset_count",
    14: "altitude_reset_count",
    15: "heading_reset_count",
}
_SEPARATOR = re.compile(r"[\s,;]+")
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


@dataclass(frozen=True)
class ImuStateRecord:
    timestamp: float
    flight_mode: int


def parse_imu_states(path: Path) -> List[ImuStateRecord]:
    rows = _read_numeric_rows(
        path,
        len(IMU_FIXED_COLUMNS),
        "IMU",
        allow_extra=True,
        ignore_last_line=True,
    )
    records: List[ImuStateRecord] = []
    previous_timestamp: Optional[float] = None
    for row_no, row in enumerate(rows, start=1):
        for column, name in _INTEGER_COLUMNS.items():
            if not _is_integer(row[column]):
                raise ParseError(
                    f"{path}: {name} must be an integer at data row {row_no}"
                )
        timestamp = row[0]
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise ParseError(f"{path}: timestamps must be non-decreasing")
        records.append(ImuStateRecord(timestamp, int(round(row[3]))))
        previous_timestamp = timestamp
    return records


def parse_image_timestamps(path: Path) -> List[float]:
    rows = _read_numeric_rows(
        path,
        1,
        "image timestamp",
        allow_extra=True,
        ignore_last_line=path.name in {"imgts_bottom.txt", "imgts_front.txt"},
    )
    timestamps = [row[0] for row in rows]
    if any(current < previous for previous, current in zip(timestamps, timestamps[1:])):
        raise ParseError(f"{path}: image timestamps must be non-decreasing")
    return timestamps


def validate_calibration(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ParseError(f"cannot read calibration file {path}: {exc}") from exc
    for key in ("T_imu_body", "T_cam_imu"):
        match = re.search(
            rf"{re.escape(key)}\s*:\s*\[([^\]]+)\]", text, flags=re.DOTALL
        )
        if not match:
            raise ParseError(f"{path}: missing required 4x4 matrix {key}")
        values = [float(token) for token in _NUMBER.findall(match.group(1))]
        if len(values) != 16 or not all(math.isfinite(value) for value in values):
            raise ParseError(f"{path}: matrix {key} must contain 16 finite values")


def validate_home_point(path: Path) -> None:
    rows = _read_numeric_rows(path, 3, "home_point", allow_extra=False)
    if len(rows) != 1:
        raise ParseError(f"{path}: home_point must contain exactly one numeric row")


def validate_nonempty_file(path: Path, label: str) -> None:
    try:
        if path.stat().st_size <= 0:
            raise ParseError(f"{path}: {label} file is empty")
    except OSError as exc:
        raise ParseError(f"cannot inspect {label} file {path}: {exc}") from exc


def _read_numeric_rows(
    path: Path,
    expected_columns: int,
    label: str,
    *,
    allow_extra: bool,
    ignore_last_line: bool = False,
) -> List[List[float]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise ParseError(f"cannot read {path}: {exc}") from exc
    if ignore_last_line:
        lines = lines[:-1]

    rows: List[List[float]] = []
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = [token for token in _SEPARATOR.split(line) if token]
        parse_tokens = tokens[:expected_columns] if allow_extra else tokens
        try:
            values = [float(token) for token in parse_tokens]
        except ValueError:
            if not rows:
                continue
            raise ParseError(f"{path}: non-numeric {label} data at line {line_no}")
        if len(tokens) < expected_columns:
            raise ParseError(
                f"{path}: {label} requires at least {expected_columns} columns at line {line_no}"
            )
        if len(tokens) > expected_columns and not allow_extra:
            raise ParseError(
                f"{path}: {label} requires exactly {expected_columns} columns"
            )
        if not all(math.isfinite(value) for value in values):
            raise ParseError(f"{path}: {label} contains NaN or infinite values")
        rows.append(values)

    if not rows:
        raise ParseError(f"{path}: no numeric {label} rows found")
    return rows


def _is_integer(value: float) -> bool:
    return math.isfinite(value) and abs(value - round(value)) <= 1e-9
