"""
P2-20: 文本截断辅助函数 —— 统一管理 [:N] + '...' 模板散落各处的反模式。

约定：
  - truncate(text, n, suffix='…') -> str
  - 长度 <= n 时原样返回，不加 suffix（避免 'short...' 误导用户）
  - 默认 suffix='…'（单字符省略号），兼容中文排版
  - 所有 max-length 模板都走这个函数；禁止再内联 text[:N] + '...'
"""
from typing import Optional


def truncate(text: Optional[str], n: int = 200, suffix: str = '…') -> str:
    """截断 text 到 n 字符以内，超过时附加 suffix。

    Args:
        text: 原始文本；None 时返回空串
        n: 最大保留字符数
        suffix: 超过时附加的后缀，默认 '…'

    Returns:
        截断后的字符串
    """
    if not text:
        return ''
    if len(text) <= n:
        return text
    return text[:n] + suffix


def truncate_with_ellipsis(text: Optional[str], n: int = 200) -> str:
    """变体：固定用 ASCII '...' 作 suffix，便于日志/前端渲染。"""
    return truncate(text, n=n, suffix='...')