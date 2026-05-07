"""
奎木狼AI小说创作系统 V6 - 统一异常处理
V6改动：定义统一异常类和错误码，便于全局处理和前端适配
"""
from typing import Optional, Dict, Any
from fastapi import HTTPException


# ---- 错误码定义 ----
class ErrorCode:
    """错误码枚举"""
    # 通用错误 (1000-1999)
    UNKNOWN_ERROR = 1000
    INVALID_PARAMETER = 1001
    RESOURCE_NOT_FOUND = 1002
    RESOURCE_ALREADY_EXISTS = 1003
    OPERATION_TIMEOUT = 1004

    # 作品相关错误 (2000-2999)
    WORK_NOT_FOUND = 2001
    WORK_ALREADY_EXISTS = 2002
    WORK_LOCKED = 2003
    WORK_READ_ONLY = 2004

    # 创作相关错误 (3000-3999)
    WRITING_IN_PROGRESS = 3001
    WRITING_PAUSED = 3002
    WRITING_COMPLETED = 3003
    WRITING_CANCELLED = 3004
    WRITING_AGENT_ERROR = 3005

    # AI/LLM相关错误 (4000-4999)
    LLM_API_ERROR = 4001
    LLM_API_KEY_MISSING = 4002
    LLM_API_KEY_INVALID = 4003
    LLM_RATE_LIMIT = 4004
    LLM_TIMEOUT = 4005
    LLM_RESPONSE_INVALID = 4006

    # 认证/权限错误 (5000-5999)
    AUTH_REQUIRED = 5001
    AUTH_FAILED = 5002
    PERMISSION_DENIED = 5003

    # 系统错误 (9000-9999)
    SYSTEM_BUSY = 9001
    DATABASE_ERROR = 9002
    FILE_SYSTEM_ERROR = 9003


# ---- 异常类定义 ----
class AppException(Exception):
    """应用基础异常类"""

    def __init__(
        self,
        code: int,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        suggestion: Optional[str] = None
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        self.suggestion = suggestion
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "suggestion": self.suggestion,
        }

    def to_http_exception(self) -> HTTPException:
        status_map = {
            # 4xx
            1001: 400,  # INVALID_PARAMETER
            1002: 404,  # RESOURCE_NOT_FOUND
            1003: 409,  # RESOURCE_ALREADY_EXISTS
            2001: 404,  # WORK_NOT_FOUND
            2002: 409,  # WORK_ALREADY_EXISTS
            2003: 423,  # WORK_LOCKED
            2004: 403,  # WORK_READ_ONLY
            3001: 409,  # WRITING_IN_PROGRESS
            3003: 400,  # WRITING_COMPLETED
            4002: 401,  # LLM_API_KEY_MISSING
            4003: 401,  # LLM_API_KEY_INVALID
            5001: 401,  # AUTH_REQUIRED
            5002: 401,  # AUTH_FAILED
            5003: 403,  # PERMISSION_DENIED
            # 5xx
            4001: 502,  # LLM_API_ERROR
            4004: 429,  # LLM_RATE_LIMIT
            4005: 504,  # LLM_TIMEOUT
            9001: 503,  # SYSTEM_BUSY
            9002: 500,  # DATABASE_ERROR
            9003: 500,  # FILE_SYSTEM_ERROR
        }
        status_code = status_map.get(self.code, 500)
        return HTTPException(status_code, detail=self.to_dict())


# ---- 常用异常工厂函数 ----
def work_not_found(work_id: str) -> AppException:
    return AppException(
        code=ErrorCode.WORK_NOT_FOUND,
        message=f"作品不存在: {work_id}",
        details={"work_id": work_id},
        suggestion="请检查作品ID是否正确，或重新创建作品"
    )


def invalid_parameter(param_name: str, reason: str) -> AppException:
    return AppException(
        code=ErrorCode.INVALID_PARAMETER,
        message=f"参数无效: {param_name}",
        details={"param": param_name, "reason": reason},
        suggestion="请检查参数格式和取值范围"
    )


def llm_api_error(original_error: str, model: str = "") -> AppException:
    return AppException(
        code=ErrorCode.LLM_API_ERROR,
        message=f"AI接口调用失败",
        details={"original_error": original_error, "model": model},
        suggestion="请检查网络连接和API配置，稍后重试"
    )


def writing_in_progress(work_id: str) -> AppException:
    return AppException(
        code=ErrorCode.WRITING_IN_PROGRESS,
        message="作品正在创作中",
        details={"work_id": work_id},
        suggestion="请等待当前创作完成，或暂停后重试"
    )


# ---- 全局异常处理器 ----
async def global_exception_handler(request, exc: Exception) -> Dict[str, Any]:
    """
    全局异常处理回调

    用于FastAPI的exception_handlers注册
    """
    if isinstance(exc, AppException):
        return exc.to_dict()

    # 处理HTTPException
    if isinstance(exc, HTTPException):
        return {
            "code": ErrorCode.UNKNOWN_ERROR,
            "message": exc.detail if isinstance(exc.detail, str) else exc.detail.get("message", "未知错误"),
            "details": {},
            "suggestion": "请联系管理员"
        }

    # 未知异常
    return {
        "code": ErrorCode.UNKNOWN_ERROR,
        "message": f"系统错误: {str(exc)}",
        "details": {},
        "suggestion": "请联系管理员处理"
    }
