"""Persistence for generated dataset instance files."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

from .errors import StorageError
from .models import DatasetInstance


class DatasetInstanceStore:
    def load(self, path: Path) -> DatasetInstance:
        try:
            payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("document root must be a mapping")
            return DatasetInstance.from_dict(payload)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise StorageError(f"cannot load dataset instance {path}: {exc}") from exc

    def save(self, path: Path, instance: DatasetInstance) -> None:
        temporary: Optional[Path] = None
        try:
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
                    instance.to_dict(),
                    handle,
                    allow_unicode=True,
                    sort_keys=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except (OSError, yaml.YAMLError) as exc:
            raise StorageError(f"cannot save dataset instance {path}: {exc}") from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)
