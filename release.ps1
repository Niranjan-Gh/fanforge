# Ships a new version: bumps VERSION in fanforge.py, commits, tags and pushes.
# GitHub Actions then builds FanForge.exe and attaches it to the release.
# Usage:  .\release.ps1 2.2.0
param([Parameter(Mandatory = $true)][string]$Version)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Use a version like 2.2.0" }

$p = Join-Path $PSScriptRoot "fanforge.py"
$txt = [IO.File]::ReadAllText($p)
$new = [regex]::Replace($txt, '(?m)^VERSION = ".*"', "VERSION = `"$Version`"")
if ($new -eq $txt) { throw "Couldn't find the VERSION line in fanforge.py" }
[IO.File]::WriteAllText($p, $new, (New-Object Text.UTF8Encoding($false)))

git add -A
git commit -m "Release v$Version"
git tag "v$Version"
git push
git push origin "v$Version"

Write-Host "`nTagged v$Version - the build is running on GitHub (Actions tab)." -ForegroundColor Green
