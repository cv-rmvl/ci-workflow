"""LPSS 后台任务结果测试。"""

import io
import threading
import time
import unittest
from unittest.mock import patch

from components.func.lpss.base import (
    LpssAdapter,
    LpssTask,
    _stream_adapter_output,
    result,
)


class LpssResultTest(unittest.TestCase):
    @staticmethod
    def _task():
        return LpssTask(LpssAdapter.MSG_STRSUB, "test_node", object())

    @staticmethod
    def _publish(task, values):
        with task.condition:
            task.results.extend(values)
            task.condition.notify_all()

    def test_waits_for_results_in_receive_order(self):
        task = self._task()
        timer = threading.Timer(0.01, self._publish, args=(task, ["a", "a"]))
        timer.start()
        try:
            self.assertEqual(result(task, 2, False, 0.5), ["a", "a"])
        finally:
            timer.join()

    def test_unique_results_are_sorted(self):
        task = self._task()
        self._publish(task, ["third", "first", "third", "second"])
        self.assertEqual(
            result(task, 3, True, 0.5),
            ["first", "second", "third"],
        )

    def test_single_result_is_returned_as_scalar(self):
        task = self._task()
        self._publish(task, ["pass"])
        self.assertEqual(result(task, 1, False, 0.5), "pass")

    def test_finished_task_fails_without_waiting_for_timeout(self):
        task = self._task()
        task.finished = True
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "producing 0 of 1"):
            result(task, 1, False, 1.0)
        self.assertLess(time.monotonic() - start, 0.1)

    def test_timeout_reports_received_values(self):
        task = self._task()
        self._publish(task, ["only"])
        with self.assertRaisesRegex(TimeoutError, r"received \['only'\]"):
            result(task, 2, False, 0.01)

    def test_adapter_markers_are_captured_only_for_collectors(self):
        collector = LpssTask(
            LpssAdapter.MSG_STRSUB,
            "collector",
            object(),
            collect_results=True,
        )
        regular = self._task()

        with patch("components.func.lpss.base.logger.info") as log:
            _stream_adapter_output(
                collector,
                io.StringIO("KDT_RESULT: value\nvisible output\n"),
            )
            _stream_adapter_output(
                regular,
                io.StringIO("KDT_RESULT: ignored\n"),
            )

        self.assertEqual(collector.results, ["value"])
        self.assertEqual(regular.results, [])
        log.assert_called_once_with(
            "visible output",
            source=LpssAdapter.MSG_STRSUB.value,
        )


if __name__ == "__main__":
    unittest.main()
