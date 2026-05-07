@echo off
chcp 65001 > nul
cd /d "%~dp0"
title 奎木狼AI小说创作系统 V6

echo ================================
echo   奎木狼AI小说创作系统 V6
echo ================================
echo.

echo 正在启动后端服务 (端口 8000)...
start "奎木狼AI后端" /min cmd /c "cd /d "%~dp0backend" && python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"

timeout /t 3 /nobreak > nul

echo.
echo 系统已启动！正在打开浏览器...
start http://localhost:8000

echo.
echo ================================
echo   访问地址: http://localhost:8000
echo   如果浏览器没有自动打开，请手动访问
echo ================================
pause
