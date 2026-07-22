"""Safe path resolution for dataset inputs."""

from __future__ import annotations

from pathlib import Path

from .errors import DatasetError
from .parsers import validate_nonempty_file


def resolve_dataset_file(path: Path, root: Path, role: str) -> Path:
    if not path.is_file():
        raise DatasetError(f"缺少文件 {role}: {path}")
    resolved = path.resolve()
    if not is_within(resolved, root):
        raise DatasetError(f"{role} 指向数据集目录之外: {resolved}")
    validate_nonempty_file(resolved, role)
    return resolved


def resolve_dataset_directory(path: Path, root: Path, role: str) -> Path:
    if not path.is_dir():
        raise DatasetError(f"缺少目录 {role}: {path}")
    resolved = path.resolve()
    if not is_within(resolved, root):
        raise DatasetError(f"{role} 指向数据集目录之外: {resolved}")
    return resolved


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
