# Deploy the MkDocs site to shep-engineering/open-brain's gh-pages branch.
#
# Why this wrapper exists: `mkdocs gh-deploy` uses the git user identity
# configured at the time of the deploy commit. The repo's default identity
# is `degailen <degailen@gmail.com>` for degailen/main work. Without
# overriding, every gh-pages deploy silently lands in the PUBLIC
# shep-engineering repo authored as degailen — a commit-author leak.
# This script forces the committer identity for the deploy and only
# the deploy, leaving the repo's default git config alone for other work.
#
# Usage:
#   .\scripts\deploy-docs.ps1              # normal deploy (appends to gh-pages)
#   .\scripts\deploy-docs.ps1 -Orphan      # force-push fresh orphan history
#                                          # (use sparingly — wipes history)
#
# Prereqs:
#   - mkdocs + mkdocs-material installed in the active Python env
#   - `shep` remote configured: git@github-shep:shep-engineering/open-brain.git
#   - SSH key for shep-engineering account wired via ~/.ssh/config alias

param(
    [switch]$Orphan
)

$ErrorActionPreference = "Stop"

$env:GIT_AUTHOR_NAME = "David Sheppard"
$env:GIT_AUTHOR_EMAIL = "davidasheppard@outlook.com"
$env:GIT_COMMITTER_NAME = "David Sheppard"
$env:GIT_COMMITTER_EMAIL = "davidasheppard@outlook.com"

$OB = Split-Path -Parent $PSScriptRoot
Push-Location $OB
try {
    if ($Orphan) {
        Write-Host "Rebuilding site + pushing clean orphan gh-pages (history wiped)..." -ForegroundColor Yellow
        mkdocs build
        if ($LASTEXITCODE -ne 0) { throw "mkdocs build failed" }

        $tmp = Join-Path ([IO.Path]::GetTempPath()) ("ob-ghp-orphan-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
        New-Item -ItemType Directory -Path $tmp | Out-Null
        Push-Location $tmp
        try {
            git init -b gh-pages | Out-Null
            git config user.name $env:GIT_AUTHOR_NAME
            git config user.email $env:GIT_AUTHOR_EMAIL
            Copy-Item -Recurse -Force "$OB\site\*" . -ErrorAction SilentlyContinue
            Copy-Item -Recurse -Force "$OB\site\.*" . -ErrorAction SilentlyContinue
            git add -A | Out-Null
            git commit -m "Deploy Open Brain docs (orphan refresh)" | Out-Null
            git remote add shep git@github-shep:shep-engineering/open-brain.git
            git push --force shep gh-pages:gh-pages
        }
        finally {
            Pop-Location
            Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        }
    }
    else {
        Write-Host "Building + deploying docs to shep/gh-pages as David Sheppard..." -ForegroundColor Cyan
        mkdocs gh-deploy -r shep -b gh-pages --force
        if ($LASTEXITCODE -ne 0) { throw "mkdocs gh-deploy failed" }
    }
    Write-Host "Done. Public site: https://shep-engineering.github.io/open-brain/" -ForegroundColor Green
}
finally {
    Pop-Location
    Remove-Item Env:GIT_AUTHOR_NAME, Env:GIT_AUTHOR_EMAIL, Env:GIT_COMMITTER_NAME, Env:GIT_COMMITTER_EMAIL -ErrorAction SilentlyContinue
}
