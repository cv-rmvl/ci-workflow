"""终端日志分段着色，日志文件保持纯文本。"""

import contextlib
import io
from pathlib import Path
import re
import tempfile
import unittest

from framework.logger import Logger


class LoggerTest(unittest.TestCase):
    def test_terminal_colors_and_plain_log_file(self):
        methods = {"info": ("info", "0"), "success": ("pass", "32"),
                   "warning": ("warn", "33"), "error": ("erro", "31"), "skip": ("skip", "90")}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.log"
            logger = Logger(path)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                for method in methods:
                    getattr(logger, method)("first\nsecond", source="test.run")
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), 10)
            for index, (level, color) in enumerate(methods.values()):
                for line in lines[index * 2:index * 2 + 2]:
                    self.assertTrue(line.startswith("\033[90m["))
                    self.assertIn(f"\033[{color}m[{level}]\033[0m", line)
                    self.assertIn("\033[34m[test.run]\033[0m", line)
            plain = path.read_text(encoding="utf-8")
            self.assertNotIn("\033[", plain)
            self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue()), plain)
