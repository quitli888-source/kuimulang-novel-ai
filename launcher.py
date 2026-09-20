"""
番茄小说AI创作系统 V5 - 启动器（跨平台：Windows / macOS / Linux）
自动检测环境、安装依赖、启动后端和前端
启动后自动打开浏览器

R26-P3-39:
- Windows:  双击 安装脚本.bat（首次）或 启动图形界面.bat / python launcher.py
- macOS:    python3 launcher.py
- Linux:    python3 launcher.py
- 安装脚本.bat 是 Windows 专用便捷封装，本文件是真正的跨平台入口。

P1-98: 后端健康检查 —— 启动浏览器前先轮询 /api/health，替代原 time.sleep(4) 硬等。
       慢机器上 uvicorn 实际启动 >4s 时浏览器会拿到 ERR_CONNECTION_REFUSED。
P3-110: 全部 print() 改走 logger（输出到 data/logs/launcher.log + 控制台）。
"""
import sys
import os
import time
import logging
import webbrowser
import shutil
import traceback
import threading
import urllib.request
from pathlib import Path

# PyInstaller 打包后的路径处理
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
    INTERNAL_DIR = BASE_DIR / "_internal"
    if str(INTERNAL_DIR) not in sys.path:
        sys.path.insert(0, str(INTERNAL_DIR))
else:
    BASE_DIR = Path(__file__).parent
    INTERNAL_DIR = None

# 打包后 backend 和 frontend 在 _internal 目录
if INTERNAL_DIR and INTERNAL_DIR.exists():
    BACKEND_DIR = INTERNAL_DIR / "backend"
else:
    BACKEND_DIR = BASE_DIR / "backend"

DEFAULT_PORT = 8000
LOG_DIR = BASE_DIR / "data" / "logs"
# R4-P2-x: import 期 mkdir 无保护 —— 安装到 Program Files 等只读位置时模块导入
# 即 PermissionError，而 logger 尚未建立，用户只看到裸 traceback。失败降级到临时目录。
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    import tempfile
    LOG_DIR = Path(tempfile.gettempdir()) / "kuimulang" / "logs"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _setup_launcher_logger() -> logging.Logger:
    """P3-110: 启动器独立 logger —— 文件 + 控制台双 handler。
    避免 print() 散落各处，且启动失败时能从 log 文件追溯。
    """
    logger = logging.getLogger('launcher')
    if logger.handlers:  # 避免重复挂 handler（多次 import）
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

    file_handler = logging.FileHandler(LOG_DIR / 'launcher.log', encoding='utf-8')
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    return logger


logger = _setup_launcher_logger()


def start_backend():
    """启动FastAPI后端"""
    import uvicorn

    logger.info("🚀 启动后端服务...")

    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    # 切换到 backend 目录
    orig_cwd = os.getcwd()
    try:
        os.chdir(BACKEND_DIR)
        logger.info(f"📁 工作目录: {os.getcwd()}")

        # R17-P0-4: 警告 —— 当前 data_service.py SQLite 长连接不支持多 worker
        # 多 worker 部署会出现 SQLite 锁错误 / 数据竞争 / 跨进程数据发散
        logger.warning("⚠️  注意：当前 SQLite 长连接仅支持单 worker（--workers=1）")
        logger.warning("   如需多 worker 部署，请把 data_service.py 改为短连接 + WAL 模式")

        uvicorn.run(
            "main:app",
            host="127.0.0.1",
            port=DEFAULT_PORT,
            log_level="info",
        )
    finally:
        os.chdir(orig_cwd)


def wait_backend_ready(url: str = f"http://127.0.0.1:{DEFAULT_PORT}/api/health", max_attempts: int = 30, interval: float = 0.5) -> bool:
    """P1-98: 轮询后端健康检查端点，直到就绪或超时。返回是否就绪。

    用 urllib（标准库）避免引入额外依赖；每次请求 timeout=0.5s，
    最多 30 次 * 0.5s = 15s 等待窗口，覆盖冷启动最坏情况。
    """
    logger.info(f"⏳ 等待后端健康检查就绪: {url}")
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if resp.status == 200:
                    logger.info(f"✅ 后端就绪（第 {attempt} 次尝试）")
                    return True
        except Exception as e:
            if attempt % 10 == 0:
                logger.info(f"  等待中（第 {attempt}/{max_attempts} 次）: {type(e).__name__}")
            time.sleep(interval)
    logger.error(f"❌ 后端启动超时（{max_attempts} 次尝试均失败），请检查端口 {url} 是否被占用或后端日志")
    return False


def open_browser(url: str = f"http://127.0.0.1:{DEFAULT_PORT}") -> None:
    """自动打开浏览器"""
    logger.info(f"🌐 打开浏览器: {url}")
    webbrowser.open(url)


def main():
    try:
        logger.info("=" * 50)
        logger.info("🐺 奎木狼AI小说创作系统 V6")
        logger.info("=" * 50)

        # 在后台线程中启动后端
        backend_thread = threading.Thread(target=start_backend, daemon=True)
        backend_thread.start()

        # P1-98: 健康检查替代硬等 sleep(4)
        if not wait_backend_ready():
            # R4-P2-x: 后端线程 daemon 化、内部异常不可见——此前健康检查失败后仍打印
            # "系统已启动"并进入 while True 空转，进程挂起无退出码，.bat 无法感知失败。
            logger.error("后端启动失败，退出。请查看 data/logs/launcher.log 与后端日志定位原因")
            sys.exit(1)
        else:
            open_browser()

        # 保持主线程运行
        logger.info("🍅 系统已启动！按 Ctrl+C 停止服务")
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("\n🛑 停止服务...")
    except Exception as e:
        error_msg = f"启动失败: {e}\n{traceback.format_exc()}"
        logger.error(error_msg)
        try:
            input("按回车键退出...")
        except EOFError:
            pass


if __name__ == "__main__":
    main()