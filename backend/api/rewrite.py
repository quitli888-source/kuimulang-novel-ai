"""
番茄小说AI创作系统 V5 - AI辅助改写API
"""
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

router = APIRouter()


class RewriteRequest(BaseModel):
    text: str
    mode: str  # "polish" | "expand" | "summarize"
    context: str = ""  # 上下文


@router.post("/")
async def ai_rewrite(req: RewriteRequest):
    """AI改写接口 - 润色/扩写/缩写"""
    from services.rewrite_service import RewriteService
    from .sse import get_emitter

    service = RewriteService()
    result = await service.rewrite(req.text, req.mode, req.context)
    return result
