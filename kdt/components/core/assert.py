"""针对 Python 值的通用断言组件。"""

from typing import Any


def equal(actual: Any, expected: Any):
    """断言两个 Python 值相等。"""
    if actual != expected:
        raise AssertionError(
            f"Values differ: actual={actual!r}, expected={expected!r}"
        )


def not_equal(actual: Any, expected: Any):
    """断言两个 Python 值不相等。"""
    if actual == expected:
        raise AssertionError(
            f"Values are equal: actual={actual!r}, expected={expected!r}"
        )


def is_true(value: Any):
    """断言 Python 值的真值为 True。"""
    if not value:
        raise AssertionError(f"Value is not true: {value!r}")


def is_false(value: Any):
    """断言 Python 值的真值为 False。"""
    if value:
        raise AssertionError(f"Value is not false: {value!r}")
