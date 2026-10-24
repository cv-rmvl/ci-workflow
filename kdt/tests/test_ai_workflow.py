"""数字测试员的状态、边界和真实验证结果测试，不调用在线模型。"""

import contextlib
import importlib.util
import io
import json
import shutil
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
        self.root = Path(self.temporary.name) / "kdt"
        self.root.mkdir()
        self.directory = self.root / "tmp/ai"
        self.directory.mkdir(parents=True)
        self.output = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def workflow(self, provider=None):
        return ai.Workflow(self.root, {"directory": str(self.directory), "prompt": "KDT"}, provider)

    def test_summary_is_limited_to_forty_words_without_splitting_identifiers(self):
        text = " ".join(["lpss.tool-C++17" + str(index) for index in range(41)])
        summary = ai.concise_summary(text)
        self.assertEqual(len(summary.rstrip("…").split()), 40)
        self.assertTrue(summary.endswith("lpss.tool-C++1739…"))
        self.assertEqual(ai.concise_summary("支持 C++17~C++23 的 lpss.tool 六项查询"), "支持 C++17~C++23 的 lpss.tool 六项查询")
        self.assertEqual(ai.concise_summary("测" * 41), "测" * 40 + "…")
        self.assertEqual(ai.concise_summary("  任务\n 摘要  "), "任务 摘要")

    def test_questions_are_concise_current_items_not_an_accumulated_history(self):
        answers = iter([
            {"category": "consultation", "message": "说明", "questions": [" 待确认路径 ", "待确认路径", " "]},
            {"category": "consultation", "message": "已解决", "questions": []},
        ])
        workflow = self.workflow(lambda: next(answers))
        workflow.phase = "analysis"
        workflow.ask()
        self.assertEqual(workflow.questions, ["待确认路径"])
        workflow.ask()
        self.assertEqual(workflow.questions, [])

    def test_optional_conclusion_is_published_on_consultation_completion(self):
        workflow = self.workflow(lambda: {"category": "consultation", "message": "说明", "conclusion": "  最终结论  "})
        workflow.run()
        events = list(map(json.loads, self.output.getvalue().splitlines()))
        conclusions = [event for event in events if event["type"] == "conclusion"]
        self.assertEqual(conclusions, [{"type": "conclusion", "text": "最终结论"}])
        self.assertEqual(events[-1], {"type": "log", "level": "info", "text": "[ai.workflow] completed"})

    def test_empty_conclusion_does_not_publish_placeholder(self):
        workflow = self.workflow(lambda: {"category": "consultation", "message": "说明", "conclusion": " \n "})
        workflow.run()
        self.assertFalse(any(event["type"] == "conclusion" for event in map(json.loads, self.output.getvalue().splitlines())))
        with self.assertRaises(ValueError):
            workflow.publish_conclusion({"conclusion": []})

    def test_only_successful_final_review_publishes_development_conclusion(self):
        workflow = self.workflow()
        attempts = []

        def provider():
            attempts.append(workflow.phase)
            return {"category": "development", "message": "阶段说明", "acceptance_checks": ["新功能"],
                    "editable_files": [], "files": [], "functional_cases": [],
                    "conclusion": "最终验证完成" if workflow.phase == "review" else "中间阶段不能作为结论",
                    "blocking_issues": ["待修复"] if workflow.phase == "review" and attempts.count("review") == 1 else []}

        def validate(_):
            workflow.dryrun_ok = True

        workflow.provider, workflow.validate = provider, validate
        workflow.run()
        conclusions = [event["text"] for event in map(json.loads, self.output.getvalue().splitlines()) if event["type"] == "conclusion"]
        self.assertEqual(conclusions, ["最终验证完成"])

    def test_nested_codex_error_is_preserved_instead_of_only_exit_code(self):
        workflow = self.workflow()
        message = "The selected model is not supported for this login method."
        raw = {"type": "error", "status": 400, "error": {"type": "invalid_request_error", "message": message}}

        def stream(*args, **kwargs):
            yield json.dumps(raw)
            yield json.dumps({"type": "turn.failed", "error": {"message": json.dumps(raw)}})
            raise ai.ProcessExitError(1)

        with patch.object(ai, "run_stream", side_effect=stream):
            with self.assertRaises(RuntimeError) as raised:
                list(workflow.codex_events(["codex"], "KDT"))
        self.assertEqual(str(raised.exception), message + "\n\nCodex CLI 退出码：1")
        events = [json.loads(line) for line in self.output.getvalue().splitlines()]
        self.assertEqual([event["text"] for event in events if event["type"] == "log"], [message])

    def test_codex_error_is_failure_even_when_process_returns_zero(self):
        workflow = self.workflow()
        with patch.object(ai, "run_stream", return_value=iter([json.dumps({"type": "error", "message": "rejected"})])):
            with self.assertRaisesRegex(RuntimeError, "rejected"):
                list(workflow.codex_events(["codex"], "KDT"))

    def test_public_progress_and_commands_are_forwarded_without_reasoning(self):
        workflow = self.workflow()
        workflow.phase = "analysis"
        command = {"id": "cmd1", "type": "command_execution", "command": "rg test", "status": "in_progress"}
        events = [
            {"type": "item.started", "item": command},
            {"type": "item.updated", "item": {**command, "aggregated_output": "partial"}},
            {"type": "item.completed", "item": {**command, "aggregated_output": "full output", "exit_code": 0}},
            {"type": "item.completed", "item": {**command, "aggregated_output": "full output", "exit_code": 0}},
            {"type": "item.completed", "item": {"id": "msg", "type": "agent_message", "text": "正在核对参数类型"}},
            {"type": "item.completed", "item": {"id": "hidden", "type": "reasoning", "text": "private reasoning"}},
            {"type": "item.completed", "item": {"id": "final", "type": "agent_message", "text": json.dumps({"category": "consultation", "message": "阶段完成", "files": []})}},
        ]
        with patch.object(ai, "run_stream", return_value=iter(map(json.dumps, events))):
            list(workflow.codex_events(["codex"], "KDT"))
        output = [json.loads(line) for line in self.output.getvalue().splitlines()]
        self.assertEqual([e["text"] for e in output if e["type"] == "log"], ["rg test", "full output"])
        progress = [e for e in output if e["type"] == "progress"]
        self.assertEqual(progress[0]["id"], progress[1]["id"])
        self.assertEqual(progress[0]["status"], "running")
        self.assertEqual(progress[2]["status"], "completed")
        self.assertEqual(progress[-1]["text"], "正在核对参数类型")
        self.assertNotIn("private reasoning", self.output.getvalue())
        self.assertNotIn("阶段完成", self.output.getvalue())
        self.assertTrue(any(e["type"] == "activity" for e in output))

    def test_command_dedup_is_by_item_and_call_not_command_text(self):
        workflow = self.workflow()
        events = [{"type": "item.completed", "item": {"id": id, "type": "command_execution", "command": "ls", "exit_code": 2}}
                  for id in ("a", "b")]
        for _ in range(2):
            with patch.object(ai, "run_stream", return_value=iter(map(json.dumps, events))):
                list(workflow.codex_events(["codex"], "KDT"))
        output = [json.loads(line) for line in self.output.getvalue().splitlines()]
        progress = [e for e in output if e["type"] == "progress"]
        self.assertEqual(len({e["id"] for e in progress}), 4)
        self.assertTrue(all(e["status"] == "failed" for e in progress))
        self.assertEqual(len([e for e in output if e["type"] == "log"]), 4)

    def test_plan_search_and_mcp_only_forward_safe_fields(self):
        workflow = self.workflow()
        items = [
            {"id": "plan", "type": "todo_list", "items": [{"text": "读取组件", "completed": True}, {"text": "检查用例", "completed": False}]},
            {"id": "search", "type": "web_search", "action": {"query": "RMVL 文档"}},
            {"id": "mcp", "type": "mcp_tool_call", "server": "docs", "tool": "search", "arguments": {"secret": "hidden payload"}},
        ]
        with patch.object(ai, "run_stream", return_value=iter(json.dumps({"type": "item.updated", "item": item}) for item in items)):
            list(workflow.codex_events(["codex"], "KDT"))
        progress = [e for e in map(json.loads, self.output.getvalue().splitlines()) if e["type"] == "progress"]
        self.assertEqual(progress[0]["steps"][0]["status"], "completed")
        self.assertEqual(progress[0]["steps"][1]["status"], "pending")
        self.assertEqual(progress[1]["detail"], "RMVL 文档")
        self.assertEqual(progress[2]["detail"], "docs / search")
        self.assertNotIn("hidden payload", self.output.getvalue())

    def test_dryrun_progress_marks_failed_command(self):
        workflow = self.workflow()
        workflow.phase = "dryrun"
        with patch.object(ai, "run_stream", side_effect=ai.ProcessExitError(1)):
            with self.assertRaises(ai.ProcessExitError):
                list(workflow.execute(["cmake", "--build", "build"], 10, "构建 Adapter"))
        progress = [json.loads(line) for line in self.output.getvalue().splitlines()]
        self.assertEqual([e["status"] for e in progress], ["running", "failed"])
        self.assertEqual(progress[0]["id"], progress[1]["id"])

    def test_process_exit_without_error_response_still_reports_exit_code(self):
        workflow = self.workflow()
        with patch.object(ai, "run_stream", side_effect=ai.ProcessExitError(9)):
            with self.assertRaises(ai.ProcessExitError) as raised:
                list(workflow.codex_events(["codex"], "KDT"))
        self.assertEqual(raised.exception.code, 9)

    def test_runtime_diagnostics_do_not_expose_environment_credentials(self):
        with patch.object(ai.subprocess, "run") as run, patch.dict(ai.os.environ, {"CODEX_API_KEY": "private-key"}):
            run.return_value.stdout = "codex-cli 1.2.3\n"
            runtime = ai.codex_runtime("/tmp/codex", self.root)
        self.assertEqual(runtime["version"], "codex-cli 1.2.3")
        self.assertNotIn("private-key", json.dumps(runtime))
        self.assertEqual(runtime["workspace"], str(self.root))

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
            self.assertIn("最多40词", kwargs["stdin"])
            self.assertIn("规范文件 agents/knowledge/analysis.md", kwargs["stdin"])
            self.assertNotIn("规范文件 agents/knowledge/review.md", kwargs["stdin"])
            self.assertIn("精简为1至2句话", kwargs["stdin"])
            self.assertIn("每条一句短句", kwargs["stdin"])
            result = Path(command[command.index("--output-last-message") + 1])
            result.write_text(json.dumps(response), encoding="utf-8")
            return iter(())

        with patch.object(ai.shutil, "which", return_value="codex"), patch.object(ai, "run_stream", side_effect=stream) as run:
            self.assertEqual(workflow.codex(), response)
            run.assert_called_once()
        self.assertEqual(workflow.target("components/x.py"), root / "components/x.py")

    def test_each_phase_loads_its_knowledge_and_reuses_dryrun_rules(self):
        workflow = self.workflow()
        expected = {"analysis": ["analysis.md"], "boundary": ["edge.md"],
                    "coding": ["coding.md", "dry-run.md"], "dryrun": ["dry-run.md"],
                    "review": ["review.md", "dry-run.md"]}
        for phase, names in expected.items():
            with self.subTest(phase=phase):
                workflow.phase = phase
                instructions = workflow.knowledge_instructions()
                for name in names:
                    self.assertIn((ai.KNOWLEDGE_DIRECTORY / name).read_text(encoding="utf-8").strip(), instructions)
                self.assertEqual(instructions.count("规范文件 agents/knowledge/"), len(names))

    def test_knowledge_is_reloaded_and_missing_or_empty_rules_fail_explicitly(self):
        workflow = self.workflow()
        workflow.phase = "analysis"
        knowledge = Path(self.temporary.name) / "knowledge"
        knowledge.mkdir()
        with patch.object(ai, "KNOWLEDGE_DIRECTORY", knowledge):
            with self.assertRaisesRegex(RuntimeError, "无法读取阶段规范"):
                workflow.knowledge_instructions()
            target = knowledge / "analysis.md"
            target.write_text(" \n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "阶段规范不能为空"):
                workflow.knowledge_instructions()
            target.write_text("第一版规范", encoding="utf-8")
            self.assertIn("第一版规范", workflow.knowledge_instructions())
            target.write_text("新版规范", encoding="utf-8")
            self.assertIn("新版规范", workflow.knowledge_instructions())
            self.assertNotIn("第一版规范", workflow.knowledge_instructions())

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
                    "acceptance_checks": ["新功能验收"],
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

    def test_process_corrections_do_not_consume_failure_allowance(self):
        workflow = self.workflow()
        reviews = []

        def provider():
            if workflow.phase == "review":
                reviews.append(True)
            correction = workflow.phase == "review" and len(reviews) <= 4
            return {"category": "development", "message": "阶段说明", "editable_files": [],
                    "acceptance_checks": ["新功能"], "files": [],
                    "blocking_issues": ["应通过新组件调用验证"] if correction else [],
                    "review_issue_kind": "dryrun_process" if correction else "none"}

        def validate(_):
            if len(reviews) < 4:
                raise ValueError("临时用例编排需要修正")
            workflow.dryrun_ok = True

        workflow.provider, workflow.validate = provider, validate
        workflow.run()
        self.assertEqual(len(reviews), 5)
        self.assertEqual(workflow.phase, "completed")
        self.assertEqual(self.output.getvalue().count("不计入失败次数"), 4)

    def test_actual_execution_failures_cannot_be_waived_as_process_advice(self):
        workflow = self.workflow()
        reviews = []

        def provider():
            if workflow.phase == "review":
                reviews.append(True)
            return {"category": "development", "message": "阶段说明", "editable_files": [],
                    "acceptance_checks": ["新功能"], "files": [],
                    "blocking_issues": ["调整测试流程"], "review_issue_kind": "dryrun_process"}

        def validate(_):
            workflow.dryrun_ok = False
            workflow.validation_failed = True

        workflow.provider, workflow.validate = provider, validate
        with self.assertRaisesRegex(RuntimeError, "累计三次"):
            workflow.run()
        self.assertEqual(len(reviews), 3)

    def test_implementation_review_failures_are_limited_to_three(self):
        workflow = self.workflow()
        reviews = []

        def provider():
            if workflow.phase == "review":
                reviews.append(True)
            return {"category": "development", "message": "阶段说明", "editable_files": [],
                    "acceptance_checks": ["新功能"], "files": [],
                    "blocking_issues": ["实现返回错误结果"], "review_issue_kind": "implementation"}

        def validate(_):
            workflow.dryrun_ok = True

        workflow.provider, workflow.validate = provider, validate
        with self.assertRaisesRegex(RuntimeError, "累计三次"):
            workflow.run()
        self.assertEqual(len(reviews), 3)

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

    def test_session_summary_has_stable_eight_digit_hash_and_isolated_workspace(self):
        workflow = self.workflow()
        workflow.set_session_summary("  工具查询功能  验证\n")
        self.assertEqual(workflow.summary, "工具查询功能 验证")
        expected = ai.hashlib.sha256((workflow.summary + "\0" + str(self.directory.resolve())).encode()).hexdigest()[:8]
        self.assertEqual(workflow.session_key, expected)
        self.assertEqual(workflow.session_dir, self.root.parent / "build" / ("ai_assisstant-" + expected))
        workflow.acceptance = ["node list", "node info"]
        workflow.prepare_session()
        self.assertIn("node list", (workflow.session_dir / "SESSION.md").read_text())
        other = ai.Workflow(self.root, {"directory": str(self.directory / "other"), "prompt": "KDT"})
        other.set_session_summary(workflow.summary)
        self.assertNotEqual(other.session_key, workflow.session_key)
        with self.assertRaises(ValueError):
            workflow.set_session_summary("不能更换已有会话")

    def test_session_does_not_reuse_existing_directory_on_hash_collision(self):
        workflow = self.workflow()
        workflow.session_dir.mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            workflow.prepare_session()

    def test_scratch_rejects_escape_and_consultation_writes(self):
        workflow = self.workflow()
        for relative in ("../outside.py", "/tmp/outside.py", "..\\outside.py", "."):
            with self.assertRaises(ValueError):
                workflow.scratch_target(relative)
        workflow.category = "consultation"
        with self.assertRaises(ValueError):
            workflow.apply_scratch([{"path": "check.py", "content": "pass"}])

    def test_scratch_metadata_and_tmp_do_not_follow_external_symlinks(self):
        workflow = self.workflow()
        workflow.prepare_session()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "target").write_text("user content", encoding="utf-8")
        try:
            (workflow.session_dir / "SESSION.md").unlink()
            (workflow.session_dir / "SESSION.md").symlink_to(outside / "target")
        except OSError:
            self.skipTest("环境不支持创建符号链接")
        with self.assertRaises(ValueError):
            workflow.prepare_session()
        self.assertEqual((outside / "target").read_text(), "user content")
        with self.assertRaises(ValueError):
            workflow.session_permissions()

    def test_coding_grants_only_current_session_write_permission(self):
        workflow = self.workflow()
        workflow.category, workflow.phase = "development", "coding"

        def stream(command, cwd, cancel, **kwargs):
            setting = next(value for value in command if value.startswith("permissions.kdt_readonly_network.filesystem="))
            self.assertEqual(setting, 'permissions.kdt_readonly_network.filesystem={' + json.dumps(str(workflow.session_dir), ensure_ascii=False) + '= "write"}')
            self.assertEqual(kwargs["env"]["TMPDIR"], str(workflow.session_dir / "tmp"))
            self.assertIn("构建并执行验证程序", kwargs["stdin"])
            Path(command[command.index("--output-last-message") + 1]).write_text('{}', encoding="utf-8")
            return iter(())

        with patch.object(ai.shutil, "which", return_value="codex"), patch.object(ai, "codex_runtime", return_value={}), \
                patch.object(ai, "run_stream", side_effect=stream):
            workflow.codex()
        with patch.object(ai.shutil, "which", return_value="codex"):
            command = workflow.session_command(["python", "check.py"])
        self.assertEqual(command[:2], ["codex", "sandbox"])
        self.assertEqual(command[command.index("--") + 1:], ["python", "check.py"])
        self.assertNotIn("danger-full-access", " ".join(command))

    def test_review_advice_is_not_a_blocker_but_real_issues_are(self):
        workflow = self.workflow()
        self.assertTrue(workflow.review_passed({"approved": False, "blocking_issues": [], "questions": ["建议补文档"]}))
        self.assertFalse(workflow.review_passed({"approved": True, "blocking_issues": ["新功能返回错误结果"]}))
        with self.assertRaises(ValueError):
            workflow.review_passed({"blocking_issues": "不是列表"})

    def test_development_requires_new_feature_acceptance_items(self):
        workflow = self.workflow(lambda: {"category": "development", "message": "分析", "acceptance_checks": []})
        with self.assertRaisesRegex(ValueError, "验收项"):
            workflow.run()

    def regression_result(self, command, timeout, label, **kwargs):
        if label == "运行回归测试":
            yield 'KDT_AI_TEST_RESULT:{"total":1,"passed":1,"failed":0,"skipped":0}'
        else:
            yield "真实退出结果由执行层测试覆盖，此处只测试调度分类"

    def test_existing_cases_and_description_only_changes_are_regressions(self):
        case = self.root / "cases/lpss/old.yml"
        case.parent.mkdir(parents=True)
        case.write_text('info: {name: old}\nsteps: [{name: assert_true, args: [true]}]\n', encoding="utf-8")
        workflow = self.workflow()
        case.write_text('info: {name: renamed}\nsteps: [{name: assert_true, args: [true]}]\n', encoding="utf-8")
        workflow.acceptance = ["新查询功能"]
        workflow.functional_checks = [{"name": "新查询功能", "case": "lpss.old"}]
        with patch.object(workflow, "execute", side_effect=self.regression_result):
            result = workflow.validate([])
        self.assertEqual(result["regression"]["passed"], 2)
        self.assertEqual(result["functional"]["passed"], 0)
        self.assertEqual(result["missingAcceptance"], ["新查询功能"])
        self.assertFalse(workflow.dryrun_ok)

    def test_modified_case_steps_are_functional_but_old_case_is_regression(self):
        case = self.root / "cases/feature.yml"
        case.parent.mkdir()
        case.write_text('info: feature\nsteps: []\n', encoding="utf-8")
        workflow = self.workflow()
        case.write_text('info: feature\nsteps: [{name: assert_true, args: [true]}]\n', encoding="utf-8")
        workflow.acceptance = ["本次功能"]
        workflow.functional_checks = [{"name": "本次功能", "case": "feature"}]
        with patch.object(workflow, "execute", side_effect=self.regression_result):
            result = workflow.validate([])
        self.assertEqual(result["functional"]["passed"], 1)
        self.assertTrue(workflow.dryrun_ok)

    def test_regression_crash_after_pass_marker_is_still_a_failure(self):
        workflow = self.workflow()

        def execute(command, timeout, label, **kwargs):
            yield from self.regression_result(command, timeout, label, **kwargs)
            raise ai.ProcessExitError(9)

        with patch.object(workflow, "execute", side_effect=execute):
            result = workflow.validate([])
        self.assertEqual(result["regression"], {"passed": 0, "failed": 1, "skipped": 0, "total": 1})
        self.assertFalse(workflow.dryrun_ok)

    def test_six_new_feature_checks_have_separate_evidence_and_missing_is_failure(self):
        workflow = self.workflow()
        workflow.category = "development"
        workflow.acceptance = [group + " " + action for group in ("node", "topic", "service") for action in ("list", "info")]
        workflow.prepare_session()
        workflow.apply_scratch([{"path": "verify.yml", "content": "info: 六项接口验证\nsteps: [{name: assert_true, args: [true]}]\n"}])
        workflow.functional_checks = [{"name": name, "yaml": "verify.yml"} for name in workflow.acceptance]
        with patch.object(workflow, "execute", side_effect=self.regression_result), patch.object(ai.shutil, "which", return_value="codex"):
            result = workflow.validate([])
            self.assertTrue(workflow.dryrun_ok)
            self.assertEqual(result["functional"], {"total": 6, "passed": 6, "failed": 0, "skipped": 0})
            self.assertEqual([check["name"] for check in result["checks"]], workflow.acceptance)
            workflow.functional_checks.pop()
            result = workflow.validate([])
        self.assertFalse(workflow.dryrun_ok)
        self.assertEqual(result["functional"]["failed"], 1)
        self.assertEqual(result["missingAcceptance"], ["service info"])
        self.assertTrue((workflow.session_dir / "dryrun.json").is_file())

    def test_skipped_new_feature_does_not_count_as_passed(self):
        workflow = self.workflow()
        workflow.prepare_session()
        workflow.apply_scratch([])
        workflow.scratch_target("verify.yml").write_text("info: 验证\nsteps: [{name: assert_true, args: [true]}]\n", encoding="utf-8")
        workflow.acceptance = ["新功能"]
        workflow.functional_checks = [{"name": "新功能", "yaml": "verify.yml"}]

        def execute(command, timeout, label, **kwargs):
            if label.startswith("验证新功能"):
                raise ai.ProcessExitError(77)
            yield from self.regression_result(command, timeout, label, **kwargs)

        with patch.object(workflow, "execute", side_effect=execute), patch.object(ai.shutil, "which", return_value="codex"):
            result = workflow.validate([])
        self.assertFalse(workflow.dryrun_ok)
        self.assertEqual(result["functional"]["skipped"], 1)
        self.assertEqual(result["functional"]["passed"], 0)

    def test_adapter_build_failure_does_not_run_functional_tests(self):
        workflow = self.workflow()
        workflow.prepare_session()
        workflow.scratch_target("verify.yml").write_text("info: 验证\nsteps: [{name: assert_true, args: [true]}]\n", encoding="utf-8")
        workflow.changed = {"adapter/src/new.cpp": {"path": "adapter/src/new.cpp", "status": "added"}}
        workflow.acceptance = ["新功能"]
        workflow.functional_checks = [{"name": "新功能", "yaml": "verify.yml"}]
        labels = []

        def execute(command, timeout, label, **kwargs):
            labels.append(label)
            if label == "构建 Adapter":
                raise ai.ProcessExitError(1)
            yield from self.regression_result(command, timeout, label, **kwargs)

        with patch.object(workflow, "execute", side_effect=execute), patch.object(ai.shutil, "which", return_value="codex"):
            result = workflow.validate([])
        self.assertTrue(result["buildFailed"])
        self.assertEqual(result["functional"]["skipped"], 1)
        self.assertFalse(any(label.startswith("验证新功能") for label in labels))
        self.assertFalse(workflow.dryrun_ok)

    def test_ai_marker_follows_info_without_reformatting_steps(self):
        import yaml
        source = 'info:\n  name: 测试\n  input: |\n    第一行\n    第二行\n# 步骤说明\nsteps:\n  - name: assert_true\n    args: [true]\n'
        marked = ai.ai_yaml(source)
        self.assertEqual(yaml.safe_load(marked)["ai_created"], True)
        self.assertLess(marked.index("ai_created: true"), marked.index("steps:"))
        self.assertIn('args: [true]', marked)
        self.assertIn('# 步骤说明', marked)
        self.assertEqual(ai.ai_yaml(marked), marked)
        replacement = ai.ai_yaml('ai_created: false\ninfo: example\nsteps: []\n')
        self.assertEqual(replacement, 'info: example\nai_created: true\nsteps: []\n')
        with self.assertRaises(ValueError):
            ai.ai_yaml('steps: []')

    def test_formal_cases_require_a_changed_plan_and_mark_both_files(self):
        import yaml
        workflow = self.workflow()
        workflow.category = "development"
        workflow.allowed = {"cases/feature.yml", "plans/feature.yml"}
        workflow.apply([{"path": "cases/feature.yml", "content": "info: 功能验证\nsteps: [{name: assert_true, args: [true]}]\n"}])
        workflow.acceptance = ["新功能"]
        workflow.functional_checks = [{"name": "新功能", "case": "feature", "yaml": ""}]
        with self.assertRaisesRegex(ValueError, "加入.*测试计划"):
            workflow.validate([])
        workflow.apply([{"path": "plans/feature.yml", "content": "info: 功能计划\ncases: [{case: feature}]\n"}])
        with patch.object(workflow, "execute", side_effect=self.regression_result):
            results = workflow.validate([])
        self.assertTrue(workflow.dryrun_ok)
        for relative in workflow.allowed:
            self.assertTrue(yaml.safe_load((self.root / relative).read_text())["ai_created"])
        self.assertEqual(results["checks"][0]["caseName"], "功能验证")
        self.assertFalse(results["checks"][0]["temporary"])

    def test_python_only_and_non_yaml_feature_checks_are_rejected(self):
        workflow = self.workflow()
        workflow.functional_checks = [{"name": "功能", "script": "check.py"}]
        with self.assertRaisesRegex(ValueError, "不能直接执行脚本"):
            workflow.validate([])
        workflow.changed = {"components/example.py": {"path": "components/example.py"}}
        workflow.functional_checks = [{"name": "功能", "case": "old"}]
        with self.assertRaisesRegex(ValueError, "临时 KDT YAML"):
            workflow.validate([])
        for content in ('steps: []', 'info: test\nsteps: []', 'info: test\nsteps: [false]',
                        'info: test\nsteps: [{name: test, dep: 1}]'):
            with self.assertRaises(ValueError):
                ai.case_definition(content)
        legacy, name = ai.case_definition('- name: assert_true\n  args: [true]\n', functional=False, fallback="legacy")
        self.assertEqual(name, "legacy")
        self.assertEqual(len(legacy["steps"]), 1)
        self.assertEqual(ai.case_definition('info: old\nsteps: []\n', functional=False)[0]["steps"], [])

    def test_temporary_yaml_is_archived_with_results_and_rounds_are_immutable(self):
        workflow = self.workflow()
        workflow.category = "development"
        workflow.acceptance = ["新功能"]
        workflow.prepare_session()
        workflow.apply_scratch([{"path": "pending/check.yaml", "content": "info: 临时功能验证\nsteps: [{name: assert_true, args: [true]}]\n"}])
        workflow.functional_checks = [{"name": "新功能", "yaml": "pending/check.yaml"}]
        with patch.object(workflow, "execute", side_effect=self.regression_result), patch.object(ai.shutil, "which", return_value="codex"):
            first = workflow.validate([])["checks"][0]
            workflow.scratch_target("pending/check.yaml").write_text("info: 修订验证\nsteps: [{name: assert_true, args: [false]}]\n", encoding="utf-8")
            second = workflow.validate([])["checks"][0]
        self.assertEqual(first["status"], "passed")
        self.assertTrue(first["temporary"])
        self.assertEqual(first["caseName"], "临时功能验证")
        self.assertNotEqual(first["archivePath"], second["archivePath"])
        self.assertIn("[true]", (workflow.workspace / first["archivePath"]).read_text())
        self.assertIn("[false]", (workflow.workspace / second["archivePath"]).read_text())
        self.assertFalse((self.root / "cases").exists())

    def test_new_components_must_be_called_directly_by_temporary_cases(self):
        keywords = self.root / "components/keywords.yml"
        keywords.parent.mkdir()
        keywords.write_text("old: {component: core.old}\n", encoding="utf-8")
        workflow = self.workflow()
        workflow.prepare_session()
        keywords.write_text("old: {component: core.old}\nnew_a: {component: core.new_a}\n"
                            "new_b: {component: core.new_b}\n", encoding="utf-8")
        workflow.changed = {"components/keywords.yml": {"path": "components/keywords.yml"}}
        workflow.acceptance = ["新组件"]
        workflow.functional_checks = [{"name": "新组件", "yaml": "check.yml"}]
        target = workflow.scratch_target("check.yml")
        target.write_text("info: 错误的验证\nsteps: [{name: fs_command, args: ['new_a new_b']}]\n", encoding="utf-8")
        with patch.object(workflow, "execute") as execute:
            with self.assertRaisesRegex(ValueError, "直接调用新增组件：new_a, new_b"):
                workflow.validate([])
            execute.assert_not_called()
        target.write_text("info: 部分覆盖\nsteps: [{name: new_a}]\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "直接调用新增组件：new_b"):
            workflow.validate([])
        target.write_text("info: 新组件验证\nsteps: [{name: new_a}, {name: new_b}]\n", encoding="utf-8")
        with patch.object(workflow, "execute", side_effect=self.regression_result), \
                patch.object(ai.shutil, "which", return_value="codex"):
            workflow.validate([])
        self.assertTrue(workflow.dryrun_ok)

    def test_existing_component_changes_do_not_require_a_new_keyword(self):
        keywords = self.root / "components/keywords.yml"
        keywords.parent.mkdir()
        keywords.write_text("existing: {component: core.old}\n", encoding="utf-8")
        workflow = self.workflow()
        workflow.prepare_session()
        keywords.write_text("existing: {component: core.corrected}\n", encoding="utf-8")
        workflow.changed = {"components/keywords.yml": {"path": "components/keywords.yml"}}
        workflow.acceptance = ["修正现有组件"]
        workflow.functional_checks = [{"name": "修正现有组件", "yaml": "check.yml"}]
        workflow.scratch_target("check.yml").write_text("info: 修正验证\nsteps: [{name: existing}]\n", encoding="utf-8")
        with patch.object(workflow, "execute", side_effect=self.regression_result), \
                patch.object(ai.shutil, "which", return_value="codex"):
            workflow.validate([])
        self.assertTrue(workflow.dryrun_ok)

    def test_temporary_yaml_real_runner_reports_pass_fail_and_uses_session_log(self):
        # 使用真实 runner/组件加载和子进程退出码，不调用模型、不依赖外部 Adapter。
        shutil.copytree(Path(__file__).resolve().parents[1] / "framework", self.root / "framework")
        component = self.root / "components/core/probe.py"
        component.parent.mkdir(parents=True)
        component.write_text('def check(value):\n    assert value, "functional failure"\n', encoding="utf-8")
        (self.root / "components/keywords.yml").write_text('probe:\n  name: 探针\n  component: core.probe.check\n  args: [{name: value, type: Any}]\n', encoding="utf-8")
        workflow = self.workflow()
        workflow.category = "development"
        workflow.acceptance = ["探针"]
        workflow.prepare_session()
        target = workflow.scratch_target("probe.yml")
        workflow.functional_checks = [{"name": "探针", "yaml": "probe.yml"}]
        real_execute = workflow.execute
        def execute(command, timeout, label, **kwargs):
            if label == "运行回归测试":
                yield from self.regression_result(command, timeout, label, **kwargs)
            else:
                yield from real_execute(command, timeout, label, **kwargs)
        # 此处仅替换外层 CLI 沙箱，真实 runner 仍在独立临时目录执行。
        with patch.object(workflow, "execute", side_effect=execute), patch.object(workflow, "session_command", side_effect=lambda command: command):
            for value, status in (("true", "passed"), ("false", "failed")):
                target.write_text('info: 真实 YAML 验证\nsteps: [{name: probe, args: [' + value + ']}]\n', encoding="utf-8")
                result = workflow.validate([])
                self.assertEqual(result["checks"][0]["status"], status)
                self.assertEqual(workflow.dryrun_ok, status == "passed")
        self.assertTrue(workflow.scratch_target("tmp/summary.log").is_file())
        self.assertFalse((self.root / "tmp/summary.log").exists())

    def test_cancelled_yaml_preserves_archived_case_in_delivery(self):
        workflow = self.workflow()
        workflow.prepare_session()
        workflow.scratch_target("check.yml").write_text('info: 可取消验证\nsteps: [{name: assert_true, args: [true]}]\n', encoding="utf-8")
        workflow.functional_checks = [{"name": "新功能", "yaml": "check.yml"}]
        def execute(command, timeout, label, **kwargs):
            if label.startswith("验证新功能"):
                raise InterruptedError("用户取消任务")
            yield from self.regression_result(command, timeout, label, **kwargs)
        with patch.object(workflow, "execute", side_effect=execute), patch.object(ai.shutil, "which", return_value="codex"):
            with self.assertRaises(InterruptedError):
                workflow.validate([])
        result = json.loads(workflow.scratch_target("dryrun.json").read_text())
        self.assertEqual(result["checks"][0]["status"], "cancelled")
        self.assertTrue((workflow.workspace / result["checks"][0]["archivePath"]).is_file())

    def test_delivery_archive_contains_only_final_metadata_and_verification(self):
        workflow = self.workflow()
        workflow.category = "development"
        workflow.prepare_session()
        workflow.changed = {"adapter/src/tool.cpp": {"path": "adapter/src/tool.cpp", "status": "added"}}
        workflow.questions = ["待确认支持平台"]
        workflow.publish_conclusion({"conclusion": " 已完成验证 "})
        workflow.dryrun_results = {"functional": {"total": 1, "passed": 1}}
        workflow.save_delivery("completed")
        archive = json.loads(workflow.scratch_target("delivery.json").read_text())
        self.assertEqual(archive["schemaVersion"], 1)
        self.assertEqual(archive["session"]["key"], workflow.session_key)
        self.assertEqual(archive["conclusion"], "已完成验证")
        self.assertEqual(archive["files"], list(workflow.changed.values()))
        self.assertNotIn("commands", archive)
        self.assertNotIn("catalog", archive)
        self.assertNotIn("quota", archive)
        self.assertGreaterEqual(archive["timing"]["finishedAt"], archive["timing"]["startedAt"])
        workflow.save_delivery("cancelled", "用户取消")
        self.assertEqual(json.loads(workflow.scratch_target("delivery.json").read_text())["error"], "用户取消")
        self.assertFalse(workflow.scratch_target("delivery.json.tmp").exists())

    def test_consultation_without_session_does_not_create_delivery_archive(self):
        workflow = self.workflow()
        workflow.save_delivery("completed")
        self.assertFalse(workflow.session_dir.exists())


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
