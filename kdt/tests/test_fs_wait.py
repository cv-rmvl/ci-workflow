"""异步文件输出等待组件测试。"""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from framework.keywords import keywords


class WaitContainsLinesTest(unittest.TestCase):
    def test_waits_until_all_expected_lines_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "received.txt"

            def write_later():
                time.sleep(0.03)
                path.write_text("publisher 2\n", encoding="utf-8")
                time.sleep(0.03)
                path.write_text(
                    "publisher 2\npublisher 1\npublisher 3\n",
                    encoding="utf-8",
                )

            writer = threading.Thread(target=write_later)
            writer.start()
            keywords.call(
                "fs_wait_contains_lines",
                str(path),
                ["publisher 1", "publisher 2", "publisher 3"],
                0.5,
            )
            writer.join()

    def test_reports_missing_lines_after_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "received.txt"
            with self.assertRaisesRegex(TimeoutError, "publisher 3"):
                keywords.call(
                    "fs_wait_contains_lines",
                    str(path),
                    ["publisher 3"],
                    0.01,
                )


if __name__ == "__main__":
    unittest.main()
