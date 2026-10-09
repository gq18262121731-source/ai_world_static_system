@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "BACKEND_PORT=8080"
set "FRONTEND_PORT=5173"
set "FOUND="

echo 正在停止 AIoT 居家养老健康平台...
echo.

REM 手环网关要先停、且不能强杀：强杀会让 COM 口句柄不释放，下次只能拔插 USB。
REM 用 Ctrl+C 等价的正常退出信号，等 3 秒让串口关干净。
for /f "usebackq" %%p in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'wristband_gateway' } | Select-Object -ExpandProperty ProcessId"`) do (
    echo   停止手环采集网关，进程 %%p
    powershell -NoProfile -Command "Stop-Process -Id %%p -ErrorAction SilentlyContinue"
    set "FOUND=1"
)
if defined FOUND ping -n 4 127.0.0.1 >nul

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%BACKEND_PORT% " ^| findstr LISTENING') do (
    echo   停止后端服务，端口 %BACKEND_PORT%
    taskkill /F /PID %%p >nul 2>&1
    set "FOUND=1"
)

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%FRONTEND_PORT% " ^| findstr LISTENING') do (
    echo   停止前端服务，端口 %FRONTEND_PORT%
    taskkill /F /PID %%p >nul 2>&1
    set "FOUND=1"
)

if not defined FOUND (
    echo   没有检测到正在运行的服务
) else (
    echo.
    echo   已全部停止
)

echo.
pause
