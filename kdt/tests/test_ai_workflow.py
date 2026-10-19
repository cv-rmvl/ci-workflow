"""数字测试员的状态、边界和真实验证结果测试，不调用在线模型。"""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


_SPEC = importlib.util.spec_from_file_location(
    "kdt_ai_worker", Path(__file__).resolve().parents[1] / "agents/ai_worker.py"
)
ai = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ai)
_QUOTA_SPEC = importlib.util.spec_from_file_location(
    "kdt_ai_quota", Path(__file__).resolve().parents[1] / "agents/ai_quota.py"
)
quota = importlib.util.module_from_spec(_QUOTA_SPEC)
_QUOTA_SPEC.loader.exec_module(quota)


class AiWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "tmp/ai"
        self.directory.mkdir(parents=True)
        self.output = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def workflow(self, provider=None):
        return ai.Workflow(self.root, {"directory": str(self.directory), "prompt": "KDT"}, provider)

    def test_phase_cannot_skip_boundary(self):
        workflow = self.workflow()
        workflow.transition("analysis")
        with self.assertRaises(ValueError):
            workflow.transition("coding")
        self.assertEqual(workflow.phase, "analysis")

    def test_consultation_does_not_apply_suggested_files(self):
        workflow = self.workflow(lambda: {"category": "consultation", "message": "说明", "files": [{"path": "components/x.py", "content": "pass"}]})
        with self.assertRaises(ValueError):
            workflow.run()
        self.assertFalse((self.root / "components/x.py").exists())

    def test_external_change_and_out_of_bounds_are_rejected(self):
        workflow = self.workflow()
        for path in ("../x.py", "/tmp/x.py", "framework/x.py", "adapter/build/x.py",
                     "../.agents/skills/x.py", ".agents/skills/x.py", "kdt/components/x.py"):
            with self.assertRaises(ValueError):
                workflow.target(path)
        workflow.category = "development"
        workflow.allowed = {"components/x.py"}
        workflow.expected["components/x.py"] = None
        target = self.root / "components/x.py"
        target.parent.mkdir()
        target.write_text("# user's new work\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            workflow.apply([{"path": "components/x.py", "content": "pass\n"}])
        self.assertIn("user's", target.read_text())

    def test_codex_workspace_is_parent_but_file_paths_remain_kdt_relative(self):
        root = self.root / "ci-workflow/kdt"
        root.mkdir(parents=True)
        workflow = ai.Workflow(root, {"directory": str(self.directory), "prompt": "KDT"})
        response = {"category": "consultation", "message": "说明"}

        def stream(command, cwd, cancel, **kwargs):
            self.assertEqual(cwd, root.parent)
            self.assertEqual(command[command.index("-C") + 1], str(root.parent))
            self.assertIn("--search", command)
            self.assertIn('default_permissions="kdt_readonly_network"', command)
            self.assertIn('permissions.kdt_readonly_network.extends=":read-only"', command)
            self.assertIn("permissions.kdt_readonly_network.network.enabled=true", command)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
            self.assertIn(str(root), kwargs["stdin"])
            self.assertIn(".agents/skills", kwargs["stdin"])
            self.assertIn("不要添加 kdt/ 前缀", kwargs["stdin"])
            result = Path(command[command.index("--output-last-message") + 1])
            result.write_text(json.dumps(response), encoding="utf-8")
            return iter(())

        with patch.object(ai.shutil, "which", return_value="codex"), patch.object(ai, "run_stream", side_effect=stream) as run:
            self.assertEqual(workflow.codex(), response)
            run.assert_called_once()
        self.assertEqual(workflow.target("components/x.py"), root / "components/x.py")

    def test_task_model_and_effort_override_local_defaults(self):
        workflow = ai.Workflow(self.root, {"directory": str(self.directory), "prompt": "KDT",
                                         "model": "selected-model", "effort": "high"})

        def stream(command, cwd, cancel, **kwargs):
            self.assertEqual(command[command.index("--model") + 1], "selected-model")
            self.assertIn('model_reasoning_effort="high"', command)
            Path(command[command.index("--output-last-message") + 1]).write_text('{}', encoding="utf-8")
            return iter(())

        with patch.object(ai.shutil, "which", return_value="codex"), \
                patch.dict(ai.os.environ, {"KDT_AI_MODEL": "environment-model"}), \
                patch.object(ai, "run_stream", side_effect=stream):
            workflow.codex()

    def test_invalid_model_and_effort_do_not_start_codex(self):
        for options in ({"model": "model; touch /tmp/x"}, {"effort": "unsupported"}):
            workflow = ai.Workflow(self.root, {"directory": str(self.directory), "prompt": "KDT", **options})
            with patch.object(ai.shutil, "which", return_value="codex"), patch.object(ai, "run_stream") as run:
                with self.assertRaises(ValueError):
                    workflow.codex()
                run.assert_not_called()

    def test_repair_loop_preserves_phase_order(self):
        workflow = self.workflow()
        attempts = []
        def provider():
            attempts.append(workflow.phase)
            return {"category": "development", "message": "阶段说明", "editable_files": [],
                    "files": [], "functional_cases": [], "approved": attempts.count("review") > 1}
        workflow.provider = provider
        def validate(_):
            workflow.dryrun_ok = True
        workflow.validate = validate
        workflow.run()
        self.assertEqual(attempts, ["analysis", "boundary", "coding", "review", "coding", "review"])
        events = [json.loads(line) for line in self.output.getvalue().splitlines()]
        self.assertEqual([event["phase"] for event in events if event["type"] == "phase"],
                         ["analysis", "boundary", "coding", "dryrun", "review", "coding", "dryrun", "review", "completed"])

    def test_dryrun_uses_real_pass_fail_skip_counts(self):
        (self.root / "tests").mkdir()
        (self.root / "tests/test_sample.py").write_text(
            "import unittest\nclass Sample(unittest.TestCase):\n"
            " def test_pass(self): self.assertTrue(True)\n"
            " @unittest.skip('environment')\n def test_skip(self): pass\n", encoding="utf-8")
        workflow = self.workflow()
        results = workflow.validate([])
        self.assertEqual(results["regression"], {"total": 2, "passed": 1, "failed": 0, "skipped": 1})
        self.assertFalse(workflow.dryrun_ok)  # 未执行功能测试时不可宣称开发验证通过。

    def test_cancel_prevents_next_phase_execution(self):
        workflow = self.workflow(lambda: self.fail("Cancelled task must not call provider"))
        workflow.cancel.touch()
        with self.assertRaises(InterruptedError):
            workflow.run()

    def test_no_fixed_token_budget_stops_the_task(self):
        workflow = self.workflow(lambda: {"category": "consultation", "message": "KDT 说明", "files": []})
        workflow.usage = 10 ** 9
        workflow.run()
        self.assertEqual(workflow.phase, "completed")


class CodexQuotaTest(unittest.TestCase):
    def test_configured_default_missing_from_catalog_can_select_effort(self):
        with patch.dict(quota.os.environ, {"KDT_AI_MODEL": ""}):
            result = quota.normalize_models(
                [{"model": "listed-model", "isDefault": True,
                  "supportedReasoningEfforts": [{"reasoningEffort": "low"}]}],
                {"model": "configured-model", "model_reasoning_effort": "medium"})
        self.assertEqual(result["defaultModel"], "configured-model")
        selected = next(item for item in result["models"] if item["value"] == result["defaultModel"])
        self.assertIn("high", selected["efforts"])
        self.assertEqual(selected["defaultEffort"], "medium")
        self.assertFalse(selected["effortsVerified"])
        self.assertEqual(result["models"][0]["efforts"], ["low"])

    def test_environment_default_missing_from_catalog_takes_precedence(self):
        with patch.dict(quota.os.environ, {"KDT_AI_MODEL": "environment-model"}):
            result = quota.normalize_models([], {"model": "configured-model"})
        self.assertEqual(result["defaultModel"], "environment-model")
        self.assertEqual(result["models"][0]["value"], "environment-model")
        self.assertFalse(result["models"][0]["effortsVerified"])

    def test_unavailable_quota_does_not_block_model_catalog(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(quota, "CodexQuotaClient") as factory:
            stop = Path(temporary) / "stop"
            client = factory.return_value
            client.process.poll.return_value = None
            client.request.side_effect = RuntimeError("quota unavailable")
            client.catalog.return_value = {"models": [{"value": "available-model"}]}
            events = []

            def emit(event, **fields):
                events.append((event, fields))
                if event == "catalog":
                    stop.touch()

            quota.watch_quota(stop, emit)
            self.assertEqual([event for event, _ in events], ["quota", "catalog"])
            self.assertEqual(events[-1][1]["catalog"]["models"][0]["value"], "available-model")
            client.close.assert_called_once()

    def test_model_catalog_filters_hidden_and_deduplicates_without_exposing_config(self):
        models = [{"model": "test-model", "displayName": "Test Model", "isDefault": True,
                   "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [
                       {"reasoningEffort": "medium"}, {"reasoningEffort": "high"}, {"reasoningEffort": "high"}]},
                  {"model": "hidden-model", "hidden": True}, {"model": "test-model"}]
        with patch.dict(quota.os.environ, {"KDT_AI_MODEL": ""}):
            result = quota.normalize_models(models, {"model_reasoning_effort": "high", "secret": "private"})
        self.assertEqual(result["defaultModel"], "test-model")
        self.assertEqual(result["defaultEffort"], "high")
        self.assertEqual(result["models"], [{"value": "test-model", "label": "Test Model",
                                            "efforts": ["medium", "high"], "defaultEffort": "medium"}])
        self.assertNotIn("private", json.dumps(result))

    def test_model_catalog_reads_all_pages_and_effective_workspace_config(self):
        client = object.__new__(quota.CodexQuotaClient)
        with patch.dict(quota.os.environ, {"KDT_AI_MODEL": ""}), patch.object(client, "request", side_effect=[
            {"data": [{"model": "first"}], "nextCursor": "next"},
            {"data": [{"model": "second"}], "nextCursor": None},
            {"config": {"model": "second", "model_reasoning_effort": "low"}},
        ]) as request:
            result = client.catalog()
        self.assertEqual([item["value"] for item in result["models"]], ["first", "second"])
        self.assertEqual(result["defaultModel"], "second")
        self.assertEqual(request.call_args_list[1].args[1]["cursor"], "next")
        self.assertEqual(request.call_args_list[2].args[1]["cwd"], str(Path(quota.__file__).resolve().parents[2]))

    def test_lowest_remaining_window_is_the_effective_limit(self):
        result = quota.normalize_quota({"rateLimits": {
            "primary": {"usedPercent": 30, "windowDurationMins": 300, "resetsAt": 100},
            "secondary": {"usedPercent": 90, "windowDurationMins": 10080, "resetsAt": 200},
        }})
        self.assertEqual(result["remainingPercent"], 10)
        self.assertEqual([window["remainingPercent"] for window in result["windows"]], [70, 10])

    def test_unknown_is_not_replaced_with_a_fake_budget(self):
        result = quota.normalize_quota({})
        self.assertIsNone(result["remainingPercent"])
        self.assertEqual(result["windows"], [])

    def test_multi_bucket_codex_values_take_precedence(self):
        result = quota.normalize_quota({
            "rateLimits": {"primary": {"usedPercent": 0}},
            "rateLimitsByLimitId": {"codex": {"primary": {"usedPercent": 100}}},
        })
        self.assertEqual(result["remainingPercent"], 0)

    def test_non_finite_and_boolean_percentages_are_ignored(self):
        result = quota.normalize_quota({"rateLimits": {
            "primary": {"usedPercent": float("nan")}, "secondary": {"usedPercent": False},
        }})
        self.assertIsNone(result["remainingPercent"])


if __name__ == "__main__":
    unittest.main()
