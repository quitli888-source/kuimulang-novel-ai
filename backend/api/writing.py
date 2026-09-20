"""
番茄小说AI创作系统 V5 - 创作控制API
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Optional
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
            await emitter.emit(EventType.ERROR, {'message': str(e), 'work_id': work_id}, work_id=work_id)
    return run


def _assert_not_running(work_id: str) -> None:
    """R4-P1-x: 同一作品只允许一个创作实例。

    此前 /run 不检查 _writing_state 的 running 标志（该字段只写不读），前端连点两次
    即产生两个 WritingService：各持一份内存 data、都调 _save()（虽已原子化但内容
    互相覆盖）、重写 part 索引错乱。并发启动直接 409。
    """
    from services.writing_service import _writing_state
    state = _writing_state.get(work_id)
    if state and state.get('running'):
        raise HTTPException(409, '该作品已有创作任务正在进行，请先暂停或等待完成')


@router.post('/run')
async def run_writing(req: RunRequest, background_tasks: BackgroundTasks):
    """P2-58: 合并 /writing/start 与 /writing/resume —— 单一 /run 端点，
    body 携带 restart / resume flag。保留旧端点作为兼容 shim。
    """
    logger.info(f'[API] /writing/run 被调用, work_id={req.work_id}, restart={req.restart}, resume={req.resume}')
    work_path = get_work_file(req.work_id)
    if not work_path.exists():
        raise HTTPException(404, '作品不存在')
    _assert_not_running(req.work_id)
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
    """重写指定Part

    P1-88: 外层 try/except 兜底，确保无论 service.rewrite_part() 是否 raise，
    前端 WritingProgress 永远能收到 PART_COMPLETE（成功 / 失败 words=0）。
    否则 UI 会处于等待 PART_COMPLETE 的悬挂状态。
    """
    emitter = get_emitter()

    async def run():
        try:
            service = WritingService(work_id, emitter)
            await service.rewrite_part(part_num)
        except Exception as e:
            await emitter.emit(EventType.ERROR, {'message': str(e), 'work_id': work_id}, work_id=work_id)
        finally:
            # 兜底 emit：确保前端 PART_COMPLETE 等待不会无限挂起
            await emitter.emit(EventType.PART_COMPLETE, {'part': part_num, 'words': 0, 'work_id': work_id, 'source': 'rewrite_fallback'}, work_id=work_id)
    background_tasks.add_task(run)
    return {'status': 'rewriting', 'part': part_num}

class _ConfirmRequest(BaseModel):
    work_id: str
    choice: str
    confirm_id: Optional[str] = None

@router.post('/confirm')
def writing_confirm(req: _ConfirmRequest):
    """处理用户对 phase / part 完成提示的确认选择

    R4-P1-x: confirm_id 由前端从 CONFIRM 事件回传，用于拒绝过期/错位响应
    （如 600s 超时自动 proceed 之后用户才点的取消，不得作用到下一个 confirm）。
    """
    from services.writing_service import WritingService
    WritingService.handle_confirm_response(req.work_id, req.choice, req.confirm_id)
    return {'ok': True}
    return {'ok': True, 'message': f'已收到您的选择: {req.choice}'}