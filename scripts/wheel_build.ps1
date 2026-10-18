# Build wheel (Windows)
# Run from repo root: powershell -File scripts/wheel_build.ps1
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $RepoRoot

$DashboardDir = Join-Path $RepoRoot "dashboard"
$DashboardDest = Join-Path $RepoRoot "src\octop\dashboard"

Write-Host "[wheel_build] Building dashboard frontend..."
Push-Location $DashboardDir
npm ci
npm run build
Pop-Location

$indexHtml = Join-Path $DashboardDest "index.html"
if (-not (Test-Path $indexHtml)) {
    throw "Dashboard build did not produce src/octop/dashboard/index.html"
}

Write-Host "[wheel_build] Dashboard ready at src/octop/dashboard/"

Write-Host "[wheel_build] Building wheel + sdist..."
python -m pip install --quiet build
if (Test-Path dist) { Remove-Item dist -Recurse -Force }
python -m build --outdir dist .

Write-Host "[wheel_build] Done. Artifacts in: $RepoRoot\dist\"
Get-ChildItem dist
