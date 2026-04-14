#!/usr/bin/env bash
# Deploy the MkDocs site to shep-engineering/open-brain's gh-pages branch.
#
# See scripts/deploy-docs.ps1 for the full rationale. TL;DR: forces the
# git committer identity for the deploy commit so we don't silently leak
# the degailen@gmail.com identity into the public shep-engineering repo.
#
# Usage:
#   scripts/deploy-docs.sh              # normal deploy (appends to gh-pages)
#   scripts/deploy-docs.sh --orphan     # force-push fresh orphan history

set -euo pipefail

ORPHAN=0
if [[ "${1:-}" == "--orphan" ]]; then
    ORPHAN=1
fi

export GIT_AUTHOR_NAME="David Sheppard"
export GIT_AUTHOR_EMAIL="davidasheppard@outlook.com"
export GIT_COMMITTER_NAME="David Sheppard"
export GIT_COMMITTER_EMAIL="davidasheppard@outlook.com"

OB="$(cd "$(dirname "$0")/.." && pwd)"
cd "$OB"

if [[ "$ORPHAN" == "1" ]]; then
    echo "Rebuilding site + pushing clean orphan gh-pages (history wiped)..."
    mkdocs build
    TMP="$(mktemp -d -t ob-ghp-orphan-XXXXXX)"
    trap "rm -rf '$TMP'" EXIT
    cd "$TMP"
    git init -b gh-pages
    git config user.name "$GIT_AUTHOR_NAME"
    git config user.email "$GIT_AUTHOR_EMAIL"
    cp -r "$OB/site/." .
    git add -A
    git commit -m "Deploy Open Brain docs (orphan refresh)"
    git remote add shep git@github-shep:shep-engineering/open-brain.git
    git push --force shep gh-pages:gh-pages
else
    echo "Building + deploying docs to shep/gh-pages as David Sheppard..."
    mkdocs gh-deploy -r shep -b gh-pages --force
fi

echo "Done. Public site: https://shep-engineering.github.io/open-brain/"
