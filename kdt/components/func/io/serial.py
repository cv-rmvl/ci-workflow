"""串口测试所需的虚拟串口组件。"""

import atexit
import os
import re
import signal
import shutil
import subprocess
import time
from enum import Enum
from pathlib import Path
from threading import Lock, Thread
from typing import List, Tuple

from framework import logger
from framework.exceptions import KeywordSkipped


_CREATED_PAIRS: List[Tuple[subprocess.Popen, Path, Path]] = []
_BACKGROUND_PROCESSES: List[Tuple[str, subprocess.Popen]] = []
_BACKGROUND_PROCESSES_LOCK = Lock()
_STOP_REQUESTED = set()
_SHUTTING_DOWN = False
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class BaudRate(Enum):
    """
    RMVL 串口支持的波特率。
    """

    BR_1200 = 0
    BR_2400 = 1
    BR_4800 = 2
    BR_9600 = 3
    BR_19200 = 4
    BR_38400 = 5
    BR_57600 = 6
    BR_115200 = 7


def _adapter(name: str) -> Path:
    """返回 adapter/bin 下指定可执行程序的绝对路径。"""
    suffix = ".exe" if os.name == "nt" else ""
    executable = (
        Path(__file__).resolve().parents[3]
        / "adapter"
        / "bin"
        / f"{name}{suffix}"
    )
    if not executable.is_file():
        raise FileNotFoundError(f"Adapter executable not found: {executable}")
    return executable


def _log_adapter_output(name: str, stdout: str, stderr: str):
    """将 adapter 的标准输出和标准错误逐行转为 KDT 日志。"""
    for line in stdout.splitlines():
        logger.info(_ANSI_ESCAPE.sub("", line), source=name)
    for line in stderr.splitlines():
        logger.info(_ANSI_ESCAPE.sub("", line), source=name)


def _stream_adapter_output(name: str, stream, log):
    """逐行转发后台 adapter 的单个输出流。"""
    if stream is None:
        return
    try:
        for line in stream:
            log(_ANSI_ESCAPE.sub("", line.rstrip("\r\n")), source=name)
    finally:
        stream.close()


def _monitor_adapter(name: str, process: subprocess.Popen):
    """实时转发后台 adapter 输出并等待其结束。"""
    _stream_adapter_output(name, process.stdout, logger.info)
    process.wait()

    with _BACKGROUND_PROCESSES_LOCK:
        stopped = process in _STOP_REQUESTED
        _STOP_REQUESTED.discard(process)
        _BACKGROUND_PROCESSES[:] = [
            item for item in _BACKGROUND_PROCESSES if item[1] is not process
        ]
    if not _SHUTTING_DOWN and process.returncode != 0 and not stopped:
        logger.error(
            f"Process exited with code {process.returncode}",
            source=name,
        )


def _start_background_adapter(name: str, arguments: List[str]):
    """启动并跟踪一个长生命周期串口 adapter。"""
    process_options = {}
    if os.name == "nt":
        process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        process_options["start_new_session"] = True

    process = subprocess.Popen(
        [str(_adapter(name)), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **process_options,
    )
    with _BACKGROUND_PROCESSES_LOCK:
        _BACKGROUND_PROCESSES.append((name, process))
    Thread(
        target=_monitor_adapter,
        args=(name, process),
        daemon=True,
    ).start()


def _request_process_stop(process: subprocess.Popen):
    """请求后台 adapter 正常退出，失败时再强制终止。"""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
    except (OSError, ValueError):
        process.terminate()

    deadline = time.monotonic() + 1
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    if process.poll() is None:
        process.kill()


def _cleanup_background_processes():
    """测试进程退出时终止仍在运行的串口 adapter。"""
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    with _BACKGROUND_PROCESSES_LOCK:
        processes = [process for _, process in _BACKGROUND_PROCESSES]
        _STOP_REQUESTED.update(processes)
    for process in processes:
        _request_process_stop(process)


def _remove_pair(pair: Tuple[subprocess.Popen, Path, Path]):
    """停止桥接进程并删除一对虚拟串口链接。"""
    process, port_a, port_b = pair
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    if process.stderr is not None:
        process.stderr.close()

    for port in (port_a, port_b):
        if port.is_symlink():
            port.unlink()


def _cleanup_pairs():
    """测试进程退出时清理后台桥接进程和虚拟串口链接。"""
    for pair in list(_CREATED_PAIRS):
        try:
            _remove_pair(pair)
        except OSError:
            pass
    _CREATED_PAIRS.clear()


atexit.register(_cleanup_pairs)
atexit.register(_cleanup_background_processes)


def write(port: str, baud: BaudRate, data: str):
    """
    向串口执行单次写操作。

    :param port: 串口名称
    :param baud: 波特率
    :param data: 待写入的数据
    """
    command = [str(_adapter("io.serial.write")), port, str(baud.value), data]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _log_adapter_output("io.serial.write", result.stdout, "")
    if result.returncode != 0:
        raise RuntimeError(
            f"io.serial.write exited with code {result.returncode}"
        )


def read(port: str, baud: BaudRate, output: str):
    """
    注册单次读串口操作，读取到的数据将写入指定文件。

    adapter 使用阻塞读取，因此该函数只负责在后台启动进程并立即返回。

    :param port: 串口名称
    :param baud: 波特率
    :param output: 输出文件路径
    """
    _start_background_adapter(
        "io.serial.read",
        [port, str(baud.value), output],
    )


def multiread(port: str, baud: BaudRate, output: str):
    """
    注册持续读串口操作，接收到的数据将追加到指定文件。

    :param port: 串口名称
    :param baud: 波特率
    :param output: 输出文件路径
    """
    _start_background_adapter(
        "io.serial.multiread",
        [port, str(baud.value), output],
    )


def multiwrite(port: str, baud: BaudRate, input_file: str):
    """
    注册持续写串口操作，输入文件中的数据发送后将被清空。

    :param port: 串口名称
    :param baud: 波特率
    :param input_file: 输入文件路径
    """
    _start_background_adapter(
        "io.serial.multiwrite",
        [port, str(baud.value), input_file],
    )


def stop():
    """终止当前 KDT 进程启动的全部串口后台 adapter。"""
    with _BACKGROUND_PROCESSES_LOCK:
        processes = [process for _, process in _BACKGROUND_PROCESSES]
        _STOP_REQUESTED.update(processes)

    for process in processes:
        _request_process_stop(process)

    if processes:
        logger.success(f"Stopped {len(processes)} serial background process(es)")
    else:
        logger.warning("No serial background process is running")


def check_socat():
    """
    检查当前系统是否支持虚拟串口。

    :return: 如果支持返回 True，否则返回 False
    """
    if os.name != "posix":
        return False

    socat = shutil.which("socat")
    if socat is None:
        logger.warning("Creating virtual serial ports requires socat")
        return False

    return True


def create(port_a: str, port_b: str):
    """
    创建一对互相连通的虚拟串口。

    不兼容 POSIX PTY 的平台会直接跳过；创建失败时抛出异常。

    :param port_a: 第一个虚拟串口名称
    :param port_b: 第二个虚拟串口名称
    """
    if not check_socat():
        if os.name != "posix":
            raise KeywordSkipped(
                "Virtual serial ports require POSIX PTY and are not supported "
                "on this platform"
            )
        raise KeywordSkipped(
            "Creating virtual serial ports requires socat"
        )

    if not isinstance(port_a, str) or not port_a:
        raise ValueError("port_a must be a non-empty string")
    if not isinstance(port_b, str) or not port_b:
        raise ValueError("port_b must be a non-empty string")

    path_a = Path(os.path.abspath(port_a))
    path_b = Path(os.path.abspath(port_b))
    if path_a == path_b:
        raise ValueError("port_a and port_b must be different")
    if os.path.lexists(path_a) or os.path.lexists(path_b):
        raise FileExistsError("Virtual serial port name already exists")

    socat = shutil.which("socat")
    if socat is None:
        raise RuntimeError("Creating virtual serial ports requires socat")

    path_a.parent.mkdir(parents=True, exist_ok=True)
    path_b.parent.mkdir(parents=True, exist_ok=True)
    command = [
        socat,
        f"pty,raw,echo=0,link={path_a}",
        f"pty,raw,echo=0,link={path_b}",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if os.path.lexists(path_a) and os.path.lexists(path_b):
            _CREATED_PAIRS.append((process, path_a, path_b))
            logger.success(f"Created virtual serial pair: {path_a} <-> {path_b}")
            return

        if process.poll() is not None:
            stderr = process.stderr.read().strip() if process.stderr else ""
            raise RuntimeError(stderr or "socat failed to create virtual serial ports")
        time.sleep(0.05)

    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    stderr = process.stderr.read().strip() if process.stderr else ""
    raise RuntimeError(stderr or "Timed out while creating virtual serial ports")


def remove(port_a: str, port_b: str):
    """
    删除由 create 创建的一对虚拟串口。

    不兼容平台或目标不存在时直接返回；清理失败时抛出异常。

    :param port_a: 第一个虚拟串口名称
    :param port_b: 第二个虚拟串口名称
    """
    if not check_socat():
        return

    if not isinstance(port_a, str) or not port_a:
        raise ValueError("port_a must be a non-empty string")
    if not isinstance(port_b, str) or not port_b:
        raise ValueError("port_b must be a non-empty string")

    requested = {
        Path(os.path.abspath(port_a)),
        Path(os.path.abspath(port_b)),
    }
    for pair in _CREATED_PAIRS:
        _, created_a, created_b = pair
        if requested == {created_a, created_b}:
            _remove_pair(pair)
            _CREATED_PAIRS.remove(pair)
            logger.success(
                f"Removed virtual serial pair: {created_a} <-> {created_b}"
            )
            return

    logger.warning(f"Virtual serial pair not found: {port_a} <-> {port_b}")
