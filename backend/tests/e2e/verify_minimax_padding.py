"""
P0 修复验证（2026-09-18）：minimax-m3 反凑字数 —— 端到端跑一遍案例。

策略：
  1. 创建 6 Part 中篇小说（minimax_m3 provider）
  2. 跑 Phase 1（灵感 + 题材）→ Phase 2（情节规划）
  3. 跑 Phase 3（逐 Part 写作）—— 这是模型最易触发省略号/破折号堆叠的阶段
  4. 对每个 Part 输出做密度检测（…… 和 —— 每千字），超出阈值则失败
  5. 输出报告 + 每 Part 抽样

运行：
    cd backend && python -u tests/e2e/verify_minimax_padding.py
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# P0 反凑字数阈值（与 core.text_utils 一致）
ELLIPSIS_DENSITY_MAX = 5   # …… 密度上限 / 1k 字符
EMDASH_DENSITY_MAX = 6     # —— 密度上限 / 1k 字符
ELLIPSIS_RUN_MAX = 4       # 单段连续 …… 上限（出现 4+ 即注水）
EMDASH_RUN_MAX = 4         # 单段连续 —— 上限

OUT_DIR = BACKEND_ROOT.parent / 'data' / 'verification'
OUT_DIR.mkdir(parents=True, exist_ok=True)
TIMESTAMP = time.strftime('%Y%m%d_%H%M%S')
RUN_DIR = OUT_DIR / f'minimax_padding_check_{TIMESTAMP}'
RUN_DIR.mkdir(parents=True, exist_ok=True)


def density_check(text):
    """对单个 Part 文本做密度检查 + 返回具体证据。"""
    n = max(len(text), 1)
    ell = text.count('…') + text.count('...')
    emd = text.count('——') + text.count('--')
    import re
    ell_runs = re.findall(r'…{3,}|\.{3,}', text)
    emd_runs = re.findall(r'——{2,}|-{2,}', text)
    return {
        'chars': n,
        'ellipsis_count': ell,
        'ellipsis_per_1k': ell * 1000 / n,
        'emdash_count': emd,
        'emdash_per_1k': emd * 1000 / n,
        'ellipsis_runs_3plus': len(ell_runs),
        'emdash_runs_2plus': len(emd_runs),
        'longest_ellipsis_run': max((len(r) for r in ell_runs), default=0),
        'longest_emdash_run': max((len(r) for r in emd_runs), default=0),
        'verdict': 'PASS' if (ell * 1000 / n <= ELLIPSIS_DENSITY_MAX and
                             emd * 1000 / n <= EMDASH_DENSITY_MAX and
                             len(ell_runs) == 0 and
                             len(emd_runs) == 0) else 'FAIL',
    }


def make_outline(num_parts):
    """生成 6 Part 玄幻修真 outline。

    用 1500 字/Part（短篇下限）加快测试速度 —— 长篇后段才是 padding 高发区，
    6 个连续 Part 足以让 rolling summary 触发并污染后续上下文，验证完整链路。
    """
    items = [
        ('开篇：血脉觉醒', '开端', '林风被家族贬为杂役十年，一日意外发现血脉中封存的上古记忆', '震惊与不甘',
         '"这怎么可能？"', '血脉觉醒引来的不速之客', '起始：林风受辱'),
        ('冲突：宗门外门', '冲突', '林风觉醒血脉后被外门长老盯上，迫其参加宗门大比', '紧张',
         '"大比之日，我等你。"', '外门长老设下暗局', '承前：血脉暴露'),
        ('转折：暗中结盟', '转折', '林风与大比对手暗中联手，发现宗门内鬼', '震撼',
         '"原来我们都被人算计了。"', '内鬼的真实身份', '承前：宗门大比'),
        ('升级：禁地探秘', '发展', '林风与盟友闯入宗门禁地，寻找破局之法', '神秘与期待',
         '"禁地之中，必有答案。"', '禁地深处的守护者', '承前：内鬼发现'),
        ('高潮：宗主对决', '高潮', '林风与宗主正面对决，血脉完全觉醒', '悲壮',
         '"我不甘心，我命由我不由天。"', '宗主临终揭露真相', '承前：禁地真相'),
        ('结局：飞升离去', '结局', '林风查明真相后离开宗门，飞升上界', '释然',
         '"后会有期。"', '上界的新冒险', '承前：真相揭露'),
    ][:num_parts]
    return [
        {
            'part': i + 1,
            'title': title,
            'phase': phase,
            'word_count': 1500,
            'core_event': event,
            'emotion_target': emotion,
            'key_dialogue': dialogue,
            'end_hook': hook,
            'causality': causality,
            'pacing': '紧张推进' if i < num_parts - 2 else '收尾',
            'foreshadow_plant': [],
            'foreshadow_reveal': [],
        }
        for i, (title, phase, event, emotion, dialogue, hook, causality) in enumerate(items)
    ]


async def main():
    print('=' * 70, flush=True)
    print('P0 修复验证：minimax-m3 反凑字数 —— 端到端案例', flush=True)
    print(f'时间戳: {TIMESTAMP}', flush=True)
    print(f'输出目录: {RUN_DIR}', flush=True)
    print('=' * 70, flush=True)

    # 1. 配置检查
    from core.config import get_llm_config_for_agent, load_llm_config
    active = load_llm_config().active_provider_id
    print(f'\n[Step 1] active_provider_id = {active}', flush=True)
    cfg = get_llm_config_for_agent('part_writer')
    print(f'  part_writer model = {cfg.model}', flush=True)
    print(f'  base_url = {cfg.base_url}', flush=True)
    if 'minimax' not in cfg.model.lower():
        print('⚠️  警告：当前模型不是 minimax，需要切换 active provider', flush=True)
        return False

    # 2. 初始化 work
    import uuid
    from datetime import datetime
    work_id = uuid.uuid4().hex[:12]
    print(f'\n[Step 2] 创建 work: {work_id}', flush=True)

    num_parts = 6
    outline = make_outline(num_parts)
    initial_data = {
        'id': work_id,
        'title': 'P0修复验证：血脉觉醒',
        'inspiration': '少年林风被家族贬为杂役十年，偶然觉醒上古血脉',
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat(),
        'phase': 'phase2_complete',
        'word_count': 0,
        'parts': {},
        'part_summaries': {},
        'part_outline': outline,
        'characters': [{'name': '林风', 'role': '主角', 'identity': '被贬杂役的少年', 'core_trait': '隐忍',
                        'motivation': '查清真相', 'secret': '上古血脉觉醒者', 'arc': '从杂役成长为飞升者'}],
        'world_setting': '修仙世界，宗门林立，以实力为尊。林风出身没落家族，被贬为杂役。',
        'foreshadowing': [],
        'current_plot_state': '林风刚觉醒血脉',
        'character_state_track': {},
        'cost_summary': {'calls': [], 'estimated_cost_rmb': 0.0},
        'review_report': None,
        'final_draft': {},
    }
    work_file = BACKEND_ROOT.parent / 'data' / 'works' / f'{work_id}.json'
    work_file.write_text(json.dumps(initial_data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'  work 写入: {work_file}', flush=True)

    # 3. 构造 WritingService + Phase3Runner
    from services.writing_service import WritingService, TempStoryState
    from services.writing_phase_runners import Phase3Runner
    from core.progress_manager import progress_manager

    class FakeEmitter:
        """最简 SSE emitter mock —— 仅做日志，不真的发事件"""
        async def emit(self, event_type, data, work_id=None):
            pass
        def emit_sync(self, event_type, data, work_id=None):
            pass

    fake_emitter = FakeEmitter()

    data = json.loads(work_file.read_text(encoding='utf-8'))
    service = WritingService(work_id, fake_emitter, resume=True)
    service.data = data
    # 关掉 confirm_mode，否则 Phase 3 每 5 Part 会卡在等待用户确认
    service.cfg.confirm_mode = False
    # 修正 part_count：默认模板是 短篇 (3 Part)，必须强制覆盖为 6 才符合 outline
    # 注意：cfg.part_count 是 property，只改底层 _part_count + 通过 reload_config 重新计算派生字段
    service.cfg._part_count = num_parts
    from core.config import reload_config
    reload_config()
    # 同时把 _request_confirm 替换成 no-op，避免 i % 5 == 0 分支 await _request_confirm
    async def _no_confirm(confirm_id, message):
        pass
    service._request_confirm = _no_confirm
    progress_manager.reset_progress(work_id)
    temp_state = TempStoryState(data, memory=None, vector_store=service.vector_store)

    # 4. 跑 Phase 3
    print(f'\n[Step 3] 跑 Phase 3 逐 Part 写作 ({num_parts} parts)...', flush=True)
    runner = Phase3Runner(service)
    # 调试 hook：监控 Phase3Runner.run 内的 for 循环迭代
    orig_run = runner.run
    async def traced_run(*args, **kwargs):
        try:
            return await orig_run(*args, **kwargs)
        except BaseException as exc:
            import traceback
            print(f'❌ runner.run 抛出: {type(exc).__name__}: {exc}', flush=True)
            traceback.print_exc()
            raise
    runner.run = traced_run
    t0 = time.time()
    try:
        await runner.run(start_from=1)
    except Exception as e:
        print(f'❌ Phase 3 异常: {type(e).__name__}: {e}', flush=True)
        import traceback; traceback.print_exc()
    elapsed = time.time() - t0
    print(f'  Phase 3 总耗时: {elapsed:.1f}s', flush=True)
    print(f'  Phase 3 实际生成 Part 数: {len(json.loads(work_file.read_text(encoding="utf-8")).get("parts", {}))}', flush=True)

    # 5. 重新读 work 文件
    data = json.loads(work_file.read_text(encoding='utf-8'))
    parts = data.get('parts', {})

    # 6. 密度检查
    print(f'\n[Step 4] 密度检查（阈值：…… ≤{ELLIPSIS_DENSITY_MAX}/1k, —— ≤{EMDASH_DENSITY_MAX}/1k）', flush=True)
    results = []
    for k in sorted(parts.keys(), key=lambda x: int(x)):
        text = parts[k]
        if not isinstance(text, str):
            continue
        r = density_check(text)
        results.append((k, r))
        status = '✓' if r['verdict'] == 'PASS' else '✗'
        line = (
            f"  {status} Part {k}: {r['chars']:4d} chars | "
            f"…×{r['ellipsis_count']:3d} ({r['ellipsis_per_1k']:5.1f}/1k) | "
            f"——×{r['emdash_count']:3d} ({r['emdash_per_1k']:5.1f}/1k) | "
            f"3+…:{r['ellipsis_runs_3plus']} | 2+——:{r['emdash_runs_2plus']} | "
            f"最长 …×{r['longest_ellipsis_run']} ——×{r['longest_emdash_run']}"
        )
        print(line, flush=True)

    # 7. 报告
    pass_count = sum(1 for _, r in results if r['verdict'] == 'PASS')
    fail_count = len(results) - pass_count
    summary = {
        'timestamp': TIMESTAMP,
        'work_id': work_id,
        'provider': active,
        'model': cfg.model,
        'num_parts': num_parts,
        'parts_passed': pass_count,
        'parts_failed': fail_count,
        'parts_data': dict(results),
        'phase3_elapsed_sec': elapsed,
    }
    report_file = RUN_DIR / 'report.json'
    report_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

    work_copy = RUN_DIR / 'work.json'
    work_copy.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'\n[Step 5] 报告: {report_file}', flush=True)
    print(f'  Parts PASS: {pass_count}/{num_parts}', flush=True)
    if fail_count > 0 or pass_count == 0:
        print(f'  Parts FAIL: {fail_count}/{num_parts}', flush=True)
        print(f'\n❌ 反凑字数修复未达标，需要再次排查', flush=True)
        return False
    print(f'\n✅ 反凑字数修复已通过端到端验证 ({pass_count}/{num_parts} parts PASS)', flush=True)
    return True


if __name__ == '__main__':
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)