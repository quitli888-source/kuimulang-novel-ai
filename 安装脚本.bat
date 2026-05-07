@echo off
chcp 65001 > nul
cd /d "%~dp0"
title 番茄小说AI创作系统 V5 - 安装程序

echo.
echo  ========================================
echo    番茄小说AI创作系统 V5  安装程序
echo  ========================================
echo.

:: 检查管理员权限（用于pip install）
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [提示] 建议右键选择"以管理员身份运行"以获得最佳体验
    echo.
)

:: =============================================
:: 第1步：检查Python
:: =============================================
echo [1/5] 检查Python环境...
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [错误] 未找到Python，请先安装 Python 3.10+
    echo   下载地址：https://www.python.org/downloads/
    echo.
    echo 安装后请重新运行本脚本
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   %PYVER%  ✓

:: =============================================
:: 第2步：安装后端依赖
:: =============================================
echo.
echo [2/5] 安装后端依赖（首次可能需要1-3分钟）...
echo   正在安装 fastapi, uvicorn, openai 等...

pip install -r backend\requirements.txt > pip_install.log 2>&1
if %errorLevel% neq 0 (
    echo   [错误] 后端依赖安装失败，详细信息见 pip_install.log
    type pip_install.log
    pause
    exit /b 1
)
echo   后端依赖安装完成  ✓

:: =============================================
:: 第3步：检查Node.js
:: =============================================
echo.
echo [3/5] 检查Node.js环境...
node --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [警告] 未找到Node.js，前端页面将无法启动
    echo   如需图形界面，请先安装：https://nodejs.org/
    echo   也可以直接使用命令行版本：python cli.py
    set NODE_MISSING=1
) else (
    for /f "delims=" %%v in ('node --version 2^>^&1') do set NODEVER=%%v
    echo   %NODEVER%  ✓

    :: =============================================
    :: 第4步：安装前端依赖
    :: =============================================
    echo.
    echo [4/5] 安装前端依赖（首次可能需要2-5分钟）...
    echo   正在安装 vue, naive-ui, vite 等...

    cd frontend
    call npm install > npm_install.log 2>&1
    if %errorLevel% neq 0 (
        echo   [错误] 前端依赖安装失败，详细信息见 npm_install.log
        type npm_install.log
        cd ..
        pause
        exit /b 1
    )
    cd ..
    echo   前端依赖安装完成  ✓
)

:: =============================================
:: 第5步：检查.env配置
:: =============================================
echo.
echo [5/5] 检查配置文件...
if not exist ".env" (
    echo   正在创建 .env 配置文件...
    copy .env.example .env > nul 2>&1
    echo   ✓ 已创建 .env 文件
    echo.
    echo   ===============================================
    echo   [重要] 请用记事本打开 .env 文件
    echo          填入你的 MiniMax API Key
    echo          然后重新运行 install.bat
    echo   ===============================================
    start notepad.exe .env
    pause
    exit /b 0
)
echo   .env 已存在  ✓

:: =============================================
:: 安装完成
:: =============================================
echo.
echo ================================================
echo    安装完成！
echo ================================================
echo.
if defined NODE_MISSING (
    echo   [注意] 未安装Node.js，将使用命令行模式运行
    echo   运行命令：python cli.py
) else (
    echo   运行方式：
    echo   双击 启动图形界面.bat  即可打开浏览器使用
    echo.
    echo   或在命令行运行：
    echo   python launcher.py
)
echo.
pause
