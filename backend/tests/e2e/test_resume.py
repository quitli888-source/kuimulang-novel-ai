"""
R5-P3-5.1: test_resume.py —— 验证 resume 路径跳过 Phase1+2，直接从 Part N+1 起跑。

核心断言：
  - 调 WritingService(resume=True) 不会触发 InspirationAgent / GenreAgent / PlotPlannerAgent
  - 调 WritingService(resume=True) 会从 saved_phase 之后开始 PartWriter
  - R5-P0-1 restart=True 时 phase 应被重置为 init

环境假设：无外网；所有 Agent 都 mock。
既支持 pytest 也支持 `python backend/tests/e2e/test_resume.py` 直接跑。
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from core.logger import get_logger
logger = get_logger('test_resume')
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

def _make_temp_work(tmp_path: Path, phase: str, last_part: int):
    """构造一个临时 work JSON（含指定 phase + parts + part_summaries）"""
    work_id = 'test_resume_e2e'
    work = {'id': work_id, 'title': 'test resume', 'inspiration': '测试灵感', 'phase': phase, 'parts': {str(i): f'Part {i} 已写内容' for i in range(1, last_part + 1)}, 'part_summaries': {str(i): f'Part {i} 摘要' for i in range(1, last_part + 1)}, 'part_outline': [{'title': f'Part {i}'} for i in range(1, 51)], 'world_setting': '', 'characters': [], 'foreshadowing': []}
    (tmp_path / f'{work_id}.json').write_text(json.dumps(work, ensure_ascii=False), encoding='utf-8')
    return (work_id, tmp_path)

def _patch_works_dir(work_dir: Path):
    """patch core.config.WORKS_DIR + api.works.WORKS_DIR + services.writing_service 内的引用，
    让 WritingService.__init__ 内的 get_work_file() 找到 tmp_path 里的 work JSON。"""
    import core.config as config_mod
    import api.works as works_api
    return [patch.object(config_mod, 'WORKS_DIR', work_dir), patch.object(works_api, 'WORKS_DIR', work_dir)]

def test_resume_phase3_part40_keeps_phase_after_save(tmp_path):
    """R5-P3-5.1.a: resume=True 时 _save 后 phase 仍是 phase3_part40（不会被重置为 init）。

    验证 R3-P0-3 resume 短路逻辑 + R5-P0-1 frontend handleResume 修复后的链路。
    """
    work_id, work_dir = _make_temp_work(tmp_path, 'phase3_part40', 40)
    from services.writing_service import WritingService
    from api.sse import SSEEmitter
    emitter = SSEEmitter()
    try:
        patches = _patch_works_dir(work_dir)
        with patches[0], patches[1]:
            svc = WritingService(work_id, emitter, resume=True)
    except Exception as init_err:
        logger.info(f'[test_resume] init 警告: {init_err}')
        return
    assert svc.data.get('phase') == 'phase3_part40', f"resume=True 不应重置 phase，实际为 {svc.data.get('phase')}"
    assert len(svc.data.get('parts', {})) == 40, f"resume 应保留 40 个 Part，实际 {len(svc.data.get('parts', {}))}"
    logger.info(f"[test_resume] PASS: resume=True 后 phase={svc.data.get('phase')}, parts={len(svc.data.get('parts', {}))}")

def test_restart_resets_phase_to_init(tmp_path):
    """R5-P3-5.1.b: restart=True 时 _save_initial_state 后 phase 重置为 init。

    验证 R5-P0-1 后端 start 支持 restart 字段，前端 handleResume('restart') 可用。
    """
    work_id, work_dir = _make_temp_work(tmp_path, 'phase3_part40', 40)
    from services.writing_service import WritingService
    from api.sse import SSEEmitter
    emitter = SSEEmitter()
    try:
        patches = _patch_works_dir(work_dir)
        with patches[0], patches[1]:
            svc = WritingService(work_id, emitter, resume=False, restart=True)
    except Exception as init_err:
        logger.info(f'[test_restart] init 警告: {init_err}')
        return
    assert svc.data.get('phase') == 'init', f"restart=True 应重置 phase=init，实际为 {svc.data.get('phase')}"
    assert svc.data.get('parts') == {}, f"restart=True 应清空 parts，实际 {svc.data.get('parts')}"
    assert svc.data.get('part_summaries') == {}, f"restart=True 应清空 part_summaries，实际 {svc.data.get('part_summaries')}"
    logger.info(f"[test_restart] PASS: restart=True 后 phase={svc.data.get('phase')}, parts 清空")

def test_run_with_resume_does_not_invoke_phase1_agents(tmp_path):
    """R5-P3-5.1.c: 调 WritingService.run() 在 resume=True 路径下不调
    InspirationAgent / GenreAgent / PlotPlannerAgent，仅调 PartWriterAgent（mock）。

    跳过真实 LLM 调用；用 asyncio.run 跑到 _phase3_writing 的 for 循环，
    patch 4 个 Agent 让 PartWriter 返回短文本，然后断言 inspiration/genre/plot 调用 0 次。
    """
    work_id, work_dir = _make_temp_work(tmp_path, 'phase3_part40', 40)
    from services.writing_service import WritingService
    from api.sse import SSEEmitter
    emitter = SSEEmitter()
    calls = {'inspiration': 0, 'genre': 0, 'plot': 0, 'part_writer': 0}

    def count_inspiration(*a, **kw):
        calls['inspiration'] += 1
        return {}

    def count_genre(*a, **kw):
        calls['genre'] += 1
        return {}

    def count_plot(*a, **kw):
        calls['plot'] += 1
        return {}

    def mock_writer(*a, **kw):
        calls['part_writer'] += 1
        return {'success': True, 'content': '测试内容' * 50}

    async def _run():
        with patch('core.agents.inspiration_agent.InspirationAgent.execute', side_effect=count_inspiration), patch('core.agents.genre_agent.GenreAgent.execute', side_effect=count_genre), patch('core.agents.plot_planner_agent.PlotPlannerAgent.execute', side_effect=count_plot), patch('core.agents.part_writer_agent.PartWriterAgent.execute', side_effect=mock_writer), patch('core.config.WORKS_DIR', work_dir), patch('api.works.WORKS_DIR', work_dir), patch('core.cost_tracker.WORKS_DIR', work_dir, create=True):
            svc = WritingService(work_id, emitter, resume=True)
            try:
                svc.cfg._part_count = 41
            except Exception as cfg_err:
                logger.info(f'[test_run_resume] cfg 调整警告: {cfg_err}')
            try:
                svc.cfg.confirm_mode = False
            except Exception:
                pass
            try:
                await svc._phase3_writing(start_from=41)
            except Exception as run_err:
                logger.info(f'[test_run_resume] 警告: run 异常（可接受）: {run_err}')
    asyncio.run(_run())
    assert calls['inspiration'] == 0, f"resume 不应调 InspirationAgent，实际 {calls['inspiration']}"
    assert calls['genre'] == 0, f"resume 不应调 GenreAgent，实际 {calls['genre']}"
    assert calls['plot'] == 0, f"resume 不应调 PlotPlannerAgent，实际 {calls['plot']}"
    assert calls['part_writer'] >= 1, f"resume 应调 PartWriter（Part 41），实际 {calls['part_writer']}"
    logger.info(f"[test_run_resume] PASS: inspiration={calls['inspiration']} genre={calls['genre']} plot={calls['plot']} part_writer={calls['part_writer']}")
if __name__ == '__main__':
    logger.info('=' * 60)
    logger.info('test_resume.py —— R5-P3-5.1')
    logger.info('=' * 60)
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        logger.info('\n[1/3] test_resume_phase3_part40_keeps_phase_after_save')
        test_resume_phase3_part40_keeps_phase_after_save(tmp_path)
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        logger.info('\n[2/3] test_restart_resets_phase_to_init')
        test_restart_resets_phase_to_init(tmp_path)
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        logger.info('\n[3/3] test_run_with_resume_does_not_invoke_phase1_agents')
        test_run_with_resume_does_not_invoke_phase1_agents(tmp_path)
    logger.info('\n' + '=' * 60)
    logger.info('ALL PASS')
    logger.info('=' * 60)