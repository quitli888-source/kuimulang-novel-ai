"""
Round 4 姓名一致性回归测试（R4-1 角色规范名注册表 / R4-4 facts 名册对照+别名候选）
—— 全部离线断言，不调 LLM。

覆盖：
  R4-1  build_name_registry（空/重名/缺字段/非 dict 过滤，无合并逻辑）；
        render_name_roster（departed / aliases / 候选晋升分开展示 / 空 registry）；
        render_name_roster_for_state（registry 优先、旧 work JSON 退化 characters 名列表）；
        集成断言（>30 条 facts 场景 get_part_context 仍含完整名册段 —— 不受 facts
        截断影响）；mock 级断言（consistency/logic 两个 agent 的 user_prompt 含名册段）
  R4-4  register_name_variants 只追加不改写（既有 facts/registry 逐字段比对）；
        候选晋升规则（1 次无证据不展示 / 2 次或带 quote 展示）；
        含 name_variants 的 payload 解析不破坏 facts 列表（≤10 条上限不回退）

既支持 pytest 也支持 `python backend/tests/e2e/test_round4_names.py` 直接跑。
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.logger import get_logger

logger = get_logger('test_round4_names')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.name_registry import (  # noqa: E402
    build_name_registry, render_name_roster, render_name_roster_for_state,
)


def _registry():
    return build_name_registry([
        {'name': '林渊', 'role': '主角', 'identity': '少年', 'core_trait': '坚忍'},
        {'name': '林忠', 'role': '核心配角', 'identity': '老仆', 'core_trait': '忠诚'},
    ])


# ---------------- R4-1: build_name_registry ----------------

def test_build_name_registry_filters_invalid_entries():
    """R4-1: 空/非 list/重名/缺 name/非 dict 全部安全处理；合法条目四字段齐全。"""
    assert build_name_registry([]) == {}
    assert build_name_registry(None) == {}
    assert build_name_registry('not a list') == {}
    reg = build_name_registry([
        {'name': '林渊', 'role': '主角'},      # 合法
        {'name': '林渊', 'role': '配角'},      # 重名 → 跳过（不合并）
        {'role': '无名氏'},                    # 缺 name → 跳过
        'not a dict',                          # 非 dict → 跳过
        {'name': ' 林忠 ', 'role': '核心配角'},  # 名字去空白
        {'name': ''},                          # 空名 → 跳过
    ])
    assert set(reg.keys()) == {'林渊', '林忠'}, reg
    assert reg['林渊'] == {'role': '主角', 'introduced_part': 1,
                           'aliases': [], 'alias_candidates': []}
    assert reg['林忠']['role'] == '核心配角'
    logger.info('[test_build_name_registry] PASS: 重名/空名/非 dict 均跳过且不留合并痕迹')


def test_build_name_registry_no_merge_logic():
    """R4-1（Round 1 拒绝理由负面清单）：注册表无任何相似度/编辑距离合并代码路径。

    用 AST 查真实 import 与调用（docstring 的否定式描述不算）。
    """
    import ast
    src = (_BACKEND / 'core' / 'name_registry.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    banned_modules = {'difflib', 'Levenshtein', 'rapidfuzz', 'fuzzywuzzy', 'numpy'}
    banned_calls = {'SequenceMatcher', 'get_close_matches', 'levenshtein',
                    'edit_distance', 'ratio', 'similarity', 'cosine'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split('.')[0] not in banned_modules, f'禁止导入: {a.name}'
        if isinstance(node, ast.ImportFrom):
            assert (node.module or '').split('.')[0] not in banned_modules, f'禁止导入: {node.module}'
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else '')
            assert name not in banned_calls, f'禁止调用合并/相似度函数: {name}'
    logger.info('[test_no_merge_logic] PASS: 注册表零相似度/合并 import 与调用')


# ---------------- R4-1: render_name_roster ----------------

def test_render_name_roster_empty():
    """R4-1: 空 registry 返回 ''（Phase 2 角色档案为空时不注入空段）。"""
    assert render_name_roster({}) == ''
    assert render_name_roster(None) == ''
    assert render_name_roster('x') == ''
    logger.info('[test_render_empty] PASS: 空 registry → 空串')


def test_render_name_roster_with_departed():
    """R4-1: departed_track 中的角色标注"已退场（PartN）——严禁出场"。"""
    reg = _registry()
    out = render_name_roster(reg, {'林忠': 'Part8 死亡: 林忠战死于断魂崖'})
    assert '【角色名册——唯一正确写法（最高优先级）】' in out
    assert '- 林渊（主角）：全书唯一正确写法是"林渊"' in out
    assert '已退场（Part8 死亡: 林忠战死于断魂崖）——严禁出场' in out
    assert '规则：本段与【角色档案】冲突时以本段为准' in out
    # 无 departed 时不出现退场标注
    assert '严禁出场' not in render_name_roster(reg)
    logger.info('[test_render_departed] PASS: 退场角色进名册段并标注严禁出场')


def test_render_name_roster_aliases_and_candidates():
    """R4-1: 正式 aliases 与晋升后的 alias_candidates 分开展示。"""
    reg = _registry()
    reg['林渊']['aliases'] = ['渊哥']
    out = render_name_roster(reg)
    assert '- 已登记别名：渊哥 = 林渊' in out
    logger.info('[test_render_aliases] PASS: 正式 aliases 展示为已登记别名行')


def test_render_name_roster_candidate_promotion():
    """R4-4 晋升规则：1 次无证据不展示 / 2 次或带 quote 展示（graphiti 式不确定不生效）。"""
    reg = _registry()
    # 场景 1: 单次无证据候选 —— 只记录不注入 prompt
    reg['林渊']['alias_candidates'] = [
        {'variant': '林万重', 'part_num': 2, 'evidence': '', 'timestamp': 't'}]
    assert '林万重' not in render_name_roster(reg), '单次无证据候选不得注入 prompt'
    # 场景 2: 同一 variant 出现 2 次 —— 晋升展示
    reg['林渊']['alias_candidates'] = [
        {'variant': '林万重', 'part_num': 2, 'evidence': '', 'timestamp': 't1'},
        {'variant': '林万重', 'part_num': 5, 'evidence': '', 'timestamp': 't2'}]
    out = render_name_roster(reg)
    assert '已登记别名：林万重 = 林渊' in out
    assert '正文仍必须使用规范名"林渊"' in out
    # 场景 3: 单次但带 quote 证据 —— 晋升展示
    reg['林渊']['alias_candidates'] = [
        {'variant': '林万重', 'part_num': 2, 'evidence': '林万重踏上寻道之路', 'timestamp': 't'}]
    out = render_name_roster(reg)
    assert '已登记别名：林万重 = 林渊（Part2 误写登记' in out
    logger.info('[test_render_promotion] PASS: 候选晋升规则三分支全部符合预期')


def test_render_name_roster_for_state_degraded():
    """R4-1: state 无 registry 时退化为 characters 名列表（兼容旧 work JSON）。"""
    state = SimpleNamespace(characters=[{'name': '林尘'}, {'name': ''}, 'x'])
    out = render_name_roster_for_state(state)
    assert '【角色名册' in out and '- 林尘：' in out and '林渊' not in out
    # 两者都无 → 空
    assert render_name_roster_for_state(SimpleNamespace()) == ''
    assert render_name_roster_for_state(None) == ''
    logger.info('[test_render_state_degraded] PASS: 旧 work JSON 退化路径正常')


# ---------------- R4-1: 集成断言（不受 facts 截断影响） ----------------

def _ctx_state_data():
    """>30 条 facts + registry 的场景（锁定名册不受 established_facts 截断影响）。"""
    facts = [{'id': f'F1_{i}', 'part_num': 1, 'category': 'event',
              'text': f'第{i}号事件', 'subject': f'主体{i}', 'predicate': '发生'}
             for i in range(1, 36)]  # 35 条 > max_total=30
    return {
        'id': 'r4_names_ctx', 'inspiration': '灵感', 'core_elements': {},
        'market_positioning': {}, 'world_setting': '玄幻世界',
        'characters': [{'name': '林渊', 'role': '主角', 'identity': '少年',
                        'core_trait': '坚忍', 'motivation': '寻道', 'secret': '血脉'},
                       {'name': '林忠', 'role': '核心配角', 'identity': '老仆',
                        'core_trait': '忠诚', 'motivation': '护主', 'secret': '无'}],
        'part_outline': [{'title': 'P1', 'core_event': '破封', 'word_count': 5000},
                         {'title': 'P2', 'core_event': '寻道', 'word_count': 5000}],
        'foreshadowing': [],
        'parts': {'1': '林渊踏入禁地，发现古井，寒风吹动衣角。' * 50},
        'part_summaries': {'1': '林渊踏入禁地。'},
        'character_state_track': {'林忠': 'Part1 死亡: 林忠战死于断魂崖'},
        'name_registry': _registry(),
        'established_facts': {'version': 1, 'facts': facts},
    }


def test_get_part_context_contains_full_roster():
    """R4-1 验收: >30 条 facts 场景 get_part_context 输出仍含完整名册段。"""
    from services.writing_service import TempStoryState
    data = _ctx_state_data()
    state = TempStoryState(data, memory='')
    assert len(state.established_facts.facts) == 35
    ctx = state.get_part_context(2)
    expected = render_name_roster(data['name_registry'], data['character_state_track'])
    assert expected in ctx, '名册段必须完整出现在 get_part_context 输出中（不受 facts 截断影响）'
    # 回退路径同样有名册（窗口 build 异常时名册不消失）
    legacy = state._legacy_get_part_context(2)
    assert expected in legacy, '_legacy_get_part_context 回退路径必须包含名册段'
    logger.info('[test_ctx_roster] PASS: 35 条 facts 下名册段完整进主路径与回退路径')


def test_review_agent_prompts_contain_roster():
    """R4-1 验收: consistency/logic 两个 agent 的 user_prompt 含名册段（mock 捕获）。"""
    from core.agents.consistency_review_agent import ConsistencyReviewAgent
    from core.agents.logic_review_agent import LogicReviewAgent

    reg = _registry()
    reg['林渊']['alias_candidates'] = [
        {'variant': '林万重', 'part_num': 2, 'evidence': '林万重踏上寻道之路', 'timestamp': 't'}]
    state = SimpleNamespace(
        work_id='r4', characters=data_chars(), part_summaries={'1': '林渊踏入禁地。'},
        parts={'1': '林渊踏入禁地。' * 50}, final_draft={'1': 'x'}, world_setting='玄幻',
        part_outline=[{'title': 'P1'}, {'title': 'P2', 'core_event': '寻道'}],
        name_registry=reg, character_state_track={},
        build_established_facts_block=lambda pn, categories=None: '',
    )

    captured = {}

    def fake_json(**kwargs):
        captured['user_prompt'] = kwargs.get('user_prompt', '')
        return {'pass': True, 'overall_score': 8, 'issues': [],
                'character_states': {}, 'verdict': 'ok'}

    with patch('core.agents.consistency_review_agent.call_llm_json', side_effect=fake_json):
        ConsistencyReviewAgent().execute(state, 2, '林渊继续前行。' * 30)
    up = captured['user_prompt']
    assert '【角色名册——唯一正确写法（最高优先级）】' in up, 'consistency user_prompt 缺名册段'
    assert '林渊（主角）' in up and '名称一致性判定指引' in up
    assert '名字不在名册内即为 P0 名称不一致' in up

    def fake_json_logic(**kwargs):
        captured['user_prompt'] = kwargs.get('user_prompt', '')
        return {'score': 8, 'pass': True, 'p0_count': 0, 'p1_count': 0, 'verdict': 'ok'}

    with patch('core.agents.logic_review_agent.call_llm_json', side_effect=fake_json_logic):
        LogicReviewAgent().execute(state, 2, '林渊继续前行。' * 30)
    up = captured['user_prompt']
    assert '【角色名册——唯一正确写法（最高优先级）】' in up, 'logic user_prompt 缺名册段'
    assert '已登记别名：林万重 = 林渊' in up, '晋升候选应进 logic 名册段'
    logger.info('[test_review_prompts] PASS: 两评审 user_prompt 均含名册段')


def data_chars():
    return [{'name': '林渊', 'role': '主角', 'identity': '少年', 'core_trait': '坚忍',
             'motivation': '寻道', 'secret': '血脉'},
            {'name': '林忠', 'role': '核心配角', 'identity': '老仆', 'core_trait': '忠诚',
             'motivation': '护主', 'secret': '无'}]


# ---------------- R4-4: register_name_variants ----------------

def test_register_name_variants_append_only():
    """R4-4: 只追加 alias_candidates，不改写 registry 其他字段/facts/历史文本。"""
    from core.established_facts import register_name_variants, EstablishedFacts, Fact
    reg = _registry()
    ef = EstablishedFacts()
    ef.add(Fact(id='F1_1', part_num=1, category='character', text='林渊是少年',
                subject='林渊', predicate='身份'))
    facts_before = [f.to_dict() for f in ef.facts]
    reg_before = {k: {kk: (list(vv) if isinstance(vv, list) else vv)
                      for kk, vv in v.items()} for k, v in reg.items()}

    registered = register_name_variants(reg, [
        {'variant': '林万重', 'canonical': '林渊', 'evidence': '林万重踏上寻道之路'},
        {'variant': '林某', 'canonical': '不存在的人', 'evidence': 'q'},   # canonical 不在名册 → 跳过
        'not a dict',                                                     # 脏数据 → 跳过
        {'variant': '', 'canonical': '林渊', 'evidence': 'q'},              # 空 variant → 跳过
    ], part_num=2)

    assert len(registered) == 1 and registered[0]['variant'] == '林万重'
    assert registered[0]['part_num'] == 2
    # registry 除 alias_candidates 外逐字段不变
    for name, info in reg.items():
        before = reg_before[name]
        for k, v in before.items():
            if k == 'alias_candidates':
                continue
            assert info[k] == v, f'registry[{name}][{k}] 被改写'
    assert len(reg['林渊']['alias_candidates']) == 1
    cand = reg['林渊']['alias_candidates'][0]
    assert cand['variant'] == '林万重' and cand['part_num'] == 2
    assert cand['evidence'] == '林万重踏上寻道之路' and cand['timestamp']
    assert reg['林忠']['alias_candidates'] == []
    # 既有 facts 一字未动
    assert [f.to_dict() for f in ef.facts] == facts_before
    # 空 registry / 非 list variants 安全返回
    assert register_name_variants({}, [{'variant': 'a', 'canonical': 'b'}], 1) == []
    assert register_name_variants(reg, 'not a list', 1) == []
    logger.info('[test_register_variants] PASS: 只追加候选，facts/registry 零改写')


def test_payload_with_name_variants_parses_facts():
    """R4-4: 含 name_variants 的 payload 不破坏 facts 解析；≤10 条上限不回退（R1-F 口径）。"""
    from core.established_facts import facts_from_extractor_payload
    payload = {
        'facts': [{'category': 'character', 'subject': '林渊', 'predicate': '身份',
                   'text': f'事实{i}', 'quote': 'q'} for i in range(12)],
        'name_variants': [{'variant': '林万重', 'canonical': '林渊', 'evidence': 'q'}],
    }
    facts = facts_from_extractor_payload(payload, 3)
    assert len(facts) == 10, f'facts 条数上限应保持 10: {len(facts)}'
    assert all(f.part_num == 3 for f in facts)
    assert all(f.subject == '林渊' for f in facts)
    logger.info('[test_payload_variants] PASS: name_variants 不影响 facts 解析与 10 条上限')


if __name__ == '__main__':
    logger.info('=' * 60)
    logger.info('test_round4_names.py —— Round 4 姓名一致性回归（mock LLM）')
    logger.info('=' * 60)
    for fn in (test_build_name_registry_filters_invalid_entries,
               test_build_name_registry_no_merge_logic,
               test_render_name_roster_empty,
               test_render_name_roster_with_departed,
               test_render_name_roster_aliases_and_candidates,
               test_render_name_roster_candidate_promotion,
               test_render_name_roster_for_state_degraded,
               test_get_part_context_contains_full_roster,
               test_review_agent_prompts_contain_roster,
               test_register_name_variants_append_only,
               test_payload_with_name_variants_parses_facts):
        fn()
        print(f'PASS {fn.__name__}')
    logger.info('\nALL PASS')
