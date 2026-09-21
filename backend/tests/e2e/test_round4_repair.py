"""
Round 4 修复回路回归测试（R4-2 姓名漂移定点修复 / R4-5 修复回路四段式）
—— 全部离线断言，不调 LLM。

覆盖：
  R4-2  配对推导（显式对 / 歧义放弃 / 错误名在 registry 放弃 / alias_candidate 来源）；
        4 条安全闸（长度 / 双向子串 / 退场名）；替换计数校验；重审不过回退；
        冒烟 A 历史数据回放（step5_longform_20260921_005855 Part 2 原文片段 +
        林渊/林万重）走离线 mock：断言走定点修复路径、零 LLM 调用、结果确定
  R4-5  degraded 判定（residual 不降 / 新 dimension 出现 / logic p0_count 上升）；
        第二跳条件（严格改善才触发、未改善不触发、brief 含新引入问题清单）；
        2 轮硬顶；mock-LLM 集成覆盖"定点修复通过 / 定点失败回退 / 闸失败回落重写 /
        重写变坏回退 / 条件性第二跳 / 2 轮硬顶"路径

既支持 pytest 也支持 `python backend/tests/e2e/test_round4_repair.py` 直接跑。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

from core.logger import get_logger

logger = get_logger('test_round4_repair')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.name_registry import build_name_registry  # noqa: E402
from services.consistency_repair import (  # noqa: E402
    ConsistencyRepairer, apply_name_spotfix, apply_safety_gates,
    derive_name_pairs, has_name_issue, is_revision_degraded,
    newly_introduced_problems,
)

# 冒烟 A（step5_longform_20260921_005855）Part 2 原文逐字片段：Phase 2 规范名是
# "林万重"（林氏大长老，Part 1 出现 22 次），Part 2 漂移出"林渊"（15 次），
# consistency 判 P0（dimension=名称一致性）。历史 revision_log：4 P0 → 重写 5202 字
# → residual 3 且引入新类别冲突 → revision_passed=False。
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

# 冒烟 A 历史 consistency P0 issue（work.json review_report 逐字结构）
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

SMOKE_A_CHARACTERS = [
    {'name': '林尘', 'role': '主角', 'identity': '被家族封禁十年的“天煞孤星”'},
    {'name': '林万重', 'role': '核心配角/反派', 'identity': '林氏大长老，祠堂审判主持者'},
    {'name': '殷刹', 'role': '反派', 'identity': '上古神族巡界使'},
    {'name': '林轻眉', 'role': '核心配角', 'identity': '林尘生母，十年前“病逝”的守井人'},
]


class _FakeService:
    """ConsistencyRepairer 所需的最小 service 面（离线，无 work 文件）。"""

    def __init__(self, data=None):
        self.data = data if isinstance(data, dict) else {}
        self.work_id = 'r4_repair_test'
        self.vector_store = None
        self.progress_callback = lambda *a, **k: None
        self.saved_chunks = {}
        self.save_count = 0

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


# ---------------- R4-2: 配对推导 ----------------

def test_derive_name_pairs_explicit_from_issue():
    """R4-2 (b): issue 的 character token + description/location 字面包含 → 显式配对。"""
    reg = build_name_registry(SMOKE_A_CHARACTERS)
    pairs = derive_name_pairs(SMOKE_A_PART2_EXCERPT,
                              {'issues': [SMOKE_A_NAME_ISSUE]}, reg)
    assert len(pairs) == 1, pairs
    p = pairs[0]
    assert p['wrong'] == '林渊' and p['right'] == '林万重', p
    assert p['source'] == 'issue_character'
    logger.info('[test_pairs_issue] PASS: 冒烟 A issue 推出显式配对 林渊→林万重')


def test_derive_name_pairs_ambiguity_gives_up():
    """R4-2: 歧义即放弃 —— 多个 canonical / 无 canonical / 错误名在 registry。"""
    reg = build_name_registry(SMOKE_A_CHARACTERS)
    text = SMOKE_A_PART2_EXCERPT
    # 场景 1: description 含 2 个 canonical（林万重 + 林尘）→ 歧义放弃
    issue_multi = {'level': 'P0', 'dimension': '名称一致性',
                   'character': '林渊',
                   'description': '林万重与林尘的名字发生混淆，林渊也在场。'}
    assert derive_name_pairs(text, {'issues': [issue_multi]}, reg) == []
    # 场景 2: description 不含任何 canonical → 放弃
    issue_none = {'level': 'P0', 'dimension': '名称一致性',
                  'character': '林渊',
                  'description': '此处人名有问题，但未提及任何档案角色。'}
    assert derive_name_pairs(text, {'issues': [issue_none]}, reg) == []
    # 场景 3: 错误名本身在 registry → 放弃（正确名必须在名册、错误名必须不在）
    issue_in_reg = {'level': 'P0', 'dimension': '名称一致性',
                    'character': '殷刹',
                    'description': '殷刹与林万重名称矛盾。'}
    assert derive_name_pairs(text, {'issues': [issue_in_reg]}, reg) == []
    # 场景 4: 正文没有错误名 → 放弃（替换无意义）
    assert derive_name_pairs('完全没有漂移名的正文。', {'issues': [SMOKE_A_NAME_ISSUE]}, reg) == []
    # 场景 5: 非 P0 issue 不参与
    p1_issue = dict(SMOKE_A_NAME_ISSUE, level='P1')
    assert derive_name_pairs(text, {'issues': [p1_issue]}, reg) == []
    logger.info('[test_pairs_ambiguity] PASS: 四类歧义/越界场景全部放弃')


def test_derive_name_pairs_from_promoted_candidate():
    """R4-2 (a): registry 已晋升的别名候选（带证据）也是显式配对来源。"""
    reg = build_name_registry(SMOKE_A_CHARACTERS)
    reg['林万重']['alias_candidates'] = [
        {'variant': '林万重', 'part_num': 2, 'evidence': '', 'timestamp': 't'},  # 同 canonical，忽略
        {'variant': '林渊', 'part_num': 2, 'evidence': '大长老林渊突然发出一声凄厉的惨笑', 'timestamp': 't'},
    ]
    pairs = derive_name_pairs(SMOKE_A_PART2_EXCERPT, {'issues': []}, reg)
    assert len(pairs) == 1 and pairs[0]['wrong'] == '林渊'
    assert pairs[0]['right'] == '林万重' and pairs[0]['source'] == 'alias_candidate'
    # 未晋升（单次无证据）候选不产配对
    reg2 = build_name_registry(SMOKE_A_CHARACTERS)
    reg2['林万重']['alias_candidates'] = [
        {'variant': '林渊', 'part_num': 2, 'evidence': '', 'timestamp': 't'}]
    assert derive_name_pairs(SMOKE_A_PART2_EXCERPT, {'issues': []}, reg2) == []
    logger.info('[test_pairs_candidate] PASS: 晋升候选产配对、未晋升不产配对')


def test_has_name_issue_triage():
    """R4-2 分诊：dimension=名称一致性 / description 含晋升别名对 / 其余不算。"""
    reg = build_name_registry(SMOKE_A_CHARACTERS)
    assert has_name_issue({'issues': [SMOKE_A_NAME_ISSUE]}, reg) is True
    other = {'level': 'P0', 'dimension': '状态连续性', 'character': '林尘',
             'description': '林尘位置突变。'}
    assert has_name_issue({'issues': [other]}, reg) is False
    reg['林万重']['alias_candidates'] = [
        {'variant': '林渊', 'part_num': 2, 'evidence': 'quote', 'timestamp': 't'}]
    alias_issue = {'level': 'P0', 'dimension': '状态连续性', 'character': '林渊',
                   'description': '林渊的身份出现矛盾。'}
    assert has_name_issue({'issues': [alias_issue]}, reg) is True
    assert has_name_issue({'issues': [SMOKE_A_NAME_ISSUE]}, {}) is False
    logger.info('[test_has_name_issue] PASS: 分诊三类判定符合预期')


# ---------------- R4-2: 4 条安全闸 ----------------

def test_apply_safety_gates_four_conditions():
    """R4-2: 长度 / 双向子串 / 退场名 / 合法通过。"""
    reg = {'林渊': {'aliases': ['渊哥'], 'alias_candidates': []},
           '林忠': {'aliases': [], 'alias_candidates': []}}
    departed = ['林轻眉']
    # 闸 (b): 长度 < 2
    out = apply_safety_gates([{'wrong': '林', 'right': '林渊'}], reg, departed)
    assert out == []
    # 闸 (c) 方向 1: 错误名是注册名的子串（林渊 ⊂ 林渊渊）
    reg_sub = {'林渊渊': {'aliases': [], 'alias_candidates': []}}
    assert apply_safety_gates([{'wrong': '林渊', 'right': '林渊渊'}], reg_sub) == []
    # 闸 (c) 方向 2: 注册名是错误名的子串（林渊 ⊂ 林渊某某）
    assert apply_safety_gates([{'wrong': '林渊某某', 'right': '殷刹'}],
                              {'林渊': {'aliases': [], 'alias_candidates': []}}) == []
    # 闸 (c) 别名字段同样参与
    assert apply_safety_gates([{'wrong': '渊哥', 'right': '殷刹'}], reg) == []
    # 闸 (d): 退场名
    assert apply_safety_gates([{'wrong': '林轻眉', 'right': '林渊'}], reg, departed) == []
    # 合法配对通过（错误名与任何注册名/别名/退场名无子串关系）
    reg_ok = {'殷刹': {'aliases': [], 'alias_candidates': []},
              '林忠': {'aliases': [], 'alias_candidates': []}}
    ok = [{'wrong': '林渊', 'right': '殷刹'}]
    assert apply_safety_gates(ok, reg_ok, departed) == ok
    logger.info('[test_safety_gates] PASS: 4 条安全闸逐一验证')


def test_apply_name_spotfix_count_identity():
    """R4-2: 替换计数恒等式（中文名不等长时仍成立）；恒等式破坏即失败。"""
    text = '林渊来了。林渊又走了。林万重在场。'
    new_text, ok, reason = apply_name_spotfix(text, [{'wrong': '林渊', 'right': '林万重'}])
    assert ok, reason
    assert new_text.count('林渊') == 0
    assert new_text.count('林万重') == text.count('林万重') + text.count('林渊') == 3
    assert new_text == '林万重来了。林万重又走了。林万重在场。'
    # 反向（错误名更长）：恒等式同样成立
    new_text2, ok2, _ = apply_name_spotfix('甲乙丙丁。', [{'wrong': '甲乙', 'right': 'A'}])
    assert ok2 and new_text2 == 'A丙丁。'
    # 替换 reintroduce 错误名 → 失败且原文不动
    bad, ok3, reason3 = apply_name_spotfix('AA', [{'wrong': 'AA', 'right': 'AAA'}])
    assert not ok3 and bad == 'AA' and '未清零' in reason3
    # 多配对顺序应用
    multi, ok4, _ = apply_name_spotfix('甲和乙。', [{'wrong': '甲', 'right': '丙'},
                                                   {'wrong': '乙', 'right': '丁'}])
    assert ok4 and multi == '丙和丁。'
    logger.info('[test_spotfix_count] PASS: 计数恒等式与失败保护')


# ---------------- R4-5: degraded / 第二跳 / 硬顶 ----------------

def test_is_revision_degraded_criteria():
    """R4-5: residual 不降 / 新 dimension 出现 / logic p0_count 上升 → degraded。"""
    first_cons = {'issues': [{'level': 'P0', 'dimension': '名称一致性',
                              'description': '名字漂移'}]}
    # residual 没变好（4 → 4）
    assert is_revision_degraded(4, 4, first_cons, {'issues': []}, _clean_logic(4), _clean_logic(4))
    # 新 dimension 出现（首检没有的知识合理性）
    new_cons = {'issues': [{'level': 'P0', 'dimension': '知识合理性',
                            'description': '信息越界'}]}
    assert is_revision_degraded(4, 1, first_cons, new_cons, _clean_logic(4), _clean_logic(3))
    # logic V5 p0_count 上升
    assert is_revision_degraded(4, 3, first_cons, first_cons, _clean_logic(2), _clean_logic(3))
    # 严格改善且无新类别 → 不 degraded
    assert not is_revision_degraded(4, 3, first_cons, first_cons, _clean_logic(4), _clean_logic(3))
    logger.info('[test_degraded] PASS: 三条劣化判据 + 反向')


def test_newly_introduced_problems_diff():
    """R4-5: 新引入问题清单 = consistency 差集 + logic verdict 变化。"""
    first_cons = {'issues': [{'level': 'P0', 'dimension': '名称一致性',
                              'description': '名字漂移', 'location': 'Part2 第3段'}]}
    new_cons = {'issues': [
        {'level': 'P0', 'dimension': '名称一致性', 'description': '名字漂移', 'location': 'Part2 第3段'},
        {'level': 'P0', 'dimension': '知识合理性', 'description': '信息越界', 'location': 'Part2 第9段'}]}
    problems = newly_introduced_problems(_clean_logic(4, verdict='v1'), first_cons,
                                         _clean_logic(3, verdict='v2'), new_cons)
    assert len(problems) == 2, problems
    assert any('信息越界' in p for p in problems)
    assert any('逻辑结论变化' in p for p in problems)
    # 无变化 → 空
    assert newly_introduced_problems(_clean_logic(4, verdict='v1'), first_cons,
                                     _clean_logic(3, verdict='v1'), first_cons) == []
    logger.info('[test_introduced] PASS: 差集 + logic verdict 变化')


# ---------------- mock-LLM 集成：三条主路径 + 第二跳 + 硬顶 ----------------

def test_spotfix_smoke_a_replay_zero_llm():
    """R4-2 验收: 冒烟 A 历史数据回放 —— 走定点修复路径、零 LLM 调用、结果确定。"""
    reg = build_name_registry(SMOKE_A_CHARACTERS)
    service = _FakeService({'name_registry': reg, 'character_state_track': {},
                            'established_facts': None, 'parts': {'2': SMOKE_A_PART2_EXCERPT}})
    repairer = _repairer(service, [_clean_logic(0)], [_clean_cons()])

    def _boom(*a, **k):
        raise AssertionError('定点修复路径不应发生任何 LLM 调用（writer/评审均 mock）')

    with patch('core.agents.part_writer_agent.PartWriterAgent.execute', _boom), \
         patch('core.agents.part_writer_agent.call_llm', _boom), \
         patch('core.agents.part_writer_agent.call_llm_json', _boom):
        note = asyncio.run(repairer.maybe_repair_part(
            2, SMOKE_A_PART2_EXCERPT,
            {'p0_count': 3, 'p1_count': 0, 'issues': [], 'verdict': '身份撕裂'},
            {'issues': [SMOKE_A_NAME_ISSUE], 'verdict': 'ok', 'overall_score': 4},
            state_mock=type('M', (), {'parts': {}, 'final_draft': {}})()))

    assert note.get('revision_passed') is True, note
    assert note.get('revision_spotfixed') is True
    fixed = service.saved_chunks[2]
    assert fixed.count('林渊') == 0, '漂移名必须清零'
    assert fixed.count('林万重') == (SMOKE_A_PART2_EXCERPT.count('林万重')
                                     + SMOKE_A_PART2_EXCERPT.count('林渊'))
    # 除名字外一字不动（去掉名字后骨架一致）
    assert fixed.replace('林万重', '') == SMOKE_A_PART2_EXCERPT.replace('林渊', '').replace('林万重', '')
    # 留痕：revision_log 有 name_spotfix 专属字段
    log = service.data['revision_log']
    assert len(log) == 1
    entry = log[0]
    assert entry['type'] == 'name_spotfix'
    assert entry['wrong_name'] == '林渊' and entry['right_name'] == '林万重'
    assert entry['pair_source'] == 'issue_character'
    assert entry['revision_passed'] is True and entry['p0_before'] == 4
    logger.info('[test_spotfix_replay] PASS: 冒烟 A 回放走定点修复，零 LLM，林渊→林万重')


def test_spotfix_failed_rerereview_rolls_back():
    """R4-2: 定点修复后重审仍有 P0 → R6-2（S2）起改为分层持久化：
    过闸的名称修复独立落盘（partial_applied，不连坐），残留 P0 以修复稿为
    base 转全文重写（本用例 patch _rewrite_once 失败 → 保留修复稿）。"""
    reg = build_name_registry(SMOKE_A_CHARACTERS)
    service = _FakeService({'name_registry': reg, 'character_state_track': {},
                            'parts': {'2': SMOKE_A_PART2_EXCERPT}})
    # 重审 logic 仍 2 个 P0 + cons 1 个名称类 P0 → residual=3（非名称残留 2）
    repairer = _repairer(service, [_clean_logic(2, verdict='仍有矛盾')],
                         [_clean_cons([{'level': 'P0', 'dimension': '名称一致性',
                                        'description': '仍有名字问题'}])])
    rewrite_calls = []

    async def fake_rewrite(self, part_num, brief):
        rewrite_calls.append(brief)
        return '', 'rewrite_failed'  # 重写不可用 → 保留 base_text（修复稿）

    state_mock = type('M', (), {'parts': {}, 'final_draft': {}})()
    with patch.object(ConsistencyRepairer, '_rewrite_once', fake_rewrite):
        note = asyncio.run(repairer.maybe_repair_part(
            2, SMOKE_A_PART2_EXCERPT, _clean_logic(3), {'issues': [SMOKE_A_NAME_ISSUE]},
            state_mock=state_mock))
    assert note.get('revision_passed') is False
    assert note.get('revision_error') == 'rewrite_failed'
    assert note.get('first_pass_p0') == 4, 'first_pass_p0 恒定原始首检数'
    # 分层持久化：修复稿落盘（不是原文），state_mock 快照同步
    fixed = service.saved_chunks[2]
    assert fixed != SMOKE_A_PART2_EXCERPT, 'R6-2 起过闸修复不再连坐回退'
    assert fixed.count('林渊') == 0 and fixed.count('林万重') == \
        SMOKE_A_PART2_EXCERPT.count('林万重') + SMOKE_A_PART2_EXCERPT.count('林渊')
    assert state_mock.parts['2'] == fixed and state_mock.final_draft['2'] == fixed
    # 双留痕：revision_log partial_applied + name_audit_log partial_applied
    log = service.data['revision_log']
    partial = [e for e in log if e.get('partial_applied')]
    assert len(partial) == 1, log
    assert partial[0]['type'] == 'name_spotfix'
    assert partial[0]['residual_non_name_p0'] == 2, partial[0]
    assert partial[0]['revision_passed'] is False
    from services.name_audit import AUDIT_LOG_KEY
    audit = service.data[AUDIT_LOG_KEY]
    pa = [e for e in audit if e.get('action') == 'partial_applied']
    assert len(pa) == 1 and pa[0]['wrong'] == '林渊' and pa[0]['right'] == '林万重'
    assert pa[0]['trigger'] != 'final_audit', 'partial_applied 不进终审 latest-entry 口径'
    # 残留 P0 转重写：_rewrite_once 被调用且 brief 含"姓名已按名册归一化"行
    assert len(rewrite_calls) == 1, '残留 P0 必须落全文重写（不连坐）'
    assert '姓名已按名册归一化' in rewrite_calls[0]
    logger.info('[test_spotfix_rollback] PASS: R6-2 分层持久化（修复保留+残留转重写）')


def test_gate_failure_falls_back_to_rewrite():
    """R4-2: 配对未过安全闸 → 放弃定点修复，落回全文重写（writer 被调用）。"""
    reg = build_name_registry(SMOKE_A_CHARACTERS)
    service = _FakeService({'name_registry': reg,
                            'character_state_track': {'林渊': 'Part1 死亡: 林渊战死'},
                            'parts': {'2': SMOKE_A_PART2_EXCERPT}})
    repairer = _repairer(service, [_clean_logic(0)], [_clean_cons()])
    rewrite_calls = []

    async def fake_rewrite(self, part_num, brief):
        rewrite_calls.append(brief)
        return ('重写后的新正文。' * 400), ''

    with patch.object(ConsistencyRepairer, '_rewrite_once', fake_rewrite):
        note = asyncio.run(repairer.maybe_repair_part(
            2, SMOKE_A_PART2_EXCERPT, _clean_logic(4), {'issues': [SMOKE_A_NAME_ISSUE]},
            state_mock=type('M', (), {'parts': {}, 'final_draft': {}})()))
    # 闸 (d): 林渊 在退场名单 → 定点修复放弃 → 重写路径
    assert len(rewrite_calls) == 1, '闸失败必须落回全文重写'
    assert note.get('revision_passed') is True and note.get('revision_spotfixed') is None
    assert '姓名定点修正' in rewrite_calls[0], 'brief 应含名称类指令（配对仍在 brief 层展示）'
    assert '角色名册' in rewrite_calls[0], 'brief 应追加名册段'
    assert service.saved_chunks[2] == '重写后的新正文。' * 400
    logger.info('[test_gate_fallback] PASS: 闸失败回落重写且 brief 含名册与姓名指令')


def test_rewrite_degraded_rolls_back():
    """R4-5: 重写导致劣化（residual 不降）→ 立即回退原文 + revision_degraded。"""
    service = _FakeService({'name_registry': {}, 'parts': {'2': SMOKE_A_PART2_EXCERPT}})
    # 首检 4 P0，重写后仍 4 P0（没变好）→ degraded
    repairer = _repairer(service, [_clean_logic(4, verdict='v1')], [_clean_cons()])
    rewrite_calls = []

    async def fake_rewrite(self, part_num, brief):
        rewrite_calls.append(brief)
        return ('重写稿。' * 400), ''

    with patch.object(ConsistencyRepairer, '_rewrite_once', fake_rewrite):
        note = asyncio.run(repairer.maybe_repair_part(
            2, SMOKE_A_PART2_EXCERPT, _clean_logic(4, verdict='v1'), _clean_cons(),
            state_mock=type('M', (), {'parts': {}, 'final_draft': {}})()))
    assert note.get('revision_passed') is False
    assert note.get('revision_degraded') is True and note.get('residual_p0') == 4
    assert len(rewrite_calls) == 1, '劣化必须立即回退，不进入第二轮'
    assert service.saved_chunks[2] == SMOKE_A_PART2_EXCERPT
    entry = service.data['revision_log'][0]
    assert entry.get('revision_degraded') is True and entry.get('residual_p0') == 4
    logger.info('[test_rewrite_degraded] PASS: 劣化回退且不进第二轮')

    # 场景 2: residual 严格改善（4→3）但重审出现首检没有的新 P0 类别 → 仍判劣化，
    # 不进第二轮（钉死 degraded 与第二跳条件的交互）
    service2 = _FakeService({'name_registry': {}, 'parts': {'2': SMOKE_A_PART2_EXCERPT}})
    first_cons2 = _clean_cons([{'level': 'P0', 'dimension': '名称一致性',
                                'description': '名字漂移'}])
    new_cons2 = _clean_cons([{'level': 'P0', 'dimension': '名称一致性',
                              'description': '名字漂移'},
                             {'level': 'P0', 'dimension': '知识合理性',
                              'description': '信息越界'}])
    repairer2 = _repairer(service2, [_clean_logic(2, verdict='v2')], [new_cons2])
    rewrite_calls2 = []

    async def fake_rewrite2(self, part_num, brief):
        rewrite_calls2.append(brief)
        return ('重写稿。' * 400), ''

    with patch.object(ConsistencyRepairer, '_rewrite_once', fake_rewrite2):
        note2 = asyncio.run(repairer2.maybe_repair_part(
            2, SMOKE_A_PART2_EXCERPT, _clean_logic(4, verdict='v1'), first_cons2,
            state_mock=type('M', (), {'parts': {}, 'final_draft': {}})()))
    assert len(rewrite_calls2) == 1, '改善但引入新类别必须立即回退，不进第二轮'
    # 首检 5（logic 4 + cons 1）→ 重审 4（logic 2 + cons 2）：严格改善但出现新类别
    assert note2.get('revision_degraded') is True and note2.get('residual_p0') == 4
    assert service2.saved_chunks[2] == SMOKE_A_PART2_EXCERPT
    logger.info('[test_rewrite_degraded_new_dim] PASS: 改善+新类别 → 劣化回退不进第二轮')


def test_conditional_second_hop_with_introduced_problems():
    """R4-5: 第一轮严格改善未归零 → 条件性第二轮，brief 附新引入问题清单。"""
    service = _FakeService({'name_registry': {}, 'parts': {'2': SMOKE_A_PART2_EXCERPT}})
    first_cons = _clean_cons([{'level': 'P0', 'dimension': '名称一致性',
                               'description': '名字漂移', 'location': 'Part2 第3段'}])
    # 首检 logic 4 P0 + cons 1 P0 = 5；第一轮重审 logic 3 P0 + cons 2 P0（新类别）= 5 → 不降 → degraded？
    # 这里构造"严格改善"：第一轮 residual=3 < 5，且无新类别
    r1_logic = _clean_logic(2, verdict='v2')
    r1_cons = _clean_cons([{'level': 'P0', 'dimension': '名称一致性',
                            'description': '名字漂移', 'location': 'Part2 第3段'}])
    r2_logic = _clean_logic(0)
    r2_cons = _clean_cons()
    repairer = _repairer(service, [r1_logic, r2_logic], [r1_cons, r2_cons])
    rewrite_calls = []

    async def fake_rewrite(self, part_num, brief):
        rewrite_calls.append(brief)
        return ('第一轮重写稿。' * 400), ''

    with patch.object(ConsistencyRepairer, '_rewrite_once', fake_rewrite):
        note = asyncio.run(repairer.maybe_repair_part(
            2, SMOKE_A_PART2_EXCERPT,
            _clean_logic(4, verdict='v1'), first_cons,
            state_mock=type('M', (), {'parts': {}, 'final_draft': {}})()))
    assert len(rewrite_calls) == 2, f'严格改善应触发第二轮: {len(rewrite_calls)}'
    assert note.get('revision_passed') is True
    # 第二轮 brief 必须附第一轮新引入的问题清单
    assert '第一轮重写新引入的问题' in rewrite_calls[1]
    assert '逻辑结论变化' in rewrite_calls[1]
    assert service.saved_chunks[2] == '第一轮重写稿。' * 400
    logger.info('[test_second_hop] PASS: 条件性第二跳 + brief 附新引入问题清单')


def test_rewrite_hard_cap_two_rounds():
    """R4-5: 2 轮硬顶 —— 第二轮仍改善但未归零也保留原文（不再第三轮）。"""
    service = _FakeService({'name_registry': {}, 'parts': {'2': SMOKE_A_PART2_EXCERPT}})
    # 首检 4；第一轮 3（改善）；第二轮 2（改善但 round_no==2 达硬顶）
    repairer = _repairer(service,
                         [_clean_logic(3, verdict='v2'), _clean_logic(2, verdict='v3')],
                         [_clean_cons(), _clean_cons()])
    rewrite_calls = []

    async def fake_rewrite(self, part_num, brief):
        rewrite_calls.append(brief)
        return (f'第{len(rewrite_calls)}轮重写稿。' * 400), ''

    with patch.object(ConsistencyRepairer, '_rewrite_once', fake_rewrite):
        note = asyncio.run(repairer.maybe_repair_part(
            2, SMOKE_A_PART2_EXCERPT, _clean_logic(4, verdict='v1'), _clean_cons(),
            state_mock=type('M', (), {'parts': {}, 'final_draft': {}})()))
    assert len(rewrite_calls) == 2, f'硬顶 2 轮: {len(rewrite_calls)}'
    assert note.get('revision_passed') is False and note.get('residual_p0') == 2
    assert service.saved_chunks[2] == SMOKE_A_PART2_EXCERPT, '2 轮后仍不过必须保留原文'
    logger.info('[test_hard_cap] PASS: 2 轮硬顶后保留原文')


def test_no_p0_no_repair():
    """R1-J 语义保持: 无 P0 时返回 {}（零行为变化）。"""
    service = _FakeService({'name_registry': build_name_registry(SMOKE_A_CHARACTERS)})
    repairer = _repairer(service, [], [])
    note = asyncio.run(repairer.maybe_repair_part(
        2, SMOKE_A_PART2_EXCERPT, _clean_logic(0), _clean_cons(),
        state_mock=type('M', (), {'parts': {}, 'final_draft': {}})()))
    assert note == {}
    assert service.saved_chunks == {} and 'revision_log' not in service.data
    logger.info('[test_no_p0] PASS: 无 P0 不触发修复')


if __name__ == '__main__':
    logger.info('=' * 60)
    logger.info('test_round4_repair.py —— Round 4 修复回路回归（mock LLM）')
    logger.info('=' * 60)
    for fn in (test_derive_name_pairs_explicit_from_issue,
               test_derive_name_pairs_ambiguity_gives_up,
               test_derive_name_pairs_from_promoted_candidate,
               test_has_name_issue_triage,
               test_apply_safety_gates_four_conditions,
               test_apply_name_spotfix_count_identity,
               test_is_revision_degraded_criteria,
               test_newly_introduced_problems_diff,
               test_spotfix_smoke_a_replay_zero_llm,
               test_spotfix_failed_rerereview_rolls_back,
               test_gate_failure_falls_back_to_rewrite,
               test_rewrite_degraded_rolls_back,
               test_conditional_second_hop_with_introduced_problems,               test_rewrite_hard_cap_two_rounds,
               test_no_p0_no_repair):
        fn()
        print(f'PASS {fn.__name__}')
    logger.info('\nALL PASS')
