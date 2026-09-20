"""
Round 5 看门狗回归测试（R5-S6: 伏笔误报止损 + 连续失败告警 + 四段式 logger 双写）
—— 全部离线断言，不调 LLM。

覆盖（02_review.md §2.6 验收标准）：
  P1-3  foreshadow_unrevealed 不再注入 consistency 评审 prompt 警告段
        （R4-6 实证 7/7 误报；work.json 留痕与 verify 汇总行不变 —— 由
        test_round4_foreshadow 既有两项锁定）；纯伏笔 flags 不生成警告段
  P1-1  Phase4Runner 跨 Part 连续修复失败计数：连续 3 个 revision_passed=False
        → logger.warning 建议人工介入且管线不抛异常；第 4 个 passed → 计数复位
  P1-2  ConsistencyRepairer._emit_log emitter + logger 双写（消息逐字保留）

既支持 pytest 也支持 `python backend/tests/e2e/test_round5_watchdogs.py` 直接跑。
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.logger import get_logger

logger = get_logger('test_round5_watchdogs')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import services.consistency_repair as cr_mod  # noqa: E402
import services.writing_phase_runners as wpr_mod  # noqa: E402
from services.consistency_repair import ConsistencyRepairer  # noqa: E402


class _FakeService:
    """ConsistencyRepairer 所需的最小 service 面（离线，无 work 文件）。"""

    def __init__(self, data=None):
        self.data = data if isinstance(data, dict) else {}
        self.work_id = 'r5_watchdog_test'
        self.vector_store = None
        self.progress_callback = lambda *a, **k: None
        self.saved_chunks = {}
        self.save_count = 0
        self.emitted = []
        emitted = self.emitted

        class _Emitter:
            async def emit(self, *a, **k):
                emitted.append(a)

        self.emitter = _Emitter()

    def _save_chunk_progress(self, part_num, text, summary=None):
        self.saved_chunks[part_num] = text

    def _save(self):
        self.save_count += 1

    @staticmethod
    def _review_failure(kind, part_num, err):
        return {'pass': False, 'overall_score': 3, 'issues': [], 'verdict': 'x'}


# ---------------- P1-3: 伏笔误报止损（不进评审 prompt） ----------------

def test_foreshadow_flags_not_injected_into_prompt():
    """R5-S6 验收: foreshadow_unrevealed 不再注入 consistency 评审 prompt。

    R4-6 实证 7/7 误报（Phase 2 content 是剧情描述句，前 10 字子串命中结构性
    不可能成功）；只保留 work.json 留痕 + verify 汇总行（观测通道不删）。
    纯伏笔 flags → 不生成警告段；混有退场 flag 时退场 flag 照常展示。
    """
    from core.agents.consistency_review_agent import ConsistencyReviewAgent
    chars = [{'name': '林尘', 'role': '主角', 'identity': '少年', 'core_trait': '坚忍',
              'motivation': '寻道', 'secret': '血脉'}]
    fs_flags = [{'part': 3, 'type': 'foreshadow_unrevealed', 'foreshadow_id': 'FS_X'},
                {'part': 4, 'type': 'foreshadow_unrevealed', 'foreshadow_id': 'FS_Y'}]
    state = SimpleNamespace(
        work_id='r5', characters=chars, part_summaries={'1': '摘要'},
        parts={'1': '林尘踏入禁地。' * 50}, final_draft={'1': 'x'},
        name_registry={}, character_state_track={}, consistency_flags=fs_flags)
    captured = {}

    def fake_json(**kwargs):
        captured['user_prompt'] = kwargs.get('user_prompt', '')
        return {'pass': True, 'overall_score': 8, 'issues': [],
                'character_states': {}, 'verdict': 'ok'}

    with patch('core.agents.consistency_review_agent.call_llm_json', side_effect=fake_json):
        ConsistencyReviewAgent().execute(state, 2, '林尘继续前行。' * 30)
    up = captured['user_prompt']
    assert '系统预检警告' not in up, '纯伏笔 flags 不得生成警告段'
    assert 'FS_X' not in up and 'FS_Y' not in up
    assert '未在正文中检出回收关键词' not in up
    # 混有退场 flag 时：退场 flag 展示、伏笔 id 仍不展示
    mixed = fs_flags + [{'part': 5, 'character': '林忠', 'count': 2,
                         'departed_record': 'Part2 死亡'}]
    state_mixed = SimpleNamespace(**{**vars(state), 'consistency_flags': mixed})
    with patch('core.agents.consistency_review_agent.call_llm_json', side_effect=fake_json):
        ConsistencyReviewAgent().execute(state_mixed, 2, '林尘继续前行。' * 30)
    up2 = captured['user_prompt']
    assert '⚠ 系统预检警告' in up2 and '已退场角色 林忠' in up2
    assert 'FS_X' not in up2 and 'FS_Y' not in up2
    logger.info('[test_foreshadow_stoploss] PASS: 伏笔误报止损，退场 flag 不受影响')


# ---------------- P1-2: emitter + logger 双写 ----------------

def test_emit_log_dual_write():
    """R5-S6 验收: _emit_log 同时写 emitter 与 logger（消息文本逐字保留）。

    verify 的 FakeEmitter.emit 是 pass —— 四段式回退/第二跳/完成消息此前只走
    emitter，全量跑日志不可见（P1-2 实证）。
    """
    service = _FakeService({'name_registry': {}})
    repairer = ConsistencyRepairer(service, None, None)
    logged = []
    orig_info = cr_mod.logger.info

    def spy(msg, *a, **k):
        logged.append(str(msg))

    with patch.object(cr_mod.logger, 'info', spy):
        asyncio.run(repairer._emit_log('✅ Part 2 姓名定点修复完成（测试）'))
    assert service.emitted, 'emitter 必须仍收到事件（前端行为不变）'
    assert '✅ Part 2 姓名定点修复完成（测试）' in str(service.emitted[0])
    assert any('[ConsistencyRepairer] ✅ Part 2 姓名定点修复完成（测试）' in m
               for m in logged), 'logger 必须收到同一消息（全量跑日志可见）'
    logger.info('[test_emit_log] PASS: emitter + logger 双写，消息逐字保留')


# ---------------- P1-1: 连续修复失败告警 ----------------

def _make_work(tmp_path: Path, work_id: str, part_count: int):
    part_text = '林尘跌入古井，井底青光萦绕不散，林啸天率族人封锁井口。' * 45
    work = {
        'id': work_id, 'title': 'smoke r5 watchdog', 'inspiration': '少年林尘被预言为天煞孤星。',
        'phase': 'phase3',
        'core_elements': {'protagonist': {'identity': '少年'}},
        'market_positioning': {},
        'world_setting': '玄幻世界',
        'characters': [{'name': '林尘', 'role': '主角', 'identity': '少年',
                        'core_trait': '坚忍', 'motivation': '寻道', 'secret': '血脉'}],
        'part_outline': [{'title': f'P{i}', 'phase': 'p', 'word_count': 5000,
                          'core_event': '事件', 'emotion_target': '紧张',
                          'key_dialogue': '对话', 'end_hook': '钩子',
                          'causality': '因果', 'pacing': '快'} for i in range(1, part_count + 1)],
        'foreshadowing': [],
        'parts': {str(i): part_text for i in range(1, part_count + 1)},
        'part_summaries': {str(i): '林尘坠入古井。' for i in range(1, part_count + 1)},
        'name_registry': {},
    }
    (tmp_path / f'{work_id}.json').write_text(
        __import__('json').dumps(work, ensure_ascii=False), encoding='utf-8')


def test_consecutive_repair_failure_alert(tmp_path):
    """R5-S6 验收: 连续 3 个 Part 修复未通过 → logger.warning 且管线不抛异常；
    第 4 个 passed → 计数复位（7 Part 脚本 F,F,F,P,F,F,F → 恰好 2 次告警）。"""
    import core.config as config_mod
    import api.works as works_api
    from services.writing_service import WritingService
    from services.writing_phase_runners import Phase4Runner
    from api.sse import SSEEmitter

    work_id = 'smoke_r5_watchdog'
    _make_work(tmp_path, work_id, 7)
    with patch.object(config_mod, 'WORKS_DIR', tmp_path), \
         patch.object(works_api, 'WORKS_DIR', tmp_path):
        svc = WritingService(work_id, SSEEmitter(), resume=False)
        svc.cfg.confirm_mode = False
        # 评审/风格优化全部 mock（零 LLM）；修复回路脚本化：F,F,F,P,F,F,F
        outcomes = [False, False, False, True, False, False, False]

        async def fake_repair(self, part_num, part_text, logic_result,
                              consistency_result, state_mock):
            passed = outcomes.pop(0) if outcomes else True
            return {'revision_attempted': True, 'revision_passed': passed,
                    'residual_p0': 0 if passed else 2}

        warnings_seen = []
        orig_warning = wpr_mod.logger.warning

        def spy_warning(msg, *a, **k):
            warnings_seen.append(str(msg))

        with patch('core.agents.logic_review_agent.LogicReviewAgent.execute',
                   lambda self, state, pn, text: {'overall_score': 8, 'pass': True,
                                                  'p0_count': 0, 'p1_count': 0,
                                                  'issues': [], 'verdict': 'ok'}), \
             patch('core.agents.emotion_review_agent.EmotionReviewAgent.execute',
                   lambda self, state, pn, text: {'emotion_score': 8, 'pass': True,
                                                  'resonance_score': 8, 'immersion_score': 8,
                                                  'emotion_target_met': True,
                                                  'highlights': [], 'weaknesses': [],
                                                  'enhancement_suggestions': [],
                                                  'verdict': 'ok'}), \
             patch('core.agents.consistency_review_agent.ConsistencyReviewAgent.execute',
                   lambda self, state, pn, text: {'pass': True, 'overall_score': 8,
                                                  'issues': [], 'character_states': {},
                                                  'verdict': 'ok'}), \
             patch('core.agents.style_optimizer_agent.StyleOptimizerAgent.execute',
                   lambda self, state, pn, text, **kw: text), \
             patch('services.consistency_repair.ConsistencyRepairer.maybe_repair_part',
                   fake_repair), \
             patch.object(wpr_mod.logger, 'warning', spy_warning):
            asyncio.run(Phase4Runner(svc).run())  # 不抛异常即通过（告警不停机）

    alerts = [w for w in warnings_seen if '连续' in w and '修复未通过' in w]
    assert len(alerts) == 2, f'F,F,F,P,F,F,F 应恰好 2 次告警（第 4 个 passed 复位）: {alerts}'
    assert '连续 3 个 Part 修复未通过' in alerts[0]
    assert '建议人工介入核查 revision_log' in alerts[0]
    assert 'Part 3' in alerts[0], '首次告警应指向 Part 3'
    assert 'Part 7' in alerts[1], '复位后第二次告警应指向 Part 7'
    logger.info('[test_consec_alert] PASS: 连续 3 次失败告警、passed 复位、管线不抛异常')


if __name__ == '__main__':
    logger.info('=' * 60)
    logger.info('test_round5_watchdogs.py —— Round 5 看门狗回归（mock LLM）')
    logger.info('=' * 60)
    import tempfile
    for fn in (test_foreshadow_flags_not_injected_into_prompt,
               test_emit_log_dual_write):
        fn()
        print(f'PASS {fn.__name__}')
    with tempfile.TemporaryDirectory() as td:
        test_consecutive_repair_failure_alert(Path(td))
    print('PASS test_consecutive_repair_failure_alert')
    logger.info('\nALL PASS')
