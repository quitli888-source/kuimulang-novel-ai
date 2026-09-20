"""
Round 3 · S2: PartWriterAgent chunk 路径 mock-LLM 集成测试（离线，不调 LLM）。

背景（round2/04_feedback.md P0-1/P0-2）：
  - P0-1: part_writer_agent.py:195 `expected_min_len=chunk_target // 2` 引用不存在
    的变量 → NameError 被 except 吞成空内容 → Phase 3 全线瘫痪（2 Part × 3 attempt
    × 6 chunk 全废、108 次 INFO 噪音）。
  - P0-2: 离线 pytest 对该路径零覆盖 —— test_round1_integration 整体 mock
    PartWriterAgent.execute，test_round2_fixes 止于 llm_client 层，"50 全绿"与
    "线上 0 字"并存。本文件在 LLM 边界（part_writer 模块级 call_llm）注入脚本化
    假 LLM，真实跑 execute / Phase3Runner 全链路（retry/拼接/去重/早退/facts
    全走真代码），仿 openai-agents ScriptedModel + assert_complete 模式。

期望值全部硬编码（模块常量已按 20 Part/100k 冻结）：outline.word_count=5000 →
target=5000 → 首片段 chunk_target=3500 → expected_min_len=1750。不用同源公式
推导期望值 —— 公式推导是同义反复，没有牙齿。

既支持 pytest 也支持 `python backend/tests/e2e/test_part_writer_chunked.py` 直接跑。
"""
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

from core.logger import get_logger

logger = get_logger('test_part_writer_chunked')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


class ScriptedCallLLM:
    """脚本化假 call_llm（LLM 边界注入，仿 openai-agents ScriptedModel）。

    script: [返回文本 或 ('raise', Exc实例)]，按调用序消费并记录实参；
    assert_complete(): 脚本未消耗完即失败 —— 防"工作流提前退出"漂移
    （openai-agents UnconsumedModelSteps 同款思想）。
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if self.script else None
        if isinstance(item, tuple) and item[0] == 'raise':
            raise item[1]
        return item or '片段正文。' * 500

    def assert_complete(self):
        assert not self.script, f'脚本未消耗完（工作流提前退出？）: {len(self.script)} 项剩余'


def _make_work(tmp_path: Path, work_id: str, parts: int = 1):
    """最小 work dict（复制自 test_round1_integration.py，part_outline 恰好 parts 条）。

    测试间 import 耦合比 30 行重复更贵，故复制而非跨文件 import。
    """
    work = {
        'id': work_id, 'title': 'smoke r3 chunked', 'inspiration': '少年林尘被预言为天煞孤星。',
        'phase': 'phase2',
        'core_elements': {'protagonist': {'identity': '少年'}},
        'market_positioning': {},
        'world_setting': '玄幻世界',
        'characters': [
            {'name': '林尘', 'role': '主角', 'identity': '少年', 'core_trait': '坚忍',
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


def _freeze_part_writer_constants():
    """按 20 Part / 100k 配置冻结 part_writer_agent 的 import 时模块常量
    （test_round2_fixes.py:286-289 同款处理）—— 否则 outline.word_count=5000
    会被本机 .env/默认模板的 floor 抬高，后续期望值不可算。"""
    import core.agents.part_writer_agent as pw
    return [patch.object(pw, 'TARGET_WORD_COUNT', 100000),
            patch.object(pw, 'PART_COUNT', 20),
            patch.object(pw, 'PART_WORD_MIN', 2500),
            patch.object(pw, 'PART_WORD_MAX', 10000)]


def _run_phase3_part1(tmp_path, work_id, script):
    """Phase3Runner 真实跑 Part 1（PartWriterAgent.execute 全链路真代码）。

    额外 patch 两处否则会打真 API 的东西：part_writer 模块的 call_llm（主注入点）
    与 call_llm_json（facts 抽取路径）；sliding_window 的 rolling/milestone 生成
    （与 test_round1_integration.py:91-93 同型）。
    返回 (fake_llm, json_calls, work_data)。
    """
    from services.writing_service import WritingService
    from services.writing_phase_runners import Phase3Runner
    from api.sse import SSEEmitter
    import core.agents.part_writer_agent as pw
    _make_work(tmp_path, work_id)
    fake = ScriptedCallLLM(script)
    json_calls = []

    def _fake_call_llm_json(**kwargs):
        json_calls.append(kwargs)
        return {'facts': [{'category': 'object', 'text': '古井', 'quote': '古井'}]}

    p0, p1 = _patch_dirs(tmp_path)
    consts = _freeze_part_writer_constants()
    patches = [p0, p1, *consts,
               patch.object(pw, 'call_llm', fake),
               patch.object(pw, 'call_llm_json', _fake_call_llm_json),
               patch('core.llm_client.call_llm_json', _fake_call_llm_json),
               patch('core.sliding_window.SlidingWindow.maybe_generate_rolling_summary',
                     lambda self, pn, **kw: {'generated': False}),
               patch('core.sliding_window.SlidingWindow.maybe_generate_milestone',
                     lambda self, pn, **kw: {'generated': False})]
    with contextlib.ExitStack() as stack:
        for cm in patches:
            stack.enter_context(cm)
        svc = WritingService(work_id, SSEEmitter(), resume=False)
        svc.cfg.confirm_mode = False
        svc._request_confirm = lambda *a, **k: asyncio.sleep(0)
        _apply_custom_template(svc, 1)
        asyncio.run(Phase3Runner(svc).run(start_from=1))
    data = json.loads((tmp_path / f'{work_id}.json').read_text(encoding='utf-8'))
    return fake, json_calls, data


# ---------------- 5 项核心断言（每项都直接对着 Round 2 事故有牙齿） ----------------

def test_chunk_expected_min_len_hardcoded_1750(tmp_path):
    """P0-1 根因断言: 首片段 expected_min_len == 1750、次片段 == 850（硬编码）。

    target=5000 → 首片段 remaining=5000 → chunk_target=3500 → 1750；
    次片段 remaining=1500 → chunk_target=1700 → 850。S1 修复前 chunk_target
    未定义（NameError 被吞/直接抛出），本断言必 FAIL。
    """
    fake, _, data = _run_phase3_part1(tmp_path, 'smoke_r3_c1', ['甲' * 3500, '乙' * 3500])
    assert len(fake.calls) >= 2, f'应发生 2 次片段调用: {len(fake.calls)}'
    assert fake.calls[0]['expected_min_len'] == 1750, fake.calls[0]
    assert fake.calls[1]['expected_min_len'] == 850, fake.calls[1]
    assert len(data['parts']['1']) == 7000, len(data['parts']['1'])
    fake.assert_complete()


def test_chunk_concatenation_and_prev_tail_dedup(tmp_path):
    """片段拼接 + prev_tail 去重: chunk2 以 chunk1 末 800 字开头 → 不重复计入。"""
    chunk1 = '甲' * 3500 + '。'
    chunk2 = chunk1[-800:] + '乙' * 1800 + '。'
    fake, _, data = _run_phase3_part1(tmp_path, 'smoke_r3_c2', [chunk1, chunk2])
    text = data['parts']['1']
    assert text == chunk1 + '乙' * 1800 + '。', f'拼接/去重异常: len={len(text)}'
    assert text.count('甲') == 3500, 'chunk1 末尾被重复计入（prev_tail 去重失效）'
    fake.assert_complete()


def test_early_exit_threshold_break_and_continue(tmp_path):
    """R2-4 防线 4 联动: 累计 4802 ≥ 4250 且片段 <2100 且以。结尾 → break（2 次调用）；
    对照场景 chunk2 不以句号结尾 → 不 break，chunk3 被调用（expected_min_len==199）。"""
    # 场景 A: 自然收尾早退
    fake, _, data = _run_phase3_part1(tmp_path, 'smoke_r3_c3a', ['甲' * 3000 + '。', '乙' * 1800 + '。'])
    assert len(fake.calls) == 2, f'应在第 2 片段后早退: {len(fake.calls)}'
    assert len(data['parts']['1']) == 3001 + 1801, len(data['parts']['1'])
    fake.assert_complete()
    # 场景 B: 无句号结尾 → 不早退，chunk3 被调用（尾部 remaining=199 → 399//2）
    fake_b, _, data_b = _run_phase3_part1(
        tmp_path, 'smoke_r3_c3b', ['甲' * 3000 + '。', '乙' * 1800, '丙' * 300 + '。'])
    assert len(fake_b.calls) == 3, f'无句号结尾不应早退: {len(fake_b.calls)}'
    assert fake_b.calls[2]['expected_min_len'] == 199, fake_b.calls[2]
    assert len(data_b['parts']['1']) == 3001 + 1800 + 301
    fake_b.assert_complete()


def test_facts_extraction_chain_runs(tmp_path):
    """facts 抽取链路: call_llm_json 被调 ≥1 次且 established_facts 非空落盘。"""
    _, json_calls, data = _run_phase3_part1(tmp_path, 'smoke_r3_c4', ['甲' * 3500, '甲' * 3500])
    assert len(json_calls) >= 1, 'facts 抽取未发生（call_llm_json 零调用）'
    ef = data.get('established_facts') or {}
    assert ef.get('facts'), f'established_facts 未落盘: {ef}'


def test_fail_fast_contract_programming_error(tmp_path):
    """S3 配套 fail-fast 契约: 第 1 次调用即 raise NameError → 只调 1 次、
    execute 返回 success=False 且 error 含 NameError 信息（不得出现"3 次重试后
    伪装空内容"的路径 —— Round 2 的 108 次 INFO 噪音模式）。

    Phase3Runner 会按产品语义对失败 Part 重试 3 次 attempt（asyncio.sleep 阶梯），
    无法观测"0 次无谓重试"，故本项直接调 PartWriterAgent.execute 验证契约。
    S3 未实施时本测试 FAIL（calls==2、success=True），实施后 PASS。
    """
    from core.memory_manager import get_all_memory
    from services.writing_service import WritingService, TempStoryState
    from api.sse import SSEEmitter
    from core.agents.part_writer_agent import PartWriterAgent
    work_id = 'smoke_r3_c5'
    _make_work(tmp_path, work_id)
    fake = ScriptedCallLLM([('raise', NameError('chunk_target is not defined'))])
    p0, p1 = _patch_dirs(tmp_path)
    consts = _freeze_part_writer_constants()
    patches = [p0, p1, *consts,
               patch('core.agents.part_writer_agent.call_llm', fake),
               patch('core.agents.part_writer_agent.call_llm_json', lambda **kw: {'facts': []})]
    with contextlib.ExitStack() as stack:
        for cm in patches:
            stack.enter_context(cm)
        svc = WritingService(work_id, SSEEmitter(), resume=False)
        svc.cfg.confirm_mode = False
        _apply_custom_template(svc, 1)
        state = TempStoryState(svc.data, get_all_memory())
        result = PartWriterAgent().execute(state, 1)
    assert len(fake.calls) == 1, f'编程错误不得重试（观察到 {len(fake.calls)} 次调用）'
    assert result['success'] is False, result
    assert 'chunk_target' in result['error'], result
    fake.assert_complete()


if __name__ == '__main__':
    import tempfile

    for fn in (test_chunk_expected_min_len_hardcoded_1750,
               test_chunk_concatenation_and_prev_tail_dedup,
               test_early_exit_threshold_break_and_continue,
               test_facts_extraction_chain_runs,
               test_fail_fast_contract_programming_error):
        with tempfile.TemporaryDirectory() as td:
            fn(Path(td))
        print(f'PASS {fn.__name__}')
    print('all part_writer_chunked tests passed')
