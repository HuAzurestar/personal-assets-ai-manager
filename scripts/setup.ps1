[CmdletBinding()]
param(
    [string]$VenvPath = ".venv"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonCommand = Get-Command python -ErrorAction Stop
$venvDirectory = Join-Path $projectRoot $VenvPath
$venvPython = Join-Path $venvDirectory "Scripts\python.exe"

& $pythonCommand.Source -m venv $venvDirectory
& $venvPython -m pip --isolated install -r (Join-Path $projectRoot "requirements.txt")

Write-Host "Setup complete. Start with: $venvPython run.py"
