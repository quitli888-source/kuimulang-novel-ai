"""
Round 5 补充: consistency prev_tail int/str 键修复回归测试
—— 全部离线断言，mock LLM，不发起真实调用。

缺陷（已核实）：
  core/agents/consistency_review_agent.py「前文结尾」段此前用 int 键查询
  final_draft（`part_num - 1 in final_draft`），而 work.json 的 JSON 对象键必为
  字符串（"1"/"2"...，经 writing_service mock state 原样传入），int 查 str dict
  恒 False —— "前一部分结尾"（连续性最关键上下文）从未注入一致性评审 prompt。

覆盖：
  1. part_num=3 + final_draft={"1":..,"2":..} → 评审 prompt 的
     「## 前一部分（Part 2）结尾」段非空且逐字等于 Part 2 尾部 800 字
     （>800 字截断、不含 Part 2 开头、不含 Part 1 内容）
  2. 上一 Part 不足 800 字 → 全文注入（不截断）
  3. part_num=1 → 提前返回不炸、不调 LLM
  4. 防御性：值非字符串 / 缺键 / final_draft=None → 不炸、尾段为空

既支持 pytest 也支持 `python backend/tests/e2e/test_consistency_prev_tail.py` 直接跑。
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.logger import get_logger  # noqa: E402
from core.agents.consistency_review_agent import ConsistencyReviewAgent  # noqa: E402

logger = get_logger('test_consistency_prev_tail')

_CHARS = [{'name': '林尘', 'role': '主角', 'identity': '少年', 'core_trait': '坚忍',
           'motivation': '寻道', 'secret': '血脉'}]


def _make_state(final_draft, part_summaries=None):
    """构造 consistency agent 可用的最小 state（与 test_round5_watchdogs 同规格）。"""
    return SimpleNamespace(
        work_id='r5_prev_tail_test', characters=_CHARS,
        part_summaries=part_summaries or {'1': '林尘踏入禁地。', '2': '林尘跌入古井。'},
        parts={'1': '林尘踏入禁地。' * 50, '2': '林尘跌入古井。' * 50},
        final_draft=final_draft,
        name_registry={}, character_state_track={}, consistency_flags=[])


def _run_capture(state, part_num, part_text):
    """mock LLM 执行 consistency agent，返回捕获的 user_prompt。"""
    captured = {}

    def fake_json(**kwargs):
        captured['user_prompt'] = kwargs.get('user_prompt', '')
        return {'pass': True, 'overall_score': 8, 'issues': [],
                'character_states': {}, 'verdict': 'ok'}

    with patch('core.agents.consistency_review_agent.call_llm_json',
               side_effect=fake_json):
        ConsistencyReviewAgent().execute(state, part_num, part_text)
    return captured['user_prompt']


def _tail_section(user_prompt, part_num):
    """截取 prompt 中「## 前一部分（Part N）结尾」段的正文（strip 后）。"""
    marker = f'## 前一部分（Part {part_num - 1}）结尾'
    next_marker = f'## Part {part_num} 正文'
    return user_prompt.split(marker)[1].split(next_marker)[0].strip()


# ---------------- 主用例: 前文结尾段注入且逐字等于 Part 2 尾部 ----------------

def test_prev_tail_injected_from_final_draft():
    """R5 补充验收: final_draft 字符串键查询生效 —— Part 3 评审注入 Part 2 结尾。

    Part 2 终稿 > 800 字：尾段须恰好是最后 800 字（截断生效），
    且不得混入 Part 2 开头或 Part 1 内容（取的是 Part 2 而非其他 Part）。
    """
    part2 = ''.join(f'第{i}句，剧情继续推进。' for i in range(1, 301)) + '【PART2独特结尾标记】'
    assert len(part2) > 800, '用例构造失误：Part 2 终稿应超过 800 字以验证截断'
    part1 = '【PART1开头独有内容】林尘踏入禁地，禁地深处传来低语。' * 30
    state = _make_state(final_draft={'1': part1, '2': part2})

    up = _run_capture(state, 3, '【PART3正文独有内容】林尘继续前行。' * 30)

    tail = _tail_section(up, 3)
    assert tail, 'prev_tail 为空：前一部分结尾段未注入评审 prompt（int/str 键 bug 复现）'
    assert tail == part2[-800:], '尾段须逐字等于 Part 2 终稿的最后 800 字'
    assert '【PART2独特结尾标记】' in tail, '连续性最关键的 Part 2 真实结尾缺失'
    assert '第1句，剧情继续推进。' not in up, '超过 800 字应截断，不得注入 Part 2 开头'
    assert '【PART1开头独有内容】' not in up, '应取 Part 2 结尾，不得混入 Part 1 内容'
    logger.info('[test_prev_tail_injected] PASS: Part2 结尾 800 字注入，截断/取 Part 正确')


def test_prev_tail_short_text_full_injection():
    """上一 Part 不足 800 字 → 全文注入（[-800:] 不误截）。"""
    part2 = '林尘跌入古井，井底青光萦绕不散。' * 5
    assert len(part2) < 800
    state = _make_state(final_draft={'1': '林尘踏入禁地。' * 50, '2': part2})

    up = _run_capture(state, 3, '林尘继续前行。' * 30)

    tail = _tail_section(up, 3)
    assert tail == part2, '不足 800 字应全文注入'
    logger.info('[test_prev_tail_short] PASS: 短前文全文注入')


# ---------------- part_num=1: 不炸、不调 LLM ----------------

def test_first_part_no_prev_and_no_llm():
    """part_num=1: 提前返回"第一部分无需检查"，不调用 LLM、不抛异常。"""
    state = _make_state(final_draft={'1': '林尘踏入禁地。' * 50})
    called = []

    def fake_json(**kwargs):
        called.append(kwargs)
        return {'pass': True, 'overall_score': 10, 'issues': [],
                'character_states': {}, 'verdict': 'x'}

    with patch('core.agents.consistency_review_agent.call_llm_json',
               side_effect=fake_json):
        result = ConsistencyReviewAgent().execute(state, 1, '林尘踏入禁地。' * 30)
    assert result['pass'] is True and result['issues'] == []
    assert not called, '第一部分不应调用 LLM（无前文可查）'
    logger.info('[test_first_part] PASS: part_num=1 不炸且零 LLM 调用')


# ---------------- 防御性: 脏数据不破坏主流程 ----------------

def test_dirty_final_draft_no_crash():
    """防御性验收: 值非字符串 / 缺键 / final_draft=None → 不抛异常、尾段为空。"""
    part3_text = '林尘继续前行。' * 30
    # 值非字符串（如旧 work JSON 脏数据 / 占位对象）
    up = _run_capture(_make_state(final_draft={'1': 'x', '2': 12345}), 3, part3_text)
    assert _tail_section(up, 3) == '', '非字符串值应跳过注入'
    # 缺上一 Part 键（Phase5 尚未产出 Part 2 终稿）
    up = _run_capture(_make_state(final_draft={'1': 'x'}), 3, part3_text)
    assert _tail_section(up, 3) == '', '缺键时应留空尾段而非报错'
    # final_draft=None
    up = _run_capture(_make_state(final_draft=None), 3, part3_text)
    assert _tail_section(up, 3) == '', 'final_draft=None 时应留空尾段而非报错'
    # 脏类型 list（str in list 不炸、取值被 except 兜底）
    up = _run_capture(_make_state(final_draft=['不是dict']), 3, part3_text)
    assert _tail_section(up, 3) == ''
    logger.info('[test_dirty_final_draft] PASS: 脏 final_draft 不破坏主流程')


if __name__ == '__main__':
    logger.info('=' * 60)
    logger.info('test_consistency_prev_tail.py —— Round 5 补充回归（mock LLM）')
    logger.info('=' * 60)
    for fn in (test_prev_tail_injected_from_final_draft,
               test_prev_tail_short_text_full_injection,
               test_first_part_no_prev_and_no_llm,
               test_dirty_final_draft_no_crash):
        fn()
        print(f'PASS {fn.__name__}')
    logger.info('\nALL PASS')
