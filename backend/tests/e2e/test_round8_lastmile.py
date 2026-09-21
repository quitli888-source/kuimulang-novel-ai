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

from core.established_facts import EstablishedFacts  # noqa: E402
from core.name_registry import build_name_registry  # noqa: E402
from services.consistency_repair import (  # noqa: E402
    ConsistencyRepairer, apply_name_spotfix, apply_safety_gates,
    derive_name_pairs, has_name_issue,
)
from services.name_audit import (  # noqa: E402
    AUDIT_LOG_KEY, DRIFT_DICT_KEY, audit_name_drift, is_blocking,
    record_name_pairs, scan_departed_reappearance,
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
    for fn in (test_s3_budget_priority_with_residual_join,
               test_s3_kill_switch_and_no_join,
               test_s3_residual_map_helper):
        fn()
        print(f'PASS {fn.__name__}')
    logger.info('\nALL PASS')
