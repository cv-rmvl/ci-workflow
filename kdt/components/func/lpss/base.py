"""LPSS 后台节点的生命周期管理。"""

import atexit
import os
import re
import signal
import subprocess
import time
from enum import Enum
from pathlib import Path
from threading import Lock, Thread
from typing import List, Optional, Tuple

from framework import logger
from framework.exceptions import KeywordSkipped


class LpssAdapter(Enum):
    """可由 KDT 启动和终止的 LPSS Adapter。"""

    MSG_STRPUB = "lpss.msg.strpub"
    MSG_STRSUB = "lpss.msg.strsub"
    MSG_TESTPUB = "lpss.msg.testpub"
    MSG_TESTSUB = "lpss.msg.testsub"
    SRV_CLIENT = "lpss.srv.client"
    SRV_SERVICE = "lpss.srv.service"
    SRV_TESTCLIENT = "lpss.srv.testclient"
    SRV_TESTSERVICE = "lpss.srv.testservice"


class LpssSignal(Enum):
    """可发送给 LPSS 后台节点的终止信号。"""

    SIGINT = "SIGINT"
    SIGTERM = "SIGTERM"
    SIGABRT = "SIGABRT"
    SIGSEGV = "SIGSEGV"
    SIGKILL = "SIGKILL"


_BACKGROUND_PROCESSES: List[Tuple[LpssAdapter, str, subprocess.Popen]] = []
_BACKGROUND_PROCESSES_LOCK = Lock()
_STOP_REQUESTED = set()
_SHUTTING_DOWN = False
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SKIPPED_EXIT_CODE = 77


def _adapter(adapter: LpssAdapter) -> Path:
    """返回 adapter/bin 下指定 LPSS 可执行程序的绝对路径。"""
    suffix = ".exe" if os.name == "nt" else ""
    executable = (
        Path(__file__).resolve().parents[3]
        / "adapter"
        / "bin"
        / f"{adapter.value}{suffix}"
    )
    if not executable.is_file():
        raise FileNotFoundError(f"Adapter executable not found: {executable}")
    return executable


def _stream_adapter_output(adapter: LpssAdapter, stream):
    """将后台 Adapter 的输出实时转发到 KDT 日志。"""
    if stream is None:
        return
    try:
        for line in stream:
            logger.info(
                _ANSI_ESCAPE.sub("", line.rstrip("\r\n")),
                source=adapter.value,
            )
    finally:
        stream.close()


def _monitor_adapter(adapter: LpssAdapter, process: subprocess.Popen):
    """转发后台 Adapter 输出，等待进程结束并维护进程表。"""
    _stream_adapter_output(adapter, process.stdout)
    process.wait()

    with _BACKGROUND_PROCESSES_LOCK:
        stopped = process in _STOP_REQUESTED
        _STOP_REQUESTED.discard(process)
        _BACKGROUND_PROCESSES[:] = [
            item for item in _BACKGROUND_PROCESSES if item[2] is not process
        ]

    if not _SHUTTING_DOWN and process.returncode != 0 and not stopped:
        logger.error(
            f"Process exited with code {process.returncode}",
            source=adapter.value,
        )


def _start(adapter: LpssAdapter, arguments: List[str]):
    """启动并跟踪一个长生命周期 LPSS Adapter。"""
    if not arguments:
        raise ValueError("LPSS Adapter arguments must contain a node name")

    node_name = arguments[0]
    process_options = {}
    if os.name == "nt":
        process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        process_options["start_new_session"] = True

    process = subprocess.Popen(
        [str(_adapter(adapter)), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **process_options,
    )
    with _BACKGROUND_PROCESSES_LOCK:
        _BACKGROUND_PROCESSES.append((adapter, node_name, process))

    Thread(target=_monitor_adapter, args=(adapter, process), daemon=True).start()


def _run(adapter: LpssAdapter, arguments: List[str], expected_exit_code: int = 0, timeout: Optional[float] = None):
    """执行短生命周期 LPSS Adapter，并校验退出状态和最长运行时间。"""
    result = subprocess.run(
        [str(_adapter(adapter)), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    for line in result.stdout.splitlines():
        logger.info(_ANSI_ESCAPE.sub("", line), source=adapter.value)
    if result.returncode == _SKIPPED_EXIT_CODE:
        raise KeywordSkipped(
            result.stdout.strip() or f"{adapter.value} is not supported"
        )
    if result.returncode != expected_exit_code:
        raise RuntimeError(
            f"{adapter.value} exited with code {result.returncode}, "
            f"expected {expected_exit_code}"
        )


def _require_supported(adapter: LpssAdapter):
    """查询 Adapter 能力，不支持时通知 Runner 跳过当前关键字。"""
    result = subprocess.run(
        [str(_adapter(adapter)), "--kdt-capability"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode == _SKIPPED_EXIT_CODE:
        raise KeywordSkipped(
            result.stdout.strip() or f"{adapter.value} requires C++20"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to query {adapter.value} capability: "
            f"exit code {result.returncode}"
        )


def _native_signal(signal_type: LpssSignal) -> int:
    """返回当前平台对应的原生信号值。"""
    native_signal = getattr(signal, signal_type.value, None)
    if native_signal is None:
        raise RuntimeError(
            f"Signal {signal_type.value} is not supported on this platform"
        )
    return native_signal


def _send_process_signal(
    process: subprocess.Popen, signal_type: LpssSignal
):
    """向指定后台 Adapter 发送所选信号。"""
    if os.name == "nt":
        if signal_type is LpssSignal.SIGINT:
            # 新进程组中的 CTRL_C_EVENT 会映射为 C 运行时的 SIGINT。
            process.send_signal(signal.CTRL_C_EVENT)
        elif signal_type is LpssSignal.SIGKILL:
            process.kill()
        elif signal_type is LpssSignal.SIGTERM:
            process.terminate()
        else:
            process.send_signal(_native_signal(signal_type))
        return

    process.send_signal(_native_signal(signal_type))


def _request_process_stop(
    process: subprocess.Popen, signal_type: LpssSignal = LpssSignal.SIGINT
) -> bool:
    """向后台 Adapter 发送信号，返回超时后是否使用了强制终止。"""
    if process.poll() is not None:
        return False
    try:
        _send_process_signal(process, signal_type)
    except (OSError, ValueError):
        process.kill()
        process.wait()
        return True

    deadline = time.monotonic() + 1
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    if process.poll() is None:
        process.kill()
        process.wait()
        return True
    return False


def stop(adapter: LpssAdapter, signal_type: LpssSignal, node_name: str):
    """
    向指定名称的 LPSS 后台节点发送信号。

    SIGINT 要求节点在超时时间内以退出码 0 优雅退出；其他信号要求节点确实
    由所选信号终止。若存在多个同名同类型节点，则全部终止并逐一校验。

    :param adapter: 待停止节点使用的 LPSS Adapter
    :param signal_type: 要发送的信号类型
    :param node_name: 启动组件传入的节点名称
    """
    with _BACKGROUND_PROCESSES_LOCK:
        processes = [
            process
            for registered_adapter, registered_name, process in _BACKGROUND_PROCESSES
            if registered_adapter is adapter and registered_name == node_name
        ]
        _STOP_REQUESTED.update(processes)

    if not processes:
        if adapter in {
            LpssAdapter.SRV_CLIENT,
            LpssAdapter.SRV_SERVICE,
            LpssAdapter.SRV_TESTCLIENT,
            LpssAdapter.SRV_TESTSERVICE,
        }:
            _require_supported(adapter)
        raise RuntimeError(
            f"No {adapter.value} process named {node_name!r} is running"
        )

    failures = []
    for process in processes:
        forced = _request_process_stop(process, signal_type)
        if forced:
            failures.append(
                f"PID {process.pid} did not exit after {signal_type.value}"
            )
        elif signal_type is LpssSignal.SIGINT and process.returncode != 0:
            failures.append(
                f"PID {process.pid} exited with code {process.returncode}"
            )
        elif os.name != "nt" and signal_type is not LpssSignal.SIGINT:
            expected_code = -int(_native_signal(signal_type))
            if process.returncode != expected_code:
                failures.append(
                    f"PID {process.pid} exited with code {process.returncode}, "
                    f"expected {expected_code} for {signal_type.value}"
                )

    if failures:
        raise RuntimeError(
            f"Failed to stop {adapter.value} node {node_name!r}: "
            + "; ".join(failures)
        )

    logger.success(
        f"Sent {signal_type.value} to {len(processes)} "
        f"{adapter.value} process(es) named {node_name!r}"
    )


def _cleanup_background_processes():
    """KDT 进程退出时终止仍在运行的 LPSS Adapter。"""
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    with _BACKGROUND_PROCESSES_LOCK:
        processes = [process for _, _, process in _BACKGROUND_PROCESSES]
        _STOP_REQUESTED.update(processes)
    for process in processes:
        _request_process_stop(process)


atexit.register(_cleanup_background_processes)
