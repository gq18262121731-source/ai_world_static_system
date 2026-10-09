<#
    一键启动开发环境（Windows PowerShell）。

    用法:
        .\scripts\dev.ps1              # 启动后端 + 前端
        .\scripts\dev.ps1 -Seed        # 先初始化演示数据再启动
        .\scripts\dev.ps1 -WithDocker  # 额外启动 PostgreSQL / Redis / EMQX
#>

param(
    [switch]$Seed,
    [switch]$WithDocker,
    [int]$BackendPort = 8080
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'backend\.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Host '未找到虚拟环境，正在创建…' -ForegroundColor Yellow
    python -m venv (Join-Path $root 'backend\.venv')
    & $python -m pip install -r (Join-Path $root 'backend\requirements.txt')
}

if (-not (Test-Path (Join-Path $root 'backend\.env'))) {
    Copy-Item (Join-Path $root 'backend\.env.example') (Join-Path $root 'backend\.env')
    Write-Host '已根据 .env.example 生成 backend\.env' -ForegroundColor Green
}

if ($WithDocker) {
    Write-Host '启动基础设施容器…' -ForegroundColor Cyan
    docker compose -f (Join-Path $root 'docker-compose.yml') up -d
}

if ($Seed) {
    Write-Host '初始化演示数据…' -ForegroundColor Cyan
    Push-Location (Join-Path $root 'backend')
    & $python -m app.seed
    Pop-Location
}

Write-Host "启动后端 http://127.0.0.1:$BackendPort …" -ForegroundColor Cyan
Start-Process -FilePath $python `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', "$BackendPort" `
    -WorkingDirectory (Join-Path $root 'backend')

Start-Sleep -Seconds 3

Write-Host '启动前端 http://localhost:5173 …' -ForegroundColor Cyan
Push-Location (Join-Path $root 'web')
if (-not (Test-Path 'node_modules')) { npm install }
npm run dev
Pop-Location
