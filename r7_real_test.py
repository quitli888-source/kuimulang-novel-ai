"""R7 真实 E2E：Part 1-3 + 3 Review Agents + 一致性/连贯性/可读性评分"""
import os
import sys
import json
import time

sys.path.insert(0, 'backend')

from core.sliding_window import SlidingWindow
from core.agents.part_writer_agent import PartWriterAgent
from core.agents.logic_review_agent import LogicReviewAgent
from core.agents.emotion_review_agent import EmotionReviewAgent
from core.agents.consistency_review_agent import ConsistencyReviewAgent
from core.cost_tracker import get_tracker
from openai import OpenAI


class State:
    def __init__(self, characters, world, outline, foreshadowing, window):
        self.characters = characters
        self.world_setting = world
        self.part_outline = outline
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


characters = [
    {"name": "林枫", "role": "主角", "identity": "前刑警", "core_trait": "执拗、敏锐",
     "motivation": "追查三年前搭档失踪案", "secret": "他收到过搭档的匿名警告"},
    {"name": "沈渊", "role": "搭档", "identity": "原刑侦队长", "core_trait": "冷静、缜密",
     "motivation": "潜伏在犯罪组织内部", "secret": "三年前的失踪是主动安排"},
]
world = ("江南雨城，警署与地下势力相互渗透。三年前雨夜搭档失踪案悬而未决。"
         "近年城市边缘出现以旧书商为幌子的情报网，暗号用红墨水书写。")
outline = [
    {"phase": "开端", "title": "雨夜重逢", "core_event": "林枫推开尘封的书房门",
     "emotion_target": "悬疑与不安", "key_dialogue": "你终于来了",
     "end_hook": "墙上红字指向下一个地点", "pacing": "慢起", "causality": "承前：搭档失踪悬案"},
    {"phase": "发展", "title": "墨迹追凶", "core_event": "林枫解读红字暗号",
     "emotion_target": "紧张", "key_dialogue": "这不是失踪，是潜伏",
     "end_hook": "暗号指向钟楼", "pacing": "中等", "causality": "承 Part1 红字"},
    {"phase": "高潮", "title": "钟楼对峙", "core_event": "林枫找到沈渊",
     "emotion_target": "震撼", "key_dialogue": "三年，你终于来了",
     "end_hook": "组织已发现他们", "pacing": "快", "causality": "承 Part2 暗号"},
]
foreshadowing = [
    {"id": "F1", "content": "红字暗号", "plant_part": 1, "reveal_part": 2, "hint": "墙上红字用墨水写成"},
    {"id": "F2", "content": "钟楼线索引出沈渊", "plant_part": 2, "reveal_part": 3, "hint": "旧书商在钟楼"},
]

print("=" * 70)
print("R7 真实 E2E — step-3.7-flash × SlidingWindow × PartWriter × 3 Review")
print("=" * 70)

# 验证 STEP_API_KEY
with open('.env') as f:
    for line in f:
        if line.startswith('STEP_API_KEY='):
            os.environ['STEP_API_KEY'] = line.split('=', 1)[1].strip()
            break
print(f"STEP_API_KEY 已加载: {os.environ['STEP_API_KEY'][:20]}...")

# 真实写 3 个 Part
window = SlidingWindow(window_size=3)
state = State(characters, world, outline, foreshadowing, window)
writer = PartWriterAgent()
tracker = get_tracker()

parts_text = {}
review_results = {}

for part_num in [1, 2, 3]:
    print(f"\n--- 写 Part {part_num} ---")
    t0 = time.time()
    result = writer.execute(state, part_num)
    dt = time.time() - t0
    text = result.get('content', '') if isinstance(result, dict) else result
    word_count = len(text)
    print(f"  耗时: {dt:.1f}s  字数: {word_count}")
    parts_text[part_num] = text
    state.parts[str(part_num)] = text
    summary = text[:200]
    state.part_summaries[str(part_num)] = summary
    window.add_part(part_num, text, summary)

# 跑 3 个 Review Agents（验证 Bug B 修复 + 一致性/连贯性）
print("\n" + "=" * 70)
print("Review Agents（Part 1, 2, 3 全部评估）")
print("=" * 70)
for part_num in [1, 2, 3]:
    text = parts_text[part_num]
    review_state = State(characters, world, outline, foreshadowing, window)
    review_state.parts = dict(state.parts)
    review_state.part_summaries = dict(state.part_summaries)
    review_state.character_state_track = dict(state.character_state_track)

    print(f"\n--- Review Part {part_num} ---")
    for AgentCls in [LogicReviewAgent, EmotionReviewAgent, ConsistencyReviewAgent]:
        agent = AgentCls()
        t0 = time.time()
        try:
            r = agent.execute(review_state, part_num, text)
            dt = time.time() - t0
            if part_num not in review_results:
                review_results[part_num] = {}
            review_results[part_num][agent.__class__.__name__] = {
                "result": r,
                "duration": dt,
                "success": True,
            }
            print(f"  {agent.__class__.__name__}: OK in {dt:.1f}s")
            if r:
                if 'overall_score' in r:
                    print(f"    overall_score: {r['overall_score']}")
                if 'emotion_score' in r:
                    print(f"    emotion_score: {r['emotion_score']}")
                if 'pass' in r:
                    print(f"    pass: {r['pass']}")
                if 'issues' in r and r['issues']:
                    p0 = sum(1 for i in r['issues'] if i.get('level') == 'P0')
                    p1 = sum(1 for i in r['issues'] if i.get('level') == 'P1')
                    print(f"    issues: P0x{p0} P1x{p1}")
        except Exception as e:
            print(f"  {agent.__class__.__name__}: FAIL {type(e).__name__}: {str(e)[:200]}")
            review_results.setdefault(part_num, {})[agent.__class__.__name__] = {
                "error": str(e),
                "success": False,
            }

# 成本统计（验证 Bug C）
print("\n" + "=" * 70)
print("成本统计（Bug C 验证）")
print("=" * 70)
summary = tracker.get_summary()
print(f"total_calls: {summary.get('total_calls')}")
print(f"total_tokens: {summary.get('total_tokens')}")
print(f"estimated_cost: ¥{summary.get('estimated_cost', 0):.4f}")

# 保存完整产出到 JSON
output = {
    "parts": parts_text,
    "review_results": review_results,
    "cost_summary": summary,
}
with open('r7_real_output.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\n完整产出已存: r7_real_output.json")
print(f"Part 1 长度: {len(parts_text.get(1, ''))}")
print(f"Part 2 长度: {len(parts_text.get(2, ''))}")
print(f"Part 3 长度: {len(parts_text.get(3, ''))}")
