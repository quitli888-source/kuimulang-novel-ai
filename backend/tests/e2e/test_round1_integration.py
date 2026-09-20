"""
Round 1 集成回归（R1-D/E/G/I/J）—— mock 全部 LLM 调用，跑 Phase3Runner + Phase4Runner 真实代码路径。

覆盖（离线，不调 LLM）：
  - Phase3: established_facts 每 Part 落盘；退场账本写入 character_state_track；
    已退场角色再现 → warning + consistency_flags；story_deltas 每 3 Part 一条
  - Phase4: 三评审并行路径；P0 触发修复回路 → 重写一次 → 重审通过 → 修订稿落盘 +
    review_report 标注 + revision_log；仍不过 → 保留原文；无 P0 的 Part 无标注字段

既支持 pytest 也支持 `python backend/tests/e2e/test_round1_integration.py` 直接跑。
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from core.logger import get_logger

logger = get_logger('test_round1_integration')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _make_work(tmp_path: Path, work_id: str, parts: int = 6):
    work = {
        'id': work_id, 'title': 'smoke', 'inspiration': '少年林尘被预言为天煞孤星。',
        'phase': 'phase2',
        'core_elements': {'protagonist': {'identity': '少年'}},
        'market_positioning': {},
        'world_setting': '玄幻世界',
        'characters': [
            {'name': '林尘', 'role': '主角', 'identity': '少年', 'core_trait': '坚忍',
             'motivation': '寻道', 'secret': '血脉', 'arc': '成长'},
            {'name': '苏婉', 'role': '配角', 'identity': '师姐', 'core_trait': '冷傲',
             'motivation': '宗门', 'secret': '无', 'arc': '无'}],
        'part_outline': [{'title': f'Part {i}', 'phase': 'p', 'word_count': 5000,
                          'core_event': '事件', 'emotion_target': '紧张', 'key_dialogue': '对话',
                          'end_hook': '钩子', 'causality': '因果', 'pacing': '快'}
                         for i in range(1, parts + 3)],
        'foreshadowing': [], 'parts': {}, 'part_summaries': {},
    }
    (tmp_path / f'{work_id}.json').write_text(json.dumps(work, ensure_ascii=False), encoding='utf-8')


def _patch_dirs(tmp_path: Path):
    import core.config as config_mod
    import api.works as works_api
    return [patch.object(config_mod, 'WORKS_DIR', tmp_path),
            patch.object(works_api, 'WORKS_DIR', tmp_path)]


def _apply_custom_template(svc, parts: int):
    """R1-A 同构：显式 apply_template（os.environ 的 CUSTOM_* 对 read_env 无效）。"""
    import core.config as config_mod
    tmpl = next((t for t in config_mod.DEFAULT_TEMPLATES if t.name == '自定义'),
                config_mod.WritingTemplate('自定义', 0, 0, 0, 0))
    svc.cfg.apply_template(tmpl, custom_target_words=parts * 5000, custom_part_count=parts)


def _fake_execute(self, state, part_num, **kwargs):
    """mock PartWriterAgent.execute：Part 3 让已退场角色苏婉"复活出场"，Part 2 抽取出死亡 fact。"""
    from core.established_facts import Fact
    mention = '苏婉忽然出现。' if part_num == 3 else ''
    text = f'Part {part_num} 正文。林尘踏入禁地，发现古井。{mention}' * 60
    if part_num == 2:
        state.established_facts.add(Fact(
            id='F2_1', part_num=2, category='character', subject='苏婉',
            predicate='死亡', text='苏婉为救林尘战死'))
    return {'success': True, 'content': text, 'word_count': len(text), 'part_num': part_num}


def test_round1_phase3_facts_departure_deltas(tmp_path):
    """R1-D/E/G: Phase3 全链路（facts 落盘 / 退场账本 / 预检告警 / story_deltas）。"""
    work_id = 'smoke_r1_phase3'
    _make_work(tmp_path, work_id)
    from services.writing_service import WritingService
    from services.writing_phase_runners import Phase3Runner
    from api.sse import SSEEmitter
    p0, p1 = _patch_dirs(tmp_path)
    with p0, p1:
        svc = WritingService(work_id, SSEEmitter(), resume=False)
        svc.cfg.confirm_mode = False
        svc._request_confirm = lambda *a, **k: asyncio.sleep(0)
        _apply_custom_template(svc, 6)
        with patch('core.agents.part_writer_agent.PartWriterAgent.execute', _fake_execute), \
             patch('core.sliding_window.SlidingWindow.maybe_generate_rolling_summary',
                   lambda self, pn, **kw: {'generated': True, 'text': 'rolling', 'char_count': 7}), \
             patch('core.sliding_window.SlidingWindow.maybe_generate_milestone',
                   lambda self, pn, **kw: {'generated': False}), \
             patch('core.llm_client.call_llm_json',
                   lambda **kw: {'departed_characters': ['苏婉'], 'new_objects': ['古井'],
                                 'foreshadow_planted': ['古井秘密'], 'foreshadow_revealed': [],
                                 'outline_adjustments': ''}):
            asyncio.run(Phase3Runner(svc).run(start_from=1))
    data = json.loads((tmp_path / f'{work_id}.json').read_text(encoding='utf-8'))
    assert len(data['parts']) == 6, list(data['parts'].keys())
    # R1-D: facts 每 Part 落盘（resume 不丢）
    assert data.get('established_facts', {}).get('facts'), 'established_facts 未落盘'
    # R1-E: 退场账本
    assert data.get('character_state_track', {}).get('苏婉', '').startswith('Part2 死亡'), \
        f"退场账本未写入: {data.get('character_state_track')}"
    # R1-E: 确定性预检只告警不阻断 + consistency_flags 留痕
    flags = data.get('consistency_flags') or []
    assert any(f['character'] == '苏婉' and f['part'] == 3 for f in flags), \
        f'Part 3 再现已退场角色应有 consistency_flags: {flags}'
    # R1-G: story_deltas 每 3 Part 一条（末 Part 无下一 Part 大纲故不生成）
    deltas = data.get('story_deltas') or {}
    assert '3' in deltas and '6' in deltas, f'story_deltas 应每 3 Part 一条: {list(deltas)}'
    logger.info('[test_round1_phase3] PASS: facts=%d, track=%s, flags=%s, deltas=%s' % (
        len(data['established_facts']['facts']), data['character_state_track'], flags, list(deltas)))


def test_round1_phase4_repair_pass_and_fail(tmp_path):
    """R1-I/J: Phase 4 并行 + P0 修复回路（重审通过落盘修订稿 / 仍不过保留原文）。"""
    from services.writing_service import WritingService
    from services.writing_phase_runners import Phase3Runner, Phase4Runner
    from api.sse import SSEEmitter

    p0_logic = {'score': 3, 'overall_score': 3, 'pass': False, 'p0_count': 1,
                'issues': [{'level': 'P0', 'location': 'Part 4', 'description': '苏婉已死却出场'}],
                'verdict': 'P0'}
    ok_logic = {'score': 8, 'overall_score': 8, 'pass': True, 'p0_count': 0, 'issues': [], 'verdict': 'ok'}
    ok_cons = {'pass': True, 'overall_score': 8, 'issues': [], 'character_states': {}, 'verdict': 'ok'}
    p0_cons = {'pass': False, 'overall_score': 3,
               'issues': [{'level': 'P0', 'character': '苏婉', 'location': 'Part 4',
                           'description': '已死角色出场', 'suggestion': '改为回忆'}],
               'character_states': {}, 'verdict': 'P0'}

    def fake_logic(self, state, part_num, part_text):
        if part_num != 4:
            return ok_logic
        return p0_logic if '修订后' not in part_text else ok_logic

    def fake_cons(self, state, part_num, part_text):
        if part_num != 4:
            return ok_cons
        return p0_cons if '修订后' not in part_text else ok_cons

    def fake_emotion(self, state, part_num, part_text):
        return {'pass': True, 'emotion_score': 8, 'resonance_score': 8, 'immersion_score': 8}

    def fake_rewrite(self, state, part_num, **kwargs):
        brief = state.get_part_context(part_num)
        assert '修订指令' in brief and '苏婉已死却出场' in brief, 'revision brief 未注入 writer 上下文'
        return {'success': True, 'content': f'Part {part_num} 修订后正文。林尘独自继续。' * 60,
                'word_count': 5000, 'part_num': part_num}

    # --- 场景 1：重写后重审通过 ---
    work_id = 'smoke_r1_p4_pass'
    _make_work(tmp_path, work_id)
    p0, p1 = _patch_dirs(tmp_path)
    with p0, p1:
        svc = WritingService(work_id, SSEEmitter(), resume=False)
        svc.cfg.confirm_mode = False
        _apply_custom_template(svc, 6)
        with patch('core.agents.part_writer_agent.PartWriterAgent.execute', _fake_execute), \
             patch('core.sliding_window.SlidingWindow.maybe_generate_rolling_summary',
                   lambda self, pn, **kw: {'generated': False}), \
             patch('core.sliding_window.SlidingWindow.maybe_generate_milestone',
                   lambda self, pn, **kw: {'generated': False}):
            asyncio.run(Phase3Runner(svc).run(start_from=1))
        with patch('core.agents.logic_review_agent.LogicReviewAgent.execute', fake_logic), \
             patch('core.agents.consistency_review_agent.ConsistencyReviewAgent.execute', fake_cons), \
             patch('core.agents.emotion_review_agent.EmotionReviewAgent.execute', fake_emotion), \
             patch('core.agents.style_optimizer_agent.StyleOptimizerAgent.execute',
                   lambda self, state, pn, text, **kw: text), \
             patch('core.agents.part_writer_agent.PartWriterAgent.execute', fake_rewrite):
            asyncio.run(Phase4Runner(svc).run())
    data = json.loads((tmp_path / f'{work_id}.json').read_text(encoding='utf-8'))
    report = data.get('review_report') or {}
    p4 = [p for p in report.get('parts', []) if p.get('part') == 4][0]
    assert p4.get('revision_attempted') is True and p4.get('revision_passed') is True, f'Part 4 修复标注异常: {p4}'
    assert '修订后' in data['parts']['4'], '修订稿未落盘'
    rev_log = data.get('revision_log') or []
    assert rev_log and rev_log[-1]['part'] == 4 and rev_log[-1]['revision_passed'] is True, rev_log
    assert report['logic']['p0_count'] == 0 and report['consistency']['p0_count'] == 0, '修复后 P0 应归零'
    # 无 P0 的 Part 不带标注字段（与改前逐字节一致）
    p5 = [p for p in report.get('parts', []) if p.get('part') == 5][0]
    assert 'revision_attempted' not in p5, f'无修复 Part 不应有标注: {p5}'
    logger.info('[test_round1_p4_pass] PASS: %s' % p4)

    # --- 场景 2：重写后仍 P0 → 保留原文 ---
    work_id2 = 'smoke_r1_p4_fail'
    _make_work(tmp_path, work_id2)
    with p0, p1:
        svc2 = WritingService(work_id2, SSEEmitter(), resume=False)
        svc2.cfg.confirm_mode = False
        _apply_custom_template(svc2, 6)
        with patch('core.agents.part_writer_agent.PartWriterAgent.execute', _fake_execute), \
             patch('core.sliding_window.SlidingWindow.maybe_generate_rolling_summary',
                   lambda self, pn, **kw: {'generated': False}), \
             patch('core.sliding_window.SlidingWindow.maybe_generate_milestone',
                   lambda self, pn, **kw: {'generated': False}):
            asyncio.run(Phase3Runner(svc2).run(start_from=1))
        original_p4 = svc2.data['parts']['4']

        def always_p0_cons(self, state, part_num, part_text):
            return p0_cons if part_num == 4 else ok_cons

        def bad_rewrite(self, state, part_num, **kwargs):
            return {'success': True, 'content': '修订后仍然有矛盾的内容。' * 100,
                    'word_count': 1000, 'part_num': part_num}

        with patch('core.agents.logic_review_agent.LogicReviewAgent.execute', fake_logic), \
             patch('core.agents.consistency_review_agent.ConsistencyReviewAgent.execute', always_p0_cons), \
             patch('core.agents.emotion_review_agent.EmotionReviewAgent.execute', fake_emotion), \
             patch('core.agents.style_optimizer_agent.StyleOptimizerAgent.execute',
                   lambda self, state, pn, text, **kw: text), \
             patch('core.agents.part_writer_agent.PartWriterAgent.execute', bad_rewrite):
            asyncio.run(Phase4Runner(svc2).run())
    data2 = json.loads((tmp_path / f'{work_id2}.json').read_text(encoding='utf-8'))
    assert data2['parts']['4'] == original_p4, '修复失败必须保留原文'
    rl = (data2.get('revision_log') or [])[-1]
    assert rl['revision_passed'] is False and (rl.get('residual_p0') or 0) > 0, rl
    logger.info('[test_round1_p4_fail] PASS: 原文保留, revision_log=%s' % rl)


if __name__ == '__main__':
    logger.info('=' * 60)
    logger.info('test_round1_integration.py —— Round 1 集成回归（mock LLM）')
    logger.info('=' * 60)
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        test_round1_phase3_facts_departure_deltas(Path(td) / 'a')
        (Path(td) / 'a').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        test_round1_phase4_repair_pass_and_fail(Path(td))
    logger.info('\nALL PASS')
