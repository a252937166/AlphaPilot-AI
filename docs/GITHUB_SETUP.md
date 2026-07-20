# GitHub Repository Setup

The publishing scripts create a private repository by default.

## One-command publishing

Linux/macOS:

```bash
./scripts/publish_to_github.sh AlphaPilot-AI --private
```

Windows PowerShell:

```powershell
./scripts/publish_to_github.ps1 -RepoName AlphaPilot-AI -Visibility private
```

Prerequisites:

```bash
gh auth login
gh auth status
```

## Recommended repository settings

- Default branch: `main`.
- Require pull requests before merging.
- Require the backend and frontend CI jobs.
- Require conversation resolution.
- Block force pushes and branch deletion on `main`.
- Enable secret scanning and push protection where available.
- Keep Actions permissions read-only by default.
- Store environment secrets in GitHub Environments, never repository files.

## Initial labels

Recommended labels:

- `area:data`
- `area:prediction`
- `area:scenario`
- `area:risk`
- `area:trading`
- `area:web`
- `priority:p0`
- `priority:p1`
- `priority:p2`
- `security`
- `data-license`

## Initial milestones

Use the milestones from `docs/ROADMAP.md`: Foundation, Point-in-Time Data, Research Prediction, Tracking Automation, MiroFish Bridge, Paper Trading and Limited Live Trading.
