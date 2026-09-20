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
    if SKIP_PHASE4:
        print('[G4] 连贯性: SKIP（KML_SKIP_PHASE4=1）', flush=True)
        gates['G4_coherence'] = {'pass': None, 'skipped': True}
    else:
        report = data.get('review_report') or {}
        logic = report.get('logic') or {}
        cons = report.get('consistency') or {}
        parts_reviewed = logic.get('parts_count') or cons.get('parts_count') or 0
        total_p0 = int(logic.get('p0_count') or 0) + int(cons.get('p0_count') or 0)
        total_p1 = int(logic.get('p1_count') or 0) + int(cons.get('p1_count') or 0)
        avg_logic = logic.get('avg_score') or 0
        cons_pass = bool(cons.get('pass'))
        cons_rate = 1.0 if cons_pass else 0.0
        per_part_detail = [
            {'part': p.get('part'), 'logic_score': p.get('logic_score'),
             'consistency_score': p.get('consistency_score'),
             'p0_issues': [str(i)[:80] for i in (p.get('p0_issues') or [])],
             'p1_issues': [str(i)[:80] for i in (p.get('p1_issues') or [])]}
            for p in (report.get('parts') or [])
        ]
        g4 = (parts_reviewed > 0 and total_p0 <= MAX_TOTAL_P0
              and avg_logic >= MIN_AVG_LOGIC_SCORE and cons_pass)
        gates['G4_coherence'] = {
            'pass': g4, 'reviewed_parts': parts_reviewed,
            'total_p0': total_p0, 'total_p1': total_p1,
            'avg_logic_score': avg_logic, 'consistency_pass': cons_pass,
            'thresholds': {'max_total_p0': MAX_TOTAL_P0, 'min_avg_logic_score': MIN_AVG_LOGIC_SCORE,
                           'min_consistency_pass_rate': MIN_CONSISTENCY_PASS_RATE},
            'per_part': per_part_detail,
        }
        print(f"[G4] 连贯性: {'PASS' if g4 else 'FAIL'} "
              f'(审查 {parts_reviewed} Parts | P0={total_p0} P1={total_p1} | '
              f'逻辑均分={avg_logic} | 一致性通过={cons_pass})', flush=True)

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
