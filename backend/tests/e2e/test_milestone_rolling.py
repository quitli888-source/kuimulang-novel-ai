"""
R5-P3-5.2: test_milestone_rolling.py —— 验证 sliding_window 的 milestone + rolling 触发条件 + 摘要写入。

R8-P1-4 改动：
  - ROLLING_EVERY 5 → 3：触发点 Part 3 / 6 / 9 / 12 / 15 / 18 ...
  - SUMMARY_L2_LEN 500 → 800（更详细的二级摘要）
核心断言：
  - should_create_rolling_summary 在 Part 3 / 6 / 9 / 12 / 15 / 18 触发
  - should_create_milestone 在 Part 20 / 40 触发（不变）
  - add_rolling_summary(part_num, ...) 第一参数是 part_num
  - add_milestone(milestone_num, ...) 第一参数是 milestone_num（不是 part_num）
  - build() 输出包含【里程碑摘要】+【近期滚动摘要】

既支持 pytest 也支持直接 python 跑。
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def test_should_create_rolling_summary_triggers_at_3_6_9_12():
    """R8-P1-4 验证 rolling 摘要触发条件（每 3 Part 一次；5→3）"""
    from core.sliding_window import SlidingWindow
    window = SlidingWindow(window_size=3)
    # 触发点
    for pn in (3, 6, 9, 12, 15, 18, 21):
        assert window.should_create_rolling_summary(pn) is True, (
            f"Part {pn} 应触发 rolling 摘要"
        )
    # 不触发
    for pn in (1, 2, 4, 5, 7, 8, 10, 11):
        assert window.should_create_rolling_summary(pn) is False, (
            f"Part {pn} 不应触发 rolling 摘要"
        )
    print("[test_rolling_trigger] PASS (R8: 5→3 触发周期)")


def test_should_create_milestone_triggers_at_20_40():
    """验证 milestone 摘要触发条件（每 20 Part 一次）"""
    from core.sliding_window import SlidingWindow
    window = SlidingWindow(window_size=3)
    assert window.should_create_milestone(20) is True
    assert window.should_create_milestone(40) is True
    assert window.should_create_milestone(5) is False
    assert window.should_create_milestone(19) is False
    assert window.should_create_milestone(21) is False
    print("[test_milestone_trigger] PASS")


def test_add_rolling_summary_writes_part_num_key():
    """验证 add_rolling_summary(part_num, ...) 第一参数是 part_num"""
    from core.sliding_window import SlidingWindow
    window = SlidingWindow(window_size=3)
    # R8-P1-4: 触发点改为 3/6/9/12/15
    for pn in (3, 6, 9, 12, 15):
        window.add_rolling_summary(pn, f"Part {pn} 聚合摘要")
    for pn in (3, 6, 9, 12, 15):
        assert window.rolling_summaries[pn] == f"Part {pn} 聚合摘要"
    print("[test_rolling_add] PASS: rolling_summaries[3/6/9/12/15] 写入正确（R8: 5→3 触发周期）")


def test_add_milestone_writes_milestone_num_key():
    """验证 add_milestone(milestone_num, ...) 第一参数是 milestone_num（注意：不是 part_num）"""
    from core.sliding_window import SlidingWindow
    window = SlidingWindow(window_size=3)
    # Part 20 → milestone_num = 20 // 20 = 1
    # Part 40 → milestone_num = 40 // 20 = 2
    window.add_milestone(1, "Milestone #1 全文脉络...")
    window.add_milestone(2, "Milestone #2 全文脉络...")
    assert window.milestones[1] == "Milestone #1 全文脉络..."
    assert window.milestones[2] == "Milestone #2 全文脉络..."
    # 不应有 key=20 / key=40（避免 R5-P0-2 修复时把 part_num 误当 milestone_num 写入）
    assert 20 not in window.milestones
    assert 40 not in window.milestones
    print("[test_milestone_add] PASS: milestones[1/2] 写入正确（注意是 milestone_num）")


def test_build_includes_milestones_and_rolling_sections():
    """验证 build() 同时输出【里程碑摘要】+【近期滚动摘要】段"""
    from core.sliding_window import SlidingWindow
    window = SlidingWindow(window_size=3)
    # 灌入 1 个 Part 原文（在窗口内）+ 滚动 + 里程碑
    window.add_part(20, "Part 20 全文", "Part 20 一级摘要")
    window.add_rolling_summary(20, "Part 20 滚动摘要")
    window.add_milestone(1, "Milestone #1 内容")

    prompt = window.build(part_num=21)
    assert "【里程碑摘要】" in prompt, f"build() 缺【里程碑摘要】段: {prompt[:200]}"
    assert "里程碑 #1" in prompt, f"build() 缺里程碑 #1 内容: {prompt[:200]}"
    assert "【近期滚动摘要】" in prompt, f"build() 缺【近期滚动摘要】段: {prompt[:200]}"
    assert "Part 20 滚动摘要" in prompt, f"build() 缺滚动摘要内容: {prompt[:200]}"
    print("[test_build_sections] PASS: build() 同时包含 milestone + rolling 段")


def test_rolling_and_milestone_20_part_simulation():
    """模拟 20 个 Part 后状态：rolling[3/6/9/12/15/18] 有内容 + milestone[1] 有内容"""
    from core.sliding_window import SlidingWindow
    window = SlidingWindow(window_size=3)
    # 灌入 20 个 Part
    for i in range(1, 21):
        window.add_part(i, f"Part {i} 全文内容", f"Part {i} 一级摘要")
    # R8-P1-4: rolling 触发 5→3，20 Part 场景下应触发 6 次（3/6/9/12/15/18）
    for pn in [3, 6, 9, 12, 15, 18]:
        window.add_rolling_summary(pn, f"Part {pn} 滚动摘要")
    # 模拟外部生成的 1 次 milestone（milestone_num=1）
    window.add_milestone(1, "Milestone #1 全文脉络")

    assert len(window.rolling_summaries) == 6
    for pn in (3, 6, 9, 12, 15, 18):
        assert pn in window.rolling_summaries
    assert 1 in window.milestones
    print("[test_20_part_sim] PASS: rolling[3/6/9/12/15/18] + milestone[1] 齐全（R8: 5→3）")


# ---- 直接 python 跑 ----
if __name__ == "__main__":
    print("=" * 60)
    print("test_milestone_rolling.py —— R5-P3-5.2")
    print("=" * 60)

    tests = [
        ("should_create_rolling_summary", test_should_create_rolling_summary_triggers_at_3_6_9_12),
        ("should_create_milestone", test_should_create_milestone_triggers_at_20_40),
        ("add_rolling_summary", test_add_rolling_summary_writes_part_num_key),
        ("add_milestone", test_add_milestone_writes_milestone_num_key),
        ("build sections", test_build_includes_milestones_and_rolling_sections),
        ("20-part simulation", test_rolling_and_milestone_20_part_simulation),
    ]
    for name, fn in tests:
        print(f"\n[{name}]")
        fn()

    print("\n" + "=" * 60)
    print("ALL PASS")
    print("=" * 60)
