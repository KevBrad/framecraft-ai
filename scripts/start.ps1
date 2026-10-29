$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path '.venv\Scripts\python.exe')) { throw 'Run .\scripts\setup.ps1 first.' }
if (-not (Test-Path 'frontend\dist\index.html')) { throw 'Build the frontend first: npm.cmd --prefix frontend run build' }
Write-Host 'Framecraft: http://127.0.0.1:8000 -- Ctrl+C to stop. Keep this terminal open.'
& '.venv\Scripts\python.exe' -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
