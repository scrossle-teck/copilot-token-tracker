# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-03-09

### Added

- `tokentracker sync` command to import completed Copilot CLI sessions from `~/.copilot/session-state` into a local SQLite database.
- `tokentracker summary` command for terminal-based usage summaries with optional `--scope` filtering.
- `tokentracker dashboard` command to generate a self-contained HTML dashboard with daily trends, per-model breakdown, per-repo breakdown, and recent sessions.
- Per-project dashboard pages under `projects/` with cross-links to the global overview.
- `tokentracker install` and `tokentracker uninstall` commands to manage personal hooks in `~/.copilot/hooks`.
- `tokentracker doctor` command to diagnose local setup and report next steps.
- Hook-based automatic sync on `sessionStart` and `sessionEnd` events.
- Backfill design: sessions whose `session.shutdown` event arrives after the hook returns are imported on the next sync.
- Idempotent sync using `source_mtime_ns` comparison to skip unchanged session files.
- Per-model token tracking with input, output, cache-read, and cache-write breakdowns.
- Premium request unit tracking from Copilot telemetry.
- Code-change metrics: lines added, lines removed, and files modified per session.
- Centralized pricing in `pricing.json` with seeded public API rates for 24 models (OpenAI, Anthropic, Google).
- Dual cost estimation: per-model token pricing preferred, `usdPerPremiumRequest` fallback for unknown models.
- Automatic `pricing.json` upgrade from legacy or blank files to include seeded model rates.
- CI workflow testing on Python 3.11, 3.12, and 3.13 on Windows.
- Trusted-publishing workflow for PyPI releases via GitHub Actions.

[Unreleased]: https://github.com/J-Bax/copilot-token-tracker/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/J-Bax/copilot-token-tracker/releases/tag/v0.1.0
