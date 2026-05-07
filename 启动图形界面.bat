@echo off
chcp 65001 > nul
cd /d "%~dp0"
title 奎木狼AI小说创作系统 V6

echo ================================
echo   奎木狼AI小说创作系统 V6
echo ================================
echo.

:: 检查.env是否存在
if not exist ".env" (
    echo [错误] 未找到配置文件 .env
    echo 请先运行 安装脚本.bat 进行安装
    pause
    exit /b 1
)

:: 检查API Key是否已配置
findstr /C:"DEEPSEEK_API_KEY=sk-" .env > nul 2>&1
if %errorLevel% neq 0 (
    findstr /C:"DEEPSEEK_API_KEY=your_api_key_here" .env > nul 2>&1
    if %errorLevel% equ 0 (
        echo [错误] 请先配置 .env 文件中的 DeepSeek API Key
        echo   用记事本打开：notepad .env
        start notepad.exe .env
        pause
        exit /b 1
    )
)

echo [1/2] 正在启动后端服务 (端口 8000)...
start "奎木狼AI后端" /min cmd /c "cd /d "%~dp0backend" && python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"

echo [2/2] 等待服务启动...
timeout /t 3 /nobreak > nul

echo.
echo 正在打开浏览器...
start http://127.0.0.1:8000

echo.
echo ================================
echo   系统已启动！
echo   访问地址: http://127.0.0.1:8000
echo   按任意键打开安装脚本目录...
echo ================================
pause > nul
