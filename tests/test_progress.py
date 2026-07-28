from __future__ import annotations

import io
import unittest

from slam_benchmark.progress import (
    MODULE_RUN,
    MODULE_TOTAL,
    TerminalProgress,
    _RemainingTimeColumn,
)


class TerminalProgressTests(unittest.TestCase):
    def test_renders_overall_and_module_bars_with_eta(self) -> None:
        output = io.StringIO()

        with TerminalProgress(
            (MODULE_TOTAL, MODULE_RUN),
            output=output,
            force_terminal=True,
            enabled=True,
            auto_refresh=False,
        ) as progress:
            progress.begin(MODULE_TOTAL, total=2, detail="执行测试")
            progress.begin(MODULE_RUN, total=2, detail="等待 Segment")
            progress.estimate(
                MODULE_RUN,
                eta_seconds=65,
                detail="当前 Segment 50%",
            )
            run_task = progress._progress.tasks[progress._task_ids[MODULE_RUN]]
            self.assertEqual(
                str(_RemainingTimeColumn().render(run_task)),
                "预计剩余 00:01:05",
            )
            self.assertEqual(run_task.fields["detail"], "当前 Segment 50%")
            progress.advance(MODULE_RUN, amount=2, detail="Segment 2")
            progress.advance(MODULE_TOTAL, amount=2, detail="全部保存")
            progress.finish(MODULE_RUN, status="success")
            progress.finish(MODULE_TOTAL, status="success")
            self.assertEqual(str(_RemainingTimeColumn().render(run_task)), "")
            progress.refresh()

        rendered = output.getvalue()
        self.assertIn("总进度", rendered)
        self.assertIn("RUN", rendered)
        self.assertIn("2/2", rendered)
        self.assertIn("100%", rendered)
        self.assertNotIn("预计剩余", rendered)
        self.assertIn("完成", rendered)

    def test_disabled_progress_does_not_write_terminal_output(self) -> None:
        output = io.StringIO()

        with TerminalProgress(
            (MODULE_TOTAL,),
            output=output,
            force_terminal=True,
            enabled=False,
        ) as progress:
            progress.begin(MODULE_TOTAL, total=1)
            progress.advance(MODULE_TOTAL)
            progress.finish(MODULE_TOTAL, status="success")

        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
