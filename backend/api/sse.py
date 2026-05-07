"""
番茄小说AI创作系统 V5 - SSE实时事件系统

通过 Server-Sent Events 将后端创作进度实时推送到前端。
V6改动：支持按workId隔离，防止多作品同时创作时消息混乱
"""
import json
import asyncio
import time
from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

# ---- 事件类型 ----
class EventType:
    PHASE = "phase"           # 阶段切换
    AGENT_CALL = "agent_call" # Agent开始/结束调用
    LOG = "log"               # 日志消息
    PART_COMPLETE = "part_complete" # Part完成
    SCORE = "score"           # 评分更新
    ERROR = "error"           # 错误
    CONFIRM = "confirm"       # 等待用户确认（低分/P0问题）
    FINAL = "final"           # 创作完成
    HEARTBEAT = "heartbeat"   # 心跳保活


class SSEEmitter:
    """
    SSE事件发射器 - V6版本按workId隔离

    订阅关系: work_id -> list of queues
    只有指定work_id的订阅者会收到该work_id的事件
    """

    def __init__(self):
        # { work_id: [queue1, queue2, ...] }
        self._subscriptions: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, work_id: str) -> asyncio.Queue:
        """
        订阅指定workId的事件

        Args:
            work_id: 作品ID

        Returns:
            asyncio.Queue: 用于接收事件的队列
        """
        q = asyncio.Queue(maxsize=100)
        if work_id not in self._subscriptions:
            self._subscriptions[work_id] = []
        self._subscriptions[work_id].append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue, work_id: str):
        """
        取消订阅

        Args:
            q: 之前subscribe返回的队列
            work_id: 作品ID
        """
        if work_id in self._subscriptions:
            if q in self._subscriptions[work_id]:
                self._subscriptions[work_id].remove(q)
            # 清理空列表
            if not self._subscriptions[work_id]:
                del self._subscriptions[work_id]

    async def emit(self, event_type: str, data: dict, work_id: Optional[str] = None):
        """
        发送事件到指定workId的订阅者

        Args:
            event_type: 事件类型
            data: 事件数据
            work_id: 作品ID，如果为None则发送到所有订阅者（用于系统级事件）
        """
        payload = json.dumps({"type": event_type, "data": data, "ts": time.time()}, ensure_ascii=False)

        if work_id:
            # 精准推送：只发送给指定work_id的订阅者
            queues = self._subscriptions.get(work_id, [])
            for q in queues:
                try:
                    await q.put_nowait(payload)
                except:
                    pass
        else:
            # 全局广播：发送给所有订阅者（兼容系统级事件）
            for queues in self._subscriptions.values():
                for q in queues:
                    try:
                        await q.put_nowait(payload)
                    except:
                        pass

    def emit_sync(self, event_type: str, data: dict, work_id: Optional[str] = None):
        """
        同步发送事件（用于非异步上下文）

        Args:
            event_type: 事件类型
            data: 事件数据
            work_id: 作品ID，如果为None则发送到所有订阅者
        """
        payload = json.dumps({"type": event_type, "data": data, "ts": time.time()}, ensure_ascii=False)

        if work_id:
            queues = self._subscriptions.get(work_id, [])
            for q in queues:
                try:
                    q.put_nowait(payload)
                except:
                    pass
        else:
            for queues in self._subscriptions.values():
                for q in queues:
                    try:
                        q.put_nowait(payload)
                    except:
                        pass


# 全局单例
_emitter = SSEEmitter()


def get_emitter() -> SSEEmitter:
    return _emitter


# ---- SSE端点 ----
@router.get("/stream")
async def sse_stream(request: Request, work_id: str = ""):
    """
    SSE事件流，前端通过此端点接收实时事件

    Args:
        work_id: 作品ID，用于隔离不同作品的事件
                前端应传入当前正在查看/编辑的作品ID
    """
    if not work_id:
        # 如果没有提供work_id，返回错误
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "work_id is required"}, status_code=400)

    queue = _emitter.subscribe(work_id)

    async def event_generator():
        # 发送心跳保持连接
        last_heartbeat = time.time()
        try:
            while True:
                try:
                    # 等待事件或心跳超时
                    payload = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"data: {payload}\n\n"
                    last_heartbeat = time.time()
                except asyncio.TimeoutError:
                    # 发送心跳
                    heartbeat = json.dumps({"type": EventType.HEARTBEAT, "data": {}, "ts": time.time()})
                    yield f"data: {heartbeat}\n\n"
                    last_heartbeat = time.time()
        except GeneratorExit:
            pass
        except Exception:
            pass
        finally:
            _emitter.unsubscribe(queue, work_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
