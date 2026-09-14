"""
真实端到端冒烟测试：
- 手工驱动 PartWriterAgent + SlidingWindow
- 用 step-3.7-flash 真实生成
- 验证：滑动窗口机制 + 分块生成 + review_agent 调用契约
"""
import os
import sys
import json
import time
import io

sys.path.insert(0, 'backend')

# 强制注入真实 key
os.environ['STEP_API_KEY'] = '2AUHLIl7GnTbiSC0G9EwAX5OJQuKcA2XDk8vbvArNISugDJUnXw0fyDnJACyFR6e7'

from core.sliding_window import SlidingWindow
from core.agents.part_writer_agent import PartWriterAgent
from core.agents.logic_review_agent import LogicReviewAgent
from core.agents.emotion_review_agent import EmotionReviewAgent
from core.agents.consistency_review_agent import ConsistencyReviewAgent
from core.cost_tracker import get_tracker


class MockState:
    """最小化的 state，给 PartWriter 用"""
    def __init__(self, characters, world_setting, part_outline, foreshadowing, window):
        self.characters = characters
        self.world_setting = world_setting
        self.part_outline = part_outline
        self.foreshadowing = foreshadowing
        self.window = window
        self.parts = {}
        self.part_summaries = {}
        self.current_plot_state = ""
        self.character_state_track = {}

    def get_part_context(self, part_num):
        return self.window.build(
            part_num,
            characters=self.characters,
            world_setting=self.world_setting,
            outline=self.part_outline[part_num - 1] if part_num <= len(self.part_outline) else None,
        )


print("=" * 70)
print("【真实 E2E 冒烟】 step-3.7-flash × SlidingWindow × PartWriter")
print("=" * 70)

# ---- 1) 准备小说设定 ----
characters = [
    {"name": "林枫", "role": "主角", "identity": "前刑警", "core_trait": "执拗、敏锐",
     "motivation": "追查三年前的搭档失踪案", "secret": "他收到过搭档的匿名警告"},
    {"name": "沈渊", "role": "搭档", "identity": "原刑侦队长", "core_trait": "冷静、缜密",
     "motivation": "潜伏在犯罪组织内部", "secret": "三年前的失踪是主动安排"},
]
world_setting = (
    "江南雨城，警署与地下势力相互渗透。三年前的一场雨夜搭档失踪案悬而未决。"
    "近年城市边缘出现以旧书商为幌子的情报网，暗号用红墨水书写。"
)
part_outline = [
    {"phase": "开端", "title": "雨夜重逢", "core_event": "林枫推开尘封的书房门",
     "emotion_target": "悬疑与不安", "key_dialogue": "你终于来了",
     "end_hook": "墙上红字指向下一个地点", "pacing": "慢起",
     "causality": "承前：搭档失踪悬案"},
    {"phase": "发展", "title": "墨迹追凶", "core_event": "林枫解读红字暗号",
     "emotion_target": "紧张", "key_dialogue": "这不是失踪，是潜伏",
     "end_hook": "暗号指向钟楼", "pacing": "中等",
     "causality": "承 Part1 红字"},
    {"phase": "高潮", "title": "钟楼对峙", "core_event": "林枫找到沈渊",
     "emotion_target": "震撼", "key_dialogue": "三年，你终于来了",
     "end_hook": "组织已发现他们", "pacing": "快", "causality": "承 Part2 暗号"},
]
foreshadowing = [
    {"id": "F1", "content": "红字暗号", "plant_part": 1, "reveal_part": 2, "hint": "墙上红字用墨水写成"},
    {"id": "F2", "content": "钟楼线索引出沈渊", "plant_part": 2, "reveal_part": 3, "hint": "旧书商在钟楼"},
]

# ---- 2) 初始化滑动窗口 ----
window = SlidingWindow(window_size=3)
state = MockState(characters, world_setting, part_outline, foreshadowing, window)

# ---- 3) 写 3 个 Part 验证滑动窗口 ----
writer = PartWriterAgent()
tracker = get_tracker()
all_text = []

for part_num in [1, 2, 3]:
    print(f"\n--- 写 Part {part_num} ---")
    state.current_part = part_num

    t0 = time.time()
    result = writer.execute(state, part_num)
    dt = time.time() - t0

    text = result.get('content', '') if isinstance(result, dict) else result
    word_count = len(text)

    print(f"  耗时: {dt:.1f}s  字数: {word_count}  目标: {result.get('target_words') if isinstance(result, dict) else '?'}")

    # 写入窗口 + state
    state.parts[str(part_num)] = text
    summary = text[:200] + "..."
    state.part_summaries[str(part_num)] = summary
    window.add_part(part_num, text, summary)
    all_text.append((part_num, text))

# ---- 4) 打印产出 ----
print("\n" + "=" * 70)
print("【产出】三段连贯悬疑小说（约 {} 字）".format(sum(len(t) for _, t in all_text)))
print("=" * 70)
for part_num, text in all_text:
    print(f"\n【Part {part_num}】")
    print(text)
    print()

# ---- 5) 检查滑动窗口状态 ----
print("\n" + "=" * 70)
print("【SlidingWindow 状态检查】")
print("=" * 70)
print(f"window.parts keys (最近 3 个 Part 原文): {sorted(window.parts.keys())}")
print(f"window.summaries keys (全部摘要): {sorted(window.summaries.keys())}")
print(f"window.rolling_summaries keys: {sorted(window.rolling_summaries.keys())}")
print(f"window.milestones keys: {sorted(window.milestones.keys())}")

# ---- 6) 测 review_agent 调用契约（之前 R1 的关键 bug）----
print("\n" + "=" * 70)
print("【Review Agent 调用契约验证】（R1 bug 修复点）")
print("=" * 70)
for part_num, text in all_text[:2]:
    review_state = MockState(characters, world_setting, part_outline, foreshadowing, window)
    review_state.parts[str(part_num)] = text
    review_state.part_summaries[str(part_num)] = text[:200]

    for AgentCls in [LogicReviewAgent, EmotionReviewAgent, ConsistencyReviewAgent]:
        agent = AgentCls()
        t0 = time.time()
        try:
            r = agent.execute(review_state, part_num, text)
            dt = time.time() - t0
            score_keys = [k for k in ['overall_score', 'emotion_score', 'pass'] if k in (r or {})]
            print(f"  Part {part_num} {agent.name}: OK in {dt:.1f}s  scores={score_keys}  pass={r.get('pass') if r else 'N/A'}")
        except Exception as e:
            print(f"  Part {part_num} {agent.name}: FAIL {type(e).__name__}: {str(e)[:120]}")

# ---- 7) 成本统计 ----
print("\n" + "=" * 70)
print("【成本统计】")
print("=" * 70)
summary = tracker.get_summary()
print(f"总调用次数: {summary.get('total_calls')}")
print(f"总 token: {summary.get('total_tokens')} (in: {summary.get('total_input_tokens')}, out: {summary.get('total_output_tokens')})")
print(f"预估成本: ¥{summary.get('estimated_cost', 0):.4f}")
print(f"按 agent 拆分: {summary.get('by_agent', {})}")
