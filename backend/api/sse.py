"""
番茄小说AI创作系统 V5 - SSE实时事件系统

通过 Server-Sent Events 将后端创作进度实时推送到前端。
V6改动：支持按workId隔离，防止多作品同时创作时消息混乱
R3-P0-5：① 队列 maxsize=10000（容纳 50 Part 任务的 ~610 events 峰值）；
        ② emit 改阻塞 put + 2s 超时，丢事件打 warning（不再静默 except: pass）；
        ③ event_seq 单调递增 + 写入 payload "id" 字段，前端 EventSource 重连
           自动带 Last-Event-ID header，后端 sse_stream 读 header 把 id 之后的事件补发。
"""
import json
import asyncio
import time
from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from core.logger import get_logger
logger = get_logger('sse')
router = APIRouter()
SSE_QUEUE_MAXSIZE = 10000
SSE_PUT_TIMEOUT_SEC = 2.0

class EventType:
    PHASE = 'phase'
    AGENT_CALL = 'agent_call'
    LOG = 'log'
    PART_COMPLETE = 'part_complete'
    SCORE = 'score'
    ERROR = 'error'
    CONFIRM = 'confirm'
    FINAL = 'final'
    HEARTBEAT = 'heartbeat'

class SSEEmitter:
    """
    SSE事件发射器 - V6版本按workId隔离

    订阅关系: work_id -> list of queues
    只有指定work_id的订阅者会收到该work_id的事件

    R3-P0-5：emit 不再静默 except:pass；改为阻塞 put + 2s 超时 + warning log。
    队列上限放宽到 10000。维护每 work_id 的 event_seq + 最近 N 条缓冲，用于
    Last-Event-ID 重连补发。
    """
    REPLAY_BUFFER_SIZE = 2000

    def __init__(self):
        self._subscriptions: dict[str, list[asyncio.Queue]] = {}
        self._event_seq: int = 0
        self._recent_events: dict[int, str] = {}
        self.dropped_count: int = 0
        self._hb_counter_offset: int = 0

    def _next_event_id(self) -> int:
        self._event_seq += 1
        return self._event_seq

    def subscribe(self, work_id: str) -> asyncio.Queue:
        """
        订阅指定workId的事件

        Args:
            work_id: 作品ID

        Returns:
            asyncio.Queue: 用于接收事件的队列
        """
        q = asyncio.Queue(maxsize=SSE_QUEUE_MAXSIZE)
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
            if not self._subscriptions[work_id]:
                del self._subscriptions[work_id]

    async def _put_to_queue(self, q: asyncio.Queue, payload: str) -> None:
        """R3-P0-5: 阻塞 put，超时后丢事件并 log warning（替代原 except:pass）。"""
        try:
            await asyncio.wait_for(q.put(payload), timeout=SSE_PUT_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            self.dropped_count += 1
            logger.info(f'[SSE] WARN: 队列满（maxsize={SSE_QUEUE_MAXSIZE}），丢事件 (累计丢弃 {self.dropped_count})')

    def get_replay_payloads(self, last_event_id: int) -> list[str]:
        """R3-P0-5: 返回 id > last_event_id 的最近事件（按 id 升序）。"""
        if not self._recent_events:
            return []
        candidate_ids = sorted((eid for eid in self._recent_events.keys() if eid > last_event_id))
        candidate_ids = candidate_ids[-self.REPLAY_BUFFER_SIZE:]
        return [self._recent_events[eid] for eid in candidate_ids]

    def _record_recent(self, event_id: int, payload: str) -> None:
        """R3-P0-5: 记录最近事件到环形缓冲。"""
        self._recent_events[event_id] = payload
        if len(self._recent_events) > self.REPLAY_BUFFER_SIZE:
            min_id = min(self._recent_events.keys())
            del self._recent_events[min_id]

    async def emit(self, event_type: str, data: dict, work_id: Optional[str]=None):
        """
        发送事件到指定workId的订阅者

        Args:
            event_type: 事件类型
            data: 事件数据
            work_id: 作品ID，如果为None则发送到所有订阅者（用于系统级事件）
        """
        event_id = self._next_event_id()
        payload = json.dumps({'type': event_type, 'data': data, 'ts': time.time(), 'id': event_id}, ensure_ascii=False)
        self._record_recent(event_id, payload)
        if work_id:
            queues = self._subscriptions.get(work_id, [])
            for q in queues:
                await self._put_to_queue(q, payload)
        else:
            for queues in self._subscriptions.values():
                for q in queues:
                    await self._put_to_queue(q, payload)

    def emit_sync(self, event_type: str, data: dict, work_id: Optional[str]=None):
        """
        同步发送事件（用于非异步上下文）

        Args:
            event_type: 事件类型
            data: 事件数据
            work_id: 作品ID，如果为None则发送到所有订阅者
        """
        event_id = self._next_event_id()
        payload = json.dumps({'type': event_type, 'data': data, 'ts': time.time(), 'id': event_id}, ensure_ascii=False)
        self._record_recent(event_id, payload)
        if work_id:
            queues = self._subscriptions.get(work_id, [])
            for q in queues:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    self.dropped_count += 1
                    logger.info(f'[SSE] WARN: 同步路径队列满，丢事件 (累计 {self.dropped_count})')
        else:
            for queues in self._subscriptions.values():
                for q in queues:
                    try:
                        q.put_nowait(payload)
                    except asyncio.QueueFull:
                        self.dropped_count += 1
                        logger.info(f'[SSE] WARN: 同步路径队列满，丢事件 (累计 {self.dropped_count})')
_emitter = SSEEmitter()

def get_emitter() -> SSEEmitter:
    return _emitter

@router.get('/stream')
async def sse_stream(request: Request, work_id: str=''):
    """
    SSE事件流，前端通过此端点接收实时事件

    Args:
        work_id: 作品ID，用于隔离不同作品的事件
                前端应传入当前正在查看/编辑的作品ID
    """
    if not work_id:
        from fastapi.responses import JSONResponse
        return JSONResponse({'error': 'work_id is required'}, status_code=400)
    last_event_id_raw = request.headers.get('Last-Event-ID', '0')
    try:
        last_event_id = int(last_event_id_raw)
    except (TypeError, ValueError):
        last_event_id = 0
    queue = _emitter.subscribe(work_id)

    async def event_generator():
        try:
            if last_event_id > 0:
                replay_payloads = _emitter.get_replay_payloads(last_event_id)
                for payload in replay_payloads:
                    yield f'data: {payload}\n\n'
        except Exception as replay_err:
            logger.info(f'[SSE] 补发 Last-Event-ID={last_event_id} 失败（不影响主流程）: {replay_err}')
        last_heartbeat = time.time()
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f'data: {payload}\n\n'
                    last_heartbeat = time.time()
                except asyncio.TimeoutError:
                    heartbeat = json.dumps({'type': EventType.HEARTBEAT, 'data': {'last_event_id': _emitter._event_seq, 'dropped_count': _emitter.dropped_count}, 'ts': time.time(), 'id': f'hb-{int(time.time() * 1000) - _emitter._hb_counter_offset}'}, ensure_ascii=False)
                    yield f'data: {heartbeat}\n\n'
                    last_heartbeat = time.time()
        except GeneratorExit:
            pass
        except Exception:
            pass
        finally:
            _emitter.unsubscribe(queue, work_id)
    return StreamingResponse(event_generator(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'})