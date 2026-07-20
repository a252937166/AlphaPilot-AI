#!/usr/bin/env bash
set -euo pipefail

REPO_NAME="${1:-AlphaPilot-AI}"
VISIBILITY="${2:---private}"
DESCRIPTION="AI-driven probabilistic stock research, monitoring, scenario analysis and trading-assistance platform"

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI is required: https://cli.github.com/" >&2
  exit 1
fi

gh auth status
OWNER="$(gh api user --jq .login)"

if gh repo view "$OWNER/$REPO_NAME" >/dev/null 2>&1; then
  echo "Repository already exists: $OWNER/$REPO_NAME"
  if ! git remote get-url origin >/dev/null 2>&1; then
    git remote add origin "https://github.com/$OWNER/$REPO_NAME.git"
  fi
  git push -u origin main
else
  gh repo create "$REPO_NAME" "$VISIBILITY" \
    --description "$DESCRIPTION" \
    --source . \
    --remote origin \
    --push
fi

echo "Published: https://github.com/$OWNER/$REPO_NAME"
