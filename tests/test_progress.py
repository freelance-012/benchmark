from __future__ import annotations

import io
import unittest
from unittest import mock

from slam_benchmark.progress import (
    MODULE_EVALUATE,
    MODULE_REPORT,
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
            renderables = tuple(progress._progress.get_renderables())
            self.assertEqual(len(renderables), 1)
            self.assertEqual(
                renderables[0].width,
                progress._progress.console.width - 2,
            )
            lines = progress._progress.console.render_lines(
                progress._progress.get_renderable(),
                pad=False,
            )
            rendered_widths = [
                sum(segment.cell_length for segment in line) for line in lines
            ]
            self.assertLessEqual(
                max(rendered_widths),
                progress._progress.console.width - 2,
            )
            self.assertFalse(progress._progress.expand)
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

    def test_paused_status_is_rendered_without_completing_the_task(self) -> None:
        output = io.StringIO()

        with TerminalProgress(
            (MODULE_TOTAL,),
            output=output,
            force_terminal=True,
            enabled=True,
        ) as progress:
            progress.begin(MODULE_TOTAL, total=2, detail="运行数据集")
            progress.finish(
                MODULE_TOTAL,
                status="paused",
                detail="等待数据盘重新挂载",
            )
            task = progress._progress.tasks[progress._task_ids[MODULE_TOTAL]]

            self.assertEqual(task.fields["state"], "paused")
            self.assertEqual(task.completed, 0)

        self.assertIn("已暂停", output.getvalue())

    def test_event_driven_refresh_does_not_redraw_unchanged_scan_state(self) -> None:
        output = io.StringIO()
        clock = _FakeClock()

        with mock.patch.dict("os.environ", {"TERM": "xterm-256color"}):
            with TerminalProgress(
                (MODULE_TOTAL,),
                output=output,
                force_terminal=True,
                enabled=True,
                get_time=clock,
            ) as progress:
                progress.begin(MODULE_TOTAL, total=1, detail="扫描并录入数据集")
                rendered_after_begin = output.getvalue()

                self.assertFalse(progress._progress.live.auto_refresh)
                self.assertIsNone(progress._progress.live._refresh_thread)

                clock.advance(60)
                self.assertEqual(output.getvalue(), rendered_after_begin)

                progress.describe(MODULE_TOTAL, "扫描完成")
                self.assertGreater(len(output.getvalue()), len(rendered_after_begin))

    def test_event_driven_refresh_throttles_repeated_runtime_estimates(self) -> None:
        output = io.StringIO()
        clock = _FakeClock()

        with mock.patch.dict("os.environ", {"TERM": "xterm-256color"}):
            with TerminalProgress(
                (MODULE_RUN,),
                output=output,
                force_terminal=True,
                enabled=True,
                get_time=clock,
            ) as progress:
                progress.begin(MODULE_RUN, total=1, detail="运行 Segment")
                rendered_after_begin = len(output.getvalue())

                progress.estimate(MODULE_RUN, eta_seconds=60)
                rendered_after_first_estimate = len(output.getvalue())
                self.assertGreater(
                    rendered_after_first_estimate,
                    rendered_after_begin,
                )

                progress.estimate(MODULE_RUN, eta_seconds=59)
                self.assertEqual(
                    len(output.getvalue()),
                    rendered_after_first_estimate,
                )

                clock.advance(0.25)
                progress.estimate(MODULE_RUN, eta_seconds=58)
                self.assertGreater(
                    len(output.getvalue()),
                    rendered_after_first_estimate,
                )

    def test_waiting_modules_pause_and_accumulate_only_active_time(self) -> None:
        output = io.StringIO()
        clock = _FakeClock()

        with TerminalProgress(
            (MODULE_EVALUATE, MODULE_REPORT),
            output=output,
            force_terminal=True,
            enabled=True,
            auto_refresh=False,
            get_time=clock,
        ) as progress:
            progress.prepare(
                MODULE_EVALUATE,
                total=2,
                detail="等待运行结果",
            )
            task = progress._progress.tasks[progress._task_ids[MODULE_EVALUATE]]
            clock.advance(100)
            self.assertIsNone(task.elapsed)
            self.assertEqual(task.fields["state"], "waiting")
            self.assertEqual(str(_RemainingTimeColumn().render(task)), "")

            progress.begin(MODULE_EVALUATE, detail="评估 Segment 1")
            clock.advance(3)
            progress.advance(MODULE_EVALUATE, detail="Segment 1：success")
            progress.wait(MODULE_EVALUATE, detail="等待下一段运行结果")

            self.assertEqual(task.completed, 1)
            self.assertEqual(task.elapsed, 3)
            self.assertEqual(task.fields["state"], "waiting")
            clock.advance(50)
            self.assertEqual(task.elapsed, 3)

            progress.begin(MODULE_EVALUATE, detail="评估 Segment 2")
            self.assertEqual(task.completed, 1)
            clock.advance(2)
            progress.advance(MODULE_EVALUATE, detail="Segment 2：success")
            progress.wait(MODULE_EVALUATE, detail="等待下一段运行结果")
            progress.finish(MODULE_EVALUATE, status="success")

            self.assertEqual(task.completed, 2)
            self.assertEqual(task.elapsed, 5)
            clock.advance(100)
            self.assertEqual(task.elapsed, 5)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


if __name__ == "__main__":
    unittest.main()
