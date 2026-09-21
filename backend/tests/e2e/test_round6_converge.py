"""
Round 6 收敛轮回归测试（test_round6_converge.py）
—— 全部离线断言，不调 LLM（call_llm/call_llm_json/create 均 patch 或脚本化假 agent）。

按 02_review.md 批准清单的 S 编号组织：
  S5（R6-3）429 退避重试 + 全局并发信号量（carve-out 独立计数器 ≤5 次不计入
          3 次 attempt 预算 + full jitter + Retry-After 封顶 60s + 模块级
          BoundedSemaphore 默认 4）
  S4（R6-4）Phase4 逐 Part checkpoint（评审/风格增量落盘 + resume 跳过 +
          needs_rerun + KML_SKIP_STYLE）
  S1（R6-1）logic 审查明细补取（两段式 + 计数守恒 + anchor 逐字校验）
  S2（R6-2）修复动作分层持久化（过闸定点修复独立落盘 + 残留非名称 P0 不连坐
          落重写 + _rewrite_repair base 参数 + first_pass_p0 恒定）
  S3（R6-5）SEARCH/REPLACE 定点编辑重写（唯一性/±30%/全文 10%/非重叠硬闸）
  S6（R6-6）退场角色复现探测器（advisory-only + 复用 derive_departed_characters）

既支持 pytest 也支持 `python backend/tests/e2e/test_round6_converge.py` 直接跑。
"""
import asyncio
import importlib.util
import json
import os
import pathlib
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.logger import get_logger

logger = get_logger('test_round6_converge')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _load_verify_module():
    """从门禁脚本 import evaluate_g4 / summarize_name_audit（屏蔽 RUN_DIR.mkdir）。"""
    verify_path = _HERE / 'verify_step5_longform.py'
    spec = importlib.util.spec_from_file_location('verify_under_test_r6', verify_path)
    mod = importlib.util.module_from_spec(spec)
    orig_mkdir = pathlib.Path.mkdir
    pathlib.Path.mkdir = lambda self, *a, **k: None
    try:
        spec.loader.exec_module(mod)
    finally:
        pathlib.Path.mkdir = orig_mkdir
    return mod


_verify_mod = _load_verify_module()
evaluate_g4 = _verify_mod.evaluate_g4
summarize_name_audit = _verify_mod.summarize_name_audit

import core.llm_client as llm_client  # noqa: E402
from core.error_handler import LLMError, SystemError  # noqa: E402
from core.name_registry import build_name_registry  # noqa: E402
from services.consistency_repair import (  # noqa: E402
    ConsistencyRepairer, apply_name_spotfix, apply_safety_gates,
    derive_name_pairs, has_name_issue,
)
from services.name_audit import (  # noqa: E402
    AUDIT_LOG_KEY, DRIFT_DICT_KEY, audit_name_drift, is_blocking,
    record_name_pairs,
)
from services.review_aggregator import aggregate_review_results  # noqa: E402
from services.writing_phase_runners import Phase4Runner  # noqa: E402

# ---------------- 共享 fixture（Round 4/5 冒烟真实数据形态，逐字沿用） ----------------

R6_CHARACTERS = [
    {'name': '林尘', 'role': '主角', 'identity': '被家族封禁十年的"天煞孤星"'},
    {'name': '林万重', 'role': '核心配角/反派', 'identity': '林氏大长老，祠堂审判主持者'},
    {'name': '殷刹', 'role': '反派', 'identity': '上古神族巡界使'},
    {'name': '林轻眉', 'role': '核心配角', 'identity': '林尘生母，十年前"病逝"的守井人'},
]

SMOKE_A_PART2_EXCERPT = (
    '大长老林渊突然发出一声凄厉的惨笑。他袖中传讯玉毫无征兆地炸成齑粉，碎片割破了他的手腕，'
    '鲜血淋漓。他却浑然不觉，只是颤抖着指向井口。\n\n'
    '“时辰到了，时辰到了啊。”\n\n'
    '林万重从牌位堆里爬起，满脸是血：“大长老，您老人家这是怎么了？那可是您的传讯玉，'
    '是神族赐下来的命牌！”\n\n'
    '“闭嘴。”林渊嘶声吼道，“他来了。他终究还是来了。”\n\n'
    '“谁？”\n\n'
    '“巡界使。”\n\n'
    '殷刹就是在这时降临的。\n\n'
    '天空没有预兆地裂开一道金纹，仿佛有一柄无形的刀，将苍穹生生劈成两半。'
    '一只穿着云纹靴的脚率先踏出，落地无声，祠堂内浓郁的黑气却如遇天敌，尖叫着退散。'
    '那人一身玄色锦袍，腰悬古玉，面容俊美得不似活人。\n\n'
    '林万重和大长老同时跪伏在地，额头触地：“恭迎巡界使大人。”'
)

SMOKE_A_NAME_ISSUE = {
    'level': 'P0',
    'dimension': '名称一致性',
    'character': '林万重/林渊',
    'location': "Part 2中从'大长老林渊突然发出一声凄厉的惨笑'到'林万重和大长老同时扑向林尘'等多处段落",
    'description': ('角色档案明确设定林万重为林氏大长老，且Part 1中由其主持祠堂审判。'
                    '但Part 2中突然出现另一位\'大长老林渊\'，林万重不仅称其为\'大长老\'，'
                    '还提及\'您的传讯玉\'，而角色档案中传讯玉应属林万重。'
                    '二人以不同身份并存并同时行动，导致核心配角身份撕裂，'
                    '属于同一角色被错误拆分或名字混用。'),
    'suggestion': ('统一大长老身份：若维持林万重为大长老，应将所有\'林渊\'改为\'林万重\'，'
                   '并删除林万重对其以\'大长老\'相称的对话。'),
}


class _FakeService:
    """ConsistencyRepairer / Phase4Runner 所需的最小 service 面（离线）。"""

    def __init__(self, data=None):
        self.data = data if isinstance(data, dict) else {}
        self.work_id = 'r6_converge_test'
        self.vector_store = None
        self.progress_callback = lambda *a, **k: None
        self.saved_chunks = {}
        self.save_count = 0
        self.cfg = SimpleNamespace(part_count=2, target_word_count=10000)

        class _Emitter:
            async def emit(self, *a, **k):
                pass

        self.emitter = _Emitter()

    def _save_chunk_progress(self, part_num, text, summary=None):
        self.saved_chunks[part_num] = text

    def _save(self):
        self.save_count += 1

    @staticmethod
    def _review_failure(kind, part_num, err):
        return {'pass': False, 'overall_score': 3,
                'issues': [{'level': 'P0', 'dimension': '自动审查',
                            'location': f'Part {part_num}',
                            'description': f'审查失败: {err}'}],
                'verdict': f'检查失败（降级评分）: {err}'}


class _ScriptedAgent:
    """按脚本返回评审结果的假 agent（同步 execute，_re_review 走 to_thread）。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def execute(self, state, part_num, part_text):
        self.calls.append(part_text)
        if self.script:
            return self.script.pop(0)
        return {'pass': True, 'overall_score': 8, 'issues': [], 'verdict': 'ok'}


def _clean_logic(p0=0, verdict='ok'):
    return {'score': 8, 'overall_score': 8, 'pass': True, 'p0_count': p0,
            'p1_count': 0, 'issues': [], 'verdict': verdict}


def _clean_cons(p0_issues=None, score=8):
    return {'pass': True, 'overall_score': score, 'issues': p0_issues or [],
            'character_states': {}, 'verdict': 'ok'}


def _clean_emotion(score=8):
    return {'pass': True, 'emotion_score': score, 'resonance_score': score,
            'immersion_score': score, 'verdict': 'ok', 'weaknesses': [],
            'enhancement_suggestions': []}


def _repairer(service, logic_script, cons_script):
    return ConsistencyRepairer(service, _ScriptedAgent(logic_script),
                               _ScriptedAgent(cons_script))


def _mock_state():
    return type('M', (), {'parts': {}, 'final_draft': {}})()


# ---------------- S5（R6-3）: 429 退避 + 全局并发信号量 ----------------

class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)
        self.finish_reason = 'stop'
        self.delta = None


class _FakeResponse:
    def __init__(self, content='ok'):
        self.choices = [_FakeChoice(content)]
        self.usage = None


class _FakeCompletions:
    """脚本化 create：列表元素为 Exception 则抛、否则作为响应返回。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if self.script else _FakeResponse()
        if isinstance(item, Exception):
            raise item
        return item


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)


def _rate_limit_error(msg='429 - concurrency reached, current: 6, limit: 5',
                      headers=None):
    """构造带 status_code=429 的限流异常（reval 实证报文形态）。"""
    e = Exception(msg)
    e.status_code = 429
    e.response = SimpleNamespace(headers=headers if headers is not None else {})
    return e


def _patch_sleep():
    """patch llm_client 的 time.sleep 并记录 sleep 序列（避免真实等待）。"""
    sleeps: list = []

    def _fake_sleep(s):
        sleeps.append(s)

    return sleeps, patch.object(llm_client.time, 'sleep', _fake_sleep)


def test_call_llm_429_backoff_then_success():
    """S5 验收 1: 前 3 次 429、第 4 次成功 → 成功返回、429 不计入 attempt 预算。

    若 429 消耗 attempt 预算（3 次），第 3 次 429 即抛异常；独立计数器下
    第 4 次调用成功，且无 3*(attempt+1) 阶梯 sleep（只有 3 次 jitter 退避）。
    """
    comps = _FakeCompletions([_rate_limit_error(), _rate_limit_error(),
                              _rate_limit_error(), _FakeResponse('成功内容')])
    sleeps, sleep_patch = _patch_sleep()
    with patch.object(llm_client, '_get_client_for_agent',
                      lambda agent: (_FakeClient(comps), 'test-model')), sleep_patch:
        out = llm_client.call_llm('sys', 'user', agent='逻辑校验Agent')
    assert out == '成功内容'
    assert len(comps.calls) == 4, '429 重试不得消耗 3 次 attempt 预算'
    assert len(sleeps) == 3, '每次 429 恰好一次退避 sleep'
    assert all(s <= 30 for s in sleeps), 'full jitter cap 30s'


def test_call_llm_429_exhaustion_raises_systemerror():
    """S5 验收 2: 持续 429 → 恰好 5 次 rl 重试后抛 SystemError（最终分类口径）。"""
    comps = _FakeCompletions([_rate_limit_error()] * 10)
    sleeps, sleep_patch = _patch_sleep()
    with patch.object(llm_client, '_get_client_for_agent',
                      lambda agent: (_FakeClient(comps), 'test-model')), sleep_patch:
        with pytest.raises(SystemError) as ei:
            llm_client.call_llm('sys', 'user', agent='逻辑校验Agent')
    assert len(comps.calls) == 6, '1 次首发 + 5 次退避重试'
    assert len(sleeps) == 5
    assert '速率限制' in str(ei.value)


def test_retry_after_header_capped_and_jitter_bound():
    """S5 验收 3: Retry-After 封顶 60s；无响应头 → sleep ∈ [0, min(30, 2^rl)]。"""
    e120 = _rate_limit_error(headers={'retry-after': '120'})
    assert llm_client._rate_limit_sleep_seconds(e120, 1) == 60.0
    e_ms = _rate_limit_error(headers={'retry-after-ms': '90000'})
    assert llm_client._rate_limit_sleep_seconds(e_ms, 1) == 60.0
    e_small = _rate_limit_error(headers={'retry-after': '2.5'})
    assert llm_client._rate_limit_sleep_seconds(e_small, 1) == 2.5
    e_bad = _rate_limit_error(headers={'retry-after': 'not-a-number'})
    for rl in range(1, 6):
        s = llm_client._rate_limit_sleep_seconds(e_bad, rl)
        assert 0 <= s <= min(30, 2 ** rl), (rl, s)


def test_is_client_error_429_carveout():
    """S5 验收 4: 429 不再一刀切归为客户端错误；400 fail-fast / 500 可重试不变。"""
    import httpx
    from openai import (BadRequestError, InternalServerError, RateLimitError)
    req = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
    rl = RateLimitError('rate limited', response=httpx.Response(429, request=req), body=None)
    assert llm_client._is_client_error(rl) is False
    br = BadRequestError('bad request', response=httpx.Response(400, request=req), body=None)
    assert llm_client._is_client_error(br) is True
    ise = InternalServerError('boom', response=httpx.Response(500, request=req), body=None)
    assert llm_client._is_client_error(ise) is False
    # 报文关键词兜底（代理包装的异常不带 SDK 类型）
    assert llm_client._is_client_error(_rate_limit_error()) is False


def test_non_429_paths_zero_behavior_change():
    """S5 验收 5: 非 429 路径逐字节不变 —— 阶梯 sleep [3,6] + 4xx fail-fast。"""
    comps = _FakeCompletions([Exception('server exploded'), Exception('server exploded'),
                              _FakeResponse('ok')])
    sleeps, sleep_patch = _patch_sleep()
    with patch.object(llm_client, '_get_client_for_agent',
                      lambda agent: (_FakeClient(comps), 'test-model')), sleep_patch:
        out = llm_client.call_llm('sys', 'user')
    assert out == 'ok' and len(comps.calls) == 3 and sleeps == [3, 6]
    # 4xx fail-fast：1 次调用即 LLMError、零 sleep
    br = Exception('400 bad request')
    br.status_code = 400
    comps2 = _FakeCompletions([br])
    sleeps2, sleep_patch2 = _patch_sleep()
    with patch.object(llm_client, '_get_client_for_agent',
                      lambda agent: (_FakeClient(comps2), 'test-model')), sleep_patch2:
        with pytest.raises(LLMError):
            llm_client.call_llm('sys', 'user')
    assert len(comps2.calls) == 1 and sleeps2 == []


def test_semaphore_caps_concurrency_peak():
    """S5 验收 6: KML_LLM_CONCURRENCY=2 时并发峰值 ≤2；BoundedSemaphore(4) 时 ≤4。"""
    def _run_with_limit(limit, n_threads):
        sem = threading.BoundedSemaphore(limit)
        peak = {'cur': 0, 'max': 0}
        lock = threading.Lock()

        def create(**kwargs):
            with lock:
                peak['cur'] += 1
                peak['max'] = max(peak['max'], peak['cur'])
            time.sleep(0.02)
            with lock:
                peak['cur'] -= 1
            return _FakeResponse('ok')

        comps = _FakeCompletions([])
        comps.create = create
        with patch.object(llm_client, '_LLM_SEMAPHORE', sem), \
             patch.object(llm_client, '_get_client_for_agent',
                          lambda agent: (_FakeClient(comps), 'test-model')):
            ts = [threading.Thread(target=lambda: llm_client.call_llm('s', 'u'))
                  for _ in range(n_threads)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=10)
        return peak['max']

    assert _run_with_limit(2, 6) <= 2
    assert _run_with_limit(4, 8) <= 4
    # 模块级信号量类型与默认额度（env 未覆盖时默认 4）
    assert isinstance(llm_client._LLM_SEMAPHORE, threading.BoundedSemaphore)
    if 'KML_LLM_CONCURRENCY' not in os.environ:
        assert llm_client._LLM_CONCURRENCY == 4


def test_json_429_carveout_before_response_format_degradation():
    """S5 验收 7: 429 报文含 'invalid' 字样也不得误降 response_format。"""
    llm_client._response_format_cache.pop('test-model', None)
    err = Exception("429 - {'error': {'message': 'invalid request: rate limited'}}")
    err.status_code = 429
    comps = _FakeCompletions([err, _FakeResponse('{"score": 8}')])
    sleeps, sleep_patch = _patch_sleep()
    with patch.object(llm_client, '_supports_response_format', lambda model: True), \
         patch.object(llm_client, 'OpenAI', lambda **kw: _FakeClient(comps)), \
         patch.object(llm_client, 'get_llm_config_for_agent',
                      lambda agent: SimpleNamespace(api_key='k', base_url='b',
                                                    model='test-model',
                                                    json_model='test-model')), \
         sleep_patch:
        out = llm_client.call_llm_json('sys', 'user', agent='逻辑校验Agent')
    assert out == {'score': 8}
    assert 'test-model' not in llm_client._response_format_cache, '429 不得触发降级'
    assert len(comps.calls) == 2 and len(sleeps) == 1


def test_json_429_exhaustion_attaches_raw_text():
    """S5: call_llm_json 持续 429 → 5 次退避后 SystemError 且 raw_text 附加不变。"""
    llm_client._response_format_cache.pop('test-model', None)
    comps = _FakeCompletions([_rate_limit_error()] * 8)
    sleeps, sleep_patch = _patch_sleep()
    with patch.object(llm_client, 'OpenAI', lambda **kw: _FakeClient(comps)), \
         patch.object(llm_client, 'get_llm_config_for_agent',
                      lambda agent: SimpleNamespace(api_key='k', base_url='b',
                                                    model='test-model',
                                                    json_model='test-model')), \
         sleep_patch:
        with pytest.raises(SystemError) as ei:
            llm_client.call_llm_json('sys', 'user')
    assert len(comps.calls) == 6 and len(sleeps) == 5
    assert getattr(ei.value, 'raw_text', '') == '', '429 路径无解析产物，raw_text 为空串'


# ---------------- S4（R6-4）: Phase4 逐 Part checkpoint + KML_SKIP_STYLE ----------------

class _Phase4FakeService(_FakeService):
    """Phase4Runner.run() 所需的最小 service 面（离线，零 LLM）。"""

    def _build_review_state_mock(self):
        return SimpleNamespace(parts=dict(self.data.get('parts', {}) or {}),
                               final_draft=dict(self.data.get('final_draft', {}) or {}))

    @staticmethod
    def _aggregate_review_results(per_part_results):
        return aggregate_review_results(per_part_results)


class _ScriptedReviewAgent:
    """按 kind 确定性返回评审/风格结果的假 agent（记录 part 调用序列）。"""

    def __init__(self, kind, fn=None):
        self.kind = kind
        self.calls: list = []
        self._fn = fn

    def execute(self, state, part_num, part_text):
        self.calls.append(part_num)
        if self._fn is not None:
            return self._fn(part_num, part_text)
        if self.kind == 'logic':
            return _clean_logic()
        if self.kind == 'emotion':
            return _clean_emotion()
        if self.kind == 'consistency':
            return _clean_cons()
        return part_text  # style: 原文 passthrough


def _scripted_agents():
    return (_ScriptedReviewAgent('logic'), _ScriptedReviewAgent('emotion'),
            _ScriptedReviewAgent('consistency'), _ScriptedReviewAgent('style'))


def _patch_phase4_agents(logic_agent, emotion_agent, cons_agent, style_agent):
    """Phase4Runner.run() 内部 from-import，patch 源模块类对象。"""
    return (
        patch('core.agents.logic_review_agent.LogicReviewAgent', lambda: logic_agent),
        patch('core.agents.emotion_review_agent.EmotionReviewAgent', lambda: emotion_agent),
        patch('core.agents.consistency_review_agent.ConsistencyReviewAgent', lambda: cons_agent),
        patch('core.agents.style_optimizer_agent.StyleOptimizerAgent', lambda: style_agent),
    )


def _progress_entry(part_num, logic=None, emotion=None, cons=None,
                    repair_note=None, needs_rerun=False):
    return {'part': part_num,
            'logic_result': logic if logic is not None else _clean_logic(),
            'emotion_result': emotion if emotion is not None else _clean_emotion(),
            'consistency_result': cons if cons is not None else _clean_cons(),
            'repair_note': repair_note, 'needs_rerun': needs_rerun}


def _phase4_parts(n=20):
    return {str(i): f'Part {i} 正文内容，林尘踏入禁地。' * 60 for i in range(1, n + 1)}


def test_phase4_resume_skips_reviewed_parts():
    """S4 验收 1: 崩溃模拟 —— progress 含 Part 1-10 → Part 1-10 零 LLM 调用、
    Part 11-20 正常三审；聚合报告与"无 progress 全量跑"逐字节一致。"""
    parts = _phase4_parts(20)
    service = _Phase4FakeService({
        'parts': parts, 'phase': 'phase3_part20',
        'phase4_review_progress': [_progress_entry(i) for i in range(1, 11)]})
    la, ea, ca, sa = _scripted_agents()
    with _patch_phase4_agents(la, ea, ca, sa)[0], _patch_phase4_agents(la, ea, ca, sa)[1], \
         _patch_phase4_agents(la, ea, ca, sa)[2], _patch_phase4_agents(la, ea, ca, sa)[3]:
        asyncio.run(Phase4Runner(service).run())
    assert la.calls == list(range(11, 21)), la.calls
    assert ea.calls == list(range(11, 21)), ea.calls
    assert ca.calls == list(range(11, 21)), ca.calls
    # 与无 progress 全量跑逐字节一致（同脚本响应）
    service2 = _Phase4FakeService({'parts': parts, 'phase': 'phase3_part20'})
    la2, ea2, ca2, sa2 = _scripted_agents()
    with _patch_phase4_agents(la2, ea2, ca2, sa2)[0], _patch_phase4_agents(la2, ea2, ca2, sa2)[1], \
         _patch_phase4_agents(la2, ea2, ca2, sa2)[2], _patch_phase4_agents(la2, ea2, ca2, sa2)[3]:
        asyncio.run(Phase4Runner(service2).run())
    assert la2.calls == list(range(1, 21)), la2.calls
    assert service.data['review_report'] == service2.data['review_report']
    # progress 条目 JSON 可序列化（S4 验收 6）
    json.dumps(service.data, ensure_ascii=False)
    assert len(service.data['phase4_review_progress']) == 20
    logger.info('[test_phase4_resume] PASS: 已审 Part 零调用，聚合口径一致，可序列化')


def test_phase4_needs_rerun_part_is_re_reviewed():
    """S4 验收 2: needs_rerun=True（_fallback 降级）的 Part 被重审。"""
    parts = _phase4_parts(3)
    service = _Phase4FakeService({
        'parts': parts, 'phase': 'phase3_part3',
        'phase4_review_progress': [
            _progress_entry(1), _progress_entry(2),
            _progress_entry(3, logic=dict(_clean_logic(), _fallback=True,
                                          p0_count=1, verdict='审查失败（已降级评分）'),
                            needs_rerun=True)]})
    la, ea, ca, sa = _scripted_agents()
    p1, p2, p3, p4 = _patch_phase4_agents(la, ea, ca, sa)
    with p1, p2, p3, p4:
        asyncio.run(Phase4Runner(service).run())
    assert la.calls == [3] and ca.calls == [3] and ea.calls == [3], (la.calls, ea.calls, ca.calls)
    # 重审后 progress 该 Part 末条 needs_rerun=False
    last3 = [e for e in service.data['phase4_review_progress'] if e['part'] == 3][-1]
    assert last3['needs_rerun'] is False
    logger.info('[test_phase4_needs_rerun] PASS: 降级 Part 被重审且末条标记完成')


def test_phase4_progress_last_entry_wins():
    """S4 验收 3: 同 Part 多条 progress → 恢复时只取最后一条。"""
    parts = _phase4_parts(2)
    service = _Phase4FakeService({
        'parts': parts, 'phase': 'phase3_part2',
        'phase4_review_progress': [
            _progress_entry(1),
            _progress_entry(2, logic=_clean_logic()),
            _progress_entry(2, logic=dict(_clean_logic(), score=9, overall_score=9)),
        ]})
    la, ea, ca, sa = _scripted_agents()
    p1, p2, p3, p4 = _patch_phase4_agents(la, ea, ca, sa)
    with p1, p2, p3, p4:
        asyncio.run(Phase4Runner(service).run())
    assert la.calls == [], '两条 progress 已覆盖 Part 1-2，零重审'
    report = service.data['review_report']
    p2_entry = [p for p in report['parts'] if p['part'] == 2][0]
    assert p2_entry['logic_score'] == 9, p2_entry
    logger.info('[test_phase4_last_entry] PASS: 同 Part 多条取最后一条')


def test_phase4_style_checkpoint_skip_and_staleness():
    """S4 验收 4: 风格 checkpoint —— 已优化 Part 零 style 调用且用 checkpoint 文本；
    parts 被修复改变（长度≠source_len）→ 重新优化（防陈旧稿）。"""
    parts = _phase4_parts(2)
    style_text = '优化稿开头。' + '润色后的正文内容。' * 200
    service = _Phase4FakeService({
        'parts': parts, 'phase': 'phase3_part2',
        'phase4_style_progress': [
            {'part': 1, 'status': 'optimized', 'text': style_text,
             'source_len': len(parts['1'])}]})
    la, ea, ca, sa = _scripted_agents()
    p1, p2, p3, p4 = _patch_phase4_agents(la, ea, ca, sa)
    with p1, p2, p3, p4:
        asyncio.run(Phase4Runner(service).run())
    assert sa.calls == [2], 'Part 1 已 checkpoint 必须零 style 调用'
    assert service.data['final_draft']['1'] == style_text
    # 陈旧场景：parts['1'] 被修复改变（长度≠source_len）→ Part 1 重新 style
    parts_stale = dict(parts, **{'1': parts['1'] + '修复补充段落。' * 10})
    service2 = _Phase4FakeService({
        'parts': parts_stale, 'phase': 'phase3_part2',
        'phase4_style_progress': [
            {'part': 1, 'status': 'optimized', 'text': style_text,
             'source_len': len(parts['1'])}]})
    la2, ea2, ca2, sa2 = _scripted_agents()
    q1, q2, q3, q4 = _patch_phase4_agents(la2, ea2, ca2, sa2)
    with q1, q2, q3, q4:
        asyncio.run(Phase4Runner(service2).run())
    assert sa2.calls == [1, 2], 'source_len 不匹配必须重新优化'
    logger.info('[test_phase4_style_ckpt] PASS: checkpoint 跳过 + source_len 防陈旧')


def test_phase4_skip_style_env():
    """S4/R6-7 验收 5: KML_SKIP_STYLE=1 → 零 style 调用、名称终审与聚合照常。"""
    prev = os.environ.get('KML_SKIP_STYLE')
    os.environ['KML_SKIP_STYLE'] = '1'
    try:
        parts = _phase4_parts(3)
        service = _Phase4FakeService({'parts': parts, 'phase': 'phase3_part3'})
        la, ea, ca, sa = _scripted_agents()
        p1, p2, p3, p4 = _patch_phase4_agents(la, ea, ca, sa)
        with p1, p2, p3, p4:
            asyncio.run(Phase4Runner(service).run())
    finally:
        if prev is None:
            os.environ.pop('KML_SKIP_STYLE', None)
        else:
            os.environ['KML_SKIP_STYLE'] = prev
    assert sa.calls == [], 'KML_SKIP_STYLE=1 不得调用 StyleOptimizer'
    assert service.data['review_report'], '聚合照常产出'
    assert service.data['final_draft'] == parts, '跳风格时 final_draft == parts'
    assert len(service.data['phase4_review_progress']) == 3
    logger.info('[test_phase4_skip_style] PASS: 零 style 调用，终审与聚合不变')


def test_reval_script_clears_progress_keys():
    """S4/R6-7 验收 2: reval 脚本构造的新 work 无 phase4 progress 键污染。"""
    reval_src = (_HERE.parent.parent.parent.parent / 'loop' / 'reval_phase4.py').read_text(encoding='utf-8')
    assert "data['phase4_review_progress'] = []" in reval_src
    assert "data['phase4_style_progress'] = []" in reval_src
    assert 'KML_SKIP_STYLE' in reval_src, 'reval 须透传打印 KML_SKIP_STYLE 状态'
    logger.info('[test_reval_clears] PASS: reval 同步清 progress 键并透传 SKIP 状态')


# ---------------- S1（R6-1）: logic 审查明细补取（计数守恒 + anchor 逐字校验） ----------------

class _LogicState:
    """LogicReviewAgent.execute 所需的最小 state 面（离线）。"""

    def __init__(self, part_text='', parts=None, summaries=None):
        self.part_outline = [{'core_event': f'核心事件{i}', 'emotion_target': '情绪目标',
                              'causality': '因果关系'} for i in range(1, 6)]
        self.characters = [{'name': '林尘', 'role': '主角', 'core_trait': '隐忍',
                            'motivation': '查明身世', 'secret': '天煞孤星'},
                           {'name': '林万重', 'role': '核心配角', 'core_trait': '阴沉',
                            'motivation': '夺权', 'secret': '私通外敌'}]
        self.world_setting = '世界观设定'
        self.part_summaries = summaries if summaries is not None else {'1': '前文摘要'}
        self.parts = parts if parts is not None else {'1': '前文正文'}
        self.character_state_track = {}
        self.name_registry = build_name_registry(R6_CHARACTERS)
        self.work_id = None

    def build_established_facts_block(self, part_num):
        return '【前文已确立事实清单】\n- 残玉在 Part 2 被苏晚晴收起'


def _logic_main_result(p0=2, verdict='残玉被苏晚晴收起后林尘却从怀中取出，物品位置矛盾'):
    return {'score': 3, 'pass': False, 'p0_count': p0, 'p1_count': 0,
            'p2_count': 0, 'verdict': verdict}


def _detail_issues(items):
    return {'issues': items, 'returned': len(items), 'total': len(items)}


def _run_logic_agent(part_text, main, detail, part_num=3):
    """脚本化 call_llm_json（首发主协议、次发明细协议）跑 LogicReviewAgent。"""
    from core.agents.logic_review_agent import LogicReviewAgent
    calls: list = []

    def fake_call(system_prompt, user_prompt, **kw):
        calls.append(system_prompt)
        if len(calls) == 1:
            return main
        if isinstance(detail, Exception):
            raise detail
        return detail

    with patch('core.agents.logic_review_agent.call_llm_json',
               side_effect=fake_call):
        result = LogicReviewAgent().execute(_LogicState(part_text=part_text),
                                            part_num, part_text)
    return result, calls


R6_LOGIC_PART_TEXT = (
    '林尘跌入古井，残玉被苏晚晴收起。后来林尘却从怀中取出残玉，径直走向井口。'
    '他突言母亲当年亲手换过神纹，径直推开了祠堂大门。'
)


def test_logic_detail_count_conservation_with_aggregator():
    """S1 验收 1: p0_count=2 + detail 回 2 条（anchor 均在正文）→ 2 条真实明细；
    聚合 total_p0/residual 与灌入前（数值兜底）完全一致（计数守恒）。"""
    detail = _detail_issues([
        {'idx': 1, 'dimension': '物品状态', 'anchor': '林尘却从怀中取出残玉',
         'claim': '残玉位置矛盾', 'conflict': '残玉在 Part 2 被苏晚晴收起'},
        {'idx': 2, 'dimension': '信息越界', 'anchor': '他突言母亲当年亲手换过神纹',
         'claim': '林尘突言母亲换神纹', 'conflict': '前文未揭示此信息'},
    ])
    result, calls = _run_logic_agent(R6_LOGIC_PART_TEXT,
                                     _logic_main_result(2), detail)
    assert len(calls) == 2, 'p0>0 应触发第二次明细调用'
    assert len(result['issues']) == 2
    assert result['_detail_returned'] == 2 and result['_detail_total'] == 2
    assert all(not i.get('_detail_placeholder') for i in result['issues'])
    assert result['_detail_protocol'] == 'V5+detail'
    # 计数守恒：灌入前后聚合口径一致（灌入前 issues=[] → 数值兜底 p0_count=2）
    base = dict(_logic_main_result(2), issues=[], overall_score=3)
    rep_before = aggregate_review_results(
        [{'part': 3, 'logic_result': base, 'emotion_result': {},
          'consistency_result': {}}])
    rep_after = aggregate_review_results(
        [{'part': 3, 'logic_result': result, 'emotion_result': {},
          'consistency_result': {}}])
    assert rep_before['logic']['p0_count'] == rep_after['logic']['p0_count'] == 2
    assert (rep_before['first_pass_total_p0'] == rep_after['first_pass_total_p0'] == 2)
    assert (rep_before['residual_total_p0'] == rep_after['residual_total_p0'] == 2)
    logger.info('[test_detail_conservation] PASS: 明细灌入聚合计数不变（计数守恒）')


def test_logic_detail_pad_and_truncate():
    """S1 验收 2: p0_count=3 + detail 回 1 条 → 3（1 真 + 2 占位）；
    detail 回 5 条 → 截断为 3；聚合计数均为 3。"""
    one = _detail_issues([
        {'idx': 1, 'dimension': '物品状态', 'anchor': '林尘却从怀中取出残玉',
         'claim': '残玉位置矛盾', 'conflict': 'Part 2 被苏晚晴收起'}])
    result1, _ = _run_logic_agent(R6_LOGIC_PART_TEXT, _logic_main_result(3), one)
    assert len(result1['issues']) == 3, result1['issues']
    assert sum(1 for i in result1['issues'] if i.get('_detail_placeholder')) == 2
    assert result1['_detail_returned'] == 1 and result1['_detail_total'] == 3
    rep = aggregate_review_results(
        [{'part': 3, 'logic_result': result1, 'emotion_result': {},
          'consistency_result': {}}])
    assert rep['logic']['p0_count'] == 3
    five = _detail_issues([
        {'idx': i, 'dimension': '物品状态', 'anchor': f'林尘跌入古井{i}',
         'claim': f'问题{i}', 'conflict': '冲突'} for i in range(1, 6)])
    result5, _ = _run_logic_agent(R6_LOGIC_PART_TEXT, _logic_main_result(3), five)
    assert len(result5['issues']) == 3, 'detail 回 5 条必须截断为 3'
    assert all(not i.get('_detail_placeholder') for i in result5['issues'])
    logger.info('[test_detail_pad_truncate] PASS: 少补占位、多截断，计数守恒')


def test_logic_detail_failure_degrades_to_v5():
    """S1 验收 3: detail 抛异常 / 返回非 dict → issues=[]，unified 与 V5 一致
    （除新增观测字段）；主路径结果不受影响。"""
    result_exc, calls_exc = _run_logic_agent(
        R6_LOGIC_PART_TEXT, _logic_main_result(2),
        RuntimeError('429 - concurrency reached'))
    assert result_exc['issues'] == []
    assert result_exc['score'] == 3 and result_exc['p0_count'] == 2
    assert result_exc['_detail_protocol'] == 'V5+detail'
    assert result_exc['_detail_returned'] == 0 and result_exc['_detail_total'] == 2
    assert len(calls_exc) == 2
    # 非 dict（list）→ 同样退化为 V5
    result_list, _ = _run_logic_agent(R6_LOGIC_PART_TEXT,
                                      _logic_main_result(2), ['not', 'a', 'dict'])
    assert result_list['issues'] == [] and result_list['_detail_returned'] == 0
    # 聚合计数回落数值兜底（2），与无明细一致
    rep = aggregate_review_results(
        [{'part': 3, 'logic_result': result_list, 'emotion_result': {},
          'consistency_result': {}}])
    assert rep['logic']['p0_count'] == 2
    logger.info('[test_detail_failure] PASS: 异常/非 dict 退化 V5，零回归')


def test_logic_detail_hallucinated_anchor_cleared():
    """S1 验收 4: anchor 不在正文（幻觉引文）→ anchor 清空、issue 保留、
    计数守恒成立（location 无锚点、description 无引文段）。"""
    detail = _detail_issues([
        {'idx': 1, 'dimension': '物品状态', 'anchor': '林尘从袖中抖出一枚玉佩',
         'claim': '物品位置矛盾', 'conflict': '前文在匣中'},
        {'idx': 2, 'dimension': '时间线', 'anchor': '林尘却从怀中取出残玉',
         'claim': '时间线不连续', 'conflict': '白天突然入夜'},
    ])
    result, _ = _run_logic_agent(R6_LOGIC_PART_TEXT, _logic_main_result(2), detail)
    assert len(result['issues']) == 2
    fake, real = result['issues'][0], result['issues'][1]
    assert 'anchor' not in fake['location'] and '原文：' not in fake['description']
    assert fake['location'] == 'Part 3'
    assert '（锚点：' in real['location'] and '原文：“' in real['description']
    rep = aggregate_review_results(
        [{'part': 3, 'logic_result': result, 'emotion_result': {},
          'consistency_result': {}}])
    assert rep['logic']['p0_count'] == 2
    logger.info('[test_detail_anchor] PASS: 幻觉引文清空、issue 保留、计数守恒')


def test_build_revision_brief_includes_logic_detail():
    """S1 验收 5: logic issues 非空时 brief 含明细行（[Part N（锚点：…）] …
    冲突：…）且不再出现 generic 分支文案。"""
    detail = _detail_issues([
        {'idx': 1, 'dimension': '物品状态', 'anchor': '林尘却从怀中取出残玉',
         'claim': '残玉位置矛盾', 'conflict': '残玉在 Part 2 被苏晚晴收起'}])
    logic_result, _ = _run_logic_agent(R6_LOGIC_PART_TEXT,
                                       _logic_main_result(2), detail)
    repairer = ConsistencyRepairer(_FakeService({}), _ScriptedAgent([]),
                                   _ScriptedAgent([]))
    brief = repairer._build_revision_brief(3, logic_result, _clean_cons())
    assert '[Part 3（锚点：' in brief, brief
    assert '冲突：残玉在 Part 2 被苏晚晴收起' in brief, brief
    assert '逻辑审查未给出问题明细' not in brief, '有明细时不得走 generic 分支'
    # 反向：无明细（issues 空）时 generic 分支仍在（V5 行为不变）
    brief_v5 = repairer._build_revision_brief(
        3, dict(_logic_main_result(2), issues=[]), _clean_cons())
    assert '逻辑审查未给出问题明细' in brief_v5
    logger.info('[test_detail_brief] PASS: 明细行进 brief，generic 分支让位')


def test_logic_detail_kill_switch_and_part1():
    """S1 验收 6: KML_LOGIC_DETAIL=0 → 零补取调用；Part 1 不补取。"""
    prev = os.environ.get('KML_LOGIC_DETAIL')
    os.environ['KML_LOGIC_DETAIL'] = '0'
    try:
        _, calls = _run_logic_agent(R6_LOGIC_PART_TEXT, _logic_main_result(2),
                                    _detail_issues([
                                        {'idx': 1, 'dimension': '物品状态',
                                         'anchor': '林尘却从怀中取出残玉',
                                         'claim': 'x', 'conflict': 'y'}]))
        assert len(calls) == 1, 'KML_LOGIC_DETAIL=0 必须零补取调用'
    finally:
        if prev is None:
            os.environ.pop('KML_LOGIC_DETAIL', None)
        else:
            os.environ['KML_LOGIC_DETAIL'] = prev
    # Part 1：强制 p0=0 → 不补取（主调用 1 次）
    result1, calls1 = _run_logic_agent(R6_LOGIC_PART_TEXT,
                                       {'score': 10, 'pass': True, 'p0_count': 0,
                                        'p1_count': 0, 'p2_count': 0,
                                        'verdict': '首部无前文基线'},
                                       _detail_issues([]), part_num=1)
    assert len(calls1) == 1 and result1['issues'] == []
    assert '_detail_protocol' not in result1, 'Part 1 不得进补取分支'
    logger.info('[test_detail_killswitch] PASS: kill-switch 与 Part 1 零补取')


def test_logic_detail_prompt_file_and_fallback_synced():
    """S1: detail prompt 文件与内嵌 fallback 同含三句硬约束（R5-4 纪律）。"""
    from core.agents.logic_review_agent import DETAIL_SYSTEM_PROMPT
    prompt_file = (_HERE.parent.parent.parent / 'prompts'
                   / 'logic_review_detail.txt').read_text(encoding='utf-8')
    for constraint in ('最多列出 3 条', '逐字复制', '不超过 30 字', '不得翻页'):
        assert constraint in DETAIL_SYSTEM_PROMPT, constraint
        assert constraint in prompt_file, constraint
    logger.info('[test_detail_prompt_sync] PASS: 文件与 fallback 同步含硬约束')


# ---------------- S2（R6-2）: 修复动作分层持久化 ----------------

def test_s2_partial_applied_falls_through_to_rewrite():
    """S2 验收 1-3: spotfix 过闸 + 重审残留 2 个非名称 P0 → 修复稿独立落盘
    （partial_applied + residual_non_name_p0=2 + state_mock 快照同步），残留
    以修复稿为 base 转重写（brief 含"姓名已按名册归一化"、劣化判据用效审后
    基线 → 立即回退 base_text），first_pass_p0 恒定原始首检数，合并进
    per_part_results 的 note 不含 logic_result/_base_* 键。"""
    reg = build_name_registry(R6_CHARACTERS)
    excerpt = SMOKE_A_PART2_EXCERPT
    fixed_excerpt = excerpt.replace('林渊', '林万重')
    service = _FakeService({'name_registry': reg, 'character_state_track': {},
                            'parts': {'2': excerpt}})
    # 首检 logic 2 P0 + cons 1 名称 P0 = 3；spotfix 重审 logic 仍 2、cons 干净
    # → residual=2（全是 logic 非名称）；重写后重审仍 2（无改善）→ 劣化回退
    logic_script = [_clean_logic(2, verdict='仍有矛盾'), _clean_logic(2, verdict='重写仍矛盾')]
    cons_script = [_clean_cons(), _clean_cons()]
    repairer = _repairer(service, logic_script, cons_script)
    rewrite_calls: list = []
    captured: dict = {}

    async def fake_rewrite(self, part_num, brief):
        rewrite_calls.append(brief)
        return '重写稿内容。' * 400, ''

    orig_rewrite_repair = ConsistencyRepairer._rewrite_repair

    async def spy_rewrite_repair(self, part_num, part_text, p0_before,
                                 logic_result, consistency_result, state_mock,
                                 registry, **kw):
        captured.update(kw)
        captured['p0_before'] = p0_before
        captured['part_text'] = part_text
        return await orig_rewrite_repair(
            self, part_num, part_text, p0_before, logic_result,
            consistency_result, state_mock, registry, **kw)

    state_mock = _mock_state()
    with patch.object(ConsistencyRepairer, '_rewrite_once', fake_rewrite), \
         patch.object(ConsistencyRepairer, '_rewrite_repair', spy_rewrite_repair):
        note = asyncio.run(repairer.maybe_repair_part(
            2, excerpt, _clean_logic(2, verdict='林渊尸体时间线矛盾'),
            {'issues': [SMOKE_A_NAME_ISSUE], 'verdict': 'ok', 'overall_score': 6},
            state_mock=state_mock))

    # 1) 修复稿独立落盘（不是原文），state_mock 快照同步（后续 Part 评审看得到）
    assert service.saved_chunks[2] == fixed_excerpt, '过闸修复必须独立保留'
    assert service.saved_chunks[2].count('林渊') == 0
    assert state_mock.parts['2'] == fixed_excerpt
    assert state_mock.final_draft['2'] == fixed_excerpt
    # 2) 双留痕：revision_log partial_applied + residual_non_name_p0=2
    partial = [e for e in service.data['revision_log'] if e.get('partial_applied')]
    assert len(partial) == 1, service.data['revision_log']
    assert partial[0]['residual_non_name_p0'] == 2, partial[0]
    assert partial[0]['revision_passed'] is False and partial[0]['p0_before'] == 3
    audit = [e for e in service.data[AUDIT_LOG_KEY]
             if e.get('action') == 'partial_applied']
    assert len(audit) == 1 and audit[0]['trigger'] != 'final_audit'
    # 3) 残留转重写：base_text=修复稿、p0_before=效审后残留、基线为效审后值
    assert len(rewrite_calls) == 1, '劣化判据用效审后基线 → 立即回退不进第二轮'
    assert captured['base_text'] == fixed_excerpt
    assert captured['p0_before'] == 2
    assert captured['base_logic']['p0_count'] == 2
    assert '姓名已按名册归一化' in rewrite_calls[0]
    assert "'林渊'→'林万重'" in rewrite_calls[0]
    # 4) 回退落在 base_text（修复稿）；note 无 logic_result/_base_* 键
    assert note.get('revision_passed') is False
    assert note.get('revision_degraded') is True
    for k in ('logic_result', 'consistency_result', '_base_text',
              '_base_logic', '_base_cons', '_base_pairs'):
        assert k not in note, k
    # 5) first_pass_p0 恒定原始首检数（聚合预算护栏不被修复过程改变）
    assert note.get('first_pass_p0') == 3
    entry = {'part': 2, 'logic_result': _clean_logic(2), 'emotion_result': {},
             'consistency_result': {'issues': [SMOKE_A_NAME_ISSUE]}}
    entry.update(note)
    report = aggregate_review_results([entry])
    assert report['parts'][0]['first_pass_p0'] == 3
    assert report['parts'][0]['residual_p0'] == 2
    logger.info('[test_s2_partial] PASS: 修复独立落盘 + 残留不连坐转重写 + 基线正确')


def test_s2_spotfix_pass_branch_unchanged():
    """S2 验收 5: spotfix 通过分支（residual=0）行为不变 —— 落盘修复稿、
    applied_verified 沉淀、无重写调用（R5 既有语义零改）。"""
    reg = build_name_registry(R6_CHARACTERS)
    service = _FakeService({'name_registry': reg, 'character_state_track': {},
                            'parts': {'2': SMOKE_A_PART2_EXCERPT}})
    repairer = _repairer(service, [_clean_logic(0)], [_clean_cons()])
    rewrite_calls = []

    async def fake_rewrite(self, part_num, brief):
        rewrite_calls.append(brief)
        return 'x', ''

    state_mock = _mock_state()
    with patch.object(ConsistencyRepairer, '_rewrite_once', fake_rewrite):
        note = asyncio.run(repairer.maybe_repair_part(
            2, SMOKE_A_PART2_EXCERPT, _clean_logic(2),
            {'issues': [SMOKE_A_NAME_ISSUE], 'verdict': 'ok'},
            state_mock=state_mock))
    assert note.get('revision_passed') is True and note.get('revision_spotfixed') is True
    assert note.get('first_pass_p0') == 3
    assert 'partial_applied' not in note and '_base_text' not in note
    assert rewrite_calls == [], '通过分支不得转重写'
    assert service.saved_chunks[2].count('林渊') == 0
    assert service.data[DRIFT_DICT_KEY]['林渊']['applied_verified'] is True
    logger.info('[test_s2_pass_branch] PASS: 通过分支行为不变')


# ---------------- S3（R6-5）: SEARCH/REPLACE 定点编辑重写 ----------------

from services.consistency_repair import (  # noqa: E402
    TARGETED_EDIT_SYSTEM, _apply_targeted_edits, _edit_trigger_ok,
    _issue_anchor,
)

R6_EDIT_PART_TEXT = (
    '林尘跌入古井，残玉被苏晚晴收起。后来林尘却从怀中取出残玉把玩，径直走向井口。'
    '他突言母亲当年亲手换过神纹之事，猛然推开了祠堂的朱漆大门。'
    '井底青光一闪，苏晚晴的身影出现在石碑之后。守井人喃喃自语，说这是三十年来的异象。'
)

# SEARCH 片段均 ≥15 字（编辑协议硬闸）、在全文恰好出现 1 次
R6_EDIT_S1 = '林尘却从怀中取出残玉把玩，径直'
R6_EDIT_S2 = '他突言母亲当年亲手换过神纹之事'
R6_EDIT_S3 = '，苏晚晴的身影出现在石碑之后。'
R6_EDIT_R1 = '林尘只从袖中取出残玉端详，转身'
R6_EDIT_R2 = '他无意间提及母亲换神纹的旧事'
R6_EDIT_R3 = '，石碑之后闪过一道故人的旧影。'


def _blocks(pairs):
    """构造 SEARCH/REPLACE 编辑块文本（[(search, replace), ...]）。"""
    return '\n'.join(f'<<<<<<< SEARCH\n{s}\n=======\n{r}\n>>>>>>> REPLACE'
                     for s, r in pairs)


R6_EDIT_BLOCKS = _blocks([(R6_EDIT_S1, R6_EDIT_R1),
                          (R6_EDIT_S2, R6_EDIT_R2),
                          (R6_EDIT_S3, R6_EDIT_R3)])
R6_EDIT_EXPECTED = (R6_EDIT_PART_TEXT
                    .replace(R6_EDIT_S1, R6_EDIT_R1)
                    .replace(R6_EDIT_S2, R6_EDIT_R2)
                    .replace(R6_EDIT_S3, R6_EDIT_R3))


def _edit_issue(dimension, quote, claim, conflict, suggestion=''):
    return {'level': 'P0', 'dimension': dimension, 'character': '',
            'location': 'Part 3',
            'description': f'{claim}：“{quote}”，{conflict}',
            'suggestion': suggestion}


R6_EDIT_ISSUES = [
    _edit_issue('物品状态', R6_EDIT_S1, '物品位置矛盾',
                '残玉在 Part 2 已被苏晚晴收起', '改为从袖中取出'),
    _edit_issue('信息越界', R6_EDIT_S2, '信息越界',
                '前文未揭示此信息', '改为无意提及'),
    _edit_issue('状态连续性', R6_EDIT_S3, '退场角色复现',
                '苏晚晴已退场', '改为故人旧影'),
]


def test_apply_targeted_edits_pure_function_guards():
    """S3 验收 3/4: 应用器硬闸 —— 唯一性/≥15 字/±30%/非重叠/全文 10%/逐字。"""
    # 前置：fixture 自身满足协议长度（SEARCH ≥15 且在全文唯一）
    for s in (R6_EDIT_S1, R6_EDIT_S2, R6_EDIT_S3):
        assert len(s) >= 15 and R6_EDIT_PART_TEXT.count(s) == 1
    # 合法块：应用成功、块外文本逐字节不变
    new_text, applied, failures = _apply_targeted_edits(R6_EDIT_PART_TEXT, R6_EDIT_BLOCKS)
    assert applied == 3 and failures == [], (applied, failures)
    assert new_text == R6_EDIT_EXPECTED
    # SEARCH 不在正文逐字出现（模型改写）→ 块弃用，永不模糊匹配
    t2, a2, f2 = _apply_targeted_edits(
        R6_EDIT_PART_TEXT, _blocks([(R6_EDIT_S1.replace('却', '竟'), R6_EDIT_R1)]))
    assert a2 == 0 and t2 == R6_EDIT_PART_TEXT and 'search_not_unique' in f2
    # 唯一性硬闸：SEARCH 在正文出现 2 次 → 弃用（防改错位置，禁模糊匹配）
    dup_base = R6_EDIT_PART_TEXT + R6_EDIT_PART_TEXT
    t_dup, a_dup, f_dup = _apply_targeted_edits(
        dup_base, _blocks([(R6_EDIT_S1, R6_EDIT_R1)]))
    assert a_dup == 0 and t_dup == dup_base and 'search_not_unique' in f_dup
    # SEARCH <15 字 → 弃用
    t3, a3, f3 = _apply_targeted_edits(
        R6_EDIT_PART_TEXT, _blocks([('林尘跌入古井', '林尘跌落古井')]))
    assert a3 == 0 and any('too_short' in x for x in f3)
    # REPLACE 超 ±30% → 弃用
    t4, a4, f4 = _apply_targeted_edits(
        R6_EDIT_PART_TEXT, _blocks([(R6_EDIT_S2, R6_EDIT_S2 + '足足三十余字')]))
    assert a4 == 0 and 'replace_len_out_of_tolerance' in f4
    # 块重叠：第二块与第一块在原文中重叠 → 第二块弃用、第一块应用
    o1, o2 = '林尘跌入古井，残玉被苏晚晴收起', '残玉被苏晚晴收起。后来林尘却从'
    assert R6_EDIT_PART_TEXT.count(o1) == 1 and R6_EDIT_PART_TEXT.count(o2) == 1
    t5, a5, f5 = _apply_targeted_edits(
        R6_EDIT_PART_TEXT,
        _blocks([(o1, '林尘跌入古井，残玉被苏晚晴收好'), (o2, '残玉被苏晚晴收起。此后林尘却')]))
    assert a5 == 1 and 'blocks_overlap' in f5
    assert '残玉被苏晚晴收好' in t5
    # 全文变化 >10% → 整体回退（3 块各 +4 字 / 全文 107 字 ≈ 11.2%）
    guard_pairs = [(s, s + '底细种种') for s in (R6_EDIT_S1, R6_EDIT_S2, R6_EDIT_S3)]
    t6, a6, f6 = _apply_targeted_edits(R6_EDIT_PART_TEXT, _blocks(guard_pairs))
    assert a6 == 0 and t6 == R6_EDIT_PART_TEXT and 'total_len_exceeded' in f6
    # 无有效块 / 块数 >3 取前 3
    t7, a7, f7 = _apply_targeted_edits(R6_EDIT_PART_TEXT, '没有任何编辑块的普通文本')
    assert a7 == 0 and 'no_valid_block' in f7
    four = _blocks([(R6_EDIT_S1, R6_EDIT_R1), (R6_EDIT_S2, R6_EDIT_R2),
                    (R6_EDIT_S3, R6_EDIT_R3),
                    ('守井人喃喃自语，说这是三十年来的异象', '守井人喃喃自语，说起三十年旧事')])
    t8, a8, _ = _apply_targeted_edits(R6_EDIT_PART_TEXT, four)
    assert a8 == 3, '超过 3 块只取前 3'
    logger.info('[test_apply_edits] PASS: 应用器六道硬闸 + 截断全部符合预期')


def test_edit_trigger_requires_unique_anchor_per_p0():
    """S3 触发条件: P0≤3 且每个 P0 有唯一 anchor；缺 anchor / P0>3 均不触发。"""
    text = R6_EDIT_PART_TEXT
    cons = _clean_cons(R6_EDIT_ISSUES)
    ok, anchors = _edit_trigger_ok(text, _clean_logic(0), cons)
    assert ok is True and len(anchors) == 3, anchors
    # 引文在正文出现 2 次 → 无唯一 anchor → 不触发
    dup_text = text + '后来林尘却从怀中取出残玉把玩，径直的消息传遍了祠堂。'
    ok2, _ = _edit_trigger_ok(dup_text, _clean_logic(0), cons)
    assert ok2 is False
    # 无引号 span 的 issue → 无 anchor → 不触发
    no_quote = _clean_cons([{'level': 'P0', 'dimension': '物品状态',
                             'description': '物品位置矛盾但未给出引文',
                             'location': 'Part 3'}])
    assert _edit_trigger_ok(text, _clean_logic(0), no_quote)[0] is False
    # P0 > 3 → 不触发
    four = _clean_cons(R6_EDIT_ISSUES + [_edit_issue(
        '知识合理性', '守井人喃喃自语，说这是三十年来的异象', '知识合理性', 'x')])
    assert _edit_trigger_ok(text, _clean_logic(0), four)[0] is False
    # logic p0_count 与明细不一致（无明细）→ 不触发
    assert _edit_trigger_ok(text, _clean_logic(2), _clean_cons())[0] is False
    # _issue_anchor：description 优先、location 兜底、长度下限
    assert _issue_anchor(R6_EDIT_ISSUES[0], text) == R6_EDIT_S1
    loc_issue = {'level': 'P0', 'description': '无引文描述',
                 'location': "Part 3中从'林尘跌入古井，残玉被苏晚晴收起'起多处"}
    assert _issue_anchor(loc_issue, text) == '林尘跌入古井，残玉被苏晚晴收起'
    logger.info('[test_edit_trigger] PASS: 触发条件四类场景 + anchor 抽取双来源')


def test_s3_edit_applied_saved_and_brief():
    """S3 验收 1: 3 个 P0 各带唯一 anchor + 模型回 3 个合法块 → 应用成功、
    块外文本逐字节不变、落编辑稿；编辑 prompt 含编号明细与全文。"""
    service = _FakeService({'name_registry': {}, 'parts': {'3': R6_EDIT_PART_TEXT}})
    repairer = _repairer(service, [_clean_logic(0)], [_clean_cons()])
    edit_calls = []

    def fake_call_llm(system, user, *a, **k):
        edit_calls.append((system, user))
        return R6_EDIT_BLOCKS

    state_mock = _mock_state()
    with patch('services.consistency_repair.call_llm', side_effect=fake_call_llm):
        note = asyncio.run(repairer.maybe_repair_part(
            3, R6_EDIT_PART_TEXT, _clean_logic(0),
            _clean_cons(R6_EDIT_ISSUES), state_mock=state_mock))
    assert note.get('revision_passed') is True, note
    assert note.get('revision_edited') is True
    assert note.get('first_pass_p0') == 3
    saved = service.saved_chunks[3]
    assert saved == R6_EDIT_EXPECTED, '块外文本逐字节不变'
    assert state_mock.parts['3'] == saved and state_mock.final_draft['3'] == saved
    # 编辑 prompt：system 是编辑协议、user 含编号明细行与全文
    assert len(edit_calls) == 1
    sys_p, user_p = edit_calls[0]
    assert '<<<<<<< SEARCH' in sys_p and '逐字复制' in sys_p
    assert '1. [Part 3] 物品位置矛盾' in user_p, 'brief 明细行须编号'
    assert R6_EDIT_PART_TEXT in user_p, 'user 须含全文'
    # revision_log 留痕（targeted_edit，纯观测不影响聚合器）
    entry = [e for e in service.data['revision_log'] if e.get('type') == 'targeted_edit']
    assert len(entry) == 1 and entry[0]['applied_blocks'] == 3
    logger.info('[test_s3_applied] PASS: 编辑应用/落盘/编号 brief/留痕全部符合')


def test_s3_no_edit_when_anchor_not_unique_falls_to_rewrite():
    """S3 验收 2/6: anchor 出现 2 次 → 零编辑调用、直接落重写且文本未被改。"""
    dup_text = R6_EDIT_PART_TEXT + '后来林尘却从怀中取出残玉把玩，径直的消息传遍了祠堂。'
    service = _FakeService({'name_registry': {}, 'parts': {'3': dup_text}})
    repairer = _repairer(service, [_clean_logic(0), _clean_logic(0)],
                         [_clean_cons(), _clean_cons()])
    edit_calls = []
    rewrite_calls = []

    def fake_call_llm(system, user, *a, **k):
        edit_calls.append(system)
        return R6_EDIT_BLOCKS

    async def fake_rewrite(self, part_num, brief):
        rewrite_calls.append(brief)
        return '重写稿内容。' * 400, ''

    with patch('services.consistency_repair.call_llm', side_effect=fake_call_llm), \
         patch.object(ConsistencyRepairer, '_rewrite_once', fake_rewrite):
        note = asyncio.run(repairer.maybe_repair_part(
            3, dup_text, _clean_logic(0), _clean_cons(R6_EDIT_ISSUES),
            state_mock=_mock_state()))
    assert edit_calls == [], 'anchor 不唯一不得触发编辑'
    assert len(rewrite_calls) == 1, '必须落全量重写'
    assert service.saved_chunks[3] == '重写稿内容。' * 400
    assert note.get('revision_passed') is True
    logger.info('[test_s3_no_edit] PASS: 无唯一 anchor 零编辑调用落重写')


def test_s3_edit_degraded_rolls_back_then_rewrite():
    """S3 验收 5: 编辑后重审劣化 → 回退 base_text（编辑层），带原 base 继续
    落重写；revision_log 双留痕（targeted_edit 劣化 + rewrite）。"""
    service = _FakeService({'name_registry': {}, 'parts': {'3': R6_EDIT_PART_TEXT}})
    # 编辑后重审：logic 2 P0 + cons 1 新类别 P0 → residual 3 ≥ 2 → 劣化
    edit_logic = _clean_logic(2, verdict='编辑后仍矛盾')
    edit_cons = _clean_cons([{'level': 'P0', 'dimension': '知识合理性',
                              'description': '编辑引入信息越界', 'location': 'Part 3'}])
    repairer = _repairer(service, [edit_logic, _clean_logic(0)],
                         [edit_cons, _clean_cons()])
    rewrite_calls = []

    async def fake_rewrite(self, part_num, brief):
        rewrite_calls.append(brief)
        return '重写稿内容。' * 400, ''

    with patch('services.consistency_repair.call_llm',
               side_effect=lambda *a, **k: R6_EDIT_BLOCKS), \
         patch.object(ConsistencyRepairer, '_rewrite_once', fake_rewrite):
        note = asyncio.run(repairer.maybe_repair_part(
            3, R6_EDIT_PART_TEXT, _clean_logic(0),
            _clean_cons(R6_EDIT_ISSUES), state_mock=_mock_state()))
    # 编辑层回退后重写通过 → 最终落重写稿；但编辑劣化事件留痕
    assert note.get('revision_passed') is True and len(rewrite_calls) == 1
    edit_entries = [e for e in service.data['revision_log']
                    if e.get('type') == 'targeted_edit']
    assert len(edit_entries) == 1 and edit_entries[0]['revision_degraded'] is True
    assert edit_entries[0]['residual_p0'] == 3
    logger.info('[test_s3_degraded] PASS: 编辑劣化回退 + 留痕 + 继续重写')


def test_s3_targeted_edit_prompt_file_and_fallback_synced():
    """S3 验收 7: 编辑 prompt 文件与内嵌 fallback 同含协议硬约束。"""
    prompt_file = (_HERE.parent.parent.parent / 'prompts'
                   / 'targeted_edit.txt').read_text(encoding='utf-8')
    for constraint in ('<<<<<<< SEARCH', '>>>>>>> REPLACE', '逐字复制',
                       '最多 3 个编辑块', '±30%'):
        assert constraint in TARGETED_EDIT_SYSTEM, constraint
        assert constraint in prompt_file, constraint
    logger.info('[test_s3_prompt_sync] PASS: 文件与 fallback 同步含硬约束')


# ---------------- S6（R6-6）: 退场角色复现探测器（advisory-only） ----------------

from services.name_audit import (  # noqa: E402
    _parse_departed_part, scan_departed_reappearance,
)

# departed facts（林渊 Part 8 死亡；林轻眉 Part 3 离开）—— R1-E 账本既有格式
R6_DEPARTED_FACTS = {'version': 1, 'facts': [
    {'id': 'F8_1', 'part_num': 8, 'category': 'character', 'subject': '林渊',
     'predicate': '死亡', 'text': '林渊在祠堂大战中身死'},
    {'id': 'F3_2', 'part_num': 3, 'category': 'character', 'subject': '林轻眉',
     'predicate': '离开', 'text': '林轻眉离开林家远走他乡'},
]}

R6_DEPARTED_CHARS = ['林尘', '林渊', '林轻眉', '林万重']

# Part 17：林渊×3（退场后复现）+ 林重×1（漂移名，供 advisory 沉淀断言）；
# 各 Part 正文均提及在场 canonical（隔离 B 探测器噪音）
R6_DEPARTED_DRAFT = {
    '1': '林尘跌入古井，林轻眉在井边看了他一眼。',
    '3': '林轻眉离开林家，林渊在家中设宴。',
    '9': '林渊的名字被提起一次，林轻眉望着远方。',
    '17': ('林渊的碑影立在坟前，林渊的声音再度响起，林渊的实体持刀参战，'
           '林轻眉在远处守望，林万重大步赶来，林重紧随其后。'),
}


def _departed_draft():
    return dict(R6_DEPARTED_DRAFT)


def _audit_service_r6(draft=None):
    """S6 终审 fixture： departed facts + final_draft。"""
    fd = draft if draft is not None else _departed_draft()
    return _FakeService({
        'name_registry': build_name_registry(
            [{'name': n, 'role': '配角'} for n in R6_DEPARTED_CHARS]),
        'character_state_track': {'林渊': 'Part8 死亡: 林渊在祠堂大战中身死'},
        'established_facts': R6_DEPARTED_FACTS,
        'characters': [{'name': n, 'role': '配角'} for n in R6_DEPARTED_CHARS],
        'parts': dict(fd),
        'final_draft': dict(fd),
        'name_drift_dict': {},
    })


def _clean_report():
    """干净跑法的 review_report（R4-3 新键齐全，20 Part）。"""
    return {
        'logic': {'avg_score': 8, 'pass': True, 'total_issues': 0, 'p0_count': 0,
                  'p1_count': 0, 'top_issue': '', 'parts_count': 20},
        'consistency': {'avg_score': 8, 'pass': True, 'total_issues': 0, 'p0_count': 0,
                        'p1_count': 0, 'top_issue': '', 'parts_count': 20},
        'parts': [], 'first_pass_total_p0': 0, 'residual_total_p0': 0,
        'revision_stats': {'attempted': 0, 'passed': 0, 'degraded': 0, 'spotfixed': 0},
    }


def test_scan_departed_reappearance_threshold():
    """S6 验收 1: 林渊 Part8 死亡 + Part 17 含 ×3 → finding（count=3）；
    Part 9 仅 1 次 → 不报（count<2 阈值缓释合法回忆/提及形式）。"""
    findings = scan_departed_reappearance(_departed_draft(), R6_DEPARTED_FACTS,
                                          R6_DEPARTED_CHARS)
    lin_yuan = [f for f in findings if f['character'] == '林渊']
    assert len(lin_yuan) == 1, findings
    f = lin_yuan[0]
    assert f['part'] == 17 and f['count'] == 3
    assert f['kind'] == 'departed_reappearance'
    assert len(f['samples']) == 2 and all('林渊' in s for s in f['samples'])
    assert not any(x['part'] == 9 for x in findings), 'count<2 不得报'
    # 林轻眉 Part3 离开：Part 9/17 有在场提及但 canonical 名出现 <2 次 → 无 finding
    assert not any(x['character'] == '林轻眉' for x in findings)
    # 账本值格式解析（PartN 前缀）
    assert _parse_departed_part('Part8 死亡: 林渊身死') == 8
    assert _parse_departed_part('无Part前缀') is None
    assert _parse_departed_part(None) is None
    logger.info('[test_departed_scan] PASS: 阈值/账本解析/samples 全部符合')


def test_departed_finding_never_enters_g4():
    """S6 验收 2: departed finding 不进 residual_blocking、不改文本、不进 G4。"""
    findings = scan_departed_reappearance(_departed_draft(), R6_DEPARTED_FACTS,
                                          R6_DEPARTED_CHARS)
    assert findings, 'fixture 应有命中'
    # 模拟 name_audit_log 留痕（departed_flagged + rereviewed）后 G4 不受影响
    data = {'final_draft': _departed_draft(), 'name_drift_dict': {},
            'name_audit_log': [
                {'part': 17, 'wrong': '', 'right': '林渊', 'count_before': 3,
                 'count_after': 3, 'action': 'departed_flagged',
                 'trigger': 'final_audit', 'pair_source': 'departed_reappearance'},
                {'part': 17, 'wrong': '', 'right': '林渊', 'count_before': 3,
                 'count_after': 3, 'action': 'rereviewed',
                 'trigger': 'final_audit', 'pair_source': 'departed_reappearance'},
            ]}
    na = summarize_name_audit(data)
    assert na['residual_blocking'] == 0, na
    assert na['canonical_absent'] == 0 and na['residual_advisory'] == 0
    g4, detail = evaluate_g4(_clean_report(), 20, na)
    assert g4 is True and detail['name_audit']['residual_blocking'] == 0
    # wrong='' 不进 name_drift_dict：A 类残留重扫无原料
    scan = audit_name_drift(_departed_draft(), {}, R6_DEPARTED_FACTS,
                            build_name_registry(
                                [{'name': n, 'role': '配角'} for n in R6_DEPARTED_CHARS]))
    assert scan['residual_blocking'] == []
    logger.info('[test_departed_g4] PASS: advisory finding 对 G4 零影响')


def test_final_audit_departed_rereview_budget():
    """S6 验收 3a: 预算内恰好 1 次针对性重审；advisory 不改文本、双留痕；
    重审产出配对按 advisory 沉淀（gates_passed/applied_verified 不置位）——
    守住" departed 永不进 G4"裁定（探针⑪ 目标）。"""
    service = _audit_service_r6()
    before = dict(service.data['final_draft'])
    name_issue = {'level': 'P0', 'dimension': '名称一致性',
                  'character': '林万重/林重', 'location': 'Part 17',
                  'description': '林万重与林重的名字发生混用，需统一',
                  'suggestion': '统一使用规范名'}
    cons_agent = _ScriptedAgent([_clean_cons([name_issue])])
    asyncio.run(Phase4Runner(service)._final_name_audit(
        service, [17], cons_agent, SimpleNamespace(final_draft={})))
    assert len(cons_agent.calls) == 1, f'预算内恰好 1 次针对性重审: {cons_agent.calls}'
    assert cons_agent.calls[0] == R6_DEPARTED_DRAFT['17']
    assert service.data['final_draft'] == before, 'advisory 永不自动改文本'
    actions = [e['action'] for e in service.data[AUDIT_LOG_KEY]]
    assert 'departed_flagged' in actions and 'rereviewed' in actions
    flagged = [e for e in service.data[AUDIT_LOG_KEY]
               if e['pair_source'] == 'departed_reappearance']
    assert all(e['trigger'] == 'final_audit' for e in flagged)
    assert flagged[0]['wrong'] == '' and flagged[0]['right'] == '林渊'
    # 重审产出配对 → advisory 沉淀（对齐 R5-1 B 探测器裁定，不进 G4）
    drift = service.data[DRIFT_DICT_KEY]
    assert '林重' in drift, drift
    assert is_blocking(drift['林重']) is False, ' departed 重审配对不得升格 blocking'
    na = summarize_name_audit(service.data)
    assert na['residual_blocking'] == 0, na
    g4, detail = evaluate_g4(_clean_report(), 20, na)
    assert g4 is True and detail['name_audit']['residual_blocking'] == 0
    logger.info('[test_departed_budget] PASS: 1 次重审 + 不改文本 + advisory 不进 G4')


def test_final_audit_departed_coexists_with_canonical_absent():
    """S6 验收 3b: 与 canonical_absent 共存时排序确定（tier, part 升序）。"""
    # Part 3 缺林轻眉（B finding，tier 1）+ Part 17 退场复现（tier 1）→ Part 3 先
    draft = dict(R6_DEPARTED_DRAFT, **{'3': '林渊在家中设宴，无人提及轻眉。'})
    service = _audit_service_r6(draft)
    cons_agent = _ScriptedAgent([_clean_cons(), _clean_cons()])
    asyncio.run(Phase4Runner(service)._final_name_audit(
        service, [3, 17], cons_agent, SimpleNamespace(final_draft={})))
    assert cons_agent.calls == [draft['3'], draft['17']], cons_agent.calls
    kinds = [e['pair_source'] for e in service.data[AUDIT_LOG_KEY]
             if e['action'] == 'rereviewed']
    assert kinds == ['canonical_absent', 'departed_reappearance'], kinds
    logger.info('[test_departed_coexist] PASS: 与 B 共存排序确定（tier, part 升序）')


def test_scan_departed_dirty_data_fail_open():
    """S6 验收 4: 脏数据（facts None / character_names 空 / 占位）不抛异常。"""
    fd = _departed_draft()
    assert scan_departed_reappearance(fd, None, R6_DEPARTED_CHARS) == []
    assert scan_departed_reappearance(fd, R6_DEPARTED_FACTS, []) == []
    assert scan_departed_reappearance(fd, R6_DEPARTED_FACTS, None) == []
    assert scan_departed_reappearance(fd, 'not a dict', R6_DEPARTED_CHARS) == []
    assert scan_departed_reappearance(None, R6_DEPARTED_FACTS,
                                      R6_DEPARTED_CHARS) == []
    assert scan_departed_reappearance(fd, {'version': 1, 'facts': 'bad'},
                                      R6_DEPARTED_CHARS) == []
    # final_draft 含失败占位 / 非 str → 跳过
    dirty_fd = dict(fd, **{'20': '[Part 20 创作失败]', '21': None})
    findings = scan_departed_reappearance(dirty_fd, R6_DEPARTED_FACTS,
                                          R6_DEPARTED_CHARS)
    assert all(f['part'] != 20 and f['part'] != 21 for f in findings)
    logger.info('[test_departed_dirty] PASS: 脏数据 fail-open 不阻断')


if __name__ == '__main__':
    logger.info('=' * 60)
    logger.info('test_round6_converge.py —— Round 6 收敛轮回归（mock LLM）')
    logger.info('=' * 60)
    for fn in (test_call_llm_429_backoff_then_success,
               test_call_llm_429_exhaustion_raises_systemerror,
               test_retry_after_header_capped_and_jitter_bound,
               test_is_client_error_429_carveout,
               test_non_429_paths_zero_behavior_change,
               test_semaphore_caps_concurrency_peak,
               test_json_429_carveout_before_response_format_degradation,
               test_json_429_exhaustion_attaches_raw_text,
               test_phase4_resume_skips_reviewed_parts,
               test_phase4_needs_rerun_part_is_re_reviewed,
               test_phase4_progress_last_entry_wins,
               test_phase4_style_checkpoint_skip_and_staleness,
               test_phase4_skip_style_env,
               test_reval_script_clears_progress_keys,
               test_logic_detail_count_conservation_with_aggregator,
               test_logic_detail_pad_and_truncate,
               test_logic_detail_failure_degrades_to_v5,
               test_logic_detail_hallucinated_anchor_cleared,
               test_build_revision_brief_includes_logic_detail,
               test_logic_detail_kill_switch_and_part1,
               test_logic_detail_prompt_file_and_fallback_synced,
               test_s2_partial_applied_falls_through_to_rewrite,
               test_s2_spotfix_pass_branch_unchanged,
               test_apply_targeted_edits_pure_function_guards,
               test_edit_trigger_requires_unique_anchor_per_p0,
               test_s3_edit_applied_saved_and_brief,
               test_s3_no_edit_when_anchor_not_unique_falls_to_rewrite,
               test_s3_edit_degraded_rolls_back_then_rewrite,
               test_s3_targeted_edit_prompt_file_and_fallback_synced,
               test_scan_departed_reappearance_threshold,
               test_departed_finding_never_enters_g4,
               test_final_audit_departed_rereview_budget,
               test_final_audit_departed_coexists_with_canonical_absent,
               test_scan_departed_dirty_data_fail_open):
        fn()
        print(f'PASS {fn.__name__}')
    logger.info('\nALL PASS')
