from __future__ import annotations

import unittest

from slam_benchmark.datasets.models import Segment
from slam_benchmark.execution.runtime_progress import (
    PROGRESS_PARSER_BENCHMARK_JSON_V1,
    RunEtaEstimator,
    progress_parser,
)


class RuntimeProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.segments = (
            Segment("seg-0", 0, 100.0, 200.0, 100.0, 1000, True),
            Segment("seg-1", 1, 200.0, 400.0, 200.0, 2000, True),
        )

    def test_parser_accepts_only_prefixed_finite_progress_json(self) -> None:
        parser = progress_parser(PROGRESS_PARSER_BENCHMARK_JSON_V1)
        assert parser is not None

        sample = parser(
            'BENCHMARK_PROGRESS {"timestamp":125.0,"frame":250,'
            '"fps":20.0,"percent":25.0}'
        )

        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertEqual(sample.timestamp, 125.0)
        self.assertEqual(sample.frame, 250.0)
        self.assertEqual(sample.fps, 20.0)
        self.assertEqual(sample.percent, 25.0)
        self.assertIsNone(parser("ordinary algorithm output"))
        self.assertIsNone(parser("BENCHMARK_PROGRESS not-json"))
        self.assertIsNone(parser('BENCHMARK_PROGRESS {"fps":"fast"}'))

    def test_eta_uses_duration_then_adapts_to_live_timestamp_speed(self) -> None:
        estimator = RunEtaEstimator(self.segments)

        initial = estimator.start_segment(0, now=0.0)
        parser = progress_parser(PROGRESS_PARSER_BENCHMARK_JSON_V1)
        assert parser is not None
        sample = parser('BENCHMARK_PROGRESS {"timestamp":125.0}')
        assert sample is not None
        live = estimator.observe(sample, now=50.0)

        self.assertEqual(initial.fraction, 0.0)
        self.assertEqual(initial.eta_seconds, 300.0)
        self.assertIsNotNone(live)
        assert live is not None
        self.assertAlmostEqual(live.fraction, 0.25)
        self.assertAlmostEqual(live.data_rate, 0.65)
        self.assertAlmostEqual(live.eta_seconds, 275.0 / 0.65)

        remaining = estimator.finish_segment(
            successful=True,
            duration_seconds=200.0,
        )
        self.assertEqual(remaining.fraction, 1.0)
        self.assertAlmostEqual(remaining.data_rate, 0.575)
        self.assertAlmostEqual(remaining.eta_seconds, 200.0 / 0.575)

    def test_frame_progress_is_used_when_timestamp_is_unavailable(self) -> None:
        estimator = RunEtaEstimator(self.segments)
        estimator.start_segment(0, now=0.0)
        parser = progress_parser(PROGRESS_PARSER_BENCHMARK_JSON_V1)
        assert parser is not None
        sample = parser('BENCHMARK_PROGRESS {"frame":500,"fps":25.0}')
        assert sample is not None

        estimate = estimator.observe(sample, now=40.0)

        self.assertIsNotNone(estimate)
        assert estimate is not None
        self.assertEqual(estimate.source, "frame")
        self.assertAlmostEqual(estimate.fraction, 0.5)


if __name__ == "__main__":
    unittest.main()
