#!/usr/bin/env python3
"""Generate a two-Segment RK3399 case whose first algorithm run fails."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "testdata" / "segment_continue_case"
ALGORITHM_FIXTURE = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "mock_algorithms" / "algorithm2"
)

CALIBRATION = """%YAML:1.0
T_imu_body: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
cam0:
  T_cam_imu: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "generate two valid RK3399 Segments and a mock algorithm that "
            "intentionally fails only the first Segment"
        )
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help=f"replace only the generated case at {DEFAULT_OUTPUT_ROOT}",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    output_root = DEFAULT_OUTPUT_ROOT.resolve()
    if output_root.exists():
        if not args.clean:
            print(
                f"error: generated case already exists: {output_root}; "
                "use --clean to replace it",
                file=sys.stderr,
            )
            return 2
        shutil.rmtree(output_root)

    dataset_root = output_root / "datasets" / "two_segment_case"
    algorithm_root = output_root / "algorithm2_first_segment_fails"
    config_root = output_root / "configs"

    _write_dataset(dataset_root)
    _write_algorithm(algorithm_root)
    _initialize_git_repository(algorithm_root)
    _write_configs(config_root, dataset_root.parent, algorithm_root)

    print(f"case_root: {output_root}")
    print(f"dataset_root: {dataset_root}")
    print("expected: Segment 1 failed, Segment 2 success, not_run 0")
    print("")
    print("run:")
    print(
        f'  benchmark dataset scan --config "{config_root / "dataset.yaml"}" --refresh'
    )
    print(
        "  benchmark run "
        f'--algorithm-config "{config_root / "algorithm.yaml"}" '
        f'--dataset-config "{config_root / "dataset.yaml"}" --debug'
    )
    return 0


def _write_dataset(dataset_root: Path) -> None:
    dataset_root.mkdir(parents=True)
    modes = [0] + [1] * 200 + [0] + [2] * 200 + [0]
    timestamps = [float(index) for index in range(len(modes))]
    imu_rows = [
        _imu_row(timestamp, flight_mode)
        for timestamp, flight_mode in zip(timestamps, modes)
    ]
    (dataset_root / "imu.txt").write_text(
        "\n".join(imu_rows) + "\nIGNORED LAST LINE\n",
        encoding="utf-8",
    )
    (dataset_root / "imgts.txt").write_text(
        "\n".join(str(timestamp) for timestamp in timestamps) + "\n",
        encoding="utf-8",
    )
    (dataset_root / "img.avi").write_bytes(b"synthetic-rk3399-video\n")
    (dataset_root / "calib_raw.yaml").write_text(
        CALIBRATION,
        encoding="utf-8",
    )


def _imu_row(timestamp: float, flight_mode: int) -> str:
    values = [
        timestamp,
        timestamp,
        1,
        flight_mode,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        31.1,
        121.2,
        50,
        51,
        5,
    ]
    return " ".join(str(value) for value in values)


def _write_algorithm(algorithm_root: Path) -> None:
    shutil.copytree(ALGORITHM_FIXTURE, algorithm_root)
    source_path = algorithm_root / "main.c"
    source = source_path.read_text(encoding="utf-8")
    anchor = '    FILE *output = fopen(OUTPUT_FILENAME, "w");'
    if source.count(anchor) != 1:
        raise RuntimeError(
            "algorithm2 fixture no longer has the expected output anchor"
        )
    source = source.replace(
        anchor,
        (
            "    if (start < 100.0) {\n"
            '        fprintf(stderr, "intentional failure for Segment 1\\n");\n'
            "        return 9;\n"
            "    }\n\n"
            f"{anchor}"
        ),
    )
    source_path.write_text(source, encoding="utf-8")
    (algorithm_root / ".gitignore").write_text(
        "build/\nmock_output.txt\nhome_point.txt\n",
        encoding="utf-8",
    )
    (algorithm_root / "build.sh").chmod(0o755)


def _initialize_git_repository(algorithm_root: Path) -> None:
    commands = (
        ("init", "-q"),
        ("config", "user.email", "segment-test@example.com"),
        ("config", "user.name", "Segment Test"),
        ("add", "."),
        ("commit", "-q", "-m", "segment continuation fixture"),
    )
    for arguments in commands:
        subprocess.run(
            ["git", *arguments],
            cwd=algorithm_root,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


def _write_configs(
    config_root: Path,
    dataset_collection: Path,
    algorithm_root: Path,
) -> None:
    config_root.mkdir(parents=True)
    (config_root / "dataset.yaml").write_text(
        yaml.safe_dump(
            {
                "dataset": {
                    "root_path": str(dataset_collection),
                    "type": "rk3399",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (config_root / "algorithm.yaml").write_text(
        yaml.safe_dump(
            {
                "algorithm": "algorithm2",
                "build": {
                    "algorithm_path": str(algorithm_root),
                    "script_path": str(algorithm_root / "build.sh"),
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
