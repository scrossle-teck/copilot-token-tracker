from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from tokentracker.models import ModelMetrics, SessionMetrics

# Approximate tokens per character for English text.
# GPT/Claude tokenizers average ~0.25 tokens per character for typical prose.
TOKENS_PER_CHAR = 0.25

_SOURCE = "vscode-chat"


def discover_vscode_db_paths(storage_root: Path | None = None) -> list[Path]:
    """Find all VS Code workspace ``state.vscdb`` files that contain Copilot Chat data."""
    if storage_root is None:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return []
        storage_root = Path(appdata) / "Code" / "User" / "workspaceStorage"
    if not storage_root.is_dir():
        return []

    results: list[Path] = []
    for child in storage_root.iterdir():
        if not child.is_dir():
            continue
        db_path = child / "state.vscdb"
        if db_path.is_file():
            results.append(db_path)
    return sorted(results, key=lambda p: p.stat().st_mtime_ns, reverse=True)


def parse_vscode_sessions(db_path: Path) -> list[SessionMetrics]:
    """Parse Copilot Chat sessions from a single VS Code ``state.vscdb`` file.

    Opens the database in read-only mode so VS Code is never blocked.
    Returns a list of :class:`SessionMetrics` for completed chat sessions.
    """
    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return []

    try:
        return list(_extract_sessions(conn, db_path))
    finally:
        conn.close()


def iter_all_vscode_sessions(storage_root: Path | None = None) -> Iterable[SessionMetrics]:
    """Discover all VS Code workspace DBs and yield chat sessions."""
    for db_path in discover_vscode_db_paths(storage_root):
        yield from parse_vscode_sessions(db_path)


def _extract_sessions(
    conn: sqlite3.Connection, db_path: Path,
) -> Iterable[SessionMetrics]:
    """Read the ChatSessionStore index and interactive-session history."""
    index = _read_json_key(conn, "chat.ChatSessionStore.index")
    if not isinstance(index, dict):
        return

    entries = index.get("entries", {})
    if not isinstance(entries, dict):
        return

    non_empty = {k: v for k, v in entries.items() if isinstance(v, dict) and not v.get("isEmpty", True)}
    if not non_empty:
        return

    history_by_location = _read_json_key(conn, "memento/interactive-session")
    all_history = _collect_all_history_entries(history_by_location)

    # Extract model name and total estimated tokens from the full history,
    # then distribute proportionally across sessions.
    model_name, total_tokens = _extract_model_and_tokens(all_history)
    session_count = len(non_empty)
    per_session_input = total_tokens["input"] // max(session_count, 1)
    per_session_output = total_tokens["output"] // max(session_count, 1)
    per_session_requests = max(total_tokens["request_count"] // max(session_count, 1), 1)

    mtime_ns = db_path.stat().st_mtime_ns

    for session_id, meta in non_empty.items():
        session = _build_session(
            session_id=str(session_id),
            meta=meta,
            model_name=model_name,
            estimated_input=per_session_input,
            estimated_output=per_session_output,
            request_count=per_session_requests,
            db_path=db_path,
            mtime_ns=mtime_ns,
        )
        if session is not None:
            yield session


def _build_session(
    session_id: str,
    meta: dict[str, object],
    model_name: str | None,
    estimated_input: int,
    estimated_output: int,
    request_count: int,
    db_path: Path,
    mtime_ns: int,
) -> SessionMetrics | None:
    """Build a :class:`SessionMetrics` from a VS Code chat session."""
    prefixed_id = f"vscode-chat:{session_id}"

    timing = meta.get("timing", {})
    if isinstance(timing, dict):
        start_ms = timing.get("startTime")
        end_ms = timing.get("endTime")
    else:
        start_ms = None
        end_ms = None

    last_msg_date = meta.get("lastMessageDate")

    started_at = _ms_to_iso(start_ms) if start_ms else None
    shutdown_at = _ms_to_iso(end_ms) or _ms_to_iso(last_msg_date)
    if shutdown_at is None:
        return None

    duration = None
    if start_ms is not None and (end_ms or last_msg_date):
        end_val = end_ms or last_msg_date
        try:
            duration = max(0, int((int(end_val) - int(start_ms)) / 1000))
        except (TypeError, ValueError):
            pass

    title = meta.get("title", "")
    location = meta.get("initialLocation", "panel")

    total_tokens = estimated_input + estimated_output

    models: list[ModelMetrics] = []
    if model_name and total_tokens > 0:
        models.append(
            ModelMetrics(
                model_name=model_name,
                request_count=request_count,
                premium_request_cost=0.0,
                input_tokens=estimated_input,
                output_tokens=estimated_output,
                cache_read_tokens=0,
                cache_write_tokens=0,
            )
        )

    return SessionMetrics(
        session_id=prefixed_id,
        source_file=db_path,
        source_mtime_ns=mtime_ns,
        copilot_version=None,
        started_at=started_at,
        shutdown_at=shutdown_at,
        shutdown_type=None,
        duration_seconds=duration,
        cwd=None,
        git_root=None,
        branch=None,
        repository=None,
        selected_model=model_name,
        current_model=model_name,
        total_premium_requests=0.0,
        total_api_duration_ms=0,
        lines_added=0,
        lines_removed=0,
        files_modified=[],
        models=models,
        raw_start_json=None,
        raw_shutdown_json=json.dumps({"title": title, "location": location}, sort_keys=True),
        source=_SOURCE,
        is_estimated=True,
    )


def _extract_model_and_tokens(
    history_entries: list[dict[str, object]],
) -> tuple[str | None, dict[str, int]]:
    """Extract the primary model and estimate token counts from chat history entries."""
    model_name: str | None = None
    total_input_chars = 0
    request_count = 0

    for entry in history_entries:
        if not isinstance(entry, dict):
            continue

        input_text = entry.get("inputText", "")
        if isinstance(input_text, str):
            total_input_chars += len(input_text)

        request_count += 1

        if model_name is None:
            selected = entry.get("selectedModel", {})
            if isinstance(selected, dict):
                meta = selected.get("metadata", {})
                if isinstance(meta, dict):
                    model_name = meta.get("id") or meta.get("name")
                    if model_name:
                        model_name = str(model_name)

    estimated_input = int(total_input_chars * TOKENS_PER_CHAR)
    # Estimate output as ~2× input (models typically respond with more text)
    estimated_output = int(estimated_input * 2)

    return model_name, {
        "input": estimated_input,
        "output": estimated_output,
        "request_count": request_count,
    }


def _collect_all_history_entries(
    history_data: object,
) -> list[dict[str, object]]:
    """Collect all chat history entries from the interactive-session memento.

    The memento stores entries per *location* (``copilot``, ``editor-copilot``,
    etc.) rather than per session, so we aggregate them all for model detection
    and global token estimation.
    """
    if not isinstance(history_data, dict):
        return []

    history = history_data.get("history", {})
    if not isinstance(history, dict):
        return []

    entries: list[dict[str, object]] = []
    for _location, items in history.items():
        if isinstance(items, list):
            entries.extend(e for e in items if isinstance(e, dict))
    return entries


def _read_json_key(conn: sqlite3.Connection, key: str) -> object:
    """Read a JSON-encoded value from the VS Code ``ItemTable``."""
    try:
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = ?", (key,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None


def _ms_to_iso(value: object) -> str | None:
    """Convert a millisecond epoch timestamp to an ISO 8601 string."""
    if value is None:
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")
