"""R11 评估脚本：采样 7 个 Part × 3 Review = 21 次评分
目标：量化的长程一致性衰减曲线 + 跨 Part 一致性验证
"""
import os
import sys
import json
import time

sys.path.insert(0, 'backend')

from core.agents.logic_review_agent import LogicReviewAgent
from core.agents.emotion_review_agent import EmotionReviewAgent
from core.agents.consistency_review_agent import ConsistencyReviewAgent
from core.cost_tracker import get_tracker
from core.sliding_window import SlidingWindow


# 加载 R11 完整产出
with open('r11_100k_novel.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

OUTLINE = d['outline']
CHARACTERS = d['characters']
WORLD = d['world_setting']
FORESHADOWING = d['foreshadowing']
parts_text = d['parts']
part_summaries = d.get('part_summaries', {})

# 取所有 Part
all_part_nums = sorted(int(k) for k in parts_text.keys())
total_chars = sum(len(v) for v in parts_text.values())
print("=" * 70)
print("R11 10万字小说一致性采样评估")
print("=" * 70)
print(f"总 Part 数: {len(all_part_nums)}")
print(f"总字数: {total_chars:,}")
print(f"采样 Part: {all_part_nums[:1]} + [5, 10, 15, 20, 25, 30]（每阶段代表）")

# 构建 state（用真实 parts 重建 sliding window 上下文）
class State:
    def __init__(self, parts, summaries, characters, world, outline, foreshadowing):
        self.parts = {str(k): v for k, v in parts.items()}
        self.part_summaries = {str(k): v for k, v in summaries.items()}
        self.characters = characters
        self.world_setting = world
        self.part_outline = outline
        self.foreshadowing = foreshadowing
        self.current_plot_state = ""
        self.character_state_track = {}
        self.established_facts = None

    def get_part_context(self, part_num):
        # 简化版：直接返回 None 让 Agent 用 parts/summaries 自己读
        return ""


# 采样 Part（开端 1 + 发展 5/10/15 + 高潮 20/25 + 结局 30）
SAMPLE_PARTS = [1, 5, 10, 15, 20, 25, 30]
# 过滤实际存在的（parts_text keys 是字符串）
SAMPLE_PARTS = [p for p in SAMPLE_PARTS if str(p) in parts_text]
print(f"实际采样: {SAMPLE_PARTS}")

state = State(parts_text, part_summaries, CHARACTERS, WORLD, OUTLINE, FORESHADOWING)
review_results = {}

for part_num in SAMPLE_PARTS:
    text = parts_text[str(part_num)]
    print(f"\n--- Review Part {part_num}（{OUTLINE[part_num-1]['title']}, {len(text)}字）---")
    review_results[part_num] = {}
    for AgentCls in [LogicReviewAgent, EmotionReviewAgent, ConsistencyReviewAgent]:
        agent = AgentCls()
        t0 = time.time()
        try:
            r = agent.execute(state, part_num, text)
            dt = time.time() - t0
            review_results[part_num][agent.__class__.__name__] = {
                "result": r, "duration": dt, "success": True,
            }
            score = r.get('score') or r.get('overall_score') or r.get('emotion_score') or 'N/A'
            print(f"  {agent.__class__.__name__}: {score}/10 in {dt:.1f}s  pass={r.get('pass')}")
            if 'p0_count' in r:
                print(f"    P0={r['p0_count']} P1={r['p1_count']}  issues={r.get('issues_summary', '')[:100]}")
        except Exception as e:
            print(f"  {AgentCls.__name__}: FAIL {type(e).__name__}: {str(e)[:200]}")
            review_results[part_num][agent.__class__.__name__] = {"error": str(e), "success": False}

# 汇总
print(f"\n{'='*70}")
print("10万字采样评分汇总")
print(f"{'='*70}")
print(f"{'Part':>5} {'Logic':>7} {'Emotion':>8} {'Consist':>8}  Pass率")
for p in SAMPLE_PARTS:
    l = review_results.get(p, {}).get('LogicReviewAgent', {}).get('result', {}).get('score') or \
        review_results.get(p, {}).get('LogicReviewAgent', {}).get('result', {}).get('overall_score') or 0
    e = review_results.get(p, {}).get('EmotionReviewAgent', {}).get('result', {}).get('emotion_score') or 0
    c = review_results.get(p, {}).get('ConsistencyReviewAgent', {}).get('result', {}).get('overall_score') or 0
    passes = sum(1 for s in [l, e, c] if s and s >= 7)
    print(f"{p:>5} {l:>7} {e:>8} {c:>8}  {passes}/3")

# 衰减曲线（R10 + R11 合并）
print(f"\n{'='*70}")
print("一致性衰减曲线（R10 8 Parts + R11 7 采样 Parts）")
print(f"{'='*70}")

# R10 Logic scores（之前结果）
r10_logic = [10, 8, 3, 3, 3, 2, 3, 10]
# R11 sampling（待填）
r11_logic = []
for p in SAMPLE_PARTS:
    l = review_results.get(p, {}).get('LogicReviewAgent', {}).get('result', {}).get('score') or \
        review_results.get(p, {}).get('LogicReviewAgent', {}).get('result', {}).get('overall_score') or 0
    r11_logic.append(l)

combined_logic = r10_logic + r11_logic
print(f"Part 序号:  {list(range(1, 9))} + {SAMPLE_PARTS}")
print(f"Logic 评分: {r10_logic} + {r11_logic}")
avg_logic = sum(combined_logic) / len(combined_logic)
print(f"15 Part 平均 Logic: {avg_logic:.1f}/10")
print(f"Logic ≥ 7 Part 数: {sum(1 for s in combined_logic if s >= 7)}/{len(combined_logic)} = {sum(1 for s in combined_logic if s >= 7)/len(combined_logic)*100:.0f}%")

# 保存
with open('r11_evaluation.json', 'w', encoding='utf-8') as f:
    json.dump({
        "total_parts": len(all_part_nums),
        "total_chars": total_chars,
        "sample_parts": SAMPLE_PARTS,
        "review_results": review_results,
        "decay_curve": {
            "r10_logic": r10_logic,
            "r11_logic": r11_logic,
            "combined_logic": combined_logic,
            "avg_logic": avg_logic,
        },
    }, f, ensure_ascii=False, indent=2)
print(f"\n评估结果: r11_evaluation.json")
