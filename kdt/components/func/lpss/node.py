"""指定域的 LPSS 节点发现快照，默认域 0，查询节点自身不计入结果。"""

import re

from .base import LpssQueryAdapter, _run


def _domain_arguments(domain_id: int):
    if type(domain_id) is not int:
        raise TypeError("domain_id must be an integer")
    if not 0 <= domain_id <= 255:
        raise ValueError("domain_id must be between 0 and 255")
    return ["--domain", str(domain_id)]


def _query(arguments, domain_id: int):
    # 使用 Adapter 默认的 1000 ms 发现窗口，另设进程级硬超时。
    output = _run(LpssQueryAdapter.TOOL, arguments + _domain_arguments(domain_id), timeout=5).strip()
    if re.fullmatch(r"[0-9]+", output) is None:
        raise RuntimeError(f"Invalid LPSS node query response: {output!r}")
    return int(output)


def count(domain_id: int = 0) -> int:
    """按 GUID 返回发现窗口内的节点数量，同名节点分别计数。"""
    return _query(["node", "list", "-c"], domain_id)


def check(node_name: str, exists: bool, domain_id: int = 0):
    """断言精确名称是否存在，查询错误或超时直接传播，不视为不存在。"""
    if not isinstance(node_name, str):
        raise TypeError("node_name must be a string")
    if not node_name or "\0" in node_name or len(node_name.encode("utf-8")) > 255:
        raise ValueError("node_name must contain 1 to 255 UTF-8 bytes without NUL")
    if type(exists) is not bool:
        raise TypeError("exists must be a boolean")
    output = _run(LpssQueryAdapter.TOOL, ["node", "list", *_domain_arguments(domain_id)], timeout=5)
    actual = node_name in output.splitlines()
    if actual != exists:
        raise AssertionError(
            f"LPSS node {node_name!r}: exists={actual}, expected {exists}"
        )
