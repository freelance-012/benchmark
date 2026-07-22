from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from slam_benchmark.datasets.errors import ParseError
from slam_benchmark.datasets.parsers import (
    parse_image_timestamps,
    parse_imu_states,
    parse_kitti_timestamps,
    validate_calibration,
    validate_kitti_calibration,
    validate_kitti_poses,
)
from slam_benchmark.datasets.segmentation import segment_by_flight_mode


class DatasetParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_rejects_imu_with_fewer_than_21_columns(self) -> None:
        path = self.root / "imu.txt"
        path.write_text(
            "0 0 1 3 0 0\n" + _imu_row(1.0, 0) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ParseError, "at least 21 columns"):
            parse_imu_states(path)

    def test_rejects_non_integer_flight_mode(self) -> None:
        path = self.root / "imu.txt"
        path.write_text(
            _imu_row(1.0, 1.5) + "\n" + _imu_row(2.0, 0) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ParseError, "flight_mode must be an integer"):
            parse_imu_states(path)

    def test_rejects_non_finite_imu_value(self) -> None:
        path = self.root / "imu.txt"
        values = _imu_row(1.0, 3).split()
        values[4] = "nan"
        path.write_text(
            " ".join(values) + "\n" + _imu_row(2.0, 0) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ParseError, "NaN or infinite"):
            parse_imu_states(path)

    def test_active_range_closes_at_last_nonzero_record_at_eof(self) -> None:
        path = self.root / "imu.txt"
        path.write_text(
            "\n".join(
                [
                    _imu_row(0.0, 0),
                    _imu_row(1.0, 3),
                    _imu_row(2.0, 4),
                    _imu_row(3.0, 0),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        ranges = segment_by_flight_mode(parse_imu_states(path))

        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0], (1.0, 2.0))

    def test_imu_ignores_last_line_even_when_it_is_invalid(self) -> None:
        path = self.root / "imu.txt"
        path.write_text(
            _imu_row(1.0, 3) + "\n" + _imu_row(2.0, 4) + "\nBROKEN TAIL\n",
            encoding="utf-8",
        )

        records = parse_imu_states(path)

        self.assertEqual([record.timestamp for record in records], [1.0, 2.0])

    def test_single_line_imu_is_ignored_and_rejected_as_empty(self) -> None:
        path = self.root / "imu.txt"
        path.write_text(_imu_row(1.0, 3) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(ParseError, "no numeric IMU rows"):
            parse_imu_states(path)

    def test_rk3588_image_timestamps_ignore_last_line(self) -> None:
        for filename in ("imgts_bottom.txt", "imgts_front.txt"):
            path = self.root / filename
            path.write_text("1.0\n2.0\nBROKEN TAIL\n", encoding="utf-8")

            self.assertEqual(parse_image_timestamps(path), [1.0, 2.0])

    def test_rk3399_image_timestamps_still_read_last_line(self) -> None:
        path = self.root / "imgts.txt"
        path.write_text("1.0\nBROKEN TAIL\n", encoding="utf-8")

        with self.assertRaisesRegex(ParseError, "non-numeric"):
            parse_image_timestamps(path)

    def test_rejects_decreasing_image_timestamps(self) -> None:
        path = self.root / "imgts.txt"
        path.write_text("1.0\n0.9\n", encoding="utf-8")

        with self.assertRaisesRegex(ParseError, "non-decreasing"):
            parse_image_timestamps(path)

    def test_calibration_requires_both_fixed_matrices(self) -> None:
        path = self.root / "calib_raw.yaml"
        path.write_text(
            "T_imu_body: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ParseError, "T_cam_imu"):
            validate_calibration(path)

    def test_kitti_timestamps_must_be_strictly_increasing(self) -> None:
        path = self.root / "times.txt"
        path.write_text("0.0\n0.1\n0.1\n", encoding="utf-8")

        with self.assertRaisesRegex(ParseError, "strictly increasing"):
            parse_kitti_timestamps(path)

    def test_kitti_timestamps_do_not_accept_a_header(self) -> None:
        path = self.root / "times.txt"
        path.write_text("timestamp\n0.0\n0.1\n", encoding="utf-8")

        with self.assertRaisesRegex(ParseError, "non-numeric KITTI timestamp"):
            parse_kitti_timestamps(path)

    def test_kitti_calibration_requires_selected_projection_pair(self) -> None:
        path = self.root / "calib.txt"
        path.write_text(
            "P0: 1 0 0 0 0 1 0 0 0 0 1 0\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ParseError, "P1"):
            validate_kitti_calibration(path, ("P0", "P1"))

    def test_kitti_pose_count_must_match_timestamps(self) -> None:
        path = self.root / "00.txt"
        path.write_text(_kitti_pose_row() + "\n", encoding="utf-8")

        with self.assertRaisesRegex(ParseError, "does not match timestamp count"):
            validate_kitti_poses(path, 2)


def _imu_row(timestamp: float, flight_mode: float) -> str:
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


def _kitti_pose_row() -> str:
    return "1 0 0 0 0 1 0 0 0 0 1 0"


if __name__ == "__main__":
    unittest.main()
