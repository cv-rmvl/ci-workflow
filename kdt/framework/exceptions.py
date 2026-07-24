"""KDT 框架与关键字组件共享的控制流异常。"""


class KeywordSkipped(Exception):
    """当前运行环境不支持关键字功能，应将步骤标记为跳过。"""
