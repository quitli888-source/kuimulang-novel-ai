"""
Round 1 修复回归测试（R1-A/B/D/E/H/J）—— 全部离线断言，不调 LLM。

覆盖：
  R1-A  verify 脚本配置装配：apply_template 后 part_word_max/硬上限正确（G2 可及）
  R1-B  _save_chunk_progress 不再抛 NameError('_os') 且落盘正确
  R1-D  TempStoryState established_facts 反序列化 + build_established_facts_block；
        review state mock 挂事实块；render_for_prompt 保护类豁免全局截断
  R1-E  derive_departed_characters 纯函数（正常/空/非正式角色名/别名不命中）；
        退场角色严禁出场段注入 writer 上下文
  R1-H  sliding_window 关键词通道注入 + 与向量段去重 + 默认关闭
  R1-J  count_p0 跳过降级结果；聚合器仅在有修复时追加标注字段

既支持 pytest 也支持 `python backend/tests/e2e/test_round1_fixes.py` 直接跑。
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

from core.logger import get_logger

logger = get_logger('test_round1_fixes')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ---------------- R1-B: _save_chunk_progress NameError 回归 ----------------

def _make_service(tmp_path: Path, work_id: str = 'test_round1_r1b'):
    """构造临时 work + WritingService（patch WORKS_DIR 到 tmp_path）。"""
    import core.config as config_mod
    import api.works as works_api
    work = {
        'id': work_id, 'title': 'test r1b', 'inspiration': '测试灵感', 'phase': 'phase3_part1',
        'parts': {}, 'part_summaries': {}, 'part_outline': [{'title': 'Part 1'}],
        'world_setting': '', 'characters': [], 'foreshadowing': [],
    }
    (tmp_path / f'{work_id}.json').write_text(json.dumps(work, ensure_ascii=False), encoding='utf-8')
    from services.writing_service import WritingService
    from api.sse import SSEEmitter
    with patch.object(config_mod, 'WORKS_DIR', tmp_path), patch.object(works_api, 'WORKS_DIR', tmp_path):
        svc = WritingService(work_id, SSEEmitter(), resume=False)
    return svc, tmp_path / f'{work_id}.json'


def test_r1b_save_chunk_progress_no_name_error(tmp_path):
    """R1-B: _save_chunk_progress 必须走轻量原子路径（此前 _os.replace NameError 每次都回退全量 _save）。"""
    svc, work_file = _make_service(tmp_path)
    try:
        svc._save_chunk_progress(1, '第一章正文内容' * 50, '第一章摘要')
        data = json.loads(work_file.read_text(encoding='utf-8'))
        assert data['parts']['1'] == '第一章正文内容' * 50, 'checkpoint 后 parts[1] 未正确写入'
        assert data['part_summaries']['1'] == '第一章摘要', 'checkpoint 后 part_summaries[1] 未正确写入'
        logger.info('[test_r1b] PASS: _save_chunk_progress 无异常且 parts/summaries 落盘正确')
    finally:
        svc._save()


# ---------------- R1-E: derive_departed_characters 纯函数 ----------------

def _facts_with_departures():
    from core.established_facts import EstablishedFacts, Fact
    ef = EstablishedFacts()
    ef.add(Fact(id='F1_1', part_num=1, category='character', subject='林尘', predicate='身份', text='林尘是少年'))
    ef.add(Fact(id='F3_1', part_num=3, category='character', subject='林尘', predicate='死亡', text='林尘在与反派决战中死亡'))
    ef.add(Fact(id='F4_1', part_num=4, category='character', subject='苏婉', predicate='离开', text='苏婉离开宗门'))
    ef.add(Fact(id='F4_2', part_num=4, category='object', subject='玄铁令', predicate='位置', text='玄铁令在长老手中'))
    return ef


def test_r1e_derive_departed_characters_normal():
    """R1-E: 正常场景 —— 死亡/离开 fact 且 subject 是正式角色名时才入账本。"""
    from core.established_facts import derive_departed_characters
    ef = _facts_with_departures()
    out = derive_departed_characters(ef, ['林尘', '苏婉', '玄机'])
    assert out == {'林尘': 'Part3 死亡: 林尘在与反派决战中死亡',
                   '苏婉': 'Part4 离开: 苏婉离开宗门'}, out
    # 直传 .facts 列表也应等价
    assert derive_departed_characters(ef.facts, ['林尘']) == {'林尘': 'Part3 死亡: 林尘在与反派决战中死亡'}
    logger.info('[test_r1e_normal] PASS')


def test_r1e_derive_departed_characters_predicate_in_value():
    """R1-E: text 用近义表述（战死）时 predicate 仍入值 —— 下游按谓词子串筛条目不漏判。"""
    from core.established_facts import derive_departed_characters, Fact, EstablishedFacts
    ef = EstablishedFacts()
    ef.add(Fact(id='F9_1', part_num=9, category='character', subject='林尘', predicate='死亡', text='林尘战死沙场'))
    out = derive_departed_characters(ef, ['林尘'])
    assert out == {'林尘': 'Part9 死亡: 林尘战死沙场'}, out
    logger.info('[test_r1e_predicate] PASS')


def test_r1e_derive_departed_characters_edge_cases():
    """R1-E: 空 facts / None / 非正式角色名（含大小写与别名）不命中。"""
    from core.established_facts import derive_departed_characters, Fact, EstablishedFacts
    names = ['林尘']
    assert derive_departed_characters([], names) == {}
    assert derive_departed_characters(None, names) == {}
    assert derive_departed_characters(EstablishedFacts(), names) == {}
    # 非正式角色名（常见词/别名/大小写不同）不得误报
    ef = EstablishedFacts()
    ef.add(Fact(id='X1', part_num=2, category='character', subject='死亡', predicate='状态', text='有人死亡'))
    ef.add(Fact(id='X2', part_num=2, category='character', subject='林尘儿时', predicate='离开', text='林尘儿时离开故乡'))
    ef.add(Fact(id='X3', part_num=2, category='character', subject='linchen', predicate='死亡', text='linchen 死亡'))
    assert derive_departed_characters(ef, names) == {}, '非正式角色名/别名不得命中'
    logger.info('[test_r1e_edge] PASS')


def test_r1e_departed_block_injected_into_context():
    """R1-E: character_state_track 含退场谓词时，writer 上下文出现严禁出场段。"""
    from services.writing_service import TempStoryState
    data = {
        'id': 'w', 'parts': {'1': '林尘战死沙场。' * 30}, 'part_summaries': {'1': '林尘战死。'},
        'part_outline': [{'title': 'P1'}, {'title': 'P2'}], 'characters': [],
        # 与 derive_departed_characters 实际输出一致（含 predicate 字面量）
        'character_state_track': {'林尘': 'Part1 死亡: 林尘战死沙场', '苏婉': 'Part2 在宗门修炼'},
    }
    state = TempStoryState(data)
    ctx = state.get_part_context(2)
    assert '【已退场角色——严禁出场】' in ctx, '退场清单段未注入 writer 上下文'
    assert '林尘' in ctx and '苏婉' not in ctx.split('【已退场角色——严禁出场】')[1], '非退场条目不应进严禁出场段'
    logger.info('[test_r1e_inject] PASS')


# ---------------- R1-D: established_facts 消费链 ----------------

def test_r1d_temp_story_state_facts_roundtrip():
    """R1-D: TempStoryState 从 data 反序列化 established_facts 并渲染事实块。"""
    from services.writing_service import TempStoryState
    data = {
        'id': 'w', 'parts': {'1': 'x' * 100}, 'part_summaries': {'1': 's'},
        'part_outline': [], 'characters': [],
        'established_facts': {'version': 1, 'facts': [
            {'id': 'F1_1', 'part_num': 1, 'category': 'object', 'text': '油纸包在主角背包中',
             'subject': '油纸包', 'predicate': '位置'}]},
    }
    state = TempStoryState(data)
    block = state.build_established_facts_block(2)
    assert '【前文已确立事实清单' in block and '油纸包在主角背包中' in block, block
    # Part 1 无前文事实时应为空串
    assert state.build_established_facts_block(1) == ''
    logger.info('[test_r1d_roundtrip] PASS')


def test_r1d_review_state_mock_has_facts_block(tmp_path):
    """R1-D: _build_review_state_mock 挂上 build_established_facts_block（Logic agent hasattr 守卫生效）。"""
    svc, _ = _make_service(tmp_path, 'test_round1_r1d')
    try:
        svc.data['established_facts'] = {'version': 1, 'facts': [
            {'id': 'F1_1', 'part_num': 1, 'category': 'character', 'text': '林尘是少年',
             'subject': '林尘', 'predicate': '身份'}]}
        mock = svc._build_review_state_mock()
        assert hasattr(mock, 'build_established_facts_block')
        block = mock.build_established_facts_block(2)
        assert '前文已确立事实清单' in block and '林尘是少年' in block, block
        logger.info('[test_r1d_mock] PASS')
    finally:
        svc._save()


def test_r1d_render_protected_categories_survive_truncation():
    """R1-D: object/location/relationship/world_rule 不参与全局 30 条倒序截断。"""
    from core.established_facts import EstablishedFacts, Fact
    ef = EstablishedFacts()
    ef.add(Fact(id='O1', part_num=1, category='object', subject='油纸包', predicate='位置', text='油纸包在主角背包中'))
    for i in range(30):
        ef.add(Fact(id=f'E{i}', part_num=5, category='event', text=f'事件{i}'))
    rendered = ef.render_for_prompt(before_part_num=6)
    assert '油纸包在主角背包中' in rendered, '关键物品事实被全局截断砍掉（R1-D 回归）'
    # 签名/默认值不变（旧调用方无感）
    import inspect
    sig = inspect.signature(EstablishedFacts.render_for_prompt)
    assert sig.parameters['max_per_category'].default == 8
    assert sig.parameters['max_total'].default == 30
    logger.info('[test_r1d_render] PASS')


# ---------------- R1-H: 关键词检索通道 ----------------

def test_r1h_keyword_channel_injects_recent_mentions():
    """R1-H: 传入专名列表时注入'【关键实体最近提及】'段；不传时通道关闭。"""
    from core.sliding_window import SlidingWindow
    sw = SlidingWindow(window_size=2)
    for i in range(1, 16):
        summary = f'Part {i} 摘要'
        if i == 3:
            summary += '，玄铁令重现江湖'
        if i == 12:
            summary += '，玄铁令被主角获得'
        sw.add_part(i, f'Part {i} 全文', summary)
    ctx_off = sw.build(part_num=15)
    assert '关键实体最近提及' not in ctx_off, '默认（不传 key_entities）不应注入关键词段'
    ctx_on = sw.build(part_num=15, key_entities=['玄铁令', '油纸包'])
    assert '【关键实体最近提及】' in ctx_on, '传入专名后应注入关键词段'
    assert 'Part 12' in ctx_on and 'Part 3' in ctx_on, '应命中最近 2 个提及 Part'
    logger.info('[test_r1h_channel] PASS')


def test_r1h_keyword_channel_dedup_with_vector():
    """R1-H: 与向量检索段去重 —— 同 Part 不重复注入。"""

    class _StubVS:
        enabled = True

        def query(self, q, top_k=3, exclude_part_num=None):
            return [(12, 0.9)]

        def get_text(self, part_num):
            return f'Part {part_num} 全文内容'

    from core.sliding_window import SlidingWindow
    sw = SlidingWindow(window_size=2)
    for i in range(1, 16):
        summary = f'Part {i} 摘要'
        if i == 12:
            summary += '，玄铁令被主角获得'
        sw.add_part(i, f'Part {i} 全文', summary)
    ctx = sw.build(part_num=15, vector_store=_StubVS(), vector_query='玄铁令', key_entities=['玄铁令'])
    assert '【关键实体最近提及】' in ctx
    assert '【相关前文片段（向量检索 Top-K）】' in ctx
    vec_idx = ctx.find('【相关前文片段（向量检索 Top-K）】')
    assert 'Part 12' not in ctx[vec_idx:], '关键词段已注入的 Part 不应在向量段重复出现'
    logger.info('[test_r1h_dedup] PASS')


# ---------------- R1-A: 验收脚本配置装配 ----------------

def test_r1a_apply_template_makes_g2_reachable():
    """R1-A: apply_template(自定义) + reload_config 后，20 Part 硬上限总和 ≥ G2 下限。"""
    import core.config as cc
    cfg = cc.get_app_config()
    # 快照当前配置，测后恢复（避免污染同会话其他测试）
    snapshot = (cfg._target_words, cfg._part_count, cfg._part_word_min, cfg._part_word_max, cfg.template)
    try:
        tmpl = next((t for t in cc.DEFAULT_TEMPLATES if t.name == '自定义'),
                    cc.WritingTemplate('自定义', 0, 0, 0, 0))
        cfg.apply_template(tmpl, custom_target_words=20 * 5000, custom_part_count=20)
        cc.reload_config()
        assert cc.PART_COUNT == 20
        hard_max = cc.PART_WORD_MAX + 200
        assert 20 * hard_max >= 90000, f'20 Part 理论上限 {20 * hard_max} < G2 下限 90000'
        cfg.apply_template(tmpl, custom_target_words=2 * 5000, custom_part_count=2)
        cc.reload_config()
        assert cc.PART_WORD_MAX + 200 == 10200, 'KML_PARTS=2 时硬上限应为 10200'
        logger.info('[test_r1a] PASS: 20 Part 硬上限=%d（总 %d），2 Part 硬上限=10200' % (hard_max, 20 * hard_max))
    finally:
        cfg._target_words, cfg._part_count, cfg._part_word_min, cfg._part_word_max, cfg.template = snapshot
        cc.reload_config()


# ---------------- R1-J: 修复回路 ----------------

def test_r1j_count_p0_skips_degraded_results():
    """R1-J: agent 降级结果（调用失败）不计入 P0 —— 重写治不了基础设施故障。"""
    from services.consistency_repair import count_p0
    degraded_logic = {'p0_count': 1, '_fallback': True, 'verdict': '审查失败'}
    degraded_cons = {'pass': False, 'issues': [{'level': 'P0'}], 'verdict': '检查失败（降级评分）: x'}
    assert count_p0(degraded_logic, {}) == 0
    assert count_p0({}, degraded_cons) == 0
    ok_logic = {'p0_count': 2}
    ok_cons = {'issues': [{'level': 'P0'}, {'level': 'P1'}]}
    assert count_p0(ok_logic, ok_cons) == 3
    logger.info('[test_r1j_count_p0] PASS')


def test_r1j_aggregator_annotation_only_when_repaired():
    """R1-J: 无修复触发时聚合输出不含标注字段（与改前一致）；触发时追加。"""
    from services.review_aggregator import aggregate_review_results
    base = [{'part': 1, 'logic_result': {'overall_score': 8, 'pass': True},
             'emotion_result': {'emotion_score': 7}, 'consistency_result': {'overall_score': 8, 'pass': True}}]
    out_plain = aggregate_review_results(base)
    assert 'revision_attempted' not in out_plain['parts'][0]
    repaired = [dict(base[0], revision_attempted=True, revision_passed=False)]
    out_fixed = aggregate_review_results(repaired)
    assert out_fixed['parts'][0]['revision_attempted'] is True
    assert out_fixed['parts'][0]['revision_passed'] is False
    logger.info('[test_r1j_aggregator] PASS')


if __name__ == '__main__':
    logger.info('=' * 60)
    logger.info('test_round1_fixes.py —— Round 1 修复回归（离线）')
    logger.info('=' * 60)
    import tempfile

    def _run(fn):
        with tempfile.TemporaryDirectory() as td:
            fn(Path(td))

    tests = [
        ('r1b_save_chunk_progress', lambda p: test_r1b_save_chunk_progress_no_name_error(p)),
        ('r1e_normal', lambda p: test_r1e_derive_departed_characters_normal()),
        ('r1e_edge', lambda p: test_r1e_derive_departed_characters_edge_cases()),
        ('r1e_inject', lambda p: test_r1e_departed_block_injected_into_context()),
        ('r1d_roundtrip', lambda p: test_r1d_temp_story_state_facts_roundtrip()),
        ('r1d_mock', lambda p: test_r1d_review_state_mock_has_facts_block(p)),
        ('r1d_render', lambda p: test_r1d_render_protected_categories_survive_truncation()),
        ('r1h_channel', lambda p: test_r1h_keyword_channel_injects_recent_mentions()),
        ('r1h_dedup', lambda p: test_r1h_keyword_channel_dedup_with_vector()),
        ('r1a_template', lambda p: test_r1a_apply_template_makes_g2_reachable()),
        ('r1j_count_p0', lambda p: test_r1j_count_p0_skips_degraded_results()),
        ('r1j_aggregator', lambda p: test_r1j_aggregator_annotation_only_when_repaired()),
    ]
    for name, fn in tests:
        logger.info(f'\n[{name}]')
        _run(fn)
    logger.info('\n' + '=' * 60)
    logger.info('ALL PASS')
    logger.info('=' * 60)
