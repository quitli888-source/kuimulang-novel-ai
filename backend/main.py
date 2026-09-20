"""
奎木狼AI小说创作系统 V6 - FastAPI 后端入口
V6改动：安全性加固（受限CORS、全局异常处理、请求限流）
"""
import sys
import os
import time
import threading
import logging
from collections import deque
from collections import OrderedDict

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
    # R4-P2-x: build.spec 把 backend 作为 data 放到 _internal/backend/，
    # 此前指向 _internal 本身（与打包布局不符；能工作只因 launcher 预插了 sys.path）。
    BACKEND_DIR = INTERNAL_DIR / "backend"
else:
    BASE_DIR = Path(__file__).parent.parent
    FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
    BACKEND_DIR = BASE_DIR


# P3-74 + P3-111: setup_logging 延后到 lifespan 启动期 —— 此时 init_app_config() 已经
# 设置好 DATA_DIR 等配置常量，log 文件能写到正确位置（之前模块顶层 import 时就
# 立即创建 log 目录 / FileHandler，DATA_DIR 路径虽已可用但仍属于"配置未初始化"状态）。
_setup_logging_pending = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _setup_logging_pending
    init_app_config()
    if _setup_logging_pending:
        from core.logger import setup_logging as _setup_logging  # noqa: E402
        _setup_logging('kuaimulang', logging.INFO)
        _setup_logging_pending = False
    yield


app = FastAPI(title="奎木狼AI小说创作系统 V6", version="V6.1.01", lifespan=lifespan)


# ---- 请求限流中间件 ----
class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    简单请求限流中间件
    限制每个IP的请求频率，防止滥用

    P0-78 + P1-82 修复：
    - 改用 deque(maxlen=requests_per_minute) 环形缓冲，避免 list 反复重建（O(1) 追加）
    - 用 LRU(OrderedDict) 限定 IP 表上限，防止随机造 IP 导致 dict 无限增长
    - _cleanup_expired 周期性裁剪长期不活跃的 IP
    - 默认只信任直连 request.client.host；仅当显式配置 TRUSTED_PROXIES 时才读 XFF
    """

    MAX_TRACKED_IPS = 5000
    CLEANUP_INTERVAL_SECONDS = 60

    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.request_counts: "OrderedDict[str, deque]" = OrderedDict()
        self._lock = threading.Lock()
        self._last_cleanup_ts = time.time()
        # P1-82: 信任的反代 IP/CIDR 列表（逗号分隔）；为空则忽略 X-Forwarded-For
        self.trusted_proxies = {
            ip.strip() for ip in os.getenv("TRUSTED_PROXIES", "").split(",") if ip.strip()
        }

    def _is_trusted_proxy(self, host: str) -> bool:
        if not host or not self.trusted_proxies:
            return False
        if host in self.trusted_proxies:
            return True
        # 简单子网匹配：10.0.0.0/8、127.0.0.0/8 等以 "10." / "127." / "192.168." 前缀处理
        for prefix in ("10.", "127.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31."):
            if host.startswith(prefix):
                return True
        return False

    def _get_client_ip(self, request: Request) -> str:
        direct = request.client.host if request.client else "unknown"
        # P1-82: 仅在反代可信时才采纳 XFF，否则直接用对端 IP（防伪造）
        if not self.trusted_proxies or not self._is_trusted_proxy(direct):
            return direct
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        return direct

    def _cleanup_expired(self, now: float) -> None:
        if now - self._last_cleanup_ts < self.CLEANUP_INTERVAL_SECONDS:
            return
        self._last_cleanup_ts = now
        cutoff = now - 60
        # 删除已无活跃请求的 IP（deque 全空）
        stale = [ip for ip, dq in self.request_counts.items() if not dq or dq[-1] <= cutoff]
        for ip in stale:
            self.request_counts.pop(ip, None)

    def _is_rate_limited(self, client_ip: str) -> bool:
        now = time.time()

        with self._lock:
            self._cleanup_expired(now)
            dq = self.request_counts.get(client_ip)
            if dq is None:
                dq = deque(maxlen=self.requests_per_minute)
                self.request_counts[client_ip] = dq
            else:
                self.request_counts.move_to_end(client_ip)
            # deque(maxlen=N) 自动覆盖最旧元素；只需要把窗口外的老记录裁掉
            while dq and dq[0] < now - 60:
                dq.popleft()
            if len(dq) >= self.requests_per_minute:
                return True
            dq.append(now)
            # P0-78: LRU 上限 —— 超限丢最久未活跃的 IP
            while len(self.request_counts) > self.MAX_TRACKED_IPS:
                self.request_counts.popitem(last=False)
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


# R4-P1-x: allow_credentials=True 与 allow_origins=["*"] 同时出现时，Starlette 会
# 回显请求 Origin 并附 Access-Control-Allow-Credentials: true —— 任意站点可发带
# 凭据的跨域请求。API Key 配置端点（/api/config/llm）在此组合下可被站外读取。
# 通配 origin 时强制关闭 credentials；需要凭据的场景请显式列 origin 白名单。
_ALLOW_CREDENTIALS = ALLOWED_ORIGINS != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=_ALLOW_CREDENTIALS,
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
    """处理所有未捕获的异常

    R4-P2-x: 不再把 str(exc) 透给客户端 —— 内部路径 / SQL / provider 报文会
    随响应体外泄。细节只进服务端日志，客户端给通用文案 + traceId。
    """
    import traceback
    import uuid as _uuid
    trace_id = _uuid.uuid4().hex[:12]
    logging.getLogger('main').error(f'[unhandled] trace_id={trace_id} path={request.url.path} err={exc!r}', exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "code": 1000,
            "message": f"系统内部错误（trace_id={trace_id}），请携带该 ID 反馈",
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
# （P1-83: 原代码 @app.post("/api/works") 重复定义两次，Python 后者覆盖前者，FastAPI 只注册一次。
# 现合并到唯一的 handler 上，并把 config_post_no_slash 也补齐。）


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
# P0-81 修复：
#   1. 用 {full_path:path} 显式名避免与其它路由变量名冲突
#   2. 用 .resolve() + startswith() 阻止 path traversal（...//etc/passwd）
#   3. 只对 "api/" 前缀返回 404 JSON，其它情况一律 fallback 到 index.html 让 Vue Router 接管
@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"error": f"API {full_path} not found"}, status_code=404)
    # R4-P1-x: 前端未构建时给可操作提示，而不是 FileResponse 的裸 500
    # （前端 dist 缺失是本机开发常见状态：npm run build 未执行）
    if not (FRONTEND_DIST / "index.html").is_file():
        return JSONResponse(
            {"error": "frontend_not_built", "message": "前端未构建：请在 frontend/ 目录执行 npm install && npm run build"},
            status_code=503,
        )
    try:
        file_path = (FRONTEND_DIST / full_path).resolve()
        frontend_root = FRONTEND_DIST.resolve()
        # 安全检查：阻止 path traversal（含同级前缀目录名 dist_backup 这类绕过）
        if frontend_root not in file_path.parents:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if file_path.is_file():
            return FileResponse(str(file_path))
    except (OSError, ValueError):
        # 路径非法（如含 NUL 等）时落到 SPA fallback
        pass
    # 其余全部走 Vue Router
    return FileResponse(str(FRONTEND_DIST / "index.html"))


@app.get("/")
async def root():
    if not (FRONTEND_DIST / "index.html").is_file():
        return JSONResponse(
            {"error": "frontend_not_built", "message": "前端未构建：请在 frontend/ 目录执行 npm install && npm run build"},
            status_code=503,
        )
    return FileResponse(str(FRONTEND_DIST / "index.html"))
