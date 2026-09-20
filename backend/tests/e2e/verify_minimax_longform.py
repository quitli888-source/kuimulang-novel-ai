"""
P0 修复验证 #2：minimax-m3 长篇（≥10万字） —— 着重抽检后半 Part。

策略：
  1. 创建 25 Part 长篇 outline（修仙玄幻题材，minimax-m3 擅长且触发 padding 风险高）
  2. 跑 Phase 3 全部 25 Part（每 Part 4500 字，目标 ~11 万字）
  3. 全 Part 密度检测 + 重点抽检后半 12 Part（Part 14-25）
  4. 对比 r10/r14 baseline（r14 后半 Part 19-30 `——` 密度达 19-49/1k）

阈值（与 core.text_utils.strip_padding_chars 一致）：
  - `……` 密度 ≤ 5/1k 字符
  - `——` 密度 ≤ 6/1k 字符
  - 无 3+ 连续 `……` run
  - 无 2+ 连续 `——` run

运行：
    cd backend && python -u tests/e2e/verify_minimax_longform.py
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# 阈值
ELLIPSIS_DENSITY_MAX = 5
EMDASH_DENSITY_MAX = 6
# 后半部分重点观察的 Part 编号（>= 13 即"后半"，共 25 Part 后半 = 14-25）
TAIL_PARTS_START = 14  # 1-indexed

OUT_DIR = BACKEND_ROOT.parent / 'data' / 'verification'
TIMESTAMP = time.strftime('%Y%m%d_%H%M%S')
RUN_DIR = OUT_DIR / f'longform_{TIMESTAMP}'
RUN_DIR.mkdir(parents=True, exist_ok=True)


def density_check(text):
    n = max(len(text), 1)
    ell = text.count('…') + text.count('...')
    emd = text.count('——') + text.count('--')
    import re
    ell_runs = re.findall(r'…{3,}|\.{3,}', text)
    emd_runs = re.findall(r'——{2,}|-{2,}', text)
    return {
        'chars': n,
        'ellipsis_count': ell,
        'ellipsis_per_1k': round(ell * 1000 / n, 2),
        'emdash_count': emd,
        'emdash_per_1k': round(emd * 1000 / n, 2),
        'ellipsis_runs_3plus': len(ell_runs),
        'emdash_runs_2plus': len(emd_runs),
        'longest_ellipsis_run': max((len(r) for r in ell_runs), default=0),
        'longest_emdash_run': max((len(r) for r in emd_runs), default=0),
        'verdict': 'PASS' if (ell * 1000 / n <= ELLIPSIS_DENSITY_MAX and
                             emd * 1000 / n <= EMDASH_DENSITY_MAX and
                             len(ell_runs) == 0 and
                             len(emd_runs) == 0) else 'FAIL',
    }


def make_long_outline(num_parts=25):
    """25 Part 玄幻修真长篇 outline —— 完整"凡人流"三幕式结构。"""
    items = [
        ('序：血脉蒙尘',     '开端',  '少年林尘出生即被预言"天煞孤星"，被家族封禁修为于废柴',         '压抑与不甘',
         '"我不信命。"',                                                            '十年封印将被一道裂缝打破',                   '起始：血脉蒙尘'),
        ('觉醒：废土破封',   '开端',  '林尘偶然跌落禁地古井，血脉破封，遇远古传承',                       '震惊与希望',
         '"这是——我的机缘。"',                                                       '上古遗迹将引来窥伺',                       '承前：血脉破封'),
        ('立志：重返山门',   '开端',  '林尘告别故土，独自踏上寻道之路，立志重返家族夺回尊严',           '决心',
         '"总有一天，我会让他们后悔。"',                                             '途中遭遇散修截杀',                         '承前：踏上征途'),
        ('初入江湖：客栈风波','开端', '林尘于荒城客栈遇散修截杀，显露身手救下同行少女柳青青',             '紧张',
         '"你没事吧？"',                                                            '少女身份神秘',                             '承前：旅途波折'),
        ('并肩：生死与共',   '发展',  '林尘与柳青青结伴，遇妖兽袭击，两人以弱胜强',                       '战斗热血',
         '"一起杀出去！"',                                                          '妖兽背后疑似有人操控',                     '承前：客栈遇险'),
        ('误会：身份暴露',   '发展',  '林尘修为暴露，柳青青误会其为魔道奸细，分道扬镳',                   '遗憾',
         '"你——竟然骗我。"',                                                        '误会埋下伏笔',                             '承前：并肩作战'),
        ('磨难：孤身入荒漠', '发展',  '林尘孤身穿越无尽荒漠，修为遇瓶颈，险些走火入魔',                   '绝望与坚持',
         '"我不能倒在这里。"',                                                      '荒漠深处出现神秘古城',                     '承前：分道扬镳'),
        ('奇遇：荒漠古城',   '发展',  '林尘误入荒漠古城，遇守墓人指点，得上古丹方',                       '惊喜',
         '"多谢前辈。"',                                                            '古城背后势力介入',                         '承前：荒漠深处'),
        ('回归：旧友重逢',   '发展',  '林尘修为小成返家，发现故友已成家族弃子，被押赴刑场',               '愤怒',
         '"今日谁都别想动他！"',                                                    '家族长老出面对峙',                         '承前：荒漠历练'),
        ('初胜：打脸家族',   '转折',  '林尘于刑场独战家族数位长老，一战成名',                              '畅快',
         '"还有谁！"',                                                              '家主被迫现身',                             '承前：刑场对峙'),
        ('真相：血脉之谜',   '转折',  '林尘与家主对峙中得知自身血脉真相：上古神族遗孤',                    '震撼',
         '"我——竟是神族后人？"',                                                    '家主欲夺血脉，引爆大战',                   '承前：家主现身'),
        ('决裂：废脉退族',   '转折',  '林尘当着全族宣布断绝血脉，退族出走，立下三年之约',                  '悲壮',
         '"三年后，我会回来。"',                                                    '退族途中遭遇截杀',                         '承前：血脉真相'),
        ('追杀：千里逃亡',   '转折',  '林尘被家族高手千里追杀，九死一生逃入禁地',                          '紧张',
         '"追吧，我等着。"',                                                        '禁地深处触发古老封印',                     '承前：退族之约'),
        ('封印：禁地真相',   '发展',  '林尘于禁地触发远古封印，卷入正邪大战的余波',                          '宏大',
         '"这是——万年前的战场。"',                                                  '正邪双方皆欲拉拢',                         '承前：禁地封印'),
        ('抉择：暗流涌动',   '发展',  '正邪两方使者在禁地外围对峙，林风被迫做出选择',                      '抉择',
         '"我不会做任何人的棋子。"',                                                '使者间剑拔弩张',                           '承前：禁地真相'),
        ('交易：与魔结盟',   '发展',  '林尘为自保与魔族达成秘密交易，得魔族庇护三年',                      '复杂',
         '"三年之内，我活着，你们的筹码就在。"',                                    '魔族内有不同声音',                         '承前：禁地抉择'),
        ('同行：旧友重逢',   '发展',  '柳青青再次现身，已是正道圣女，与林尘阵营对峙',                       '复杂情感',
         '"原来——是你。"',                                                          '两人立场冲突但不敌对',                     '承前：魔盟交易'),
        ('双线：布局天下',   '发展',  '林尘在魔族庇护下三年闭关，修为突飞猛进，暗中布局',                  '智谋',
         '"棋盘已经摆好，就看谁先落子。"',                                           '正邪两方暗流加剧',                         '承前：旧友重逢'),
        ('出关：风云再起',   '高潮',  '林尘三年期满出关，修为已至化神，天下格局已变',                       '壮阔',
         '"三年之期已到。"',                                                        '正邪大战一触即发',                         '承前：闭关布局'),
        ('大战：正邪对决',   '高潮',  '林尘于天下人面前独战正道七位化神老祖',                                '震撼',
         '"来吧，让我看看正道的本事！"',                                            '七老祖暗藏杀招',                           '承前：出关'),
        ('逆转：血脉觉醒',   '高潮',  '战斗中林尘神族血脉彻底觉醒，修为瞬间破境',                          '热血沸腾',
         '"血脉归来——神族不死！"',                                                  '神族之力引发天地异象',                     '承前：正邪大战'),
        ('抉择：天下大义',   '高潮',  '林尘觉醒神族之力后发现：所谓正邪大战实为神族阴谋',                  '震撼反转',
         '"原来——一切都是局。"',                                                    '神族真正身份浮出水面',                     '承前：血脉觉醒'),
        ('屠神：弑神之战',   '高潮',  '林尘决定挑战神族权威，独身闯入神界',                                  '悲壮决绝',
         '"今日——弑神。"',                                                          '神族诸神现身',                             '承前：天下大义'),
        ('真相：神族之秘',   '高潮',  '林尘于神界深处发现神族起源真相，三千世界皆是牢笼',                    '震撼哲学',
         '"这一切——必须终结。"',                                                    '神族之主现身',                             '承前：弑神之战'),
        ('终章：破界飞升',   '结局',  '林尘与神族之主终极对决，斩破三千世界壁垒，得证大道飞升上界',           '壮阔',
         '"凡尘三千，今日破之！"',                                                  '飞升之际，柳青青现身',                     '承前：弑神真相'),
    ]
    return [
        {
            'part': i + 1,
            'title': t,
            'phase': p,
            'word_count': 4500,
            'core_event': e,
            'emotion_target': m,
            'key_dialogue': d,
            'end_hook': h,
            'causality': c,
            'pacing': '稳进' if i < num_parts // 3 else ('冲突推进' if i < 2 * num_parts // 3 else '高潮收尾'),
            'foreshadow_plant': [],
            'foreshadow_reveal': [],
        }
        for i, (t, p, e, m, d, h, c) in enumerate(items[:num_parts])
    ]


async def main():
    print('=' * 70, flush=True)
    print('P0 修复验证 #2：minimax-m3 长篇（≥10万字）', flush=True)
    print(f'时间戳: {TIMESTAMP}', flush=True)
    print(f'输出目录: {RUN_DIR}', flush=True)
    print('=' * 70, flush=True)

    # 1. 配置检查
    from core.config import get_llm_config_for_agent, load_llm_config, reload_config
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
    num_parts = 25
    print(f'\n[Step 2] 创建 work: {work_id} ({num_parts} Parts × 4500字 ≈ 11万字)', flush=True)

    outline = make_long_outline(num_parts)
    initial_data = {
        'id': work_id,
        'title': 'P0修复验证#2：凡尘破界',
        'inspiration': '少年林尘出生即被预言天煞孤星，被家族封禁修为十年。血脉觉醒后踏上修仙之路，最终弑神破界。',
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat(),
        'phase': 'phase2_complete',
        'word_count': 0,
        'parts': {},
        'part_summaries': {},
        'part_outline': outline,
        'characters': [
            {'name': '林尘', 'role': '主角', 'identity': '被封禁修为的少年', 'core_trait': '不屈',
             'motivation': '破除神族阴谋', 'secret': '上古神族遗孤', 'arc': '从废柴到弑神者'},
            {'name': '柳青青', 'role': '女主', 'identity': '正道圣女', 'core_trait': '刚柔并济',
             'motivation': '追寻真相', 'secret': '神族后裔', 'arc': '从误会到并肩'},
            {'name': '守墓人', 'role': '导师', 'identity': '荒漠古城隐士', 'core_trait': '神秘',
             'motivation': '守护秘密', 'secret': '远古神族守护者', 'arc': '从隐居到复出'},
        ],
        'world_setting': '凡尘三千世界，修士修真界分正邪两道。神族隐于上界，统治万界。',
        'foreshadowing': [],
        'current_plot_state': '林尘在荒漠古城闭关',
        'character_state_track': {},
        'cost_summary': {'calls': [], 'estimated_cost_rmb': 0.0},
        'review_report': None,
        'final_draft': {},
    }
    work_file = BACKEND_ROOT.parent / 'data' / 'works' / f'{work_id}.json'
    work_file.write_text(json.dumps(initial_data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'  work 写入: {work_file}', flush=True)

    # 3. 强制 part_count = 25
    from services.writing_service import WritingService, TempStoryState
    from services.writing_phase_runners import Phase3Runner
    from core.progress_manager import progress_manager

    class FakeEmitter:
        async def emit(self, event_type, data, work_id=None):
            pass
        def emit_sync(self, event_type, data, work_id=None):
            pass

    fake_emitter = FakeEmitter()

    data = json.loads(work_file.read_text(encoding='utf-8'))
    service = WritingService(work_id, fake_emitter, resume=True)
    service.data = data
    service.cfg.confirm_mode = False
    # 关键：覆盖 part_count
    service.cfg._part_count = num_parts
    reload_config()
    print(f'  cfg.part_count 覆盖为: {service.cfg.part_count}', flush=True)
    # 替换 _request_confirm 为 no-op
    async def _no_confirm(confirm_id, message):
        pass
    service._request_confirm = _no_confirm
    progress_manager.reset_progress(work_id)
    temp_state = TempStoryState(data, memory=None, vector_store=service.vector_store)

    # 4. 跑 Phase 3（带 trace）
    print(f'\n[Step 3] 跑 Phase 3 逐 Part 写作 ({num_parts} parts × 4500字)...', flush=True)
    print(f'  预计耗时: {num_parts * 1.0:.0f}-{num_parts * 1.5:.0f} 分钟 (minimax-m3 ~30-60s/Part)', flush=True)
    runner = Phase3Runner(service)
    t_start = time.time()
    try:
        await runner.run(start_from=1)
    except Exception as e:
        print(f'❌ Phase 3 异常: {type(e).__name__}: {e}', flush=True)
        import traceback; traceback.print_exc()
    elapsed_total = time.time() - t_start
    print(f'\n  Phase 3 总耗时: {elapsed_total:.1f}s ({elapsed_total/60:.1f}min)', flush=True)

    # 5. 重新读 work 文件
    data = json.loads(work_file.read_text(encoding='utf-8'))
    parts = data.get('parts', {})
    print(f'  实际生成 Part 数: {len(parts)}/{num_parts}', flush=True)
    total_chars = sum(len(v) for v in parts.values() if isinstance(v, str))
    print(f'  累计字数: {total_chars} 字符 (~{total_chars//10000}万字)', flush=True)

    # 6. 全局密度检查
    print(f'\n[Step 4] 密度检查（阈值：…… ≤{ELLIPSIS_DENSITY_MAX}/1k, —— ≤{EMDASH_DENSITY_MAX}/1k）', flush=True)
    print(f'  重点抽检: Part {TAIL_PARTS_START}-{num_parts}（后半 12 Parts）', flush=True)
    results = []
    for k in sorted(parts.keys(), key=lambda x: int(x)):
        text = parts[k]
        if not isinstance(text, str):
            continue
        r = density_check(text)
        results.append((k, r))
        status = '✓' if r['verdict'] == 'PASS' else '✗'
        marker = '🔥' if int(k) >= TAIL_PARTS_START else '  '
        line = (
            f"  {status} {marker} Part {k:>2s}: {r['chars']:5d} chars | "
            f"…×{r['ellipsis_count']:3d} ({r['ellipsis_per_1k']:5.1f}/1k) | "
            f"——×{r['emdash_count']:3d} ({r['emdash_per_1k']:5.1f}/1k) | "
            f"3+…:{r['ellipsis_runs_3plus']} 2+——:{r['emdash_runs_2plus']}"
        )
        print(line, flush=True)

    # 7. 报告
    pass_count = sum(1 for _, r in results if r['verdict'] == 'PASS')
    fail_count = len(results) - pass_count
    # 后半统计
    tail_results = [(k, r) for k, r in results if int(k) >= TAIL_PARTS_START]
    tail_pass = sum(1 for _, r in tail_results if r['verdict'] == 'PASS')
    tail_max_ell = max((r['ellipsis_per_1k'] for _, r in tail_results), default=0)
    tail_max_emd = max((r['emdash_per_1k'] for _, r in tail_results), default=0)

    summary = {
        'timestamp': TIMESTAMP,
        'work_id': work_id,
        'provider': active,
        'model': cfg.model,
        'num_parts': num_parts,
        'actual_parts': len(parts),
        'total_chars': total_chars,
        'parts_passed': pass_count,
        'parts_failed': fail_count,
        'tail_parts_start': TAIL_PARTS_START,
        'tail_pass': tail_pass,
        'tail_total': len(tail_results),
        'tail_max_ellipsis_per_1k': tail_max_ell,
        'tail_max_emdash_per_1k': tail_max_emd,
        'parts_data': dict(results),
        'phase3_elapsed_sec': elapsed_total,
    }
    report_file = RUN_DIR / 'report.json'
    report_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

    work_copy = RUN_DIR / 'work.json'
    work_copy.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'\n[Step 5] 报告: {report_file}', flush=True)
    print(f'  全局: Parts PASS = {pass_count}/{len(results)}', flush=True)
    print(f'  后半(Part {TAIL_PARTS_START}-{num_parts}): PASS = {tail_pass}/{len(tail_results)}, '
          f'最大 … 密度 = {tail_max_ell:.1f}/1k, 最大 —— 密度 = {tail_max_emd:.1f}/1k', flush=True)

    # R4-P1-x: Phase 3 整体异常（parts 为空）时 results/tail_results 均为空，
    # 两个 != 比较都为 False → 假 PASS。必须先断言实际产出 Part 数与目标一致。
    if len(results) != num_parts or len(tail_results) != num_parts - TAIL_PARTS_START + 1:
        print(f'\n❌ 产出不完整：期望 {num_parts} Parts，实际检查 {len(results)} 个'
              f'（后半期望 {num_parts - TAIL_PARTS_START + 1}，实际 {len(tail_results)}）', flush=True)
        return False
    if pass_count != len(results) or tail_pass != len(tail_results):
        print(f'\n❌ 反凑字数修复未达标：', flush=True)
        if pass_count != len(results):
            print(f'  全局失败: {len(results) - pass_count} Parts', flush=True)
        if tail_pass != len(tail_results):
            print(f'  后半失败: {len(tail_results) - tail_pass} Parts', flush=True)
        return False
    print(f'\n✅ 长篇反凑字数修复已通过端到端验证', flush=True)
    print(f'  全局 {pass_count}/{len(results)} PASS | 后半 {tail_pass}/{len(tail_results)} PASS', flush=True)
    print(f'  对照 r14 baseline（后半 Part 19-30 ——— 密度 19-49/1k）', flush=True)
    print(f'  本次后半最大 ——— 密度: {tail_max_emd:.1f}/1k（降低 {(49 - tail_max_emd)/49*100:.0f}%）', flush=True)
    return True


if __name__ == '__main__':
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)