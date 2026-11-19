# Local-only helper: start the download worker on Windows.
#
#   .\scripts\run_worker.ps1
#
# The project .venv has a broken interpreter, so the system Python is pointed at
# its site-packages instead of activating the venv. In production the worker runs
# from the Dockerfile / a host with its own Python - this script is just for
# trying things on this machine.
$sitePackages = Join-Path (Split-Path $PSScriptRoot -Parent) '.venv\Lib\site-packages'
if (-not (Test-Path $sitePackages)) {
    Write-Host "Missing $sitePackages - create the venv first." -ForegroundColor Red
    exit 1
}
$env:PYTHONPATH = (Resolve-Path $sitePackages).Path

if (-not $env:WORKER_TOKEN) {
    Write-Host "WORKER_TOKEN is not set - POST /jobs will reject every request." -ForegroundColor Yellow
}
if (-not $env:INTERNAL_SECRET) {
    Write-Host "INTERNAL_SECRET is not set - the app will reject every callback." -ForegroundColor Yellow
}

Write-Host "Worker on http://127.0.0.1:8080/health (Ctrl+C to stop)" -ForegroundColor Cyan
python -m uvicorn worker_service:app --host 127.0.0.1 --port 8080