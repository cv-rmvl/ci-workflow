"""
KDT 框架的公共接口，对外提供日志记录器和测试计划执行函数
"""

from .logger import logger


def __getattr__(name):
    """延迟导出测试计划接口，避免组件导入日志时触发关键字注册。"""
    if name == "run_plan":
        from .runner import run_plan

        return run_plan
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "logger",
    "run_plan",
]
