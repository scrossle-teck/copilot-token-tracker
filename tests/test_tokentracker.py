from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tokentracker import __version__
from tokentracker.cli import handle_doctor, handle_summary, main, sync_sessions
from tokentracker.dashboard import project_dashboard_path
from tokentracker.pricing import (
    DEFAULT_USD_PER_PREMIUM_REQUEST,
    PUBLIC_MODEL_PRICING,
    ensure_pricing_file,
    estimate_model_currency_amount,
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
    ) -> None:
        session_dir = self.session_state_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
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
                                "startTime": "2026-03-07T22:00:00.000Z",
                                "selectedModel": model_name,
                                "context": {
                                    "cwd": "C:\\Projects\\Deep\\Nested\\Example\\Workspace\\With\\Long\\Paths",
                                    "gitRoot": "C:\\Projects\\Deep\\Nested\\Example\\Workspace\\With\\Long\\Paths",
                                    "branch": "main",
                                    "repository": repository,
                                },
                            },
                            "id": f"start-{session_id}",
                            "timestamp": "2026-03-07T22:00:00.010Z",
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
                                "sessionStartTime": 1772920800000,
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
                            "timestamp": "2026-03-07T22:01:00.000Z",
                            "parentId": f"start-{session_id}",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def test_sync_imports_completed_sessions_and_ignores_incomplete_ones(self) -> None:
        result = sync_sessions(self.copilot_home, self.data_dir, regenerate_dashboard=True)

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
                    duration_seconds
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
        first = sync_sessions(self.copilot_home, self.data_dir, regenerate_dashboard=False)
        second = sync_sessions(self.copilot_home, self.data_dir, regenerate_dashboard=False)

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
        sync_sessions(self.copilot_home, self.data_dir, regenerate_dashboard=True)

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

        self.assertEqual(upgraded["usdPerPremiumRequest"], DEFAULT_USD_PER_PREMIUM_REQUEST)
        self.assertEqual(upgraded["models"]["gpt-5.4"], PUBLIC_MODEL_PRICING["gpt-5.4"])

    def test_dashboard_html_includes_history_note_and_layout_guards(self) -> None:
        long_repo = "octo/" + ("very-long-repository-name-" * 6).rstrip("-")
        self._write_completed_session("fixture-session-long", long_repo)

        sync_sessions(self.copilot_home, self.data_dir, regenerate_dashboard=True)
        html = (self.data_dir / "dashboard.html").read_text(encoding="utf-8")
        project_page = project_dashboard_path(self.data_dir / "dashboard.html", long_repo)
        project_html = project_page.read_text(encoding="utf-8")

        self.assertIn("Versions before <code>0.0.422</code>", html)
        self.assertIn("class=\"table-wrap\"", html)
        self.assertIn("overflow-wrap: anywhere;", html)
        self.assertIn("class=\"wrap-cell\"", html)
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
                    },
                )()
            )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Scope: octo/demo", output)
        self.assertIn("Sessions: 1", output)
        self.assertIn("Tokens: 1,610", output)


if __name__ == "__main__":
    unittest.main()
