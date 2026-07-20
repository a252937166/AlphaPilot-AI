param(
  [string]$RepoName = "AlphaPilot-AI",
  [ValidateSet("private", "public", "internal")]
  [string]$Visibility = "private"
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
  throw "GitHub CLI is required: https://cli.github.com/"
}

gh auth status
$Owner = gh api user --jq .login
$FullName = "$Owner/$RepoName"

try {
  gh repo view $FullName | Out-Null
  if (-not (git remote get-url origin 2>$null)) {
    git remote add origin "https://github.com/$FullName.git"
  }
  git push -u origin main
} catch {
  gh repo create $RepoName "--$Visibility" --description `
    "AI-driven probabilistic stock research, monitoring, scenario analysis and trading-assistance platform" `
    --source . --remote origin --push
}

Write-Host "Published: https://github.com/$FullName"
