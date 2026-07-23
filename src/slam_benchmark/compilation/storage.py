"""Atomic persistence for build receipts."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

import yaml

from .models import BuildReceipt


class BuildReceiptStore:
    def save(self, path: Path, receipt: BuildReceipt) -> None:
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
                    receipt.to_dict(),
                    handle,
                    allow_unicode=True,
                    sort_keys=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(f"cannot save build receipt {path}: {exc}") from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)
