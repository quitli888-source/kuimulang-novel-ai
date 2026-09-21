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
import os
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
               test_json_429_exhaustion_attaches_raw_text):
        fn()
        print(f'PASS {fn.__name__}')
    logger.info('\nALL PASS')
