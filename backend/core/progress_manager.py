"""
番茄小说AI创作系统 V5 - 进度管理系统
V5.1改动：实现创作进度的跟踪和实时推送
"""
import time
from typing import Dict, Any, Optional, Callable
from api.sse import get_emitter, EventType


class ProgressManager:
    """
    进度管理系统
    """
    def __init__(self):
        self.emitter = get_emitter()
        self.current_progress: Dict[str, Any] = {
            "total": 0,
            "current": 0,
            "phase": "init",
            "agent": "",
            "message": "准备开始",
            "timestamp": time.time()
        }
        self.session_progress: Dict[str, Dict[str, Any]] = {}

    def update_progress(self, session_id: str, progress: int, message: str, agent: str = ""):
        """
        更新进度
        Args:
            session_id: 会话ID
            progress: 进度百分比 (0-100)
            message: 进度消息
            agent: Agent名称
        """
        # 更新全局进度
        self.current_progress = {
            "total": 100,
            "current": progress,
            "phase": "writing",
            "agent": agent,
            "message": message,
            "timestamp": time.time()
        }

        # 更新会话进度
        if session_id not in self.session_progress:
            self.session_progress[session_id] = {
                "total": 100,
                "current": 0,
                "phase": "init",
                "agent": "",
                "message": "准备开始",
                "timestamp": time.time()
            }

        self.session_progress[session_id] = {
            "total": 100,
            "current": progress,
            "phase": "writing",
            "agent": agent,
            "message": message,
            "timestamp": time.time()
        }

        # 推送进度事件
        self.emitter.emit_sync(EventType.LOG, {
            "level": "info",
            "message": message,
            "agent": agent,
            "progress": progress,
            "work_id": session_id,
        }, work_id=session_id)

        # 每10%推送一次进度事件
        if progress % 10 == 0 or progress == 100:
            self.emitter.emit_sync("progress", {
                "session_id": session_id,
                "progress": progress,
                "message": message,
                "agent": agent,
                "timestamp": time.time(),
                "work_id": session_id,
            }, work_id=session_id)

    def get_progress_callback(self, session_id: str, agent: str) -> Callable[[int, str], None]:
        """
        获取进度回调函数
        Args:
            session_id: 会话ID
            agent: Agent名称
        Returns:
            Callable[[int, str], None]: 进度回调函数
        """
        def callback(progress: int, message: str):
            self.update_progress(session_id, progress, message, agent)
        return callback

    def get_current_progress(self) -> Dict[str, Any]:
        """
        获取当前进度
        Returns:
            Dict[str, Any]: 当前进度
        """
        return self.current_progress

    def get_session_progress(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        获取会话进度
        Args:
            session_id: 会话ID
        Returns:
            Optional[Dict[str, Any]]: 会话进度
        """
        return self.session_progress.get(session_id)

    def reset_progress(self, session_id: str):
        """
        重置进度
        Args:
            session_id: 会话ID
        """
        if session_id in self.session_progress:
            del self.session_progress[session_id]

        # 重置全局进度
        self.current_progress = {
            "total": 0,
            "current": 0,
            "phase": "init",
            "agent": "",
            "message": "准备开始",
            "timestamp": time.time()
        }


# 全局进度管理器实例
progress_manager = ProgressManager()