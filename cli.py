"""
番茄小说AI创作系统 V5 - CLI命令行入口
保留V4的CLI用法，支持 --resume / --rewrite-part 等参数
用法: python cli.py "你的灵感" [--auto] [--resume] [--rewrite-part N]
"""
import sys
import os
from pathlib import Path

# 添加backend到路径
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from core.config import init_app_config, get_app_config
from core.story_state import StoryState
from core.cost_tracker import reset_tracker, get_tracker
from core.logger import StoryLogger
from core.agents.inspiration_agent import InspirationAgent
from core.agents.genre_agent import GenreAgent
from core.agents.plot_planner_agent import PlotPlannerAgent
from core.agents.part_writer_agent import PartWriterAgent
from core.agents.logic_review_agent import LogicReviewAgent
from core.agents.emotion_review_agent import EmotionReviewAgent
from core.agents.consistency_review_agent import ConsistencyReviewAgent
from core.agents.style_optimizer_agent import StyleOptimizerAgent
from core.llm_client import call_llm_json


def count_chinese_chars(text: str) -> int:
    import re
    return len(re.sub(r'\s', '', text))


def main():
    if len(sys.argv) < 2:
        print("用法: python cli.py '你的灵感' [--auto] [--resume] [--rewrite-part N]")
        print('示例: python cli.py "一个能看见死亡倒计时的女人" --auto')
        sys.exit(1)

    inspiration = sys.argv[1]
    auto_mode = "--auto" in sys.argv
    resume = "--resume" in sys.argv
    rewrite_part = None

    for i, arg in enumerate(sys.argv):
        if arg == "--rewrite-part" and i + 1 < len(sys.argv):
            rewrite_part = int(sys.argv[i + 1])

    init_app_config()
    reset_tracker()

    from core.config import get_app_config, reload_config
    reload_config()

    print(f"🍅 番茄小说AI创作系统 V5")
    print(f"📝 灵感: {inspiration}")

    cfg = get_app_config()
    print(f"📋 模板: {cfg.template.name} | 目标: {cfg._target_words}字 | {cfg._part_count}Part")

    # 创建StoryState
    state = StoryState(inspiration)

    print("\n" + "="*50)
    print("Phase 1: 灵感解析")
    print("="*50)

    # 1.1 InspirationAgent
    print("\n[1/2] 调用灵感解析Agent...")
    inspiration_agent = InspirationAgent()
    core_elements = inspiration_agent.execute(state)
    state.core_elements = core_elements
    print(f"  主角: {core_elements.get('protagonist', {}).get('identity', '未知')}")
    print(f"  核心冲突: {core_elements.get('conflict', {}).get('core_conflict', '未知')}")
    print(f"  主题: {core_elements.get('theme', '未知')}")

    # 1.2 GenreAgent
    print("\n[2/2] 调用题材判断Agent...")
    genre_agent = GenreAgent()
    genre_result = genre_agent.execute(state)
    state.genre = genre_result
    state.market_positioning = genre_result
    print(f"  题材: {genre_result.get('genre_primary', '')} > {genre_result.get('genre_secondary', '')}")
    print(f"  目标受众: {'男性' if genre_result.get('target_gender') == 'male' else '女性' if genre_result.get('target_gender') == 'female' else '通用'}")

    print("\n" + "="*50)
    print("Phase 2: 情节规划")
    print("="*50)

    print("\n调用情节规划Agent...")
    plot_agent = PlotPlannerAgent()
    plot_result = plot_agent.execute(state)
    state.world_setting = plot_result.get("world_setting", "")
    state.characters = plot_result.get("characters", [])
    state.part_outline = plot_result.get("part_outline", [])
    state.foreshadowing = plot_result.get("foreshadowing", [])
    print(f"  世界观: {state.world_setting[:50]}...")
    print(f"  角色: {len(state.characters)}个")
    print(f"  伏笔: {len(state.foreshadowing)}个")
    print(f"  Part规划: {len(state.part_outline)}个")

    print("\n" + "="*50)
    print("Phase 3: 逐Part创作")
    print("="*50)

    writer_agent = PartWriterAgent()
    for i in range(1, cfg._part_count + 1):
        print(f"\n[Part {i}/{cfg._part_count}] 开始创作...")
        part_text = writer_agent.execute(state, i)
        state.parts[str(i)] = part_text
        summary = part_text[:200] + "..." if len(part_text) > 200 else part_text
        state.part_summaries[str(i)] = summary
        print(f"  [Part {i}] 完成 ({len(part_text)}字)")

    print("\n" + "="*50)
    print("Phase 4: 风格优化")
    print("="*50)

    # 逻辑校验
    print("\n[1/4] 执行逻辑校验...")
    logic_agent = LogicReviewAgent()
    logic_result = logic_agent.execute(state)
    print(f"  逻辑校验完成")

    # 情感评估
    print("\n[2/4] 执行情感评估...")
    emotion_agent = EmotionReviewAgent()
    emotion_result = emotion_agent.execute(state)
    print(f"  情感评分: {emotion_result.get('emotion_score', 'N/A')}")

    # 一致性检查
    print("\n[3/4] 执行一致性检查...")
    consistency_agent = ConsistencyReviewAgent()
    consistency_result = consistency_agent.execute(state)
    print(f"  一致性检查完成")

    # 风格优化
    print("\n[4/4] 执行风格优化...")
    style_agent = StyleOptimizerAgent()
    style_result = style_agent.execute(state)
    print(f"  风格优化完成")

    # 保存终稿
    state.final_draft = state.parts
    state.review_report = {
        "logic": logic_result,
        "emotion": emotion_result,
        "consistency": consistency_result,
    }

    # 保存状态
    # R4-P2-x: phase 必须在 save() 之前赋值 —— save() 按调用时的 self.phase
    # 序列化，此前先 save 后置位，磁盘上的 phase 永远不是 complete，
    # --resume 续写链路实际失效。
    state.phase = "complete"
    state.save()

    # 输出成本统计
    tracker = get_tracker()
    print("\n" + "="*50)
    print("📊 Token消耗统计")
    print("="*50)
    print(tracker.summary())

    total_words = sum(len(t) for t in state.final_draft.values())
    print(f"\n🍅 创作完成！")
    print(f"   总字数: {total_words}字")
    print(f"   作品已保存到: data/memory/current_story_state.json")


if __name__ == "__main__":
    main()
