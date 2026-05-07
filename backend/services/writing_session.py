"""
奎木狼AI小说创作系统 V6 - 创作会话管理
V6改动：使用上下文管理器封装WritingSession，支持多作品并发创作
"""
import asyncio
import threading
from typing import Optional, Dict, Any, Callable
from contextlib import contextmanager

from api.sse import SSEEmitter
from services.data_service import get_data_service


class WritingSession:
    """
    创作会话封装类

    使用上下文管理器确保：
    - 会话生命周期清晰
    - 资源正确释放
    - 支持多作品并发创作
    """

    _instances: Dict[str, "WritingSession"] = {}
    _lock = threading.Lock()

    def __init__(self, work_id: str, emitter: SSEEmitter, resume: bool = False):
        """
        初始化创作会话

        Args:
            work_id: 作品ID
            emitter: SSE事件发射器
            resume: 是否从断点恢复
        """
        self.work_id = work_id
        self.emitter = emitter
        self.resume = resume
        self.data_service = get_data_service()
        self.work_data: Optional[Dict[str, Any]] = None
        self._running = False
        self._paused = False
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        """异步上下文管理器入口"""
        self._running = True
        self.work_data = self.data_service.get_work(self.work_id)
        if not self.work_data:
            raise ValueError(f"作品 {self.work_id} 不存在")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        self._running = False
        if self.work_data:
            self.data_service.save_work_data(self.work_id, self.work_data)
        return False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    async def pause(self):
        """暂停创作"""
        async with self._lock:
            self._paused = True

    async def resume_writing(self):
        """恢复创作"""
        async with self._lock:
            self._paused = False

    def update_phase(self, phase: str):
        """更新当前阶段"""
        if self.work_data:
            self.work_data["phase"] = phase

    def update_progress(self, progress: int, message: str):
        """更新进度"""
        if self.work_data:
            self.work_data["progress"] = progress
            self.work_data["progress_message"] = message

    @classmethod
    def get_session(cls, work_id: str) -> Optional["WritingSession"]:
        """
        获取已存在的会话

        Args:
            work_id: 作品ID
        Returns:
            Optional[WritingSession]: 已存在的会话，不存在则返回None
        """
        with cls._lock:
            return cls._instances.get(work_id)

    @classmethod
    def register_session(cls, session: "WritingSession"):
        """
        注册会话

        Args:
            session: WritingSession实例
        """
        with cls._lock:
            cls._instances[session.work_id] = session

    @classmethod
    def unregister_session(cls, work_id: str):
        """
        注销会话

        Args:
            work_id: 作品ID
        """
        with cls._lock:
            if work_id in cls._instances:
                del cls._instances[work_id]

    @classmethod
    def get_all_active_sessions(cls) -> Dict[str, "WritingSession"]:
        """
        获取所有活跃会话

        Returns:
            Dict[str, WritingSession]: work_id -> session映射
        """
        with cls._lock:
            return dict(cls._instances)


@contextmanager
def get_writing_session(work_id: str, emitter: SSEEmitter, resume: bool = False):
    """
    同步上下文管理器，用于创建WritingSession

    Args:
        work_id: 作品ID
        emitter: SSE事件发射器
        resume: 是否恢复
    """
    session = WritingSession(work_id, emitter, resume)
    WritingSession.register_session(session)
    try:
        yield session
    finally:
        WritingSession.unregister_session(work_id)


# 全局创作状态存储
_writing_states: Dict[str, Dict[str, Any]] = {}
_pause_events: Dict[str, asyncio.Event] = {}
_confirm_events: Dict[str, Dict[str, Any]] = {}


def get_writing_state(work_id: str) -> Dict[str, Any]:
    """获取作品创作状态"""
    return _writing_states.get(work_id, {
        "phase": "idle",
        "current_part": 0,
        "total_parts": 0,
        "running": False,
        "paused": False,
    })


def set_writing_state(work_id: str, state: Dict[str, Any]):
    """设置作品创作状态"""
    _writing_states[work_id] = state


def get_pause_event(work_id: str) -> asyncio.Event:
    """获取暂停事件"""
    if work_id not in _pause_events:
        _pause_events[work_id] = asyncio.Event()
    return _pause_events[work_id]


def clear_pause_event(work_id: str):
    """清除暂停事件"""
    if work_id in _pause_events:
        _pause_events[work_id].clear()


def set_pause_event(work_id: str):
    """设置暂停事件"""
    if work_id in _pause_events:
        _pause_events[work_id].set()


def get_confirm_data(work_id: str) -> Dict[str, Any]:
    """获取确认数据"""
    return _confirm_events.get(work_id, {"event": asyncio.Event(), "result": ""})


def set_confirm_data(work_id: str, data: Dict[str, Any]):
    """设置确认数据"""
    _confirm_events[work_id] = data


def clear_writing_session(work_id: str):
    """清理创作会话相关状态"""
    if work_id in _writing_states:
        del _writing_states[work_id]
    if work_id in _pause_events:
        del _pause_events[work_id]
    if work_id in _confirm_events:
        del _confirm_events[work_id]
    WritingSession.unregister_session(work_id)
