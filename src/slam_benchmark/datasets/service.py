"""Dataset discovery, registration, and catalog queries."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set, Tuple

from ..debug import debug_command
from .contracts import (
    INSTANCE_FILENAME,
    MIN_SEGMENT_DURATION_SECONDS,
    MIN_SEGMENT_FRAMES,
)
from .errors import DatasetError, StorageError
from .handlers import DatasetHandler, get_handler
from .models import DatasetInstance, DatasetScanConfig, ScanDiagnostic, ScanReport
from .paths import is_within
from .storage import DatasetInstanceStore


class DatasetManager:
    """Coordinates dataset use cases without knowing format-specific parsing."""

    def __init__(
        self, config: DatasetScanConfig, store: Optional[DatasetInstanceStore] = None
    ):
        self.config = config
        self.handler: DatasetHandler = get_handler(config.dataset_type)
        self.store = store or DatasetInstanceStore()

    def scan(self, *, refresh: bool = False, persist: bool = True) -> ScanReport:
        with debug_command(
            "DATASET_SCAN",
            (
                "dataset.scan",
                "--root-path",
                self.config.root_path,
                "--dataset-type",
                self.config.dataset_type,
                "--refresh",
                refresh,
                "--persist",
                persist,
            ),
        ) as trace:
            report = self._scan(refresh=refresh, persist=persist)
            trace.complete(
                status="failed" if report.has_errors else "success",
                datasets=len(report.datasets),
                ready=sum(item.status == "ready" for item in report.datasets),
                segments=sum(item.valid_segment_count for item in report.datasets),
                diagnostics=len(report.diagnostics),
                saved_files=(
                    sum(
                        (item.root_path / INSTANCE_FILENAME).is_file()
                        for item in report.datasets
                    )
                    if persist
                    else 0
                ),
                output_name=INSTANCE_FILENAME if persist else None,
            )
            return report

    def _scan(self, *, refresh: bool, persist: bool) -> ScanReport:
        datasets: List[DatasetInstance] = []
        diagnostics: List[ScanDiagnostic] = []
        candidates = self._discover_candidates()
        if not candidates:
            message = f"未发现包含 {self.handler.discovery_filename} 的数据集"
            diagnostic = ScanDiagnostic(
                "error", "no_datasets_found", self.config.root_path, message
            )
            return ScanReport(tuple(), (diagnostic,))

        for root in candidates:
            try:
                instance, current = self._load_or_register(
                    root, refresh=refresh, persist=persist
                )
                datasets.append(instance)
                diagnostics.extend(current)
            except DatasetError as exc:
                diagnostics.append(
                    ScanDiagnostic("error", "dataset_invalid", root, str(exc))
                )

        datasets.sort(key=lambda item: (str(item.root_path), item.dataset_id))
        return ScanReport(tuple(datasets), tuple(diagnostics))

    def catalog(self) -> ScanReport:
        with debug_command(
            "DATASET_LIST",
            (
                "dataset.list",
                "--root-path",
                self.config.root_path,
                "--dataset-type",
                self.config.dataset_type,
            ),
        ) as trace:
            report = self._catalog()
            trace.complete(
                status="failed" if report.has_errors else "success",
                datasets=len(report.datasets),
                ready=sum(item.status == "ready" for item in report.datasets),
                diagnostics=len(report.diagnostics),
            )
            return report

    def _catalog(self) -> ScanReport:
        datasets: List[DatasetInstance] = []
        diagnostics: List[ScanDiagnostic] = []
        for path in sorted(self.config.root_path.rglob(INSTANCE_FILENAME)):
            try:
                instance = self.store.load(path)
                if instance.dataset_type != self.config.dataset_type:
                    continue
                if instance.root_path != path.parent.resolve():
                    raise StorageError(
                        "instance root_path does not match its directory"
                    )
                if not is_within(instance.root_path, self.config.root_path):
                    raise StorageError(
                        "instance root_path is outside configured dataset root"
                    )
                datasets.append(instance)
            except DatasetError as exc:
                diagnostics.append(
                    ScanDiagnostic("error", "instance_invalid", path, str(exc))
                )
        datasets.sort(key=lambda item: (str(item.root_path), item.dataset_id))
        return ScanReport(tuple(datasets), tuple(diagnostics))

    def _discover_candidates(self) -> List[Path]:
        roots: Set[Path] = set()
        for path in self.config.root_path.rglob(self.handler.discovery_filename):
            if path.is_file():
                root = path.parent.resolve()
                if is_within(root, self.config.root_path) and ".git" not in root.parts:
                    roots.add(root)
        return sorted(roots, key=str)

    def _load_or_register(
        self,
        root: Path,
        *,
        refresh: bool,
        persist: bool,
    ) -> Tuple[DatasetInstance, List[ScanDiagnostic]]:
        instance_path = root / INSTANCE_FILENAME
        diagnostics: List[ScanDiagnostic] = []
        if instance_path.is_file() and not refresh:
            try:
                instance = self.store.load(instance_path)
                if self.handler.can_reuse(
                    instance,
                    root,
                    self.config.root_path,
                ):
                    if instance.status == "unavailable":
                        diagnostics.append(
                            ScanDiagnostic(
                                "error",
                                "no_valid_segment",
                                root,
                                (
                                    f"没有同时达到 {MIN_SEGMENT_FRAMES} 个图像帧和 "
                                    f"{MIN_SEGMENT_DURATION_SECONDS:g} 秒的有效 Segment"
                                ),
                            )
                        )
                    return instance, diagnostics
            except StorageError as exc:
                diagnostics.append(
                    ScanDiagnostic(
                        "warning", "instance_rebuilt", instance_path, str(exc)
                    )
                )

        instance, registration_diagnostics = self.handler.register(
            root,
            self.config.root_path,
        )
        diagnostics.extend(registration_diagnostics)
        if persist:
            self.store.save(instance_path, instance)
        return instance, diagnostics
