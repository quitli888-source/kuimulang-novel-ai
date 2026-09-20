# -*- mode: python ; coding: utf-8 -*-
"""
番茄小说AI创作系统 V5 - PyInstaller打包配置

P3-108: 版本字符串改为单一来源（_APP_VERSION 常量），避免 EXE / COLLECT 两处硬编码。
P3-109: excludes 加入 stdlib 重模块（tkinter / unittest / pydoc 等），
        减小 binary 体积 30-50MB。
"""
import sys
from pathlib import Path

block_cipher = None

# 项目根目录（自动检测）
import os
PROJECT_DIR = Path(os.getcwd())

# P3-108: 单一版本字符串 —— 改版本只改这里
_APP_VERSION = "V6.1.01"
_APP_NAME = f"奎木狼AI小说创作系统_{_APP_VERSION}"

# 收集所有数据文件
datas = [
    # 后端核心模块
    (str(PROJECT_DIR / "backend"), "backend"),
    # Prompt模板
    (str(PROJECT_DIR / "prompts"), "prompts"),
    # 环境变量示例
    (str(PROJECT_DIR / ".env.example"), "."),
]

# 收集前端静态资源
frontend_dist = PROJECT_DIR / "frontend" / "dist"
if frontend_dist.exists():
    datas.append((str(frontend_dist), "frontend/dist"))

a = Analysis(
    [str(PROJECT_DIR / "launcher.py")],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "fastapi",
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "openai",
        "pydantic",
        "python_multipart",
        "starlette",
        "starlette.middleware",
        "starlette.middleware.cors",
        "starlette.staticfiles",
        "sse_starlette",
        "aiosqlite",
        "anyio",
        "certifi",
        "charset_normalizer",
        "h11",
        "httpcore",
        "httpx",
        "idna",
        "sniffio",
        "urllib3",
        "jiter",
        "pydantic_settings",
        "python_dotenv",
        "webbrowser",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # P3-109: 排除用不到的 stdlib，binary 体积减 30-50MB
    excludes=[
        "tkinter",
        "unittest",
        "unittest.mock",
        "doctest",
        "pydoc",
        "xmlrpc",
        "xmlrpc.client",
        "xmlrpc.server",
        "sqlite3",
        "test",
        "tests",
        "idlelib",
        "lib2to3",
        "pdb",
        "profile",
        "pstats",
        "curses",
        "turtledemo",
        "multiprocessing",  # uvicorn 用不到
        "email",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=_APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=_APP_NAME,
)