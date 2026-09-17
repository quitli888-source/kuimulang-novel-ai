"""
番茄小说AI创作系统 - 统一Console管理 V3

统一创建Rich Console实例，解决Windows编码问题。
所有模块通过 from console import get_console 获取Console。

P2-29: PYTHONIOENCODING / stdout reconfigure 已迁移到 backend/main.py
最开头（在所有业务模块 import 之前）。此文件保留 stdout reconfigure 作为
兜底，仅当 main.py 未先被加载时生效；正常启动顺序下不会重复执行。
"""
import sys
import io

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
