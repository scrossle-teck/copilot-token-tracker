from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from tokentracker.models import ModelMetrics, SessionMetrics


def discover_session_files(copilot_home: Path) -> list[Path]:
    session_state_dir = copilot_home / "session-state"
    if not session_state_dir.exists():
        return []
    session_files = [
        child / "events.jsonl"
        for child in session_state_dir.iterdir()
        if child.is_dir() and (child / "events.jsonl").exists()
    ]
    return sorted(session_files, key=lambda path: path.stat().st_mtime_ns, reverse=True)


def parse_completed_session(path: Path) -> SessionMetrics | None:
    start_event: dict[str, object] | None = None
    shutdown_event: dict[str, object] | None = None

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            event = json.loads(line)
            event_type = event.get("type")
            if event_type == "session.start":
                start_event = event
            elif event_type == "session.shutdown":
                shutdown_event = event

    if shutdown_event is None:
        return None

    start_data = _as_dict((start_event or {}).get("data"))
    shutdown_data = _as_dict(shutdown_event.get("data"))
    context = _as_dict(start_data.get("context"))

    models: list[ModelMetrics] = []
    for model_name, payload in _as_dict(shutdown_data.get("modelMetrics")).items():
        metrics = _as_dict(payload)
        requests = _as_dict(metrics.get("requests"))
        usage = _as_dict(metrics.get("usage"))
        models.append(
            ModelMetrics(
                model_name=str(model_name),
                request_count=_as_int(requests.get("count")),
                premium_request_cost=_as_float(requests.get("cost")),
                input_tokens=_as_int(usage.get("inputTokens")),
                output_tokens=_as_int(usage.get("outputTokens")),
                cache_read_tokens=_as_int(usage.get("cacheReadTokens")),
                cache_write_tokens=_as_int(usage.get("cacheWriteTokens")),
            )
        )

    shutdown_timestamp = str(shutdown_event["timestamp"])

    return SessionMetrics(
        session_id=str(start_data.get("sessionId") or path.parent.name),
        source_file=path,
        source_mtime_ns=path.stat().st_mtime_ns,
        copilot_version=_as_optional_str(start_data.get("copilotVersion")),
        started_at=_coerce_started_at(start_data, shutdown_data),
        shutdown_at=shutdown_timestamp,
        shutdown_type=_as_optional_str(shutdown_data.get("shutdownType")),
        duration_seconds=_compute_duration_seconds(shutdown_timestamp, shutdown_data, start_data),
        cwd=_as_optional_str(context.get("cwd")),
        git_root=_as_optional_str(context.get("gitRoot")),
        branch=_as_optional_str(context.get("branch")),
        repository=_as_optional_str(context.get("repository")),
        selected_model=_as_optional_str(start_data.get("selectedModel")),
        current_model=_as_optional_str(shutdown_data.get("currentModel")),
        total_premium_requests=_as_float(shutdown_data.get("totalPremiumRequests")),
        total_api_duration_ms=_as_int(shutdown_data.get("totalApiDurationMs")),
        lines_added=_as_int(_as_dict(shutdown_data.get("codeChanges")).get("linesAdded")),
        lines_removed=_as_int(_as_dict(shutdown_data.get("codeChanges")).get("linesRemoved")),
        files_modified=[
            str(value)
            for value in _as_list(_as_dict(shutdown_data.get("codeChanges")).get("filesModified"))
        ],
        models=models,
        raw_start_json=json.dumps(start_event, sort_keys=True) if start_event else None,
        raw_shutdown_json=json.dumps(shutdown_event, sort_keys=True),
    )


def iter_completed_sessions(copilot_home: Path) -> Iterable[SessionMetrics]:
    for path in discover_session_files(copilot_home):
        session = parse_completed_session(path)
        if session is not None:
            yield session


def _coerce_started_at(start_data: dict[str, object], shutdown_data: dict[str, object]) -> str | None:
    if isinstance(start_data.get("startTime"), str):
        return str(start_data["startTime"])
    start_time_ms = shutdown_data.get("sessionStartTime")
    if start_time_ms is None:
        return None
    return _datetime_to_iso(_parse_timestamp(start_time_ms))


def _compute_duration_seconds(
    shutdown_timestamp: str,
    shutdown_data: dict[str, object],
    start_data: dict[str, object],
) -> int | None:
    shutdown_dt = _parse_timestamp(shutdown_timestamp)
    start_ms = shutdown_data.get("sessionStartTime")
    if start_ms is not None:
        start_dt = _parse_timestamp(start_ms)
    elif start_data.get("startTime") is not None:
        start_dt = _parse_timestamp(start_data["startTime"])
    else:
        return None
    return max(0, int((shutdown_dt - start_dt).total_seconds()))


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    raise TypeError(f"Unsupported timestamp: {value!r}")


def _datetime_to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _as_int(value: object) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _as_float(value: object) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None

