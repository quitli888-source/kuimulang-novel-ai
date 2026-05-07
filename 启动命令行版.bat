@echo off
chcp 65001 > nul
cd /d "%~dp0"
title 奎木狼AI小说创作系统 V6 - 命令行版

echo ================================
echo   奎木狼AI小说创作系统 V6
echo   命令行模式
echo ================================
echo.

:: 检查.env是否存在
if not exist ".env" (
    echo [错误] 未找到配置文件 .env
    echo 请先运行 安装脚本.bat 进行安装
    pause
    exit /b 1
)

:: 启动后端
echo 正在启动后端服务...
start "奎木狼AI后端" /min cmd /c "cd /d "%~dp0backend" && python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"

echo.
echo 后端已启动在 http://127.0.0.1:8000
echo 前端已集成在 / 路径
echo.
echo 如需查看前端，请访问: http://127.0.0.1:8000
echo 或运行 启动图形界面.bat
echo.
pause
