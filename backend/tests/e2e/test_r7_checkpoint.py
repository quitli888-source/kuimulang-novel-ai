"""
R7-T5: chunk checkpoint 真实验证。
- 真实调 PartWriterAgent 写 Part 1（多个 chunk）
- 检查每次 chunk 后回调是否触发
- 检查每个 chunk 后 state.parts[str(1)] 都有累计内容
"""
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
# P0-41: STEP_API_KEY 由 conftest.py fixture 提供；缺失则 pytest.skip
STEP_API_KEY = os.environ.get('STEP_API_KEY', '')
os.environ['ENABLE_VECTOR_RAG'] = '0'
import core.llm_client as llm_client_mod
_original_call_llm = llm_client_mod.call_llm

def _safe_call_llm(system_prompt, user_prompt, temperature=0.7, max_tokens=4000, agent='default', stream=False, stream_callback=None, *args, **kwargs):
    """对 stream 路径跳过 choices=[] 的 chunk（stepfun 偶发）；非 stream 走原函数。

    R4-P1-x: 签名加 *args/**kwargs 透传 —— 生产 call_llm 带 work_id 关键字参数，
    PartWriterAgent 以 work_id=... 调用，此前窄签名直接 TypeError，异常被
    part_writer 的 except 吞掉计为空内容，测试既不报错也不打真 API。
    """
    if not stream:
        return _original_call_llm(system_prompt, user_prompt, temperature, max_tokens, agent, stream, stream_callback, *args, **kwargs)
    import time as _t
    from core.config import get_llm_config_for_agent
    from openai import OpenAI
    cfg = get_llm_config_for_agent(agent)
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    temp = llm_client_mod._safe_temperature(temperature, cfg.model)
    messages = [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}]
    call_start = _t.time()
    response = client.chat.completions.create(model=cfg.model, messages=messages, temperature=temp, max_tokens=max_tokens, stream=True)
    content = ''
    last_chunk = None
    for chunk in response:
        last_chunk = chunk
        if not chunk.choices:
            continue
        if chunk.choices[0].delta.content:
            chunk_text = chunk.choices[0].delta.content
            content += chunk_text
            if stream_callback:
                stream_callback(chunk_text)
    content = content.strip()
    content = llm_client_mod._strip_think_tags(content)
    call_duration = (_t.time() - call_start) * 1000
    from core.cost_tracker import get_tracker as _gt, estimate_tokens_from_text as _ett
    tracker = _gt()
    usage = last_chunk.usage if last_chunk is not None and getattr(last_chunk, 'usage', None) is not None else None
    if usage is not None:
        tracker.record(model=cfg.model, agent=agent, is_json=False, prompt_tokens=usage.prompt_tokens or 0, completion_tokens=usage.completion_tokens or 0, total_tokens=usage.total_tokens or 0, duration_ms=call_duration)
    else:
        pt = _ett(system_prompt + '\n' + user_prompt)
        ct = _ett(content)
        tracker.record(model=cfg.model, agent=agent, is_json=False, prompt_tokens=pt, completion_tokens=ct, total_tokens=pt + ct, duration_ms=call_duration, estimated=True)
    return content


# R3-S2: 模块级 call_llm 替换收进 install/restore 函数对（pytest 收集即 import，
# 模块级赋值会污染整个 session 的全局 call_llm）；由 fixture 显式安装、yield 后恢复。


def _install_safe_call_llm():
    llm_client_mod.call_llm = _safe_call_llm
    sys.modules['core.llm_client'].call_llm = _safe_call_llm


def _restore_call_llm():
    llm_client_mod.call_llm = _original_call_llm
    sys.modules['core.llm_client'].call_llm = _original_call_llm


@pytest.fixture
def _patched_call_llm():
    _install_safe_call_llm()
    yield _safe_call_llm
    _restore_call_llm()


@pytest.mark.live
def test_r7_checkpoint_live(live_llm_key, _patched_call_llm):
    """R7-T5 pytest 化（R3-S2）: Chunk Checkpoint 真实验证（不能 mock）。

    需真实 API key（live_llm_key 门禁）；未传 --live 时默认 skip。薄包装：
    main() 逻辑一字未动，退出码 0 转为 pytest 断言。
    """
    assert main() == 0


from core.agents.part_writer_agent import PartWriterAgent
from core.sliding_window import SlidingWindow
from core.logger import get_logger
logger = get_logger('test_r7_checkpoint')

class MockState:

    def __init__(self, characters, world_setting, part_outline, foreshadowing, window):
        self.characters = characters
        self.world_setting = world_setting
        self.part_outline = part_outline
        self.foreshadowing = foreshadowing
        self.window = window
        self.parts = {}
        self.part_summaries = {}
        self.current_plot_state = ''
        self.character_state_track = {}

    def get_part_context(self, part_num):
        return self.window.build(part_num, characters=self.characters, world_setting=self.world_setting, outline=self.part_outline[part_num - 1] if part_num <= len(self.part_outline) else None)

def main():
    logger.info('=' * 70)
    logger.info('【R7-T5】Chunk Checkpoint 真实验证')
    logger.info('=' * 70)
    characters = [{'name': '林枫', 'role': '主角', 'identity': '前刑警', 'core_trait': '执拗、敏锐', 'motivation': '追查三年前的搭档失踪案', 'secret': '他收到过搭档的匿名警告'}]
    world_setting = '江南雨城，警署与地下势力相互渗透。三年前一场雨夜搭档失踪案悬而未决。'
    part_outline = [{'phase': '开端', 'title': '雨夜重逢', 'core_event': '林枫推开尘封的书房门', 'emotion_target': '悬疑与不安', 'key_dialogue': '你终于来了', 'end_hook': '墙上红字指向下一个地点', 'pacing': '慢起', 'causality': '承前：搭档失踪悬案', 'word_count': 1500}]
    foreshadowing = []
    window = SlidingWindow(window_size=3)
    state = MockState(characters, world_setting, part_outline, foreshadowing, window)
    writer = PartWriterAgent()
    logger.info('\n--- 单元验证: checkpoint_callback 接口 ---')
    captured = []
    writer.set_checkpoint_callback(lambda p, c, t: captured.append((p, c, len(t))))
    logger.info(f'  callback set: {writer.checkpoint_callback is not None}')
    assert writer.checkpoint_callback is not None
    writer.checkpoint_callback(99, 0, 'hello world')
    logger.info(f'  captured after manual call: {captured}')
    assert captured == [(99, 0, 11)], 'checkpoint callback 签名 (part_num, chunk_idx, accumulated_text) 必须正确'
    captured.clear()
    writer.set_checkpoint_callback(lambda p, c, t: captured.append((p, c, len(t))))
    logger.info('\n--- 真实调 writer.execute(state, part_num=1) ---')
    t0 = time.time()
    result = writer.execute(state, 1)
    elapsed = time.time() - t0
    logger.info(f'  耗时 {elapsed:.1f}s')
    logger.info(f"  result success: {result.get('success')}")
    logger.info(f"  result word_count: {result.get('word_count')}")
    logger.info(f"  result chunk_count: {result.get('chunk_count')}")
    logger.info(f"  content length: {len(result.get('content', ''))}")
    logger.info('\n--- 验证 checkpoint 回调 ---')
    logger.info(f'  captured (part_num, chunk_idx, len(text)) calls:')
    for entry in captured:
        logger.info(f'    {entry}')
    logger.info('\n--- 验证 chunk 累计（长度单调递增） ---')
    lengths = [c[2] for c in captured]
    monotonic = all((lengths[i] <= lengths[i + 1] for i in range(len(lengths) - 1)))
    logger.info(f'  lengths = {lengths}')
    logger.info(f'  单调递增: {monotonic}')
    logger.info('\n--- R7-T5 Assertions ---')
    results = []
    r1 = result.get('success') is True
    logger.info(f"  [{('OK' if r1 else 'FAIL')}] writer.execute 成功: {r1}")
    results.append({'name': 'writer_success', 'ok': r1})
    r2 = result.get('word_count', 0) > 0
    logger.info(f"  [{('OK' if r2 else 'FAIL')}] word_count > 0: {result.get('word_count')}")
    results.append({'name': 'word_count_positive', 'ok': r2, 'actual': result.get('word_count')})
    r3 = len(captured) >= 1
    logger.info(f"  [{('OK' if r3 else 'FAIL')}] 至少触发 1 次 checkpoint 回调: {len(captured)}")
    results.append({'name': 'checkpoint_fired', 'ok': r3, 'actual': len(captured)})
    r4 = monotonic
    logger.info(f"  [{('OK' if r4 else 'FAIL')}] 累计长度单调递增: {monotonic}")
    results.append({'name': 'monotonic_accumulation', 'ok': r4})
    r5 = all((c[0] == 1 for c in captured))
    logger.info(f"  [{('OK' if r5 else 'FAIL')}] 所有回调 part_num == 1: {r5}")
    results.append({'name': 'part_num_consistent', 'ok': r5})
    state.parts['1'] = result.get('content', '')
    summary_text = result.get('content', '')[:200] + '...'
    state.part_summaries['1'] = summary_text
    logger.info(f"\n--- state.parts[str(1)] 已写入: {len(state.parts['1'])} 字 ---")
    all_pass = all((x['ok'] for x in results))
    logger.info('\n' + '=' * 70)
    if all_pass:
        logger.info(f'【R7-T5 PASS】Chunk Checkpoint 真实验证全部通过（{len(captured)} 次回调）')
    else:
        logger.info(f"【R7-T5 FAIL】 {sum((1 for x in results if not x['ok']))} / {len(results)} 项断言失败")
    logger.info('=' * 70)
    return 0 if all_pass else 1
if __name__ == '__main__':
    _install_safe_call_llm()
    sys.exit(main())