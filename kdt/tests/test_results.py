"""步骤返回值缓存与引用解析测试。"""

import unittest

from framework.results import StepResultCache, StepResultReferenceError


class StepResultCacheTest(unittest.TestCase):
    def setUp(self):
        self.results = StepResultCache()
        self.results.store(1, 42)
        self.results.store(2, ["a", "b"])
        self.results.store(3, None)

    def test_exact_reference_preserves_python_type(self):
        self.assertEqual(self.results.resolve_arguments("{{ step1 }}", 4), 42)
        self.assertEqual(
            self.results.resolve_arguments("{{step2}}", 4), ["a", "b"]
        )
        self.assertIsNone(self.results.resolve_arguments(" {{ step3 }} ", 4))

    def test_reference_is_resolved_recursively(self):
        resolved = self.results.resolve_arguments(
            ["value={{ step1 }}", {"nested": "{{ step2 }}"}], 4
        )
        self.assertEqual(resolved, ["value=42", {"nested": ["a", "b"]}])

    def test_future_and_unavailable_results_are_rejected(self):
        with self.assertRaisesRegex(StepResultReferenceError, "only completed"):
            self.results.resolve_arguments("{{ step4 }}", 4)
        with self.assertRaisesRegex(StepResultReferenceError, "unavailable"):
            self.results.resolve_arguments("{{ step9 }}", 10)

    def test_complex_result_cannot_be_embedded_in_text(self):
        with self.assertRaisesRegex(StepResultReferenceError, "entire argument"):
            self.results.resolve_arguments("items={{ step2 }}", 4)


if __name__ == "__main__":
    unittest.main()
