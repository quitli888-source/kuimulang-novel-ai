"""
R18-P1-8: Review 聚合器 —— 把 per-Part 评审结果聚合成顶层摘要 + parts[] 数组。

从 writing_service._aggregate_review_results 静态方法抽离出来，便于独立测试。
"""
from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _issues_by_level(issues: list, level: str) -> list:
    if not isinstance(issues, list):
        return []
    return [i for i in issues if isinstance(i, dict) and i.get("level") == level]


def _count_by_level(result: dict, level: str, count_key: str) -> list:
    """R2-8: 按 level 取 issue 列表 —— issues 数组优先，为空时回退到 per-part 数值字段。

    背景：logic_review 的 V5 短 JSON 协议（prompts/logic_review.txt:44,62）明确
    "issues 列表在主 JSON 中省略"，agent 只输出数值 p0_count/p1_count 字段
    （logic_review_agent.py:228,245,248）。此前聚合器只从 issues 数组派生计数 →
    logic.p0_count 恒 0，门禁 total_p0 = logic.p0_count + consistency.p0_count
    永远漏掉 logic 维度的 P0（G4 指标系统性低估）。"issues 优先、数值兜底"对
    旧式带 issues 的结果行为不变（向后兼容）。
    """
    issues = _issues_by_level(result.get("issues", []), level)
    if issues:
        return issues
    count = result.get(count_key)
    try:
        n = int(count or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        # 数值兜底：合成占位 issue 计数（V5 协议无 issue 明细，top_issue 显示提示）
        return [{'level': level, 'description': f'（V5 短协议：{count_key}={n}，无 issue 明细）'} for _ in range(n)]
    return []


def _avg(xs: list) -> float:
    return round(sum(xs) / len(xs), 2) if xs else 0.0


def _first_issue(issues: list) -> str:
    if not issues:
        return ""
    i0 = issues[0]
    return (i0.get("description") or i0.get("suggestion") or "") if isinstance(i0, dict) else str(i0)


def aggregate_review_results(per_part_results: list) -> dict:
    """把 per-Part 评审结果聚合成顶层摘要 + parts[] 数组。

    输入：[{"part": N, "logic_result": {...}, "emotion_result": {...}, "consistency_result": {...}}, ...]
    输出：{
        "logic":       {avg_score, pass, total_issues, top_issue, parts_count, ...},
        "emotion":     {avg_score, pass, avg_resonance, avg_immersion, parts_count},
        "consistency": {avg_score, pass, total_issues, top_issue, parts_count, ...},
        "parts": [
            {"part": N, "logic_score": ..., "emotion_score": ..., "consistency_score": ...,
             "p0_issues": [...], "p1_issues": [...], "summary": "..."},
            ...
        ]
    }
    """
    logic_scores: list = []
    emotion_scores: list = []
    consistency_scores: list = []
    resonance_scores: list = []
    immersion_scores: list = []
    logic_pass: list = []
    emotion_pass: list = []
    consistency_pass: list = []
    logic_p0: list = []
    logic_p1: list = []
    consistency_p0: list = []
    consistency_p1: list = []

    parts_out: list = []

    for entry in per_part_results:
        part_num = entry.get("part")
        lr = entry.get("logic_result") or {}
        er = entry.get("emotion_result") or {}
        cr = entry.get("consistency_result") or {}

        l_score = _f(lr.get("overall_score"), 0.0)
        e_score = _f(er.get("emotion_score"), 0.0)
        c_score = _f(cr.get("overall_score"), 0.0)

        logic_scores.append(l_score)
        emotion_scores.append(e_score)
        consistency_scores.append(c_score)
        resonance_scores.append(_f(er.get("resonance_score"), 0.0))
        immersion_scores.append(_f(er.get("immersion_score"), 0.0))

        logic_pass.append(bool(lr.get("pass", l_score >= 6)))
        emotion_pass.append(bool(er.get("pass", e_score >= 6)))
        consistency_pass.append(bool(cr.get("pass", c_score >= 6)))

        # R2-8: issues 优先、数值兜底（V5 短协议下 logic 只有 p0_count/p1_count 数值）
        l_p0 = _count_by_level(lr, "P0", "p0_count")
        l_p1 = _count_by_level(lr, "P1", "p1_count")
        c_p0 = _count_by_level(cr, "P0", "p0_count")
        c_p1 = _count_by_level(cr, "P1", "p1_count")
        e_p1 = er.get("weaknesses", []) if isinstance(er.get("weaknesses"), list) else []
        # R2-8: e_p0 修正 —— enhancement_suggestions 是情感增强建议，不是 P0 issues，
        # 此前误计入 parts[].p0_issues 污染报告展示（不影响门禁口径，但误导排查）
        e_p0 = []

        logic_p0.extend(l_p0)
        logic_p1.extend(l_p1)
        consistency_p0.extend(c_p0)
        consistency_p1.extend(c_p1)

        part_entry = {
            "part": part_num,
            "logic_score": l_score,
            "emotion_score": e_score,
            "consistency_score": c_score,
            "p0_issues": list(l_p0) + list(c_p0) + list(e_p0),
            "p1_issues": list(l_p1) + list(c_p1) + list(e_p1),
            "summary": (
                f"逻辑{l_score}/10 "
                f"情感{e_score}/10 "
                f"一致{c_score}/10"
            ),
        }
        # R1-J: 修复回路标注 —— 仅在触发过修复时追加（无修复时输出与改前逐字节一致）
        if "revision_attempted" in entry:
            part_entry["revision_attempted"] = bool(entry.get("revision_attempted"))
            part_entry["revision_passed"] = bool(entry.get("revision_passed"))
        parts_out.append(part_entry)

    return {
        "logic": {
            "avg_score": _avg(logic_scores),
            "pass": all(logic_pass) if logic_pass else False,
            "total_issues": len(logic_p0) + len(logic_p1),
            "p0_count": len(logic_p0),
            "p1_count": len(logic_p1),
            "top_issue": _first_issue(logic_p0) or _first_issue(logic_p1),
            "parts_count": len(per_part_results),
        },
        "emotion": {
            "avg_score": _avg(emotion_scores),
            "pass": all(emotion_pass) if emotion_pass else False,
            "avg_resonance": _avg(resonance_scores),
            "avg_immersion": _avg(immersion_scores),
            "parts_count": len(per_part_results),
        },
        "consistency": {
            "avg_score": _avg(consistency_scores),
            "pass": all(consistency_pass) if consistency_pass else False,
            "total_issues": len(consistency_p0) + len(consistency_p1),
            "p0_count": len(consistency_p0),
            "p1_count": len(consistency_p1),
            "top_issue": _first_issue(consistency_p0) or _first_issue(consistency_p1),
            "parts_count": len(per_part_results),
        },
        "parts": parts_out,
    }
