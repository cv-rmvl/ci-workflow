"""Runner 步骤返回值传递测试。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from framework import runner


class RunnerResultTest(unittest.TestCase):
    def test_real_components_can_assert_slice_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            output = root / "line.txt"
            case_file = root / "result.yml"
            case_file.write_text(
                yaml.safe_dump(
                    {
                        "info": {
                            "name": "真实组件返回值测试",
                            "input": "第二行文本",
                            "expected": ["返回第二行", "断言通过"],
                        },
                        "steps": [
                            {
                                "name": "fs_write",
                                "args": [str(source), "first\nsecond\n"],
                            },
                            {
                                "name": "fs_slice",
                                "args": [str(source), 2, str(output)],
                            },
                            {
                                "name": "assert_equal",
                                "args": ["{{ step2 }}", "second"],
                            },
                        ],
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            with patch.object(runner, "_CASES_DIR", root):
                runner.run_case("result")

            self.assertEqual(output.read_text(encoding="utf-8"), "second\n")

    def test_result_can_be_used_by_later_step(self):
        calls = []

        def call(name, *args):
            calls.append((name, args))
            if name == "produce":
                return {"value": 7}
            return None

        with tempfile.TemporaryDirectory() as directory:
            case_file = Path(directory) / "result.yml"
            case_file.write_text(
                """info: 返回值传递测试
steps:
  - name: produce
  - name: consume
    args:
      - "{{ step1 }}"
""",
                encoding="utf-8",
            )
            with patch.object(runner, "_CASES_DIR", Path(directory)), patch.object(
                runner.keywords, "call", side_effect=call
            ):
                runner.run_case("result")

        self.assertEqual(calls[1], ("consume", ({"value": 7},)))

    def test_failed_step_result_is_not_available(self):
        def call(name, *args):
            if name == "produce":
                raise RuntimeError("failed")

        with tempfile.TemporaryDirectory() as directory:
            case_file = Path(directory) / "result.yml"
            case_file.write_text(
                """info: 返回值失败测试
steps:
  - name: produce
  - name: consume
    args:
      - "{{ step1 }}"
""",
                encoding="utf-8",
            )
            with patch.object(runner, "_CASES_DIR", Path(directory)), patch.object(
                runner.keywords, "call", side_effect=call
            ):
                with self.assertRaises(runner.CaseExecutionError) as context:
                    runner.run_case("result")

        self.assertEqual(len(context.exception.failures), 2)
        self.assertIn("unavailable", str(context.exception.failures[1][2]))


if __name__ == "__main__":
    unittest.main()
