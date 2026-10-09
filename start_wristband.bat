@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "VENV_PY=%~dp0backend\.venv\Scripts\python.exe"

REM 广播板收全部手环的心率/血氧/体温/SOS；响应板轮转逐只拉血压/步数/电量。
REM 只有一块板时把 RESPONSE_PORT 留空，会自动退化成单板时间片轮转。
set "BROADCAST_PORT=COM12"
set "RESPONSE_PORT=COM13"

REM 现场手环 MAC，逗号分隔。列在这里的手环排在轮转队首且永不被当噪声拒绝；
REM 留空也能跑，未列出的手环靠广播自动发现（MAC 前缀 54:10:26）。
REM 注意这里不是白名单：无论填不填，所有可信手环都会上报。
set "TARGET_MACS=54:10:26:01:00:DC,54:10:26:01:00:DF,54:10:26:01:00:E7"

echo ==========================================================
echo   T10 手环采集网关 - 多手环模式
echo ==========================================================
echo.

if not exist "%VENV_PY%" (
    echo [错误] 尚未初始化运行环境，请先运行 start.bat
    pause
    exit /b 1
)

echo   广播板 %BROADCAST_PORT%   响应板 %RESPONSE_PORT%
echo   预置手环 %TARGET_MACS%
echo.
echo   端口对不上时先运行： python -m wristband_gateway.main ports
echo   串口一次只能被一个进程打开，报"拒绝访问"就是别的程序占着，
echo   用不带 -Force 的 Stop-Process 停掉它再等 3 秒。
echo.
echo   新发现的手环会自动登记为「未绑定」，
echo   需要到「设置 - 设备管理」里绑定到老人，数据才会入库。
echo.
echo   按 Ctrl+C 停止采集。
echo ----------------------------------------------------------
echo.

"%VENV_PY%" -u -m wristband_gateway.main multi ^
    --broadcast-port %BROADCAST_PORT% ^
    --response-port %RESPONSE_PORT% ^
    --target-macs %TARGET_MACS%

pause
