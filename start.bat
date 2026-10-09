@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "BACKEND_PORT=8080"
set "FRONTEND_PORT=5173"
set "VENV_PY=%~dp0backend\.venv\Scripts\python.exe"
set "BOOTSTRAP_PY=python"
if defined PYTHON_EXE set "BOOTSTRAP_PY=%PYTHON_EXE%"

REM 清除可能从系统环境继承的同名变量，确保配置只来自 backend\.env
set "DATABASE_URL="
set "REDIS_URL="
set "MQTT_HOST="
set "MQTT_PORT="
set "LLM_PROVIDER="
set "LLM_API_KEY="

echo ==========================================================
echo   AIoT 居家养老健康平台 - 一键启动
echo ==========================================================
echo.

where npm >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Node.js，请先安装 Node.js 18 或更高版本
    echo        下载地址 https://nodejs.org/
    pause
    exit /b 1
)

if not exist "%VENV_PY%" goto create_venv
"%VENV_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto rebuild_venv
echo [1/5] 后端环境已就绪
goto venv_done

:rebuild_venv
echo [1/5] 检测到不可用的 Python 虚拟环境，正在重建...
rmdir /s /q "backend\.venv"
goto bootstrap_venv

:create_venv
echo [1/5] 首次运行，正在创建 Python 虚拟环境...

:bootstrap_venv
"%BOOTSTRAP_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto python_missing
"%BOOTSTRAP_PY%" -m venv backend\.venv
if errorlevel 1 goto venv_create_failed
echo       正在安装后端依赖，可能需要 1-3 分钟...
"%VENV_PY%" -m pip install --upgrade pip -q
"%VENV_PY%" -m pip install -r backend\requirements.txt -q
if errorlevel 1 goto dependency_install_failed
goto venv_done

:python_missing
echo [错误] 未检测到 Python 3.11 或更高版本
echo        可先设置 PYTHON_EXE 指向 Python 3.11，例如：
echo        set PYTHON_EXE=C:\Path\To\Python311\python.exe
echo        下载地址 https://www.python.org/downloads/
pause
exit /b 1

:venv_create_failed
echo [错误] 创建虚拟环境失败
pause
exit /b 1

:dependency_install_failed
echo [错误] 安装后端依赖失败
pause
exit /b 1

:venv_done

if not exist "backend\.env" (
    copy /y "backend\.env.example" "backend\.env" >nul
    echo [2/5] 已创建配置文件 backend\.env
) else (
    echo [2/5] 配置文件已就绪
)

if not exist "backend\aiot_elder.db" (
    echo [3/5] 正在初始化数据库和演示数据...
    pushd backend
    "%VENV_PY%" -m app.seed
    popd
) else (
    echo [3/5] 数据库已存在，跳过初始化
    echo       如需重建演示数据库，请删除 backend\aiot_elder.db 后重新运行本脚本
)

if not exist "web\node_modules" (
    echo [4/5] 首次运行，正在安装前端依赖，可能需要 1-3 分钟...
    pushd web
    call npm install --no-audit --no-fund
    popd
) else (
    echo [4/5] 前端环境已就绪
)

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%BACKEND_PORT% " ^| findstr LISTENING') do (
    echo       释放被占用的后端端口 %BACKEND_PORT%
    taskkill /F /PID %%p >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%FRONTEND_PORT% " ^| findstr LISTENING') do (
    echo       释放被占用的前端端口 %FRONTEND_PORT%
    taskkill /F /PID %%p >nul 2>&1
)

echo [5/5] 正在启动服务...
start "AIoT-Backend" /D "%~dp0backend" "%VENV_PY%" -m uvicorn app.main:app --host 0.0.0.0 --port %BACKEND_PORT%
start "AIoT-Frontend" /D "%~dp0web" cmd /k "chcp 65001 >nul && npm run dev"

echo       等待服务就绪...
ping -n 12 127.0.0.1 >nul

netstat -ano | findstr ":%BACKEND_PORT% " | findstr LISTENING >nul
if errorlevel 1 (
    echo [错误] 后端服务未能启动，请查看 AIoT-Backend 窗口中的错误信息
    pause
    exit /b 1
)

netstat -ano | findstr ":%FRONTEND_PORT% " | findstr LISTENING >nul
if errorlevel 1 (
    echo [错误] 前端服务未能启动，请查看 AIoT-Frontend 窗口中的错误信息
    pause
    exit /b 1
)

start "" "http://localhost:%FRONTEND_PORT%"

echo.
echo ==========================================================
echo   启动完成
echo ----------------------------------------------------------
echo   管理后台   http://localhost:%FRONTEND_PORT%
echo   接口文档   http://127.0.0.1:%BACKEND_PORT%/docs
echo.
echo   演示账号
echo     admin  / admin123     平台管理员，可查看全部数据
echo     staff  / staff123     养老机构工作人员
echo     family / family123    家属，仅可查看关联老人
echo.
echo   演示操作：刷新或单独按 Ctrl 推进一步，Ctrl+Alt+S 重置
echo   系统打开后会停在当前演示状态，不会自动推进。
echo   每次刷新页面，演示数据都会向前推进一步并产生新的状态。
echo   按 Ctrl+Alt+S 可随时重置演示数据。
echo.
echo   建议按以下顺序在“设备模拟器”页面完成演示：
echo     1. 将老人健康档位设为“血氧偏低”。
echo     2. 说“我想出去走走”，系统会拒绝并说明原因。
echo     3. 将老人健康档位恢复为“健康平稳”，再说一次，通过外出评估。
echo     4. 点击“向外走 5 步”，查看轨迹进入电子围栏外。
echo     5. 依次点击两个跌倒按钮，观察双源融合为同一条告警。
echo     6. 说“我没事”，完成语音确认并自动关闭告警。
echo ----------------------------------------------------------
echo   停止服务：运行 stop.bat 关闭后端与前端窗口
echo ==========================================================
echo.
pause
