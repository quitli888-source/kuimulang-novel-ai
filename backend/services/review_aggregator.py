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

        l_p0 = _issues_by_level(lr.get("issues", []), "P0")
        l_p1 = _issues_by_level(lr.get("issues", []), "P1")
        c_p0 = _issues_by_level(cr.get("issues", []), "P0")
        c_p1 = _issues_by_level(cr.get("issues", []), "P1")
        e_p1 = er.get("weaknesses", []) if isinstance(er.get("weaknesses"), list) else []
        e_p0 = er.get("enhancement_suggestions", []) if isinstance(er.get("enhancement_suggestions"), list) else []

        logic_p0.extend(l_p0)
        logic_p1.extend(l_p1)
        consistency_p0.extend(c_p0)
        consistency_p1.extend(c_p1)

        parts_out.append({
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
        })

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
