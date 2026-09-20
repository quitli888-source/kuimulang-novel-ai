"""
Round 4 门禁口径回归测试（R4-3 G4 混合门禁：终稿 residual 口径 + 首检率预算护栏）
—— 全部离线断言，不调 LLM。

覆盖（02_review.md §S4 验收标准）：
  (a) 无修复跑法 report 与改前一致（parts[] 既有字段零改动、无 revision 键时
      输出逐字节一致；既有聚合测试语义不回退）
  (b) 构造 passed 修复 → residual_total_p0=0、revision_stats.attempted==passed
  (c) 构造 failed 修复 → residual>0 且 evaluate_g4 为 False
  (d) first_pass 超预算 → False、未超 → True（含冒烟 PARTS<10 不设限与
      env KML_MAX_FIRST_PASS_P0 覆盖/关闭）
  (e) evaluate_g4 纯函数直接可测（从门禁脚本 import，屏蔽 RUN_DIR.mkdir 副作用）
  revision_stats 汇总（attempted/passed/degraded/spotfixed，从 entry 的
  revision_* 字段汇总、不读 revision_log）

既支持 pytest 也支持 `python backend/tests/e2e/test_round4_gates.py` 直接跑。
"""
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

from core.logger import get_logger

logger = get_logger('test_round4_gates')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _load_verify_module():
    """从门禁脚本 import evaluate_g4 / first_pass_p0_budget（屏蔽 RUN_DIR.mkdir 副作用）。"""
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
first_pass_p0_budget = verify_mod.first_pass_p0_budget

# 无 revision 键时 parts[] 的既有字段集合（R1-J 纪律：逐字节一致）
_LEGACY_PART_KEYS = {'part', 'logic_score', 'emotion_score', 'consistency_score',
                     'p0_issues', 'p1_issues', 'summary'}


def _clean_result(score=8):
    return {'overall_score': score, 'pass': True, 'p0_count': 0, 'p1_count': 0,
            'issues': [], 'verdict': 'ok'}


def _report(logic_p0=0, cons_p0=0, parts_count=20, avg_logic=8, cons_pass=True,
            first_pass_total_p0=0, residual_total_p0=0,
            revision_stats=None, legacy=False):
    """构造 review_report（legacy=True 时刻意去掉 R4-3 新键，模拟旧格式）。"""
    rep = {
        'logic': {'avg_score': avg_logic, 'pass': avg_logic >= 6,
                  'total_issues': logic_p0, 'p0_count': logic_p0, 'p1_count': 0,
                  'top_issue': '', 'parts_count': parts_count},
        'consistency': {'avg_score': 8, 'pass': cons_pass,
                        'total_issues': cons_p0, 'p0_count': cons_p0, 'p1_count': 0,
                        'top_issue': '', 'parts_count': parts_count},
        'parts': [],
    }
    if not legacy:
        rep['first_pass_total_p0'] = first_pass_total_p0
        rep['residual_total_p0'] = residual_total_p0
        rep['revision_stats'] = revision_stats or {
            'attempted': 0, 'passed': 0, 'degraded': 0, 'spotfixed': 0}
    return rep


# ---------------- 聚合器：无修复跑法零改动 ----------------

def test_aggregator_no_revision_output_unchanged():
    """R4-3 验收 (a): 无 revision 键时 parts[] 输出与改前逐字节一致。"""
    from services.review_aggregator import aggregate_review_results
    per_part = [{
        'part': 1,
        'logic_result': {'overall_score': 5, 'p0_count': 2, 'p1_count': 1, 'issues': []},
        'emotion_result': {'emotion_score': 7, 'enhancement_suggestions': [], 'weaknesses': []},
        'consistency_result': {'overall_score': 8, 'p0_count': 0, 'p1_count': 0, 'issues': []},
    }]
    report = aggregate_review_results(per_part)
    p0 = report['parts'][0]
    assert set(p0.keys()) == _LEGACY_PART_KEYS, f'无修复时 parts[] 字段被改动: {set(p0.keys())}'
    assert 'revision_attempted' not in p0 and 'first_pass_p0' not in p0
    assert 'residual_p0' not in p0 and 'revision_degraded' not in p0
    # 既有聚合语义不回退
    assert report['logic']['p0_count'] == 2 and report['logic']['p1_count'] == 1
    assert report['consistency']['p0_count'] == 0
    # summary 新键始终存在且值正确（首检=残留=2，无修复）
    assert report['first_pass_total_p0'] == 2
    assert report['residual_total_p0'] == 2
    assert report['revision_stats'] == {'attempted': 0, 'passed': 0,
                                        'degraded': 0, 'spotfixed': 0}
    logger.info('[test_agg_no_revision] PASS: 无修复跑法 parts[] 零改动，summary 新键正确')


# ---------------- 聚合器：passed / failed / degraded / spotfixed ----------------

def test_aggregator_passed_repair_residual_zero():
    """R4-3 验收 (b): passed 修复 → residual_total_p0=0、attempted==passed、
    first_pass_p0 取 note 携带的真实首检数（修复通过时结果已被重审值替换）。"""
    from services.review_aggregator import aggregate_review_results
    per_part = [{
        'part': 2,
        'logic_result': _clean_result(),      # 重审结果（P0 归零）
        'emotion_result': {'emotion_score': 9},
        'consistency_result': _clean_result(),  # 重审结果
        'revision_attempted': True, 'revision_passed': True,
        'first_pass_p0': 4,                   # 真实首检数（p0_before）
    }]
    report = aggregate_review_results(per_part)
    entry = report['parts'][0]
    assert entry['revision_attempted'] is True and entry['revision_passed'] is True
    assert entry['first_pass_p0'] == 4, 'first_pass_p0 必须取 note 真实首检数'
    assert entry['residual_p0'] == 0, 'passed 修复 residual = 重审 P0 = 0'
    assert report['first_pass_total_p0'] == 4
    assert report['residual_total_p0'] == 0
    assert report['revision_stats']['attempted'] == 1
    assert report['revision_stats']['passed'] == 1
    logger.info('[test_agg_passed] PASS: passed 修复 residual=0、首检数不丢失')


def test_aggregator_failed_repair_keeps_first_pass_issues():
    """R4-3 验收 (c): failed 修复 → residual = note['residual_p0']，
    p0_issues 保持首检值（现状不变）。"""
    from services.review_aggregator import aggregate_review_results
    per_part = [{
        'part': 2,
        'logic_result': {'overall_score': 2, 'pass': False, 'p0_count': 3,
                         'issues': [], 'verdict': '仍有矛盾'},
        'emotion_result': {'emotion_score': 9},
        'consistency_result': {'overall_score': 4, 'pass': False,
                               'issues': [{'level': 'P0', 'dimension': '名称一致性',
                                           'description': '名字漂移'}]},
        'revision_attempted': True, 'revision_passed': False,
        'residual_p0': 3, 'first_pass_p0': 4,
    }]
    report = aggregate_review_results(per_part)
    entry = report['parts'][0]
    assert entry['first_pass_p0'] == 4 and entry['residual_p0'] == 3
    assert len(entry['p0_issues']) == 4, 'failed 修复 p0_issues 必须保持首检值'
    assert report['residual_total_p0'] == 3
    assert report['revision_stats']['attempted'] == 1
    assert report['revision_stats']['passed'] == 0
    g4, detail = evaluate_g4(report, 20)
    assert g4 is False, 'residual>0 必须 FAIL'
    assert detail['residual_total_p0'] == 3
    logger.info('[test_agg_failed] PASS: failed 修复 residual=3、evaluate_g4=False')


def test_aggregator_revision_stats_degraded_and_spotfixed():
    """R4-5: revision_stats 汇总 degraded / spotfixed（从 entry revision_* 字段）。"""
    from services.review_aggregator import aggregate_review_results
    per_part = [
        {'part': 2, 'logic_result': _clean_result(), 'emotion_result': {},
         'consistency_result': _clean_result(),
         'revision_attempted': True, 'revision_passed': True,
         'revision_spotfixed': True, 'first_pass_p0': 1},
        {'part': 5, 'logic_result': _clean_result(), 'emotion_result': {},
         'consistency_result': _clean_result(),
         'revision_attempted': True, 'revision_passed': False,
         'revision_degraded': True, 'residual_p0': 4, 'first_pass_p0': 4},
        {'part': 9, 'logic_result': _clean_result(), 'emotion_result': {},
         'consistency_result': _clean_result()},
    ]
    report = aggregate_review_results(per_part)
    assert report['revision_stats'] == {'attempted': 2, 'passed': 1,
                                        'degraded': 1, 'spotfixed': 1}
    assert report['first_pass_total_p0'] == 5
    assert report['residual_total_p0'] == 4
    logger.info('[test_agg_stats] PASS: revision_stats 四字段汇总正确')


# ---------------- evaluate_g4 四象限 + 预算 ----------------

def test_evaluate_g4_quadrants():
    """R4-3 验收 (d)(e): 干净 / residual>0 / 修复未收敛 / 超预算 四象限 + 旧格式回退。"""
    # 1) 干净跑法（含 2 个 passed 修复）→ PASS
    ok = _report(first_pass_total_p0=4, residual_total_p0=0,
                 revision_stats={'attempted': 2, 'passed': 2, 'degraded': 0, 'spotfixed': 1})
    g4, detail = evaluate_g4(ok, 20)
    assert g4 is True, detail
    assert detail['first_pass_p0_budget'] == 10
    # 2) residual > 0 → FAIL
    bad = _report(first_pass_total_p0=4, residual_total_p0=3,
                  revision_stats={'attempted': 2, 'passed': 2, 'degraded': 0, 'spotfixed': 1})
    g4, _ = evaluate_g4(bad, 20)
    assert g4 is False
    # 3) 修复未收敛（attempted != passed）→ FAIL
    unconverged = _report(first_pass_total_p0=4, residual_total_p0=0,
                          revision_stats={'attempted': 2, 'passed': 1,
                                          'degraded': 1, 'spotfixed': 0})
    g4, _ = evaluate_g4(unconverged, 20)
    assert g4 is False
    # 4) 首检超预算（11 > 10）→ FAIL；恰好 10 → PASS
    over = _report(first_pass_total_p0=11, residual_total_p0=0,
                   revision_stats={'attempted': 5, 'passed': 5, 'degraded': 0, 'spotfixed': 0})
    g4, detail = evaluate_g4(over, 20)
    assert g4 is False and detail['first_pass_total_p0'] == 11
    edge = _report(first_pass_total_p0=10, residual_total_p0=0,
                   revision_stats={'attempted': 5, 'passed': 5, 'degraded': 0, 'spotfixed': 0})
    g4, _ = evaluate_g4(edge, 20)
    assert g4 is True
    # 5) 逻辑均分 / 一致性 / 零审查 Part → FAIL
    assert evaluate_g4(_report(avg_logic=5), 20)[0] is False
    assert evaluate_g4(_report(cons_pass=False), 20)[0] is False
    assert evaluate_g4(_report(parts_count=0), 20)[0] is False
    # 6) 旧格式报告（无 R4-3 新键）→ residual 回退聚合 P0 数
    legacy = _report(logic_p0=2, parts_count=20, legacy=True)
    g4, detail = evaluate_g4(legacy, 20)
    assert g4 is False and detail['residual_total_p0'] == 2
    legacy_clean = _report(parts_count=20, legacy=True)
    assert evaluate_g4(legacy_clean, 20)[0] is True
    logger.info('[test_g4_quadrants] PASS: 四象限 + 均分/一致性/零审查 + 旧格式回退')


def test_first_pass_budget_smoke_and_env():
    """R4-3: 冒烟（PARTS<10）不设限仅记录；env KML_MAX_FIRST_PASS_P0 覆盖/关闭。"""
    assert first_pass_p0_budget(2) is None, '冒烟不设限'
    assert first_pass_p0_budget(9) is None
    assert first_pass_p0_budget(10) == 5
    assert first_pass_p0_budget(20) == 10
    os.environ['KML_MAX_FIRST_PASS_P0'] = '3'
    try:
        assert first_pass_p0_budget(20) == 3
        assert first_pass_p0_budget(2) == 3, 'env 显式覆盖对冒烟同样生效'
    finally:
        os.environ.pop('KML_MAX_FIRST_PASS_P0', None)
    os.environ['KML_MAX_FIRST_PASS_P0'] = '0'
    try:
        assert first_pass_p0_budget(20) is None, 'env 0 = 关闭护栏'
    finally:
        os.environ.pop('KML_MAX_FIRST_PASS_P0', None)
    # 冒烟 + 巨额首检 P0 仍不因预算 FAIL（只记录）
    smoke = _report(first_pass_total_p0=99, residual_total_p0=0)
    g4, detail = evaluate_g4(smoke, 2)
    assert g4 is True and detail['first_pass_total_p0'] == 99
    assert detail['first_pass_p0_budget'] is None
    logger.info('[test_budget_smoke_env] PASS: 冒烟不设限、env 覆盖与关闭、非法值回退')


def test_g4_detail_json_safe():
    """R4-3: gates['G4_coherence'] detail 必须 JSON 可序列化（report.json 落盘）。"""
    import json
    ok = _report(first_pass_total_p0=4, residual_total_p0=0,
                 revision_stats={'attempted': 2, 'passed': 2, 'degraded': 0, 'spotfixed': 1})
    _, detail = evaluate_g4(ok, 20)
    text = json.dumps(detail, ensure_ascii=False)
    assert '"first_pass_total_p0": 4' in text
    assert '"residual_total_p0": 0' in text
    assert '"spotfixed": 1' in text
    assert '"first_pass_p0_budget": 10' in text
    _, smoke_detail = evaluate_g4(_report(), 2)
    assert '"first_pass_p0_budget": null' in json.dumps(smoke_detail, ensure_ascii=False)
    logger.info('[test_g4_json] PASS: detail JSON 安全（含 null 预算）')


# ---------------- R5-1: evaluate_g4 第三参 name_audit 象限 ----------------

def test_evaluate_g4_name_audit_quadrants():
    """R5-1 验收: evaluate_g4(report, parts, name_audit) 四象限。

    干净 name_audit → 不改变判定；blocking 残留 → FAIL；env 关闸后条件不生效
    但 gate_enabled=False 原样落 detail（不静默放宽）；无第三参（旧格式）→
    detail 不含 name_audit 键（既有 7 项调用行为逐字节不变）。
    """
    ok = _report(first_pass_total_p0=0, residual_total_p0=0)
    na = {'scanned': 20, 'findings': 3, 'fixed': 2, 'residual_blocking': 0,
          'residual_advisory': 1, 'canonical_absent': 1, 'gate_enabled': True}
    g4, detail = evaluate_g4(ok, 20, na)
    assert g4 is True and detail['name_audit']['residual_blocking'] == 0
    assert detail['name_audit']['gate_enabled'] is True
    bad = dict(na, residual_blocking=1)
    g4, detail = evaluate_g4(ok, 20, bad)
    assert g4 is False and detail['name_audit']['residual_blocking'] == 1
    off = dict(na, residual_blocking=1, gate_enabled=False)
    g4, detail = evaluate_g4(ok, 20, off)
    assert g4 is True and detail['name_audit']['gate_enabled'] is False
    # 旧格式（无第三参）→ detail 不含 name_audit（向后兼容）
    g4, detail = evaluate_g4(ok, 20)
    assert 'name_audit' not in detail
    logger.info('[test_g4_name_audit] PASS: name_audit 四象限 + 旧格式 detail 不变')


if __name__ == '__main__':
    logger.info('=' * 60)
    logger.info('test_round4_gates.py —— Round 4 G4 门禁口径回归（纯离线）')
    logger.info('=' * 60)
    for fn in (test_aggregator_no_revision_output_unchanged,
               test_aggregator_passed_repair_residual_zero,
               test_aggregator_failed_repair_keeps_first_pass_issues,
               test_aggregator_revision_stats_degraded_and_spotfixed,
               test_evaluate_g4_quadrants,
               test_first_pass_budget_smoke_and_env,
               test_g4_detail_json_safe,
               test_evaluate_g4_name_audit_quadrants):
        fn()
        print(f'PASS {fn.__name__}')
    logger.info('\nALL PASS')
