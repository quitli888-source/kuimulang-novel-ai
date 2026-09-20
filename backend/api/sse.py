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
import threading
import time
from bisect import bisect_left
from collections import OrderedDict
from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from core.logger import get_logger
logger = get_logger('sse')
router = APIRouter()
SSE_QUEUE_MAXSIZE = 10000
SSE_PUT_TIMEOUT_SEC = 2.0
MAX_SUBSCRIBERS_PER_WORK = 5  # P1-85: 每个 work 最多 5 个并发订阅（一般前端 ≤3 tab）

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
        # P1-84: 用 OrderedDict 保留插入顺序 + 单独 _recent_event_ids 数组
        #         get_replay_payloads 用 bisect 找 cutoff，避免每次 sorted() 全 keys（O(log N)）
        self._recent_events: "OrderedDict[int, str]" = OrderedDict()
        self._recent_event_ids: list[int] = []
        self.dropped_count: int = 0
        self._hb_counter_offset: int = 0
        # R4-P2-x: emit_sync 从 asyncio.to_thread 工作线程调 put_nowait ——
        # asyncio.Queue.put_nowait 不是线程安全的（内部 call_soon 只在本 loop
        # 被调度时唤醒），事件可能延迟到 15s 超时才被消费。seq/recent 记账同样
        # 需要锁保护，否则 _recent_event_ids 乱序会让 bisect 补发失效。
        self._seq_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """记录 SSE 消费方所在事件循环，供 emit_sync 跨线程安全投递。"""
        self._loop = loop

    def _next_event_id(self) -> int:
        with self._seq_lock:
            self._event_seq += 1
            return self._event_seq

    def subscribe(self, work_id: str) -> asyncio.Queue:
        """
        订阅指定workId的事件

        P1-85: 每个 work 最多 MAX_SUBSCRIBERS_PER_WORK 个订阅；超出返回 None 让上层返回 503。

        Returns:
            asyncio.Queue 或 None（订阅超限时）。
        """
        if work_id not in self._subscriptions:
            self._subscriptions[work_id] = []
        if len(self._subscriptions[work_id]) >= MAX_SUBSCRIBERS_PER_WORK:
            logger.info(f'[SSE] work_id={work_id} 订阅数达上限 {MAX_SUBSCRIBERS_PER_WORK}，拒绝新订阅')
            return None
        q = asyncio.Queue(maxsize=SSE_QUEUE_MAXSIZE)
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
        """P1-84: 返回 id > last_event_id 的最近事件（按 id 升序）。O(log N) bisect 查找。"""
        if not self._recent_event_ids:
            return []
        # bisect_left 找到第一个 > last_event_id 的位置
        idx = bisect_left(self._recent_event_ids, last_event_id + 1)
        candidate_ids = self._recent_event_ids[idx:][-self.REPLAY_BUFFER_SIZE:]
        return [self._recent_events[eid] for eid in candidate_ids]

    def _record_recent(self, event_id: int, payload: str) -> None:
        """R3-P0-5 + P1-84: 记录最近事件到环形缓冲；用 OrderedDict 保留顺序 + 同步 _recent_event_ids 数组。

        R4-P2-x: 加锁 —— emit（async 线程）与 emit_sync（工作线程）并发记账时，
        id=N 的记录可能晚于 id=N+1 落库，_recent_event_ids 乱序使 bisect 补发失效。
        """
        with self._seq_lock:
            self._recent_events[event_id] = payload
            self._recent_event_ids.append(event_id)
            # 环形裁剪：从 OrderedDict 头 + 数组头同步丢最旧
            while len(self._recent_events) > self.REPLAY_BUFFER_SIZE:
                old_id, _ = self._recent_events.popitem(last=False)
                # 数组里只追加未丢过的，丢掉的时候按值弹出第一个匹配
                # （必须在 while 内：缓冲未满时 old_id 未绑定，循环外引用即 UnboundLocalError）
                if self._recent_event_ids and self._recent_event_ids[0] == old_id:
                    self._recent_event_ids.pop(0)

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

        R4-P2-x: 从 asyncio.to_thread 工作线程调用时用 loop.call_soon_threadsafe
        投递 —— 直接 q.put_nowait() 跨线程操作 asyncio.Queue 不保证唤醒消费方
        （内部 call_soon 只在所属 loop 被调度时生效），事件可能延迟最多 15s。
        """
        event_id = self._next_event_id()
        payload = json.dumps({'type': event_type, 'data': data, 'ts': time.time(), 'id': event_id}, ensure_ascii=False)
        self._record_recent(event_id, payload)
        targets = []
        if work_id:
            targets = self._subscriptions.get(work_id, [])
        else:
            for queues in self._subscriptions.values():
                targets.extend(queues)
        for q in targets:
            self._put_threadsafe(q, payload)

    def _put_threadsafe(self, q: asyncio.Queue, payload: str) -> None:
        """跨线程安全投递；loop 不可用时（无订阅 loop 上下文）退回 put_nowait。"""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._put_nowait_safe, q, payload)
                return
            except RuntimeError:
                pass
        self._put_nowait_safe(q, payload)

    def _put_nowait_safe(self, q: asyncio.Queue, payload: str) -> None:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            self.dropped_count += 1
            logger.info(f'[SSE] WARN: 同步路径队列满，丢事件 (累计 {self.dropped_count})')
_emitter = SSEEmitter()

def get_emitter() -> SSEEmitter:
    return _emitter

def _sse_frame(payload: str) -> str:
    """R4-P2-x: 输出 SSE 协议 `id:` 字段 —— 此前只在 JSON body 里带 id，浏览器
    EventSource 不认，重连永远不发 Last-Event-ID，补发机制（get_replay_payloads /
    _recent_events）整体是死代码。解析失败时退回纯 data 帧，绝不影响事件推送。"""
    try:
        event_id = json.loads(payload).get('id')
        if event_id is not None:
            return f'id: {event_id}\ndata: {payload}\n\n'
    except Exception:
        pass
    return f'data: {payload}\n\n'


@router.get('/stream')
async def sse_stream(request: Request, work_id: str=''):
    """
    SSE事件流，前端通过此端点接收实时事件

    Args:
        work_id: 作品ID，用于隔离不同作品的事件
                前端应传入当前正在查看/编辑的作品ID
    """
    if not work_id:
        return JSONResponse({'error': 'work_id is required'}, status_code=400)
    last_event_id_raw = request.headers.get('Last-Event-ID', '0')
    try:
        last_event_id = int(last_event_id_raw)
    except (TypeError, ValueError):
        last_event_id = 0
    queue = _emitter.subscribe(work_id)
    if queue is not None:
        # R4-P2-x: 绑定消费方 loop —— emit_sync 从工作线程投递时需要它做
        # call_soon_threadsafe，否则事件可能延迟到 15s 心跳超时才被消费。
        _emitter.bind_loop(asyncio.get_running_loop())
    if queue is None:
        # P1-85: 订阅数超限，返回 503 让前端走重试 / 关多余 tab 流程
        return JSONResponse(
            {'error': f'该作品的实时订阅数已达上限 {MAX_SUBSCRIBERS_PER_WORK}，请关闭多余的标签页后重试', 'code': 'too_many_subscribers'},
            status_code=503,
        )

    async def event_generator():
        try:
            if last_event_id > 0:
                replay_payloads = _emitter.get_replay_payloads(last_event_id)
                for payload in replay_payloads:
                    yield _sse_frame(payload)
        except Exception as replay_err:
            logger.info(f'[SSE] 补发 Last-Event-ID={last_event_id} 失败（不影响主流程）: {replay_err}')
        last_heartbeat = time.time()
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield _sse_frame(payload)
                    last_heartbeat = time.time()
                except asyncio.TimeoutError:
                    heartbeat = json.dumps({'type': EventType.HEARTBEAT, 'data': {'last_event_id': _emitter._event_seq, 'dropped_count': _emitter.dropped_count}, 'ts': time.time(), 'id': f'hb-{int(time.time() * 1000) - _emitter._hb_counter_offset}'}, ensure_ascii=False)
                    yield _sse_frame(heartbeat)
                    last_heartbeat = time.time()
        except GeneratorExit:
            logger.debug('sse: silent except (P2-19)', exc_info=True)
        except Exception:
            logger.debug('sse: silent except (P2-19)', exc_info=True)
        finally:
            _emitter.unsubscribe(queue, work_id)
    return StreamingResponse(event_generator(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'})