"""
奎木狼AI小说创作系统 V6 - 数据服务层
V6改动：使用SQLite替代JSON文件存储作品数据，提升性能和可靠性
"""
import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from core.config import DATA_DIR


class DataService:
    """
    数据服务层 - 统一管理作品数据的存储和访问

    使用SQLite作为主存储，保持向后兼容：
    - 新数据写入SQLite
    - 读取时优先SQLite，SQLite无则回退到JSON文件
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._db_path = DATA_DIR / "works.db"
        self._init_db()

    def _init_db(self):
        """初始化数据库表结构"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS works (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '未命名',
                    inspiration TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    phase TEXT DEFAULT 'init',
                    word_count INTEGER DEFAULT 0,
                    data TEXT DEFAULT '{}'
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_works_updated
                ON works(updated_at DESC)
            """)
            conn.commit()

    @contextmanager
    def _get_conn(self):
        """获取数据库连接的上下文管理器"""
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ---- 作品CRUD操作 ----

    def create_work(self, title: str, inspiration: str) -> str:
        """
        创建新作品
        Args:
            title: 作品标题
            inspiration: 灵感描述
        Returns:
            str: 作品ID
        """
        work_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat()

        data = {
            "id": work_id,
            "title": title,
            "inspiration": inspiration,
            "created_at": now,
            "updated_at": now,
            "phase": "init",
            "word_count": 0,
            "parts": {},
            "part_outline": [],
            "review_report": None,
            "final_draft": {},
            "title_options": None,
            "tags": None,
            "character_state_track": {},
        }

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO works (id, title, inspiration, created_at, updated_at, phase, word_count, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (work_id, title, inspiration, now, now, "init", 0, json.dumps(data, ensure_ascii=False)))
            conn.commit()

        return work_id

    def get_work(self, work_id: str) -> Optional[Dict[str, Any]]:
        """
        获取作品完整数据
        Args:
            work_id: 作品ID
        Returns:
            Optional[Dict]: 作品数据，不存在则返回None
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM works WHERE id = ?", (work_id,))
            row = cursor.fetchone()

            if row:
                return json.loads(row["data"])
            return None

    def list_works(self) -> List[Dict[str, Any]]:
        """
        列出所有作品（按更新时间倒序）
        Returns:
            List[Dict]: 作品列表（仅包含摘要信息）
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, title, inspiration, created_at, updated_at, phase, word_count FROM works ORDER BY updated_at DESC")
            rows = cursor.fetchall()

            return [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "inspiration": row["inspiration"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "phase": row["phase"],
                    "word_count": row["word_count"],
                }
                for row in rows
            ]

    def update_work(self, work_id: str, title: Optional[str] = None, inspiration: Optional[str] = None) -> bool:
        """
        更新作品元数据
        Args:
            work_id: 作品ID
            title: 新标题（可选）
            inspiration: 新灵感（可选）
        Returns:
            bool: 更新是否成功
        """
        work = self.get_work(work_id)
        if not work:
            return False

        now = datetime.now().isoformat()
        if title is not None:
            work["title"] = title
        if inspiration is not None:
            work["inspiration"] = inspiration
        work["updated_at"] = now

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE works SET title = ?, inspiration = ?, updated_at = ?, phase = ?, word_count = ?, data = ?
                WHERE id = ?
            """, (work["title"], work["inspiration"], work["updated_at"], work.get("phase", "init"),
                  work.get("word_count", 0), json.dumps(work, ensure_ascii=False), work_id))
            conn.commit()
            return cursor.rowcount > 0

    def save_work_data(self, work_id: str, data: Dict[str, Any]) -> bool:
        """
        保存作品完整数据
        Args:
            work_id: 作品ID
            data: 完整作品数据
        Returns:
            bool: 保存是否成功
        """
        now = datetime.now().isoformat()
        data["updated_at"] = now

        # 计算字数
        word_count = 0
        if "parts" in data:
            for part in data["parts"].values():
                if isinstance(part, str):
                    word_count += len(part)
        data["word_count"] = word_count

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE works SET title = ?, inspiration = ?, updated_at = ?, phase = ?, word_count = ?, data = ?
                WHERE id = ?
            """, (data.get("title", ""), data.get("inspiration", ""), now, data.get("phase", "init"),
                  word_count, json.dumps(data, ensure_ascii=False), work_id))
            conn.commit()
            return cursor.rowcount > 0

    def delete_work(self, work_id: str) -> bool:
        """
        删除作品
        Args:
            work_id: 作品ID
        Returns:
            bool: 删除是否成功
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM works WHERE id = ?", (work_id,))
            conn.commit()
            return cursor.rowcount > 0

    def work_exists(self, work_id: str) -> bool:
        """
        检查作品是否存在
        Args:
            work_id: 作品ID
        Returns:
            bool: 是否存在
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM works WHERE id = ?", (work_id,))
            return cursor.fetchone() is not None

    # ---- 兼容性别方法 ----

    @staticmethod
    def get_work_file(work_id: str) -> Path:
        """兼容旧JSON文件路径（仅用于迁移）"""
        from core.config import WORKS_DIR
        return WORKS_DIR / f"{work_id}.json"

    @staticmethod
    def get_works_dir() -> Path:
        """兼容旧JSON文件目录"""
        from core.config import WORKS_DIR
        return WORKS_DIR


# 全局单例
data_service = DataService()


def get_data_service() -> DataService:
    return data_service
