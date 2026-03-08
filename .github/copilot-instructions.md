# Copilot instructions for TokenTracker-CLIHook

## Development commands

- This is a Windows-first project. Prefer PowerShell examples and Windows-style paths when editing docs, tests, or installer behavior.
- Local CLI commands are typically run from the repository root with `python -m tokentracker <command>`.
- Common product commands:
  - `python -m tokentracker sync`
  - `python -m tokentracker summary`
  - `python -m tokentracker dashboard --open`
  - `python -m tokentracker install --project-root C:\Projects\TokenTracker-CLIHook`
  - `python -m tokentracker uninstall`
- Hook installer wrapper:
  - `powershell -ExecutionPolicy Bypass -File .\scripts\install-personal-hook.ps1`
- Test commands:
  - Full suite: `python -m unittest discover -s tests -v`
  - Single test: `python -m unittest tests.test_tokentracker.TokenTrackerTests.test_sync_imports_completed_sessions_and_ignores_incomplete_ones -v`
- No repo-specific lint or build command is configured; packaging is plain setuptools from `pyproject.toml`.

## High-level architecture

- The main entry point is `tokentracker.cli`. The `sync`, `summary`, `dashboard`, and `install` commands all flow through `sync_sessions()`, so changes to importing, persistence, pricing, or output usually affect multiple commands.
- Import flow:
  1. `tokentracker.importer.discover_session_files()` scans `~/.copilot/session-state/*/events.jsonl`.
  2. `tokentracker.importer.parse_completed_session()` only returns sessions that already have a `session.shutdown` event and converts telemetry into `SessionMetrics` and `ModelMetrics`.
  3. `tokentracker.storage` initializes the SQLite schema on connect, upserts into `sessions` and `session_models`, and exposes aggregate queries for the CLI and dashboard.
  4. `tokentracker.dashboard.render_dashboard()` and `handle_summary()` read those storage queries to produce HTML and terminal summaries.
- Pricing is centralized in `tokentracker.pricing`. The tracker keeps `pricing.json` in the data directory, prefers per-model token rates when present, and falls back to `usdPerPremiumRequest`.
- Personal-hook installation is part of the product behavior, not just dev tooling: `install` writes both `~/.copilot/hooks/copilot-token-tracker.json` and a PowerShell bridge script, while `scripts\install-personal-hook.ps1` just runs that command from the repo root.
- The tracker depends on completed-session telemetry from Copilot CLI `session.shutdown` events; older CLI versions do not provide the same data needed for backfilled totals.

## Key conventions

- Keep Windows and PowerShell assumptions intact. Path handling is built around `pathlib.Path`, and hook scripts use PowerShell-safe quoting via `_ps_quote()`.
- Treat incomplete sessions as normal state, not as failures. Import code skips session folders that do not yet contain `session.shutdown`, and the hook design backfills those sessions on the next `sessionStart` or manual `sync`.
- Preserve idempotent sync behavior. `sync_sessions()` compares `source_mtime_ns` against `storage.existing_session_mtimes()` before reparsing, and `upsert_session()` refreshes both session rows and per-model rows together.
- Normalize telemetry defensively. `importer.py` uses `_as_dict()`, `_as_list()`, `_as_int()`, `_as_float()`, and `_as_optional_str()` helpers instead of assuming every field exists in Copilot event payloads.
- Reuse the existing formatting helpers when changing output. `cli.py` and `dashboard.py` intentionally mirror `_format_int()`, `_format_decimal()`, `_format_duration()`, and `_format_currency()` so terminal and HTML summaries stay aligned.
- Tests are fixture-driven `unittest` cases in `tests\test_tokentracker.py`. They build a temporary `.copilot` tree and copy `tests\fixtures\completed-session` and `tests\fixtures\incomplete-session` into `session-state` to exercise realistic import flows.
- Dashboard changes should preserve the current layout guardrails for long text and empty states (`overflow-x`, `overflow-wrap`, placeholder rows, responsive grids); there is a regression test covering those cases.
