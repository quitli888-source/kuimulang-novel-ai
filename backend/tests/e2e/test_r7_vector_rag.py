"""
R7-T4: 真实启用向量检索 RAG 双轨。
- 设置 ENABLE_VECTOR_RAG=1
- 创建 VectorStore 实例
- 真实调 LLM 生成 Part 1 文本（≥ 300 字）
- 调 store.add() 索引
- 调 store.query("林枫") 看是否能检索回 Part 1
- 用真实 query 调用 SlidingWindow.build() 看是否注入"向量检索 Top-K"段
"""
import os
import sys
import json
import time
import types

# 必须先设环境变量再 import VectorStore（__init__ 会读环境变量）
os.environ["ENABLE_VECTOR_RAG"] = "1"
os.environ.setdefault(
    "STEP_API_KEY",
    "2AUHLIl7GnTbiSC0G9EwAX5OJQuKcA2XDk8vbvArNISugDJUnXw0fyDnJACyFR6e7",
)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from core.vector_store import VectorStore
from core.sliding_window import SlidingWindow
from core.llm_client import call_llm


def main():
    print("=" * 70)
    print("【R7-T4】向量检索 RAG 双轨 真实验证")
    print("=" * 70)

    # 1) 准备 VectorStore
    vs = VectorStore()
    print(f"\n--- VectorStore init ---")
    print(f"  enabled = {vs.enabled}")
    print(f"  embedding_provider = {vs.embedding_provider}")
    print(f"  embedding_dim = {vs.embedding_dim}")
    print(f"  len(store) = {len(vs)}")
    assert vs.enabled is True, "ENABLE_VECTOR_RAG=1 时 enabled 必须 True"

    # 2) 真实调 LLM 生成 Part 1（≥ 300 字）
    print("\n--- 真实生成 Part 1（≥ 300 字）---")
    sys_prompt = (
        "你是番茄小说平台顶级短篇作家。"
        "请用中文直接输出 ≥ 300 字的悬疑短篇片段，主角林枫是前刑警。"
        "**只输出正文，不要任何解释或标注。**"
    )
    user_prompt = (
        "请写一段悬疑短篇片段，主角林枫是前刑警，"
        "三年前搭档沈渊失踪，他收到一封匿名信来到钟楼下的旧书店，"
        "墙上用红墨水写着暗号。请用 300+ 字写出这一场景。"
    )

    t0 = time.time()
    part1 = call_llm(
        system_prompt=sys_prompt,
        user_prompt=user_prompt,
        temperature=0.7,
        max_tokens=1500,
        agent="test_r7_vector_rag",
    )
    elapsed = time.time() - t0
    print(f"  耗时 {elapsed:.1f}s, 生成 {len(part1)} 字")
    print(f"  预览: {part1[:120]}...")
    assert len(part1) >= 300, f"Part 1 应 ≥ 300 字，实际 {len(part1)}"

    # 3) add() 索引
    print("\n--- store.add(1, part1) ---")
    ok = vs.add(1, part1)
    print(f"  add() returned = {ok}")
    print(f"  len(store) = {len(vs)}")
    assert ok is True, "add() 在 enabled=True 时必须 True"
    assert len(vs) == 1

    # 4) query("林枫") 应能检索回 Part 1
    print("\n--- store.query('林枫') ---")
    hits = vs.query("林枫", top_k=3, exclude_part_num=None)
    print(f"  hits = {hits}")
    assert len(hits) >= 1, "query 必须返回至少 1 个 hit"
    assert hits[0][0] == 1, "Top-1 必须是 Part 1（hash embedding 至少 1 个 hit）"

    # 5) 排除自身 query
    print("\n--- store.query('林枫', exclude_part_num=1) ---")
    hits2 = vs.query("林枫", top_k=3, exclude_part_num=1)
    print(f"  hits (excluding Part 1) = {hits2}")
    assert len(hits2) == 0, "唯一 Part 时排除后必须为 []"

    # 6) 再 add Part 2 测试 Top-K 排序
    print("\n--- store.add(2, ...) 测试 Top-K 排序 ---")
    part2 = call_llm(
        system_prompt=sys_prompt,
        user_prompt=(
            "请写一段校园青春短篇片段，主角陈晓是高三学生，"
            "和好友在篮球场打完球后去食堂吃午饭。请用 200+ 字。"
        ),
        temperature=0.7,
        max_tokens=1000,
        agent="test_r7_vector_rag_part2",
    )
    print(f"  Part 2 长度 {len(part2)}")
    vs.add(2, part2)
    print(f"  len(store) = {len(vs)}")

    hits3 = vs.query("林枫 刑警", top_k=3, exclude_part_num=2)
    print(f"  query='林枫 刑警' exclude=2 -> hits = {hits3}")
    assert hits3 and hits3[0][0] == 1, "Top-1 应是 Part 1（与 query 更相关）"

    # 7) SlidingWindow.build() 注入向量检索段
    print("\n--- SlidingWindow.build(part_num=2, vector_store=vs) ---")
    sw = SlidingWindow(window_size=3)
    sw.add_part(1, part1, part1[:200] + "...")

    ctx = sw.build(
        part_num=2,
        characters=[
            {"name": "林枫", "role": "主角", "identity": "前刑警",
             "core_trait": "执拗、敏锐", "motivation": "查搭档失踪案",
             "secret": "收到匿名警告"},
        ],
        world_setting="江南雨城，警署与地下势力相互渗透。",
        outline={"core_event": "林枫解读红字暗号", "emotion_target": "紧张"},
        vector_store=vs,
        vector_query="林枫 刑警 查案",
        vector_top_k=2,
    )
    has_vector_section = "向量检索 Top-K" in ctx
    print(f"  ctx length = {len(ctx)}")
    print(f"  contains '向量检索 Top-K' ? = {has_vector_section}")
    if has_vector_section:
        # 打印包含向量段的几行
        idx = ctx.find("【相关前文片段（向量检索 Top-K）】")
        snippet = ctx[idx:idx + 400]
        print(f"  片段预览:\n{snippet}")
    assert has_vector_section, "SlidingWindow.build 必须注入'向量检索 Top-K'段"

    # 8) 最终统计
    print("\n--- VectorStore stats ---")
    print(json.dumps(vs.stats(), ensure_ascii=False, indent=2))

    print("\n" + "=" * 70)
    print("【R7-T4 PASS】向量检索 RAG 双轨真实验证全部通过")
    print(f"  - ENABLE_VECTOR_RAG=1 启用成功（vs.enabled=True）")
    print(f"  - 真实生成 Part 1（{len(part1)} 字）+ Part 2（{len(part2)} 字）并 add 索引")
    print(f"  - query('林枫') 命中 Part 1 (Top-1)")
    print(f"  - SlidingWindow.build() 注入'向量检索 Top-K'段（ctx={len(ctx)} 字）")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
