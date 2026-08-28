"""文件系统相关的基础组件。"""

import shutil
from pathlib import Path
from typing import List

from framework import logger


def _normalized_content(path: str) -> str:
    """读取 UTF-8 文本并移除文件内容首尾的空白字符。"""
    return Path(path).read_text(encoding="utf-8").strip()


def same(path_a: str, path_b: str):
    """
    判断两个 UTF-8 文本文件的内容是否相等。

    比较时忽略每个文件全部内容开头和结尾的空白字符，但保留正文内部
    的空格、制表符和换行差异。

    :param path_a: 第一个文件路径
    :param path_b: 第二个文件路径
    """
    content_a = _normalized_content(path_a)
    content_b = _normalized_content(path_b)
    if content_a != content_b:
        raise AssertionError(
            f"File contents differ: {path_a} != {path_b}\n"
            f"--- {path_a} ---\n{content_a}\n"
            f"--- {path_b} ---\n{content_b}"
        )


def different(path_a: str, path_b: str):
    """
    判断两个 UTF-8 文本文件的内容是否不相等。

    比较时忽略每个文件全部内容开头和结尾的空白字符；若内容相等，
    则抛出 AssertionError。

    :param path_a: 第一个文件路径
    :param path_b: 第二个文件路径
    """
    if _normalized_content(path_a) == _normalized_content(path_b):
        errmsg = f"File contents are the same: {path_a} == {path_b}"
        logger.error(errmsg)
        raise AssertionError(errmsg)


def slice(source: str, line: int, target: str):
    """
    提取 UTF-8 文本文件的指定行并写入新文件。

    行号从 1 开始，输出文件末尾统一保留一个换行符。

    :param source: 源文件路径
    :param line: 要提取的行号
    :param target: 输出文件路径
    """
    if line < 1:
        raise ValueError("line must be greater than or equal to 1")

    lines = Path(source).read_text(encoding="utf-8").splitlines()
    if line > len(lines):
        raise IndexError(f"Line {line} is out of range for file: {source}")

    result = lines[line - 1]
    Path(target).write_text(result + "\n", encoding="utf-8")
    return result


def write(path: str, content: str):
    """
    使用 UTF-8 编码覆盖写入文本文件。

    :param path: 文件路径
    :param content: 待写入的内容
    """
    Path(path).write_text(content, encoding="utf-8")


def append(path: str, content: str):
    """
    在 UTF-8 文本文件末尾添加一行。

    若原文件末尾没有换行符则自动补充；content 末尾已有的换行符会被
    规范化为一个换行符，content 内部不允许包含换行。

    :param path: 文件路径
    :param content: 待添加的一行内容
    """
    line = content.rstrip("\r\n")
    if "\n" in line or "\r" in line:
        raise ValueError("content must contain only one line")

    file_path = Path(path)
    existing = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
    separator = "" if not existing or existing.endswith(("\n", "\r")) else "\n"
    with file_path.open("a", encoding="utf-8") as stream:
        stream.write(separator + line + "\n")


def remove(paths: List[str]):
    """
    删除一组文件。

    不存在的文件会被忽略；目录或无法删除的文件会正常抛出异常。

    :param paths: 待删除的文件路径列表
    """
    for path in paths:
        file_path = Path(path)
        if file_path.exists() or file_path.is_symlink():
            file_path.unlink()


def remove_recursive(paths: List[str]):
    """
    递归删除一组文件、符号链接或文件夹及其全部内容。

    不存在的路径会被忽略。

    :param paths: 待递归删除的路径列表
    """
    for path in paths:
        target = Path(path)
        if not target.exists() and not target.is_symlink():
            continue
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)


def create_directory(paths: List[str]):
    """
    递归创建一组文件夹。

    父文件夹会被自动创建，目标文件夹已存在时不会报错。

    :param paths: 待创建的文件夹路径列表
    """
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)


def command(path: str, command: str):
    """
    在指定目录下执行 shell 命令。

    标准输出会记录为 info；标准错误在命令成功时记录为 warning，
    在命令失败时记录为 error。非零退出状态会抛出异常。

    :param path: 执行命令的工作目录
    :param command: 待执行的 shell 命令
    """
    import subprocess

    result = subprocess.run(
        command,
        shell=True,
        cwd=path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    stdout = result.stdout.rstrip("\r\n")
    if stdout:
        logger.info(stdout)

    stderr = result.stderr.rstrip("\r\n")
    if stderr:
        if result.returncode == 0:
            logger.warning(stderr)
        else:
            logger.error(stderr)

    if result.returncode != 0:
        logger.error(
            f"Command failed with exit code {result.returncode}: {command}"
        )
        raise RuntimeError(f"Command exited with code {result.returncode}")
    return stdout
