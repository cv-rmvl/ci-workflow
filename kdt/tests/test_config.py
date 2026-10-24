"""KDT 运行时全局配置测试，不依赖本机已编译的 Adapter。"""

from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from framework import kdt_config, generic


class KdtConfigTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.bin = Path(self.temporary.name)
        patcher = patch.object(generic, "_ADAPTER_BIN", self.bin)
        patcher.start()
        self.addCleanup(patcher.stop)

    def adapter(self, system="Linux"):
        with patch.object(generic.platform, "system", return_value=system):
            value = generic.KdtConfig()
        target = self.bin / ("kdt.config.ext.exe" if value.system == "win" else "kdt.config.ext")
        target.touch()
        return value, target

    def test_system_names_and_import_do_not_start_process(self):
        with patch.object(generic.subprocess, "run") as run:
            for system, expected in [("Windows", "win"), ("Linux", "linux"),
                                     ("Darwin", "macos"), ("FreeBSD", "freebsd"), ("", "unknown")]:
                with self.subTest(system=system), patch.object(generic.platform, "system", return_value=system):
                    self.assertEqual(generic.KdtConfig().system, expected)
            run.assert_not_called()

    def test_missing_adapter_is_unknown_and_not_cached(self):
        value = generic.KdtConfig()
        with patch.object(generic.subprocess, "run") as run:
            self.assertIsNone(value.cpp_standard)
            run.assert_not_called()
        (self.bin / ("kdt.config.ext.exe" if value.system == "win" else "kdt.config.ext")).touch()
        with patch.object(generic.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"cpp_standard":23}\n', "")):
            self.assertEqual(value.cpp_standard, 23)

    def test_actual_adapter_standard_and_windows_executable(self):
        for system in ("Windows", "Linux", "Darwin"):
            for standard in (17, 20, 23, 26):
                with self.subTest(system=system, standard=standard):
                    value, target = self.adapter(system)
                    response = subprocess.CompletedProcess([], 0, json.dumps({"cpp_standard": standard}) + "\r\n", "")
                    with patch.object(generic.subprocess, "run", return_value=response) as run:
                        self.assertEqual(value.cpp_standard, standard)
                        self.assertEqual(run.call_args.args[0], [str(target)])
                        self.assertEqual(run.call_args.kwargs["timeout"], 5)

    def test_standard_is_cached_until_refresh(self):
        value, _ = self.adapter()
        responses = [subprocess.CompletedProcess([], 0, '{"cpp_standard":17}', ""),
                     subprocess.CompletedProcess([], 0, '{"cpp_standard":23}', "")]
        with patch.object(generic.subprocess, "run", side_effect=responses) as run:
            self.assertEqual(value.cpp_standard, 17)
            self.assertEqual(value.cpp_standard, 17)
            run.assert_called_once()
            value.refresh()
            self.assertEqual(value.cpp_standard, 23)
            self.assertEqual(run.call_count, 2)

    def test_query_failures_and_invalid_output_are_not_guessed_or_cached(self):
        value, _ = self.adapter()
        for response in [subprocess.CompletedProcess([], 1, "", "query failed"),
                         subprocess.CompletedProcess([], 0, "C++23", ""),
                         subprocess.CompletedProcess([], 0, "", ""),
                         subprocess.CompletedProcess([], 0, "23\n20", ""),
                         subprocess.CompletedProcess([], 0, "23", ""),
                         subprocess.CompletedProcess([], 0, "[]", ""),
                         subprocess.CompletedProcess([], 0, "null", ""),
                         subprocess.CompletedProcess([], 0, "{}", ""),
                         subprocess.CompletedProcess([], 0, '{"cpp_standard":"23"}', ""),
                         subprocess.CompletedProcess([], 0, '{"cpp_standard":true}', ""),
                         subprocess.CompletedProcess([], 0, '{"cpp_standard":23.0}', ""),
                         subprocess.CompletedProcess([], 0, '{"cpp_standard":99}', "")]:
            with self.subTest(response=response), patch.object(generic.subprocess, "run", return_value=response):
                with self.assertRaises(RuntimeError):
                    _ = value.cpp_standard
        for error in (OSError("cannot execute"), subprocess.TimeoutExpired("kdt.config.ext", 5)):
            with self.subTest(error=error), patch.object(generic.subprocess, "run", side_effect=error):
                with self.assertRaises(type(error)):
                    _ = value.cpp_standard

    def test_extension_fields_do_not_override_python_environment(self):
        value, _ = self.adapter("Linux")
        response = subprocess.CompletedProcess([], 0, '{"cpp_standard":23,"system":"win"}', "")
        with patch.object(generic.subprocess, "run", return_value=response):
            self.assertEqual(value.cpp_standard, 23)
            self.assertEqual(value.system, "linux")

    def test_global_object_is_shared_and_fields_are_read_only(self):
        self.assertIs(kdt_config, generic.kdt_config)
        for field, value in (("system", "win"), ("cpp_standard", 17)):
            with self.assertRaises(AttributeError):
                setattr(kdt_config, field, value)
