"""
番茄小说AI创作系统 V5 - 会话管理系统
V5.1改动：实现会话的创建、保存、加载和管理功能
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from core.config import MEMORY_DIR
from core.story_state import StoryState
from core.logger import get_logger
logger = get_logger('session_manager')

class SessionManager:
    """
    会话管理系统
    """

    def __init__(self):
        self.sessions_dir = MEMORY_DIR / 'sessions'
        self.sessions_dir.mkdir(exist_ok=True)
        self.current_session_id: Optional[str] = None

    def create_session(self, inspiration: str, title: str=None) -> str:
        """
        创建新会话
        Args:
            inspiration: 灵感描述
            title: 会话标题
        Returns:
            str: 会话ID
        """
        import os as _os
        session_id = str(uuid.uuid4())
        session_data = {'session_id': session_id, 'title': title or f"创作会话_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}", 'inspiration': inspiration, 'created_at': datetime.now().isoformat(), 'updated_at': datetime.now().isoformat(), 'status': 'active', 'phase': 'init'}
        session_meta_path = self.sessions_dir / f'{session_id}.json'

        # P1-93: 先写临时文件 + os.replace 原子替换（替代之前的两次顺序 write）
        tmp_meta = session_meta_path.with_suffix('.json.tmp')
        try:
            with open(tmp_meta, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, ensure_ascii=False, indent=2)
            _os.replace(tmp_meta, session_meta_path)
        except Exception as meta_err:
            logger.info(f'[session_manager] session_meta 写盘失败: {meta_err}')
            try:
                if tmp_meta.exists():
                    tmp_meta.unlink()
            except Exception:
                logger.debug('session_manager: silent except (P2-19)', exc_info=True)
            raise

        story_state = StoryState(inspiration)
        story_state.phase = 'init'
        # P1-93: 同样走原子替换，避免进程崩溃在两次 write 中间产生 orphan state file
        state_path = self._get_state_path(session_id)
        tmp_state = state_path.with_suffix(state_path.suffix + '.tmp') if state_path.suffix else state_path.with_suffix('.tmp')
        try:
            story_state.save(str(tmp_state))
            _os.replace(tmp_state, state_path)
        except Exception as state_err:
            logger.info(f'[session_manager] story_state 写盘失败: {state_err}')
            try:
                if tmp_state.exists():
                    tmp_state.unlink()
            except Exception:
                logger.debug('session_manager: silent except (P2-19)', exc_info=True)
            # state 写失败时把已经写好的 meta 也回滚，避免出现 meta 在但 state 不在的悬空状态
            try:
                if session_meta_path.exists():
                    session_meta_path.unlink()
            except Exception:
                logger.debug('session_manager: silent except (P2-19)', exc_info=True)
            raise

        self.current_session_id = session_id
        return session_id

    def load_session(self, session_id: str) -> Optional[StoryState]:
        """
        加载会话
        Args:
            session_id: 会话ID
        Returns:
            Optional[StoryState]: 加载的StoryState对象
        """
        state_path = self._get_state_path(session_id)
        if not state_path.exists():
            return None
        story_state = StoryState('')
        story_state.load(str(state_path))
        self.current_session_id = session_id
        self._update_session_timestamp(session_id)
        return story_state

    def save_session(self, session_id: str, story_state: StoryState) -> bool:
        """
        保存会话
        Args:
            session_id: 会话ID
            story_state: StoryState对象
        Returns:
            bool: 保存是否成功
        """
        try:
            story_state.save(self._get_state_path(session_id))
            self._update_session_timestamp(session_id)
            self._update_session_status(session_id, story_state.phase)
            return True
        except Exception as e:
            logger.info(f'保存会话失败: {e}')
            return False

    def list_sessions(self) -> List[Dict[str, Any]]:
        """
        列出所有会话
        Returns:
            List[Dict[str, Any]]: 会话列表
        """
        sessions = []
        for session_file in self.sessions_dir.glob('*.json'):
            if session_file.name.endswith('.json') and (not session_file.name.endswith('_state.json')):
                try:
                    with open(session_file, 'r', encoding='utf-8') as f:
                        session_data = json.load(f)
                        sessions.append(session_data)
                except Exception as e:
                    logger.info(f'读取会话文件失败 {session_file}: {e}')
        return sorted(sessions, key=lambda x: x.get('updated_at', ''), reverse=True)

    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        获取会话信息
        Args:
            session_id: 会话ID
        Returns:
            Optional[Dict[str, Any]]: 会话信息
        """
        session_meta_path = self.sessions_dir / f'{session_id}.json'
        if not session_meta_path.exists():
            return None
        with open(session_meta_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def update_session_title(self, session_id: str, title: str) -> bool:
        """
        更新会话标题
        Args:
            session_id: 会话ID
            title: 新标题
        Returns:
            bool: 更新是否成功
        """
        session_meta_path = self.sessions_dir / f'{session_id}.json'
        if not session_meta_path.exists():
            return False
        try:
            with open(session_meta_path, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
            session_data['title'] = title
            session_data['updated_at'] = datetime.now().isoformat()
            with open(session_meta_path, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.info(f'更新会话标题失败: {e}')
            return False

    def delete_session(self, session_id: str) -> bool:
        """
        删除会话
        Args:
            session_id: 会话ID
        Returns:
            bool: 删除是否成功
        """
        try:
            session_meta_path = self.sessions_dir / f'{session_id}.json'
            if session_meta_path.exists():
                session_meta_path.unlink()
            state_path = self._get_state_path(session_id)
            if state_path.exists():
                state_path.unlink()
            if self.current_session_id == session_id:
                self.current_session_id = None
            return True
        except Exception as e:
            logger.info(f'删除会话失败: {e}')
            return False

    def get_current_session_id(self) -> Optional[str]:
        """
        获取当前会话ID
        Returns:
            Optional[str]: 当前会话ID
        """
        return self.current_session_id

    def set_current_session_id(self, session_id: str):
        """
        设置当前会话ID
        Args:
            session_id: 会话ID
        """
        self.current_session_id = session_id

    def _get_state_path(self, session_id: str) -> Path:
        """
        获取会话状态文件路径
        Args:
            session_id: 会话ID
        Returns:
            Path: 状态文件路径
        """
        return self.sessions_dir / f'{session_id}_state.json'

    def _write_session_meta_atomic(self, session_meta_path: Path, session_data: dict):
        """R4-P2-x: 原子写会话 meta —— 读-改-写 + 直接覆写会让并发/崩溃后的
        meta 文件丢失字段或留下截断 JSON。"""
        tmp_path = session_meta_path.with_suffix('.json.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
        import os as _os
        _os.replace(tmp_path, session_meta_path)

    def _update_session_timestamp(self, session_id: str):
        """
        更新会话时间戳
        Args:
            session_id: 会话ID
        """
        session_meta_path = self.sessions_dir / f'{session_id}.json'
        if not session_meta_path.exists():
            return
        try:
            with open(session_meta_path, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
            session_data['updated_at'] = datetime.now().isoformat()
            self._write_session_meta_atomic(session_meta_path, session_data)
        except Exception as e:
            logger.info(f'更新会话时间戳失败: {e}')

    def _update_session_status(self, session_id: str, phase: str):
        """
        更新会话状态
        Args:
            session_id: 会话ID
            phase: 当前阶段
        """
        session_meta_path = self.sessions_dir / f'{session_id}.json'
        if not session_meta_path.exists():
            return
        try:
            with open(session_meta_path, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
            if phase == 'init':
                status = 'active'
            elif phase == 'optimize':
                status = 'completed'
            else:
                status = 'active'
            session_data['status'] = status
            session_data['phase'] = phase
            self._write_session_meta_atomic(session_meta_path, session_data)
        except Exception as e:
            logger.info(f'更新会话状态失败: {e}')
session_manager = SessionManager()