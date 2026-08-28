"""LPSS 字符串消息测试组件。"""

import os

from .base import LpssAdapter, _start


def strpub(node_name: str, topic: str, message: str, period_ms: int):
    """
    注册字符串消息周期发布任务。

    :param node_name: LPSS 发布节点名称
    :param topic: 发布主题
    :param message: 周期发送且内容不变的字符串消息
    :param period_ms: 发布周期，单位为毫秒
    """
    if period_ms <= 0:
        raise ValueError("period_ms must be greater than zero")
    return _start(
        LpssAdapter.MSG_STRPUB,
        [node_name, topic, message, str(period_ms)],
    )


def strcollect(node_name: str, topic: str):
    """
    注册字符串消息订阅任务并将接收结果返回给 KDT。

    :param node_name: LPSS 订阅节点名称
    :param topic: 订阅主题
    :return: 可供等待结果组件引用的 LPSS 后台任务
    """
    return _start(
        LpssAdapter.MSG_STRSUB,
        [node_name, topic, os.devnull],
        collect_results=True,
    )


def testpub(node_name: str, topic: str, period_ms: int):
    """
    注册 KDT TestTypes 复合消息发布任务。

    Adapter 在发布前会先完成一次显式序列化与反序列化自检。

    :param node_name: LPSS 发布节点名称
    :param topic: 发布主题
    :param period_ms: 发布周期，单位为毫秒
    """
    if period_ms <= 0:
        raise ValueError("period_ms must be greater than zero")
    return _start(LpssAdapter.MSG_TESTPUB, [node_name, topic, str(period_ms)])


def testcheck(node_name: str, topic: str):
    """
    注册 KDT TestTypes 复合消息订阅任务并返回字段校验结果。

    :param node_name: LPSS 订阅节点名称
    :param topic: 订阅主题
    :return: 可供等待结果组件引用的 LPSS 后台任务
    """
    return _start(
        LpssAdapter.MSG_TESTSUB,
        [node_name, topic, os.devnull],
        collect_results=True,
    )
