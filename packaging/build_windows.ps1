# Build Sitechecker Windows desktop (onedir + Setup.exe)
# Usage (from repo root, venv active):
#   .\packaging\build_windows.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "==> Installing build deps..." -ForegroundColor Cyan
python -m pip install -q -r requirements.txt
python -m pip install -q pywebview pyinstaller pillow

Write-Host "==> Generating icon..." -ForegroundColor Cyan
python packaging\make_icon.py

Write-Host "==> PyInstaller..." -ForegroundColor Cyan
python -m PyInstaller --noconfirm sitechecker.spec
if (-not (Test-Path "dist\Sitechecker\Sitechecker.exe")) {
    throw "PyInstaller failed: dist\Sitechecker\Sitechecker.exe not found"
}

$iscc = @(
    "${env:LocalAppData}\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Host "Inno Setup 6 not found. Install from https://jrsoftware.org/isinfo.php" -ForegroundColor Yellow
    Write-Host "Portable build ready: dist\Sitechecker\" -ForegroundColor Green
    exit 0
}

Write-Host "==> Inno Setup ($iscc)..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path "dist\installer" | Out-Null
& $iscc "packaging\sitechecker.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }

Write-Host "==> Done" -ForegroundColor Green
Get-ChildItem "dist\installer\*.exe" | ForEach-Object { Write-Host $_.FullName }
