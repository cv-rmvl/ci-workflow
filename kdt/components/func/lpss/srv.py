"""LPSS 标准服务测试组件。"""

import os

from .base import LpssAdapter, _require_supported, _run, _start


def service(node_name: str, service_name: str):
    """
    注册 std/SetBool 服务端节点。

    服务始终返回成功，并将 true/false 分别转换为 enabled/disabled。

    :param node_name: LPSS 服务端节点名称
    :param service_name: LPSS 服务名称
    """
    _require_supported(LpssAdapter.SRV_SERVICE)
    _start(LpssAdapter.SRV_SERVICE, [node_name, service_name])


def client(
    node_name: str,
    service_name: str,
    data: bool,
    output: str,
    timeout_ms: int,
):
    """
    调用一次 std/SetBool 服务并将响应写入文件。

    输出包含 ``success`` 和 ``message`` 两行；服务调用超时或 Adapter
    异常退出时，该组件会直接抛出异常。

    :param node_name: LPSS 客户端节点名称
    :param service_name: LPSS 服务名称
    :param data: SetBool 请求值
    :param output: 服务响应输出文件路径
    :param timeout_ms: 服务调用超时时间，单位为毫秒
    """
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be greater than zero")
    _require_supported(LpssAdapter.SRV_CLIENT)
    _run(
        LpssAdapter.SRV_CLIENT,
        [node_name, service_name, "1" if data else "0", output, str(timeout_ms)],
        timeout=timeout_ms / 1000 + 3,
    )


def timeout(node_name: str, service_name: str, timeout_ms: int):
    """
    断言无服务端时 std/SetBool 调用能够在指定时间后超时退出。

    Adapter 的退出码 5 表示服务调用超时（退出码 2 表示客户端创建失败）；Python 外层还会在预期时限之外
    强制结束等待，避免 Adapter 缺陷导致整个测试计划永久阻塞。

    :param node_name: LPSS 客户端节点名称
    :param service_name: 应当不存在的 LPSS 服务名称
    :param timeout_ms: 期望的服务调用超时时间，单位为毫秒
    """
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be greater than zero")
    _require_supported(LpssAdapter.SRV_CLIENT)
    _run(
        LpssAdapter.SRV_CLIENT,
        [node_name, service_name, "1", os.devnull, str(timeout_ms)],
        expected_exit_code=5,
        timeout=timeout_ms / 1000 + 3,
    )


def testtypes_service(node_name: str, service_name: str):
    """
    注册 KDT TestTypes 复合类型服务端。

    :param node_name: LPSS 服务端节点名称
    :param service_name: LPSS 服务名称
    """
    _require_supported(LpssAdapter.SRV_TESTSERVICE)
    _start(LpssAdapter.SRV_TESTSERVICE, [node_name, service_name])


def testtypes_client(
    node_name: str,
    service_name: str,
    output: str,
    timeout_ms: int,
):
    """
    调用 TestTypes 服务并验证请求、响应的序列化与反序列化结果。

    :param node_name: LPSS 客户端节点名称
    :param service_name: LPSS 服务名称
    :param output: 校验结果输出文件路径
    :param timeout_ms: 服务调用超时时间，单位为毫秒
    """
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be greater than zero")
    _require_supported(LpssAdapter.SRV_TESTCLIENT)
    _run(
        LpssAdapter.SRV_TESTCLIENT,
        [node_name, service_name, output, str(timeout_ms)],
        timeout=timeout_ms / 1000 + 3,
    )
