"""
番茄小说AI创作系统 - 文件日志系统 V3

提供结构化文件日志，记录创作全过程的LLM调用、Agent执行、评分等。
替代纯终端输出，方便后续分析和调试。
V6改动：添加统一logging配置，替换print()为logging
"""
import json
import time
import logging
import sys
from datetime import datetime
from typing import Optional

from core.config import DATA_DIR


# ---- 统一Logging配置 ----
def setup_logging(name: str = "kuaimulang", level: int = logging.INFO) -> logging.Logger:
    """
    设置统一日志配置

    Args:
        name: logger名称
        level: 日志级别
    Returns:
        logging.Logger: 配置好的logger实例
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重复添加handler
    if logger.handlers:
        return logger

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_formatter = logging.Formatter(
        "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # 文件输出
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(
        log_dir / f"{datetime.now().strftime('%Y%m%d')}.log",
        encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_formatter = logging.Formatter(
        "[%(asctime)s] [%(name)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    return logger


# 全局logger实例
app_logger = setup_logging("kuaimulang", logging.INFO)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    获取logger实例

    Args:
        name: 子模块名称，如 "writing_service", "agent"
    Returns:
        logging.Logger: logger实例
    """
    if name:
        return logging.getLogger(f"kuaimulang.{name}")
    return app_logger


# ---- 兼容print的日志函数 ----
def log_info(module: str, message: str):
    """记录信息日志"""
    get_logger(module).info(message)


def log_warning(module: str, message: str):
    """记录警告日志"""
    get_logger(module).warning(message)


def log_error(module: str, message: str, exc_info: Optional[Exception] = None):
    """记录错误日志"""
    if exc_info:
        get_logger(module).error(message, exc_info=True)
    else:
        get_logger(module).error(message)


def log_debug(module: str, message: str):
    """记录调试日志"""
    get_logger(module).debug(message)


# ---- 故事日志记录器（原有StoryLogger）----
class StoryLogger:
    """故事创作日志记录器"""

    def __init__(self, inspiration: str):
        self.inspiration = inspiration
        self.logs = []
        self.start_time = time.time()
        self.session_dir = DATA_DIR / "logs" / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def _add(self, category: str, message: str, data: dict = None):
        """添加一条日志"""
        entry = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "elapsed": round(time.time() - self.start_time, 1),
            "category": category,
            "message": message,
        }
        if data:
            entry["data"] = data
        self.logs.append(entry)

    def log_agent_start(self, agent_name: str, details: str = ""):
        self._add("agent_start", f"{agent_name} 开始执行", {"details": details} if details else None)

    def log_agent_done(self, agent_name: str, result_summary: str):
        self._add("agent_done", f"{agent_name} 完成", {"summary": result_summary})

    def log_agent_error(self, agent_name: str, error: str):
        self._add("agent_error", f"{agent_name} 错误: {error}")

    def log_llm_call(self, model: str, agent_name: str, is_json: bool, success: bool, duration_ms: float = 0):
        self._add("llm_call", f"LLM调用: {model} ({'JSON' if is_json else '文本'})",
                  {"agent": agent_name, "success": success, "duration_ms": round(duration_ms, 0)})

    def log_part_progress(self, part_num: int, stage: str, word_count: int = 0, score: float = 0):
        self._add("part_progress", f"Part {part_num} {stage}",
                  {"word_count": word_count, "score": score})

    def log_rewrite(self, part_num: int, reason: str):
        self._add("rewrite", f"Part {part_num} 触发重写: {reason}")

    def log_save(self, phase: str, filepath: str):
        self._add("save", f"{phase}: {filepath}")

    def save(self):
        """保存完整日志到JSON文件"""
        log_path = self.session_dir / "full_log.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(self.logs, f, ensure_ascii=False, indent=2)
        return log_path

    def save_part_review(self, part_num: int, logic_result: dict, emotion_result: dict):
        """保存单个Part的复核结果"""
        review_path = self.session_dir / f"part_{part_num:02d}_review.json"
        data = {"part": part_num, "logic": logic_result, "emotion": emotion_result}
        with open(review_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def save_summary(self, total_words: int, avg_logic: float, avg_emotion: float,
                     total_p0: int, total_p1: int, elapsed: float,
                     cost_summary: dict = None):
        """保存创作摘要（V4: 增加成本统计）"""
        summary = {
            "inspiration": self.inspiration,
            "total_words": total_words,
            "avg_logic_score": round(avg_logic, 1),
            "avg_emotion_score": round(avg_emotion, 1),
            "total_p0": total_p0,
            "total_p1": total_p1,
            "elapsed_seconds": round(elapsed, 0),
            "total_log_entries": len(self.logs),
        }
        if cost_summary:
            summary["cost"] = cost_summary
        summary_path = self.session_dir / "summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        return summary_path