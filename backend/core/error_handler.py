"""
番茄小说AI创作系统 V5 - 错误处理系统
V5.1改动：实现统一的错误处理和恢复机制
"""
import traceback
from typing import Dict, Any, Optional


class ErrorType:
    """错误类型定义"""
    AGENT_ERROR = "agent_error"      # Agent执行错误
    LLM_ERROR = "llm_error"          # LLM调用错误
    VALIDATION_ERROR = "validation_error"  # 输入验证错误
    NETWORK_ERROR = "network_error"    # 网络错误
    SYSTEM_ERROR = "system_error"      # 系统错误
    USER_ERROR = "user_error"          # 用户输入错误


class StoryError(Exception):
    """故事创作错误基类"""
    def __init__(self, error_type: str, message: str, details: Optional[Dict[str, Any]] = None):
        self.error_type = error_type
        self.message = message
        self.details = details or {}
        self.traceback = traceback.format_exc()
        super().__init__(message)


class AgentError(StoryError):
    """Agent执行错误"""
    def __init__(self, agent_name: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(ErrorType.AGENT_ERROR, message, {
            "agent_name": agent_name,
            **(details or {})
        })


class LLMError(StoryError):
    """LLM调用错误"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(ErrorType.LLM_ERROR, message, details)


class ValidationError(StoryError):
    """输入验证错误"""
    def __init__(self, field: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(ErrorType.VALIDATION_ERROR, message, {
            "field": field,
            **(details or {})
        })


class NetworkError(StoryError):
    """网络错误"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(ErrorType.NETWORK_ERROR, message, details)


class SystemError(StoryError):
    """系统错误"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(ErrorType.SYSTEM_ERROR, message, details)


class UserError(StoryError):
    """用户输入错误"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(ErrorType.USER_ERROR, message, details)


# R3-S3: 编程错误黑名单 —— NameError/AttributeError/TypeError/KeyError/IndexError
# （UnboundLocalError 是 NameError 子类，自动覆盖）。这类错误重试永远不可能成功，
# 必须立即 raise + error 级 traceback（langgraph RetryPolicy 白名单模式：未声明
# 可重试的异常绝不静默吞）。Round 1 `_os is not defined` / Round 2
# `chunk_target is not defined` 各被"重试后伪装空内容"吞过一次。
# 其余异常（网络/限流/供应商侧）维持现状 warning+重试；openai SDK 的
# APITimeoutError/RateLimitError（OpenAIError 家族）不在本名单，不受影响。
PROGRAMMING_ERRORS = (NameError, AttributeError, TypeError, KeyError, IndexError)


class ErrorHandler:
    """错误处理系统"""
    @staticmethod
    def handle_error(error: Exception) -> Dict[str, Any]:
        """
        处理错误，返回标准化的错误信息
        Args:
            error: 错误对象
        Returns:
            Dict[str, Any]: 标准化的错误信息
        """
        if isinstance(error, StoryError):
            # 已经是标准化错误
            return {
                "error_type": error.error_type,
                "message": error.message,
                "details": error.details,
                "traceback": error.traceback
            }
        else:
            # 非标准化错误，转换为系统错误
            return {
                "error_type": ErrorType.SYSTEM_ERROR,
                "message": str(error),
                "details": {},
                "traceback": traceback.format_exc()
            }

    @staticmethod
    def format_error_response(error: Exception) -> Dict[str, Any]:
        """
        格式化错误响应
        Args:
            error: 错误对象
        Returns:
            Dict[str, Any]: 错误响应
        """
        error_info = ErrorHandler.handle_error(error)
        return {
            "success": False,
            "error": error_info
        }

    @staticmethod
    def can_recover(error: Exception) -> bool:
        """
        判断错误是否可以恢复
        Args:
            error: 错误对象
        Returns:
            bool: 是否可以恢复
        """
        if isinstance(error, StoryError):
            # 特定类型的错误可以恢复
            recoverable_types = [
                ErrorType.AGENT_ERROR,
                ErrorType.LLM_ERROR,
                ErrorType.NETWORK_ERROR
            ]
            return error.error_type in recoverable_types
        return False

    @staticmethod
    def get_recovery_suggestion(error: Exception) -> Optional[str]:
        """
        获取错误恢复建议
        Args:
            error: 错误对象
        Returns:
            Optional[str]: 恢复建议
        """
        if isinstance(error, StoryError):
            if error.error_type == ErrorType.AGENT_ERROR:
                return "请检查Agent配置或尝试使用不同的Agent"
            elif error.error_type == ErrorType.LLM_ERROR:
                return "请检查API密钥或网络连接，稍后重试"
            elif error.error_type == ErrorType.NETWORK_ERROR:
                return "请检查网络连接，稍后重试"
            elif error.error_type == ErrorType.VALIDATION_ERROR:
                return "请修正输入参数后重试"
        return "请检查系统状态，稍后重试"


# 全局错误处理器实例
error_handler = ErrorHandler()