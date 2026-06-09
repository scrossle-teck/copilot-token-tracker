from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from tokentracker.importer import parse_completed_session
from tokentracker import __version__
from tokentracker.cli import handle_doctor, handle_summary, main, sync_sessions
from tokentracker.dashboard import project_dashboard_path
from tokentracker.pricing import (
    DEFAULT_USD_PER_AI_CREDIT,
    DEFAULT_USD_PER_PREMIUM_REQUEST,
    PUBLIC_MODEL_PRICING,
    ensure_pricing_file,
    estimate_model_currency_amount,
    normalize_pricing,
)


class TokenTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="tokentracker-tests-"))
        self.copilot_home = self.temp_dir / ".copilot"
        self.session_state_dir = self.copilot_home / "session-state"
        self.data_dir = self.temp_dir / "tracker-data"
        self.repo_root = Path(__file__).resolve().parent
        fixtures_root = self.repo_root / "fixtures"

        shutil.copytree(
            fixtures_root / "completed-session",
            self.session_state_dir / "fixture-session-1",
        )
        shutil.copytree(
            fixtures_root / "incomplete-session",
            self.session_state_dir / "fixture-session-2",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_completed_session(
        self,
        session_id: str,
        repository: str,
        model_name: str = "claude-haiku-4.5",
        started_at: str = "2026-03-07T22:00:00.000Z",
        shutdown_at: str = "2026-03-07T22:01:00.000Z",
    ) -> None:
        session_dir = self.session_state_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        start_ms = int(start_dt.timestamp() * 1000)
        (session_dir / "events.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "session.start",
                            "data": {
                                "sessionId": session_id,
                                "version": 1,
                                "producer": "copilot-agent",
                                "copilotVersion": "1.0.2",
                                "startTime": started_at,
                                "selectedModel": model_name,
                                "context": {
                                    "cwd": "C:\\Projects\\Deep\\Nested\\Example\\Workspace\\With\\Long\\Paths",
                                    "gitRoot": "C:\\Projects\\Deep\\Nested\\Example\\Workspace\\With\\Long\\Paths",
                                    "branch": "main",
                                    "repository": repository,
                                },
                            },
                            "id": f"start-{session_id}",
                            "timestamp": started_at,
                            "parentId": None,
                        }
                    ),
                    json.dumps(
                        {
                            "type": "session.shutdown",
                            "data": {
                                "shutdownType": "routine",
                                "totalPremiumRequests": 0.33,
                                "totalApiDurationMs": 1000,
                                "sessionStartTime": start_ms,
                                "codeChanges": {
                                    "linesAdded": 1,
                                    "linesRemoved": 0,
                                    "filesModified": ["src\\really\\long\\path\\example.py"],
                                },
                                "modelMetrics": {
                                    model_name: {
                                        "requests": {"count": 1, "cost": 0.33},
                                        "usage": {
                                            "inputTokens": 100,
                                            "outputTokens": 20,
                                            "cacheReadTokens": 0,
                                            "cacheWriteTokens": 0,
                                        },
                                    }
                                },
                                "currentModel": model_name,
                            },
                            "id": f"shutdown-{session_id}",
                            "timestamp": shutdown_at,
                            "parentId": f"start-{session_id}",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def test_sync_imports_completed_sessions_and_ignores_incomplete_ones(self) -> None:
        result = sync_sessions(self.copilot_home, self.data_dir, regenerate_dashboard=True, sources={"cli"})

        self.assertEqual(result.session_files_seen, 2)
        self.assertEqual(result.imported_sessions, 1)
        self.assertEqual(result.updated_sessions, 0)
        self.assertEqual(result.incomplete_sessions, 1)
        self.assertTrue((self.data_dir / "dashboard.html").exists())

        with sqlite3.connect(self.data_dir / "token-tracker.db") as connection:
            row = connection.execute(
                """
                SELECT
                    session_id,
                    repository,
                    total_requests,
                    total_tokens,
                    total_premium_requests,
                    lines_added,
                    lines_removed,
                    duration_seconds,
                    total_input_tokens,
                    total_output_tokens,
                    total_cache_read_tokens,
                    total_cache_write_tokens
                FROM sessions
                """
            ).fetchone()
            assert row is not None
            self.assertEqual(row[0], "fixture-session-1")
            self.assertEqual(row[1], "octo/demo")
            self.assertEqual(row[2], 3)
            self.assertEqual(row[3], 1610)
            self.assertAlmostEqual(row[4], 2.5)
            self.assertEqual(row[5], 15)
            self.assertEqual(row[6], 4)
            self.assertEqual(row[7], 300)
            self.assertEqual(row[8], 1200)   # input_tokens
            self.assertEqual(row[9], 350)    # output_tokens
            self.assertEqual(row[10], 50)    # cache_read_tokens
            self.assertEqual(row[11], 10)    # cache_write_tokens
            self.assertEqual(
                row[8] + row[9] + row[10] + row[11],
                row[3],
                "input + output + cache_read + cache_write must equal total_tokens",
            )

            model_row = connection.execute(
                """
                SELECT model_name, request_count, total_tokens, premium_request_cost
                FROM session_models
                """
            ).fetchone()
            assert model_row is not None
            self.assertEqual(model_row[0], "gpt-5.4")
            self.assertEqual(model_row[1], 3)
            self.assertEqual(model_row[2], 1610)
            self.assertAlmostEqual(model_row[3], 2.5)

    def test_sync_is_idempotent_for_unchanged_sessions(self) -> None:
        first = sync_sessions(self.copilot_home, self.data_dir, regenerate_dashboard=False, sources={"cli"})
        second = sync_sessions(self.copilot_home, self.data_dir, regenerate_dashboard=False, sources={"cli"})

        self.assertEqual(first.imported_sessions, 1)
        self.assertEqual(second.imported_sessions, 0)
        self.assertEqual(second.updated_sessions, 0)
        self.assertEqual(second.skipped_sessions, 1)

        with sqlite3.connect(self.data_dir / "token-tracker.db") as connection:
            count = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            self.assertEqual(count, 1)

    def test_install_bridge_script_uses_python_without_repo_root(self) -> None:
        from tokentracker.cli import _render_hook_bridge

        bridge = _render_hook_bridge(
            python_executable=Path("C:\\Python313\\python.exe"),
            copilot_home=Path("C:\\Users\\Tester\\.copilot"),
            data_dir=Path("C:\\Users\\Tester\\.copilot\\token-tracker"),
        )

        self.assertIn("C:\\Python313\\python.exe", bridge)
        self.assertIn("--quiet", bridge)
        self.assertNotIn("Push-Location", bridge)

    def test_doctor_reports_install_state(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.copilot_home / "hooks").mkdir(parents=True, exist_ok=True)
        (self.copilot_home / "hooks" / "copilot-token-tracker.json").write_text("{}", encoding="utf-8")
        (self.copilot_home / "hooks" / "copilot-token-tracker-sync.ps1").write_text("echo ok", encoding="utf-8")
        sync_sessions(self.copilot_home, self.data_dir, regenerate_dashboard=True, sources={"cli"})

        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = handle_doctor(
                type(
                    "Args",
                    (),
                    {
                        "copilot_home": self.copilot_home,
                        "data_dir": self.data_dir,
                    },
                )()
            )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn(f"Version: {__version__}", output)
        self.assertIn("Hook config:", output)
        self.assertIn("Imported sessions: 1", output)
        self.assertIn("Status: tracker looks ready.", output)

    def test_version_uses_cli_program_name(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as context:
                main(["--version"])

        self.assertEqual(context.exception.code, 0)
        self.assertEqual(stdout.getvalue().strip(), f"tokentracker {__version__}")

    def test_estimated_cost_prefers_token_rates_and_falls_back_to_premium_conversion(self) -> None:
        pricing = {
            "currency": "USD",
            "usdPerPremiumRequest": 0.2,
            "models": {
                "gpt-5.4": {
                    "inputCostPer1M": 2.5,
                    "outputCostPer1M": 15,
                    "cacheReadCostPer1M": 0.25,
                    "cacheWriteCostPer1M": 2.5,
                }
            },
        }

        token_based = estimate_model_currency_amount(
            model_name="gpt-5.4",
            input_tokens=1200,
            output_tokens=350,
            cache_read_tokens=50,
            cache_write_tokens=10,
            premium_request_units=2.5,
            pricing=pricing,
        )
        fallback = estimate_model_currency_amount(
            model_name="unknown-model",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            premium_request_units=2.5,
            pricing=pricing,
        )

        assert token_based is not None
        assert fallback is not None
        self.assertAlmostEqual(token_based, 0.0082875)
        self.assertAlmostEqual(fallback, 0.5)

    def test_estimated_cost_prefers_ai_credit_fallback_over_legacy_premium(self) -> None:
        pricing = {
            "currency": "USD",
            "usdPerAiCredit": 0.01,
            "usdPerPremiumRequest": 0.2,
            "models": {
                "default": {
                    "inputCostPer1M": None,
                    "outputCostPer1M": None,
                    "cacheReadCostPer1M": 0,
                    "cacheWriteCostPer1M": 0,
                }
            },
        }
        amount = estimate_model_currency_amount(
            model_name="unknown-model",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            premium_request_units=2.5,
            pricing=pricing,
            ai_credit_units=12.0,
        )

        assert amount is not None
        self.assertAlmostEqual(amount, 0.12)

    def test_summary_reports_estimated_cost_from_pricing_file(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "pricing.json").write_text(
            json.dumps(
                {
                    "currency": "USD",
                    "models": {
                        "gpt-5.4": PUBLIC_MODEL_PRICING["gpt-5.4"],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = handle_summary(
                type(
                    "Args",
                    (),
                    {
                        "copilot_home": self.copilot_home,
                        "data_dir": self.data_dir,
                        "sources": "cli",
                    },
                )()
            )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Estimated cost (USD): $0.01", output)
        self.assertIn("Cost mode: model token rates", output)

    def test_default_pricing_file_uses_public_premium_request_rate(self) -> None:
        path = ensure_pricing_file(self.data_dir)
        pricing = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(pricing["costUnitLabel"], "AI credits")
        self.assertEqual(pricing["usdPerAiCredit"], DEFAULT_USD_PER_AI_CREDIT)
        self.assertEqual(pricing["usdPerPremiumRequest"], DEFAULT_USD_PER_PREMIUM_REQUEST)
        self.assertEqual(pricing["models"]["gpt-5.4"], PUBLIC_MODEL_PRICING["gpt-5.4"])
        self.assertEqual(
            pricing["models"]["claude-opus-4.6"],
            PUBLIC_MODEL_PRICING["claude-opus-4.6"],
        )

    def test_default_only_pricing_file_is_upgraded_with_public_model_rates(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        current_default = {
            "currency": "USD",
            "costUnitLabel": "premium requests",
            "usdPerPremiumRequest": DEFAULT_USD_PER_PREMIUM_REQUEST,
            "models": {
                "default": {
                    "inputCostPer1M": None,
                    "outputCostPer1M": None,
                    "cacheReadCostPer1M": 0,
                    "cacheWriteCostPer1M": 0,
                }
            },
        }
        path = self.data_dir / "pricing.json"
        path.write_text(json.dumps(current_default) + "\n", encoding="utf-8")

        ensure_pricing_file(self.data_dir)
        upgraded = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(upgraded["usdPerAiCredit"], DEFAULT_USD_PER_AI_CREDIT)
        self.assertEqual(upgraded["usdPerPremiumRequest"], DEFAULT_USD_PER_PREMIUM_REQUEST)
        self.assertEqual(upgraded["models"]["gpt-5.4"], PUBLIC_MODEL_PRICING["gpt-5.4"])
        self.assertEqual(
            upgraded["models"]["gemini-3-pro-preview"],
            PUBLIC_MODEL_PRICING["gemini-3-pro-preview"],
        )

    def test_legacy_blank_pricing_file_is_upgraded_to_public_default_rate(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        legacy = {
            "currency": "USD",
            "costUnitLabel": "premium requests",
            "usdPerPremiumRequest": None,
            "models": {
                "default": {
                    "inputCostPer1M": None,
                    "outputCostPer1M": None,
                    "cacheReadCostPer1M": 0,
                    "cacheWriteCostPer1M": 0,
                }
            },
        }
        path = self.data_dir / "pricing.json"
        path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

        ensure_pricing_file(self.data_dir)
        upgraded = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(upgraded["usdPerAiCredit"], DEFAULT_USD_PER_AI_CREDIT)
        self.assertEqual(upgraded["usdPerPremiumRequest"], DEFAULT_USD_PER_PREMIUM_REQUEST)
        self.assertEqual(upgraded["models"]["gpt-5.4"], PUBLIC_MODEL_PRICING["gpt-5.4"])

    def test_normalize_pricing_keeps_legacy_file_without_ai_rate(self) -> None:
        normalized = normalize_pricing(
            {
                "currency": "USD",
                "costUnitLabel": "premium requests",
                "usdPerPremiumRequest": 0.04,
                "models": {"default": {"inputCostPer1M": None, "outputCostPer1M": None}},
            }
        )

        self.assertIsNone(normalized["usdPerAiCredit"])
        self.assertEqual(normalized["usdPerPremiumRequest"], 0.04)

    def test_dashboard_html_includes_history_note_and_layout_guards(self) -> None:
        long_repo = "octo/" + ("very-long-repository-name-" * 6).rstrip("-")
        self._write_completed_session("fixture-session-long", long_repo)

        sync_sessions(self.copilot_home, self.data_dir, regenerate_dashboard=True, sources={"cli"})
        html = (self.data_dir / "dashboard.html").read_text(encoding="utf-8")
        project_page = project_dashboard_path(self.data_dir / "dashboard.html", long_repo)
        project_html = project_page.read_text(encoding="utf-8")

        self.assertIn("Versions before <code>0.0.422</code>", html)
        self.assertIn("class=\"table-wrap\"", html)
        self.assertIn("overflow-wrap: anywhere;", html)
        self.assertIn("class=\"wrap-cell\"", html)
        self.assertIn("AI credits", html)
        self.assertIn("Pricing basis", html)
        self.assertIn("Input $1", html)
        self.assertIn("Cache write $1.25", html)
        self.assertIn("projects/", html)
        self.assertIn(long_repo, html)
        self.assertTrue(project_page.exists())
        self.assertIn("Back to overview", project_html)
        self.assertIn(long_repo, project_html)

    def test_summary_can_filter_to_single_scope(self) -> None:
        extra_repo = "octo/another-project"
        self._write_completed_session("fixture-session-extra", extra_repo)

        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = handle_summary(
                type(
                    "Args",
                    (),
                    {
                        "copilot_home": self.copilot_home,
                        "data_dir": self.data_dir,
                        "scope": "octo/demo",
                        "sources": "cli",
                    },
                )()
            )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Scope: octo/demo", output)
        self.assertIn("Sessions: 1", output)
        self.assertIn("Tokens: 1,610", output)

    def test_summary_can_filter_to_specific_month(self) -> None:
        self._write_completed_session(
            "fixture-session-april",
            "octo/april-project",
            started_at="2026-04-01T00:00:00.000Z",
            shutdown_at="2026-04-01T00:01:00.000Z",
        )

        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = handle_summary(
                type(
                    "Args",
                    (),
                    {
                        "copilot_home": self.copilot_home,
                        "data_dir": self.data_dir,
                        "scope": None,
                        "sources": "cli",
                        "month": "2026-04",
                        "current_month": False,
                    },
                )()
            )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Month: 2026-04", output)
        self.assertIn("Sessions: 1", output)
        self.assertIn("Tokens: 120", output)

    def test_importer_parses_nano_aiu_and_billing_token_details(self) -> None:
        session_id = "aiu-session"
        session_dir = self.session_state_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "session.shutdown",
            "timestamp": "2026-06-09T12:01:00.000Z",
            "data": {
                "shutdownType": "routine",
                "totalPremiumRequests": 1.2,
                "totalNanoAiu": 19870488000,
                "tokenDetails": {
                    "input": {"tokenCount": 148364},
                    "cache_read": {"tokenCount": 723456},
                    "output": {"tokenCount": 12278},
                },
                "totalApiDurationMs": 1000,
                "sessionStartTime": 1772920800000,
                "codeChanges": {
                    "linesAdded": 0,
                    "linesRemoved": 0,
                    "filesModified": [],
                },
                "modelMetrics": {
                    "gpt-5.4-mini": {
                        "requests": {"count": 2, "cost": 1.2},
                        "usage": {
                            "inputTokens": 871820,
                            "outputTokens": 12278,
                            "cacheReadTokens": 723456,
                            "cacheWriteTokens": 0,
                        },
                        "totalNanoAiu": 19870488000,
                        "tokenDetails": {
                            "input": {"tokenCount": 148364},
                            "cache_read": {"tokenCount": 723456},
                            "output": {"tokenCount": 12278},
                        },
                    }
                },
                "currentModel": "gpt-5.4-mini",
            },
        }
        (session_dir / "events.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")

        session = parse_completed_session(session_dir / "events.jsonl")
        assert session is not None
        self.assertEqual(session.total_nano_aiu, 19870488000)
        assert session.total_ai_credits is not None
        self.assertAlmostEqual(session.total_ai_credits, 19.870488)
        self.assertEqual(session.billed_input_tokens, 148364)
        self.assertEqual(session.billed_cache_read_tokens, 723456)
        self.assertEqual(session.billed_output_tokens, 12278)

        model = session.models[0]
        self.assertEqual(model.total_nano_aiu, 19870488000)
        assert model.ai_credits is not None
        self.assertAlmostEqual(model.ai_credits, 19.870488)
        self.assertEqual(model.billed_input_tokens, 148364)
        self.assertEqual(model.billed_cache_read_tokens, 723456)
        self.assertEqual(model.billed_output_tokens, 12278)

    def test_importer_handles_missing_nano_aiu_fields(self) -> None:
        session = parse_completed_session(self.session_state_dir / "fixture-session-1" / "events.jsonl")
        assert session is not None
        self.assertIsNone(session.total_nano_aiu)
        self.assertIsNone(session.total_ai_credits)
        self.assertIsNone(session.billed_input_tokens)
        self.assertIsNone(session.billed_cache_read_tokens)
        self.assertIsNone(session.billed_output_tokens)

    def test_summary_prefers_ai_credits_and_keeps_legacy_units_for_mixed_data(self) -> None:
        session_id = "aiu-summary-session"
        session_dir = self.session_state_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "session.shutdown",
            "timestamp": "2026-06-09T12:01:00.000Z",
            "data": {
                "shutdownType": "routine",
                "totalPremiumRequests": 1.2,
                "totalNanoAiu": 19870488000,
                "tokenDetails": {
                    "input": {"tokenCount": 148364},
                    "cache_read": {"tokenCount": 723456},
                    "output": {"tokenCount": 12278},
                },
                "totalApiDurationMs": 1000,
                "sessionStartTime": 1772920800000,
                "codeChanges": {
                    "linesAdded": 0,
                    "linesRemoved": 0,
                    "filesModified": [],
                },
                "modelMetrics": {
                    "gpt-5.4-mini": {
                        "requests": {"count": 2, "cost": 1.2},
                        "usage": {
                            "inputTokens": 871820,
                            "outputTokens": 12278,
                            "cacheReadTokens": 723456,
                            "cacheWriteTokens": 0,
                        },
                        "totalNanoAiu": 19870488000,
                        "tokenDetails": {
                            "input": {"tokenCount": 148364},
                            "cache_read": {"tokenCount": 723456},
                            "output": {"tokenCount": 12278},
                        },
                    }
                },
                "currentModel": "gpt-5.4-mini",
            },
        }
        (session_dir / "events.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")

        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = handle_summary(
                type(
                    "Args",
                    (),
                    {
                        "copilot_home": self.copilot_home,
                        "data_dir": self.data_dir,
                        "scope": None,
                        "sources": "cli",
                        "month": None,
                        "current_month": False,
                    },
                )()
            )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("AI credits:", output)
        self.assertIn("Legacy premium request units:", output)

    def test_storage_migrates_existing_database_to_ai_credit_columns(self) -> None:
        from tokentracker.storage import connect

        self.data_dir.mkdir(parents=True, exist_ok=True)
        database_path = self.data_dir / "token-tracker.db"
        with sqlite3.connect(database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE sessions (
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

                CREATE TABLE session_models (
                    session_id TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    request_count INTEGER NOT NULL,
                    premium_request_cost REAL NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cache_read_tokens INTEGER NOT NULL,
                    cache_write_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    PRIMARY KEY (session_id, model_name)
                );
                """
            )
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id,
                    source_file,
                    source_mtime_ns,
                    shutdown_at,
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
                    raw_shutdown_json,
                    source,
                    is_estimated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-session",
                    str(self.session_state_dir / "fixture-session-1" / "events.jsonl"),
                    1,
                    "2026-03-07T22:01:00.000Z",
                    1.0,
                    100,
                    1,
                    10,
                    10,
                    0,
                    0,
                    20,
                    0,
                    0,
                    0,
                    "[]",
                    "{}",
                    "cli",
                    0,
                ),
            )
            connection.commit()

        with connect(database_path) as connection:
            session_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            model_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(session_models)").fetchall()
            }
            self.assertIn("total_nano_aiu", session_columns)
            self.assertIn("total_ai_credits", session_columns)
            self.assertIn("billed_input_tokens", session_columns)
            self.assertIn("billed_output_tokens", session_columns)
            self.assertIn("billed_cache_read_tokens", session_columns)
            self.assertIn("total_nano_aiu", model_columns)
            self.assertIn("ai_credits", model_columns)
            self.assertIn("billed_input_tokens", model_columns)
            self.assertIn("billed_output_tokens", model_columns)
            self.assertIn("billed_cache_read_tokens", model_columns)

            row = connection.execute(
                "SELECT session_id, total_ai_credits, billed_input_tokens FROM sessions WHERE session_id = ?",
                ("legacy-session",),
            ).fetchone()
            assert row is not None
            self.assertEqual(row["session_id"], "legacy-session")
            self.assertIsNone(row["total_ai_credits"])
            self.assertIsNone(row["billed_input_tokens"])

    def test_sync_persists_ai_credit_fields_with_legacy_rows(self) -> None:
        session_id = "aiu-session-storage"
        session_dir = self.session_state_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "session.shutdown",
            "timestamp": "2026-06-09T12:01:00.000Z",
            "data": {
                "shutdownType": "routine",
                "totalPremiumRequests": 1.2,
                "totalNanoAiu": 19870488000,
                "tokenDetails": {
                    "input": {"tokenCount": 148364},
                    "cache_read": {"tokenCount": 723456},
                    "output": {"tokenCount": 12278},
                },
                "totalApiDurationMs": 1000,
                "sessionStartTime": 1772920800000,
                "codeChanges": {
                    "linesAdded": 0,
                    "linesRemoved": 0,
                    "filesModified": [],
                },
                "modelMetrics": {
                    "gpt-5.4-mini": {
                        "requests": {"count": 2, "cost": 1.2},
                        "usage": {
                            "inputTokens": 871820,
                            "outputTokens": 12278,
                            "cacheReadTokens": 723456,
                            "cacheWriteTokens": 0,
                        },
                        "totalNanoAiu": 19870488000,
                        "tokenDetails": {
                            "input": {"tokenCount": 148364},
                            "cache_read": {"tokenCount": 723456},
                            "output": {"tokenCount": 12278},
                        },
                    }
                },
                "currentModel": "gpt-5.4-mini",
            },
        }
        (session_dir / "events.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")

        sync_sessions(self.copilot_home, self.data_dir, regenerate_dashboard=False, sources={"cli"})

        with sqlite3.connect(self.data_dir / "token-tracker.db") as connection:
            connection.row_factory = sqlite3.Row
            new_row = connection.execute(
                """
                SELECT session_id, total_nano_aiu, total_ai_credits, billed_input_tokens,
                       billed_output_tokens, billed_cache_read_tokens
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            assert new_row is not None
            self.assertEqual(new_row["total_nano_aiu"], 19870488000)
            self.assertAlmostEqual(new_row["total_ai_credits"], 19.870488)
            self.assertEqual(new_row["billed_input_tokens"], 148364)
            self.assertEqual(new_row["billed_output_tokens"], 12278)
            self.assertEqual(new_row["billed_cache_read_tokens"], 723456)

            legacy_row = connection.execute(
                """
                SELECT session_id, total_nano_aiu, total_ai_credits, billed_input_tokens
                FROM sessions
                WHERE session_id = 'fixture-session-1'
                """
            ).fetchone()
            assert legacy_row is not None
            self.assertIsNone(legacy_row["total_nano_aiu"])
            self.assertIsNone(legacy_row["total_ai_credits"])
            self.assertIsNone(legacy_row["billed_input_tokens"])

            summary = connection.execute(
                "SELECT COALESCE(SUM(total_ai_credits), 0) AS credits FROM sessions"
            ).fetchone()
            assert summary is not None
            self.assertAlmostEqual(summary["credits"], 19.870488)


class VSCodeImporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="tokentracker-vscode-tests-"))
        self.copilot_home = self.temp_dir / ".copilot"
        self.data_dir = self.temp_dir / "tracker-data"
        self.vscode_storage = self.temp_dir / "vscode-storage"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_vscode_db(
        self,
        workspace_hash: str = "abc123",
        sessions: dict[str, dict] | None = None,
        history_entries: list[dict] | None = None,
    ) -> Path:
        """Create a mock state.vscdb with Copilot Chat session data."""
        workspace_dir = self.vscode_storage / workspace_hash
        workspace_dir.mkdir(parents=True, exist_ok=True)
        db_path = workspace_dir / "state.vscdb"

        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")

        if sessions is None:
            sessions = {
                "test-session-1": {
                    "sessionId": "test-session-1",
                    "title": "Implementing authentication",
                    "lastMessageDate": 1772920800000,
                    "isImported": False,
                    "initialLocation": "panel",
                    "isEmpty": False,
                    "timing": {
                        "startTime": 1772920500000,
                        "endTime": 1772920800000,
                    },
                },
            }

        index = {"version": 1, "entries": sessions}
        conn.execute(
            "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
            ("chat.ChatSessionStore.index", json.dumps(index)),
        )

        if history_entries is None:
            history_entries = [
                {
                    "inputText": "How do I implement JWT authentication in Python?",
                    "selectedModel": {
                        "identifier": "copilot/claude-sonnet-4.5",
                        "metadata": {
                            "id": "claude-sonnet-4.5",
                            "name": "Claude Sonnet 4.5",
                            "multiplier": "1x",
                            "maxInputTokens": 200000,
                            "maxOutputTokens": 16384,
                        },
                    },
                },
                {
                    "inputText": "Now add refresh token rotation to the implementation",
                    "selectedModel": {
                        "identifier": "copilot/claude-sonnet-4.5",
                        "metadata": {
                            "id": "claude-sonnet-4.5",
                            "name": "Claude Sonnet 4.5",
                        },
                    },
                },
            ]

        history_data = {"history": {"copilot": history_entries}}
        conn.execute(
            "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
            ("memento/interactive-session", json.dumps(history_data)),
        )

        conn.commit()
        conn.close()
        return db_path

    def test_discover_vscode_db_paths_finds_state_vscdb(self) -> None:
        from tokentracker.vscode_importer import discover_vscode_db_paths

        self._create_mock_vscode_db("workspace-1")
        self._create_mock_vscode_db("workspace-2")

        paths = discover_vscode_db_paths(self.vscode_storage)
        self.assertEqual(len(paths), 2)
        for path in paths:
            self.assertTrue(path.name == "state.vscdb")

    def test_discover_vscode_db_paths_returns_empty_for_missing_dir(self) -> None:
        from tokentracker.vscode_importer import discover_vscode_db_paths

        paths = discover_vscode_db_paths(self.temp_dir / "nonexistent")
        self.assertEqual(paths, [])

    def test_parse_vscode_sessions_extracts_session_metadata(self) -> None:
        from tokentracker.vscode_importer import parse_vscode_sessions

        db_path = self._create_mock_vscode_db()
        sessions = list(parse_vscode_sessions(db_path))

        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session.session_id, "vscode-chat:test-session-1")
        self.assertEqual(session.source, "vscode-chat")
        self.assertTrue(session.is_estimated)
        self.assertIsNotNone(session.started_at)
        self.assertIsNotNone(session.shutdown_at)
        self.assertEqual(session.duration_seconds, 300)

    def test_parse_vscode_sessions_estimates_tokens(self) -> None:
        from tokentracker.vscode_importer import parse_vscode_sessions

        db_path = self._create_mock_vscode_db()
        sessions = list(parse_vscode_sessions(db_path))

        session = sessions[0]
        self.assertGreater(session.total_tokens, 0)
        self.assertGreater(session.total_input_tokens, 0)
        self.assertGreater(session.total_output_tokens, 0)

    def test_parse_vscode_sessions_extracts_model_name(self) -> None:
        from tokentracker.vscode_importer import parse_vscode_sessions

        db_path = self._create_mock_vscode_db()
        sessions = list(parse_vscode_sessions(db_path))

        session = sessions[0]
        self.assertEqual(session.selected_model, "claude-sonnet-4.5")
        self.assertEqual(len(session.models), 1)
        self.assertEqual(session.models[0].model_name, "claude-sonnet-4.5")

    def test_parse_vscode_sessions_estimates_ai_credits_from_model_metadata(self) -> None:
        from tokentracker.vscode_importer import parse_vscode_sessions

        db_path = self._create_mock_vscode_db(
            history_entries=[
                {
                    "inputText": "A" * 100,
                    "selectedModel": {
                        "identifier": "copilot/gpt-5.3-codex",
                        "metadata": {
                            "id": "gpt-5.3-codex",
                            "name": "GPT-5.3-Codex",
                            "inputCost": 175,
                            "outputCost": 1400,
                            "cacheCost": 17,
                        },
                    },
                }
            ]
        )
        sessions = list(parse_vscode_sessions(db_path))

        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertIsNotNone(session.total_ai_credits)
        assert session.total_ai_credits is not None
        self.assertGreater(session.total_ai_credits, 0)
        self.assertIsNotNone(session.total_nano_aiu)

        model = session.models[0]
        self.assertIsNotNone(model.ai_credits)
        assert model.ai_credits is not None
        self.assertAlmostEqual(model.ai_credits, session.total_ai_credits)
        self.assertIsNotNone(model.total_nano_aiu)

    def test_parse_vscode_sessions_keeps_ai_credits_none_without_pricing_metadata(self) -> None:
        from tokentracker.vscode_importer import parse_vscode_sessions

        db_path = self._create_mock_vscode_db(
            history_entries=[
                {
                    "inputText": "A" * 100,
                    "selectedModel": {
                        "identifier": "copilot/gpt-5.3-codex",
                        "metadata": {
                            "id": "gpt-5.3-codex",
                            "name": "GPT-5.3-Codex",
                        },
                    },
                }
            ]
        )
        sessions = list(parse_vscode_sessions(db_path))

        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertIsNone(session.total_ai_credits)
        self.assertIsNone(session.total_nano_aiu)
        self.assertIsNone(session.models[0].ai_credits)
        self.assertIsNone(session.models[0].total_nano_aiu)

    def test_parse_vscode_sessions_skips_empty_sessions(self) -> None:
        from tokentracker.vscode_importer import parse_vscode_sessions

        db_path = self._create_mock_vscode_db(
            sessions={
                "empty-session": {
                    "sessionId": "empty-session",
                    "title": "",
                    "lastMessageDate": 1772920800000,
                    "isEmpty": True,
                    "initialLocation": "panel",
                }
            }
        )
        sessions = list(parse_vscode_sessions(db_path))
        self.assertEqual(len(sessions), 0)

    def test_parse_vscode_sessions_weights_allocation_by_duration(self) -> None:
        from tokentracker.vscode_importer import parse_vscode_sessions

        sessions = {
            "short-session": {
                "sessionId": "short-session",
                "title": "Short",
                "lastMessageDate": 1772920800000,
                "isImported": False,
                "initialLocation": "panel",
                "isEmpty": False,
                "timing": {
                    "startTime": 1772920790000,
                    "endTime": 1772920800000,
                },
            },
            "long-session": {
                "sessionId": "long-session",
                "title": "Long",
                "lastMessageDate": 1772920800000,
                "isImported": False,
                "initialLocation": "panel",
                "isEmpty": False,
                "timing": {
                    "startTime": 1772920200000,
                    "endTime": 1772920800000,
                },
            },
        }
        history_entries = [
            {
                "inputText": "x" * 400,
                "selectedModel": {
                    "identifier": "copilot/gpt-5.3-codex",
                    "metadata": {
                        "id": "gpt-5.3-codex",
                        "name": "GPT-5.3-Codex",
                        "inputCost": 175,
                        "outputCost": 1400,
                        "cacheCost": 17,
                    },
                },
            }
        ]

        db_path = self._create_mock_vscode_db(sessions=sessions, history_entries=history_entries)
        parsed = list(parse_vscode_sessions(db_path))
        self.assertEqual(len(parsed), 2)

        by_id = {s.session_id: s for s in parsed}
        short = by_id["vscode-chat:short-session"]
        long = by_id["vscode-chat:long-session"]

        self.assertGreater(long.total_tokens, short.total_tokens)
        self.assertGreater(long.total_requests, short.total_requests)

        total_tokens = short.total_tokens + long.total_tokens
        total_requests = short.total_requests + long.total_requests
        self.assertEqual(total_tokens, int(400 * 0.25) + int(int(400 * 0.25) * 2))
        self.assertEqual(total_requests, 1)

    def test_sync_imports_vscode_sessions_into_database(self) -> None:
        # Set up CLI fixture
        cli_session_dir = self.copilot_home / "session-state" / "cli-session-1"
        cli_session_dir.mkdir(parents=True, exist_ok=True)
        repo_root = Path(__file__).resolve().parent
        shutil.copy(
            repo_root / "fixtures" / "completed-session" / "events.jsonl",
            cli_session_dir / "events.jsonl",
        )

        # Set up VS Code fixture
        self._create_mock_vscode_db()

        result = sync_sessions(
            copilot_home=self.copilot_home,
            data_dir=self.data_dir,
            regenerate_dashboard=True,
            sources={"cli", "vscode"},
            vscode_storage_root=self.vscode_storage,
        )

        self.assertEqual(result.imported_sessions, 1)
        self.assertEqual(result.vscode_imported, 1)

        with sqlite3.connect(self.data_dir / "token-tracker.db") as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT session_id, source, is_estimated FROM sessions ORDER BY source"
            ).fetchall()
            sources = {row["source"] for row in rows}
            self.assertIn("cli", sources)
            self.assertIn("vscode-chat", sources)

            vscode_row = next(r for r in rows if r["source"] == "vscode-chat")
            self.assertEqual(vscode_row["is_estimated"], 1)

            cli_row = next(r for r in rows if r["source"] == "cli")
            self.assertEqual(cli_row["is_estimated"], 0)

    def test_sync_vscode_only_source(self) -> None:
        self._create_mock_vscode_db()

        result = sync_sessions(
            copilot_home=self.copilot_home,
            data_dir=self.data_dir,
            regenerate_dashboard=False,
            sources={"vscode"},
            vscode_storage_root=self.vscode_storage,
        )

        self.assertEqual(result.session_files_seen, 0)
        self.assertEqual(result.vscode_imported, 1)

    def test_sync_vscode_is_idempotent(self) -> None:
        self._create_mock_vscode_db()

        first = sync_sessions(
            copilot_home=self.copilot_home,
            data_dir=self.data_dir,
            regenerate_dashboard=False,
            sources={"vscode"},
            vscode_storage_root=self.vscode_storage,
        )
        second = sync_sessions(
            copilot_home=self.copilot_home,
            data_dir=self.data_dir,
            regenerate_dashboard=False,
            sources={"vscode"},
            vscode_storage_root=self.vscode_storage,
        )

        self.assertEqual(first.vscode_imported, 1)
        self.assertEqual(second.vscode_imported, 0)
        self.assertEqual(second.vscode_skipped, 1)

    def test_source_breakdown_query_returns_multiple_sources(self) -> None:
        from tokentracker.storage import connect, fetch_source_breakdown

        # Set up CLI fixture
        cli_session_dir = self.copilot_home / "session-state" / "cli-session-1"
        cli_session_dir.mkdir(parents=True, exist_ok=True)
        repo_root = Path(__file__).resolve().parent
        shutil.copy(
            repo_root / "fixtures" / "completed-session" / "events.jsonl",
            cli_session_dir / "events.jsonl",
        )

        # Set up VS Code fixture
        self._create_mock_vscode_db()

        sync_sessions(
            copilot_home=self.copilot_home,
            data_dir=self.data_dir,
            regenerate_dashboard=False,
            sources={"cli", "vscode"},
            vscode_storage_root=self.vscode_storage,
        )

        with connect(self.data_dir / "token-tracker.db") as conn:
            rows = fetch_source_breakdown(conn)
            sources = {row["source"] for row in rows}
            self.assertIn("cli", sources)
            self.assertIn("vscode-chat", sources)


if __name__ == "__main__":
    unittest.main()
