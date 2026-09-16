"""R13: 用 R12 升级后的机制（window_size=6+RAG+三层 facts+强承接）
重新生成全新 30 Parts 长篇，目标 Logic ≥ 7

R11/R12 是修旧 Parts，所以 Logic 没改善。
R13 是用升级机制**写新 Parts**，验证修复对新生成是否有效。
"""
import os
import sys
import json
import time

# R12 升级：默认开 RAG
os.environ['ENABLE_VECTOR_RAG'] = '1'

sys.path.insert(0, 'backend')

from core.sliding_window import SlidingWindow
from core.vector_store import VectorStore
from core.agents.part_writer_agent import PartWriterAgent
from core.cost_tracker import get_tracker

# ====== 复用 R11 大纲（4 阶段叙事弧 + 6 角色 + 6 伏笔）======
with open('r11_100k_novel.json', 'r', encoding='utf-8') as f:
    d11 = json.load(f)

OUTLINE = d11['outline']
CHARACTERS = d11['characters']
WORLD = d11['world_setting']
FORESHADOWING = d11['foreshadowing']

print("=" * 70)
print("R13: R12 升级机制下重新生成 30 Parts 长篇")
print("=" * 70)
print(f"机制: window_size=6 + RAG=开 + 三层 facts + PartWriter 强承接")
print(f"目标字数: 105,000 (30 Parts × 3,500)")
print(f"大纲: 复用 R11（已验证 4 阶段叙事弧完整）")

# 加载 STEP_API_KEY
NEW_KEY = 'dfJjLgI755TGpRvd4YKCg1C2BYcubVn9MRfYZgvAS7BfWXYcCszGUCbecFwsfKuv'
STEP_PLAN_BASE_URL = 'https://api.stepfun.com/step_plan/v1'
os.environ['STEP_API_KEY'] = NEW_KEY
os.environ['STEP_BASE_URL'] = STEP_PLAN_BASE_URL
with open('.env', 'w', encoding='utf-8') as f:
    f.write(f"STEP_API_KEY={NEW_KEY}\nSTEP_BASE_URL={STEP_PLAN_BASE_URL}\nSTEP_MODEL=step-3.7-flash\nSTEP_JSON_MODEL=step-3.7-flash\n")
print(f"✓ .env 已更新为新 API key + step_plan endpoint")


class State:
    def __init__(self, characters, world, outline, foreshadowing, window, vector_store=None):
        self.characters = characters
        self.world_setting = world
        self.part_outline = outline
        self.foreshadowing = foreshadowing
        self.window = window
        self.vector_store = vector_store  # R12: 接入 RAG
        self.parts = {}
        self.part_summaries = {}
        self.current_plot_state = ""
        self.character_state_track = {}
        self.established_facts = None

    def get_part_context(self, part_num):
        # R12: 调用 window.build，注入 vector_store（如果 build 支持）
        try:
            kwargs = dict(
                characters=self.characters,
                world_setting=self.world_setting,
                outline=self.part_outline[part_num - 1] if part_num <= len(self.part_outline) else None,
            )
            # 如果 build 接受 vector_store / vector_query，传入
            import inspect
            sig = inspect.signature(self.window.build)
            if 'vector_store' in sig.parameters:
                kwargs['vector_store'] = self.vector_store
                kwargs['vector_query'] = self.part_outline[part_num - 1].get('core_event', '') if part_num <= len(self.part_outline) else ''
                kwargs['vector_top_k'] = 3
            return self.window.build(part_num, **kwargs)
        except Exception as e:
            print(f"  ⚠ get_part_context 异常: {e}")
            return ""


# ====== 初始化 ======
OUTPUT_FILE = 'r13_r12_mechanism_novel.json'

# 实时落盘文件
window = SlidingWindow(window_size=6)  # R12: 6
vector_store = VectorStore() if os.environ.get('ENABLE_VECTOR_RAG') == '1' else None
state = State(CHARACTERS, WORLD, OUTLINE, FORESHADOWING, window, vector_store)
writer = PartWriterAgent()
tracker = get_tracker()

# 断点续跑
import os.path
existing = {}
if os.path.exists(OUTPUT_FILE):
    try:
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            existing = json.load(f)
        if existing.get('parts'):
            print(f"✓ 检测到旧进度：{len(existing['parts'])} Part 已完成，从 Part {len(existing['parts'])+1} 继续")
            for pn_str, pt in existing['parts'].items():
                pn = int(pn_str)
                summary = existing.get('part_summaries', {}).get(pn_str, pt[:200])
                state.parts[pn_str] = pt
                state.part_summaries[pn_str] = summary
                window.add_part(pn, pt, summary)
                if vector_store:
                    vector_store.add(pn, pt)
    except Exception as e:
        print(f"⚠ 读取旧进度失败: {e}")

start_idx = len(state.parts) + 1
total_chars_so_far = sum(len(t) for t in state.parts.values())
print(f"\n从 Part {start_idx} 开始，目标 Part 30")
print(f"已累计字数: {total_chars_so_far:,}")
print(f"SlidingWindow window_size: {window.window_size}")
print(f"VectorStore enabled: {vector_store.enabled if vector_store else False}")

# 阶段计数
phase_count = {"开端": 0, "发展": 0, "高潮": 0, "结局": 0}

PHASE_TARGETS = {'开端': 3, '发展': 15, '高潮': 8, '结局': 4}

for part_num in range(start_idx, 31):
    phase = OUTLINE[part_num - 1]['phase']
    phase_count[phase] += 1
    phase_target = PHASE_TARGETS[phase]
    print(f"\n{'='*70}")
    print(f"[{time.strftime('%H:%M:%S')}] Part {part_num}/30 [{phase}{phase_count[phase]}/{phase_target}] {OUTLINE[part_num-1]['title']}")
    print(f"{'='*70}")

    t0 = time.time()
    try:
        result = writer.execute(state, part_num)
        dt = time.time() - t0
        text = result.get('content', '') if isinstance(result, dict) else result
        if not text or len(text) < 100:
            print(f"  ✗ Part {part_num} 内容过短（{len(text) if text else 0} 字），跳过")
            continue
        word_count = len(text)
        total_chars_so_far += word_count
        print(f"  ✓ 完成: {dt:.1f}s, 字数={word_count}, 累计={total_chars_so_far:,}")

        state.parts[str(part_num)] = text
        state.part_summaries[str(part_num)] = text[:200]
        window.add_part(part_num, text, text[:200])
        if vector_store:
            vector_store.add(part_num, text)  # R12: RAG 索引

        # 实时落盘
        save_data = {
            "outline": OUTLINE,
            "characters": CHARACTERS,
            "world_setting": WORLD,
            "foreshadowing": FORESHADOWING,
            "parts": state.parts,
            "part_summaries": state.part_summaries,
            "total_chars": total_chars_so_far,
            "phase_count": phase_count,
            "last_completed": part_num,
            "mechanism": "R12: window_size=6 + RAG + 三层 facts + 强承接",
        }
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"  ✗ Part {part_num} 异常: {type(e).__name__}: {str(e)[:200]}")
        with open(OUTPUT_FILE + '.err.log', 'a', encoding='utf-8') as f:
            f.write(f"Part {part_num} FAIL: {e}\n")
        continue

# 总结
total = sum(len(t) for t in state.parts.values())
summary = tracker.get_summary()
print(f"\n{'='*70}")
print(f"R13 完成统计")
print(f"{'='*70}")
print(f"已完成 Part: {len(state.parts)}/30")
print(f"总字数: {total:,} / 105,000 目标")
print(f"总 LLM 调用: {summary.get('total_calls')}")
print(f"总 token: {summary.get('total_tokens'):,}")
print(f"预估成本: ¥{summary.get('estimated_cost', 0):.4f}")
print(f"叙事弧: 开端={phase_count['开端']}/3 发展={phase_count['发展']}/15 高潮={phase_count['高潮']}/8 结局={phase_count['结局']}/4")
