# Builds dist\FanForge.exe (single file, asks for admin on launch).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

& "$PSScriptRoot\scripts\fetch_lhm.ps1"

python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin `
    --name FanForge `
    --collect-all pythonnet --collect-all clr_loader --hidden-import clr `
    --add-data "lib;lib" `
    fanforge.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "Built: dist\FanForge.exe" -ForegroundColor Green
