"""
KDT 框架的公共接口，对外提供日志记录器和测试计划执行函数
"""

from .logger import logger
from .runner import run_plan

__all__ = [
    "logger",
    "run_plan",
]
