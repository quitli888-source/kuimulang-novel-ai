"""
R7-T2: 真实验证 Bug B 修复（review agents 的 str/int 比较）。
关键点：
- 不能 mock，必须真实调用 step-3.7-flash
- 构造 state.part_summaries 为 str keys（'1', '2'），Part 编号传 int
- 验证 Part 2/3 review 不再抛 TypeError
- 验证返回 score / pass 字段是合理值
"""
import os
import sys
import json
import time
import types
import traceback

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
# P0-41: STEP_API_KEY 由 conftest.py fixture 提供；缺失则 pytest.skip
STEP_API_KEY = os.environ.get('STEP_API_KEY', '')
from core.agents.logic_review_agent import LogicReviewAgent
from core.agents.emotion_review_agent import EmotionReviewAgent
from core.agents.consistency_review_agent import ConsistencyReviewAgent
from core.logger import get_logger
logger = get_logger('test_r7_review_part2plus')

def make_state_with_str_keys(part_text_map: dict):
    """构造 state，part_summaries 用 str keys（'1'、'2'），part_outline 用 list，
    characters / world_setting / foreshadowing 齐备。
    """
    characters = [{'name': '林枫', 'role': '主角', 'identity': '前刑警', 'core_trait': '执拗、敏锐', 'motivation': '追查三年前的搭档失踪案', 'secret': '他收到过搭档的匿名警告'}, {'name': '沈渊', 'role': '搭档', 'identity': '原刑侦队长', 'core_trait': '冷静、缜密', 'motivation': '潜伏在犯罪组织内部', 'secret': '三年前的失踪是主动安排'}]
    world_setting = '江南雨城，警署与地下势力相互渗透。三年前的雨夜搭档失踪案悬而未决。近年城市边缘出现以旧书商为幌子的情报网，暗号用红墨水书写。'
    part_outline = [{'phase': '开端', 'title': '雨夜重逢', 'core_event': '林枫推开尘封的书房门', 'emotion_target': '悬疑与不安', 'key_dialogue': '你终于来了', 'end_hook': '墙上红字指向下一个地点', 'pacing': '慢起', 'causality': '承前：搭档失踪悬案'}, {'phase': '发展', 'title': '墨迹追凶', 'core_event': '林枫解读红字暗号', 'emotion_target': '紧张', 'key_dialogue': '这不是失踪，是潜伏', 'end_hook': '暗号指向钟楼', 'pacing': '中等', 'causality': '承 Part1 红字'}, {'phase': '高潮', 'title': '钟楼对峙', 'core_event': '林枫找到沈渊', 'emotion_target': '震撼', 'key_dialogue': '三年，你终于来了', 'end_hook': '组织已发现他们', 'pacing': '快', 'causality': '承 Part2 暗号'}]
    foreshadowing = [{'id': 'F1', 'content': '红字暗号', 'plant_part': 1, 'reveal_part': 2, 'hint': '墙上红字用墨水写成'}, {'id': 'F2', 'content': '钟楼线索引出沈渊', 'plant_part': 2, 'reveal_part': 3, 'hint': '旧书商在钟楼'}]
    part_summaries = {str(k): v[:200] for k, v in part_text_map.items()}
    parts = {str(k): v for k, v in part_text_map.items()}
    return types.SimpleNamespace(characters=characters, world_setting=world_setting, part_outline=part_outline, foreshadowing=foreshadowing, part_summaries=part_summaries, parts=parts, current_plot_state='林枫追查三年前搭档失踪案', character_state_track={'林枫': '警觉', '沈渊': '潜伏'}, final_draft={})

def run_review(agent_cls, state, part_num, part_text):
    """真实调一个 review agent，捕获异常并返回 (ok, payload_or_err)。"""
    try:
        agent = agent_cls()
        t0 = time.time()
        result = agent.execute(state, part_num, part_text)
        dt = time.time() - t0
        if not isinstance(result, dict):
            return (False, {'err': '返回非 dict', 'type': type(result).__name__, 'dt': dt, 'result': str(result)[:300]})
        return (True, {'result': result, 'dt': dt})
    except Exception as e:
        return (False, {'err': f'{type(e).__name__}: {e}', 'trace': traceback.format_exc()})

def is_sane_score(value, lo=1, hi=10):
    """score 字段是否在 [lo, hi] 区间内。"""
    try:
        v = float(value)
        return lo <= v <= hi
    except (TypeError, ValueError):
        return False

def main():
    logger.info('=' * 70)
    logger.info('【R7-T2】Bug B 真实修复验证（Review Agents Part 2+）')
    logger.info('=' * 70)
    part_texts = {1: '雨打在林枫的肩膀上，他推开尘封的书房门。\n屋里满是灰尘，墙上用红墨水写着："第三天，子时，钟楼见。"\n他指腹轻轻拂过字迹，心跳骤然加快。三年前搭档失踪的那个夜晚，也是这样的雨。"你终于来了。"身后的声音平静得像深渊里的水。', 2: '林枫把红字拍成照片发给线人，对方只回了两个字："已读。"\n凌晨两点，他踩着湿漉漉的青石板走到钟楼门口。钟楼三层有人在弹钢琴，曲目是三年前他们一起听过的《雨夜》。沈渊站在阴影里，声音沙哑："这不是失踪，是潜伏。"', 3: '钟楼的钟声在凌晨四点响起，林枫拔出枪冲上三楼。沈渊背对着窗户，肩膀微微颤抖："三年，你终于来了。"\n组织的人已经包围了钟楼，红字暗号只是诱饵。"走。"沈渊把一本旧账本塞进林枫怀里。两人翻窗，跳进凌晨四点的雨里。'}
    state = make_state_with_str_keys(part_texts)
    logger.info('\n--- Unit check: _sorted_part_nums ---')
    for cls in (LogicReviewAgent, EmotionReviewAgent, ConsistencyReviewAgent):
        nums = cls._sorted_part_nums(state)
        logger.info(f'  {cls.__name__}._sorted_part_nums(state) = {nums}')
        assert all((isinstance(n, int) for n in nums)), f'{cls.__name__} 应返回 int 列表'
        assert sorted(nums) == nums, f'{cls.__name__} 应排序'
    summary = {'total': 0, 'passed': 0, 'failed': 0, 'by_agent': {}, 'failures': []}
    for part_num in (2, 3):
        part_text = part_texts[part_num]
        logger.info(f'\n--- Reviewing Part {part_num} (length={len(part_text)}) ---')
        for cls in (LogicReviewAgent, EmotionReviewAgent, ConsistencyReviewAgent):
            summary['total'] += 1
            ok, payload = run_review(cls, state, part_num, part_text)
            name = cls.__name__
            summary['by_agent'].setdefault(name, {'total': 0, 'passed': 0, 'failed': 0})
            if not ok:
                summary['failed'] += 1
                summary['by_agent'][name]['failed'] += 1
                summary['failures'].append({'agent': name, 'part': part_num, **payload})
                logger.info(f"  [FAIL] Part {part_num} {name}: {payload.get('err', 'unknown')[:140]}")
                continue
            r = payload['result']
            dt = payload['dt']
            if cls is LogicReviewAgent:
                score_key = 'overall_score'
                pass_key = 'pass'
            elif cls is EmotionReviewAgent:
                score_key = 'emotion_score'
                pass_key = 'emotion_target_met'
            else:
                score_key = 'overall_score'
                pass_key = 'pass'
            score = r.get(score_key)
            pass_v = r.get(pass_key)
            issues = r.get('issues', []) if isinstance(r.get('issues'), list) else []
            verdict = r.get('verdict', '')
            sane = is_sane_score(score)
            logger.info(f'  [OK] Part {part_num} {name} ({dt:.1f}s) | {score_key}={score} sane={sane} | {pass_key}={pass_v} | issues={len(issues)} | verdict={verdict[:60]}')
            if not sane:
                summary['failed'] += 1
                summary['by_agent'][name]['failed'] += 1
                summary['failures'].append({'agent': name, 'part': part_num, 'err': f'score {score_key}={score} 不在 [1,10]', 'result_keys': list(r.keys())})
                continue
            summary['passed'] += 1
            summary['by_agent'][name]['passed'] += 1
    logger.info('\n' + '=' * 70)
    logger.info('【R7-T2 结果汇总】')
    logger.info('=' * 70)
    logger.info(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    if summary['failed'] == 0:
        logger.error('\nR7-T2 PASS: 所有 6 次（2 Parts × 3 Agents）真实调用均返回合理 score，无 TypeError。')
        return 0
    else:
        logger.info(f"\nR7-T2 FAIL: {summary['failed']} 次失败。")
        return 1
@pytest.mark.live
def test_r7_review_part2plus_live(live_llm_key):
    """R7-T2 pytest 化（R3-S2）: Review Agents Part 2+ 真实验证（不能 mock）。

    本文件无模块级副作用（不做 call_llm 替换），只加薄包装。需真实 API key
    （live_llm_key 门禁）；未传 --live 时默认 skip。main() 逻辑一字未动，
    退出码 0 转为 pytest 断言。
    """
    assert main() == 0


if __name__ == '__main__':
    sys.exit(main())