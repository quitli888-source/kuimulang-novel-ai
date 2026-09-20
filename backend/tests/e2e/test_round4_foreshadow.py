"""
Round 4 伏笔/预检告警回归测试（R4-6 伏笔回收确定性复检 + flags 评审可见）
—— 全部离线断言，不调 LLM。

覆盖（02_review.md §S6 验收标准）：
  伏笔回收复检（零 LLM）：命中 / 未命中 / 空 foreshadowing / content 过短 /
        多伏笔部分命中 / reveal_part 不匹配 / 脏数据 / part_text 为空；
  关键词截取长度与命中规则单测固定（content 前 10 字子串）；
  flags 落 work.json（Phase3Runner 集成，mock writer）；
  flags 进 review state_mock（_build_review_state_mock）；
  consistency user_prompt 在 mock 下含"⚠ 系统预检警告"段（有 flags 时、
        无 flags 时无段、>10 条截断）；
  verify 打印汇总行（summarize_consistency_flags 纯函数）。

既支持 pytest 也支持 `python backend/tests/e2e/test_round4_foreshadow.py` 直接跑。
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.logger import get_logger

logger = get_logger('test_round4_foreshadow')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from services.writing_phase_runners import (  # noqa: E402
    FORESHADOW_KEYWORD_LEN, FORESHADOW_MIN_CONTENT_LEN, check_foreshadow_reveal,
)


def _load_verify_module():
    """从门禁脚本 import summarize_consistency_flags（屏蔽 RUN_DIR.mkdir 副作用）。"""
    verify_path = _HERE / 'verify_step5_longform.py'
    spec = importlib.util.spec_from_file_location('verify_step5_under_test', verify_path)
    mod = importlib.util.module_from_spec(spec)
    import pathlib
    orig_mkdir = pathlib.Path.mkdir
    pathlib.Path.mkdir = lambda self, *a, **k: None
    try:
        spec.loader.exec_module(mod)
    finally:
        pathlib.Path.mkdir = orig_mkdir
    return mod


verify_mod = _load_verify_module()
summarize_consistency_flags = verify_mod.summarize_consistency_flags


# ---------------- 伏笔回收复检纯函数 ----------------

def test_check_foreshadow_reveal_rules():
    """R4-6: 命中/未命中/空/过短/部分命中/不匹配/脏数据/空正文。"""
    assert FORESHADOW_KEYWORD_LEN == 10
    assert FORESHADOW_MIN_CONTENT_LEN == 4
    fs = [
        {'id': 'F1', 'content': '古井深处沉睡的巡界使印记', 'reveal_part': 2},
        {'id': 'F2', 'content': '母亲留下的半块残玉', 'reveal_part': 2},
        {'id': 'F3', 'content': '殷刹的巡界使身份', 'reveal_part': 3},
    ]
    # 部分命中：F1 关键词出现、F2 未出现 → 只报 F2
    text = '林尘坠入古井，古井深处沉睡的巡界使印记骤然亮起。'
    assert check_foreshadow_reveal(fs, 2, text) == ['F2']
    # 全部命中
    text_all = '古井深处沉睡的巡界使印记亮起，母亲留下的半块残玉也发出了微光。'
    assert check_foreshadow_reveal(fs, 2, text_all) == []
    # reveal_part 不匹配 → 不复检
    assert check_foreshadow_reveal(fs, 5, '任意正文') == []
    # 空 foreshadowing / 空正文
    assert check_foreshadow_reveal([], 2, text) == []
    assert check_foreshadow_reveal(None, 2, text) == []
    assert check_foreshadow_reveal(fs, 2, '') == []
    # content 过短（<4 字）→ 跳过不复检（无有效关键词，不误报）
    short_fs = [{'id': 'S1', 'content': '残玉', 'reveal_part': 2},
                {'id': 'S2', 'content': '', 'reveal_part': 2},
                {'id': 'S3', 'reveal_part': 2}]
    assert check_foreshadow_reveal(short_fs, 2, '完全无关的正文') == []
    # 脏数据（非 dict / reveal_part 类型异常）不炸
    dirty = ['not a dict', None, {'id': 'D1', 'content': '某个足够长的伏笔内容', 'reveal_part': '2'}]
    assert check_foreshadow_reveal(dirty, 2, '无关正文') == []
    # 关键词取 content 前 10 字（短于 10 字取全文）
    long_fs = [{'id': 'L1', 'content': '一二三四五六七八九十十一十二十三十四', 'reveal_part': 2}]
    assert check_foreshadow_reveal(long_fs, 2, '正文里出现一二三四五六七八九十') == []
    assert check_foreshadow_reveal(long_fs, 2, '正文里只出现后十个字十一十二十三十四') == ['L1']
    logger.info('[test_foreshadow_rules] PASS: 复检规则八类场景全部符合预期')


# ---------------- flags 落 work.json（Phase3Runner 集成） ----------------

def _make_work(tmp_path: Path, work_id: str):
    work = {
        'id': work_id, 'title': 'smoke r4 fs', 'inspiration': '少年林尘被预言为天煞孤星。',
        'phase': 'phase2',
        'core_elements': {'protagonist': {'identity': '少年'}},
        'market_positioning': {},
        'world_setting': '玄幻世界',
        'characters': [{'name': '林尘', 'role': '主角', 'identity': '少年', 'core_trait': '坚忍',
                        'motivation': '寻道', 'secret': '血脉', 'arc': '成长'}],
        'part_outline': [{'title': 'Part 1', 'phase': 'p', 'word_count': 5000,
                          'core_event': '事件', 'emotion_target': '紧张', 'key_dialogue': '对话',
                          'end_hook': '钩子', 'causality': '因果', 'pacing': '快'}],
        'foreshadowing': [
            {'id': 'FS_A', 'content': '古井深处沉睡的巡界使印记', 'plant_part': 1, 'reveal_part': 1},
            {'id': 'FS_B', 'content': '母亲留下的半块残玉', 'plant_part': 1, 'reveal_part': 2},
        ],
        'parts': {}, 'part_summaries': {},
    }
    (tmp_path / f'{work_id}.json').write_text(json.dumps(work, ensure_ascii=False), encoding='utf-8')


def _patch_dirs(tmp_path: Path):
    import core.config as config_mod
    import api.works as works_api
    return [patch.object(config_mod, 'WORKS_DIR', tmp_path),
            patch.object(works_api, 'WORKS_DIR', tmp_path)]


def test_phase3_foreshadow_flags_written_to_work_json(tmp_path):
    """R4-6 验收: 未回收伏笔进 s.data['consistency_flags'] 并落 work.json。"""
    from services.writing_service import WritingService
    from services.writing_phase_runners import Phase3Runner
    from api.sse import SSEEmitter
    import core.config as config_mod

    work_id = 'smoke_r4_fs'
    _make_work(tmp_path, work_id)
    p0, p1 = _patch_dirs(tmp_path)
    with p0, p1:
        svc = WritingService(work_id, SSEEmitter(), resume=False)
        svc.cfg.confirm_mode = False
        tmpl = next((t for t in config_mod.DEFAULT_TEMPLATES if t.name == '自定义'),
                    config_mod.WritingTemplate('自定义', 0, 0, 0, 0))
        svc.cfg.apply_template(tmpl, custom_target_words=5000, custom_part_count=1)
        config_mod.reload_config()
        # Part 1 只回收 FS_A（关键词在正文），FS_B 的 reveal_part=2 不在本 Part
        part_text = '林尘坠入古井，井底巡界使印记骤然亮起。' * 60
        with patch('core.agents.part_writer_agent.PartWriterAgent.execute',
                   lambda self, state, part_num, **kw: {'success': True, 'content': part_text}), \
             patch('core.sliding_window.SlidingWindow.maybe_generate_rolling_summary',
                   lambda self, pn, **kw: {'generated': False}), \
             patch('core.sliding_window.SlidingWindow.maybe_generate_milestone',
                   lambda self, pn, **kw: {'generated': False}):
            asyncio.run(Phase3Runner(svc).run(start_from=1))
    after = json.loads((tmp_path / f'{work_id}.json').read_text(encoding='utf-8'))
    flags = after.get('consistency_flags') or []
    assert any(f.get('type') == 'foreshadow_unrevealed' and f.get('foreshadow_id') == 'FS_A'
               and f.get('part') == 1 for f in flags), f'FS_A 未回收应进 flags: {flags}'
    assert not any(f.get('foreshadow_id') == 'FS_B' for f in flags), 'FS_B 不在本 Part 回收'
    logger.info('[test_phase3_flags] PASS: 未回收伏笔进 consistency_flags 并落 work.json')


def test_review_state_mock_carries_consistency_flags(tmp_path):
    """R4-6 验收: _build_review_state_mock 挂 consistency_flags（评审可见）。"""
    from services.writing_service import WritingService
    from api.sse import SSEEmitter
    work_id = 'smoke_r4_mock_flags'
    _make_work(tmp_path, work_id)
    p0, p1 = _patch_dirs(tmp_path)
    with p0, p1:
        svc = WritingService(work_id, SSEEmitter(), resume=False)
        svc.data['consistency_flags'] = [
            {'part': 3, 'type': 'foreshadow_unrevealed', 'foreshadow_id': 'FS_X'},
            {'part': 5, 'character': '林忠', 'count': 2, 'departed_record': 'Part2 死亡'},
        ]
        mock = svc._build_review_state_mock()
    assert mock.consistency_flags == svc.data['consistency_flags']
    # 无 flags 时为空列表（不炸）
    with p0, p1:
        svc2 = WritingService(work_id, SSEEmitter(), resume=False)
        assert svc2._build_review_state_mock().consistency_flags == []
    logger.info('[test_mock_flags] PASS: state_mock 携带 consistency_flags')


def test_consistency_prompt_shows_flags_block():
    """R4-6 验收: consistency user_prompt 尾部在 mock 下含系统预检警告段（≤10 行）。"""
    from core.agents.consistency_review_agent import ConsistencyReviewAgent
    chars = [{'name': '林尘', 'role': '主角', 'identity': '少年', 'core_trait': '坚忍',
              'motivation': '寻道', 'secret': '血脉'}]
    flags = ([{'part': i, 'type': 'foreshadow_unrevealed', 'foreshadow_id': f'FS_{i}'}
              for i in range(1, 13)]   # 12 条伏笔未回收
             + [{'part': 9, 'character': '林忠', 'count': 3, 'departed_record': 'Part2 死亡'}])
    state = SimpleNamespace(
        work_id='r4', characters=chars, part_summaries={'1': '摘要'},
        parts={'1': '林尘踏入禁地。' * 50}, final_draft={'1': 'x'},
        name_registry={}, character_state_track={}, consistency_flags=flags)
    captured = {}

    def fake_json(**kwargs):
        captured['user_prompt'] = kwargs.get('user_prompt', '')
        return {'pass': True, 'overall_score': 8, 'issues': [],
                'character_states': {}, 'verdict': 'ok'}

    with patch('core.agents.consistency_review_agent.call_llm_json', side_effect=fake_json):
        ConsistencyReviewAgent().execute(state, 2, '林渊继续前行。' * 30)
    up = captured['user_prompt']
    assert '⚠ 系统预检警告' in up, '有 flags 时必须展示警告段'
    assert 'FS_1' in up and 'FS_7' in up
    assert '勿仅因此判 P0' in up
    # 超过 10 条只展示前 10 行（第 11 条起的伏笔与退场 flag 均不展示）
    warn_idx = up.index('⚠ 系统预检警告')
    warn_lines = [l for l in up[warn_idx:].splitlines() if l.strip().startswith('- Part')]
    assert len(warn_lines) == 10, f'警告段应 ≤10 行: {len(warn_lines)}'
    assert 'FS_10' in up[warn_idx:]
    assert 'FS_11' not in up[warn_idx:], '第 11 条起不得展示'
    assert '已退场角色' not in up[warn_idx:], '超出 10 条上限的退场 flag 不得展示'
    # 无 flags 时不出现警告段
    state_no_flags = SimpleNamespace(**{**vars(state), 'consistency_flags': []})
    with patch('core.agents.consistency_review_agent.call_llm_json', side_effect=fake_json):
        ConsistencyReviewAgent().execute(state_no_flags, 2, '林渊继续前行。' * 30)
    assert '系统预检警告' not in captured['user_prompt']
    logger.info('[test_prompt_flags] PASS: 警告段展示/截断/无 flags 不展示')


def test_summarize_consistency_flags_line():
    """R4-6 验收: verify 汇总行（纯函数）—— 空/混合/脏数据。"""
    assert summarize_consistency_flags({}) == '[观测] consistency_flags: 0 条'
    assert summarize_consistency_flags({'consistency_flags': None}) == '[观测] consistency_flags: 0 条'
    data = {'consistency_flags': [
        {'part': 3, 'type': 'foreshadow_unrevealed', 'foreshadow_id': 'FS_X'},
        {'part': 4, 'type': 'foreshadow_unrevealed', 'foreshadow_id': 'FS_Y'},
        {'part': 5, 'character': '林忠', 'count': 2},
        'not a dict',
    ]}
    line = summarize_consistency_flags(data)
    assert '共 3 条' in line and 'foreshadow_unrevealed' in line
    assert 'departed_reappearance' in line and '只记录不加门禁' in line
    logger.info('[test_flags_summary] PASS: 汇总行空/混合/脏数据')


if __name__ == '__main__':
    logger.info('=' * 60)
    logger.info('test_round4_foreshadow.py —— Round 4 伏笔回收/预检告警回归（mock LLM）')
    logger.info('=' * 60)
    import tempfile
    for fn in (test_check_foreshadow_reveal_rules,
               test_summarize_consistency_flags_line,
               test_consistency_prompt_shows_flags_block):
        fn()
        print(f'PASS {fn.__name__}')
    for fn in (test_phase3_foreshadow_flags_written_to_work_json,
               test_review_state_mock_carries_consistency_flags):
        with tempfile.TemporaryDirectory() as td:
            fn(Path(td))
        print(f'PASS {fn.__name__}')
    logger.info('\nALL PASS')
