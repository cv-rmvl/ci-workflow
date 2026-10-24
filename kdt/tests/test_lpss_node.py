"""节点查询数据由 Adapter 提供，存在性断言由组件完成。"""

import subprocess
import unittest
from unittest.mock import patch

from components.func.lpss import node
from components.func.lpss.base import LpssQueryAdapter
from framework.exceptions import KeywordSkipped


class LpssNodeTest(unittest.TestCase):
    def test_check_matches_complete_names_in_list(self):
        with patch.object(node, "_run", return_value="other\r\ntarget\r\n") as run:
            node.check("target", True)
        run.assert_called_once_with(LpssQueryAdapter.TOOL, ["node", "list", "--domain", "0"], timeout=5)

    def test_substring_is_not_a_node_match(self):
        with patch.object(node, "_run", return_value="target_extra\nother_target\n"):
            node.check("target", False)
            with self.assertRaises(AssertionError):
                node.check("target", True)

    def test_empty_list_means_not_found(self):
        with patch.object(node, "_run", return_value=""):
            node.check("target", False)

    def test_found_node_fails_absence_assertion(self):
        with patch.object(node, "_run", return_value="target\n"):
            with self.assertRaises(AssertionError):
                node.check("target", False)

    def test_query_errors_are_not_treated_as_absence(self):
        errors = [RuntimeError("query failed"), subprocess.TimeoutExpired("lpss.tool", 5),
                  KeywordSkipped("requires C++20")]
        for error in errors:
            with self.subTest(error=type(error).__name__), patch.object(node, "_run", side_effect=error):
                with self.assertRaises(type(error)):
                    node.check("target", False)

    def test_count_still_uses_guid_count(self):
        with patch.object(node, "_run", return_value="2\n") as run:
            self.assertEqual(node.count(), 2)
        run.assert_called_once_with(LpssQueryAdapter.TOOL, ["node", "list", "-c", "--domain", "0"], timeout=5)

    def test_queries_use_the_requested_domain(self):
        for domain in (0, 42, 255):
            with self.subTest(domain=domain), patch.object(node, "_run", return_value="2\n") as run:
                self.assertEqual(node.count(domain), 2)
                run.assert_called_once_with(LpssQueryAdapter.TOOL,
                                           ["node", "list", "-c", "--domain", str(domain)], timeout=5)
            with self.subTest(domain=domain), patch.object(node, "_run", return_value="target\n") as run:
                node.check("target", True, domain)
                run.assert_called_once_with(LpssQueryAdapter.TOOL,
                                           ["node", "list", "--domain", str(domain)], timeout=5)

    def test_invalid_domains_do_not_start_adapter(self):
        for domain in (-1, 256, True, False, 1.0, "42", None):
            with self.subTest(domain=domain), patch.object(node, "_run") as run:
                with self.assertRaises((TypeError, ValueError)):
                    node.count(domain)
                with self.assertRaises((TypeError, ValueError)):
                    node.check("target", False, domain)
                run.assert_not_called()

    def test_registry_preserves_omitted_domain_defaults(self):
        from framework.keywords import keywords

        with patch.object(node, "_run", return_value="0\n"):
            self.assertEqual(keywords.call("lpss_node_count"), 0)
        with patch.object(node, "_run", return_value=""):
            keywords.call("lpss_node_check", "target", False)

    def test_invalid_arguments_do_not_query_adapter(self):
        with patch.object(node, "_run") as run:
            for name, expected in [("", False), ("a\0b", True), ("a" * 256, True), (None, True), ("target", 1)]:
                with self.subTest(name=name, expected=expected), self.assertRaises((TypeError, ValueError)):
                    node.check(name, expected)
            run.assert_not_called()
