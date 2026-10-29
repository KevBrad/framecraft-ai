$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$taskPython = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $taskPython = & py -3 -c 'import sys; print(sys.executable)'
    if ($LASTEXITCODE -ne 0) { $taskPython = $null }
}
if (-not $taskPython -and (Get-Command python -ErrorAction SilentlyContinue)) { $taskPython = (Get-Command python).Source }
if (-not $taskPython) { throw 'Install Python 3.12 and Node.js 20.19+ or 22.12+, then run this script again.' }
& $taskPython -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'
if ($LASTEXITCODE -ne 0) { throw 'A working Python 3.12+ installation is required.' }
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    & $taskPython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the project Python environment.' }
}
& '.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
Push-Location frontend
try { npm.cmd ci; if ($LASTEXITCODE -ne 0) { throw 'npm ci failed. Stop running Vite dev servers before setup; Windows locks esbuild.exe while it runs.' }; npm.cmd run build; if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' } }
finally { Pop-Location }
if (-not $env:FFMPEG_PATH -and -not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -and -not (Get-ChildItem 'tools' -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue)) {
    New-Item -ItemType Directory -Force tools | Out-Null
    Write-Host 'Downloading portable FFmpeg essentials (GPL build; see THIRD_PARTY_NOTICES.md)...'
    Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile 'tools\ffmpeg.zip'
    Expand-Archive -LiteralPath 'tools\ffmpeg.zip' -DestinationPath 'tools\ffmpeg' -Force
}
& '.venv\Scripts\python.exe' -c "from backend.config import configure;from backend.pipeline import dependency_status;print(dependency_status())"
if ($LASTEXITCODE -ne 0) { throw 'Media dependency verification failed.' }
Write-Host 'Ready. Run .\scripts\start.ps1 and open http://127.0.0.1:8000'
Write-Host 'Whisper weights download on first analysis. Run scripts\prepare_fixture.py to prepare the optional acceptance fixture.'
