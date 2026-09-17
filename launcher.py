"""
番茄小说AI创作系统 V5 - 启动器（跨平台：Windows / macOS / Linux）
自动检测环境、安装依赖、启动后端和前端
启动后自动打开浏览器

R26-P3-39:
- Windows:  双击 安装脚本.bat（首次）或 启动图形界面.bat / python launcher.py
- macOS:    python3 launcher.py
- Linux:    python3 launcher.py
- 安装脚本.bat 是 Windows 专用便捷封装，本文件是真正的跨平台入口。
"""
import sys
import os
import time
import webbrowser
import shutil
import traceback
import threading
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


def start_backend():
    """启动FastAPI后端"""
    import uvicorn

    print("🚀 启动后端服务...")

    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    # 切换到 backend 目录
    orig_cwd = os.getcwd()
    try:
        os.chdir(BACKEND_DIR)
        print(f"📁 工作目录: {os.getcwd()}")

        # R17-P0-4: 警告 —— 当前 data_service.py SQLite 长连接不支持多 worker
        # 多 worker 部署会出现 SQLite 锁错误 / 数据竞争 / 跨进程数据发散
        print("⚠️  注意：当前 SQLite 长连接仅支持单 worker（--workers=1）")
        print("   如需多 worker 部署，请把 data_service.py 改为短连接 + WAL 模式")

        uvicorn.run(
            "main:app",
            host="127.0.0.1",
            port=8000,
            log_level="info",
        )
    finally:
        os.chdir(orig_cwd)


def open_browser():
    """自动打开浏览器"""
    time.sleep(2)
    url = "http://127.0.0.1:8000"
    print(f"🌐 打开浏览器: {url}")
    webbrowser.open(url)


def main():
    try:
        print("=" * 50)
        print("🐺 奎木狼AI小说创作系统 V6")
        print("=" * 50)

        # 在后台线程中启动后端
        backend_thread = threading.Thread(target=start_backend, daemon=True)
        backend_thread.start()

        # 等待后端启动
        print("⏳ 等待后端启动...")
        time.sleep(4)

        # 打开浏览器
        open_browser()

        # 保持主线程运行
        print("🍅 系统已启动！按 Ctrl+C 停止服务")
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n🛑 停止服务...")
    except Exception as e:
        error_msg = f"启动失败: {e}\n{traceback.format_exc()}"
        print(error_msg, file=sys.stderr)
        input("按回车键退出...")


if __name__ == "__main__":
    main()