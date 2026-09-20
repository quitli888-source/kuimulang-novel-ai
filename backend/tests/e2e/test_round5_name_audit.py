"""
Round 5 姓名审计回归测试（R5-1 违禁词典+确定性终审 / R5-2 修复动作硬接线 /
R5-3 配对推导第三来源 issue 引文 span）—— 全部离线断言，不调 LLM。

覆盖（02_review.md §2.1-2.3 验收标准）：
  R5-3  引文 span 来源（Round 4 P0-1 形态：character 只写规范名也产配对）；
        位置型替换指令闸（排掉"大长老林渊→林万重"坏配对 / 功能词 span 拒绝）；
        冒烟 A 历史回放（最终配对仍为 issue_character，现有断言不变）；
        verdict 纳入 has_name_issue 扫描面；
        歧义放弃五场景不回退（test_round4_repair 同步锁定）
  R5-1  name_audit 数据层（load/record 只升不降/blocking 分层公式）；
        违禁词典扫描与探测器 B（Round 4 冒烟真实数据回放）；
        audit_name_drift 双探测器 + 脏数据 fail-open；
        evaluate_g4 第三参 name_audit（四象限 + 旧格式逐字节不变 + env 关闭）
  R5-2  重审后二次定点（有界 1 次）/ 重写稿姓名先修后判 / revision_log trigger

既支持 pytest 也支持 `python backend/tests/e2e/test_round5_name_audit.py` 直接跑。
"""
import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.logger import get_logger

logger = get_logger('test_round5_name_audit')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.name_registry import build_name_registry  # noqa: E402
from services.consistency_repair import (  # noqa: E402
    ConsistencyRepairer, apply_name_spotfix, apply_safety_gates,
    derive_name_pairs, has_name_issue,
)
from services.name_audit import (  # noqa: E402
    AUDIT_LOG_KEY, DRIFT_DICT_KEY, audit_name_drift, is_blocking, load_drift_dict,
    record_name_pairs, scan_forbidden,
)
from services.writing_phase_runners import Phase4Runner  # noqa: E402


def _load_verify_module():
    """从门禁脚本 import evaluate_g4 / summarize_name_audit（屏蔽 RUN_DIR.mkdir）。"""
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
evaluate_g4 = verify_mod.evaluate_g4
# summarize_name_audit 随 R5-1 终审接线（commit 3）落入门禁脚本；此处容忍缺席
summarize_name_audit = getattr(verify_mod, 'summarize_name_audit', None)

# ---------------- 共享 fixture（Round 4 冒烟真实数据形态） ----------------

# R4-1 名册（step5_longform_20260921_030039 work.json：4 个规范名）
R4_SMOKE_CHARACTERS = [
    {'name': '林尘', 'role': '主角', 'identity': '被家族封禁十年的"天煞孤星"'},
    {'name': '林啸天', 'role': '核心配角', 'identity': '执法长老'},
    {'name': '林战', 'role': '核心配角', 'identity': '林家族人'},
    {'name': '井中神族意识', 'role': '反派', 'identity': '古井深处沉睡的上古神族意识'},
]

# Round 4 P0-1 复审判定原文（character 只写规范名的形态）
R4_P1_NAME_ISSUE = {
    'level': 'P0',
    'dimension': '名称一致性',
    'character': '井中神族意识',
    'location': 'Part 2 中多处段落',
    'description': ("核心反派'井中神族意识'在正文中被大量简写为'井中意识'，"
                    "构成 P0 级名称不一致，需统一修正"),
    'suggestion': "统一使用规范名'井中神族意识'",
    'verdict': "存在名称漂移",
}

# Part 2 正文（井中意识×7、井中神族意识×0、其他规范名各 1 次 —— Round 4 冒烟
# work.json final_draft['2'] 的实测形态：井中神族意识 ×0 / 井中意识 ×7）
R4_P1_PART_TEXT = (
    '林尘跌入古井，井中意识在深渊中苏醒，井中意识低语着古老咒言。'
    '林啸天率族人封锁井口，井中意识却透过林战的眼睛窥视外界。'
    '井中意识许诺林尘神力，井中意识诱他献出血脉，井中意识在井底嘶鸣，'
    '井中意识终将吞没三千世界。'
)

# Part 1 正文（井中意识×0、井中神族意识×0 —— Round 4 冒烟实测形态：该角色在
# Part 1 以"井底青光"等描写形式在场、从未被点名；林尘/林啸天/林战在场）
R4_P1_PART1_TEXT = (
    '林尘跌入古井，井底青光萦绕不散。林啸天率族人封锁井口，'
    '林战的眼睛却被青光占据，喃喃自语。林尘许下查明身世的誓言。'
)

# established_facts（Round 4 冒烟形态：subject 全部逐字规范名，R4-4 协议）
# 井中神族意识 在 Part 1/Part 2 各有 fact → 探测器 B 在 Part 1/Part 2 双发
R4_P1_FACTS = {'version': 1, 'facts': [
    {'id': 'F1_1', 'part_num': 1, 'category': 'character', 'subject': '林尘',
     'predicate': '身份', 'text': '林尘是被封禁十年的天煞孤星'},
    {'id': 'F1_6', 'part_num': 1, 'category': 'character', 'subject': '井中神族意识',
     'predicate': '苏醒', 'text': '井中神族意识在古井深处苏醒'},
    {'id': 'F2_1', 'part_num': 2, 'category': 'character', 'subject': '林尘',
     'predicate': '位置', 'text': '林尘位于井底'},
    {'id': 'F2_8', 'part_num': 2, 'category': 'event', 'subject': '井中神族意识',
     'predicate': '苏醒', 'text': '井中神族意识诱林尘献出血脉'},
]}

# 冒烟 A 历史 consistency P0 issue（test_round4_repair.py 逐字结构）
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

SMOKE_A_PART2_EXCERPT = (
    '大长老林渊突然发出一声凄厉的惨笑。他袖中传讯玉毫无征兆地炸成齑粉。\n\n'
    '林万重从牌位堆里爬起，满脸是血："大长老，您老人家这是怎么了？"\n\n'
    '"闭嘴。"林渊嘶声吼道，"他来了。他终究还是来了。"\n\n'
    '林万重和大长老同时跪伏在地，额头触地："恭迎巡界使大人。"'
)

SMOKE_A_CHARACTERS = [
    {'name': '林尘', 'role': '主角', 'identity': '少年'},
    {'name': '林万重', 'role': '核心配角/反派', 'identity': '林氏大长老'},
    {'name': '殷刹', 'role': '反派', 'identity': '上古神族巡界使'},
    {'name': '林轻眉', 'role': '核心配角', 'identity': '林尘生母'},
]


class _FakeService:
    """ConsistencyRepairer / Phase4Runner 终审所需的最小 service 面（离线）。"""

    def __init__(self, data=None):
        self.data = data if isinstance(data, dict) else {}
        self.work_id = 'r5_audit_test'
        self.vector_store = None
        self.progress_callback = lambda *a, **k: None
        self.saved_chunks = {}
        self.save_count = 0
        self.cfg = SimpleNamespace(part_count=2)

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


def _repairer(service, logic_script, cons_script):
    return ConsistencyRepairer(service, _ScriptedAgent(logic_script),
                               _ScriptedAgent(cons_script))


# ---------------- R5-3: 配对推导第三来源（issue 引文 span） ----------------

def test_derive_name_pairs_issue_quote_round4_p1_form():
    """R5-3 验收 1: Round 4 P0-1 形态 —— character 只写规范名也产配对。

    description 含引文 span '井中意识' + 位置型指令"简写为" → 产出
    [{wrong: 井中意识, right: 井中神族意识, source: issue_quote}]，
    修不修不再取决于 character 字段写法。
    """
    reg = build_name_registry(R4_SMOKE_CHARACTERS)
    pairs = derive_name_pairs(R4_P1_PART_TEXT, {'issues': [R4_P1_NAME_ISSUE]}, reg)
    assert len(pairs) == 1, pairs
    p = pairs[0]
    assert p['wrong'] == '井中意识' and p['right'] == '井中神族意识', p
    assert p['source'] == 'issue_quote', p
    assert '简写为' in p['evidence'], p  # evidence 必须含位置型指令词
    logger.info('[test_quote_source] PASS: P0-1 形态经引文 span 产出 issue_quote 配对')


def test_derive_name_pairs_quote_source_rejects_bad_pair():
    """R5-3 验收 2: 冒烟 A 历史 issue —— (c) 不产出"大长老林渊→林万重"坏配对。

    '大长老林渊' 前后 6 字无替换指令词（"突然出现另一位"）→ 拒绝；
    最终配对仍为 林渊→林万重（来源 issue_character，既有断言不变）。
    """
    reg = build_name_registry(SMOKE_A_CHARACTERS)
    pairs = derive_name_pairs(SMOKE_A_PART2_EXCERPT, {'issues': [SMOKE_A_NAME_ISSUE]}, reg)
    assert len(pairs) == 1, pairs
    p = pairs[0]
    assert p['wrong'] == '林渊' and p['right'] == '林万重', p
    assert p['source'] == 'issue_character', p
    logger.info('[test_quote_bad_pair] PASS: 坏配对被指令闸拒绝，来源仍为 issue_character')


def test_derive_name_pairs_quote_source_requires_directive():
    """R5-3: 引文 span 无位置型替换指令 → 放弃（指令闸是坏配对唯一防线）。"""
    reg = build_name_registry(R4_SMOKE_CHARACTERS)
    issue = {'level': 'P0', 'dimension': '名称一致性', 'character': '井中神族意识',
             'description': "正文中突然出现另一位'井中意识'，与档案设定不符，请核查",
             'suggestion': '统一使用规范名'}
    # '井中意识' 前后 6 字（"中突然出现另一位" / "，与档案设定不符"）无指令词
    assert derive_name_pairs(R4_P1_PART_TEXT, {'issues': [issue]}, reg) == []
    # 功能词 span（'您的传讯玉' 类）同样排除
    issue2 = {'level': 'P0', 'dimension': '名称一致性', 'character': '林尘',
              'description': "道具'您的传讯玉'被改为'林尘的玉佩'，需修正",
              'suggestion': '统一名称'}
    assert derive_name_pairs(R4_P1_PART_TEXT, {'issues': [issue2]}, reg) == []
    logger.info('[test_quote_directive] PASS: 无指令佐证/功能词 span 均不产配对')


def test_derive_name_pairs_quote_source_from_verdict():
    """R5-3: verdict 字段的引文 span + 指令词同样可推导（扫描面三字段）。"""
    reg = build_name_registry(R4_SMOKE_CHARACTERS)
    issue = {'level': 'P0', 'dimension': '名称一致性', 'character': '井中神族意识',
             'description': "核心反派'井中神族意识'在正文中名称不一致，构成 P0",
             'suggestion': '请统一名称',
             'verdict': "复审判定：'井中意识'应统一为'井中神族意识'"}
    pairs = derive_name_pairs(R4_P1_PART_TEXT, {'issues': [issue]}, reg)
    assert len(pairs) == 1 and pairs[0]['source'] == 'issue_quote', pairs
    assert pairs[0]['wrong'] == '井中意识' and pairs[0]['right'] == '井中神族意识'
    logger.info('[test_quote_verdict] PASS: verdict 引文 span 可推导配对')


def test_has_name_issue_includes_verdict():
    """R5-3 验收 4: verdict 纳入 has_name_issue 扫描面。"""
    reg = build_name_registry(SMOKE_A_CHARACTERS)
    reg['林万重']['alias_candidates'] = [
        {'variant': '林渊', 'part_num': 2, 'evidence': 'quote', 'timestamp': 't'}]
    issue = {'level': 'P0', 'dimension': '状态连续性', 'character': '林尘',
             'description': '林尘位置突变。',
             'verdict': '林渊的身份出现矛盾。'}
    assert has_name_issue({'issues': [issue]}, reg) is True
    # 无 verdict 的同类 issue 仍为 False（description/location 无别名对）
    issue_no_verdict = {'level': 'P0', 'dimension': '状态连续性', 'character': '林尘',
                        'description': '林尘位置突变。'}
    assert has_name_issue({'issues': [issue_no_verdict]}, reg) is False
    logger.info('[test_has_name_verdict] PASS: verdict 进分诊扫描面')


# ---------------- R5-1: name_audit 数据层 ----------------

def test_load_drift_dict_tolerates_dirty_data():
    """R5-1: load_drift_dict 容忍脏数据（非 dict / 条目非 dict / 空键）。"""
    assert load_drift_dict({}) == {}
    assert load_drift_dict(None) == {}
    assert load_drift_dict('x') == {}
    assert load_drift_dict({DRIFT_DICT_KEY: 'not a dict'}) == {}
    dirty = {DRIFT_DICT_KEY: {'': {'wrong': ''}, 'x': 'not a dict',
                              '井中意识': {'wrong': '井中意识'}}}
    out = load_drift_dict(dirty)
    assert set(out.keys()) == {'井中意识'}, out
    logger.info('[test_load_dict] PASS: 脏数据安全加载')


def test_record_name_pairs_create_and_monotonic():
    """R5-1: record_name_pairs 建条/更新（parts_seen 去重、occurrences 累加、
    三标志只升不降、首次信息不被后写覆盖）。"""
    data = {}
    p = {'wrong': '井中意识', 'right': '井中神族意识', 'source': 'issue_character',
         'evidence': '简写为井中意识'}
    affected = record_name_pairs(data, [p], 2, 'first_pass')
    assert len(affected) == 1
    entry = data[DRIFT_DICT_KEY]['井中意识']
    assert entry['wrong'] == '井中意识' and entry['right'] == '井中神族意识'
    assert entry['source'] == 'issue_character'
    assert entry['directive_confirmed'] is False and entry['gates_passed'] is False
    assert entry['applied_verified'] is False
    assert entry['parts_seen'] == [2] and entry['first_seen_part'] == 2
    assert entry['last_seen_part'] == 2 and entry['occurrences'] == 1
    assert entry['evidence'] == '简写为井中意识' and entry['timestamp']
    # 同 Part 再记：parts_seen 去重、occurrences 累加
    record_name_pairs(data, [p], 2, 'first_pass')
    entry = data[DRIFT_DICT_KEY]['井中意识']
    assert entry['parts_seen'] == [2] and entry['occurrences'] == 2
    # 跨 Part：last_seen_part 前进；gates/applied 只升不降
    record_name_pairs(data, [{'wrong': '井中意识', 'right': '井中神族意识',
                              'source': 'issue_quote', 'evidence': ''}], 5,
                      're_review', applied_verified=True, gates_passed=True)
    entry = data[DRIFT_DICT_KEY]['井中意识']
    assert entry['parts_seen'] == [2, 5] and entry['last_seen_part'] == 5
    assert entry['gates_passed'] is True and entry['applied_verified'] is True
    assert entry['directive_confirmed'] is True
    assert entry['right'] == '井中神族意识' and entry['source'] == 'issue_character', \
        '首次出现的 right/source 不被后写覆盖'
    assert entry['evidence'] == '简写为井中意识', '首次 evidence 不被空值覆盖'
    # 脏配对直接跳过（wrong==right / 空 / 非 dict）
    record_name_pairs(data, [{'wrong': 'A', 'right': 'A'}, {'wrong': '', 'right': 'B'},
                             'not a dict'], 7, 'first_pass')
    assert set(data[DRIFT_DICT_KEY].keys()) == {'井中意识'}
    logger.info('[test_record_pairs] PASS: 建条/去重/累加/只升不降/脏数据跳过')


def test_is_blocking_layers():
    """R5-1 验收 3: blocking 分层公式 —— directive_confirmed=False 且非
    applied_verified → advisory，G4 不受影响。"""
    base = {'wrong': 'w', 'right': 'r'}
    assert is_blocking(dict(base, applied_verified=True)) is True
    assert is_blocking(dict(base, directive_confirmed=True, gates_passed=True)) is True
    assert is_blocking(dict(base, source='alias_candidate', evidence='q')) is True
    # advisory：单次无证据候选 / 闸未过 / 无指令佐证的 issue 配对
    assert is_blocking(dict(base, directive_confirmed=False, gates_passed=True)) is False
    assert is_blocking(dict(base, directive_confirmed=True, gates_passed=False)) is False
    assert is_blocking(dict(base, source='issue_character', gates_passed=True)) is False
    assert is_blocking(dict(base, source='alias_candidate', evidence='')) is False
    assert is_blocking(None) is False
    logger.info('[test_blocking_layers] PASS: blocking/advisory 分层符合公式')


# ---------------- R5-2: 修复动作硬接线到每个检查点 ----------------

# 重写稿（首检 consistency 无名称 P0 → 落全文重写；重写稿自身漂移出"林渊"）
R5_REWRITE_TEXT = (
    '林渊跪伏在地，额头触地，口称恭迎巡界使大人。林渊抬头时满脸是血，'
    '林万重扶住林渊的肩膀，连声追问大长老的伤势。林渊嘶声吼道他来了，'
    '林渊终究还是来了。林万重与大长老同时跪伏，祠堂内黑气尖啸着退散。'
) * 12


def test_rewrite_name_fix_before_judgement():
    """R5-2 验收 1: 重写重审 consistency 报名称 P0 → 先定点修复再判 pass。

    首检 consistency 无名称 P0（落重写）→ 重写稿漂移出"林渊" → 重审报名称
    P0 且 description 含规范名 + 引文 span → (2) 触发：重写稿被先定点修复
    再判 pass；revision_log trigger=='re_review'；spotfixed 计数正确。
    """
    reg = build_name_registry(SMOKE_A_CHARACTERS)
    service = _FakeService({'name_registry': reg, 'character_state_track': {},
                            'parts': {'2': SMOKE_A_PART2_EXCERPT}})
    rewrite_name_issue = {
        'level': 'P0', 'dimension': '名称一致性', 'character': '林万重',
        'location': 'Part 2 重写稿多处',
        'description': "重写稿中'林渊'应统一为'林万重'，仍构成 P0 级名称不一致",
        'suggestion': "将所有'林渊'改为'林万重'"}
    repairer = _repairer(service, [_clean_logic(0), _clean_logic(0)],
                         [_clean_cons([rewrite_name_issue]), _clean_cons()])
    rewrite_calls = []

    async def fake_rewrite(self, part_num, brief):
        rewrite_calls.append(brief)
        return R5_REWRITE_TEXT, ''

    with patch.object(ConsistencyRepairer, '_rewrite_once', fake_rewrite):
        note = asyncio.run(repairer.maybe_repair_part(
            2, SMOKE_A_PART2_EXCERPT, _clean_logic(2, verdict='v1'), _clean_cons(),
            state_mock=type('M', (), {'parts': {}, 'final_draft': {}})()))

    assert len(rewrite_calls) == 1, '首检无名称 P0 应走重写'
    assert note.get('revision_passed') is True, note
    assert note.get('revision_spotfixed') is True, '重写稿姓名定点应计入 spotfixed'
    fixed = service.saved_chunks[2]
    assert fixed.count('林渊') == 0 and fixed.count('林万重') == \
        R5_REWRITE_TEXT.count('林万重') + R5_REWRITE_TEXT.count('林渊')
    entry = service.data['revision_log'][-1]
    assert entry['trigger'] == 're_review', entry
    # revision_stats.spotfixed 计数正确（聚合器从 entry revision_* 字段汇总）
    from services.review_aggregator import aggregate_review_results
    report = aggregate_review_results([{
        'part': 2, 'logic_result': _clean_logic(), 'emotion_result': {},
        'consistency_result': _clean_cons(), **note}])
    assert report['revision_stats']['spotfixed'] == 1, report['revision_stats']
    # 违禁词典沉淀：applied_verified=True → blocking
    drift = service.data[DRIFT_DICT_KEY]
    assert is_blocking(drift['林渊']) is True and drift['林渊']['applied_verified'] is True
    logger.info('[test_rewrite_name_fix] PASS: 重写稿先修后判，trigger=re_review')


def test_spotfix_retry_bounded_once():
    """R5-2 验收 2: _spotfix_names 首轮重审不过且新 cons 含名称 P0 →
    (1) 触发二次定点且只重试 1 次（脚本 agent 调用次数断言）。"""
    reg = build_name_registry(R4_SMOKE_CHARACTERS)
    part_text = (
        '林尘跌入古井，井中意识在深渊中苏醒，井中意识低语古老咒言。'
        '林啸天率族人封锁井口，古井意识却占据林战的双眼。'
        '井中意识许诺林尘神力，古井意识终将吞没三千世界。'
    )
    service = _FakeService({'name_registry': reg, 'character_state_track': {},
                            'parts': {'2': part_text}})
    retry_issue = {
        'level': 'P0', 'dimension': '名称一致性', 'character': '井中神族意识',
        'location': 'Part 2 修复稿多处',
        'description': "修复稿中'古井意识'应统一为'井中神族意识'，仍构成 P0 名称不一致",
        'suggestion': "统一使用规范名"}
    logic_agent = _ScriptedAgent([_clean_logic(1, verdict='仍有矛盾'), _clean_logic(0)])
    cons_agent = _ScriptedAgent([_clean_cons([retry_issue]), _clean_cons()])
    repairer = ConsistencyRepairer(service, logic_agent, cons_agent)
    note = asyncio.run(repairer.maybe_repair_part(
        2, part_text, _clean_logic(2, verdict='v1'), {'issues': [R4_P1_NAME_ISSUE]},
        state_mock=type('M', (), {'parts': {}, 'final_draft': {}})()))

    assert note.get('revision_passed') is True, note
    assert note.get('revision_spotfixed') is True
    fixed = service.saved_chunks[2]
    assert fixed.count('井中意识') == 0 and fixed.count('古井意识') == 0
    assert fixed.count('井中神族意识') == part_text.count('井中意识') + part_text.count('古井意识')
    # 有界：logic/consistency 各只重审 2 次（首轮 + 二次定点后），无第三次
    assert len(logic_agent.calls) == 2, logic_agent.calls
    assert len(cons_agent.calls) == 2, cons_agent.calls
    entry = service.data['revision_log'][-1]
    assert entry['trigger'] == 're_review'
    assert entry['wrong_name'] == '井中意识|古井意识', entry
    assert entry['right_name'] == '井中神族意识|井中神族意识', entry
    drift = service.data[DRIFT_DICT_KEY]
    assert is_blocking(drift['古井意识']) is True, '二次定点过闸+重审通过 → blocking'
    logger.info('[test_spotfix_retry] PASS: 二次定点触发且有界 1 次（各 2 次重审）')


# ---------------- R5-1: 违禁词典 + final_draft 确定性终审 ----------------

def _r4_drift_entry(**over):
    """违禁词典条目（blocking：directive_confirmed + gates_passed）。"""
    entry = {
        'wrong': '井中意识', 'right': '井中神族意识', 'source': 'issue_quote',
        'directive_confirmed': True, 'gates_passed': True, 'applied_verified': False,
        'evidence': "被大量简写为'井中意识'", 'parts_seen': [2],
        'first_seen_part': 2, 'last_seen_part': 2, 'occurrences': 7,
        'timestamp': '2026-09-21 03:00:00',
    }
    entry.update(over)
    return entry


def _audit_service(drift_dict=None, with_facts=True):
    return _FakeService({
        'name_registry': build_name_registry(R4_SMOKE_CHARACTERS),
        'character_state_track': {},
        'established_facts': R4_P1_FACTS if with_facts else None,
        'parts': {'1': R4_P1_PART1_TEXT, '2': R4_P1_PART_TEXT},
        'final_draft': {'1': R4_P1_PART1_TEXT, '2': R4_P1_PART_TEXT},
        'name_drift_dict': drift_dict if drift_dict is not None else {},
    })


def test_final_audit_round4_replay_spotfix():
    """R5-1 验收 1: Round 4 冒烟真实数据回放 —— A 命中 blocking → 定点修复。

    final_draft['2'] 含 井中意识×7 + 词典含该配对 → A 命中、blocking、
    定点修复后 井中神族意识 0→7、井中意识 7→0、其他名字计数不变、
    residual_blocking=0；parts 逐字节不变（禁走 _save_chunk_progress）。
    """
    service = _audit_service({'井中意识': _r4_drift_entry()})
    cons_agent = _ScriptedAgent([_clean_cons()])  # 首次发现 → 1 次重审，干净 → 保留
    runner = Phase4Runner(service)
    scan = asyncio.run(runner._final_name_audit(
        service, [1, 2], cons_agent, SimpleNamespace(final_draft={})))

    assert scan['scanned'] == 2
    fixed = service.data['final_draft']['2']
    assert fixed.count('井中意识') == 0, '漂移名必须清零'
    assert fixed.count('井中神族意识') == 7, '井中神族意识 0→7'
    for name in ('林尘', '林啸天', '林战'):
        assert fixed.count(name) == R4_P1_PART_TEXT.count(name), f'{name} 计数必须不变'
    assert service.data['parts']['2'] == R4_P1_PART_TEXT, 'parts 必须逐字节不变'
    assert service.saved_chunks == {}, '终审修复禁止走 _save_chunk_progress'
    assert scan['residual_blocking'] == [], scan['residual_blocking']
    # 留痕：name_audit_log + revision_log(final_audit) + 词典 applied_verified
    audit_log = service.data[AUDIT_LOG_KEY]
    spot = [e for e in audit_log if e.get('action') == 'spotfixed']
    assert len(spot) == 1 and spot[0]['trigger'] == 'final_audit'
    assert spot[0]['wrong'] == '井中意识' and spot[0]['count_before'] == 7
    assert spot[0]['count_after'] == 0
    rev = [e for e in service.data['revision_log'] if e.get('trigger') == 'final_audit']
    assert len(rev) == 1 and rev[0]['type'] == 'name_spotfix'
    assert service.data[DRIFT_DICT_KEY]['井中意识']['applied_verified'] is True
    logger.info('[test_final_replay] PASS: 冒烟数据回放定点修复，parts 不变，残留 0')


def test_final_audit_verified_entry_zero_llm():
    """R5-1: applied_verified=True 的条目 → 零 LLM 直接修（验证分工）。"""
    service = _audit_service({'井中意识': _r4_drift_entry(applied_verified=True)})
    cons_agent = _ScriptedAgent([])  # 不应被调用

    def _boom(*a, **k):
        raise AssertionError('applied_verified 条目终审修复不应发生 LLM 调用')

    cons_agent.execute = _boom
    runner = Phase4Runner(service)
    scan = asyncio.run(runner._final_name_audit(
        service, [1, 2], cons_agent, SimpleNamespace(final_draft={})))
    assert scan['residual_blocking'] == []
    fixed = service.data['final_draft']['2']
    assert fixed.count('井中意识') == 0 and fixed.count('井中神族意识') == 7
    logger.info('[test_final_zero_llm] PASS: 已验证配对零 LLM 直接修')


def test_final_audit_detector_b_double_fire():
    """R5-1 验收 2: 空词典 → A 零命中，B 在 Part 1/Part 2 各 1 条（双发），
    且 B 只告警不改文本、不进 G4。"""
    reg = build_name_registry(R4_SMOKE_CHARACTERS)
    fd = {'1': R4_P1_PART1_TEXT, '2': R4_P1_PART_TEXT}
    scan = audit_name_drift(fd, {}, R4_P1_FACTS, reg)
    a_findings = [f for f in scan['findings'] if f['kind'] == 'forbidden_name']
    assert a_findings == [], '空词典 A 必须零命中'
    b = scan['canonical_absent']
    assert len(b) == 2, f'B 必须双发: {b}'
    assert {f['part'] for f in b} == {1, 2}
    assert all(f['canonical'] == '井中神族意识' for f in b)
    # 全量跑终审：B 触发针对性重审但绝不改文本
    service = _audit_service({})
    before = dict(service.data['final_draft'])
    cons_agent = _ScriptedAgent([_clean_cons(), _clean_cons()])
    asyncio.run(Phase4Runner(service)._final_name_audit(
        service, [1, 2], cons_agent, SimpleNamespace(final_draft={})))
    assert service.data['final_draft'] == before, 'B 永不自动改文本'
    # B 不进 G4：汇总 residual_blocking=0
    data = {'final_draft': fd, 'name_drift_dict': {},
            'name_audit_log': [{'part': 1, 'wrong': '', 'right': '井中神族意识',
                                'count_before': 0, 'count_after': 0,
                                'action': 'rereviewed', 'trigger': 'final_audit',
                                'pair_source': 'canonical_absent'}]}
    na = summarize_name_audit(data)
    assert na['residual_blocking'] == 0 and na['canonical_absent'] == 1
    g4, detail = evaluate_g4(_clean_report(), 20, na)
    assert g4 is True and detail['name_audit']['residual_blocking'] == 0
    logger.info('[test_final_detector_b] PASS: B 双发、不改文本、不影响 G4')


def test_audit_name_drift_dirty_data_fail_open():
    """R5-1 验收 4: 脏数据（facts 非 dict / registry 为 list / 占位）不抛异常。"""
    fd = {'1': '林尘踏入禁地，发现古井。', '2': '[Part 2 创作失败]',
          '3': None, 'x': 123}
    drift = {'井中意识': _r4_drift_entry()}
    scan = audit_name_drift(fd, drift, 'not a dict', ['not', 'a', 'dict'])
    assert scan['scanned'] == 1, '只有 Part 1 是有效交付文本'
    assert scan['findings'] == [], '占位/非 str/无命中 → 零 finding'
    # facts 为 None / registry 正常 → B 无原料不误报
    scan2 = audit_name_drift({'1': '林尘踏入禁地。'}, {}, None,
                             build_name_registry(R4_SMOKE_CHARACTERS))
    assert scan2['scanned'] == 1 and scan2['findings'] == []
    # 非 dict final_draft / 非 dict drift → 空结果
    assert audit_name_drift(None, drift, R4_P1_FACTS, {})['scanned'] == 0
    assert audit_name_drift({'1': 'x'}, 'not a dict', R4_P1_FACTS, {})['findings'] == []
    logger.info('[test_audit_dirty] PASS: 脏数据 fail-open，不阻断')


def test_evaluate_g4_name_audit_quadrants():
    """R5-1 验收 5: evaluate_g4 第三参四象限 + 旧格式逐字节不变 + env 关闭。"""
    # 旧格式（无 name_audit）：detail 不得含 name_audit 键（逐字节兼容）
    g4, detail = evaluate_g4(_clean_report(), 20)
    assert g4 is True and 'name_audit' not in detail
    # 干净 name_audit → 不改变判定
    na_clean = summarize_name_audit({'final_draft': {}, 'name_drift_dict': {},
                                     'name_audit_log': []})
    g4, detail = evaluate_g4(_clean_report(), 20, na_clean)
    assert g4 is True and detail['name_audit']['residual_blocking'] == 0
    # blocking 残留 → FAIL（sound：交付文本含已证漂移名）
    na_bad = dict(na_clean, residual_blocking=1)
    g4, detail = evaluate_g4(_clean_report(), 20, na_bad)
    assert g4 is False and detail['name_audit']['residual_blocking'] == 1
    # advisory 残留不影响 G4
    na_adv = dict(na_clean, residual_advisory=3)
    assert evaluate_g4(_clean_report(), 20, na_adv)[0] is True
    # env KML_NAME_AUDIT_GATE=0 可关闭条件，但 gate_enabled=False 原样落 detail
    os.environ['KML_NAME_AUDIT_GATE'] = '0'
    try:
        na_off = summarize_name_audit({'final_draft': {}, 'name_drift_dict': {},
                                       'name_audit_log': []})
        assert na_off['gate_enabled'] is False
        g4, detail = evaluate_g4(_clean_report(), 20, na_off)
        assert g4 is True and detail['name_audit']['gate_enabled'] is False
    finally:
        os.environ.pop('KML_NAME_AUDIT_GATE', None)
    # detail JSON 安全（report.json 落盘）
    import json
    text = json.dumps(evaluate_g4(_clean_report(), 20, na_clean)[1], ensure_ascii=False)
    assert '"name_audit"' in text and '"residual_blocking": 0' in text
    logger.info('[test_g4_name_audit] PASS: 四象限 + 旧格式兼容 + env 关闭不静默')


def test_summarize_name_audit_residual_accounting():
    """R5-1: residual 口径 —— 已修复/降级 advisory 不计 blocking；回退残留进 G4。"""
    drift = {'井中意识': _r4_drift_entry()}
    base = {'final_draft': {'1': R4_P1_PART1_TEXT, '2': R4_P1_PART_TEXT},
            'name_drift_dict': drift}
    fixed_text = R4_P1_PART_TEXT.replace('井中意识', '井中神族意识')
    # 1) spotfixed 且 count_after==0 → 无残留
    data_fixed = dict(base, final_draft={'1': R4_P1_PART1_TEXT, '2': fixed_text},
                      name_audit_log=[
        {'part': 2, 'wrong': '井中意识', 'right': '井中神族意识', 'count_before': 7,
         'count_after': 0, 'action': 'spotfixed', 'trigger': 'final_audit',
         'pair_source': 'issue_quote'}])
    na = summarize_name_audit(data_fixed)
    assert na['scanned'] == 2 and na['findings'] == 1 and na['fixed'] == 1
    assert na['residual_blocking'] == 0 and na['residual_advisory'] == 0
    # 2) unfixed_blocking（重审不过回退，正文仍有 7 处）→ blocking 残留
    data_bad = dict(base, name_audit_log=[
        {'part': 2, 'wrong': '井中意识', 'right': '井中神族意识', 'count_before': 7,
         'count_after': 7, 'action': 'unfixed_blocking', 'trigger': 'final_audit',
         'pair_source': 'issue_quote'}])
    na_bad = summarize_name_audit(data_bad)
    assert na_bad['residual_blocking'] == 1 and na_bad['fixed'] == 0
    assert evaluate_g4(_clean_report(), 20, na_bad)[0] is False
    # 3) 跨 Part 护栏降级 → advisory，不进 G4（漂移名只出现在陌生 Part 7）
    data_down = dict(base, final_draft={'7': '第七章里井中意识仅出现一次。'},
                     name_audit_log=[
        {'part': 7, 'wrong': '井中意识', 'right': '井中神族意识', 'count_before': 1,
         'count_after': 1, 'action': 'downgraded_advisory', 'trigger': 'final_audit',
         'pair_source': 'issue_quote'}])
    na_down = summarize_name_audit(data_down)
    assert na_down['residual_blocking'] == 0 and na_down['residual_advisory'] == 1
    assert evaluate_g4(_clean_report(), 20, na_down)[0] is True
    # 4) advisory 条目（闸未过/无指令佐证）→ 只告警
    adv_drift = {'井中意识': _r4_drift_entry(directive_confirmed=False,
                                             gates_passed=False, source='issue_character')}
    na_adv = summarize_name_audit(dict(base, name_drift_dict=adv_drift,
                                       name_audit_log=[]))
    assert na_adv['residual_advisory'] == 1 and na_adv['residual_blocking'] == 0
    assert is_blocking(adv_drift['井中意识']) is False
    # 5) resume 场景：上一轮 spotfixed 记录不得压制本轮新产生的 blocking 残留
    #    （latest-entry 语义：最后一条是 unfixed_blocking → 仍计 blocking）
    stale = dict(base, name_audit_log=[
        {'part': 2, 'wrong': '井中意识', 'right': '井中神族意识', 'count_before': 7,
         'count_after': 0, 'action': 'spotfixed', 'trigger': 'final_audit',
         'pair_source': 'issue_quote'},
        {'part': 2, 'wrong': '井中意识', 'right': '井中神族意识', 'count_before': 7,
         'count_after': 7, 'action': 'unfixed_blocking', 'trigger': 'final_audit',
         'pair_source': 'issue_quote'}])
    na_stale = summarize_name_audit(stale)
    assert na_stale['residual_blocking'] == 1, '旧 spotfixed 不得压制新残留'
    # 6) 空/脏 work JSON → 0 条不炸
    assert summarize_name_audit({})['residual_blocking'] == 0
    assert summarize_name_audit({'final_draft': 'x', 'name_drift_dict': 1,
                                 'name_audit_log': 'y'})['scanned'] == 0
    logger.info('[test_summarize_residual] PASS: 残留口径六场景 + 脏数据')


def test_recover_drift_dict_from_revision_log():
    """R5-1 词典来源 3: revision_log 的 name_spotfix 条目恢复（resume/历史 run）。"""
    from services.name_audit import recover_drift_dict_from_revision_log
    data = {'revision_log': [
        {'part': 2, 'type': 'name_spotfix', 'wrong_name': '井中意识',
         'right_name': '井中神族意识', 'revision_passed': True},
        {'part': 5, 'type': 'name_spotfix', 'wrong_name': '林战魂|古井意识',
         'right_name': '林战|井中神族意识', 'revision_passed': False},
        {'part': 6, 'revision_attempted': True},  # 非 name_spotfix → 跳过
        'not a dict',
    ]}
    drift = recover_drift_dict_from_revision_log(data)
    assert set(drift.keys()) == {'井中意识', '林战魂', '古井意识'}, drift
    assert drift['井中意识']['applied_verified'] is True
    assert drift['井中意识']['gates_passed'] is True
    assert drift['林战魂']['applied_verified'] is False
    assert drift['林战魂']['right'] == '林战' and drift['古井意识']['right'] == '井中神族意识'
    assert drift['井中意识']['parts_seen'] == [2]
    # 幂等恢复（只升不降，条目不丢）
    drift2 = recover_drift_dict_from_revision_log(data)
    assert set(drift2.keys()) == {'井中意识', '林战魂', '古井意识'}
    logger.info('[test_recover_dict] PASS: revision_log 恢复 + applied_verified 语义')


# ---------------- R5-4: consistency prompt 名册核对工序 ----------------

_ROSTER_STEPS = ('第一步·名册逐一核对', '第二步·判定', '第三步·引证')


def test_consistency_prompt_roster_procedure():
    """R5-4 验收: prompt 文件与内嵌 fallback 同含名册核对三句工序。

    prompt_loader 文件优先，但内嵌副本不同步会在缺文件的部署上行为分裂，
    故两处必须同改（02_review §2.4）。
    """
    prompts_file = _HERE.parents[2] / 'prompts' / 'consistency_review.txt'
    txt = prompts_file.read_text(encoding='utf-8')
    for step in _ROSTER_STEPS:
        assert step in txt, f'prompt 文件缺工序句: {step}'
    assert '写法→出现次数' in txt and '该写法在正文中的出现次数' in txt
    # 内嵌 fallback（load_prompt 的第二参）同改
    agent_src = (_BACKEND / 'core' / 'agents' / 'consistency_review_agent.py').read_text(encoding='utf-8')
    for step in _ROSTER_STEPS:
        assert step in agent_src, f'内嵌 fallback 缺工序句: {step}'
    # 代码侧 name_guidance 三条规则句未被触碰（test_round4_names.py:222 锁定）
    assert '名字不在名册内即为 P0 名称不一致' in agent_src
    # 净增受控：三句工序合计 ≤200 汉字（原文两句 44 字 → 211 字）
    import re
    section = txt[txt.index('1. **角色名称一致性'):txt.index('2. **角色状态连续性')]
    cjk = len(re.findall(r'[一-鿿]', section))
    assert 150 <= cjk <= 220, f'工序段汉字数异常: {cjk}'
    logger.info('[test_roster_procedure] PASS: 两处同含三句工序，净增受控')


if __name__ == '__main__':
    logger.info('=' * 60)
    logger.info('test_round5_name_audit.py —— Round 5 姓名审计回归（mock LLM）')
    logger.info('=' * 60)
    for fn in (test_derive_name_pairs_issue_quote_round4_p1_form,
               test_derive_name_pairs_quote_source_rejects_bad_pair,
               test_derive_name_pairs_quote_source_requires_directive,
               test_derive_name_pairs_quote_source_from_verdict,
               test_has_name_issue_includes_verdict,
               test_load_drift_dict_tolerates_dirty_data,
               test_record_name_pairs_create_and_monotonic,
               test_is_blocking_layers,
               test_rewrite_name_fix_before_judgement,
               test_spotfix_retry_bounded_once,
               test_final_audit_round4_replay_spotfix,
               test_final_audit_verified_entry_zero_llm,
               test_final_audit_detector_b_double_fire,
               test_audit_name_drift_dirty_data_fail_open,
               test_evaluate_g4_name_audit_quadrants,
               test_summarize_name_audit_residual_accounting,
               test_recover_drift_dict_from_revision_log,
               test_consistency_prompt_roster_procedure):
        fn()
        print(f'PASS {fn.__name__}')
    logger.info('\nALL PASS')
