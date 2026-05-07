"""
番茄小说AI创作系统 - 统一Console管理 V3

统一创建Rich Console实例，解决Windows编码问题。
所有模块通过 from console import get_console 获取Console。
"""
import sys
import os
import io

# 修复Windows终端编码
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

from rich.console import Console

_console = None


def get_console() -> Console:
    """获取全局唯一的Rich Console实例"""
    global _console
    if _console is None:
        if sys.platform == "win32":
            _utf8_stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
            _console = Console(file=_utf8_stdout, highlight=False)
        else:
            _console = Console(highlight=False)
    return _console
