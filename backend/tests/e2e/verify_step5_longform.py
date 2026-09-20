"""
step-5-preview 内核全链路验收：完整产出 ~10 万字连贯小说

与 verify_minimax_longform.py 的区别：
  1. 驱动完整管线（Phase 1 灵感解析 → Phase 2 情节规划 → Phase 3 逐Part创作 →
     Phase 4 三审+风格优化），从一句灵感开始，验证"完整产出"能力
  2. 连贯性门禁直接取 Phase 4 的 review_report（Logic/Emotion/Consistency
     三个 Review Agent 对每个 Part 的审查结果）聚合 P0/P1 计数
  3. provider 无关（不强制 minimax），默认 20 Part × 5000 字 ≈ 10 万字

环境变量：
  KML_PARTS          默认 20
  KML_WORDS_PER_PART 默认 5000（仅用于目标字数提示）
  KML_INSPIRATION    默认内置玄幻凡人流灵感
  KML_SKIP_PHASE4    设 1 则跳过 Phase 4（快速冒烟，无连贯性门禁）

运行：
  cd backend && python -u tests/e2e/verify_step5_longform.py
退出码：0 = 全部门禁通过
"""
import asyncio
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# R2-1: 密度口径单点统一 —— 门禁与清洗器（core.text_utils.strip_padding_chars）
# 共用同一把尺子（此前门禁按字符计、清洗器按出现次数计，导致"清洗干净却门禁
# 失败"的口径漂移）。判定逻辑（阈值 5/1k、6/1k、run=0）与输出字段保持不变。
from core.text_utils import (ELLIPSIS_RUN_RE, EMDASH_RUN_RE,
                             ELLIPSIS_DENSITY_MAX, EMDASH_DENSITY_MAX,
                             ellipsis_units, emdash_units)

PARTS = int(os.environ.get('KML_PARTS', '20'))
WORDS_PER_PART = int(os.environ.get('KML_WORDS_PER_PART', '5000'))
INSPIRATION = os.environ.get(
    'KML_INSPIRATION',
    '少年林尘出生即被预言为天煞孤星，被家族封禁修为十年。一次禁地古井的坠落让血脉破封，'
    '他踏上寻道之路，誓要重返家族夺回尊严，却在途中发现自己的血脉牵扯上古神族倾覆三千世界的阴谋。',
)
SKIP_PHASE4 = os.environ.get('KML_SKIP_PHASE4', '') == '1'

TARGET_MIN = int(os.environ.get('KML_TARGET_MIN', str(PARTS * WORDS_PER_PART * 9 // 10)))
TARGET_MAX = int(os.environ.get('KML_TARGET_MAX', str(int(PARTS * WORDS_PER_PART * 1.15))))

# 连贯性门禁阈值
MAX_TOTAL_P0 = 0          # 逻辑/一致性 P0 总数必须为 0
MIN_AVG_LOGIC_SCORE = 6   # 逻辑均分下限
MIN_CONSISTENCY_PASS_RATE = 0.8  # 一致性通过率下限

OUT_DIR = BACKEND_ROOT.parent / 'data' / 'verification'
TIMESTAMP = time.strftime('%Y%m%d_%H%M%S')
RUN_DIR = OUT_DIR / f'step5_longform_{TIMESTAMP}'
RUN_DIR.mkdir(parents=True, exist_ok=True)


def density_check(text: str) -> dict:
    # R2-1: 口径单点来自 core.text_utils（ellipsis_units/emdash_units + run 正则），
    # 与 strip_padding_chars 共用；判定逻辑与输出字段不变。
    # R3-S5: 空/占位/过短文本前置 FAIL —— 此前 n=max(len,1) 让 13 字失败占位
    # （[Part N生成失败: ...]）密度全 0 虚 PASS（Round 2 冒烟 26 字占位 G3 PASS，
    # 唯一抓住 Phase 3 瘫痪的只有 G1）。字段与原返回字典同构 + 新增 reason。
    if not text or not text.strip() or text.strip().startswith('[Part '):
        return {'chars': 0, 'ellipsis_per_1k': 0, 'emdash_per_1k': 0, 'ellipsis_runs_3plus': 0,
                'emdash_runs_2plus': 0, 'verdict': 'FAIL', 'reason': 'empty_or_placeholder'}
    if len(text) < 1000:
        # 远低于 Part 正常体量（e2e part_word_min=2500 / R2-4 写作端 floor 3500），
        # 1000 ≈ 前者的 40%：只拦"明显没有正文"，不误伤任何真实 Part
        return {'chars': len(text), 'ellipsis_per_1k': 0, 'emdash_per_1k': 0, 'ellipsis_runs_3plus': 0,
                'emdash_runs_2plus': 0, 'verdict': 'FAIL', 'reason': 'text_too_short'}
    n = max(len(text), 1)
    ell = ellipsis_units(text)
    emd = emdash_units(text)
    ell_runs = re.findall(ELLIPSIS_RUN_RE, text)
    emd_runs = re.findall(EMDASH_RUN_RE, text)
    return {
        'chars': n,
        'ellipsis_per_1k': round(ell * 1000 / n, 2),
        'emdash_per_1k': round(emd * 1000 / n, 2),
        'ellipsis_runs_3plus': len(ell_runs),
        'emdash_runs_2plus': len(emd_runs),
        'verdict': 'PASS' if (ell * 1000 / n <= ELLIPSIS_DENSITY_MAX and
                              emd * 1000 / n <= EMDASH_DENSITY_MAX and
                              len(ell_runs) == 0 and len(emd_runs) == 0) else 'FAIL',
    }


def first_pass_p0_budget(parts_expected: int):
    """R4-3（S4）: 首检 P0 率预算 = PARTS // 2（20 Part → 10）。

    - 冒烟（PARTS < 10）不设限、仅记录 —— 小规模跑法首检 P0 天生波动大；
    - env KML_MAX_FIRST_PASS_P0 可覆盖；显式设 0（或负值）= 关闭护栏；
    - 取 0.5/Part 的理由：它是"可见劣化即拦"的上限而非达标线 —— 预防侧
      （名册/facts 对照/伏笔复检）生效的正常跑法首检 P0 应远低于此。

    Returns:
        int 预算值，或 None 表示不设限（JSON 安全，report.json 落 null）。
    """
    raw = os.environ.get('KML_MAX_FIRST_PASS_P0', '')
    if raw.strip():
        try:
            override = int(raw)
        except (TypeError, ValueError):
            override = None
        if override is not None:
            return None if override <= 0 else override
    if parts_expected < 10:
        return None
    return parts_expected // 2


def evaluate_g4(report: dict, parts_expected: int, name_audit: dict = None) -> tuple:
    """R4-3（S4）: G4 连贯性门禁判定（纯函数，可单测；此前内联在 main()）。

    混合案 (c) 口径：终稿语义（residual P0 = 0 且所有 revision_attempted 的
    Part revision_passed = True 且逻辑均分 ≥ 6 且一致性 pass）+ 首检 P0 率
    预算护栏（防"修复刷分"—— 重写引入新冲突却靠重审闭嘴进终稿）。
    R5-1（S1）: 第三参 name_audit 非 None 时追加确定性名册合规条件
    （residual_blocking == 0；env KML_NAME_AUDIT_GATE=0 可关闭但
    gate_enabled=False 原样落 report.json，不静默放宽）。三个阈值常量
    （MAX_TOTAL_P0 / MIN_AVG_LOGIC_SCORE / MIN_CONSISTENCY_PASS_RATE）
    一个不改；cons_pass / avg_logic 维持现状。

    Args:
        report: review_report（services/review_aggregator 产物）
        parts_expected: 期望 Part 数（KML_PARTS）
        name_audit: summarize_name_audit 产物（None = 旧格式，行为逐字节不变）

    Returns:
        (g4_pass: bool, detail: dict) —— detail 直接进 gates['G4_coherence']。
    """
    logic = report.get('logic') or {}
    cons = report.get('consistency') or {}
    parts_reviewed = logic.get('parts_count') or cons.get('parts_count') or 0
    # 首检口径（保留计算与打印，供趋势分析）
    total_p0 = int(logic.get('p0_count') or 0) + int(cons.get('p0_count') or 0)
    total_p1 = int(logic.get('p1_count') or 0) + int(cons.get('p1_count') or 0)
    avg_logic = logic.get('avg_score') or 0
    cons_pass = bool(cons.get('pass'))
    cons_rate = 1.0 if cons_pass else 0.0
    # 终稿口径（R4-3）：residual = 重审残留；旧格式报告无该键时回退聚合 P0 数
    if 'residual_total_p0' in report:
        residual_total_p0 = int(report.get('residual_total_p0') or 0)
    else:
        residual_total_p0 = total_p0
    first_pass_total_p0 = int(report.get('first_pass_total_p0') or 0)
    revision_stats = report.get('revision_stats') or {}
    budget = first_pass_p0_budget(parts_expected)
    budget_ok = budget is None or first_pass_total_p0 <= budget
    revision_converged = (int(revision_stats.get('attempted', 0) or 0)
                          == int(revision_stats.get('passed', 0) or 0))
    g4 = (parts_reviewed > 0
          and residual_total_p0 <= MAX_TOTAL_P0
          and revision_converged
          and avg_logic >= MIN_AVG_LOGIC_SCORE
          and cons_pass
          and budget_ok)
    # R5-1: 确定性名册合规条件（blocking 残留零容忍；advisory 不影响 G4）
    if name_audit is not None:
        gate_enabled = bool(name_audit.get('gate_enabled', True))
        residual_blocking = int(name_audit.get('residual_blocking') or 0)
        g4 = g4 and ((not gate_enabled) or residual_blocking == 0)
    detail = {
        'pass': g4, 'reviewed_parts': parts_reviewed,
        'total_p0': total_p0, 'total_p1': total_p1,
        'avg_logic_score': avg_logic, 'consistency_pass': cons_pass,
        'first_pass_total_p0': first_pass_total_p0,
        'residual_total_p0': residual_total_p0,
        'revision_stats': {
            'attempted': int(revision_stats.get('attempted', 0) or 0),
            'passed': int(revision_stats.get('passed', 0) or 0),
            'degraded': int(revision_stats.get('degraded', 0) or 0),
            'spotfixed': int(revision_stats.get('spotfixed', 0) or 0),
        },
        'first_pass_p0_budget': budget,
        'thresholds': {'max_total_p0': MAX_TOTAL_P0, 'min_avg_logic_score': MIN_AVG_LOGIC_SCORE,
                       'min_consistency_pass_rate': MIN_CONSISTENCY_PASS_RATE},
        'per_part': [
            {'part': p.get('part'), 'logic_score': p.get('logic_score'),
             'consistency_score': p.get('consistency_score'),
             'p0_issues': [str(i)[:80] for i in (p.get('p0_issues') or [])],
             'p1_issues': [str(i)[:80] for i in (p.get('p1_issues') or [])]}
            for p in (report.get('parts') or [])
        ],
    }
    # R5-1: name_audit 非 None 才增列（旧格式调用 detail 逐字节不变）
    if name_audit is not None:
        detail['name_audit'] = {
            'gate_enabled': bool(name_audit.get('gate_enabled', True)),
            'residual_blocking': int(name_audit.get('residual_blocking') or 0),
            'residual_advisory': int(name_audit.get('residual_advisory') or 0),
            'scanned': int(name_audit.get('scanned') or 0),
            'findings': int(name_audit.get('findings') or 0),
            'fixed': int(name_audit.get('fixed') or 0),
            'canonical_absent': int(name_audit.get('canonical_absent') or 0),
        }
    return g4, detail


def summarize_name_audit(data: dict) -> dict:
    """R5-1（S1）: name_audit 汇总行（纯函数，可单测；只读观测 + G4 新条件输入）。

    从 work JSON 的 name_audit_log + name_drift_dict + final_draft 汇总
    {scanned, findings, fixed, residual_blocking, residual_advisory,
    canonical_absent, gate_enabled}。residual 用违禁词典对**交付文本**
    final_draft 重扫（确定性、与 Phase4Runner 终审同口径）：审计时已修复
    （spotfixed 且 count_after==0）与跨 Part 护栏降级（downgraded_advisory）
    的 finding 不计 blocking 残留；修不掉/未处置的 blocking 违禁 → G4 FAIL
    （交付文本含"有显式证据证明是漂移"的名字即不达标）。
    """
    from services.name_audit import is_blocking
    if not isinstance(data, dict):
        data = {}
    log = [e for e in (data.get('name_audit_log') or []) if isinstance(e, dict)]
    drift = data.get('name_drift_dict') or {}
    drift = drift if isinstance(drift, dict) else {}
    raw_fd = data.get('final_draft')
    final_draft = {k: v for k, v in raw_fd.items()
                   if isinstance(k, str) and isinstance(v, str)
                   and not v.startswith('[Part ')} if isinstance(raw_fd, dict) else {}
    gate_enabled = os.environ.get('KML_NAME_AUDIT_GATE', '1') != '0'
    # 每个 (part, wrong) 只认**最后一条** final_audit 处置记录 —— resume 重跑
    # Phase 4 时上一轮的 spotfixed/downgraded 不得压制本轮新产生的 blocking 残留
    latest: dict = {}
    for e in log:
        if e.get('trigger') != 'final_audit':
            continue
        latest[(e.get('part'), e.get('wrong'))] = e
    resolved = {k for k, e in latest.items()
                if e.get('action') == 'spotfixed' and (e.get('count_after') or 0) == 0}
    downgraded = {k for k, e in latest.items() if e.get('action') == 'downgraded_advisory'}
    residual_blocking = 0
    residual_advisory = 0
    for key, text in final_draft.items():
        try:
            part_num = int(key)
        except (TypeError, ValueError):
            continue
        for wrong, entry in drift.items():
            if not isinstance(wrong, str) or not wrong or not isinstance(entry, dict):
                continue
            if text.count(wrong) <= 0:
                continue
            if not is_blocking(entry):
                residual_advisory += 1
            elif (part_num, wrong) in resolved or (part_num, wrong) in downgraded:
                residual_advisory += 1
            else:
                residual_blocking += 1
    return {
        'scanned': len(final_draft),
        'findings': len(log),
        'fixed': len([e for e in log if e.get('action') == 'spotfixed']),
        'residual_blocking': residual_blocking,
        'residual_advisory': residual_advisory,
        'canonical_absent': len([e for e in log
                                 if e.get('pair_source') == 'canonical_absent']),
        'gate_enabled': gate_enabled,
    }


def summarize_consistency_flags(data: dict) -> str:
    """R4-6: consistency_flags 汇总行（纯函数，可单测；只读观测，不加门禁）。

    flags 由 Phase 3 确定性预检写入 work JSON（退场角色再现 + 伏笔未回收），
    与 R1-E"只告警不阻断"一致，终判交 Phase 4。
    """
    flags = [f for f in (data.get('consistency_flags') or []) if isinstance(f, dict)]
    if not flags:
        return '[观测] consistency_flags: 0 条'
    kind_count: dict = {}
    for f in flags:
        k = f.get('type') or ('departed_reappearance' if f.get('character') else 'unknown')
        kind_count[k] = kind_count.get(k, 0) + 1
    return (f'[观测] consistency_flags: 共 {len(flags)} 条 {kind_count}'
            f'（确定性预检告警，只记录不加门禁）')


class FakeEmitter:
    async def emit(self, event_type, data, work_id=None):
        pass

    def emit_sync(self, event_type, data, work_id=None):
        pass


async def main():
    print('=' * 70, flush=True)
    print('step-5-preview 全链路验收：完整产出 ~10 万字连贯小说', flush=True)
    print(f'时间戳: {TIMESTAMP} | 目标: {PARTS} Parts × {WORDS_PER_PART} 字 ≈ {PARTS * WORDS_PER_PART} 字', flush=True)
    print(f'输出目录: {RUN_DIR}', flush=True)
    print('=' * 70, flush=True)

    # 0. 配置：自定义模板 100k / 20 Part
    os.environ['CUSTOM_TARGET_WORDS'] = str(PARTS * WORDS_PER_PART)
    os.environ['CUSTOM_PART_COUNT'] = str(PARTS)

    from core.config import init_app_config, reload_config, get_app_config, get_llm_config_for_agent, load_llm_config, DEFAULT_TEMPLATES, WritingTemplate
    init_app_config()
    reload_config()

    # R1-A: read_env 只读 .env 文件、不读 os.environ —— 上面第 95-96 行写入的
    # CUSTOM_TARGET_WORDS / CUSTOM_PART_COUNT 对 AppConfig 完全无效，配置回退
    # DEFAULT_TEMPLATES[0]「短篇」（3 Part / 4200 硬上限），20 Part 理论上限
    # 84,000 < G2 下限 90,000，G2 结构性必败。这里显式 apply_template 把自定义
    # 模板应用到配置单例，再 reload_config() 同步模块级 PART_WORD_MIN/MAX
    # （必须在 import part_writer_agent 之前，否则其模块级常量冻结旧值）。
    custom_template = next((t for t in DEFAULT_TEMPLATES if t.name == '自定义'),
                           WritingTemplate('自定义', 0, 0, 0, 0))
    cfg = get_app_config()
    cfg.apply_template(custom_template,
                       custom_target_words=PARTS * WORDS_PER_PART,
                       custom_part_count=PARTS)
    reload_config()

    active = load_llm_config().active_provider_id
    cfg_llm = get_llm_config_for_agent('part_writer')
    print(f'\n[Step 0] active_provider={active} | model={cfg_llm.model} | base_url={cfg_llm.base_url}', flush=True)
    # R1-A: 打印有效字数配置，防再次静默漂移（此前只打 provider/model，模板回退不可见）
    print(f'[Step 0] 模板={cfg.template.name} | target_words={cfg.target_word_count} | '
          f'part_count={cfg.part_count} | part_word_min={cfg.part_word_min} | '
          f'part_word_max={cfg.part_word_max} | Part 硬上限={cfg.part_word_max + 200}', flush=True)
    if not cfg_llm.api_key:
        print('❌ API Key 未配置，终止', flush=True)
        return False

    # 1. 创建 work（仅灵感，phase=init，走完整管线）
    work_id = uuid.uuid4().hex[:12]
    work_file = BACKEND_ROOT.parent / 'data' / 'works' / f'{work_id}.json'
    initial_data = {
        'id': work_id,
        'title': f'验收-凡尘破界-{TIMESTAMP}',
        'inspiration': INSPIRATION,
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat(),
        'phase': 'init',
        'word_count': 0,
        'parts': {},
        'part_summaries': {},
        'part_outline': [],
        'characters': [],
        'world_setting': '',
        'foreshadowing': [],
        'cost_summary': {'calls': [], 'estimated_cost_rmb': 0.0},
        'review_report': None,
        'final_draft': {},
    }
    work_file.write_text(json.dumps(initial_data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n[Step 1] work={work_id} 已创建（phase=init，完整管线）', flush=True)

    # 2. 完整管线
    from services.writing_service import WritingService
    from core.progress_manager import progress_manager

    service = WritingService(work_id, FakeEmitter(), resume=False)
    service.cfg.confirm_mode = False
    service.cfg._part_count = PARTS
    reload_config()

    async def _no_confirm(confirm_id, message):
        pass
    service._request_confirm = _no_confirm
    progress_manager.reset_progress(work_id)

    if SKIP_PHASE4:
        # P1-1: 此前 SKIP_PHASE4 只跳 G4 门禁打印、Phase 4 照跑（冒烟 32min vs 文档
        # 承诺的"分钟级"）。这里 monkeypatch Phase4Runner.run 为 async no-op ——
        # _phase4_optimize 在运行时才解析 .run，打补丁即生效；不动 WritingService
        # 主流程签名，避免波及其他调用方与 test_resume。
        from services.writing_phase_runners import Phase4Runner

        async def _skip_phase4(self):
            print('[SKIP] KML_SKIP_PHASE4=1，Phase 4（风格优化+三评审）已跳过', flush=True)

        Phase4Runner.run = _skip_phase4

    t_start = time.time()
    print(f'\n[Step 2] 开始完整创作流程（Phase 1-4）...', flush=True)
    try:
        await service.run()
    except Exception as e:
        print(f'❌ 管线异常: {type(e).__name__}: {e}', flush=True)
        import traceback
        traceback.print_exc()
    elapsed_total = time.time() - t_start
    print(f'\n  管线总耗时: {elapsed_total:.1f}s ({elapsed_total / 60:.1f}min)', flush=True)

    # 3. 重新读 work 文件
    data = json.loads(work_file.read_text(encoding='utf-8'))
    parts = {k: v for k, v in (data.get('parts') or {}).items() if isinstance(v, str)}
    total_chars = sum(len(v) for v in parts.values())
    print(f'  实际生成 Part 数: {len(parts)}/{PARTS}')
    print(f'  累计字数: {total_chars:,} 字符')

    gates = {}

    # G1 完整性
    failed_parts = [k for k, v in parts.items() if not v.strip() or v.startswith('[Part ')]
    g1 = (len(parts) == PARTS) and not failed_parts
    gates['G1_complete'] = {
        'pass': g1, 'expected_parts': PARTS, 'actual_parts': len(parts),
        'failed_parts': failed_parts,
    }
    print(f"\n[G1] 完整性: {'PASS' if g1 else 'FAIL'} ({len(parts)}/{PARTS} Parts, 失败 {len(failed_parts)} 个)", flush=True)

    # G2 字数
    g2 = TARGET_MIN <= total_chars <= TARGET_MAX
    gates['G2_word_count'] = {
        'pass': g2, 'total_chars': total_chars,
        'range': [TARGET_MIN, TARGET_MAX],
    }
    print(f"[G2] 字数: {'PASS' if g2 else 'FAIL'} ({total_chars:,} ∈ [{TARGET_MIN:,}, {TARGET_MAX:,}])", flush=True)

    # G3 密度（反凑字数）
    density_results = {}
    for k in sorted(parts.keys(), key=lambda x: int(x)):
        density_results[k] = density_check(parts[k])
    density_pass = sum(1 for r in density_results.values() if r['verdict'] == 'PASS')
    g3 = len(density_results) == PARTS and density_pass == PARTS
    gates['G3_density'] = {
        'pass': g3, 'passed': density_pass, 'total': len(density_results),
        'parts': density_results,
    }
    print(f"[G3] 密度: {'PASS' if g3 else 'FAIL'} ({density_pass}/{len(density_results)} Parts)", flush=True)

    # G4 连贯性（Phase 4 review_report 聚合，规范结构见 services/review_aggregator.py）
    g4 = None
    # R5-1: 确定性终审汇总（只读观测 + G4 新条件输入；Phase 4 跳过时为 0 条）
    name_audit = summarize_name_audit(data)
    if SKIP_PHASE4:
        print('[G4] 连贯性: SKIP（KML_SKIP_PHASE4=1）', flush=True)
        gates['G4_coherence'] = {'pass': None, 'skipped': True}
    else:
        report = data.get('review_report') or {}
        # R4-3（S4）: 判定抽成纯函数 evaluate_g4（可单测）；混合案 (c) 口径 ——
        # 终稿零残留 P0 + 修复全收敛 + 逻辑均分 + 一致性 pass + 首检 P0 率预算；
        # R5-1（S1）: 追加确定性名册合规条件（name_audit.residual_blocking == 0）
        g4, g4_detail = evaluate_g4(report, PARTS, name_audit)
        gates['G4_coherence'] = g4_detail
        rs = g4_detail['revision_stats']
        budget_txt = ('不设限' if g4_detail['first_pass_p0_budget'] is None
                      else str(g4_detail['first_pass_p0_budget']))
        na = g4_detail['name_audit']
        print(f"[G4] 连贯性: {'PASS' if g4 else 'FAIL'} "
              f"(审查 {g4_detail['reviewed_parts']} Parts | P0={g4_detail['total_p0']} "
              f"P1={g4_detail['total_p1']} | 逻辑均分={g4_detail['avg_logic_score']} | "
              f"一致性通过={g4_detail['consistency_pass']})", flush=True)
        print(f"     终稿口径: residual_p0={g4_detail['residual_total_p0']} | "
              f"首检_p0={g4_detail['first_pass_total_p0']}（预算 {budget_txt}）| "
              f"修复统计: attempted={rs['attempted']} passed={rs['passed']} "
              f"degraded={rs['degraded']} spotfixed={rs['spotfixed']} | "
              f"名称审计: residual_blocking={na['residual_blocking']}"
              f"（门禁 {'开' if na['gate_enabled'] else '关'}）", flush=True)

    # R4-6: consistency_flags 汇总（只读观测，不加门禁 —— 与 R1-E"只告警不阻断"
    # 一致，终判交 Phase 4； flags 由 Phase 3 确定性预检写入 work JSON）
    print(summarize_consistency_flags(data), flush=True)

    # R5-1: name_audit 汇总行（只读观测；blocking 残留已进 G4 判定）
    print(f'[观测] name_audit: 扫描 {name_audit["scanned"]} Part | findings '
          f'{name_audit["findings"]} | fixed {name_audit["fixed"]} | '
          f'residual_blocking {name_audit["residual_blocking"]} | '
          f'residual_advisory {name_audit["residual_advisory"]} | '
          f'canonical_absent {name_audit["canonical_absent"]}'
          f'（确定性名册审计，blocking 残留进 G4）', flush=True)

    # 成本
    try:
        from core.cost_tracker import get_tracker
        tracker = get_tracker(work_id=work_id)
        cost_summary = tracker.summary()
    except Exception:
        cost_summary = 'N/A'

    all_pass = all(g['pass'] for g in gates.values() if g.get('pass') is not None)
    summary = {
        'timestamp': TIMESTAMP,
        'work_id': work_id,
        'provider': active,
        'model': cfg_llm.model,
        'inspiration': INSPIRATION,
        'num_parts_target': PARTS,
        'actual_parts': len(parts),
        'total_chars': total_chars,
        'elapsed_sec': round(elapsed_total, 1),
        'gates': gates,
        'overall_pass': all_pass,
        'cost': cost_summary,
    }
    report_file = RUN_DIR / 'report.json'
    report_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (RUN_DIR / 'work.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告: {report_file}', flush=True)
    print(f"\n{'=' * 70}\n总体判定: {'✅ 全部通过' if all_pass else '❌ 存在未过门禁'}\n{'=' * 70}", flush=True)
    return all_pass


if __name__ == '__main__':
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
