"""运行时确定的 KDT 全局环境配置。"""

import json
import platform
from pathlib import Path
import subprocess
from typing import Optional


_ADAPTER_BIN = Path(__file__).resolve().parents[1] / "adapter/bin"


def _system_type() -> str:
    name = platform.system().lower()
    return {"windows": "win", "darwin": "macos"}.get(name, name or "unknown")


class KdtConfig:
    """共享环境信息，Python 无法直接获取的扩展信息通过 Adapter 查询。"""

    def __init__(self):
        self.refresh()

    def refresh(self):
        """刷新系统信息并清空标准缓存，不在此启动 Adapter。"""
        self._system = _system_type()
        self._cpp_standard = None

    @property
    def system(self) -> str:
        return self._system

    @property
    def cpp_standard(self) -> Optional[int]:
        if self._cpp_standard is not None:
            return self._cpp_standard
        executable = _ADAPTER_BIN / ("kdt.config.ext.exe" if self.system == "win" else "kdt.config.ext")
        if not executable.is_file():
            return None
        result = subprocess.run([str(executable)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", timeout=5)
        if result.returncode:
            raise RuntimeError(f"kdt.config.ext 异常退出，退出码: {result.returncode}: {result.stderr.strip()}")
        output = result.stdout.strip()
        try:
            extension = json.loads(output)
        except ValueError as error:
            raise RuntimeError(f"kdt.config.ext 返回了无效的 JSON: {output!r}") from error
        if not isinstance(extension, dict):
            raise RuntimeError("kdt.config.ext 必须返回一个 JSON 对象，而不是其他类型")
        standard = extension.get("cpp_standard")
        if type(standard) is not int or standard not in {17, 20, 23, 26}:
            raise RuntimeError(f"kdt.config.ext 返回了一个非法的 cpp_standard 值: {standard!r}")
        self._cpp_standard = standard
        return self._cpp_standard


kdt_config = KdtConfig()
