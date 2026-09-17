"""
番茄小说AI创作系统 V5 - 创作控制API
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from .sse import get_emitter, EventType
from api.works import get_work_file
from services.writing_service import WritingService
from core.logger import get_logger
logger = get_logger('writing')
router = APIRouter()

class StartWritingRequest(BaseModel):
    work_id: str
    restart: bool = False

class PauseRequest(BaseModel):
    work_id: str

class RunRequest(BaseModel):
    """P2-58: 合并 start + resume 为单一 /run 端点，restart / resume 由 body 控制。"""
    work_id: str
    restart: bool = False
    resume: bool = False


async def _launch_run(work_id: str, emitter, *, restart: bool = False, resume: bool = False):
    """P2-58: start / resume 共用的 BackgroundTask 工厂 —— 消除两份闭包重复。"""
    async def run():
        service = WritingService(work_id, emitter, restart=restart, resume=resume)
        try:
            await service.run()
        except Exception as e:
            logger.info(f'[BackgroundTask] 创作任务出错: {e}')
            await emitter.emit(EventType.ERROR, {'message': str(e)})
    return run


@router.post('/run')
async def run_writing(req: RunRequest, background_tasks: BackgroundTasks):
    """P2-58: 合并 /writing/start 与 /writing/resume —— 单一 /run 端点，
    body 携带 restart / resume flag。保留旧端点作为兼容 shim。
    """
    logger.info(f'[API] /writing/run 被调用, work_id={req.work_id}, restart={req.restart}, resume={req.resume}')
    work_path = get_work_file(req.work_id)
    if not work_path.exists():
        raise HTTPException(404, '作品不存在')
    emitter = get_emitter()
    run = await _launch_run(req.work_id, emitter, restart=req.restart, resume=req.resume)
    background_tasks.add_task(run)
    mode = 'restart' if req.restart else ('resume' if req.resume else 'start')
    return {'status': mode, 'work_id': req.work_id}


@router.post('/start')
async def start_writing(req: StartWritingRequest, background_tasks: BackgroundTasks):
    """P2-58: 兼容 shim —— 转发到 /run（保持旧前端调用方工作）。"""
    new_req = RunRequest(work_id=req.work_id, restart=req.restart, resume=False)
    return await run_writing(new_req, background_tasks)

@router.post('/pause')
def pause_writing(req: PauseRequest):
    """暂停创作（信号量通知）"""
    from services.writing_service import _pause_flags
    work_id = req.work_id
    if work_id in _pause_flags:
        _pause_flags[work_id].set()
    return {'status': 'paused'}

@router.post('/resume/{work_id}')
async def resume_writing(work_id: str, background_tasks: BackgroundTasks):
    """P2-58: 兼容 shim —— 转发到 /run（resume=True）。"""
    new_req = RunRequest(work_id=work_id, restart=False, resume=True)
    return await run_writing(new_req, background_tasks)

@router.get('/status/{work_id}')
def get_writing_status(work_id: str):
    """查询创作状态"""
    from services.writing_service import _writing_state
    if work_id in _writing_state:
        return _writing_state[work_id]
    return {'status': 'idle'}

@router.post('/rewrite-part/{work_id}/{part_num}')
async def rewrite_part(work_id: str, part_num: int, background_tasks: BackgroundTasks):
    """重写指定Part"""
    emitter = get_emitter()

    async def run():
        service = WritingService(work_id, emitter)
        try:
            await service.rewrite_part(part_num)
        except Exception as e:
            await emitter.emit(EventType.ERROR, {'message': str(e)})
    background_tasks.add_task(run)
    return {'status': 'rewriting', 'part': part_num}

class _ConfirmRequest(BaseModel):
    work_id: str
    choice: str

@router.post('/confirm')
def writing_confirm(req: _ConfirmRequest):
    """处理用户对 phase / part 完成提示的确认选择"""
    from services.writing_service import WritingService
    WritingService.handle_confirm_response(req.work_id, req.choice)
    return {'ok': True, 'message': f'已收到您的选择: {req.choice}'}