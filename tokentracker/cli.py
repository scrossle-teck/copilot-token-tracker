from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
import webbrowser
from pathlib import Path

from tokentracker import __version__
from tokentracker.dashboard import project_dashboard_path, render_dashboard
from tokentracker.importer import discover_session_files, parse_completed_session
from tokentracker.models import SyncResult
from tokentracker.pricing import (
    describe_cost_strategy,
    ensure_pricing_file,
    estimate_total_currency_amount,
    load_pricing,
    pricing_currency,
)
from tokentracker.storage import (
    fetch_billing_mix,
    connect,
    existing_session_mtimes,
    fetch_model_breakdown,
    fetch_source_breakdown,
    fetch_summary,
    upsert_session,
)
from tokentracker.vscode_importer import iter_all_vscode_sessions


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return int(args.handler(args) or 0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tokentracker",
        description="Track GitHub Copilot usage across CLI and VS Code sessions.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    sync_parser = subparsers.add_parser("sync", help="Import completed Copilot sessions into SQLite.")
    _add_common_paths(sync_parser)
    sync_parser.add_argument("--skip-dashboard", action="store_true", help="Skip dashboard regeneration.")
    sync_parser.add_argument("--quiet", action="store_true", help="Suppress sync output.")
    _add_sources_flag(sync_parser)
    sync_parser.set_defaults(handler=handle_sync)

    summary_parser = subparsers.add_parser("summary", help="Print an aggregated summary.")
    _add_common_paths(summary_parser)
    _add_scope_filter(summary_parser)
    _add_period_filter(summary_parser)
    _add_sources_flag(summary_parser)
    summary_parser.set_defaults(handler=handle_summary)

    dashboard_parser = subparsers.add_parser("dashboard", help="Regenerate the HTML dashboard.")
    _add_common_paths(dashboard_parser)
    _add_scope_filter(dashboard_parser)
    _add_period_filter(dashboard_parser)
    _add_sources_flag(dashboard_parser)
    dashboard_parser.add_argument("--open", action="store_true", help="Open the generated dashboard in the default browser.")
    dashboard_parser.set_defaults(handler=handle_dashboard)

    doctor_parser = subparsers.add_parser("doctor", help="Inspect local tracker installation and data paths.")
    _add_common_paths(doctor_parser)
    doctor_parser.set_defaults(handler=handle_doctor)

    install_parser = subparsers.add_parser("install", help="Install personal hooks under ~/.copilot/hooks.")
    _add_common_paths(install_parser)
    install_parser.add_argument(
        "--python-executable",
        type=Path,
        default=Path(sys.executable),
        help="Python executable the hook bridge should use.",
    )
    install_parser.add_argument("--open-dashboard", action="store_true", help="Open the dashboard after the initial sync.")
    install_parser.set_defaults(handler=handle_install)

    uninstall_parser = subparsers.add_parser("uninstall", help="Remove personal hook files.")
    _add_common_paths(uninstall_parser)
    uninstall_parser.set_defaults(handler=handle_uninstall)

    return parser


def _add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--copilot-home",
        type=Path,
        default=Path.home() / ".copilot",
        help="Copilot home directory. Defaults to ~/.copilot.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory for SQLite, dashboard, and pricing files. Defaults to <copilot-home>/token-tracker.",
    )


def _add_scope_filter(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scope",
        type=str,
        default=None,
        help="Filter output to a single repository, git root, or cwd scope.",
    )


def _parse_month_value(raw: str) -> str:
    try:
        parsed = datetime.strptime(raw, "%Y-%m")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Month must be in YYYY-MM format.") from exc
    return parsed.strftime("%Y-%m")


def _add_period_filter(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--current-month",
        action="store_true",
        help="Filter output to the current UTC calendar month.",
    )
    group.add_argument(
        "--month",
        type=_parse_month_value,
        default=None,
        help="Filter output to a specific month in YYYY-MM format.",
    )


def _month_bounds(month_value: str) -> tuple[str, str]:
    month_start = datetime.strptime(month_value, "%Y-%m").replace(day=1)
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)
    return month_start.strftime("%Y-%m-%d"), month_end.strftime("%Y-%m-%d")


def _resolve_period_filter(args: argparse.Namespace) -> tuple[str | None, str | None, str | None]:
    month_value = getattr(args, "month", None)
    if month_value is None and bool(getattr(args, "current_month", False)):
        month_value = datetime.now(timezone.utc).strftime("%Y-%m")
    if month_value is None:
        return None, None, None
    period_start, period_end = _month_bounds(month_value)
    return month_value, period_start, period_end


_ALL_SOURCES = ("cli", "vscode")


def _add_sources_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--sources",
        type=str,
        default=",".join(_ALL_SOURCES),
        help="Comma-separated list of sources to import. Options: cli, vscode. Default: cli,vscode.",
    )


def _parse_sources(raw: str) -> set[str]:
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def handle_sync(args: argparse.Namespace) -> int:
    sources = _parse_sources(getattr(args, "sources", ",".join(_ALL_SOURCES)))
    result = sync_sessions(
        copilot_home=args.copilot_home,
        data_dir=_data_dir_for(args.copilot_home, args.data_dir),
        regenerate_dashboard=not args.skip_dashboard,
        sources=sources,
    )
    if not args.quiet:
        _print_sync_result(result)
    return 0


def handle_summary(args: argparse.Namespace) -> int:
    data_dir = _data_dir_for(args.copilot_home, args.data_dir)
    scope = getattr(args, "scope", None)
    period_label, period_start, period_end = _resolve_period_filter(args)
    sources = _parse_sources(getattr(args, "sources", ",".join(_ALL_SOURCES)))
    result = sync_sessions(copilot_home=args.copilot_home, data_dir=data_dir, regenerate_dashboard=True, sources=sources)
    pricing = load_pricing(data_dir)
    _print_sync_result(result, heading="Sync summary")
    with connect(result.database_path) as connection:
        summary = fetch_summary(connection, scope=scope, period_start=period_start, period_end=period_end)
        model_rows = fetch_model_breakdown(connection, scope=scope, period_start=period_start, period_end=period_end)
        source_rows = fetch_source_breakdown(connection, scope=scope, period_start=period_start, period_end=period_end)
        billing_mix = fetch_billing_mix(connection, scope=scope, period_start=period_start, period_end=period_end)
    estimated_cost = estimate_total_currency_amount(model_rows, pricing)
    print()
    print("Totals")
    if scope:
        print(f"  Scope: {scope}")
    if period_label:
        print(f"  Month: {period_label}")
    print(f"  Sessions: {_format_int(summary['session_count'])}")
    print(f"  Requests: {_format_int(summary['total_requests'])}")
    print(f"  Tokens: {_format_int(summary['total_tokens'])}")
    print(f"    Input: {_format_int(summary['total_input_tokens'])}")
    print(f"    Output: {_format_int(summary['total_output_tokens'])}")
    print(f"    Cache read: {_format_int(summary['total_cache_read_tokens'])}")
    print(f"    Cache write: {_format_int(summary['total_cache_write_tokens'])}")
    print(f"  AI credits: {_format_decimal(summary['total_ai_credits'])}")
    if int(billing_mix["legacy_premium_sessions"] or 0) > 0:
        print(f"  Legacy premium request units: {_format_decimal(summary['total_premium_requests'])}")
    print(f"  Estimated cost ({pricing_currency(pricing)}): {_format_currency(estimated_cost, pricing_currency(pricing))}")
    print(f"  Cost mode: {describe_cost_strategy(pricing)}")
    print(f"  Session duration: {_format_duration(summary['duration_seconds'])}")
    print(f"  Code changes: {_format_int(summary['lines_added'])} added / {_format_int(summary['lines_removed'])} removed")
    if source_rows:
        print("  By source:")
        for srow in source_rows:
            label = _source_label(str(srow["source"]))
            estimated_count = int(srow["estimated_count"] or 0)
            actual_count = int(srow["session_count"]) - estimated_count
            mode_note = f" (actual: {actual_count}, estimated: {estimated_count})"
            print(f"    {label}: {_format_int(srow['session_count'])} sessions, {_format_int(srow['total_tokens'])} tokens{mode_note}")
    if result.dashboard_path is not None:
        print(f"  Dashboard: {result.dashboard_path}")
    return 0


def handle_dashboard(args: argparse.Namespace) -> int:
    data_dir = _data_dir_for(args.copilot_home, args.data_dir)
    scope = getattr(args, "scope", None)
    period_label, period_start, period_end = _resolve_period_filter(args)
    sources = _parse_sources(getattr(args, "sources", ",".join(_ALL_SOURCES)))
    result = sync_sessions(copilot_home=args.copilot_home, data_dir=data_dir, regenerate_dashboard=True, sources=sources)
    if result.dashboard_path is None:
        raise RuntimeError("Dashboard path was not generated.")
    if period_start is not None and period_end is not None:
        pricing = load_pricing(data_dir)
        with connect(result.database_path) as connection:
            render_dashboard(
                connection,
                result.dashboard_path,
                pricing,
                period_start=period_start,
                period_end=period_end,
                period_label=period_label,
            )
    dashboard_path = result.dashboard_path
    if scope is not None:
        dashboard_path = project_dashboard_path(result.dashboard_path, scope)
        if not dashboard_path.exists():
            raise RuntimeError(f"No dashboard page was generated for scope: {scope}")
    print(f"Dashboard written to {dashboard_path}")
    if args.open:
        webbrowser.open(dashboard_path.resolve().as_uri())
    return 0


def handle_install(args: argparse.Namespace) -> int:
    data_dir = _data_dir_for(args.copilot_home, args.data_dir)
    hook_dir = args.copilot_home / "hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    ensure_pricing_file(data_dir)

    bridge_path = _hook_bridge_path(args.copilot_home)
    config_path = _hook_config_path(args.copilot_home)

    bridge_path.write_text(
        _render_hook_bridge(
            python_executable=args.python_executable.resolve(),
            copilot_home=args.copilot_home.resolve(),
            data_dir=data_dir.resolve(),
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "sessionStart": [
                        {
                            "type": "command",
                            "powershell": ".\\copilot-token-tracker-sync.ps1",
                            "cwd": str(hook_dir.resolve()),
                            "timeoutSec": 20,
                        }
                    ],
                    "sessionEnd": [
                        {
                            "type": "command",
                            "powershell": ".\\copilot-token-tracker-sync.ps1",
                            "cwd": str(hook_dir.resolve()),
                            "timeoutSec": 20,
                        }
                    ],
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = sync_sessions(copilot_home=args.copilot_home, data_dir=data_dir, regenerate_dashboard=True)
    print(f"Installed hook config: {config_path}")
    print(f"Installed hook bridge: {bridge_path}")
    print(f"Database: {result.database_path}")
    if result.dashboard_path is not None:
        print(f"Dashboard: {result.dashboard_path}")
    if args.open_dashboard and result.dashboard_path is not None:
        webbrowser.open(result.dashboard_path.resolve().as_uri())
    return 0


def handle_doctor(args: argparse.Namespace) -> int:
    data_dir = _data_dir_for(args.copilot_home, args.data_dir)
    hook_dir = args.copilot_home / "hooks"
    session_state_dir = args.copilot_home / "session-state"
    database_path = data_dir / "token-tracker.db"
    dashboard_path = data_dir / "dashboard.html"
    pricing_path = data_dir / "pricing.json"
    pricing = load_pricing(data_dir) if pricing_path.exists() else {"models": {}, "usdPerPremiumRequest": None}
    session_files = discover_session_files(args.copilot_home)
    config_path = _hook_config_path(args.copilot_home)
    bridge_path = _hook_bridge_path(args.copilot_home)

    print("Token tracker doctor")
    print(f"  Version: {__version__}")
    print(f"  Copilot home: {args.copilot_home.resolve()}")
    print(f"  Hook directory: {hook_dir.resolve()} ({_status(hook_dir.exists())})")
    print(f"  Session state: {session_state_dir.resolve()} ({_status(session_state_dir.exists())})")
    print(f"  Hook config: {config_path} ({_status(config_path.exists())})")
    print(f"  Hook bridge: {bridge_path} ({_status(bridge_path.exists())})")
    print(f"  Pricing file: {pricing_path} ({_status(pricing_path.exists())})")
    print(f"  Database: {database_path} ({_status(database_path.exists())})")
    print(f"  Dashboard: {dashboard_path} ({_status(dashboard_path.exists())})")
    print(f"  Pricing mode: {describe_cost_strategy(pricing)}")
    print(f"  Session files discovered: {_format_int(len(session_files))}")

    if database_path.exists():
        with connect(database_path) as connection:
            summary = fetch_summary(connection)
        print(f"  Imported sessions: {_format_int(summary['session_count'])}")
        print(f"  Stored tokens: {_format_int(summary['total_tokens'])}")
    else:
        print("  Imported sessions: 0")
        print("  Stored tokens: 0")

    if not config_path.exists() or not bridge_path.exists():
        print("  Next step: run `tokentracker install` to register the personal hooks.")
    elif not session_files:
        print("  Next step: finish a Copilot CLI session, then run `tokentracker sync`.")
    elif not database_path.exists():
        print("  Next step: run `tokentracker sync` to create the SQLite database.")
    else:
        print("  Status: tracker looks ready.")
    return 0


def handle_uninstall(args: argparse.Namespace) -> int:
    hook_dir = args.copilot_home / "hooks"
    removed = []
    for candidate in (
        _hook_config_path(args.copilot_home),
        _hook_bridge_path(args.copilot_home),
    ):
        if candidate.exists():
            candidate.unlink()
            removed.append(str(candidate))
    if removed:
        print("Removed:")
        for entry in removed:
            print(f"  {entry}")
    else:
        print("No hook files were installed.")
    return 0


def sync_sessions(
    copilot_home: Path,
    data_dir: Path,
    regenerate_dashboard: bool,
    sources: set[str] | None = None,
    vscode_storage_root: Path | None = None,
) -> SyncResult:
    if sources is None:
        sources = set(_ALL_SOURCES)
    data_dir.mkdir(parents=True, exist_ok=True)
    ensure_pricing_file(data_dir)
    database_path = data_dir / "token-tracker.db"
    dashboard_path = data_dir / "dashboard.html" if regenerate_dashboard else None

    imported = 0
    updated = 0
    skipped = 0
    incomplete = 0
    session_files: list[Path] = []

    vscode_seen = 0
    vscode_imported = 0
    vscode_updated = 0
    vscode_skipped = 0

    with connect(database_path) as connection:
        cached_mtimes = existing_session_mtimes(connection)

        # --- CLI sessions ---
        if "cli" in sources:
            session_files = discover_session_files(copilot_home)
            for session_file in session_files:
                session_id = session_file.parent.name
                current_mtime = session_file.stat().st_mtime_ns
                if cached_mtimes.get(session_id) == current_mtime:
                    skipped += 1
                    continue
                session = parse_completed_session(session_file)
                if session is None:
                    incomplete += 1
                    continue
                status = upsert_session(connection, session)
                if status == "inserted":
                    imported += 1
                else:
                    updated += 1

        # --- VS Code chat sessions ---
        if "vscode" in sources:
            for session in iter_all_vscode_sessions(vscode_storage_root):
                vscode_seen += 1
                if cached_mtimes.get(session.session_id) == session.source_mtime_ns:
                    vscode_skipped += 1
                    continue
                status = upsert_session(connection, session)
                if status == "inserted":
                    vscode_imported += 1
                else:
                    vscode_updated += 1

        connection.commit()

        if regenerate_dashboard:
            pricing = load_pricing(data_dir)
            assert dashboard_path is not None
            render_dashboard(connection, dashboard_path, pricing)

    return SyncResult(
        session_files_seen=len(session_files),
        imported_sessions=imported,
        updated_sessions=updated,
        skipped_sessions=skipped,
        incomplete_sessions=incomplete,
        database_path=database_path,
        dashboard_path=dashboard_path,
        vscode_sessions_seen=vscode_seen,
        vscode_imported=vscode_imported,
        vscode_updated=vscode_updated,
        vscode_skipped=vscode_skipped,
    )


def _data_dir_for(copilot_home: Path, data_dir: Path | None) -> Path:
    return data_dir.resolve() if data_dir is not None else (copilot_home / "token-tracker").resolve()


def _render_hook_bridge(
    python_executable: Path,
    copilot_home: Path,
    data_dir: Path,
) -> str:
    return f"""$ErrorActionPreference = "Stop"
$pythonExecutable = '{_ps_quote(python_executable)}'
$copilotHome = '{_ps_quote(copilot_home)}'
$dataDir = '{_ps_quote(data_dir)}'

& $pythonExecutable -m tokentracker sync --copilot-home $copilotHome --data-dir $dataDir --quiet
exit $LASTEXITCODE
"""


def _ps_quote(path: Path) -> str:
    return str(path).replace("'", "''")


def _hook_config_path(copilot_home: Path) -> Path:
    return copilot_home / "hooks" / "copilot-token-tracker.json"


def _hook_bridge_path(copilot_home: Path) -> Path:
    return copilot_home / "hooks" / "copilot-token-tracker-sync.ps1"


def _status(exists: bool) -> str:
    return "present" if exists else "missing"


def _print_sync_result(result: SyncResult, heading: str = "Sync complete") -> None:
    print(heading)
    print(f"  CLI session files seen: {_format_int(result.session_files_seen)}")
    print(f"  CLI imported: {_format_int(result.imported_sessions)}")
    print(f"  CLI updated: {_format_int(result.updated_sessions)}")
    print(f"  CLI skipped unchanged: {_format_int(result.skipped_sessions)}")
    print(f"  CLI incomplete sessions: {_format_int(result.incomplete_sessions)}")
    if result.vscode_sessions_seen > 0 or result.vscode_imported > 0:
        print(f"  VS Code sessions seen: {_format_int(result.vscode_sessions_seen)}")
        print(f"  VS Code imported: {_format_int(result.vscode_imported)}")
        print(f"  VS Code updated: {_format_int(result.vscode_updated)}")
        print(f"  VS Code skipped unchanged: {_format_int(result.vscode_skipped)}")
    print(f"  Database: {result.database_path}")
    if result.dashboard_path is not None:
        print(f"  Dashboard: {result.dashboard_path}")


def _source_label(source: str) -> str:
    labels = {"cli": "Copilot CLI", "vscode-chat": "VS Code Chat (estimated)"}
    return labels.get(source, source)


def _format_int(value: object) -> str:
    return f"{int(value):,}"


def _format_decimal(value: object) -> str:
    return f"{float(value):,.2f}"


def _format_duration(value: object) -> str:
    total_seconds = int(value or 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _format_currency(value: float | None, currency: str) -> str:
    if value is None:
        return "Not configured"
    amount = f"{value:,.2f}"
    return f"${amount}" if currency.upper() == "USD" else f"{currency.upper()} {amount}"

