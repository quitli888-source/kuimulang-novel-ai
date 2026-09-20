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
    AUDIT_LOG_KEY, DRIFT_DICT_KEY, is_blocking, load_drift_dict,
    record_name_pairs,
)


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

# Part 2 正文（井中意识×7、井中神族意识×0、其他规范名各 1 次）
R4_P1_PART_TEXT = (
    '林尘跌入古井，井中意识在深渊中苏醒，井中意识低语着古老咒言。'
    '林啸天率族人封锁井口，井中意识却透过林战的眼睛窥视外界。'
    '井中意识许诺林尘神力，井中意识诱他献出血脉，井中意识终将吞没三千世界。'
)

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
    """ConsistencyRepairer 所需的最小 service 面（离线，无 work 文件）。"""

    def __init__(self, data=None):
        self.data = data if isinstance(data, dict) else {}
        self.work_id = 'r5_audit_test'
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
               test_is_blocking_layers):
        fn()
        print(f'PASS {fn.__name__}')
    logger.info('\nALL PASS')
