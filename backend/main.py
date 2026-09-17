"""
奎木狼AI小说创作系统 V6 - FastAPI 后端入口
V6改动：安全性加固（受限CORS、全局异常处理、请求限流）
"""
import sys
import os
import time
import logging

# P2-29: 必须在任何 import 之前设置 Windows 终端编码（解决中文乱码）
# console.py 里的 os.environ["PYTHONIOENCODING"] 设置时机过晚（晚于其它模块 import），
# 必须在 main.py 最开头、任何业务模块 import 之前做完。
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass  # P2-19: silent fallback (no logger in module)

from pathlib import Path
from typing import Dict
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager

from api import works, config_api, writing, rewrite, sse
from core.config import init_app_config
from core.exceptions import AppException  # noqa: F401  (AppException 本身由本模块的 exception_handler 引用)

# PyInstaller 打包后的路径处理
if getattr(sys, 'frozen', False):
    EXE_DIR = Path(sys.executable).parent
    INTERNAL_DIR = EXE_DIR / "_internal"
    FRONTEND_DIST = INTERNAL_DIR / "frontend" / "dist"
    BACKEND_DIR = INTERNAL_DIR
else:
    BASE_DIR = Path(__file__).parent.parent
    FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
    BACKEND_DIR = BASE_DIR


# P3-74: 在 FastAPI app 构造之前 setup_logging —— 把根 logger 配文件 + 控制台 handler，
# 此前 setup_logging 只在 logger.py 模块加载时被动调用一次，且未绑给 root logger。
from core.logger import setup_logging as _setup_logging  # noqa: E402
_setup_logging('kuaimulang', logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_app_config()
    yield


app = FastAPI(title="奎木狼AI小说创作系统 V6", version="V6.1.01", lifespan=lifespan)


# ---- 请求限流中间件 ----
class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    简单请求限流中间件
    限制每个IP的请求频率，防止滥用
    """

    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.request_counts: Dict[str, list[float]] = {}

    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _is_rate_limited(self, client_ip: str) -> bool:
        now = time.time()
        minute_ago = now - 60

        if client_ip not in self.request_counts:
            self.request_counts[client_ip] = []

        # 清理过期的请求记录
        self.request_counts[client_ip] = [
            t for t in self.request_counts[client_ip] if t > minute_ago
        ]

        # 检查是否超过限制
        if len(self.request_counts[client_ip]) >= self.requests_per_minute:
            return True

        # 记录当前请求
        self.request_counts[client_ip].append(now)
        return False

    async def dispatch(self, request: Request, call_next):
        # 跳过健康检查的限流
        if request.url.path == "/api/health":
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        if self._is_rate_limited(client_ip):
            return JSONResponse(
                status_code=429,
                content={"error": "请求过于频繁，请稍后再试"}
            )
        return await call_next(request)


# ---- CORS配置 ----
# 允许的前端域名（开发环境可配置）
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]

# 如果需要允许所有域名（不推荐用于生产），可以从环境变量读取
if os.getenv("ALLOW_ALL_ORIGINS", "false").lower() == "true":
    ALLOWED_ORIGINS = ["*"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 添加限流中间件
app.add_middleware(RateLimitMiddleware, requests_per_minute=60)


# ---- 全局异常处理 ----
@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """处理应用自定义异常"""
    return JSONResponse(status_code=exc.to_http_exception().status_code, content=exc.to_dict())


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """处理HTTP异常"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": exc.detail if isinstance(exc.detail, str) else exc.detail.get("message", "HTTP错误"),
            "details": {},
            "suggestion": ""
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """处理所有未捕获的异常"""
    return JSONResponse(
        status_code=500,
        content={
            "code": 1000,
            "message": f"系统错误: {str(exc)}",
            "details": {},
            "suggestion": "请联系管理员处理"
        }
    )


# 注册路由
app.include_router(works.router, prefix="/api/works", tags=["作品管理"])
app.include_router(config_api.router, prefix="/api/config", tags=["配置管理"])
app.include_router(writing.router, prefix="/api/writing", tags=["创作控制"])
app.include_router(rewrite.router, prefix="/api/rewrite", tags=["AI改写"])
app.include_router(sse.router, prefix="/api/sse", tags=["实时事件"])


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "V6.1.01"}


# API路径不带斜杠的别名 - 转发到带斜杠的路由
@app.get("/api/works")
async def works_no_slash(request: Request):
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/api/works/", status_code=307)

@app.post("/api/works")
async def works_post_no_slash(request: Request):
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/api/works/", status_code=307)

@app.get("/api/config")
async def config_no_slash(request: Request):
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/api/config/", status_code=307)

@app.put("/api/config")
async def config_put_no_slash(request: Request):
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/api/config/", status_code=307)


# API路径不带斜杠的POST请求重定向
@app.post("/api/works")
async def works_post_no_slash(request: Request):
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/api/works/", status_code=307)


@app.post("/api/config")
async def config_post_no_slash(request: Request):
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/api/config/", status_code=307)


@app.post("/writing/confirm")
async def writing_confirm_no_slash(request: Request):
    from starlette.responses import RedirectResponse
    # V6.1: confirm 端点已迁移至 api/writing.py，保持向后兼容重定向
    return RedirectResponse(url="/api/writing/confirm", status_code=307)

# 前端SPA fallback
@app.get("/{path:path}")
async def serve_frontend(path: str):
    # 排除API路径
    if path.startswith("api/"):
        return JSONResponse({"error": "Not found"}, status_code=404)
    file_path = FRONTEND_DIST / path
    if file_path.is_file():
        return FileResponse(str(file_path))
    # 返回index.html让Vue Router处理
    return FileResponse(str(FRONTEND_DIST / "index.html"))


@app.get("/")
async def root():
    return FileResponse(str(FRONTEND_DIST / "index.html"))
