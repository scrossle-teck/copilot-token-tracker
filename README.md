# Copilot Token Tracker

Copilot CLI already exposes session-scoped usage with `/usage`, current context usage with `/context`, and writes per-session telemetry to `~/.copilot/session-state/<session-id>/events.jsonl`. What it does not currently provide is a built-in local dashboard that aggregates token usage across all of your sessions.

This project fills that gap for Windows-first setups by:

- scanning completed `session.shutdown` events across `~/.copilot/session-state`
- importing VS Code Copilot Chat sessions from workspace storage
- normalizing them into a local SQLite database
- generating a self-contained HTML dashboard
- installing personal hooks in `~/.copilot/hooks` so sync runs automatically across local Copilot CLI sessions

## Data sources

### Copilot CLI (actual metrics)

CLI sessions are imported from `~/.copilot/session-state/*/events.jsonl` with server-reported token counts, premium request costs, and code change stats. This is the highest-fidelity data source.

### VS Code Copilot Chat (estimated metrics)

VS Code Chat sessions are imported from `%APPDATA%\Code\User\workspaceStorage\*\state.vscdb`. Because VS Code does not persist actual per-request token counts locally, all VS Code token values are **estimated** from message text length (~0.25 tokens per character). Estimated data is clearly labeled in the dashboard and terminal summary.

**Limitations of VS Code tracking:**

- Token counts are approximate (can be 20-40% off actual usage)
- Inline completions and ghost text are not tracked (only chat sessions)
- Premium request costs are not available (shown as 0)
- Code change stats are not available from VS Code sessions
- Cache read/write tokens are not available

## What the tracker stores

For each completed session, the tracker records:

- session id, start and shutdown time
- cwd, git root, branch, and repository when available
- model-level request counts
- input, output, cache read, and cache write tokens
- total token counts
- premium request units from Copilot telemetry
- code-change counts and modified files
- API duration and full session duration

## Project layout

- `tokentracker\` - Python implementation
- `scripts\install-personal-hook.ps1` - Windows installer for personal hooks
- `tests\` - unit tests with fixture session data

## Requirements

- Windows
- Python 3.11+
- GitHub Copilot CLI 0.0.422 or newer

## Install

### Recommended install for end users

Install the published CLI into its own isolated environment:

```powershell
pipx install copilot-token-tracker
```

Then configure hooks and verify the setup:

```powershell
tokentracker install
tokentracker doctor
tokentracker summary
tokentracker dashboard --open
```

### From a source checkout

From the repository root:

```powershell
python -m pip install -e .
python -m tokentracker sync
python -m tokentracker summary
python -m tokentracker dashboard --open
```

To install automatic personal hooks:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-personal-hook.ps1
```

That installer writes:

- `~/.copilot/hooks/copilot-token-tracker.json`
- `~/.copilot/hooks/copilot-token-tracker-sync.ps1`
- `~/.copilot/token-tracker/token-tracker.db`
- `~/.copilot/token-tracker/dashboard.html`
- `~/.copilot/token-tracker/projects\*.html`
- `~/.copilot/token-tracker/pricing.json`

## Commands

### Sync telemetry into SQLite

```powershell
tokentracker sync
```

Useful options:

- `--copilot-home PATH`
- `--data-dir PATH`
- `--skip-dashboard`
- `--quiet`
- `--sources cli,vscode` (default: both; use `--sources cli` for CLI only)

### Print a terminal summary

```powershell
tokentracker summary
```

To filter to one repository, git root, or cwd scope:

```powershell
tokentracker summary --scope "octo/demo"
```

To filter to a month:

```powershell
tokentracker summary --current-month
tokentracker summary --month 2026-06
```

### Generate or open the HTML dashboard

```powershell
tokentracker dashboard --open
```

The global dashboard also generates per-project pages under `projects\`. You can open one scope directly with:

```powershell
tokentracker dashboard --scope "octo/demo" --open
```

Month filtering is also available for dashboard generation:

```powershell
tokentracker dashboard --current-month --open
tokentracker dashboard --month 2026-06 --open
```

### Install personal hooks

```powershell
tokentracker install
```

### Remove personal hooks

```powershell
tokentracker uninstall
```

### Diagnose local setup

```powershell
tokentracker doctor
```

## How hook sync works

The installer registers both `sessionStart` and `sessionEnd` hooks.

- If Copilot CLI has already written the `session.shutdown` event by the time `sessionEnd` runs, the just-finished session is imported immediately.
- If the shutdown event lands after the hook returns, the next `sessionStart` hook or a manual `sync` command backfills it.

That makes the design resilient without scraping terminal output.

## Cost notes

Copilot CLI telemetry already includes premium request cost units in `session.shutdown.modelMetrics.*.requests.cost`. This tracker stores those values directly.

The generated `pricing.json` file supports two estimation modes:

1. **Per-model token pricing** using seeded rates from GitHub's Copilot "Models and pricing" table:

  Source: [GitHub Copilot Models and pricing](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing) (last verified 2026-06-09)

```json
{
  "currency": "USD",
  "usdPerPremiumRequest": 0.04,
  "models": {
    "default": {
      "inputCostPer1M": null,
      "outputCostPer1M": null,
      "cacheReadCostPer1M": 0,
      "cacheWriteCostPer1M": 0
    },
    "gpt-5.4": {
      "inputCostPer1M": 2.5,
      "outputCostPer1M": 15,
      "cacheReadCostPer1M": 0.25,
      "cacheWriteCostPer1M": 2.5
    },
    "claude-opus-4.6": {
      "inputCostPer1M": 5,
      "outputCostPer1M": 25,
      "cacheReadCostPer1M": 0.5,
      "cacheWriteCostPer1M": 6.25
    },
    "gemini-3-pro-preview": {
      "inputCostPer1M": 2,
      "outputCostPer1M": 12,
      "cacheReadCostPer1M": 0.2,
      "cacheWriteCostPer1M": 2
    }
  }
}
```

1. **Premium-request conversion fallback** if you prefer a simpler estimate:

```json
{ "currency": "USD", "usdPerPremiumRequest": 0.04 }
```

Blank or legacy `pricing.json` files are upgraded to include those seeded model rates plus the `usdPerPremiumRequest` fallback of `0.04`, based on GitHub's public Copilot billing docs.

For providers that publish long-context surcharges or time-based cache storage prices, the tracker uses the public base token rates and best-effort cache pricing because Copilot session telemetry does not expose enough detail to price every surcharge exactly.

The dashboard and `summary` command prefer per-model token pricing when it is available, and fall back to `usdPerPremiumRequest` for unknown models.

## Testing

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```

Build release artifacts locally with:

```powershell
python -m pip install -e .[dev]
python -m build
python -m twine check dist/*
```

## GitHub and PyPI release setup

Recommended repository target:

- `https://github.com/J-Bax/copilot-token-tracker`

Recommended release flow:

1. Create the GitHub repository and push this project to it.
2. Create the `copilot-token-tracker` project on PyPI.
3. Configure PyPI trusted publishing for the GitHub Actions workflow in `.github/workflows/publish.yml`.
4. Let `.github/workflows/ci.yml` validate tests and package builds on pushes and pull requests.
5. Publish a GitHub release to trigger the PyPI publish workflow.

## Current built-in support vs this project

Built into Copilot CLI today:

- `/usage` for the current session
- `/context` for current context-window usage
- per-session `events.jsonl` telemetry in `~/.copilot/session-state`
- personal hooks in `~/.copilot/hooks`

Added by this project:

- cross-session aggregation (CLI and VS Code Chat)
- SQLite history with source tracking
- HTML dashboard with source breakdown
- automatic personal-hook based syncing
- estimated VS Code token usage from local workspace storage
