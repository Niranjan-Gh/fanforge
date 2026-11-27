# One-time setup: creates the GitHub repo, pushes the code and tags the first release.
# Needs git + GitHub CLI:  winget install Git.Git GitHub.cli   (reopen PowerShell after)
# Then log in once:        gh auth login
# Usage:  .\publish.ps1            (public repo - required for in-app update checks)
#         .\publish.ps1 -Private
param([switch]$Private, [string]$RepoName = "fanforge")

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

foreach ($t in "git", "gh") {
    if (-not (Get-Command $t -ErrorAction SilentlyContinue)) {
        throw "$t not found. Run: winget install Git.Git GitHub.cli  (then reopen PowerShell)"
    }
}
gh auth status *> $null
if ($LASTEXITCODE -ne 0) { throw "Not logged in to GitHub. Run: gh auth login" }

$user = (gh api user --jq .login).Trim()
if (-not $user) { throw "Couldn't read your GitHub username." }

# Point the in-app update checker (and README) at your repo
foreach ($f in "fanforge.py", "README.md") {
    $p = Join-Path $PSScriptRoot $f
    $txt = [IO.File]::ReadAllText($p)
    $txt = $txt.Replace("YOUR_GITHUB_USERNAME/fanforge", "$user/$RepoName").Replace("YOUR_GITHUB_USERNAME", $user)
    [IO.File]::WriteAllText($p, $txt, (New-Object Text.UTF8Encoding($false)))
}

$ver = (Select-String -Path (Join-Path $PSScriptRoot "fanforge.py") -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value

if (-not (Test-Path ".git")) { git init -b main }
if (-not (git config user.name)) {
    git config user.name $user
    git config user.email "$user@users.noreply.github.com"
}
git add -A
git commit -m "Initial commit: FanForge v$ver"

$vis = if ($Private) { "--private" } else { "--public" }
gh repo create $RepoName $vis --source . --remote origin --push
if ($LASTEXITCODE -ne 0) { throw "gh repo create failed (does a repo named '$RepoName' already exist?)" }

git tag "v$ver"
git push origin "v$ver"

Write-Host "`nPublished: https://github.com/$user/$RepoName" -ForegroundColor Green
Write-Host "The build runs on GitHub now - FanForge.exe appears under Releases in a few minutes:" -ForegroundColor Green
Write-Host "https://github.com/$user/$RepoName/actions"
