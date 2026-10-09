@echo off
setlocal
cd /d "%~dp0"

set "VENV_PY=%~dp0backend\.venv\Scripts\python.exe"

echo ==========================================================
echo   设备模拟器 - 健康手表 + 陪护机器狗
echo ==========================================================
echo.

if not exist "%VENV_PY%" (
    echo [错误] 尚未初始化运行环境，请先运行 start.bat
    pause
    exit /b 1
)

echo   模拟器会持续向平台上报体征与位置，
echo   首页「老人实时体征」卡片的数值会随之实时变化。
echo.
echo   需要 MQTT Broker 时请先执行 docker compose up -d，
echo   未连接 Broker 也不影响平台其它功能。
echo.
echo   按 Ctrl+C 停止模拟器。
echo ----------------------------------------------------------
echo.

cd simulator
"%VENV_PY%" run_all.py

pause
