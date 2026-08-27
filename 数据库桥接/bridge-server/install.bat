@echo off
setlocal enabledelayedexpansion
cd /d %~dp0

echo ============================================
echo  galaxy-bridge 一键安装（Windows x64）
echo ============================================

REM ---- 1. 检查 Python 版本（需 3.10 ~ 3.13 x64）----
python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,13) and sys.maxsize>2**32 else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未检测到合适的 Python：需要 64 位 Python 3.10~3.13。
    echo         请安装 Python 3.12 或 3.13 x64 并勾选 Add to PATH 后重试。
    exit /b 1
)

for /f %%i in ('python -c "import sys;print('cp'+str(sys.version_info.major)+str(sys.version_info.minor))"') do set PYVER=%%i
echo [1/4] 检测到 Python !PYVER! x64

REM ---- 2. 安装通用依赖 ----
echo [2/4] 安装通用依赖...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || goto :fail

REM ---- 3. 安装银河官方 wheel（AmazingData 会自动带上 tgw 依赖声明，这里显式都装）----
echo [3/4] 安装 AmazingData / tgw wheel...
python -m pip install "..\AmazingData\AmazingData-1.1.9-!PYVER!-none-any.whl" || goto :fail
python -m pip install "..\tgw-1.0.9.2-py3-none-any.whl" || goto :fail

REM ---- 4. 自检 ----
echo [4/4] 环境自检...
python test_smoke.py
if errorlevel 1 goto :fail

echo.
echo [OK] 安装完成。下一步：
echo      1) 编辑 config.ini 填写 [galaxy] 账号/密码/IP/端口
echo      2) 双击 run_server.bat 启动服务
exit /b 0

:fail
echo.
echo [ERROR] 安装失败，请把上方完整报错截图留存后排查。
exit /b 1
