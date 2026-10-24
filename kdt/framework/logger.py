"""供 KDT 框架和关键字组件共享的纯文本日志模块。"""

import inspect
import sys
from datetime import datetime
import os
from pathlib import Path
from threading import Lock


_COLORS = {
    "info": "\033[0m",
    "pass": "\033[32m",
    "warn": "\033[33m",
    "erro": "\033[31m",
    "skip": "\033[90m",
}
_COLOR_RESET = "\033[0m"
_COLOR_DATE = "\033[90m"
_COLOR_SOURCE = "\033[34m"


class Logger:
    """将带时间戳的 KDT 消息追加到纯文本日志文件。"""

    def __init__(self, log_file: Path):
        self.log_file = log_file
        self._lock = Lock()
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self._archive_old_log()

    def _archive_old_log(self):
        """开始新一轮测试前归档上一次的日志。"""
        if not self.log_file.exists():
            return

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        archive_file = self.log_file.with_name(
            f"{self.log_file.stem}_{timestamp}{self.log_file.suffix}"
        )
        index = 1
        while archive_file.exists():
            archive_file = self.log_file.with_name(
                f"{self.log_file.stem}_{timestamp}_{index}{self.log_file.suffix}"
            )
            index += 1
        self.log_file.rename(archive_file)

    @staticmethod
    def _caller_name():
        """返回产生日志的组件分类和函数名称。"""
        current_frame = inspect.currentframe()
        try:
            # 调用链：_caller_name -> _write -> 日志接口 -> 调用方
            caller = current_frame
            for _ in range(3):
                if caller is None:
                    return "unknown.unknown"
                caller = caller.f_back

            if caller is None:
                return "unknown.unknown"

            function_name: str = caller.f_code.co_name
            module_name: str = caller.f_globals.get("__name__", "")
            parts = module_name.split(".")

            if "components" in parts:
                index = parts.index("components")
                category = (
                    parts[index + 1]
                    if index + 1 < len(parts)
                    else "components"
                )
            elif module_name == "__main__":
                category = Path(caller.f_code.co_filename).stem
            else:
                category = parts[-1] if parts else "unknown"

            return f"{category}.{function_name}"
        finally:
            del current_frame

    def _write(self, level: str, message, source=None):
        """向日志文件和标准输出写入一行或多行格式化文本。"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        caller = str(source) if source else self._caller_name()
        lines = str(message).splitlines() or [""]
        text = "".join(
            f"[{timestamp}] [{level}] [{caller}] {line}\n" for line in lines
        )

        with self._lock:
            with self.log_file.open("a", encoding="utf-8") as stream:
                stream.write(text)

            color = _COLORS.get(level, "")
            terminal = "".join(
                f"{_COLOR_DATE}[{timestamp}]{_COLOR_RESET} "
                f"{color}[{level}]{_COLOR_RESET} "
                f"{_COLOR_SOURCE}[{caller}]{_COLOR_RESET} {line}\n"
                for line in lines
            )
            sys.stdout.write(terminal)
            sys.stdout.flush()

    def success(self, message, source=None):
        """记录成功信息。"""
        self._write("pass", message, source)

    def warning(self, message, source=None):
        """记录警告信息。"""
        self._write("warn", message, source)

    def error(self, message, source=None):
        """记录错误信息。"""
        self._write("erro", message, source)

    def info(self, message, source=None):
        """记录一般信息。"""
        self._write("info", message, source)

    def skip(self, message, source=None):
        """记录跳过信息。"""
        self._write("skip", message, source)

logger = Logger(Path(os.environ["KDT_LOG_FILE"]) if os.environ.get("KDT_LOG_FILE")
                else Path(__file__).resolve().parents[1] / "tmp" / "summary.log")
"""
KDT 全局日志记录器
"""


def kdt_log_result(result: str, status: str):
    """显示测试执行结果。"""
    result_block = '─' * (len(result) + len(status) + 9)
    logger.info(f'┌{result_block}┐')
    logger.info(f'│   {result} ({status})   │')
    logger.info(f'└{result_block}┘')
