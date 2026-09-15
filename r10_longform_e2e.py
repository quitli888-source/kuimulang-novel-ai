"""R10 长篇真实 E2E：8 Part × 4 阶段叙事弧（开端→发展→高潮→结局）
目标：验证滑动窗口 + 事实库在长程上的一致性衰减
"""
import os
import sys
import json
import time
import re

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
        self.established_facts = None

    def get_part_context(self, part_num):
        return self.window.build(
            part_num,
            characters=self.characters,
            world_setting=self.world_setting,
            outline=self.part_outline[part_num - 1] if part_num <= len(self.part_outline) else None,
        )


# 8 Part 4 阶段叙事弧
characters = [
    {"name": "林枫", "role": "主角", "identity": "前刑警，三年前停职", "core_trait": "执拗、敏锐",
     "motivation": "追查三年前搭档失踪案", "secret": "收到过搭档的匿名警告"},
    {"name": "沈渊", "role": "搭档", "identity": "原刑侦队长", "core_trait": "冷静、缜密",
     "motivation": "潜伏在犯罪组织内部三年", "secret": "三年前失踪是主动安排"},
    {"name": "陈锋", "role": "反派", "identity": "组织执行官", "core_trait": "狠辣、狡诈",
     "motivation": "维护组织利益，灭口所有知情者", "secret": "组织头目是他亲哥哥"},
]
world = ("江南雨城，三年前雨夜刑警队长沈渊失踪，警署定性为叛逃。"
         "旧书商公会暗中经营情报网，暗号用红墨水书写。"
         "近年城市边缘多起失踪案都指向一个以钟楼为据点的神秘组织。")

# 4 阶段叙事弧：开端(1-2) → 发展(3-5) → 高潮(6-7) → 结局(8)
outline = [
    # 开端
    {"phase": "开端", "title": "雨夜旧宅", "core_event": "林枫收到匿名包裹，来到三年前沈渊失踪案关联的民国旧宅",
     "emotion_target": "悬疑不安", "key_dialogue": "你终于来了", "end_hook": "墙上红字指向旧书商公会",
     "pacing": "慢起", "causality": "开场"},
    {"phase": "开端", "title": "暗号浮现", "core_event": "林枫在旧宅发现红字暗号，与旧书商老板娘接头",
     "emotion_target": "紧张", "key_dialogue": "红墨名单里有你师父的名字",
     "end_hook": "老板娘给的铜书签指向钟楼", "pacing": "中等", "causality": "承 Part1 旧宅"},
    # 发展
    {"phase": "发展", "title": "钟楼线索", "core_event": "林枫潜入钟楼地下库房，发现三年前的案卷被封存",
     "emotion_target": "震撼", "key_dialogue": "沈渊没死，他换了身份",
     "end_hook": "库房被人发现，林枫逃离", "pacing": "中等", "causality": "承 Part2 铜书签"},
    {"phase": "发展", "title": "组织追杀", "core_event": "陈锋带人追杀林枫至废弃码头，林枫险些被俘",
     "emotion_target": "恐惧绝望", "key_dialogue": "把芯片交出来，给你全尸",
     "end_hook": "沈渊从暗处现身救下林枫", "pacing": "快", "causality": "承 Part3 库房"},
    {"phase": "发展", "title": "搭档重逢", "core_event": "沈渊揭示三年前潜伏真相，二人决定联手",
     "emotion_target": "复杂悲壮", "key_dialogue": "三年了，我一直在等你查到这里",
     "end_hook": "组织已锁定他们的位置", "pacing": "中等", "causality": "承 Part4 码头"},
    # 高潮
    {"phase": "高潮", "title": "真相大白", "core_event": "二人回警署揭发组织，发现内鬼是张勇副队长",
     "emotion_target": "愤怒震惊", "key_dialogue": "张勇，你就是内鬼",
     "end_hook": "陈锋带人包围警署", "pacing": "快", "causality": "承 Part5 联手"},
    {"phase": "高潮", "title": "钟楼对决", "core_event": "林枫沈渊与陈锋在钟楼顶层决战",
     "emotion_target": "悲壮激昂", "key_dialogue": "哥哥你错了，沈渊从来不是叛徒",
     "end_hook": "沈渊中枪坠楼", "pacing": "快", "causality": "承 Part6 内鬼"},
    # 结局
    {"phase": "结局", "title": "雨过天晴", "core_event": "林枫带沈渊的遗物回到旧宅，案件真相大白",
     "emotion_target": "悲而不伤", "key_dialogue": "沈渊，你的名字会写进警局档案第一页",
     "end_hook": "林枫重新穿上警服", "pacing": "慢收", "causality": "承 Part7 坠楼"},
]

foreshadowing = [
    {"id": "F1", "content": "红字暗号", "plant_part": 1, "reveal_part": 3, "hint": "墙上红字指向钟楼"},
    {"id": "F2", "content": "铜书签", "plant_part": 2, "reveal_part": 4, "hint": "铜书签指向码头"},
    {"id": "F3", "content": "沈渊潜伏", "plant_part": 2, "reveal_part": 5, "hint": "老板娘提及沈渊上周来过"},
    {"id": "F4", "content": "陈锋身份", "plant_part": 4, "reveal_part": 7, "hint": "陈锋手腕刺青"},
    {"id": "F5", "content": "张勇内鬼", "plant_part": 3, "reveal_part": 6, "hint": "档案室张勇签字"},
]

# 加载 STEP_API_KEY
with open('.env') as f:
    for line in f:
        if line.startswith('STEP_API_KEY='):
            os.environ['STEP_API_KEY'] = line.split('=', 1)[1].strip()
            break

print("=" * 70)
print("R10 长篇真实 E2E — 8 Part × 4 阶段叙事弧")
print("=" * 70)
print(f"角色: {len(characters)} 个")
print(f"Part 数: {len(outline)}")
print(f"伏笔: {len(foreshadowing)} 条")
print(f"目标叙事弧: 开端(1-2) → 发展(3-5) → 高潮(6-7) → 结局(8)")

window = SlidingWindow(window_size=3)
state = State(characters, world, outline, foreshadowing, window)
writer = PartWriterAgent()
tracker = get_tracker()

# 阶段进度跟踪
phase_progress = {"开端": 0, "发展": 0, "高潮": 0, "结局": 0}
parts_text = {}

# ====== 写 8 Part ======
for part_num in range(1, 9):
    phase = outline[part_num - 1]['phase']
    phase_progress[phase] += 1
    print(f"\n{'='*70}")
    print(f"Part {part_num}/8 [{phase}{phase_progress[phase]}/{ {'开端':2,'发展':3,'高潮':2,'结局':1}[phase] }] - {outline[part_num-1]['title']}")
    print(f"{'='*70}")
    t0 = time.time()
    result = writer.execute(state, part_num)
    dt = time.time() - t0
    text = result.get('content', '') if isinstance(result, dict) else result
    word_count = len(text)
    print(f"  完成: {dt:.1f}s, 字数={word_count}")
    parts_text[part_num] = text
    state.parts[str(part_num)] = text
    state.part_summaries[str(part_num)] = text[:200]
    window.add_part(part_num, text, text[:200])

# ====== 跑 8 Part × 3 Review = 24 次评分 ======
print(f"\n{'='*70}")
print("Review 阶段（8 Part × 3 Agents = 24 次评分）")
print(f"{'='*70}")

review_results = {}
for part_num in range(1, 9):
    text = parts_text[part_num]
    rs = State(characters, world, outline, foreshadowing, window)
    rs.parts = dict(state.parts)
    rs.part_summaries = dict(state.part_summaries)
    rs.character_state_track = dict(state.character_state_track)
    rs.established_facts = state.established_facts
    rs.part_outline = [o for o in outline]  # 完整 outline

    print(f"\n--- Review Part {part_num} ---")
    review_results[part_num] = {}
    for AgentCls in [LogicReviewAgent, EmotionReviewAgent, ConsistencyReviewAgent]:
        agent = AgentCls()
        t0 = time.time()
        try:
            r = agent.execute(rs, part_num, text)
            dt = time.time() - t0
            review_results[part_num][agent.__class__.__name__] = {
                "result": r, "duration": dt, "success": True,
            }
            score = r.get('score') or r.get('overall_score') or r.get('emotion_score') or 'N/A'
            pass_v = r.get('pass')
            print(f"  {agent.__class__.__name__}: OK in {dt:.1f}s  score={score}/10  pass={pass_v}")
            if 'p0_count' in r:
                print(f"    P0={r['p0_count']}  P1={r['p1_count']}  issues={r.get('issues_summary', '')[:100]}")
        except Exception as e:
            print(f"  {AgentCls.__name__}: FAIL {type(e).__name__}: {str(e)[:200]}")
            review_results[part_num][agent.__class__.__name__] = {
                "error": str(e), "success": False,
            }

# 成本统计
summary = tracker.get_summary()
print(f"\n{'='*70}")
print("成本（Bug G 验证）")
print(f"{'='*70}")
print(f"total_calls: {summary.get('total_calls')}")
print(f"total_tokens: {summary.get('total_tokens')}")
print(f"estimated_cost: ¥{summary.get('estimated_cost', 0):.4f}")

# ====== 一致性衰减曲线 ======
print(f"\n{'='*70}")
print("一致性衰减曲线（Logic 评分 vs Part 序号）")
print(f"{'='*70}")
logic_scores = []
emotion_scores = []
consistency_scores = []
for p in range(1, 9):
    l = review_results.get(p, {}).get('LogicReviewAgent', {}).get('result', {}).get('score') or review_results.get(p, {}).get('LogicReviewAgent', {}).get('result', {}).get('overall_score') or 0
    e = review_results.get(p, {}).get('EmotionReviewAgent', {}).get('result', {}).get('emotion_score') or 0
    c = review_results.get(p, {}).get('ConsistencyReviewAgent', {}).get('result', {}).get('overall_score') or 0
    logic_scores.append(l)
    emotion_scores.append(e)
    consistency_scores.append(c)
    print(f"Part {p}: Logic={l}/10  Emotion={e}/10  Consistency={c}/10")

avg_logic = sum(logic_scores) / len(logic_scores) if logic_scores else 0
avg_emotion = sum(emotion_scores) / len(emotion_scores) if emotion_scores else 0
avg_consistency = sum(consistency_scores) / len(consistency_scores) if consistency_scores else 0
print(f"\n8 Part 平均: Logic={avg_logic:.1f}/10  Emotion={avg_emotion:.1f}/10  Consistency={avg_consistency:.1f}/10")

# 衰减率（Part 1 vs Part 8）
if logic_scores[0] > 0:
    decay_logic = (logic_scores[0] - logic_scores[-1]) / logic_scores[0] * 100
    print(f"\nLogic 评分衰减（Part 1 → Part 8）: {logic_scores[0]} → {logic_scores[-1]}（{decay_logic:+.1f}%）")

# 保存
output = {
    "parts": parts_text,
    "review_results": review_results,
    "cost_summary": summary,
    "scores": {
        "logic": logic_scores,
        "emotion": emotion_scores,
        "consistency": consistency_scores,
        "avg_logic": avg_logic,
        "avg_emotion": avg_emotion,
        "avg_consistency": avg_consistency,
    },
    "narrative_arc": {
        "开端_parts": [1, 2],
        "发展_parts": [3, 4, 5],
        "高潮_parts": [6, 7],
        "结局_parts": [8],
    },
}
with open('r10_longform_output.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\n完整产出已存: r10_longform_output.json")

# 总结
total_chars = sum(len(t) for t in parts_text.values())
print(f"\n{'='*70}")
print(f"8 Part 总字数: {total_chars:,}")
print(f"叙事弧完整度: 开端={phase_progress['开端']}/2 发展={phase_progress['发展']}/3 高潮={phase_progress['高潮']}/2 结局={phase_progress['结局']}/1")
print(f"{'='*70}")
