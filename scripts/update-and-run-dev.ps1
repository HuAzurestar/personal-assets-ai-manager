[CmdletBinding()]
param(
    [string]$VenvPath = ".venv",
    [switch]$SkipTests,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

function Stop-Safely([string]$message) {
    Write-Error $message
    exit 1
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Stop-Safely "Git is required. Install Git for Windows, then rerun this script." }
if (-not (Test-Path (Join-Path $projectRoot ".git"))) { Stop-Safely "This folder is not a Git clone; no update was attempted." }

$dirty = (& git status --porcelain)
if ($dirty) { Stop-Safely "Local changes detected. Commit, stash, or copy them elsewhere before updating; this script will not overwrite user files." }

& git fetch origin main
if ($LASTEXITCODE -ne 0) { Stop-Safely "git fetch failed. Check network access and the origin remote; your files are unchanged." }
$head = (& git rev-parse HEAD).Trim()
$remote = (& git rev-parse origin/main).Trim()
$base = (& git merge-base HEAD origin/main).Trim()
if ($head -ne $base) { Stop-Safely "Local branch is not a fast-forward ancestor of origin/main. Resolve the branch manually; no merge was performed." }
if ($head -ne $remote) {
    & git merge --ff-only origin/main
    if ($LASTEXITCODE -ne 0) { Stop-Safely "Fast-forward update failed. Resolve it manually; no forced update was used." }
}

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "setup.ps1") -VenvPath $VenvPath
if ($LASTEXITCODE -ne 0) { Stop-Safely "Dependency installation failed; no files were reset, deleted, or force-updated." }
$venvPython = Join-Path (Join-Path $projectRoot $VenvPath) "Scripts\python.exe"
if (-not (Test-Path $venvPython)) { Stop-Safely "Virtual-environment Python was not created." }

if (-not $SkipTests) {
    & $venvPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { Stop-Safely "Tests failed; development service was not started." }
}
if ($NoStart) { Write-Host "Development update, dependency installation and tests completed. -NoStart prevented service launch."; exit 0 }

Write-Host "Starting personal-assets-ai-manager development service on http://127.0.0.1:8765 (Ctrl+C stops it)."
& $venvPython (Join-Path $projectRoot "run.py")
