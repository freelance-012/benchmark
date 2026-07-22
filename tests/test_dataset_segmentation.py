from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from slam_benchmark.datasets.contracts import get_contract
from slam_benchmark.datasets.parsers import ImuStateRecord
from slam_benchmark.datasets.segmentation import build_segments


class DatasetSegmentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_dataset_types_bind_their_segmentation_rules(self) -> None:
        self.assertEqual(get_contract("rk3399").segmentation_rule, "flight_mode")
        self.assertEqual(get_contract("rk3588").segmentation_rule, "flight_mode")
        self.assertEqual(get_contract("KITTI").segmentation_rule, "timestamp")

    def test_timestamp_rule_accepts_exactly_200_frames_and_10_seconds(self) -> None:
        timestamps = [index * 10.0 / 199 for index in range(200)]

        segments, diagnostics = build_segments(
            "timestamp",
            self.root,
            "dataset-id",
            timestamps,
        )

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].frame_count, 200)
        self.assertEqual(segments[0].duration_seconds, 10.0)
        self.assertTrue(segments[0].valid)
        self.assertEqual(diagnostics, [])

    def test_timestamp_rule_rejects_200_frames_shorter_than_10_seconds(self) -> None:
        timestamps = [index * 9.0 / 199 for index in range(200)]

        segments, diagnostics = build_segments(
            "timestamp",
            self.root,
            "dataset-id",
            timestamps,
        )

        self.assertFalse(segments[0].valid)
        self.assertEqual(segments[0].invalid_reason, "shorter_than_10_seconds")
        self.assertEqual(
            [item.code for item in diagnostics],
            ["segment_too_short", "no_valid_segment"],
        )

    def test_timestamp_rule_rejects_199_frames_even_when_long_enough(self) -> None:
        timestamps = [index * 20.0 / 198 for index in range(199)]

        segments, _ = build_segments(
            "timestamp",
            self.root,
            "dataset-id",
            timestamps,
        )

        self.assertFalse(segments[0].valid)
        self.assertEqual(
            segments[0].invalid_reason,
            "fewer_than_200_image_frames",
        )

    def test_flight_mode_rule_uses_same_200_frame_10_second_validity(self) -> None:
        timestamps = [index * 9.0 / 199 for index in range(200)]
        records = [ImuStateRecord(timestamp, 1) for timestamp in timestamps]

        segments, _ = build_segments(
            "flight_mode",
            self.root,
            "dataset-id",
            timestamps,
            records,
        )

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].frame_count, 200)
        self.assertFalse(segments[0].valid)
        self.assertEqual(segments[0].invalid_reason, "shorter_than_10_seconds")


if __name__ == "__main__":
    unittest.main()
