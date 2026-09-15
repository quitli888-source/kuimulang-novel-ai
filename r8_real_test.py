"""R8 真实 E2E：验证 Logic Part 2/3 ≥ 7/10 + Bug G cost 字段"""
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
        self.established_facts = None  # R8 新增

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

# 加载 STEP_API_KEY
with open('.env') as f:
    for line in f:
        if line.startswith('STEP_API_KEY='):
            os.environ['STEP_API_KEY'] = line.split('=', 1)[1].strip()
            break

print("=" * 70)
print("R8 真实 E2E — Logic Agent V5 + EstablishedFacts + Bug G cost")
print("=" * 70)

window = SlidingWindow(window_size=3)
state = State(characters, world, outline, foreshadowing, window)
writer = PartWriterAgent()
tracker = get_tracker()

# 写 3 个 Part
parts_text = {}
for part_num in [1, 2, 3]:
    print(f"\n--- 写 Part {part_num} ---")
    t0 = time.time()
    result = writer.execute(state, part_num)
    dt = time.time() - t0
    text = result.get('content', '') if isinstance(result, dict) else result
    print(f"  耗时: {dt:.1f}s  字数: {len(text)}")
    parts_text[part_num] = text
    state.parts[str(part_num)] = text
    state.part_summaries[str(part_num)] = text[:200]
    window.add_part(part_num, text, text[:200])

# 跑 Logic / Emotion / Consistency
review_results = {}
for part_num in [1, 2, 3]:
    text = parts_text[part_num]
    rs = State(characters, world, outline, foreshadowing, window)
    rs.parts = dict(state.parts)
    rs.part_summaries = dict(state.part_summaries)
    rs.character_state_track = dict(state.character_state_track)
    rs.established_facts = state.established_facts

    print(f"\n--- Review Part {part_num} ---")
    for AgentCls in [LogicReviewAgent, EmotionReviewAgent, ConsistencyReviewAgent]:
        agent = AgentCls()
        t0 = time.time()
        try:
            r = agent.execute(rs, part_num, text)
            dt = time.time() - t0
            review_results.setdefault(part_num, {})[agent.__class__.__name__] = {
                "result": r, "duration": dt, "success": True,
            }
            # 打印关键字段
            score_keys = ['score', 'overall_score', 'emotion_score']
            scores = {k: r.get(k) for k in score_keys if k in r}
            pass_v = r.get('pass')
            print(f"  {agent.__class__.__name__}: OK in {dt:.1f}s  scores={scores}  pass={pass_v}")
            if 'p0_count' in r:
                print(f"    p0_count={r.get('p0_count')}  p1_count={r.get('p1_count')}")
                print(f"    issues_summary={r.get('issues_summary', '')[:150]}")
        except Exception as e:
            print(f"  {AgentCls.__name__}: FAIL {type(e).__name__}: {str(e)[:200]}")
            review_results.setdefault(part_num, {})[agent.__class__.__name__] = {
                "error": str(e), "success": False,
            }

# 成本（Bug G 验证）
summary = tracker.get_summary()
print("\n" + "=" * 70)
print("成本（Bug G 验证）")
print("=" * 70)
print(f"total_calls: {summary.get('total_calls')}")
print(f"total_tokens: {summary.get('total_tokens')}")
print(f"estimated_cost: ¥{summary.get('estimated_cost', 0):.4f}")
print(f"estimated_cost_rmb: ¥{summary.get('estimated_cost_rmb', 0):.4f}")

# 评分汇总
print("\n" + "=" * 70)
print("Logic 评分（核心目标 ≥ 7）")
print("=" * 70)
for part_num in [1, 2, 3]:
    if part_num in review_results and 'LogicReviewAgent' in review_results[part_num]:
        r = review_results[part_num]['LogicReviewAgent']['result']
        score = r.get('score') or r.get('overall_score') or 'N/A'
        print(f"  Part {part_num} Logic: {score}/10")

# 保存
with open('r8_real_output.json', 'w', encoding='utf-8') as f:
    json.dump({
        "parts": parts_text,
        "review_results": review_results,
        "cost_summary": summary,
    }, f, ensure_ascii=False, indent=2)
print(f"\n完整产出: r8_real_output.json")
