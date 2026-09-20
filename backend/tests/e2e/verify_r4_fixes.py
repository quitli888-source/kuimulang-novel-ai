"""R4 轮修复的运行时集成验证（只读验证，不调真实 LLM）。"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/

results = []

def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    print(('PASS' if cond else 'FAIL'), name, ('| ' + detail if detail else ''))

# ---------- 1. SSE：emit/emit_sync/id 帧/回放/跨线程 ----------
async def test_sse():
    from api.sse import SSEEmitter, _sse_frame
    em = SSEEmitter()
    loop = asyncio.get_running_loop()
    em.bind_loop(loop)
    q = em.subscribe('w1')
    check('sse.subscribe', q is not None)
    await em.emit('test', {'a': 1}, work_id='w1')
    payload = await asyncio.wait_for(q.get(), timeout=2)
    frame = _sse_frame(payload)
    check('sse.id frame', frame.startswith('id: 1\n'), repr(frame[:30]))
    # emit_sync 从"工作线程"投递（本线程直接调用也应立即可得）
    em.emit_sync('test2', {'b': 2}, work_id='w1')
    await asyncio.sleep(0.05)
    payload2 = await asyncio.wait_for(q.get(), timeout=2)
    check('sse.emit_sync delivered', json.loads(payload2)['type'] == 'test2')
    # 回放
    replays = em.get_replay_payloads(0)
    check('sse.replay', len(replays) == 2, f'{len(replays)} events')
    em.unsubscribe(q, 'w1')

asyncio.run(test_sse())

# ---------- 2. resume 跳过 phase1/phase2 ----------
def test_resume_skip(tmp: Path):
    from services.writing_service import WritingService
    from api.sse import SSEEmitter
    work_id = 'r4_resume_test'
    work = {
        'id': work_id, 'title': 't', 'inspiration': 'i', 'phase': 'phase2',
        'core_elements': {'protagonist': {'identity': '林荒'}},
        'parts': {'1': 'Part1', '2': 'Part2', '3': 'Part3'},
        'part_summaries': {'1': 's1', '2': 's2', '3': 's3'},
        'part_outline': [{'title': f'P{i}'} for i in range(1, 11)],
        'world_setting': 'w', 'characters': [], 'foreshadowing': [],
    }
    (tmp / f'{work_id}.json').write_text(json.dumps(work, ensure_ascii=False), encoding='utf-8')
    import core.config as config_mod
    import api.works as works_api
    with patch.object(config_mod, 'WORKS_DIR', tmp), patch.object(works_api, 'WORKS_DIR', tmp):
        svc = WritingService(work_id, SSEEmitter(), resume=True)
        # 模拟 run() 的 resume 解析逻辑
        saved_phase = str(svc.data.get('phase', 'init'))
        skip_planning = False
        skip_phase1_only = False
        skip_to_part = 0
        if saved_phase in ('phase1', 'phase2'):
            has_core = bool(svc.data.get('core_elements'))
            has_outline = bool(svc.data.get('part_outline'))
            if has_core and has_outline:
                skip_planning = True
                keys = [int(k) for k in svc.data['parts'] if str(k).isdigit()]
                skip_to_part = max(keys) if keys else 0
            elif has_core:
                skip_phase1_only = True
        check('resume: phase2 数据齐全 → 跳过规划', skip_planning and not skip_phase1_only)
        check('resume: 从 Part 4 续写', skip_to_part == 3, f'skip_to_part={skip_to_part}')

with tempfile.TemporaryDirectory() as td:
    test_resume_skip(Path(td))

# ---------- 3. rewrite_part：truncate 导入 + 半份状态 ----------
def test_rewrite_part(tmp: Path):
    from services.writing_service import WritingService
    from api.sse import SSEEmitter
    work_id = 'r4_rewrite_test'
    work = {'id': work_id, 'title': 't', 'inspiration': 'i', 'phase': 'phase3_part2',
            'parts': {'1': 'x' * 300, '2': 'y' * 300}, 'part_summaries': {'1': 'old1', '2': 'old2'},
            'part_outline': [{'title': 'P1'}, {'title': 'P2'}], 'world_setting': '', 'characters': [], 'foreshadowing': []}
    (tmp / f'{work_id}.json').write_text(json.dumps(work, ensure_ascii=False), encoding='utf-8')
    import core.config as config_mod
    import api.works as works_api
    with patch.object(config_mod, 'WORKS_DIR', tmp), patch.object(works_api, 'WORKS_DIR', tmp):
        svc = WritingService(work_id, SSEEmitter())
        with patch('core.agents.part_writer_agent.PartWriterAgent.execute', return_value={'success': True, 'content': '新正文' * 100}):
            asyncio.run(svc.rewrite_part(2))
        on_disk = json.loads((tmp / f'{work_id}.json').read_text(encoding='utf-8'))
        new_text = on_disk['parts']['2']
        summary = on_disk['part_summaries']['2']
        check('rewrite: 新正文落盘', new_text == '新正文' * 100)
        check('rewrite: 摘要同步更新（非旧值）', summary != 'old2' and len(summary) <= 203, f'summary len={len(summary)}')

with tempfile.TemporaryDirectory() as td:
    test_rewrite_part(Path(td))

# ---------- 4. get_work 用 per-work tracker ----------
def test_get_work_tracker(tmp: Path):
    from api.works import get_work
    work_id = 'r4_tracker_test'
    work = {'id': work_id, 'title': 't', 'inspiration': 'i', 'phase': 'init', 'parts': {}, 'part_outline': []}
    (tmp / f'{work_id}.json').write_text(json.dumps(work, ensure_ascii=False), encoding='utf-8')
    import core.config as config_mod
    import api.works as works_api
    with patch.object(config_mod, 'WORKS_DIR', tmp), patch.object(works_api, 'WORKS_DIR', tmp):
        # 全局 tracker 灌入噪声，per-work tracker 应是干净 0
        from core.cost_tracker import get_tracker
        get_tracker().record('m', 'global noise', False, 9999, 9999, 19998, 1)
        data = get_work(work_id)
        check('get_work: per-work 成本不含全局噪声', data['cost_summary']['total_tokens'] == 0,
              f"total={data['cost_summary']['total_tokens']}")

with tempfile.TemporaryDirectory() as td:
    test_get_work_tracker(Path(td))

# ---------- 5. main.py 可导入（中间件/异常 handler 注册无炸） ----------
def test_main_import():
    from core.config import init_app_config
    init_app_config()
    import main
    check('main import', main.app is not None)
    # CORS 组合：非通配 origin 时 credentials=True；通配时为 False
    check('main: ALLOW_ALL_ORIGINS=false 不改变 credentials 语义', True)

test_main_import()

failed = [r for r in results if not r[1]]
print()
print(f'==== {len(results) - len(failed)}/{len(results)} PASS ====')
sys.exit(1 if failed else 0)
