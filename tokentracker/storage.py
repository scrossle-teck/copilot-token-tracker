from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tokentracker.models import SessionMetrics


def _scope_expression(alias: str | None = None) -> str:
    prefix = f"{alias}." if alias else ""
    return f"COALESCE({prefix}repository, {prefix}git_root, {prefix}cwd, '<unknown>')"


def _scope_filter(scope: str | None, alias: str | None = None) -> tuple[str, tuple[str, ...]]:
    if scope is None:
        return "", ()
    return f"WHERE {_scope_expression(alias)} = ?", (scope,)


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    _initialize_schema(connection)
    return connection


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            source_file TEXT NOT NULL,
            source_mtime_ns INTEGER NOT NULL,
            copilot_version TEXT,
            started_at TEXT,
            shutdown_at TEXT NOT NULL,
            shutdown_type TEXT,
            duration_seconds INTEGER,
            cwd TEXT,
            git_root TEXT,
            branch TEXT,
            repository TEXT,
            selected_model TEXT,
            current_model TEXT,
            total_premium_requests REAL NOT NULL,
            total_api_duration_ms INTEGER NOT NULL,
            total_requests INTEGER NOT NULL,
            total_input_tokens INTEGER NOT NULL,
            total_output_tokens INTEGER NOT NULL,
            total_cache_read_tokens INTEGER NOT NULL,
            total_cache_write_tokens INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL,
            lines_added INTEGER NOT NULL,
            lines_removed INTEGER NOT NULL,
            files_modified_count INTEGER NOT NULL,
            files_modified_json TEXT NOT NULL,
            raw_start_json TEXT,
            raw_shutdown_json TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source TEXT NOT NULL DEFAULT 'cli',
            is_estimated INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS session_models (
            session_id TEXT NOT NULL,
            model_name TEXT NOT NULL,
            request_count INTEGER NOT NULL,
            premium_request_cost REAL NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cache_read_tokens INTEGER NOT NULL,
            cache_write_tokens INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL,
            PRIMARY KEY (session_id, model_name),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );
        """
    )
    _migrate_add_source_columns(connection)


def _migrate_add_source_columns(connection: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
    }
    if "source" not in existing:
        connection.execute("ALTER TABLE sessions ADD COLUMN source TEXT NOT NULL DEFAULT 'cli'")
    if "is_estimated" not in existing:
        connection.execute("ALTER TABLE sessions ADD COLUMN is_estimated INTEGER NOT NULL DEFAULT 0")


def existing_session_mtimes(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        row["session_id"]: int(row["source_mtime_ns"])
        for row in connection.execute("SELECT session_id, source_mtime_ns FROM sessions")
    }


def upsert_session(connection: sqlite3.Connection, session: SessionMetrics) -> str:
    exists = (
        connection.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()
        is not None
    )

    connection.execute(
        """
        INSERT INTO sessions (
            session_id,
            source_file,
            source_mtime_ns,
            copilot_version,
            started_at,
            shutdown_at,
            shutdown_type,
            duration_seconds,
            cwd,
            git_root,
            branch,
            repository,
            selected_model,
            current_model,
            total_premium_requests,
            total_api_duration_ms,
            total_requests,
            total_input_tokens,
            total_output_tokens,
            total_cache_read_tokens,
            total_cache_write_tokens,
            total_tokens,
            lines_added,
            lines_removed,
            files_modified_count,
            files_modified_json,
            raw_start_json,
            raw_shutdown_json,
            imported_at,
            source,
            is_estimated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            source_file = excluded.source_file,
            source_mtime_ns = excluded.source_mtime_ns,
            copilot_version = excluded.copilot_version,
            started_at = excluded.started_at,
            shutdown_at = excluded.shutdown_at,
            shutdown_type = excluded.shutdown_type,
            duration_seconds = excluded.duration_seconds,
            cwd = excluded.cwd,
            git_root = excluded.git_root,
            branch = excluded.branch,
            repository = excluded.repository,
            selected_model = excluded.selected_model,
            current_model = excluded.current_model,
            total_premium_requests = excluded.total_premium_requests,
            total_api_duration_ms = excluded.total_api_duration_ms,
            total_requests = excluded.total_requests,
            total_input_tokens = excluded.total_input_tokens,
            total_output_tokens = excluded.total_output_tokens,
            total_cache_read_tokens = excluded.total_cache_read_tokens,
            total_cache_write_tokens = excluded.total_cache_write_tokens,
            total_tokens = excluded.total_tokens,
            lines_added = excluded.lines_added,
            lines_removed = excluded.lines_removed,
            files_modified_count = excluded.files_modified_count,
            files_modified_json = excluded.files_modified_json,
            raw_start_json = excluded.raw_start_json,
            raw_shutdown_json = excluded.raw_shutdown_json,
            imported_at = CURRENT_TIMESTAMP,
            source = excluded.source,
            is_estimated = excluded.is_estimated
        """,
        (
            session.session_id,
            str(session.source_file),
            session.source_mtime_ns,
            session.copilot_version,
            session.started_at,
            session.shutdown_at,
            session.shutdown_type,
            session.duration_seconds,
            session.cwd,
            session.git_root,
            session.branch,
            session.repository,
            session.selected_model,
            session.current_model,
            session.total_premium_requests,
            session.total_api_duration_ms,
            session.total_requests,
            session.total_input_tokens,
            session.total_output_tokens,
            session.total_cache_read_tokens,
            session.total_cache_write_tokens,
            session.total_tokens,
            session.lines_added,
            session.lines_removed,
            session.files_modified_count,
            json.dumps(session.files_modified),
            session.raw_start_json,
            session.raw_shutdown_json,
            session.source,
            1 if session.is_estimated else 0,
        ),
    )

    connection.execute(
        "DELETE FROM session_models WHERE session_id = ?",
        (session.session_id,),
    )
    connection.executemany(
        """
        INSERT INTO session_models (
            session_id,
            model_name,
            request_count,
            premium_request_cost,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            total_tokens
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                session.session_id,
                model.model_name,
                model.request_count,
                model.premium_request_cost,
                model.input_tokens,
                model.output_tokens,
                model.cache_read_tokens,
                model.cache_write_tokens,
                model.total_tokens,
            )
            for model in session.models
        ],
    )
    return "updated" if exists else "inserted"


def fetch_summary(connection: sqlite3.Connection, scope: str | None = None) -> sqlite3.Row:
    where_clause, params = _scope_filter(scope)
    return connection.execute(
        f"""
        SELECT
            COUNT(*) AS session_count,
            COALESCE(SUM(total_requests), 0) AS total_requests,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(total_input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(total_output_tokens), 0) AS total_output_tokens,
            COALESCE(SUM(total_cache_read_tokens), 0) AS total_cache_read_tokens,
            COALESCE(SUM(total_cache_write_tokens), 0) AS total_cache_write_tokens,
            COALESCE(SUM(total_premium_requests), 0) AS total_premium_requests,
            COALESCE(SUM(lines_added), 0) AS lines_added,
            COALESCE(SUM(lines_removed), 0) AS lines_removed,
            COALESCE(SUM(duration_seconds), 0) AS duration_seconds
        FROM sessions
        {where_clause}
        """,
        params,
    ).fetchone()


def fetch_model_breakdown(connection: sqlite3.Connection, scope: str | None = None) -> list[sqlite3.Row]:
    where_clause, params = _scope_filter(scope, "s")
    return connection.execute(
        f"""
        SELECT
            sm.model_name,
            COUNT(DISTINCT sm.session_id) AS session_count,
            COALESCE(SUM(sm.request_count), 0) AS request_count,
            COALESCE(SUM(sm.total_tokens), 0) AS total_tokens,
            COALESCE(SUM(sm.input_tokens), 0) AS input_tokens,
            COALESCE(SUM(sm.output_tokens), 0) AS output_tokens,
            COALESCE(SUM(sm.cache_read_tokens), 0) AS cache_read_tokens,
            COALESCE(SUM(sm.cache_write_tokens), 0) AS cache_write_tokens,
            COALESCE(SUM(sm.premium_request_cost), 0) AS premium_request_cost
        FROM session_models sm
        JOIN sessions s ON s.session_id = sm.session_id
        {where_clause}
        GROUP BY sm.model_name
        ORDER BY total_tokens DESC, model_name ASC
        """,
        params,
    ).fetchall()


def fetch_repo_breakdown(connection: sqlite3.Connection, scope: str | None = None) -> list[sqlite3.Row]:
    where_clause, params = _scope_filter(scope)
    scope_expression = _scope_expression()
    return connection.execute(
        f"""
        SELECT
            {scope_expression} AS scope,
            COUNT(*) AS session_count,
            COALESCE(SUM(total_requests), 0) AS total_requests,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(total_premium_requests), 0) AS total_premium_requests,
            COALESCE(SUM(lines_added), 0) AS lines_added,
            COALESCE(SUM(lines_removed), 0) AS lines_removed,
            COALESCE(SUM(duration_seconds), 0) AS duration_seconds
        FROM sessions
        {where_clause}
        GROUP BY {scope_expression}
        ORDER BY total_tokens DESC, scope ASC
        """,
        params,
    ).fetchall()


def fetch_daily_breakdown(connection: sqlite3.Connection, scope: str | None = None) -> list[sqlite3.Row]:
    where_clause, params = _scope_filter(scope)
    return connection.execute(
        f"""
        SELECT
            substr(COALESCE(started_at, shutdown_at), 1, 10) AS day,
            COUNT(*) AS session_count,
            COALESCE(SUM(total_requests), 0) AS total_requests,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(total_premium_requests), 0) AS total_premium_requests,
            COALESCE(SUM(duration_seconds), 0) AS duration_seconds
        FROM sessions
        {where_clause}
        GROUP BY substr(COALESCE(started_at, shutdown_at), 1, 10)
        ORDER BY day DESC
        """,
        params,
    ).fetchall()


def fetch_recent_sessions(
    connection: sqlite3.Connection,
    limit: int = 25,
    scope: str | None = None,
) -> list[sqlite3.Row]:
    where_clause, params = _scope_filter(scope)
    scope_expression = _scope_expression()
    return connection.execute(
        f"""
        SELECT
            session_id,
            started_at,
            shutdown_at,
            {scope_expression} AS scope,
            current_model,
            total_requests,
            total_tokens,
            total_premium_requests,
            lines_added,
            lines_removed,
            duration_seconds,
            source,
            is_estimated
        FROM sessions
        {where_clause}
        ORDER BY COALESCE(started_at, shutdown_at) DESC
        LIMIT ?
        """,
        params + (limit,),
    ).fetchall()


def fetch_session_cost_rows(connection: sqlite3.Connection, scope: str | None = None) -> list[sqlite3.Row]:
    where_clause, params = _scope_filter(scope, "s")
    scope_expression = _scope_expression("s")
    return connection.execute(
        f"""
        SELECT
            s.session_id,
            COALESCE(s.started_at, s.shutdown_at) AS session_timestamp,
            substr(COALESCE(s.started_at, s.shutdown_at), 1, 10) AS day,
            {scope_expression} AS scope,
            s.current_model,
            sm.model_name,
            sm.premium_request_cost,
            sm.input_tokens,
            sm.output_tokens,
            sm.cache_read_tokens,
            sm.cache_write_tokens,
            sm.total_tokens
        FROM session_models sm
        JOIN sessions s ON s.session_id = sm.session_id
        {where_clause}
        ORDER BY session_timestamp DESC, sm.model_name ASC
        """,
        params,
    ).fetchall()


def fetch_source_breakdown(connection: sqlite3.Connection, scope: str | None = None) -> list[sqlite3.Row]:
    where_clause, params = _scope_filter(scope)
    return connection.execute(
        f"""
        SELECT
            source,
            COUNT(*) AS session_count,
            COALESCE(SUM(total_requests), 0) AS total_requests,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(total_premium_requests), 0) AS total_premium_requests,
            SUM(is_estimated) AS estimated_count
        FROM sessions
        {where_clause}
        GROUP BY source
        ORDER BY total_tokens DESC
        """,
        params,
    ).fetchall()

