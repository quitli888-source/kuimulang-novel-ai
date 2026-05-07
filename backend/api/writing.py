"""
番茄小说AI创作系统 V5 - 创作控制API
"""
import asyncio
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from .sse import get_emitter, EventType
from api.works import get_work_file
from services.writing_service import WritingService

router = APIRouter()


class StartWritingRequest(BaseModel):
    work_id: str


@router.post("/start")
async def start_writing(req: StartWritingRequest, background_tasks: BackgroundTasks):
    """启动创作流程（异步）"""
    print(f"[API] /writing/start 被调用, work_id={req.work_id}")
    work_path = get_work_file(req.work_id)
    if not work_path.exists():
        print(f"[API] 作品不存在: {work_path}")
        raise HTTPException(404, "作品不存在")
    
    print(f"[API] 作品存在，准备启动创作服务")

    emitter = get_emitter()

    # 后台运行创作
    async def run():
        print(f"[BackgroundTask] 创作任务开始执行")
        service = WritingService(req.work_id, emitter)
        try:
            await service.run()
            print(f"[BackgroundTask] 创作任务完成")
        except Exception as e:
            print(f"[BackgroundTask] 创作任务出错: {e}")
            await emitter.emit(EventType.ERROR, {"message": str(e)})

    background_tasks.add_task(run)
    print(f"[API] 创作任务已添加到background_tasks")

    return {"status": "started", "work_id": req.work_id}


class PauseRequest(BaseModel):
    work_id: str


@router.post("/pause")
def pause_writing(req: PauseRequest):
    """暂停创作（信号量通知）"""
    from services.writing_service import _pause_flags
    work_id = req.work_id
    if work_id in _pause_flags:
        _pause_flags[work_id].set()
    return {"status": "paused"}


@router.post("/resume/{work_id}")
async def resume_writing(work_id: str, background_tasks: BackgroundTasks):
    """恢复创作"""
    work_path = get_work_file(work_id)
    if not work_path.exists():
        raise HTTPException(404, "作品不存在")

    emitter = get_emitter()

    async def run():
        service = WritingService(work_id, emitter, resume=True)
        try:
            await service.run()
        except Exception as e:
            await emitter.emit(EventType.ERROR, {"message": str(e)})

    background_tasks.add_task(run)
    return {"status": "resumed", "work_id": work_id}


@router.get("/status/{work_id}")
def get_writing_status(work_id: str):
    """查询创作状态"""
    from services.writing_service import _writing_state
    if work_id in _writing_state:
        return _writing_state[work_id]
    return {"status": "idle"}


@router.post("/rewrite-part/{work_id}/{part_num}")
async def rewrite_part(work_id: str, part_num: int, background_tasks: BackgroundTasks):
    """重写指定Part"""
    emitter = get_emitter()

    async def run():
        service = WritingService(work_id, emitter)
        try:
            await service.rewrite_part(part_num)
        except Exception as e:
            await emitter.emit(EventType.ERROR, {"message": str(e)})

    background_tasks.add_task(run)
    return {"status": "rewriting", "part": part_num}
