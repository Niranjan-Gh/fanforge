# Downloads LibreHardwareMonitor and copies its DLLs into ..\lib
# Usage:  .\scripts\fetch_lhm.ps1            (latest release)
#         .\scripts\fetch_lhm.ps1 -Tag v0.9.4   (pin a specific release)
param([string]$Tag = "")

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$dest = Join-Path (Split-Path $PSScriptRoot -Parent) "lib"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

$headers = @{ "User-Agent" = "FanForge" }
if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }

$base = "https://api.github.com/repos/LibreHardwareMonitor/LibreHardwareMonitor/releases"
$url = if ($Tag) { "$base/tags/$Tag" } else { "$base/latest" }
Write-Host "Fetching LibreHardwareMonitor release info..." -ForegroundColor Cyan
$rel = Invoke-RestMethod $url -Headers $headers

$asset = $rel.assets | Where-Object { $_.name -like "*net472*.zip" } | Select-Object -First 1
if (-not $asset) { $asset = $rel.assets | Where-Object { $_.name -eq "LibreHardwareMonitor.zip" } | Select-Object -First 1 }
if (-not $asset) { $asset = $rel.assets | Where-Object { $_.name -like "*.zip" } | Select-Object -First 1 }
if (-not $asset) { throw "No zip asset found on LibreHardwareMonitor release $($rel.tag_name)." }

Write-Host "Downloading $($asset.name) ($($rel.tag_name))..." -ForegroundColor Cyan
$zip = Join-Path $env:TEMP "lhm.zip"
$tmp = Join-Path $env:TEMP "lhm_extract"
Invoke-WebRequest $asset.browser_download_url -OutFile $zip
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
Expand-Archive $zip -DestinationPath $tmp -Force

$lib = Get-ChildItem $tmp -Recurse -Filter "LibreHardwareMonitorLib.dll" | Select-Object -First 1
if (-not $lib) { throw "LibreHardwareMonitorLib.dll not found in the download." }

Copy-Item (Join-Path $lib.DirectoryName "*.dll") $dest -Force
Get-ChildItem $dest -Filter *.dll | Unblock-File
Write-Host "Done: DLLs copied to $dest" -ForegroundColor Green
