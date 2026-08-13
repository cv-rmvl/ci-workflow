"""测试步骤返回值的缓存与引用解析。"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping


_EXACT_REFERENCE = re.compile(r"^\s*\{\{\s*step([1-9]\d*)\s*\}\}\s*$")
_REFERENCE = re.compile(r"\{\{\s*step([1-9]\d*)\s*\}\}")
_TEXT_SCALAR_TYPES = (str, int, float, bool)


class StepResultReferenceError(ValueError):
    """步骤结果引用无效或对应结果不可用。"""


@dataclass
class StepResultCache:
    """保存单次测试用例执行中已经成功完成的步骤返回值。"""

    _results: Dict[int, Any] = field(default_factory=dict)

    def store(self, step: int, value: Any):
        """缓存指定步骤的返回值，包括 ``None``。"""
        self._results[step] = value

    def resolve_arguments(self, value: Any, current_step: int) -> Any:
        """递归解析参数中的 ``{{ stepN }}`` 引用。"""
        if isinstance(value, str):
            return self._resolve_string(value, current_step)
        if isinstance(value, list):
            return [self.resolve_arguments(item, current_step) for item in value]
        if isinstance(value, tuple):
            return tuple(self.resolve_arguments(item, current_step) for item in value)
        if isinstance(value, Mapping):
            return {
                key: self.resolve_arguments(item, current_step)
                for key, item in value.items()
            }
        return value

    def _result(self, referenced_step: int, current_step: int) -> Any:
        if referenced_step >= current_step:
            raise StepResultReferenceError(
                f"Step {current_step} cannot reference step {referenced_step}: "
                "only completed earlier steps are available"
            )
        if referenced_step not in self._results:
            raise StepResultReferenceError(
                f"Result of step {referenced_step} is unavailable: "
                "the step failed, was skipped, or was not executed"
            )
        return self._results[referenced_step]

    def _resolve_string(self, value: str, current_step: int) -> Any:
        exact = _EXACT_REFERENCE.fullmatch(value)
        if exact:
            return self._result(int(exact.group(1)), current_step)

        def replace(match) -> str:
            referenced_step = int(match.group(1))
            result = self._result(referenced_step, current_step)
            if not isinstance(result, _TEXT_SCALAR_TYPES):
                raise StepResultReferenceError(
                    f"Result of step {referenced_step} has type "
                    f"{type(result).__name__} and cannot be embedded in text; "
                    f"use '{{{{ step{referenced_step} }}}}' as the entire argument"
                )
            return str(result)

        return _REFERENCE.sub(replace, value)
