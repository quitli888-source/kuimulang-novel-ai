"""R13 评估：用 R12 升级机制 + step_plan endpoint 重生成的 Parts 做 Logic 评估
- 采样 7 个 Part（避开缺失的 Part 1）
- 跑 3 个 Review Agent
- 目标 Logic ≥ 7
"""
import os
import sys
import json
import time

# R12: 默认开 RAG
os.environ['ENABLE_VECTOR_RAG'] = '1'

sys.path.insert(0, 'backend')

from core.sliding_window import SlidingWindow
from core.vector_store import VectorStore
from core.agents.logic_review_agent import LogicReviewAgent
from core.agents.emotion_review_agent import EmotionReviewAgent
from core.agents.consistency_review_agent import ConsistencyReviewAgent
from core.cost_tracker import get_tracker


# 加载 R13 完整产出
with open('r13_r12_mechanism_novel.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

OUTLINE = d['outline']
CHARACTERS = d['characters']
WORLD = d['world_setting']
FORESHADOWING = d['foreshadowing']
parts_text = d['parts']
part_summaries = d.get('part_summaries', {})

print("=" * 70)
print("R13 EVAL: R12 机制 + step_plan endpoint 重生成 Parts 采样评估")
print("=" * 70)
print(f"Parts: {len(parts_text)}/30（Part 1 缺失）")
print(f"总字数: {sum(len(t) for t in parts_text.values()):,}")


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
        return ""


# R12: window_size=6 + RAG
window = SlidingWindow(window_size=6)
state = State(parts_text, part_summaries, CHARACTERS, WORLD, OUTLINE, FORESHADOWING)
for pn in sorted(int(k) for k in parts_text.keys()):
    ptext = parts_text[str(pn)]
    window.add_part(pn, ptext, ptext[:200])
    state.parts[str(pn)] = ptext
    state.part_summaries[str(pn)] = ptext[:200]

vector_store = VectorStore()
for pn in sorted(int(k) for k in parts_text.keys()):
    vector_store.add(pn, parts_text[str(pn)], embedding=None)

print(f"SlidingWindow window_size: {window.window_size}")
print(f"SlidingWindow parts: {len(window.parts)}")
print(f"VectorStore enabled: {vector_store.enabled}, items: {len(vector_store)}")

# R12 增强 get_part_context（注入 window + RAG）
original_get_part_context = state.get_part_context
def r12_get_part_context(part_num):
    try:
        return window.build(
            part_num,
            characters=state.characters,
            world_setting=state.world_setting,
            outline=state.part_outline[part_num - 1] if part_num <= len(state.part_outline) else None,
        )
    except Exception as e:
        return original_get_part_context(part_num)
state.get_part_context = r12_get_part_context

# 采样（避开缺失的 Part 1，从实际生成的 Parts 中取）
available_parts = sorted([int(k) for k in parts_text.keys()])
SAMPLE_PARTS = [5, 10, 15, 20, 25, 30]
SAMPLE_PARTS = [p for p in SAMPLE_PARTS if p in available_parts]
# 加 Part 1 后段 + 中段代表
if 1 not in available_parts:
    # 找最早的可用 Part
    SAMPLE_PARTS.insert(0, min(available_parts))
print(f"\n采样 Part: {SAMPLE_PARTS}")

review_results = {}
for part_num in SAMPLE_PARTS:
    text = parts_text[str(part_num)]
    print(f"\n--- Review Part {part_num} ({len(text)}字) ---")
    review_results[part_num] = {}
    for AgentCls in [LogicReviewAgent, EmotionReviewAgent, ConsistencyReviewAgent]:
        agent = AgentCls()
        t0 = time.time()
        try:
            r = agent.execute(state, part_num, text)
            dt = time.time() - t0
            review_results[part_num][agent.__class__.__name__] = {"result": r, "duration": dt, "success": True}
            score = r.get('score') or r.get('overall_score') or r.get('emotion_score') or 'N/A'
            print(f"  {agent.__class__.__name__}: {score}/10 in {dt:.1f}s  pass={r.get('pass')}")
            if 'p0_count' in r:
                print(f"    P0={r['p0_count']} P1={r['p1_count']}  issues={r.get('issues_summary', '')[:120]}")
        except Exception as e:
            print(f"  {AgentCls.__name__}: FAIL {type(e).__name__}: {str(e)[:200]}")
            review_results[part_num][agent.__class__.__name__] = {"error": str(e), "success": False}

# 汇总
print(f"\n{'='*70}")
print("R13 EVAL 结果汇总")
print(f"{'='*70}")
print(f"{'Part':>5} {'Logic':>7} {'Emotion':>8} {'Consist':>8}  Pass率")
for p in SAMPLE_PARTS:
    l = review_results.get(p, {}).get('LogicReviewAgent', {}).get('result', {}).get('score') or \
        review_results.get(p, {}).get('LogicReviewAgent', {}).get('result', {}).get('overall_score') or 0
    e = review_results.get(p, {}).get('EmotionReviewAgent', {}).get('result', {}).get('emotion_score') or 0
    c = review_results.get(p, {}).get('ConsistencyReviewAgent', {}).get('result', {}).get('overall_score') or 0
    passes = sum(1 for s in [l, e, c] if s and s >= 7)
    print(f"{p:>5} {l:>7} {e:>8} {c:>8}  {passes}/3")

# 平均
l_avg = sum((review_results.get(p, {}).get('LogicReviewAgent', {}).get('result', {}).get('score') or
             review_results.get(p, {}).get('LogicReviewAgent', {}).get('result', {}).get('overall_score') or 0)
            for p in SAMPLE_PARTS) / len(SAMPLE_PARTS)
e_avg = sum((review_results.get(p, {}).get('EmotionReviewAgent', {}).get('result', {}).get('emotion_score') or 0)
            for p in SAMPLE_PARTS) / len(SAMPLE_PARTS)
c_avg = sum((review_results.get(p, {}).get('ConsistencyReviewAgent', {}).get('result', {}).get('overall_score') or 0)
            for p in SAMPLE_PARTS) / len(SAMPLE_PARTS)
print(f"\nR13 采样平均: Logic={l_avg:.1f}  Emotion={e_avg:.1f}  Consistency={c_avg:.1f}")
print(f"Logic ≥ 7: {sum(1 for p in SAMPLE_PARTS if (review_results.get(p,{}).get('LogicReviewAgent',{}).get('result',{}).get('score') or review_results.get(p,{}).get('LogicReviewAgent',{}).get('result',{}).get('overall_score') or 0) >= 7)}/{len(SAMPLE_PARTS)}")
print(f"\n对比 R11 (Logic 4.0): {('+' if l_avg > 4.0 else '')}{l_avg-4.0:.1f}")
print(f"对比 R12 (Logic 3.9): {('+' if l_avg > 3.9 else '')}{l_avg-3.9:.1f}")

# 保存
with open('r13_evaluation.json', 'w', encoding='utf-8') as f:
    json.dump({
        "sample_parts": SAMPLE_PARTS,
        "review_results": review_results,
        "avg_logic": l_avg,
        "avg_emotion": e_avg,
        "avg_consistency": c_avg,
        "improvements_vs_r11": {"logic_delta": round(l_avg - 4.0, 2)},
        "improvements_vs_r12": {"logic_delta": round(l_avg - 3.9, 2)},
        "mechanism": "R12: window_size=6 + RAG + 三层 facts + 强承接",
        "endpoint": "https://api.stepfun.com/step_plan/v1",
    }, f, ensure_ascii=False, indent=2)
print(f"\n结果: r13_evaluation.json")
