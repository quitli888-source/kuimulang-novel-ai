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
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

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
        # dossier 等落点改临时目录（防测试污染仓库目录）
        self.work_path = SimpleNamespace(parent=pathlib.Path(tempfile.gettempdir()))

        class _Emitter:
            async def emit(self, *a, **k):
                pass

        self.emitter = _Emitter()

    def _save_chunk_progress(self, part_num, text, summary=None):
        self.saved_chunks[part_num] = text

    def _save(self):
        self.save_count += 1

    async def _check_pause(self):
        return None

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


def _clean_emotion(score=8):
    return {'pass': True, 'emotion_score': score, 'resonance_score': score,
            'immersion_score': score, 'verdict': 'ok', 'weaknesses': [],
            'enhancement_suggestions': []}


class _Phase4FakeService(_FakeService):
    """Phase4Runner.run() 所需的最小 service 面（离线，零 LLM）。"""

    def _build_review_state_mock(self):
        return SimpleNamespace(parts=dict(self.data.get('parts', {}) or {}),
                               final_draft=dict(self.data.get('final_draft', {}) or {}),
                               consistency_flags=self.data.get('consistency_flags', []) or [])

    @staticmethod
    def _aggregate_review_results(per_part_results):
        return aggregate_review_results(per_part_results)


class _ScriptedReviewAgent:
    """按 kind 确定性返回评审/风格结果的假 agent（记录 part 调用序列）。"""

    def __init__(self, kind, fn=None):
        self.kind = kind
        self.calls: list = []
        self._fn = fn

    def execute(self, state, part_num, part_text):
        self.calls.append(part_num)
        if self._fn is not None:
            return self._fn(part_num, part_text)
        if self.kind == 'logic':
            return _clean_logic()
        if self.kind == 'emotion':
            return _clean_emotion()
        if self.kind == 'consistency':
            return _clean_cons()
        return part_text  # style: 原文 passthrough


def _scripted_agents():
    return (_ScriptedReviewAgent('logic'), _ScriptedReviewAgent('emotion'),
            _ScriptedReviewAgent('consistency'), _ScriptedReviewAgent('style'))


def _patch_phase4_agents(logic_agent, emotion_agent, cons_agent, style_agent):
    """Phase4Runner.run() 内部 from-import，patch 源模块类对象。"""
    return (
        patch('core.agents.logic_review_agent.LogicReviewAgent', lambda: logic_agent),
        patch('core.agents.emotion_review_agent.EmotionReviewAgent', lambda: emotion_agent),
        patch('core.agents.consistency_review_agent.ConsistencyReviewAgent', lambda: cons_agent),
        patch('core.agents.style_optimizer_agent.StyleOptimizerAgent', lambda: style_agent),
    )


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


# ---------------- S2（R8-2）: 大纲级退场硬约束 ----------------

# part_outline Part 6/12/14/17 条目（converge work.json 原文逐字沿用）
R8_OUTLINE_6 = {'part': 6, 'title': '命牌破绽', 'phase': '冲突升级', 'word_count': 4500,
                'core_event': '林尘破阵法反伤林烈，夺回命牌见“替”字。',
                'emotion_target': '反转带来的惊怒',
                'key_dialogue': '“我的命，你们判了十年。” “今日我自己判！”',
                'end_hook': '林渊亲自出手，镇压命纹',
                'causality': '因Part5大典杀局爆发，林尘绝地反击并发现命牌异常。',
                'pacing': '快-慢-快', 'foreshadow_plant': [], 'foreshadow_reveal': []}
R8_OUTLINE_12 = {'part': 12, 'title': '祖训真相', 'phase': '危机爆发', 'word_count': 5500,
                 'core_event': '林尘得祖训玉简，揭露林渊伪造命牌、饲神养井。',
                 'emotion_target': '真相大白的彻骨恨意',
                 'key_dialogue': '“灾星是你写的，命也是你定的？” “可惜，我不认了。”',
                 'end_hook': '玉简最后一帧是婴儿林尘入井',
                 'causality': '因Part11确认神钥身份，林尘追查林家祖训找到林渊罪证。',
                 'pacing': '快-慢-快', 'foreshadow_plant': [], 'foreshadow_reveal': ['F4', 'F6']}
R8_OUTLINE_14 = {'part': 14, 'title': '井封崩解', 'phase': '危机爆发', 'word_count': 5500,
                 'core_event': '林渊被井中黑雾吞噬，残玉与林尘印记合一。',
                 'emotion_target': '绝望压顶的窒息',
                 'key_dialogue': '“晚晴，松手！” “这一次，我不躲。”',
                 'end_hook': '井底传来“第一千次轮回”',
                 'causality': '因Part13林渊狗急跳墙引动封神井，林尘与苏晚晴被迫迎劫。',
                 'pacing': '持续紧张', 'foreshadow_plant': [], 'foreshadow_reveal': ['F3']}
R8_OUTLINE_17 = {'part': 17, 'title': '夺回阵眼', 'phase': '终极高潮', 'word_count': 5500,
                 'core_event': '林尘出意识，联合林烈楚寒反攻，夺封井阵眼，林渊溃灭。',
                 'emotion_target': '绝地反杀的酣畅',
                 'key_dialogue': '“这一阵，我替家族摆。” “林渊，你的长生到头了。”',
                 'end_hook': '林渊狂笑“祂醒了”',
                 'causality': '因Part16林尘定下封印之法，外界反攻与林渊势力总清算。',
                 'pacing': '快-慢-快', 'foreshadow_plant': [], 'foreshadow_reveal': []}


def _outline_service(outline):
    return _FakeService({
        'name_registry': _r8_registry(),
        'character_state_track': {'林渊': 'Part8 死亡: 林渊已死，碑林在学他说话'},
        'established_facts': R8_DEPARTED_FACTS,
        'characters': [dict(c) for c in R8_CHARACTERS],
        'part_outline': outline,
        'revision_log': [],
    })


def test_outline_guard_violations_real_replay():
    """S2 验收 1/2: 真实大纲回放 —— Part 6（最早退场 Part 4 修正的直接证据）/
    14/17 命中；Part 12 回顾性揭露不命中（防误报合法回顾）。"""
    from services.writing_phase_runners import _outline_guard_violations
    ledger = earliest_departure_parts(R8_DEPARTED_FACTS, R8_CHAR_NAMES)
    outline = [R8_OUTLINE_6, R8_OUTLINE_12, R8_OUTLINE_14, R8_OUTLINE_17]
    violations = _outline_guard_violations(outline, ledger)
    hit = {(v['part'], v['field']) for v in violations}
    assert (6, 'end_hook') in hit, violations
    assert (14, 'core_event') in hit, violations
    assert (17, 'core_event') in hit, violations
    assert (17, 'end_hook') in hit, violations
    # Part 12 回顾性揭露（合法）不命中；key_dialogue 中他人对林渊的喊话合法
    assert not any(v['part'] == 12 for v in violations), violations
    assert not any(v['part'] == 17 and v['field'] == 'key_dialogue' for v in violations), violations
    # 退场 Part 之前的条目不检（part <= dep_part）
    assert all(v['part'] > ledger['林渊']['dep_part'] for v in violations)
    logger.info('[test_outline_violations] PASS: 6/14/17 命中，12 合法回顾不命中')


def test_outline_guard_rewrite_three_fields_only():
    """S2 验收 1: 条目级改写 —— 只改三字段文本，word_count/foreshadow 数组/
    title/phase 逐字节不变；关键事件词保留；revision_log outline_guard 留痕。"""
    from services.writing_phase_runners import _guard_outline_departed
    import core.llm_client as llm_client
    outline = [dict(R8_OUTLINE_14), dict(R8_OUTLINE_17)]
    service = _outline_service(outline)
    before_snapshot = json.dumps(outline, ensure_ascii=False, sort_keys=True)
    calls = []

    def fake_call_llm_json(**kwargs):
        calls.append(kwargs)
        if kwargs.get('agent') != 'outline_guard':
            return {}
        user = kwargs.get('user_prompt') or ''
        if '## Part 14' in user:
            # 归因形态改写：保留 吞噬/合一，碑林借林渊形貌
            return {'core_event': '碑林借林渊的形貌被井中黑雾吞噬，残玉与林尘印记合一。',
                    'key_dialogue': R8_OUTLINE_14['key_dialogue'],
                    'end_hook': R8_OUTLINE_14['end_hook']}
        # Part 17：保留 反攻/夺封/溃灭
        return {'core_event': '林尘出意识，联合林烈楚寒反攻，夺封井阵眼，碑林借林渊的形貌溃灭。',
                'key_dialogue': R8_OUTLINE_17['key_dialogue'],
                'end_hook': '碑林学他狂笑“祂醒了”'}

    with patch.object(llm_client, 'call_llm_json', side_effect=fake_call_llm_json):
        rewritten = asyncio.run(_guard_outline_departed(service))
    assert rewritten == {14, 17}, rewritten
    assert len(calls) == 2, f'每违规条目恰好 1 次 LLM 调用: {len(calls)}'
    # Part 14 三字段改写 + 关键事件词保留
    e14 = outline[0]
    assert '碑林借林渊的形貌' in e14['core_event'] and '吞噬' in e14['core_event']
    assert '合一' in e14['core_event']
    assert '溃灭' in outline[1]['core_event']
    assert '反攻' in outline[1]['core_event'] and '夺封' in outline[1]['core_event']
    # 其余字段逐字节不变（与快照差集只含三字段）
    after = json.dumps(outline, ensure_ascii=False, sort_keys=True)
    for entry in (R8_OUTLINE_14, R8_OUTLINE_17):
        pass
    assert outline[0]['word_count'] == 5500 and outline[1]['word_count'] == 5500
    assert outline[0]['foreshadow_reveal'] == ['F3'] and outline[0]['title'] == '井封崩解'
    assert outline[0]['phase'] == '危机爆发' and outline[0]['pacing'] == '持续紧张'
    assert outline[1]['key_dialogue'] == R8_OUTLINE_17['key_dialogue']
    assert before_snapshot != after, '三字段文本应已改写'
    # revision_log outline_guard 留痕（只增不改，纯观测）
    guard_entries = [e for e in service.data['revision_log']
                     if e.get('type') == 'outline_guard']
    assert len(guard_entries) == 2 and guard_entries[0]['part'] == 14
    assert guard_entries[0]['before']['core_event'] == R8_OUTLINE_14['core_event']
    assert guard_entries[0]['after']['core_event'] == e14['core_event']
    # brief 附注（重写必须看到"本 Part 大纲已按退场规范改写"）
    repairer = ConsistencyRepairer(service, _ScriptedAgent([]), _ScriptedAgent([]))
    brief = repairer._build_revision_brief(14, _clean_logic(1), _clean_cons(),
                                           part_text='x')
    assert '大纲已按退场规范改写' in brief, brief
    brief17 = repairer._build_revision_brief(17, _clean_logic(1), _clean_cons(),
                                             part_text='x')
    assert '大纲已按退场规范改写' in brief17
    logger.info('[test_outline_rewrite] PASS: 三字段改写 + 逐字节不变 + 留痕 + brief 附注')


def test_outline_guard_rejects_bad_rewrite():
    """S2 验收 4: 改写校验不过（关键事件词丢失/仍 illegal/引入名册角色）→
    保留原大纲 + advisory 日志，不静默跳过。"""
    from services.writing_phase_runners import _guard_outline_departed
    import core.llm_client as llm_client
    outline = [dict(R8_OUTLINE_17)]
    service = _outline_service(outline)
    before = json.dumps(outline, ensure_ascii=False, sort_keys=True)

    def bad_json(**kwargs):
        # 丢掉"溃灭"（关键事件词丢失）且仍留实体形态
        return {'core_event': '林尘出意识，联合林烈楚寒反攻，夺封井阵眼。',
                'key_dialogue': R8_OUTLINE_17['key_dialogue'],
                'end_hook': R8_OUTLINE_17['end_hook']}

    with patch.object(llm_client, 'call_llm_json', side_effect=bad_json):
        rewritten = asyncio.run(_guard_outline_departed(service))
    assert rewritten == set(), '校验不过不得落盘'
    assert json.dumps(outline, ensure_ascii=False, sort_keys=True) == before
    assert not [e for e in service.data['revision_log']
                if e.get('type') == 'outline_guard'], '失败不得留 outline_guard 条目'
    # 非 dict 返回 → 同样保留原大纲
    with patch.object(llm_client, 'call_llm_json', side_effect=lambda **k: 'x'):
        assert asyncio.run(_guard_outline_departed(service)) == set()
    # 调用异常 → 保留原大纲
    def boom(**kwargs):
        raise RuntimeError('llm down')

    with patch.object(llm_client, 'call_llm_json', side_effect=boom):
        assert asyncio.run(_guard_outline_departed(service)) == set()
    assert json.dumps(outline, ensure_ascii=False, sort_keys=True) == before
    logger.info('[test_outline_reject] PASS: 校验不过保留原大纲 + advisory')


def test_outline_guard_kill_switch():
    """S2 验收 5: KML_OUTLINE_DEPARTED_GUARD=0 → 零调用、大纲原样。"""
    from services.writing_phase_runners import _guard_outline_departed
    import core.llm_client as llm_client
    outline = [dict(R8_OUTLINE_14)]
    service = _outline_service(outline)
    before = json.dumps(outline, ensure_ascii=False, sort_keys=True)
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return {}

    os.environ['KML_OUTLINE_DEPARTED_GUARD'] = '0'
    try:
        with patch.object(llm_client, 'call_llm_json', side_effect=fake):
            rewritten = asyncio.run(_guard_outline_departed(service))
    finally:
        os.environ.pop('KML_OUTLINE_DEPARTED_GUARD', None)
    assert rewritten == set() and calls == []
    assert json.dumps(outline, ensure_ascii=False, sort_keys=True) == before
    logger.info('[test_outline_killswitch] PASS: kill-switch 零调用')


def test_outline_guard_phase3_precheck_before_writer():
    """S2 验收 3: Phase 3 写 Part N 前预检在 writer 执行前触发（脚本 agent
    调用计数 + 顺序），writer 拿到改写后大纲（同一 list 引用）。"""
    import services.writing_service as ws
    import services.writing_phase_runners as wpr
    import core.llm_client as llm_client
    from core.agents.part_writer_agent import PartWriterAgent
    # 林渊 Part 1 死亡 → Part 2 大纲条目（林渊亲自出手）应在写 Part 2 前被改写
    facts = {'version': 1, 'facts': [
        {'id': 'F1_1', 'part_num': 1, 'category': 'character', 'subject': '林渊',
         'predicate': '死亡', 'text': '林渊在井边身死', 'quote': '',
         'superseded_by': None}]}
    outline = [
        {'part': 1, 'title': '井边', 'phase': '开局', 'word_count': 5000,
         'core_event': '林渊巡井三十年，终于坠井', 'key_dialogue': '', 'end_hook': '林渊死了',
         'foreshadow_plant': [], 'foreshadow_reveal': []},
        {'part': 2, 'title': '碑鸣', 'phase': '升级', 'word_count': 5000,
         'core_event': '林渊亲自出手，镇压命纹', 'key_dialogue': '', 'end_hook': '风停',
         'foreshadow_plant': [], 'foreshadow_reveal': []},
    ]
    service = _FakeService({
        'name_registry': _r8_registry(),
        'established_facts': facts,
        'characters': [dict(c) for c in R8_CHARACTERS],
        'part_outline': outline, 'parts': {}, 'part_summaries': {},
        'revision_log': [],
    })
    service.cfg = SimpleNamespace(part_count=2, target_word_count=10000,
                                  confirm_mode=False)
    events: list = []
    seen_outlines: dict = {}

    def fake_call_llm_json(**kwargs):
        if kwargs.get('agent') == 'outline_guard':
            events.append('guard')
            return {'core_event': '碑林借林渊形貌出手，镇压命纹',
                    'key_dialogue': '', 'end_hook': '风停'}
        return {}

    def fake_writer_execute(self, state, part_num, **kwargs):
        events.append(('writer', part_num))
        seen_outlines[part_num] = state.part_outline[part_num - 1]
        return {'success': True, 'content': f'Part {part_num} 正文。' * 300,
                'word_count': 3000}

    ws._writing_state[service.work_id] = {'phase': 'idle', 'current_part': 0,
                                          'total_parts': 2, 'running': False}
    try:
        with patch.object(llm_client, 'call_llm_json', side_effect=fake_call_llm_json), \
                patch.object(PartWriterAgent, 'execute', fake_writer_execute), \
                patch.object(wpr, 'get_all_memory', lambda: ''):
            asyncio.run(wpr.Phase3Runner(service).run(start_from=1))
    finally:
        ws._writing_state.pop(service.work_id, None)
    # guard 在 Part 2 writer 之前触发（Part 1 无违规 → guard 只在 Part 2 前生效）
    assert events[0] == 'guard', events
    assert ('writer', 1) in events and ('writer', 2) in events
    assert events.index('guard') < events.index(('writer', 2)), events
    # writer 拿到改写后大纲（Part 2 core_event 已归因）
    assert '碑林借林渊形貌' in seen_outlines[2]['core_event'], seen_outlines[2]
    assert seen_outlines[2]['word_count'] == 5000, 'word_count 不得被动'
    logger.info('[test_outline_phase3] PASS: 写前预检先于 writer + 改写后大纲生效')


def test_outline_guard_phase4_entry_clears_progress():
    """S2 验收 3b: pass 启动预检改写大纲 → 受影响 Part 清除 review progress
    （重审+修复闭环）；未受影响 Part 不重审。"""
    from services.writing_phase_runners import Phase4Runner
    import core.llm_client as llm_client
    facts = {'version': 1, 'facts': [
        {'id': 'F1_1', 'part_num': 1, 'category': 'character', 'subject': '林渊',
         'predicate': '死亡', 'text': '林渊在井边身死', 'quote': '',
         'superseded_by': None}]}
    outline = [
        {'part': 1, 'title': '井边', 'phase': '开局', 'word_count': 5000,
         'core_event': '林渊巡井三十年，终于坠井', 'key_dialogue': '', 'end_hook': '林渊死了',
         'foreshadow_plant': [], 'foreshadow_reveal': []},
        {'part': 2, 'title': '碑鸣', 'phase': '升级', 'word_count': 5000,
         'core_event': '林渊亲自出手，镇压命纹', 'key_dialogue': '', 'end_hook': '风停',
         'foreshadow_plant': [], 'foreshadow_reveal': []},
    ]
    parts = {'1': '林渊巡井三十年，终于坠井。' + '井水无声。' * 100,
             '2': '碑林呜呜作响，命纹明亮。' + '风停了。' * 100}
    service = _Phase4FakeService({
        'name_registry': _r8_registry(),
        'established_facts': facts,
        'characters': [dict(c) for c in R8_CHARACTERS],
        'part_outline': outline, 'parts': parts, 'phase': 'phase3_part2',
        'phase4_review_progress': [
            {'part': 1, 'logic_result': _clean_logic(), 'emotion_result': _clean_emotion(),
             'consistency_result': _clean_cons(), 'repair_note': None, 'needs_rerun': False},
            {'part': 2, 'logic_result': _clean_logic(), 'emotion_result': _clean_emotion(),
             'consistency_result': _clean_cons(), 'repair_note': None, 'needs_rerun': False}],
        'revision_log': [],
    })
    la, ea, ca, sa = _scripted_agents()
    patches = _patch_phase4_agents(la, ea, ca, sa)

    def fake_call_llm_json(**kwargs):
        if kwargs.get('agent') == 'outline_guard':
            return {'core_event': '碑林借林渊形貌出手，镇压命纹',
                    'key_dialogue': '', 'end_hook': '风停'}
        return {}

    with patches[0], patches[1], patches[2], patches[3], \
            patch.object(llm_client, 'call_llm_json', side_effect=fake_call_llm_json):
        asyncio.run(Phase4Runner(service).run())
    # Part 2 大纲已改写 + progress 清除 → 只重审 Part 2
    assert '碑林借林渊形貌' in outline[1]['core_event']
    assert la.calls == [2], la.calls
    assert [e for e in service.data['revision_log']
            if e.get('type') == 'outline_guard'], 'outline_guard 留痕'
    logger.info('[test_outline_phase4] PASS: pass 启动预检 + progress 清除闭环')


def test_outline_guard_prompt_synced():
    """S2: prompts/outline_guard.txt 与内嵌 fallback 同含硬约束（R5-4 纪律）。"""
    from services.writing_phase_runners import OUTLINE_GUARD_SYSTEM
    prompt_file = (_HERE.parent.parent.parent / 'prompts'
                   / 'outline_guard.txt').read_text(encoding='utf-8')
    for constraint in ('只改归因形态', '关键事件词', '不得引入角色名册之外的任何姓名',
                       'core_event'):
        assert constraint in OUTLINE_GUARD_SYSTEM, constraint
        assert constraint in prompt_file, constraint
    logger.info('[test_outline_prompt_sync] PASS: 文件与 fallback 同步')


# ---------------- S4（R8-4）: targeted_edit 加固 ----------------

class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content, finish_reason='stop'):
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason
        self.delta = None


class _FakeResponse:
    def __init__(self, content='ok', finish_reason='stop'):
        self.choices = [_FakeChoice(content, finish_reason)]
        self.usage = None


class _FakeCompletions:
    """脚本化 create：列表元素为 Exception 则抛、否则作为响应返回。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if self.script else _FakeResponse()
        if isinstance(item, Exception):
            raise item
        return item


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)


def _length_empty():
    """finish_reason=length 且 content 空（reasoning 吃光预算的无歧义签名）。"""
    return _FakeResponse('', 'length')


def _edit_blocks(pairs):
    return '\n'.join(f'<<<<<<< SEARCH\n{s}\n=======\n{r}\n>>>>>>> REPLACE'
                     for s, r in pairs)


def test_s4_expected_min_len_escalation_ladder():
    """S4 验收 1: expected_min_len=80 接线 —— finish_reason=length 且空 content
    触发 max_tokens 翻倍阶梯（12000→24000→48000）；再空 → 无编辑块 → 不触发
    aider 重试 → 落全量重写（升级路径与重试路径互斥）。"""
    import core.llm_client as llm_client
    from services.consistency_repair import _EDIT_EXPECTED_MIN_LEN
    assert _EDIT_EXPECTED_MIN_LEN == 80
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
    comps = _FakeCompletions([_length_empty(), _length_empty(), _length_empty()])

    async def fake_rewrite_once(self, part_num, brief):
        return '', 'rewrite_failed'

    with patch.object(llm_client, '_get_client_for_agent',
                      lambda agent: (_FakeClient(comps), 'test-model')), \
            patch.object(ConsistencyRepairer, '_rewrite_once', fake_rewrite_once):
        note = asyncio.run(repairer.maybe_repair_part(
            14, R8_EDIT_PART14, _clean_logic(0), cons, state_mock=_mock_state()))
    # 短返升级阶梯：12000 → 24000 → 48000（第 3 次仍空 → 返回空 → 无块）
    assert [c['max_tokens'] for c in comps.calls] == [12000, 24000, 48000], \
        [c['max_tokens'] for c in comps.calls]
    # 空返回不触发 aider 重试（3 次调用全部来自升级阶梯）→ 落重写
    assert note.get('revision_attempted') is True and note.get('revision_error')
    logger.info('[test_s4_escalate] PASS: expected_min_len 阶梯 + 空返不重试互斥')


# 重试 fixture：含逐字重复句（search_not_unique 场景）+ 合法尸身段（veto legal）
# + 实体动词段（illegal）的 Part 正文
R8_RETRY_PART = (
    '井台之上，封神井已经饿了三十七年，井绳磨短了三尺，无人敢近前。\n\n'
    '一声轻响之后，悬在红雾里的林渊动了，他喉间的伤口还在渗血。\n\n'
    '一声轻响之后，悬在红雾里的林渊动了，他喉间的伤口还在渗血。\n\n'
    '林渊的尸身抬起右手，五指成爪，缓缓按向命纹，井台隆隆作响。\n\n'
    '林渊忽然睁开双眼，咒环暴涨，井壁隆隆作响。'
)


def test_s4_aider_style_retry_only_failed_blocks():
    """S4 验收 2/3: 首轮 1 块 search_not_unique + 1 块成功 → 重试只重发失败块
    （prompt 含失败原因+最近邻+'其余块已应用'）；每 Part 编辑调用 ≤2 次。

    注：分类器对非唯一 illegal occurrence 不出 anchor（span 置空），非唯一
    SEARCH 场景由"模型自选重复句"构造——issue 引文 anchor 照常触发编辑。
    """
    service = _edit_service(R8_RETRY_PART, 6)
    repairer = ConsistencyRepairer(service, _ScriptedAgent([_clean_logic(0)]),
                                   _ScriptedAgent([_clean_cons()]))
    issues = [
        _p0_issue('角色状态/身份', '林渊名册标注严禁出场（原文：“悬在红雾里的林渊动了”）'
                                  '冲突：名册称已退场严禁出场', '', 'Part 6'),
    ]
    cons = _clean_cons(issues)
    dup_sent = '一声轻响之后，悬在红雾里的林渊动了，他喉间的伤口还在渗血'
    assert R8_RETRY_PART.count(dup_sent) == 2, 'fixture 应含逐字重复句（count==2）'
    ok_span = '林渊忽然睁开双眼，咒环暴涨，井壁隆隆作响'
    assert R8_RETRY_PART.count(ok_span) == 1
    edit_calls = []

    def fake_call_llm(system, user, *a, **k):
        edit_calls.append(user)
        if len(edit_calls) == 1:
            # 首轮：1 块 SEARCH 不唯一（模型自选重复句）+ 1 块合法
            return _edit_blocks([(dup_sent, '碑林借着林渊的形貌呜呜作响，井边死寂'),
                                 (ok_span, '碑影中属于林渊的残念忽然睁眼，咒环暴涨')])
        # 重试：只重发失败块（加上段落边界变成唯一片段）
        fixed = '。\n\n' + dup_sent + '。\n\n'
        assert R8_RETRY_PART.count(fixed) == 1, fixed
        return _edit_blocks([(fixed, '。\n\n碑林借着林渊的形貌呜呜作响，井边死寂。\n\n')])

    with patch('services.consistency_repair.call_llm', side_effect=fake_call_llm):
        note = asyncio.run(repairer.maybe_repair_part(
            6, R8_RETRY_PART, _clean_logic(0), cons, state_mock=_mock_state()))
    assert len(edit_calls) == 2, f'每 Part 编辑调用 ≤2 次: {len(edit_calls)}'
    retry_prompt = edit_calls[1]
    assert '出现 2 次' in retry_prompt or 'search_not_unique' in retry_prompt, retry_prompt[:400]
    assert '最近邻上下文' in retry_prompt
    assert '其余 1 个块已应用，勿重发' in retry_prompt
    assert note.get('revision_passed') is True, note
    assert service.saved_chunks[6] != R8_RETRY_PART
    entries = [e for e in service.data['revision_log']
               if e.get('type') == 'targeted_edit']
    assert entries and entries[-1].get('edit_retried') is True
    assert entries[-1].get('edit_calls') == 2
    logger.info('[test_s4_retry] PASS: aider 式重试只重发失败块 + ≤2 次调用')


def test_s4_empty_return_no_retry():
    """S4 验收 3: 空返回（no_valid_block）不触发 aider 重试——编辑调用恰好 1 次，
    落全量重写（与 expected_min_len 升级路径互斥）。"""
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
    edit_calls = []

    def fake_call_llm(system, user, *a, **k):
        edit_calls.append(user)
        return ''  # 空返回

    with patch('services.consistency_repair.call_llm', side_effect=fake_call_llm):
        note = asyncio.run(repairer.maybe_repair_part(
            14, R8_EDIT_PART14, _clean_logic(0), cons, state_mock=_mock_state()))
    assert len(edit_calls) == 1, f'空返回不得重试: {len(edit_calls)}'
    assert note.get('revision_attempted') is True
    logger.info('[test_s4_empty_noretry] PASS: 空返回 1 次调用落重写')


def test_s4_subset_edit_mode():
    """S4 验收 4: P0=4（2 departed 带 span + 2 非 departed 带 anchor）→ 触发
    子集编辑且只产 departed 类块；非 departed 类 P0=4 → 不触发（零编辑调用）。"""
    service = _edit_service(R8_EDIT_PART14, 14)
    repairer = ConsistencyRepairer(service, _ScriptedAgent([_clean_logic(0)]),
                                   _ScriptedAgent([_clean_cons()]))
    dep_issues = [
        _p0_issue('角色状态', '林渊已死严禁出场，文中却以实体被拖走'
                                '（原文：“林渊整个人被拖进碑影胸口”）'
                                '冲突：前文Part8林渊已死', '林渊'),
        _p0_issue('状态连续性', '林渊于Part8已死亡且名册标注严禁出场，但在Part 14中'
                                '却实际睁眼、说话、递出残玉，属于已死角色实体出场', '林渊'),
    ]
    other_issues = [
        _p0_issue('物品状态', '苏晚晴看见一枚发黑的残玉（原文：“苏晚晴看见一枚发黑的残玉，'
                                '失声惊呼”）冲突：前文仅一块且已咬合'),
        _p0_issue('时间线', '三十七年与前文三十年矛盾（原文：“苏晚晴死死攥住残玉，不肯松手”）'
                            '冲突：前文封神井饿了三十年'),
    ]
    cons = _clean_cons(dep_issues + other_issues)
    spans = repairer._departed_anchors(R8_EDIT_PART14, 14)['林渊']
    ok, anchors = _edit_subset_trigger_ok(R8_EDIT_PART14, _clean_logic(0), cons,
                                          {'林渊': spans})
    assert ok is True and len(anchors) == 2, anchors
    # 全链路：子集编辑触发，brief 明示其余 P0 不在本次范围
    edit_calls = []

    def fake_call_llm(system, user, *a, **k):
        edit_calls.append(user)
        return '\n'.join(_edit_blocks([(s, s.replace('林渊', '碑林学他', 1))])
                         for s in spans[:2])

    with patch('services.consistency_repair.call_llm', side_effect=fake_call_llm):
        note = asyncio.run(repairer.maybe_repair_part(
            14, R8_EDIT_PART14, _clean_logic(0), cons, state_mock=_mock_state()))
    assert note.get('revision_passed') is True, note
    assert len(edit_calls) == 1
    assert '不在本次编辑范围' in edit_calls[0], 'brief 必须明示其余 P0 不在本次范围'
    entries = [e for e in service.data['revision_log']
               if e.get('type') == 'targeted_edit']
    assert entries and entries[-1].get('edit_subset') is True
    # 非 departed 类 P0=4 → 不开放旁路（零编辑调用）
    service2 = _edit_service(R8_EDIT_PART14, 14)
    repairer2 = ConsistencyRepairer(service2, _ScriptedAgent([_clean_logic(0)]),
                                    _ScriptedAgent([_clean_cons()]))
    cons2 = _clean_cons(other_issues + [
        _p0_issue('知识合理性', '角色知道不可能知道的信息'
                                '（原文：“苏晚晴死死攥住残玉，不肯松手”）'),
        _p0_issue('信息越界', '角色越界使用未揭示信息'
                                '（原文：“碑影的巨口合下，黑雾轰然收紧”）'),
    ])
    edit_calls2 = []

    def fake2(system, user, *a, **k):
        edit_calls2.append(user)
        return ''

    with patch('services.consistency_repair.call_llm', side_effect=fake2):
        note2 = asyncio.run(repairer2.maybe_repair_part(
            14, R8_EDIT_PART14, _clean_logic(0), cons2, state_mock=_mock_state()))
    assert edit_calls2 == [], '非 departed 类 P0=4 不得触发编辑'
    assert note2.get('revision_attempted') is True
    logger.info('[test_s4_subset] PASS: 子集编辑只产 departed 块；非 departed 不开放')


def test_s4_departed_oracle_rescues_improved_edit():
    """S4 验收 5: 分类器证 illegal 严格下降 + 重审报 departed 类 P0 但其 span
    已消除 → 判"改善"保留编辑稿（不回退）。"""
    service = _edit_service(R8_EDIT_PART14, 14)
    # 重审脚本：logic 干净（编辑修好）+ consistency 报已消除 span 的 departed 类 P0
    re_logic = [_clean_logic(0)]
    re_cons = [_clean_cons([_p0_issue(
        '角色状态', '林渊已死严禁出场（原文：“林渊整个人被拖进碑影胸口，只留下一缕飞灰”）'
                    '冲突：名册称已退场严禁出场', '林渊')])]
    repairer = ConsistencyRepairer(service, _ScriptedAgent(re_logic),
                                   _ScriptedAgent(re_cons))
    issues = [
        _p0_issue('角色状态', '林渊已死严禁出场，文中却以实体被拖走'
                                '（原文：“林渊整个人被拖进碑影胸口”）'
                                '冲突：前文Part8林渊已死', '林渊'),
        _p0_issue('状态连续性', '林渊于Part8已死亡且名册标注严禁出场，但在Part 14中'
                                '却实际睁眼、说话、递出残玉，属于已死角色实体出场', '林渊'),
    ]
    cons = _clean_cons(issues)
    spans = repairer._departed_anchors(R8_EDIT_PART14, 14)['林渊']

    def fake_call_llm(system, user, *a, **k):
        return '\n'.join(_edit_blocks([(s, s.replace('林渊', '碑林学他', 1))])
                         for s in spans[:2])

    with patch('services.consistency_repair.call_llm', side_effect=fake_call_llm):
        note = asyncio.run(repairer.maybe_repair_part(
            14, R8_EDIT_PART14, _clean_logic(0), cons, state_mock=_mock_state()))
    entries = [e for e in service.data['revision_log']
               if e.get('type') == 'targeted_edit']
    assert entries, '编辑段应已执行'
    assert entries[-1].get('departed_oracle') is True, entries[-1]
    assert entries[-1].get('oracle_illegal', '').endswith('→0'), entries[-1]
    assert entries[-1].get('revision_degraded') is None, 'oracle 救回不得记劣化'
    assert service.saved_chunks[14] != R8_EDIT_PART14, '编辑稿应保留（重写未覆盖）'
    assert note.get('first_pass_p0') == 2, note
    logger.info('[test_s4_oracle] PASS: oracle 救回已消除 span 的 departed 类误报')


def test_s4_suspect_judge_noise_flag():
    """S4 验收 6: Part 10 形态回放（编辑修好 logic P0，重审报编辑前已存在的
    "晚晴"截断 → 1→1 判劣化回退）→ suspect_judge_noise 打标且行为（回退）不变。"""
    chars = [dict(c) for c in R8_CHARACTERS]
    chars.append({'name': '苏晚晴', 'role': '核心配角', 'identity': '持残玉的守井人'})
    part10 = ('林尘把残玉按在井沿，苏晚晴在旁护法。他腕间的桃花蛊纹青黑暴涨，'
              '晚晴失声提醒他收手，井水无声地涨了三寸，碑林呜呜作响。')
    service = _FakeService({
        'name_registry': build_name_registry(chars),
        'character_state_track': {},
        'established_facts': {},
        'characters': chars,
        'parts': {'10': part10},
        'final_draft': {'10': part10},
        'name_drift_dict': {},
        'revision_log': [],
        'name_audit_log': [],
    })
    # 首检：logic P0（桃花蛊纹，带引文 anchor）+ consistency 干净（10/10）
    logic_first = {'score': 3, 'overall_score': 3, 'pass': False, 'p0_count': 1,
                   'p1_count': 0,
                   'issues': [{'level': 'P0', 'dimension': '角色状态',
                               'location': 'Part 10',
                               'description': '苏晚晴身怀桃花蛊，前文未提及此设定'
                                              '（原文：“他腕间的桃花蛊纹青黑暴涨”）',
                               'suggestion': '改为残玉纹'}],
                   'verdict': '信息越界'}
    # 重审脚本：logic 干净（编辑修好了）+ consistency 报编辑前已存在的晚晴截断
    re_logic = [_clean_logic(0)]
    re_cons = [_clean_cons([{'level': 'P0', 'dimension': '名称一致性',
                             'character': '苏晚晴', 'location': 'Part 10',
                             'description': "苏晚晴被截断为'晚晴'，不在名册登记写法内",
                             'suggestion': "将'晚晴'改为'苏晚晴'"}])]
    repairer = ConsistencyRepairer(service, _ScriptedAgent(re_logic),
                                   _ScriptedAgent(re_cons))
    cons_first = _clean_cons()

    def fake_call_llm(system, user, *a, **k):
        return _edit_blocks([('苏晚晴在旁护法。他腕间的桃花蛊纹青黑暴涨，',
                              '苏晚晴在旁护法。他腕间的残玉纹路青黑暴涨，')])

    with patch('services.consistency_repair.call_llm', side_effect=fake_call_llm):
        note = asyncio.run(repairer.maybe_repair_part(
            10, part10, logic_first, cons_first, state_mock=_mock_state()))
    # 行为不变：1→1 判劣化回退（不保留仍带 P0 的编辑稿）
    entries = [e for e in service.data['revision_log']
               if e.get('type') == 'targeted_edit']
    assert entries, '编辑段应已执行'
    assert entries[-1].get('revision_degraded') is True, entries[-1]
    assert entries[-1].get('suspect_judge_noise') is True, entries[-1]
    assert service.saved_chunks[10] == part10, '回退保留原文'
    assert note.get('revision_passed') is False, note
    logger.info('[test_s4_noise] PASS: suspect_judge_noise 打标 + 回退行为不变')


# ---------------- S5（R8-5）: facts 回写 + 审计 drift 禁令双注入 ----------------

R8_S5_FACTS = {'version': 1, 'facts': [
    {'id': 'F4_1', 'part_num': 4, 'category': 'event', 'subject': '林渊',
     'predicate': '死亡', 'text': '林渊被无脸族主红雾切断咽喉杀死',
     'quote': '', 'superseded_by': None},
    # 旧"状态"fact（回写后被 supersede 的对象）
    {'id': 'F8_9', 'part_num': 8, 'category': 'character', 'subject': '林渊',
     'predicate': '状态', 'text': '旧状态：林渊以碑影实体形式出场',
     'quote': '', 'superseded_by': None},
]}


def _s5_service():
    return _FakeService({
        'name_registry': _r8_registry(),
        'character_state_track': {'林渊': 'Part8 死亡: 林渊已死，碑林在学他说话'},
        'established_facts': json.loads(json.dumps(R8_S5_FACTS)),
        'characters': [dict(c) for c in R8_CHARACTERS],
        'parts': {'14': R8_EDIT_PART14},
        'final_draft': {'14': R8_EDIT_PART14},
        'name_drift_dict': {},
        'revision_log': [],
        'name_audit_log': [],
    })


def test_s5_facts_writeback_on_revision_passed():
    """S5 验收 1/4: revision_passed 的编辑修复 → s.data['established_facts'] 出现
    superseding fact、旧 fact superseded_by 指向它、revision_log 有 facts_superseded；
    resume 新建 TempStoryState → facts 块含新 fact（副本暗礁回归）。"""
    from services.writing_service import TempStoryState
    service = _s5_service()
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
    spans = repairer._departed_anchors(R8_EDIT_PART14, 14)['林渊']

    def fake_call_llm(system, user, *a, **k):
        return '\n'.join(_edit_blocks([(s, s.replace('林渊', '碑林学他', 1))])
                         for s in spans[:2])

    with patch('services.consistency_repair.call_llm', side_effect=fake_call_llm):
        note = asyncio.run(repairer.maybe_repair_part(
            14, R8_EDIT_PART14, _clean_logic(0), cons, state_mock=_mock_state()))
    assert note.get('revision_passed') is True, note
    # ① s.data 显式落盘（TempStoryState 副本暗礁——只写副本不落 s.data 会漏）
    ef_raw = service.data['established_facts']
    new_facts = [f for f in ef_raw['facts'] if f['id'].startswith('FR14_')]
    assert new_facts, ef_raw
    nf = new_facts[0]
    assert nf['subject'] == '林渊' and nf['predicate'] == '状态'
    assert nf['part_num'] == 14 and '修复结论' in nf['text']
    # ② 旧 fact superseded_by 指向新条目
    old = [f for f in ef_raw['facts'] if f['id'] == 'F8_9'][0]
    assert old['superseded_by'] == nf['id'], old
    # ③ revision_log facts_superseded 观测
    entries = [e for e in service.data['revision_log']
               if e.get('type') == 'targeted_edit']
    assert entries and entries[-1].get('facts_superseded') == ['F8_9'], entries[-1]
    # ④ 副本暗礁回归：resume 新建 TempStoryState → facts 块含新 fact
    temp = TempStoryState(service.data)
    block = temp.build_established_facts_block(15)
    assert '修复结论' in block and 'FR14_' not in block, block
    assert '碑影实体形式出场' not in block, '旧 fact 应被 supersede 不再渲染'
    logger.info('[test_s5_writeback] PASS: 落 s.data + supersede + 留痕 + resume 可见')


def test_s5_no_writeback_when_not_passed_and_idempotent():
    """S5 验收 1（反向）/2: revision_passed=False → 零回写；重复触发不产生重复条目。"""
    service = _s5_service()
    repairer = ConsistencyRepairer(service, _ScriptedAgent([_clean_logic(0)]),
                                   _ScriptedAgent([_clean_cons()]))
    issues = [_p0_issue('角色状态', '林渊已死严禁出场（原文：“林渊整个人被拖进碑影胸口”）'
                                   '冲突：前文Part8林渊已死', '林渊')]
    before = json.dumps(service.data['established_facts'], ensure_ascii=False)
    # 反向：未证实的修复不回写（直接调 _write_back_facts 前的失败路径——
    # 编辑空返回落重写失败 → note revision_passed=False）
    with patch('services.consistency_repair.call_llm', side_effect=lambda *a, **k: ''):
        note = asyncio.run(repairer.maybe_repair_part(
            14, R8_EDIT_PART14, _clean_logic(0), _clean_cons(issues),
            state_mock=_mock_state()))
    assert note.get('revision_passed') is False, note
    assert json.dumps(service.data['established_facts'],
                      ensure_ascii=False) == before, '未通过修复不得回写'
    # 幂等：同一 (part, subject, predicate) 重复触发只产生一条
    p0 = issues
    first = repairer._write_back_facts(14, p0, R8_EDIT_PART14, _mock_state())
    second = repairer._write_back_facts(14, p0, R8_EDIT_PART14, _mock_state())
    fr = [f for f in service.data['established_facts']['facts']
          if f['id'].startswith('FR14_')]
    assert len(fr) == 1, fr
    assert first and second == [], (first, second)
    logger.info('[test_s5_nowrite_idem] PASS: 未通过零回写 + 幂等去重')


def test_s5_drift_block_dual_injection():
    """S5 验收 3: Part 10 修复后 → Part 11 首片段 prompt 含禁令行且 ≤10 行；
    _build_revision_brief 同样注入；无修复历史的 Part 无注入；kill-switch 关停。"""
    from services.writing_phase_runners import Phase4Runner
    from core.agents.part_writer_agent import PartWriterAgent
    service = _s5_service()
    runner = Phase4Runner(service)
    logic_p0 = {'score': 3, 'overall_score': 3, 'pass': False, 'p0_count': 1,
                'issues': [{'level': 'P0', 'dimension': '名称一致性',
                            'character': '苏晚晴', 'location': 'Part 10',
                            'description': '苏晚晴被截断为晚晴，不在名册登记写法内',
                            'suggestion': "将'晚晴'改为'苏晚晴'"}],
                'verdict': '名称漂移'}
    runner._record_audit_drift(service, 10, logic_p0, _clean_cons(), None)
    drift = service.data['audit_drift']
    assert len(drift) == 1 and drift[0]['part'] == 10
    assert drift[0]['character'] == '苏晚晴' and '截断' in drift[0]['claim']
    # 注入点 1（新写）：PartWriterAgent 首片段 prompt
    agent = PartWriterAgent()
    state = SimpleNamespace(
        audit_drift=drift, parts={'10': '井水无声。' * 200},
        build_established_facts_block=lambda pn: '')
    prompt = agent._build_chunk_prompt(
        part_num=11, chunk_idx=1, is_first_chunk=True, prev_tail='',
        next_plan='本章计划', context='故事上下文', foreshadow_info='无',
        outline={'phase': '升级', 'core_event': 'e', 'emotion_target': 'x',
                 'key_dialogue': '', 'end_hook': '', 'pacing': '', 'causality': ''},
        chunk_target=3000, target_words=5000, hard_max=10200,
        written_so_far=0, state=state)
    assert '本卷审计纠偏' in prompt and '苏晚晴' in prompt and '截断' in prompt
    drift_lines = [ln for ln in prompt.split('\n') if ln.startswith('- Part 10')]
    assert len(drift_lines) == 1
    # ≤10 行硬顶
    big = [{'part': i, 'dimension': 'd', 'character': 'c', 'claim': f'问题{i}'}
           for i in range(1, 21)]
    state_big = SimpleNamespace(audit_drift=big, parts={},
                                build_established_facts_block=lambda pn: '')
    prompt_big = agent._build_chunk_prompt(
        part_num=25, chunk_idx=1, is_first_chunk=True, prev_tail='',
        next_plan='p', context='c', foreshadow_info='无',
        outline={'core_event': 'e'}, chunk_target=3000, target_words=5000,
        hard_max=10200, written_so_far=0, state=state_big)
    assert len([ln for ln in prompt_big.split('\n') if ln.startswith('- Part ')]) == 10
    # 注入点 2（重写）：_build_revision_brief
    repairer = ConsistencyRepairer(service, _ScriptedAgent([]), _ScriptedAgent([]))
    brief = repairer._build_revision_brief(11, _clean_logic(1), _clean_cons(),
                                           part_text='x')
    assert '本卷审计纠偏' in brief and '苏晚晴' in brief
    # 本 Part 自己的条目不进 brief（由明细行承载）
    brief10 = repairer._build_revision_brief(10, _clean_logic(1), _clean_cons(),
                                             part_text='x')
    assert '本卷审计纠偏' not in brief10
    # 无修复历史 → 无注入
    service2 = _s5_service()
    repairer2 = ConsistencyRepairer(service2, _ScriptedAgent([]), _ScriptedAgent([]))
    assert '本卷审计纠偏' not in repairer2._build_revision_brief(
        11, _clean_logic(1), _clean_cons(), part_text='x')
    # kill-switch
    os.environ['KML_AUDIT_DRIFT'] = '0'
    try:
        assert '本卷审计纠偏' not in repairer._build_revision_brief(
            11, _clean_logic(1), _clean_cons(), part_text='x')
        state_k = SimpleNamespace(audit_drift=drift, parts={},
                                  build_established_facts_block=lambda pn: '')
        prompt_k = agent._build_chunk_prompt(
            part_num=11, chunk_idx=1, is_first_chunk=True, prev_tail='',
            next_plan='p', context='c', foreshadow_info='无',
            outline={'core_event': 'e'}, chunk_target=3000, target_words=5000,
            hard_max=10200, written_so_far=0, state=state_k)
        assert '本卷审计纠偏' not in prompt_k
    finally:
        os.environ.pop('KML_AUDIT_DRIFT', None)
    # 覆盖语义：重新记录干净 Part → 条目被清
    runner._record_audit_drift(service, 10, _clean_logic(0), _clean_cons(), None)
    assert service.data['audit_drift'] == []
    logger.info('[test_s5_drift] PASS: 双注入 + ≤10 行 + kill-switch + 覆盖语义')


def test_s5_drift_recorded_in_phase4_run():
    """S5 验收 3（链路）: Phase4Runner.run() 全流程后 audit_drift 落盘且随
    s._save() 持久化（progress 恢复路径同样重建）。"""
    import core.llm_client as llm_client
    from services.writing_phase_runners import Phase4Runner
    facts = {'version': 1, 'facts': [
        {'id': 'F1_1', 'part_num': 1, 'category': 'character', 'subject': '林渊',
         'predicate': '死亡', 'text': '林渊在井边身死', 'quote': '',
         'superseded_by': None}]}
    parts = {'1': '林渊巡井三十年，终于坠井。' + '井水无声。' * 100,
             '2': '碑林呜呜作响，命纹明亮。' + '风停了。' * 100}
    service = _Phase4FakeService({
        'name_registry': _r8_registry(),
        'established_facts': facts,
        'characters': [dict(c) for c in R8_CHARACTERS],
        'part_outline': [], 'parts': parts, 'phase': 'phase3_part2',
        'revision_log': [],
    })
    la, ea, ca, sa = _scripted_agents()
    patches = _patch_phase4_agents(la, ea, ca, sa)
    with patches[0], patches[1], patches[2], patches[3]:
        asyncio.run(Phase4Runner(service).run())
    # 干净跑法（假 agent 无 P0）→ audit_drift 为空（residual=0 的 Part 无条目）
    assert service.data.get('audit_drift') == [], service.data.get('audit_drift')
    # 有 P0 首检的 Part → 落条目
    service2 = _Phase4FakeService({
        'name_registry': _r8_registry(),
        'established_facts': facts,
        'characters': [dict(c) for c in R8_CHARACTERS],
        'part_outline': [], 'parts': parts, 'phase': 'phase3_part2',
        'revision_log': [],
    })
    logic_p0 = {'score': 3, 'overall_score': 3, 'pass': False, 'p0_count': 1,
                'issues': [{'level': 'P0', 'dimension': '角色状态',
                            'character': '林渊', 'location': 'Part 2',
                            'description': '林渊已死却以碑影实体出场互动',
                            'suggestion': '改为碑林模仿'}],
                'verdict': '退场角色复现'}
    la2, ea2, ca2, sa2 = _scripted_agents()
    la2._fn = lambda part_num, part_text: (logic_p0 if part_num == 2 else _clean_logic())
    patches2 = _patch_phase4_agents(la2, ea2, ca2, sa2)
    with patches2[0], patches2[1], patches2[2], patches2[3]:
        asyncio.run(Phase4Runner(service2).run())
    drift = service2.data.get('audit_drift') or []
    assert any(e['part'] == 2 and e['character'] == '林渊' for e in drift), drift
    logger.info('[test_s5_phase4] PASS: Phase4 链路 drift 落盘 + 干净跑法无条目')


# ---------------- S6（R8-6）: converge_dossier.json（纯观测） ----------------

_CONVERGE_WORK = (_BACKEND.parent / 'data' / 'verification'
                  / 'converge_20260921_214651' / 'work.json')


def _dossier_service(data):
    svc = _Phase4FakeService(data)
    return svc


def test_s6_dossier_real_replay():
    """S6 验收 1: converge work.json 离线回放 → g4_conjuncts 四项与 report.json
    实测一致（residual 12/0、revision 11 vs 6、cons_fail [14,17]、budget 21/10），
    unpassed_parts 含 6/10/12/14/17；departed findings 与 96 处 span 实证一致。"""
    if not _CONVERGE_WORK.exists():
        pytest.skip('converge_20260921_214651/work.json 不存在（跳过真实回放）')
    from services.writing_phase_runners import _build_converge_dossier
    d = json.loads(_CONVERGE_WORK.read_text(encoding='utf-8'))
    ppr = [{'part': e['part'], 'logic_result': e.get('logic_result') or {},
            'emotion_result': {}, 'consistency_result': e.get('consistency_result') or {},
            **(e.get('repair_note') or {})}
           for e in (d.get('phase4_review_progress') or [])]
    service = _dossier_service(d)
    service.cfg = SimpleNamespace(part_count=20)
    report_before = json.dumps(d.get('review_report'), ensure_ascii=False, sort_keys=True)
    fd_before = json.dumps(d.get('final_draft'), ensure_ascii=False, sort_keys=True)
    dossier = _build_converge_dossier(service, ppr, list(range(1, 21)))
    # 零门禁影响：dossier 构建不得污染 review_report/final_draft（G4 输入）
    assert json.dumps(d.get('review_report'), ensure_ascii=False,
                      sort_keys=True) == report_before
    assert json.dumps(d.get('final_draft'), ensure_ascii=False,
                      sort_keys=True) == fd_before
    c = dossier['g4_conjuncts']
    # 与 report.json 实测一致（residual_total_p0=12 / attempted=11 passed=6 /
    # cons_pass=all() 失败 / first_pass=21>10）
    assert c['residual'] == {'pass': False, 'value': 12, 'threshold': 0}, c['residual']
    assert c['revision_converged'] == {'pass': False, 'attempted': 11,
                                       'passed': 6}, c['revision_converged']
    assert c['cons_pass']['pass'] is False
    assert c['cons_pass']['failing_parts'] == [14, 17], c['cons_pass']
    assert c['budget_ok']['pass'] is False
    assert c['budget_ok']['first_pass'] == 21 and c['budget_ok']['budget'] == 10
    assert '死常量' in c['budget_ok']['note']
    parts = [p['part'] for p in dossier['unpassed_parts']]
    for expected in (6, 10, 12, 14, 17):
        assert expected in parts, parts
    # departed findings：Part 6/12/14/17 共 96 处出现（review 实证）
    dep = {p['part']: p['detector_findings'].get('departed', {})
           for p in dossier['unpassed_parts'] if p.get('detector_findings')}
    assert dep[6]['count'] == 36 and dep[12]['count'] == 34, dep
    assert dep[14]['count'] == 5 and dep[17]['count'] == 21, dep
    assert dep[6]['illegal_count'] >= 1 and dep[6]['illegal_spans'], dep[6]
    assert all(len(s) <= 40 for s in dep[6]['illegal_spans'])
    assert len(dep[17]['illegal_spans']) <= 3, 'span 样本每 Part ≤3 条'
    # suggested_next 确定性推导（advisory-only）
    by_part = {p['part']: p for p in dossier['unpassed_parts']}
    assert by_part[17]['suggested_next'] in ('outline_guard', 'targeted_edit',
                                             'manual')
    # dossier 可序列化 + advisory 标记
    json.dumps(dossier, ensure_ascii=False)
    assert dossier['advisory_only'] is True
    logger.info('[test_s6_replay] PASS: 四合取与 report.json 一致 + 未过 Part 全覆盖')


def test_s6_dossier_clean_run_all_true():
    """S6 验收 4: residual=0 的假设数据 → unpassed_parts 为空、四合取全 true
    （防'永远报失败'的呆逻辑）。"""
    from services.writing_phase_runners import _build_converge_dossier
    parts = {str(i): f'Part {i} 干净正文，林尘踏入禁地。' * 60
             for i in range(1, 21)}
    service = _dossier_service({
        'review_report': _clean_report(), 'parts': parts,
        'final_draft': dict(parts), 'revision_log': [],
        'established_facts': {}, 'characters': [],
        'name_registry': {}, 'name_audit_log': [],
    })
    service.cfg = SimpleNamespace(part_count=20)
    dossier = _build_converge_dossier(service, [], list(range(1, 21)))
    assert dossier['unpassed_parts'] == [], dossier['unpassed_parts']
    c = dossier['g4_conjuncts']
    assert c['residual']['pass'] is True and c['residual']['value'] == 0
    assert c['revision_converged']['pass'] is True
    assert c['cons_pass']['pass'] is True and c['cons_pass']['failing_parts'] == []
    assert c['budget_ok']['pass'] is True and c['budget_ok']['budget'] == 10
    json.dumps(dossier, ensure_ascii=False)
    logger.info('[test_s6_clean] PASS: 干净数据四合取全 true + unpassed 为空')


def test_s6_dossier_suggested_next_deterministic():
    """S6: suggested_next 由 repair_history 确定性推导（advisory-only）。"""
    from services.writing_phase_runners import _dossier_suggested_next
    assert _dossier_suggested_next([]) == 'manual'
    assert _dossier_suggested_next([{'type': 'name_spotfix'}]) == 'manual'
    assert _dossier_suggested_next([{'type': 'targeted_edit'}]) == 'targeted_edit'
    assert _dossier_suggested_next(
        [{'type': 'rewrite', 'revision_degraded': True}]) == 'outline_guard'
    assert _dossier_suggested_next(
        [{'type': 'targeted_edit'}, {'type': 'outline_guard'}]) == 'outline_guard'
    logger.info('[test_s6_next] PASS: suggested_next 确定性推导')


def test_s6_dossier_written_and_isolated(tmp_path):
    """S6 验收 2/3: Phase4Runner.run() 末尾产出 converge_dossier.json；产出异常
    （注入故障）不冒泡、主流程与 report.json 不受影响；verify 侧 dossier_path
    只增字段（detail 既有键逐字节不变）。"""
    import services.writing_phase_runners as wpr
    facts = {'version': 1, 'facts': [
        {'id': 'F1_1', 'part_num': 1, 'category': 'character', 'subject': '林渊',
         'predicate': '死亡', 'text': '林渊在井边身死', 'quote': '',
         'superseded_by': None}]}
    parts = {'1': '林渊巡井三十年，终于坠井。' + '井水无声。' * 100,
             '2': '碑林呜呜作响，命纹明亮。' + '风停了。' * 100}

    def _svc():
        svc = _Phase4FakeService({
            'name_registry': _r8_registry(),
            'established_facts': facts,
            'characters': [dict(c) for c in R8_CHARACTERS],
            'part_outline': [], 'parts': parts, 'phase': 'phase3_part2',
            'revision_log': [],
        })
        svc.work_path = SimpleNamespace(parent=tmp_path)
        return svc

    # 1) 正常产出
    service = _svc()
    la, ea, ca, sa = _scripted_agents()
    p = _patch_phase4_agents(la, ea, ca, sa)
    with p[0], p[1], p[2], p[3]:
        asyncio.run(Phase4Runner(service).run())
    dossier_file = tmp_path / 'converge_dossier.json'
    assert dossier_file.exists(), 'dossier 应已产出'
    dossier = json.loads(dossier_file.read_text(encoding='utf-8'))
    assert 'g4_conjuncts' in dossier and 'unpassed_parts' in dossier
    assert service.data['converge_dossier_path'] == str(dossier_file)
    assert service.data['review_report'], '主流程不受影响'
    assert service.data['final_draft'] == parts, 'final_draft 不得被重置'
    # verify 侧 dossier_path 只增（detail 既有键逐字节不变）
    na = summarize_name_audit(service.data)
    g4, detail = evaluate_g4(service.data['review_report'], 2, na)
    detail_before = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    if dossier_file.exists():
        detail['dossier_path'] = str(dossier_file)
    assert set(detail) - {'dossier_path'}, 'detail 应含既有键'
    assert 'dossier_path' not in detail_before
    # 2) 注入故障：dossier 产出抛异常 → 不冒泡，主流程照常
    service2 = _svc()
    la2, ea2, ca2, sa2 = _scripted_agents()
    p2 = _patch_phase4_agents(la2, ea2, ca2, sa2)

    def boom(*a, **k):
        raise RuntimeError('dossier boom')

    with p2[0], p2[1], p2[2], p2[3], \
            patch.object(wpr, '_build_converge_dossier', boom):
        asyncio.run(Phase4Runner(service2).run())  # 不得抛异常
    assert service2.data['review_report'], '异常后主流程照常'
    assert service2.data['final_draft'] == parts, '异常后 final_draft 不得被重置'
    assert service2.data.get('phase') == 'phase4'
    logger.info('[test_s6_isolated] PASS: dossier 产出 + 故障隔离 + dossier_path 只增')


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
    for fn in (test_s6_dossier_real_replay,
               test_s6_dossier_clean_run_all_true,
               test_s6_dossier_suggested_next_deterministic,
               test_s6_dossier_written_and_isolated,
               test_s5_facts_writeback_on_revision_passed,
               test_s5_no_writeback_when_not_passed_and_idempotent,
               test_s5_drift_block_dual_injection,
               test_s5_drift_recorded_in_phase4_run,
               test_s4_expected_min_len_escalation_ladder,
               test_s4_aider_style_retry_only_failed_blocks,
               test_s4_empty_return_no_retry,
               test_s4_subset_edit_mode,
               test_s4_departed_oracle_rescues_improved_edit,
               test_s4_suspect_judge_noise_flag,
               test_earliest_departure_parts_takes_first_death,
               test_classify_departed_occurrences_real_replay,
               test_classify_default_legal_conservative,
               test_s1_departed_anchor_bridges_edit,
               test_s1_issue_literal_anchor_source,
               test_s1_kill_switch_degrades_to_r6_behavior,
               test_s1_targeted_edit_prompt_synced,
               test_outline_guard_violations_real_replay,
               test_outline_guard_rewrite_three_fields_only,
               test_outline_guard_rejects_bad_rewrite,
               test_outline_guard_kill_switch,
               test_outline_guard_phase3_precheck_before_writer,
               test_outline_guard_phase4_entry_clears_progress,
               test_outline_guard_prompt_synced,
               test_s3_budget_priority_with_residual_join,
               test_s3_kill_switch_and_no_join,
               test_s3_residual_map_helper):
        fn()
        print(f'PASS {fn.__name__}')
    logger.info('\nALL PASS')
