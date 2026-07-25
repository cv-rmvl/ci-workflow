import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

# 统一编码格式
for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")

from framework import logger, run_plan
from framework.runner import PlanExecutionError

# Enviroment configuration
class Config:
    root_dir = Path(__file__).resolve().parent
    adapt_dir = root_dir / "adapter"
    adapt_build_dir = adapt_dir / "build"
    adapt_bin_dir = adapt_dir / "bin"
    components_dir = root_dir / "components"
    plans_dir = root_dir / "plans"

def parse_args():
    """
    Parse command line arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("plans", nargs="+", metavar="PLAN",
        help=("Test plans to execute. A bare name is resolved from kdt/plans and automatically gets the .yml suffix."),
    )
    parser.add_argument("--rmvl-dir", type=Path, default=None,
        help=("RMVL install prefix. RMVLConfig.cmake is searched recursively "
              "under this directory before configuring adapters."),
    )
    return parser.parse_args()

def resolve_plan(plan: str) -> Path:
    """Resolve a plan name or explicit path to a YAML file."""
    path = Path(plan)
    if path.is_file():
        return path.resolve()

    if path.suffix.lower() not in (".yml", ".yaml"):
        path = path.with_suffix(".yml")

    if path.parent == Path("."):
        path = Config.plans_dir / path

    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Test plan file not found: {path}")
    return path

def resolve_rmvl_dir(prefix: Path) -> Path:
    """在 RMVL 安装前缀中查找包含 RMVLConfig.cmake 的目录。"""
    prefix = prefix.expanduser().resolve()
    if not prefix.is_dir():
        raise FileNotFoundError(f"RMVL install prefix not found: {prefix}")

    config_files = sorted(prefix.rglob("RMVLConfig.cmake"))
    if not config_files:
        raise FileNotFoundError(
            f"RMVLConfig.cmake not found under: {prefix}"
        )

    rmvl_dir = config_files[0].parent
    logger.info(f"Found RMVL package config: {config_files[0]}")
    return rmvl_dir

def configure_rmvl_environment(rmvl_dir: Path):
    """让测试用例启动的 CMake 子进程也能找到 RMVL 包配置。"""
    current = os.environ.get("CMAKE_PREFIX_PATH")
    prefixes = [str(rmvl_dir)]
    if current:
        prefixes.append(current)
    os.environ["CMAKE_PREFIX_PATH"] = os.pathsep.join(prefixes)

def execute(cmd, silent=True):
    """
    Run a command.

    :param cmd: Command to execute as a list of strings.
    :param silent: Whether to suppress stdout.
    """
    command = " ".join(map(str, cmd))
    if silent:
        result = subprocess.run(cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace"
        )
    else:
        result = subprocess.run(cmd)

    if result.returncode == 0:
        return

    stderr = result.stderr.strip() if silent and result.stderr else ""
    message = str(stderr) or f"{command} exited with code {result.returncode}"
    logger.error(message)
    print(f"Command failed: {command}", file=sys.stderr)
    if stderr:
        print(stderr, file=sys.stderr)
    raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr if silent else None)

def build_adapter(rmvl_dir=None):
    """
    Adapter building

    :param rmvl_dir: 包含 RMVLConfig.cmake 的 CMake 包配置目录。
    """
    exe_cmd = ["cmake", "-B", str(Config.adapt_build_dir), "-S", str(Config.adapt_dir)]
    if rmvl_dir is not None:
        exe_cmd.append(f"-DRMVL_DIR={rmvl_dir}")
    execute(exe_cmd)

    exe_cmd = ["cmake", "--build", str(Config.adapt_build_dir), "--parallel"]
    if os.name == "nt":
        exe_cmd.extend(["--config", "Release"])
    execute(exe_cmd)
    logger.success("Adapter build succeeded")

def command_line() -> str:
    """返回包含 Python 解释器参数在内的实际启动命令。"""
    args = getattr(sys, "orig_argv", [sys.executable, *sys.argv])
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)

def main() -> int:
    """Build adapters and execute all plans specified on the command line."""
    logger.info(
        f"Run script: {Path(__file__).resolve()}; command: {command_line()}"
    )
    args = parse_args()
    try:
        plan_files = [resolve_plan(plan) for plan in args.plans]
        rmvl_dir = (
            resolve_rmvl_dir(args.rmvl_dir)
            if args.rmvl_dir is not None
            else None
        )
        if rmvl_dir is not None:
            configure_rmvl_environment(rmvl_dir)
    except FileNotFoundError as exc:
        logger.error(exc)
        print(exc, file=sys.stderr)
        return 2

    try:
        build_adapter(rmvl_dir)
    except Exception as exc:
        logger.error(
            "Adapter build failed; test plans were not executed: "
            f"{type(exc).__name__}: {exc}"
        )
        if isinstance(exc, subprocess.CalledProcessError):
            return exc.returncode or 1
        return 1

    failed = False
    for plan_file in plan_files:
        logger.info(f"Run plan: {plan_file}")
        try:
            run_plan(plan_file)
        except PlanExecutionError:
            failed = True
            logger.error(f"Plan failed: {plan_file}")
        except Exception as exc:
            failed = True
            logger.error(
                f"Plan failed: {plan_file}: "
                f"{type(exc).__name__}: {exc}"
            )
        else:
            logger.success(f"Plan passed: {plan_file}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
