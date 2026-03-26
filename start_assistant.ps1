$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Error "Missing virtual environment at .venv"
}

& ".\.venv\Scripts\python.exe" "ai_with_tools.py"
