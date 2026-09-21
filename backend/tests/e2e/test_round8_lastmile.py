"""
Round 8 最后一公里轮回归测试（test_round8_lastmile.py）
—— 全部离线断言，不调 LLM（call_llm/call_llm_json/create 均 patch 或脚本化假 agent）。

按 02_review.md 批准清单的 S 编号组织：
  S1（R8-1）退场角色合法形态分类器（最早退场 Part + 施事者否定式判定 + illegal
          span 作 SEARCH anchor + issue 字面片段第三来源）
  S2（R8-2）大纲级退场硬约束（Phase3 写前预检 + pass 启动预检 + 条目级归因改写）
  S3（R8-3）advisory 预算优先级重排（departed 优先 + residual_p0 join，公式不放宽）
  S4（R8-4）targeted_edit 加固（expected_min_len 接线 + aider 式失败反馈重试 +
          departed 类 oracle + 子集编辑模式）
  S5（R8-5）修复后 facts 回写（显式落 s.data + supersede 链）+ 审计 drift 禁令双注入
  S6（R8-6）converge_dossier.json（四合取逐条状态 + 未过 Part 断点 + suggested_next）

共享 fixture 形态取自 converge_20260921_214651/work.json 只读实证（林渊死亡链
F4_1→F8_1、part_outline Part 6/14/17 条目原文、final_draft span 唯一性）。

既支持 pytest 也支持 `python backend/tests/e2e/test_round8_lastmile.py` 直接跑。
"""
import asyncio
import importlib.util
import json
import os
import pathlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.logger import get_logger

logger = get_logger('test_round8_lastmile')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _load_verify_module():
    """从门禁脚本 import evaluate_g4 / summarize_name_audit（屏蔽 RUN_DIR.mkdir）。"""
    verify_path = _HERE / 'verify_step5_longform.py'
    spec = importlib.util.spec_from_file_location('verify_under_test_r8', verify_path)
    mod = importlib.util.module_from_spec(spec)
    orig_mkdir = pathlib.Path.mkdir
    pathlib.Path.mkdir = lambda self, *a, **k: None
    try:
        spec.loader.exec_module(mod)
    finally:
        pathlib.Path.mkdir = orig_mkdir
    return mod


_verify_mod = _load_verify_module()
evaluate_g4 = _verify_mod.evaluate_g4
summarize_name_audit = _verify_mod.summarize_name_audit

from core.established_facts import (  # noqa: E402
    EstablishedFacts, derive_departed_characters, earliest_departure_parts,
)
from core.name_registry import build_name_registry  # noqa: E402
from services.consistency_repair import (  # noqa: E402
    ConsistencyRepairer, TARGETED_EDIT_SYSTEM, _edit_subset_trigger_ok,
    _edit_trigger_ok, _issue_anchor, apply_name_spotfix, apply_safety_gates,
    derive_name_pairs, has_name_issue,
)
from services.name_audit import (  # noqa: E402
    AUDIT_LOG_KEY, DRIFT_DICT_KEY, audit_name_drift, is_blocking,
    record_name_pairs, scan_departed_reappearance,
)
from services.name_audit import (  # noqa: E402
    anchor_span, classify_departed_occurrences,
)
from services.review_aggregator import aggregate_review_results  # noqa: E402
from services.writing_phase_runners import (  # noqa: E402
    Phase4Runner, _residual_map_from_results,
)

# ---------------- 共享 fixture（converge_20260921_214651 真实数据形态） ----------------

R8_CHARACTERS = [
    {'name': '林尘', 'role': '主角', 'identity': '被家族封禁十年的"天煞孤星"'},
    {'name': '林渊', 'role': '核心配角/反派', 'identity': '林氏大长老，巡井三十年'},
    {'name': '林轻眉', 'role': '核心配角', 'identity': '林尘生母，守井人'},
    {'name': '林万重', 'role': '核心配角', 'identity': '林氏族长'},
]

# 林渊死亡链（converge work.json established_facts 实证逐字沿用）：
# F4_1（Part 4 首死，被无脸族主切断咽喉）→ F6_10 → F7_1 → F8_1（Part 8 确认死亡，
# "碑林在学他说话"）。derive_departed_characters last-write-wins 只留 Part 8，
# earliest_departure_parts 必须取 Part 4（R8-1 强制修正一）。
R8_DEPARTED_FACTS = {'version': 1, 'facts': [
    {'id': 'F4_1', 'part_num': 4, 'category': 'character', 'subject': '林渊',
     'predicate': '死亡', 'text': '林渊被无脸族主红雾切断咽喉杀死',
     'quote': '', 'superseded_by': 'F6_10'},
    {'id': 'F6_10', 'part_num': 6, 'category': 'character', 'subject': '林渊',
     'predicate': '死亡', 'text': '林渊尸身被反向吸力拽起坠入井中，井口闭合红雾尽散',
     'quote': '', 'superseded_by': 'F7_1'},
    {'id': 'F7_1', 'part_num': 7, 'category': 'character', 'subject': '林渊',
     'predicate': '死亡', 'text': '林渊已死，锁井阵认下林尘',
     'quote': '', 'superseded_by': 'F8_1'},
    {'id': 'F8_1', 'part_num': 8, 'category': 'character', 'subject': '林渊',
     'predicate': '死亡', 'text': '林渊已死，碑林在学他说话',
     'quote': '', 'superseded_by': None},
    # 林轻眉在场事实（探测器 B 的 active canonical 原料）
    {'id': 'F1_2', 'part_num': 1, 'category': 'character', 'subject': '林轻眉',
     'predicate': '位于', 'text': '林轻眉守在井边',
     'quote': '', 'superseded_by': None},
]}

R8_CHAR_NAMES = [c['name'] for c in R8_CHARACTERS]


def _r8_registry():
    return build_name_registry([{'name': n, 'role': c['role']}
                                for n, c in zip(R8_CHAR_NAMES, R8_CHARACTERS)])


# S3 预算 fixture：departed findings ×7（Part 9/12/13/14/15/17/20，count>=2）+
# canonical_absent ×2（Part 3/6 缺林轻眉）+ advisory ×1（Part 17 漂移名"林重"）
R8_BUDGET_DRAFT = {
    '3': '林渊在家中设宴，林尘作陪，无人提及轻眉。',
    '6': '林尘破阵法反伤林烈，林万重旁观。',
    '9': '林渊的名字被提起，林渊的碑影立在坟前，林轻眉望着远方。',
    '12': '玉简第二帧亮起，还是林渊，林渊的手捧命牌，林轻眉在旁。',
    '13': '本该写着林渊怎么饲井，碑影林渊笑了，林轻眉沉默。',
    '14': '晚晴。林渊的眼皮挣开一线，林渊整个人被拖进碑影胸口，林轻眉在远处。',
    '15': '林尘的脑中出现两幅画面，一幅里林渊捧着命牌，林轻眉守在井边。',
    '17': '林渊抬手，掌心裂纹里没有血，林渊跪倒在地，林轻眉与林重赶来。',
    '20': '林渊的笑声还在，碑林模仿着林渊的声线，林轻眉远去。',
}


def _budget_service(draft=None):
    fd = draft if draft is not None else dict(R8_BUDGET_DRAFT)
    return _FakeService({
        'name_registry': _r8_registry(),
        'character_state_track': {'林渊': 'Part8 死亡: 林渊已死，碑林在学他说话'},
        'established_facts': R8_DEPARTED_FACTS,
        'characters': [dict(c) for c in R8_CHARACTERS],
        'parts': dict(fd),
        'final_draft': dict(fd),
        'name_drift_dict': {},
    })


class _FakeService:
    """ConsistencyRepairer / Phase4Runner 所需的最小 service 面（离线）。"""

    def __init__(self, data=None):
        self.data = data if isinstance(data, dict) else {}
        self.work_id = 'r8_lastmile_test'
        self.vector_store = None
        self.progress_callback = lambda *a, **k: None
        self.saved_chunks = {}
        self.save_count = 0
        self.cfg = SimpleNamespace(part_count=2, target_word_count=10000)
        self.work_path = SimpleNamespace(parent=pathlib.Path('.'))

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
    """干净跑法的 review_report（R4-3 新键齐全）。"""
    return {
        'logic': {'avg_score': 8, 'pass': True, 'total_issues': 0, 'p0_count': 0,
                  'p1_count': 0, 'top_issue': '', 'parts_count': 20},
        'consistency': {'avg_score': 8, 'pass': True, 'total_issues': 0, 'p0_count': 0,
                        'p1_count': 0, 'top_issue': '', 'parts_count': 20},
        'parts': [], 'first_pass_total_p0': 0, 'residual_total_p0': 0,
        'revision_stats': {'attempted': 0, 'passed': 0, 'degraded': 0, 'spotfixed': 0},
    }


def _mock_state():
    return type('M', (), {'parts': {}, 'final_draft': {}})()


# ---------------- S1（R8-1）: earliest_departure_parts + 分类器 + 编辑桥接 ----------------

def test_earliest_departure_parts_takes_first_death():
    """S1 验收 1: 林渊账本（F4_1 首死）→ dep_part=4（非 last-write-wins 的 8）。"""
    ledger = earliest_departure_parts(R8_DEPARTED_FACTS, R8_CHAR_NAMES)
    assert '林渊' in ledger, ledger
    entry = ledger['林渊']
    assert entry['dep_part'] == 4, entry
    assert entry['earliest_record'].startswith('Part4 死亡'), entry
    assert '碑林在学他说话' not in entry['earliest_record'], '最早记录不得是最后一条'
    assert entry['last_record'].startswith('Part8 死亡'), entry
    assert entry['last_dep_part'] == 8, entry
    # 名册外角色不入账本；脏数据 fail-open
    assert earliest_departure_parts(R8_DEPARTED_FACTS, ['林尘']) == {}
    assert earliest_departure_parts(None, R8_CHAR_NAMES) == {}
    assert earliest_departure_parts(R8_DEPARTED_FACTS, None) == {}
    assert earliest_departure_parts('bad', R8_CHAR_NAMES) == {}
    assert earliest_departure_parts({'version': 1, 'facts': 'bad'}, R8_CHAR_NAMES) == {}
    assert earliest_departure_parts({'version': 1}, R8_CHAR_NAMES) == {}
    # 与 derive_departed_characters 语义并存（R6-6 探测器仍报 Part8，零改动）
    ef = EstablishedFacts()
    ef.from_dict(R8_DEPARTED_FACTS)
    legacy = derive_departed_characters(ef, R8_CHAR_NAMES)
    assert legacy['林渊'].startswith('Part8 死亡'), legacy
    logger.info('[test_earliest] PASS: dep_part=4（首死）+ last_record=Part8 双事实并存')


def test_classify_departed_occurrences_real_replay():
    """S1 验收 2/3: 分类器对 converge 真实正文形态的判定。

    Part 6「悬在红雾里的林渊动了」（最早退场 Part 修正后新扫描到的 Part）判
    illegal；尸体状态链（尸身 veto）判 legal；Part 14 引语归因与实体动词判
    illegal，他人提及/碑林模仿判 legal；illegal span 逐字在正文、count==1、≥15 字。
    """
    ledger = earliest_departure_parts(R8_DEPARTED_FACTS, R8_CHAR_NAMES)
    lin_yuan = ledger['林渊']
    part6 = ('井底发出一声尖啸。悬在红雾里的林渊动了，他喉间那道伤口还在渗血。'
             '林渊的尸身抬起右手，五指成爪，缓缓按向命纹。'
             '“井饿了。”林渊的声音带着笑。“你喂不喂？”')
    items = classify_departed_occurrences(part6, lin_yuan, part_num=6)
    illegal = [i for i in items if i['kind'] == 'illegal']
    legal = [i for i in items if i['kind'] == 'legal']
    assert any(i['span'].startswith('悬在红雾里的林渊动了') for i in illegal), illegal
    assert any('尸身抬起右手' in i['span'] for i in legal), legal
    assert any('的声音带着笑' in i['span'] for i in legal), legal
    for i in illegal:
        if i['span']:
            assert i['span'] in part6 and part6.count(i['span']) == 1, i
            assert len(i['span']) >= 15, i
        assert i['reason'] in ('entity_verb', 'quote_attribution'), i
    # 真实数据形态：短句（<15 字）内的 illegal 判定保留、span 置空（anchor 回落 issue 引文）
    short_sent = '一声轻响。悬在红雾里的林渊动了。他喉间的伤口还在。'
    short_items = classify_departed_occurrences(short_sent, lin_yuan, part_num=6)
    assert any(i['kind'] == 'illegal' and i['span'] == '' for i in short_items), short_items
    # Part 14：引语归因与实体动词 illegal；他人提及/碑林模仿 legal
    part14 = ('苏晚晴看见一枚发黑的残玉，失声惊呼。“晚晴。”林渊的眼皮挣开一线，'
              '眼底也是金色，“拿住。”碑影的巨口合下，黑雾轰然收紧，'
              '林渊整个人被拖进碑影胸口，只留下一缕飞灰。本该写着林渊怎么饲井的那一角，'
              '被人抹掉了。碑林模仿着他的声线冷笑，借他的形貌抬手压下。')
    items14 = classify_departed_occurrences(part14, lin_yuan, part_num=14)
    illegal14 = [i for i in items14 if i['kind'] == 'illegal']
    legal14 = [i for i in items14 if i['kind'] == 'legal']
    assert any('林渊的眼皮挣开一线' in i['span'] for i in illegal14), illegal14
    assert any('林渊整个人被拖进碑影胸口' in i['span'] for i in illegal14), illegal14
    assert any('怎么饲井' in i['span'] for i in legal14), legal14
    assert any('碑林模仿着' in i['span'] for i in legal14), legal14
    for i in illegal14:
        assert part14.count(i['span']) == 1 and len(i['span']) >= 15, i
    # 退场 Part 本身（<= dep_part）的死亡场景不算复现
    part4 = '林渊被无脸族主红雾切断咽喉杀死，尸身坠入井中。'
    assert classify_departed_occurrences(part4, lin_yuan, part_num=4) == []
    # 脏数据 fail-open
    assert classify_departed_occurrences('', lin_yuan) == []
    assert classify_departed_occurrences(part6, None) == []
    assert classify_departed_occurrences(part6, {}) == []
    assert classify_departed_occurrences('[Part 6 创作失败]', lin_yuan) == []
    logger.info('[test_classify] PASS: 引语归因/实体动词 illegal，尸体链/碑林模仿 legal')


def test_classify_default_legal_conservative():
    """S1 安全评估: 默认 legal（无法判定不错杀）——回忆/他人提及/容貌相似均合法。"""
    ledger = earliest_departure_parts(R8_DEPARTED_FACTS, R8_CHAR_NAMES)
    lin_yuan = ledger['林渊']
    text = ('族老们说起林渊伪造命牌的旧事，有人提起他坠井前的话。'
            '那少年的眉眼和林渊有七分像，众人睹物思人。')
    items = classify_departed_occurrences(text, lin_yuan, part_num=13)
    assert items and all(i['kind'] == 'legal' for i in items), items
    logger.info('[test_classify_default] PASS: 默认 legal，不错杀合法回忆/提及')


# 编辑桥接 fixture：Part 14 形态正文（林渊 ×2 illegal occurrence）+ Part 6 形态
R8_EDIT_PART14 = (
    '苏晚晴看见一枚发黑的残玉，失声惊呼。\n\n'
    '"晚晴。"林渊的眼皮挣开一线，眼底也是金色，"拿住。"\n\n'
    '碑影的巨口合下，黑雾轰然收紧，林渊整个人被拖进碑影胸口，只留下一缕飞灰。\n\n'
    '井底传来"第一千次轮回"的回响，苏晚晴死死攥住残玉，不肯松手。'
)

R8_EDIT_PART6 = (
    '井台之上，封神井已经饿了三十七年，井绳磨短了三尺，无人敢近前。\n\n'
    '一声轻响之后，悬在红雾里的林渊动了，他喉间的伤口还在渗血。'
)


def _edit_service(part_text, part_num):
    return _FakeService({
        'name_registry': _r8_registry(),
        'character_state_track': {'林渊': 'Part8 死亡: 林渊已死，碑林在学他说话'},
        'established_facts': R8_DEPARTED_FACTS,
        'characters': [dict(c) for c in R8_CHARACTERS],
        'parts': {str(part_num): part_text},
        'final_draft': {str(part_num): part_text},
        'name_drift_dict': {},
        'revision_log': [],
        'name_audit_log': [],
    })


def _p0_issue(dimension, description, character='', location='Part 14 井底场景'):
    return {'level': 'P0', 'dimension': dimension, 'character': character,
            'location': location, 'description': description, 'suggestion': ''}


def test_s1_departed_anchor_bridges_edit():
    """S1 验收 4a: Part 14 departed 类 P0（其一无引文）经 illegal span 补位 →
    触发定点编辑；user prompt 含退场角色修正规范段（§1.1.7 模板）与全文。"""
    service = _edit_service(R8_EDIT_PART14, 14)
    repairer = ConsistencyRepairer(service, _ScriptedAgent([_clean_logic(0)]),
                                   _ScriptedAgent([_clean_cons()]))
    issues = [
        _p0_issue('角色状态', '林渊已死严禁出场，文中却以实体被拖走'
                                '（原文：“林渊整个人被拖进碑影胸口”）'
                                '冲突：前文Part8林渊已死，碑林在学他说话', '林渊'),
        _p0_issue('状态连续性', '林渊于Part8已死亡且名册标注严禁出场，但在Part 14中'
                                '却实际睁眼、说话、递出残玉，属于已死角色实体出场', '林渊'),
    ]
    cons = _clean_cons(issues)
    spans = repairer._departed_anchors(R8_EDIT_PART14, 14)['林渊']
    assert len(spans) >= 2, spans
    ok, anchors = _edit_trigger_ok(R8_EDIT_PART14, _clean_logic(0), cons,
                                   {'林渊': spans})
    assert ok is True and len(anchors) == 2, anchors
    # 无 departed span 时第二个 issue 无 anchor → 不触发（R6-5 保守语义保持）
    ok_plain, _ = _edit_trigger_ok(R8_EDIT_PART14, _clean_logic(0), cons, {})
    assert ok_plain is False, '无 departed anchor 补位不得触发编辑'
    # 全链路：编辑被触发且 prompt 含修正规范段
    edit_calls = []

    def fake_call_llm(system, user, *a, **k):
        edit_calls.append((system, user))
        blocks = []
        for s in spans[:2]:
            r = s.replace('林渊', '碑林学他', 1)
            blocks.append(f'<<<<<<< SEARCH\n{s}\n=======\n{r}\n>>>>>>> REPLACE')
        return '\n'.join(blocks)

    with patch('services.consistency_repair.call_llm', side_effect=fake_call_llm):
        note = asyncio.run(repairer.maybe_repair_part(
            14, R8_EDIT_PART14, _clean_logic(0), cons, state_mock=_mock_state()))
    assert note.get('revision_passed') is True, note
    assert len(edit_calls) == 1, 'departed anchor 桥接后应触发恰好 1 次编辑'
    system, user = edit_calls[0]
    assert '退场角色修正规范' in user and '首次死亡 Part 4' in user, user[:400]
    assert '名册标注 Part 8 死亡' in user
    assert R8_EDIT_PART14 in user, '编辑 prompt 必须含全文'
    # departed_classified 留痕（trigger 非 final_audit，不进 G4）
    classified = [e for e in service.data[AUDIT_LOG_KEY]
                  if e['action'] == 'departed_classified']
    assert classified and all(e['trigger'] == 'repair' for e in classified)
    na = summarize_name_audit(service.data)
    assert na['residual_blocking'] == 0, na
    g4, _ = evaluate_g4(_clean_report(), 20, na)
    assert g4 is True
    logger.info('[test_s1_bridge] PASS: illegal span 补位触发编辑 + 规范段 + 留痕不进 G4')


def test_s1_issue_literal_anchor_source():
    """S1 验收 4b: Part 6 时间线 P0（无引文、非 departed 类）经 issue 字面片段
    第三来源获得 anchor → 触发编辑；名册名候选跳过。"""
    issues = [
        _p0_issue('角色状态/身份', '林渊名册标注严禁出场（原文：“悬在红雾里的林渊动了”）'
                                  '冲突：名册称已退场严禁出场', '', 'Part 6'),
        _p0_issue('时间线', '三十七年与前文三十年矛盾冲突：前文封神井饿了三十年',
                  '', 'Part 6'),
    ]
    # 字面片段来源单独验证（数量短语 三十七年 → 扩窗唯一 span）
    anchor2 = _issue_anchor(issues[1], R8_EDIT_PART6)
    assert anchor2 and '三十七年' in anchor2, anchor2
    assert R8_EDIT_PART6.count(anchor2) == 1 and len(anchor2) >= 15, anchor2
    ok, anchors = _edit_trigger_ok(R8_EDIT_PART6, _clean_logic(0), _clean_cons(issues))
    assert ok is True and len(anchors) == 2, anchors
    # 名册名候选跳过：字面候选命中名册名/退场名时不作 anchor
    name_issue = _p0_issue('时间线', '林渊的碑影立在坟前无人问津，时间线矛盾', '', 'Part 6')
    got = _issue_anchor(name_issue, R8_EDIT_PART6)
    assert not got or '林渊' not in got, got
    logger.info('[test_s1_literal] PASS: issue 字面片段第三来源 + 名册名跳过')


def test_s1_kill_switch_degrades_to_r6_behavior():
    """S1 验收 5: KML_DEPARTED_CLASSIFY=0 → 零分类调用，departed 类无引文
    issue 拿不到 span 补位，行为退化为 R6-5（不触发编辑落重写）。"""
    service = _edit_service(R8_EDIT_PART14, 14)
    repairer = ConsistencyRepairer(service, _ScriptedAgent([_clean_logic(0)]),
                                   _ScriptedAgent([_clean_cons()]))
    issues = [
        _p0_issue('角色状态', '林渊已死严禁出场，文中却以实体被拖走'
                                '（原文：“林渊整个人被拖进碑影胸口”）'
                                '冲突：前文Part8林渊已死', '林渊'),
        _p0_issue('状态连续性', '林渊于Part8已死亡且名册标注严禁出场，但在Part 14中'
                                '却实际睁眼、说话、递出残玉，属于已死角色实体出场', '林渊'),
    ]
    cons = _clean_cons(issues)
    os.environ['KML_DEPARTED_CLASSIFY'] = '0'
    try:
        assert repairer._departed_anchors(R8_EDIT_PART14, 14) == {}
        ok, _ = _edit_trigger_ok(R8_EDIT_PART14, _clean_logic(0), cons, {})
        assert ok is False, 'kill-switch 下 departed 类无引文 issue 不得触发编辑'
        edit_calls = []

        def fake_call_llm(system, user, *a, **k):
            edit_calls.append((system, user))
            return ''

        with patch('services.consistency_repair.call_llm', side_effect=fake_call_llm):
            note = asyncio.run(repairer.maybe_repair_part(
                14, R8_EDIT_PART14, _clean_logic(0), cons, state_mock=_mock_state()))
        assert not edit_calls, 'kill-switch 下零编辑调用（落全量重写）'
        assert note.get('revision_attempted') is True
    finally:
        os.environ.pop('KML_DEPARTED_CLASSIFY', None)
    logger.info('[test_s1_killswitch] PASS: kill-switch 退化 R6-5 行为')


def test_s1_targeted_edit_prompt_synced():
    """S1 验收 6: prompts/targeted_edit.txt 与内嵌 fallback 同含退场角色修正规范段。"""
    prompt_file = (_HERE.parent.parent.parent / 'prompts'
                   / 'targeted_edit.txt').read_text(encoding='utf-8')
    for constraint in ('退场角色修正规范', '碑林/碑影模仿其形貌或声音',
                       '不得删除该角色在剧情中的功能位', '不得引入角色名册之外的任何姓名'):
        assert constraint in TARGETED_EDIT_SYSTEM, constraint
        assert constraint in prompt_file, constraint
    logger.info('[test_s1_prompt_sync] PASS: 文件与 fallback 同步含退场修正规范')


# ---------------- S3（R8-3）: advisory 预算优先级重排 ----------------

def _called_parts(service, cons_agent):
    """cons_agent.calls 记录 part_text → 反查 Part 编号。"""
    text_to_part = {v: int(k) for k, v in service.data['final_draft'].items()}
    return [text_to_part[t] for t in cons_agent.calls]


def test_s3_budget_priority_with_residual_join():
    """S3 验收 1/2: departed ×7 + residual_map {12:1,14:3,17:4} → 预算 5 的
    rereviewed 顺序为 12/14/17/9/13（departed 内 residual 优先，同残留在 Part
    升序）；超预算记 budget_skipped（只告警）语义不变；departed 不进 G4。"""
    service = _budget_service()
    cons_agent = _ScriptedAgent([_clean_cons() for _ in range(9)])
    os.environ['KML_NAME_AUDIT_REREVIEW_BUDGET'] = '5'
    try:
        asyncio.run(Phase4Runner(service)._final_name_audit(
            service, [3, 6, 9, 12, 13, 14, 15, 17, 20], cons_agent,
            SimpleNamespace(final_draft={}),
            residual_map=_residual_map_from_results([
                {'part': 12, 'residual_p0': 1},
                {'part': 14, 'residual_p0': 3},
                {'part': 17, 'residual_p0': 4},
            ])))
    finally:
        os.environ.pop('KML_NAME_AUDIT_REREVIEW_BUDGET', None)
    assert _called_parts(service, cons_agent) == [12, 14, 17, 9, 13]
    skipped = [e for e in service.data[AUDIT_LOG_KEY] if e['action'] == 'budget_skipped']
    assert skipped, '超预算必须记 budget_skipped（只告警）'
    assert all(e['pair_source'] in ('departed_reappearance', 'canonical_absent', 'advisory')
               for e in skipped)
    na = summarize_name_audit(service.data)
    assert na['residual_blocking'] == 0, na
    g4, detail = evaluate_g4(_clean_report(), 20, na)
    assert g4 is True, detail
    logger.info('[test_s3_join] PASS: 12/14/17/9/13 顺序 + budget_skipped 语义不变 + 不进 G4')


def test_s3_kill_switch_and_no_join():
    """S3 验收 3/4: KML_DEPARTED_PRIORITY=0 → 旧排序 (tier, part)（Part 3/6 的
    canonical_absent 先于 departed）；默认排序但 residual_map=None 时 departed
    内按 Part 升序（9/12/13/14）。"""
    draft = dict(R8_BUDGET_DRAFT)
    os.environ['KML_DEPARTED_PRIORITY'] = '0'
    os.environ['KML_NAME_AUDIT_REREVIEW_BUDGET'] = '4'
    try:
        service = _budget_service(draft)
        cons_agent = _ScriptedAgent([_clean_cons() for _ in range(9)])
        asyncio.run(Phase4Runner(service)._final_name_audit(
            service, [3, 6, 9, 12, 13, 14, 15, 17, 20], cons_agent,
            SimpleNamespace(final_draft={}), residual_map={17: 4}))
        assert _called_parts(service, cons_agent) == [3, 6, 9, 12]
    finally:
        os.environ.pop('KML_DEPARTED_PRIORITY', None)
        os.environ.pop('KML_NAME_AUDIT_REREVIEW_BUDGET', None)
    service2 = _budget_service(draft)
    cons_agent2 = _ScriptedAgent([_clean_cons() for _ in range(9)])
    os.environ['KML_NAME_AUDIT_REREVIEW_BUDGET'] = '4'
    try:
        asyncio.run(Phase4Runner(service2)._final_name_audit(
            service2, [3, 6, 9, 12, 13, 14, 15, 17, 20], cons_agent2,
            SimpleNamespace(final_draft={}), residual_map=None))
    finally:
        os.environ.pop('KML_NAME_AUDIT_REREVIEW_BUDGET', None)
    assert _called_parts(service2, cons_agent2) == [9, 12, 13, 14]
    logger.info('[test_s3_killswitch] PASS: kill-switch 回退旧排序；None 时 Part 升序')


def test_s3_residual_map_helper():
    """S3 join 数据源：per_part_results → {part: residual_p0}（note 优先，否则
    首检 count_p0；降级结果不计入；脏条目跳过）。"""
    rmap = _residual_map_from_results([
        {'part': 6, 'logic_result': {'p0_count': 2, 'issues': []},
         'consistency_result': {'issues': []}},
        {'part': 10, 'residual_p0': 1,
         'logic_result': {'p0_count': 0, 'issues': []},
         'consistency_result': {'issues': []}},
        {'part': 12, 'logic_result': {'p0_count': 0, 'issues': []},
         'consistency_result': {'issues': [
             {'level': 'P0', 'dimension': '角色状态', 'description': 'x'}]}},
        {'part': 17, 'logic_result': {'_fallback': True, 'p0_count': 3},
         'consistency_result': {'verdict': '检查失败（降级评分）', 'issues': []}},
        'bad-entry', {'part': 'x'},
    ])
    assert rmap == {6: 2, 10: 1, 12: 1, 17: 0}, rmap
    assert _residual_map_from_results(None) == {}
    logger.info('[test_s3_rmap] PASS: note 优先 + 首检兜底 + 降级不计入')


if __name__ == '__main__':
    logger.info('=' * 60)
    logger.info('test_round8_lastmile.py —— Round 8 最后一公里轮回归（mock LLM）')
    logger.info('=' * 60)
    for fn in (test_earliest_departure_parts_takes_first_death,
               test_classify_departed_occurrences_real_replay,
               test_classify_default_legal_conservative,
               test_s1_departed_anchor_bridges_edit,
               test_s1_issue_literal_anchor_source,
               test_s1_kill_switch_degrades_to_r6_behavior,
               test_s1_targeted_edit_prompt_synced,
               test_s3_budget_priority_with_residual_join,
               test_s3_kill_switch_and_no_join,
               test_s3_residual_map_helper):
        fn()
        print(f'PASS {fn.__name__}')
    logger.info('\nALL PASS')
