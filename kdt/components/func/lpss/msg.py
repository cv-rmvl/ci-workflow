"""LPSS 字符串消息测试组件。"""

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
    _start(
        LpssAdapter.MSG_STRPUB,
        [node_name, topic, message, str(period_ms)],
    )


def strsub(node_name: str, topic: str, output: str):
    """
    注册字符串消息订阅任务，将消息逐行写入文件。

    :param node_name: LPSS 订阅节点名称
    :param topic: 订阅主题
    :param output: 接收消息的输出文件路径
    """
    _start(
        LpssAdapter.MSG_STRSUB,
        [node_name, topic, output],
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
    _start(LpssAdapter.MSG_TESTPUB, [node_name, topic, str(period_ms)])


def testsub(node_name: str, topic: str, output: str):
    """
    注册 KDT TestTypes 复合消息订阅任务并逐行记录校验结果。

    :param node_name: LPSS 订阅节点名称
    :param topic: 订阅主题
    :param output: 校验结果输出文件路径
    """
    _start(LpssAdapter.MSG_TESTSUB, [node_name, topic, output])
