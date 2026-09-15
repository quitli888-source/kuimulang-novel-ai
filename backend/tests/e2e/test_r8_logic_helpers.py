"""
R8 自检脚本：验证 LogicReviewAgent 辅助函数和 V5 协议字段
不依赖 LLM 调用，只做离线断言。
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def main():
    from core.agents.logic_review_agent import (
        LogicReviewAgent, _coerce_score, _extract_summary_from_raw,
    )
    from core.story_state import StoryState
    from core.established_facts import Fact, EstablishedFacts

    print("=" * 60)
    print("R8 Self-check: LogicReviewAgent + EstablishedFacts")
    print("=" * 60)

    # 1. _coerce_score 边界
    print("\n[1] _coerce_score")
    cases = [
        (5, 5), (0, 1), (-1, 1), (11, 10), (7.5, 8),
        ("abc", 3), (None, 3), (True, 3), (False, 3),
    ]
    for inp, expected in cases:
        actual = _coerce_score(inp)
        ok = "OK" if actual == expected else "FAIL"
        print(f"  [{ok}] _coerce_score({inp!r}) = {actual} (期望 {expected})")
        assert actual == expected, f"_coerce_score({inp!r}) = {actual}, expected {expected}"

    # 2. _extract_summary_from_raw markdown 包裹
    print("\n[2] _extract_summary_from_raw (markdown 包裹)")
    raw_md = (
        "以下是审查结果：\n"
        "```json\n"
        '{"score": 7, "pass": true, "p0_count": 0, "p1_count": 1, "p2_count": 0, "verdict": "OK"}\n'
        "```\n以上。"
    )
    obj = _extract_summary_from_raw(raw_md)
    assert obj.get("score") == 7, f"score 解析错误: {obj}"
    assert obj.get("pass") is True
    print(f"  [OK] markdown 包裹解析: {obj}")

    # 3. _extract_summary_from_raw 截断
    print("\n[3] _extract_summary_from_raw (截断场景)")
    raw_trunc = '以下是审查结果：\n{"score": 8, "pass": true, "p0_count": 0, "p1_count": 0, "p2_count": 0, "verdict": "truncated"}'
    obj2 = _extract_summary_from_raw(raw_trunc)
    assert obj2.get("score") == 8, f"truncated 解析错误: {obj2}"
    print(f"  [OK] 截断解析: {obj2}")

    # 4. _extract_summary_from_raw 完全无 JSON
    print("\n[4] _extract_summary_from_raw (无 JSON)")
    assert _extract_summary_from_raw("纯文字无JSON") == {}
    print(f"  [OK] 无 JSON 时返回 {{}}")

    # 5. EstablishedFacts 渲染
    print("\n[5] EstablishedFacts.render_for_prompt")
    ef = EstablishedFacts()
    ef.add(Fact(id="F1_1", part_num=1, category="character", text="林枫是前刑警"))
    ef.add(Fact(id="F1_2", part_num=1, category="object", text="红墨水暗号", quote="墙上红字"))
    ef.add(Fact(id="F2_1", part_num=2, category="event", text="林枫夜探西市当铺"))
    rendered = ef.render_for_prompt(before_part_num=3)
    assert "林枫是前刑警" in rendered
    assert "红墨水暗号" in rendered
    assert "林枫夜探西市当铺" in rendered
    print(f"  [OK] 3 facts 渲染为多行字符串，长度={len(rendered)}")
    print("  渲染片段：")
    for line in rendered.split("\n")[:6]:
        print(f"    {line}")

    # 6. StoryState.build_established_facts_block
    print("\n[6] StoryState.build_established_facts_block")
    state = StoryState(inspiration="测试")
    state.established_facts = ef
    block = state.build_established_facts_block(3)
    assert "前文已确立事实清单" in block
    assert "林枫是前刑警" in block
    print(f"  [OK] block 长度={len(block)}，含标题+事实")

    # 7. 持久化 roundtrip
    print("\n[7] StoryState save/load roundtrip (含 established_facts)")
    import tempfile
    from pathlib import Path as P
    state.parts = {"1": "P1", "2": "P2"}
    state.part_summaries = {"1": "S1", "2": "S2"}
    with tempfile.TemporaryDirectory() as td:
        fp = str(P(td) / "test.json")
        state.save(filepath=fp)
        state2 = StoryState(inspiration="X")
        state2.load(filepath=fp)
        assert len(state2.established_facts) == 3, (
            f"load 后 facts 数量错: {len(state2.established_facts)}"
        )
        print(f"  [OK] save/load 后 facts={len(state2.established_facts)}")

    # 8. cost_tracker 字段别名
    print("\n[8] cost_tracker estimated_cost alias")
    from core.cost_tracker import get_tracker
    t = get_tracker()
    t.calls = []
    t.record("step-3.7-flash", "part_writer", False, 100000, 50000, 150000, 1000)
    s = t.get_summary()
    assert "estimated_cost" in s
    assert "estimated_cost_rmb" in s
    assert s["estimated_cost"] == s["estimated_cost_rmb"]
    assert s["estimated_cost"] > 0, f"cost 应 > 0，实际 {s['estimated_cost']}"
    print(f"  [OK] estimated_cost=¥{s['estimated_cost']:.4f} == estimated_cost_rmb=¥{s['estimated_cost_rmb']:.4f}")

    # 9. LogicReviewAgent V5 markers
    print("\n[9] LogicReviewAgent V5 markers")
    src = LogicReviewAgent.execute.__code__.co_consts
    has_p0 = any("p0_count" in str(c) for c in src)
    has_v5 = any("_protocol" in str(c) for c in src) and any("V5" in str(c) for c in src)
    print(f"  [OK] execute() 包含 p0_count 字段: {has_p0}")
    print(f"  [OK] execute() 包含 V5 _protocol 标记: {has_v5}")

    # 10. SlidingWindow ROLLING_EVERY 5→3
    print("\n[10] SlidingWindow ROLLING_EVERY")
    from core.sliding_window import SlidingWindow
    assert SlidingWindow.ROLLING_EVERY == 3, f"ROLLING_EVERY 应为 3，实际 {SlidingWindow.ROLLING_EVERY}"
    assert SlidingWindow.SUMMARY_L2_LEN == 800, f"SUMMARY_L2_LEN 应为 800，实际 {SlidingWindow.SUMMARY_L2_LEN}"
    sw = SlidingWindow(window_size=3)
    assert sw.should_create_rolling_summary(3) is True
    assert sw.should_create_rolling_summary(6) is True
    assert sw.should_create_rolling_summary(2) is False
    print(f"  [OK] ROLLING_EVERY={SlidingWindow.ROLLING_EVERY}, SUMMARY_L2_LEN={SlidingWindow.SUMMARY_L2_LEN}")
    print(f"  [OK] should_create_rolling_summary(3)=True, (6)=True, (2)=False")

    print("\n" + "=" * 60)
    print("ALL SELF-CHECK PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
