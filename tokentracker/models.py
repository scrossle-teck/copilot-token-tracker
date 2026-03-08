from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ModelMetrics:
    model_name: str
    request_count: int
    premium_request_cost: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


@dataclass(slots=True)
class SessionMetrics:
    session_id: str
    source_file: Path
    source_mtime_ns: int
    copilot_version: str | None
    started_at: str | None
    shutdown_at: str
    shutdown_type: str | None
    duration_seconds: int | None
    cwd: str | None
    git_root: str | None
    branch: str | None
    repository: str | None
    selected_model: str | None
    current_model: str | None
    total_premium_requests: float
    total_api_duration_ms: int
    lines_added: int
    lines_removed: int
    files_modified: list[str] = field(default_factory=list)
    models: list[ModelMetrics] = field(default_factory=list)
    raw_start_json: str | None = None
    raw_shutdown_json: str | None = None

    @property
    def files_modified_count(self) -> int:
        return len(self.files_modified)

    @property
    def total_requests(self) -> int:
        return sum(model.request_count for model in self.models)

    @property
    def total_input_tokens(self) -> int:
        return sum(model.input_tokens for model in self.models)

    @property
    def total_output_tokens(self) -> int:
        return sum(model.output_tokens for model in self.models)

    @property
    def total_cache_read_tokens(self) -> int:
        return sum(model.cache_read_tokens for model in self.models)

    @property
    def total_cache_write_tokens(self) -> int:
        return sum(model.cache_write_tokens for model in self.models)

    @property
    def total_tokens(self) -> int:
        return sum(model.total_tokens for model in self.models)


@dataclass(slots=True)
class SyncResult:
    session_files_seen: int
    imported_sessions: int
    updated_sessions: int
    skipped_sessions: int
    incomplete_sessions: int
    database_path: Path
    dashboard_path: Path | None = None

