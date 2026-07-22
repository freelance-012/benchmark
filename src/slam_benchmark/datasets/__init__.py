"""Public dataset-management API."""

from .contracts import (
    INSTANCE_FILENAME,
    MIN_SEGMENT_DURATION_SECONDS,
    MIN_SEGMENT_FRAMES,
    supported_dataset_types,
)
from .models import (
    DatasetInstance,
    DatasetScanConfig,
    ScanDiagnostic,
    ScanReport,
    Segment,
)
from .service import DatasetManager

__all__ = [
    "DatasetInstance",
    "DatasetManager",
    "DatasetScanConfig",
    "INSTANCE_FILENAME",
    "MIN_SEGMENT_DURATION_SECONDS",
    "MIN_SEGMENT_FRAMES",
    "ScanDiagnostic",
    "ScanReport",
    "Segment",
    "supported_dataset_types",
]
