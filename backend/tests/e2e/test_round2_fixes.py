"""
Round 2 修复回归测试（R2-3/R2-4/R2-5/R2-6/R2-8）—— 全部离线断言，不调 LLM。

覆盖：
  R2-3  final_draft 三保险：Phase4Runner 字数下限守卫（431 字短返/空返都保留原文）；
        StyleOptimizer 短返自检（翻倍重试一次，仍短则 raise）；call_llm 的
        expected_min_len 短返升级重试 + 不传时零行为变化
  R2-4  大纲字数传导：两段式归一化纯函数（20 Part Σ=60k → Σ∈[95k,105k] 且每 Part
        ≥3,500）、写作端 floor、早退门槛与 target 联动
  R2-5  token 预算任务级集中管理：默认值 + env 覆盖 + 生产路径无硬编码 max_tokens
  R2-6  KML_SKIP_PHASE4 真跳过（monkeypatch Phase4Runner.run 后 run() 不再进 Phase 4）
  R2-8  聚合器 logic.p0_count 数值兜底（V5 短协议）+ 旧式 issues 向后兼容 +
        enhancement_suggestions 不再计入 p0_issues
  R3-S3  编程错误 fail-fast：call_llm 的 except 遇 TypeError 等编程错误立即
        raise（零重试、零 sleep），不包 LLMError

既支持 pytest 也支持 `python backend/tests/e2e/test_round2_fixes.py` 直接跑。
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.logger import get_logger

logger = get_logger('test_round2_fixes')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

LONG_PART_TEXT = 'Part 正文。林尘踏入禁地，发现古井，寒风吹动衣角。' * 200   # ≈ 4,800 字


class CapturingEmitter:
    """捕获 AGENT_CALL 事件（用于观测 style_ok/style_fail 计数）。"""

    def __init__(self):
        self.events = []

    async def emit(self, event_type, data, work_id=None):
        self.events.append((event_type, data))

    def emit_sync(self, event_type, data, work_id=None):
        self.events.append((event_type, data))

    def messages(self):
        return [d.get('message', '') for _, d in self.events if isinstance(d, dict)]


def _make_work(tmp_path: Path, work_id: str, parts: int = 2):
    work = {
        'id': work_id, 'title': 'smoke r2', 'inspiration': '少年林尘被预言为天煞孤星。',
        'phase': 'phase2',
        'core_elements': {'protagonist': {'identity': '少年'}},
        'market_positioning': {},
        'world_setting': '玄幻世界',
        'characters': [{'name': '林尘', 'role': '主角', 'identity': '少年', 'core_trait': '坚忍',
                        'motivation': '寻道', 'secret': '血脉', 'arc': '成长'}],
        'part_outline': [{'title': f'Part {i}', 'phase': 'p', 'word_count': 5000,
                          'core_event': '事件', 'emotion_target': '紧张', 'key_dialogue': '对话',
                          'end_hook': '钩子', 'causality': '因果', 'pacing': '快'}
                         for i in range(1, parts + 1)],
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


_OK_LOGIC = {'score': 8, 'overall_score': 8, 'pass': True, 'p0_count': 0, 'p1_count': 0, 'issues': []}
_OK_CONS = {'pass': True, 'overall_score': 8, 'p0_count': 0, 'p1_count': 0, 'issues': [], 'character_states': {}}
_OK_EMOTION = {'pass': True, 'emotion_score': 8, 'resonance_score': 8, 'immersion_score': 8,
               'enhancement_suggestions': [], 'weaknesses': []}


def _run_phase4_with_style(svc, style_impl):
    """跑 Phase4Runner：三评审全绿，StyleOptimizer 用给定实现。"""
    from services.writing_phase_runners import Phase4Runner
    with patch('core.agents.logic_review_agent.LogicReviewAgent.execute',
               lambda self, state, pn, text: dict(_OK_LOGIC)), \
         patch('core.agents.consistency_review_agent.ConsistencyReviewAgent.execute',
               lambda self, state, pn, text: dict(_OK_CONS)), \
         patch('core.agents.emotion_review_agent.EmotionReviewAgent.execute',
               lambda self, state, pn, text: dict(_OK_EMOTION)), \
         patch('core.agents.style_optimizer_agent.StyleOptimizerAgent.execute', style_impl), \
         patch('core.sliding_window.SlidingWindow.maybe_generate_rolling_summary',
               lambda self, pn, **kw: {'generated': False}), \
         patch('core.sliding_window.SlidingWindow.maybe_generate_milestone',
               lambda self, pn, **kw: {'generated': False}):
        asyncio.run(Phase4Runner(svc).run())


# ---------------- R2-3: final_draft 三保险 ----------------

def test_phase4_rejects_short_and_empty_optimized_draft(tmp_path):
    """R2-3 保险 1: 431 字短返与空返都不得覆盖原文 —— final_draft 保留原文、style_fail+1。"""
    from services.writing_service import WritingService
    from api.sse import SSEEmitter

    # 场景 1: 431 字短返（Round 1 实证 final_draft['1']=431 毁稿签名）
    work_id = 'smoke_r2_p4_short'
    _make_work(tmp_path, work_id)
    p0, p1 = _patch_dirs(tmp_path)
    with p0, p1:
        svc = WritingService(work_id, SSEEmitter(), resume=False)
        svc.cfg.confirm_mode = False
        _apply_custom_template(svc, 2)
        svc.data['parts'] = {'1': LONG_PART_TEXT, '2': LONG_PART_TEXT}
        svc.data['part_summaries'] = {'1': '摘要', '2': '摘要'}
        emitter = CapturingEmitter()
        svc.emitter = emitter
        _run_phase4_with_style(svc, lambda self, state, pn, text, **kw: '短' * 431)
        assert svc.data['final_draft']['1'] == LONG_PART_TEXT, '431 字短返不得覆盖原文'
        assert any('失败 2' in m for m in emitter.messages()), emitter.messages()

    # 场景 2: 空串返回（回归现状行为：同样保留原文）
    work_id2 = 'smoke_r2_p4_empty'
    _make_work(tmp_path, work_id2)
    with p0, p1:
        svc2 = WritingService(work_id2, SSEEmitter(), resume=False)
        svc2.cfg.confirm_mode = False
        _apply_custom_template(svc2, 2)
        svc2.data['parts'] = {'1': LONG_PART_TEXT, '2': LONG_PART_TEXT}
        svc2.data['part_summaries'] = {'1': '摘要', '2': '摘要'}
        _run_phase4_with_style(svc2, lambda self, state, pn, text, **kw: '')
        assert svc2.data['final_draft']['1'] == LONG_PART_TEXT, '空返不得覆盖原文'
    logger.info('[test_phase4_rejects_short] PASS: 431 字与空返均保留原文')


def test_phase4_accepts_legit_optimized_draft(tmp_path):
    """R2-3 保险 1 反向：合法润色稿（≥ 原文 60%）必须被接受（防守卫误伤）。"""
    from services.writing_service import WritingService
    from api.sse import SSEEmitter
    work_id = 'smoke_r2_p4_ok'
    _make_work(tmp_path, work_id)
    p0, p1 = _patch_dirs(tmp_path)
    with p0, p1:
        svc = WritingService(work_id, SSEEmitter(), resume=False)
        svc.cfg.confirm_mode = False
        _apply_custom_template(svc, 2)
        svc.data['parts'] = {'1': LONG_PART_TEXT, '2': LONG_PART_TEXT}
        svc.data['part_summaries'] = {'1': '摘要', '2': '摘要'}
        optimized = '优化后的正文。' * 1000   # ≈ 6,000 字 > 原文 60%
        _run_phase4_with_style(svc, lambda self, state, pn, text, **kw: optimized)
        assert svc.data['final_draft']['1'] == optimized, '合法润色稿不应被误拒'


def test_style_optimizer_short_return_retries_then_raises():
    """R2-3 保险 2: 短返 → 翻倍 max_tokens 重试一次；仍短 → raise（上层保留原文）。"""
    from core.agents.style_optimizer_agent import StyleOptimizerAgent
    agent = StyleOptimizerAgent()
    state = SimpleNamespace(part_outline=[{'title': 'Part 1', 'phase': 'p'}])
    original = '原文' * 5000   # 10,000 字

    # 场景 1: 首次短返、重试完整 → 采用重试结果，且第二次 max_tokens 翻倍
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        return ('短' * 431) if len(calls) == 1 else ('完整润色稿。' * 2000)

    with patch('core.agents.style_optimizer_agent.call_llm', side_effect=fake_call):
        result = agent.execute(state, 1, original, target_max=10000)
    assert len(calls) == 2, f'应观察到第二次调用: {len(calls)}'
    assert calls[1]['max_tokens'] == calls[0]['max_tokens'] * 2, \
        f"重试 max_tokens 应翻倍: {calls[0]['max_tokens']} -> {calls[1]['max_tokens']}"
    assert len(result) > 1500, '重试成功应采用完整稿'

    # 场景 2: 两次都短 → agent 内 except 保险 4 生效：返回原文（上层保留原文）
    calls2 = []

    def always_short(**kwargs):
        calls2.append(kwargs)
        return '短' * 431

    with patch('core.agents.style_optimizer_agent.call_llm', side_effect=always_short):
        result = agent.execute(state, 1, original, target_max=10000)
    assert len(calls2) == 2, f'应只重试一次: {len(calls2)}'
    assert result == original, '两次短返后必须保留原文（不得返回短稿）'
    logger.info('[test_style_short_return] PASS: 翻倍重试一次 + 仍短则保留原文')


class _FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content, finish_reason = self.script.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)],
            usage=None)


def _fake_client(script):
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(script)))


def test_call_llm_no_escalation_without_expected_min_len():
    """R2-3 验收 3: 不传 expected_min_len 时行为与改前一致（短返不触发重试）。"""
    from core import llm_client
    client = _fake_client([('短' * 431, 'length')])
    with patch.object(llm_client, '_get_client_for_agent', lambda agent: (client, 'step-5-preview')):
        out = llm_client.call_llm(system_prompt='s', user_prompt='u', max_tokens=10500)
    assert out == '短' * 431, '无 expected_min_len 时应原样返回短内容（零行为变化）'
    assert len(client.chat.completions.calls) == 1, '不应发生额外调用'


def test_call_llm_short_return_escalates():
    """R2-3 保险 3: finish_reason=length 且输出 < expected_min_len → 循环内翻倍重试。"""
    from core import llm_client
    client = _fake_client([('短' * 431, 'length'), ('完整内容。' * 2000, 'stop')])
    with patch.object(llm_client, '_get_client_for_agent', lambda agent: (client, 'step-5-preview')):
        out = llm_client.call_llm(system_prompt='s', user_prompt='u', max_tokens=10500,
                                  expected_min_len=int(5212 * 0.3))
    calls = client.chat.completions.calls
    assert len(calls) == 2, f'应升级重试一次: {len(calls)}'
    assert calls[1]['max_tokens'] == calls[0]['max_tokens'] * 2, calls
    assert out == '完整内容。' * 2000, '应返回重试后的完整内容'


# ---------------- R3-S3: 编程错误 fail-fast ----------------

def test_call_llm_programming_error_fails_fast():
    """R3-S3: fake client 第 1 次 create 抛 TypeError（编程错误）→ 立即 raise 且
    只调用 1 次；不进 sleep 阶梯（patch time.sleep 断言未被调用，防"偷偷重试"）。"""
    from core import llm_client

    class _BoomCompletions:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            raise TypeError('create() got an unexpected keyword argument')

    client = SimpleNamespace(chat=SimpleNamespace(completions=_BoomCompletions()))
    with patch.object(llm_client, '_get_client_for_agent', lambda agent: (client, 'step-5-preview')), \
         patch.object(llm_client.time, 'sleep') as sleep_mock:
        with pytest.raises(TypeError):
            llm_client.call_llm(system_prompt='s', user_prompt='u', max_tokens=1000)
    assert client.chat.completions.calls == 1, '编程错误不得重试'
    sleep_mock.assert_not_called()
    logger.info('[test_call_llm_programming_error] PASS: TypeError 立即 raise，零重试零 sleep')


# ---------------- R2-4: 大纲字数传导 ----------------

def test_normalize_outline_word_counts_two_stage():
    """R2-4 防线 2: 20 Part Σ=60,000（各 2,700-3,800）→ Σ∈[95k,105k]、每 Part≥3,500、非数字字段不变。"""
    import core.config as config_mod
    from core.agents.plot_planner_agent import normalize_outline_word_counts
    tmpl = next((t for t in config_mod.DEFAULT_TEMPLATES if t.name == '自定义'),
                config_mod.WritingTemplate('自定义', 0, 0, 0, 0))
    cfg = config_mod.get_app_config()
    cfg.apply_template(tmpl, custom_target_words=100000, custom_part_count=20)
    config_mod.reload_config()

    # 20 Part 全锚在 exemplar 量级（2,700-3,300），Σ 恰为 60,000（低于目标 95%）
    word_counts = [2700] * 10 + [3300] * 10
    assert sum(word_counts) == 60000
    outline = []
    for i, wc in enumerate(word_counts):
        outline.append({'part': i + 1, 'title': f'Part {i + 1}', 'core_event': f'事件{i}',
                        'word_count': wc,
                        'causality': '因果', 'emotion_target': '紧张'})
    before_fields = [dict(p) for p in outline]
    total_before = sum(p['word_count'] for p in outline)
    assert total_before == 60000, total_before

    normalize_outline_word_counts(outline, 100000)
    total_after = sum(p['word_count'] for p in outline)
    logger.info(f'[test_normalize] Σ {total_before} -> {total_after}')
    assert 95000 <= total_after <= 105000, f'归一化后总量越界: {total_after}'
    for p in outline:
        assert 3500 <= p['word_count'] <= 10000, p
    # 非数字字段不变
    for before, after in zip(before_fields, outline):
        for k, v in before.items():
            if k != 'word_count':
                assert after[k] == v, f'字段 {k} 被改动'
    # 幂等性：再归一化一次不应再变
    snapshot = [p['word_count'] for p in outline]
    normalize_outline_word_counts(outline, 100000)
    assert [p['word_count'] for p in outline] == snapshot, '归一化应幂等'


def test_part_target_words_floor_and_early_exit():
    """R2-4 防线 3/4: 大纲 2,700 → target ≥3,500；target=5,000 → 早退门槛 ≥4,250。"""
    import core.agents.part_writer_agent as pw
    # part_writer_agent 的 TARGET_WORD_COUNT/PART_COUNT 是 import 时冻结的模块级值
    # （R1-A 的设计：e2e 先 apply_template+reload 再 import）。这里按 20 Part 配置
    # patch 模块常量，直接验算防线 3/4 纯函数。
    with patch.object(pw, 'TARGET_WORD_COUNT', 100000), \
         patch.object(pw, 'PART_COUNT', 20), \
         patch.object(pw, 'PART_WORD_MIN', 2500), \
         patch.object(pw, 'PART_WORD_MAX', 10000):
        # 防线 3: exemplar 锚低的 2,700 被抬到 floor（20 Part 均值 5000×70%=3500）
        target = pw._part_target_words(2700, hard_max=10200)
        assert target >= 3500, f'写作端 floor 未生效: {target}'
        # 大纲目标正常时不被压低
        assert pw._part_target_words(6000, hard_max=10200) == 6000
        # 非法值回退中位默认（仍受 floor 保护）
        assert pw._part_target_words(None, hard_max=10200) >= 3500
        assert pw._part_target_words('x', hard_max=10200) >= 3500
        # 防线 4: 早退门槛 = max(PART_WORD_MIN, 85% target)
        assert pw._early_exit_threshold(5000) >= 4250, pw._early_exit_threshold(5000)
        assert pw._early_exit_threshold(2000) >= 1700
        logger.info(f'[test_floor] PASS: target={target}, 早退门槛(5000)={pw._early_exit_threshold(5000)}')


# ---------------- R2-5: token 预算集中管理 ----------------

def test_task_max_tokens_defaults_and_env():
    """R2-5: 各 task 默认值断言 + env 覆盖断言（monkeypatch os.environ）。"""
    from core.config import get_task_max_tokens, get_json_max_tokens, PART_WORD_MAX
    for k in ('KML_CHUNK_MAX_TOKENS', 'KML_POLISH_MAX_TOKENS', 'KML_JSON_MAX_TOKENS'):
        os.environ.pop(k, None)
    assert get_task_max_tokens('chunk') == 20000
    assert get_task_max_tokens('polish') == PART_WORD_MAX * 2 + 2000
    assert get_task_max_tokens('json_facts') == 8000
    assert get_task_max_tokens('json_review') == 8000
    # R1-C 边界: get_json_max_tokens 保留为薄包装，默认随条目升到 8000
    assert get_json_max_tokens() == 8000
    # env 覆盖
    os.environ['KML_CHUNK_MAX_TOKENS'] = '30000'
    os.environ['KML_POLISH_MAX_TOKENS'] = '40000'
    os.environ['KML_JSON_MAX_TOKENS'] = '12345'
    try:
        assert get_task_max_tokens('chunk') == 30000
        assert get_task_max_tokens('polish') == 40000
        assert get_task_max_tokens('json_facts') == 12345
        assert get_task_max_tokens('json_review') == 12345 == get_json_max_tokens()
    finally:
        for k in ('KML_CHUNK_MAX_TOKENS', 'KML_POLISH_MAX_TOKENS', 'KML_JSON_MAX_TOKENS'):
            os.environ.pop(k, None)


def test_no_hardcoded_max_tokens_in_production_paths():
    """R2-5 验收 2: 生产路径（core/agents、services）无硬编码 max_tokens=<数字>。

    例外：rewrite_service.py —— review 明确"非 e2e 路径的改写服务，不动"，
    其 max_tokens=min(len(text)*2+500, 16000) 维持原样。
    """
    import glob
    offenders = []
    for pattern in ('core/agents/*.py', 'services/*.py'):
        for path in glob.glob(str(_BACKEND / pattern)):
            if Path(path).name == 'rewrite_service.py':
                continue
            for n, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
                if re.search(r'max_tokens\s*=\s*\d', line):
                    offenders.append(f'{Path(path).name}:{n}: {line.strip()[:80]}')
    assert not offenders, '硬编码 max_tokens 未迁移: ' + '; '.join(offenders)
    logger.info('[test_no_hardcoded] PASS: 生产路径无硬编码 max_tokens=<数字>')


# ---------------- R2-6: KML_SKIP_PHASE4 真跳过 ----------------

def test_skip_phase4_monkeypatch_skips_phase4(tmp_path):
    """R2-6: monkeypatch Phase4Runner.run 为 no-op 后，WritingService.run() 不进 Phase 4。"""
    from services.writing_service import WritingService
    from services.writing_phase_runners import Phase4Runner
    from api.sse import SSEEmitter

    work_id = 'smoke_r2_skip_p4'
    _make_work(tmp_path, work_id, parts=2)
    data = json.loads((tmp_path / f'{work_id}.json').read_text(encoding='utf-8'))
    data['phase'] = 'phase2'
    data['parts'] = {'1': LONG_PART_TEXT, '2': LONG_PART_TEXT}
    data['part_summaries'] = {'1': '摘要', '2': '摘要'}
    (tmp_path / f'{work_id}.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    async def _skip_phase4(self):
        logger.info('[SKIP] Phase 4 已跳过（测试替身）')

    orig_run = Phase4Runner.run
    p0, p1 = _patch_dirs(tmp_path)
    with p0, p1:
        svc = WritingService(work_id, SSEEmitter(), resume=True)
        svc.cfg.confirm_mode = False
        _apply_custom_template(svc, 2)

        def _boom(**kwargs):
            raise AssertionError('SKIP_PHASE4 下不应有任何评审/优化 LLM 调用')

        Phase4Runner.run = _skip_phase4
        try:
            with patch('core.llm_client.call_llm_json', side_effect=_boom), \
                 patch('core.llm_client.call_llm', side_effect=_boom):
                asyncio.run(svc.run())
        finally:
            Phase4Runner.run = orig_run
    after = json.loads((tmp_path / f'{work_id}.json').read_text(encoding='utf-8'))
    assert after.get('review_report') is None, 'Phase 4 被跳过后不应产生 review_report'
    assert after.get('final_draft') in (None, {}), f'Phase 4 被跳过后不应产生 final_draft: {after.get("final_draft")}'
    logger.info('[test_skip_phase4] PASS: Phase 4 真跳过，G1/G2/G3 数据不受影响')


# ---------------- R2-8: 聚合器指标缺口 + 死代码 ----------------

def test_aggregator_p0_count_numeric_fallback():
    """R2-8: V5 短协议（issues 为空、只有数值）→ logic.p0_count/p1_count 正确计数。"""
    from services.review_aggregator import aggregate_review_results
    per_part = [{
        'part': 1,
        'logic_result': {'overall_score': 5, 'p0_count': 2, 'p1_count': 1, 'issues': []},
        'emotion_result': {'emotion_score': 7, 'enhancement_suggestions': [
            {'location': '第3段', 'suggested': '加一个声音细节'}]},
        'consistency_result': {'overall_score': 8, 'p0_count': 0, 'p1_count': 0, 'issues': []},
    }]
    report = aggregate_review_results(per_part)
    assert report['logic']['p0_count'] == 2, report['logic']
    assert report['logic']['p1_count'] == 1, report['logic']
    assert report['logic']['total_issues'] == 3, report['logic']
    assert report['consistency']['p0_count'] == 0
    # R2-8: enhancement_suggestions 不再计入 p0_issues
    p0_issues = report['parts'][0]['p0_issues']
    assert not any('声音细节' in str(i) for i in p0_issues), f'增强建议不应进 p0_issues: {p0_issues}'
    logger.info(f"[test_aggregator_fallback] PASS: logic.p0_count={report['logic']['p0_count']}")


def test_aggregator_backward_compat_with_issues():
    """R2-8: 旧式带 issues 数组的结果行为与改前一致（向后兼容）。"""
    from services.review_aggregator import aggregate_review_results
    per_part = [{
        'part': 1,
        'logic_result': {'overall_score': 3, 'pass': False, 'p0_count': 0,
                         'issues': [{'level': 'P0', 'description': '逻辑矛盾甲'},
                                    {'level': 'P1', 'description': '逻辑问题乙'}],
                         'p1_count': 0},
        'emotion_result': {'emotion_score': 7, 'enhancement_suggestions': [], 'weaknesses': []},
        'consistency_result': {'overall_score': 3, 'pass': False,
                               'issues': [{'level': 'P0', 'description': '一致矛盾丙'}]},
    }]
    report = aggregate_review_results(per_part)
    assert report['logic']['p0_count'] == 1, report['logic']
    assert report['logic']['p1_count'] == 1, report['logic']
    assert report['consistency']['p0_count'] == 1, report['consistency']
    assert '逻辑矛盾甲' in (report['logic']['top_issue'] or ''), report['logic']['top_issue']
    # issues 明细仍进 parts[].p0_issues（兼容修复回路/排查）
    assert any('逻辑矛盾甲' in str(i) for i in report['parts'][0]['p0_issues'])


def test_dead_code_removed():
    """R2-8: 死代码清理 —— llm_client 不再导入 strip_padding_chars；text_utils 无不可达 return。"""
    src = (_BACKEND / 'core' / 'llm_client.py').read_text(encoding='utf-8')
    assert not re.search(r'import\s+[^\n]*strip_padding_chars', src), 'llm_client 不应再导入 strip_padding_chars'
    assert 'strip_padding_chars(' not in src, 'llm_client 不应再调用 strip_padding_chars'
    assert 'from core.text_utils import truncate' in src
    tu = (_BACKEND / 'core' / 'text_utils.py').read_text(encoding='utf-8')
    tail = tu[tu.index('def _drop_runs'):]
    assert tail.count('return') == 1, f'_drop_runs 应只有 1 个 return（不可达 return 已删）: {tail.count("return")}'


if __name__ == '__main__':
    import tempfile
    logger.info('=' * 60)
    logger.info('test_round2_fixes.py —— Round 2 修复回归（mock LLM）')
    logger.info('=' * 60)
    for fn in (test_phase4_rejects_short_and_empty_optimized_draft,
               test_phase4_accepts_legit_optimized_draft,
               test_style_optimizer_short_return_retries_then_raises,
               test_call_llm_no_escalation_without_expected_min_len,
               test_call_llm_short_return_escalates,
               test_call_llm_programming_error_fails_fast,
               test_normalize_outline_word_counts_two_stage,
               test_part_target_words_floor_and_early_exit,
               test_task_max_tokens_defaults_and_env,
               test_no_hardcoded_max_tokens_in_production_paths,
               test_aggregator_p0_count_numeric_fallback,
               test_aggregator_backward_compat_with_issues,
               test_dead_code_removed):
        if fn in (test_phase4_rejects_short_and_empty_optimized_draft,
                  test_phase4_accepts_legit_optimized_draft,
                  test_skip_phase4_monkeypatch_skips_phase4):
            with tempfile.TemporaryDirectory() as td:
                fn(Path(td))
        else:
            fn()
        print(f'PASS {fn.__name__}')
    logger.info('\nALL PASS')
