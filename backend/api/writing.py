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

@router.post('/start')
async def start_writing(req: StartWritingRequest, background_tasks: BackgroundTasks):
    """启动创作流程（异步）

    R5-P0-1: 增加可选 restart 字段。当 restart=True 时，把 phase 强制重置为
    'init' 并清空 parts/part_summaries，让 WritingService 从 Phase1 起跑。
    """
    logger.info(f'[API] /writing/start 被调用, work_id={req.work_id}, restart={req.restart}')
    work_path = get_work_file(req.work_id)
    if not work_path.exists():
        logger.info(f'[API] 作品不存在: {work_path}')
        raise HTTPException(404, '作品不存在')
    logger.info(f'[API] 作品存在，准备启动创作服务')
    emitter = get_emitter()

    async def run():
        logger.info(f'[BackgroundTask] 创作任务开始执行')
        service = WritingService(req.work_id, emitter, restart=req.restart)
        try:
            await service.run()
            logger.info(f'[BackgroundTask] 创作任务完成')
        except Exception as e:
            logger.info(f'[BackgroundTask] 创作任务出错: {e}')
            await emitter.emit(EventType.ERROR, {'message': str(e)})
    background_tasks.add_task(run)
    logger.info(f'[API] 创作任务已添加到background_tasks')
    return {'status': 'started', 'work_id': req.work_id, 'restart': req.restart}

class PauseRequest(BaseModel):
    work_id: str

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
    """恢复创作

    R5-P0-1: 该端点用于断点恢复 —— WritingService(resume=True) 读 phase 自动短路
    Phase1+2，从上次 phase3_part{N} 之后继续。前端 handleResume('continue') 必须调本端点。
    """
    work_path = get_work_file(work_id)
    if not work_path.exists():
        raise HTTPException(404, '作品不存在')
    emitter = get_emitter()

    async def run():
        service = WritingService(work_id, emitter, resume=True)
        try:
            await service.run()
        except Exception as e:
            await emitter.emit(EventType.ERROR, {'message': str(e)})
    background_tasks.add_task(run)
    return {'status': 'resumed', 'work_id': work_id}

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