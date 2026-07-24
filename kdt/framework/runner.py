"""按测试计划调度并执行 KDT 测试用例。"""

from pathlib import Path
from typing import List, Mapping, Tuple, Union

from .exceptions import KeywordSkipped
from .keywords import keywords
from .logger import logger


_CASES_DIR = Path(__file__).resolve().parents[1] / "cases"


class PlanDefinitionError(Exception):
    """测试计划文件结构无效。"""


class CaseDefinitionError(Exception):
    """测试用例文件结构无效。"""


class CaseExecutionError(Exception):
    """测试用例中存在执行失败的步骤。"""

    def __init__(self, failures: List[Tuple[int, str, Exception]]):
        self.failures = failures
        details = "\n".join(
            f"step {index} ({name}): {error}"
            for index, name, error in failures
        )
        super().__init__(details)


class CaseSkipped(Exception):
    """测试用例因当前环境不支持某项功能而跳过。"""

    def __init__(self, steps: List[Tuple[int, str, Exception]]):
        self.steps = steps
        details = "; ".join(
            f"step {index} ({name}): {error}"
            for index, name, error in steps
        )
        super().__init__(details)


class PlanExecutionError(Exception):
    """测试计划中存在执行失败的用例。"""

    def __init__(self, failures: List[Tuple[str, int, Exception]]):
        self.failures = failures
        details = "\n".join(
            f"case {name} loop {loop}: {error}"
            for name, loop, error in failures
        )
        super().__init__(details)


def _read_yaml(path: Path):
    """读取 YAML 文件。"""
    try:
        import yaml
    except ImportError as exc:
        raise PlanDefinitionError("Loading test plans requires PyYAML") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_case(case_file: Path):
    """读取测试用例名称和步骤，并兼容旧的纯数组格式。"""
    definition = _read_yaml(case_file)
    if isinstance(definition, list):
        return case_file.stem, definition
    if not isinstance(definition, Mapping):
        raise CaseDefinitionError("A test case must be a mapping")

    info = definition.get("info")
    steps = definition.get("steps")
    if not isinstance(info, str) or not info.strip():
        raise CaseDefinitionError("A test case must contain non-empty info")
    if not isinstance(steps, list):
        raise CaseDefinitionError("Test case steps must be a list")
    return info.strip(), steps


def _case_file(case_name: str) -> Path:
    """将点分形式的用例名称转换为 cases 目录下的文件路径。"""
    suffix = ".yml"
    reference = case_name
    for yaml_suffix in (".yml", ".yaml"):
        if reference.lower().endswith(yaml_suffix):
            reference = reference[:-len(yaml_suffix)]
            suffix = yaml_suffix
            break

    parts = reference.split(".")
    if (
        not all(parts)
        or any(part in (".", "..") for part in parts)
        or "/" in reference
        or "\\" in reference
    ):
        raise CaseDefinitionError(
            f"Invalid dotted test case name: {case_name}"
        )

    path = (_CASES_DIR.joinpath(*parts).with_suffix(suffix)).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Test case file not found: {path}")
    return path


def run_case(case_name: str):
    """按顺序执行一个测试用例中的全部步骤。"""
    failures: List[Tuple[int, str, Exception]] = []
    skipped: List[Tuple[int, str, Exception]] = []
    previous_succeeded = True
    environment_skipped = False
    _, steps = _load_case(_case_file(case_name))
    for index, step in enumerate(steps, start=1):
        keyword_name = "<unknown>"
        try:
            if not isinstance(step, Mapping):
                raise CaseDefinitionError("Each test step must be a mapping")

            keyword_name = step.get("name")
            if not isinstance(keyword_name, str) or not keyword_name:
                raise CaseDefinitionError("Each test step must have a name")

            dependency = step.get("dep", False)
            if not isinstance(dependency, bool):
                raise CaseDefinitionError(
                    f"Dependency of step '{keyword_name}' must be a boolean"
                )
            if dependency and (environment_skipped or not previous_succeeded):
                reason = (
                    "an earlier step is not supported in this environment"
                    if environment_skipped
                    else "previous step did not succeed"
                )
                logger.skip(
                    f"Step {index}: {keyword_name} skipped: {reason}"
                )
                previous_succeeded = False
                continue

            args = step.get("args", [])
            if not isinstance(args, list):
                raise CaseDefinitionError(
                    f"Arguments of step '{keyword_name}' must be a list"
                )
            logger.info(f"Step {index}: {keyword_name}")
            keywords.call(keyword_name, *args)
            logger.success(f"Step {index}: {keyword_name} done!")
            previous_succeeded = True
        except KeywordSkipped as exc:
            previous_succeeded = False
            environment_skipped = True
            skipped.append((index, str(keyword_name), exc))
            logger.skip(f"Step {index}: {keyword_name}: {exc}")
        except Exception as exc:
            previous_succeeded = False
            failures.append((index, str(keyword_name), exc))
            logger.error(f"\u2717 Step {index}: {keyword_name}: {exc}")

    if failures:
        raise CaseExecutionError(failures)
    if skipped:
        raise CaseSkipped(skipped)


def run_plan(plan_file: Union[str, Path]):
    """
    执行一个测试计划文件中声明的全部测试用例。

    Plan 中的 Case 或某次循环失败后继续执行后续内容；全部执行结束后，
    如果存在失败，则统一抛出 PlanExecutionError。

    :param plan_file: 外部传入的测试计划 YAML 文件路径
    """
    path = Path(plan_file).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Test plan file not found: {path}")

    definition = _read_yaml(path)
    if isinstance(definition, list):
        plan = definition
    elif isinstance(definition, Mapping):
        info = definition.get("info")
        plan = definition.get("cases")
        if not isinstance(info, str) or not info.strip():
            raise PlanDefinitionError("A test plan must contain non-empty info")
        if not isinstance(plan, list):
            raise PlanDefinitionError("Test plan cases must be a list")
    else:
        raise PlanDefinitionError("A test plan must be a mapping")

    failures: List[Tuple[str, int, Exception]] = []
    for entry in plan:
        if not isinstance(entry, Mapping):
            raise PlanDefinitionError("Each test plan entry must be a mapping")

        case_name = entry.get("case")
        if not isinstance(case_name, str) or not case_name:
            raise PlanDefinitionError(
                "Each test plan entry must contain a non-empty case name"
            )

        loop = entry.get("loop", 1)
        if isinstance(loop, bool) or not isinstance(loop, int) or loop < 1:
            raise PlanDefinitionError(
                f"Loop of case '{case_name}' must be a positive integer"
            )

        case_info, _ = _load_case(_case_file(case_name))
        for iteration in range(1, loop + 1):
            logger.info(f"Case: {case_info} [{iteration}/{loop}]")
            try:
                run_case(case_name)
            except CaseSkipped as exc:
                logger.skip(
                    f"Case: {case_info} [{iteration}/{loop}]: skipped: {exc}"
                )
            except Exception as exc:
                failures.append((case_name, iteration, exc))
                logger.error(
                    f"Case: {case_info} [{iteration}/{loop}]: failed"
                )
            else:
                logger.success(
                    f"Case: {case_info} [{iteration}/{loop}]: done!"
                )

    if failures:
        raise PlanExecutionError(failures)
