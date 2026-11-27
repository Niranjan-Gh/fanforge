# Fetch the pinned .NET Framework release and verify the archive before extraction.
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$tag = "v0.9.6"
$sha256 = "086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001"
$url = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/$tag/LibreHardwareMonitor.zip"
$dest = Join-Path (Split-Path $PSScriptRoot -Parent) "lib"
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("fanforge-lhm-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null

try {
    $zip = Join-Path $tmp "LibreHardwareMonitor.zip"
    Invoke-WebRequest -Uri $url -OutFile $zip
    if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $sha256) {
        throw "LibreHardwareMonitor archive checksum mismatch."
    }
    $extract = Join-Path $tmp "extract"
    Expand-Archive -LiteralPath $zip -DestinationPath $extract
    $lib = Get-ChildItem -LiteralPath $extract -Recurse -Filter "LibreHardwareMonitorLib.dll" |
        Select-Object -First 1
    if (-not $lib) { throw "LibreHardwareMonitorLib.dll is missing from the verified archive." }

    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Get-ChildItem -LiteralPath $dest -File -Filter "*.dll" | Remove-Item
    Copy-Item (Join-Path $lib.DirectoryName "*.dll") $dest -Force
    Get-ChildItem -LiteralPath $dest -Filter "*.dll" | Unblock-File
} finally {
    $tempRoot = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path.TrimEnd('\') + '\'
    $resolved = (Resolve-Path -LiteralPath $tmp).Path
    if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
