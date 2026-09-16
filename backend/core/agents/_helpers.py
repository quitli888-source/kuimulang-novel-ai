"""
R18-P1-9: Review Agent 共享 helper。

把 LogicReviewAgent / EmotionReviewAgent / ConsistencyReviewAgent 三份一模一样的
`_sorted_part_nums` 静态方法提到本模块，三个 Agent 类继承即可。
"""
from typing import Any


def sorted_part_nums(state: Any) -> list[int]:
    """返回 state.part_summaries 中所有 part_num 的整数列表（从小到大）。

    Args:
        state: 任意具有 part_summaries 字段的对象（dict 或 dataclass）。

    Returns:
        list[int]: 所有 Part 编号升序排列；空时返回 []。
    """
    out: list[int] = []
    try:
        if hasattr(state, "part_summaries"):
            summaries = state.part_summaries
        else:
            summaries = state.get("part_summaries", {}) if isinstance(state, dict) else {}
        for k in summaries.keys():
            try:
                out.append(int(k))
            except (TypeError, ValueError):
                continue
    except Exception:
        return []
    out.sort()
    return out
