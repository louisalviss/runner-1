"""Self-contained scheduled-smoothness guard regression; no market requests."""
import ast
from datetime import date, datetime, timezone
import os
from pathlib import Path
from typing import Any
from unittest import TestCase, main
from unittest.mock import patch
from zoneinfo import ZoneInfo


def run_original_function(session, event_name="schedule"):
    source = Path(__file__).resolve().parents[1] / "stock_runner.py"
    tree = ast.parse(source.read_text())
    fun = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_smoothness")
    invoked, diagnostics = [], []
    def fetch_all(rows):
        invoked.append(rows)
        raise RuntimeError("FETCH_INVOKED_TEST_MARKER")
    def read_slot(key):
        return {
            "1": {"finalized": True, "finalized_by_mode": "preclose",
                  "session_date_us": session,
                  "rows": [{"symbol": "AAPL", "exchange": "NASDAQ"}]},
            "2": None,
        }.get(key)
    context = {
        "os": os, "date": date, "Any": Any, "NY": ZoneInfo("America/New_York"),
        "now_utc": lambda: datetime(2026, 10, 12, 1, tzinfo=timezone.utc),
        "read_slot": read_slot, "write_diag": lambda name, body: diagnostics.append((name,body)),
        "fetch_all": fetch_all,
    }
    exec(compile(ast.Module(body=[fun], type_ignores=[]), str(source), "exec"), context)
    with patch.dict(os.environ, {"EVENT_NAME": event_name}):
        try:
            context["run_smoothness"]()
            outcome = "RETURNED"
        except RuntimeError as ex:
            outcome = str(ex)
    return outcome, invoked, diagnostics


class FreshnessTest(TestCase):
    def test_old_scheduled_session_blocked_before_network(self):
        result, invoked, diags = run_original_function("2026-08-21")
        self.assertIn("refresh Stage-1", result)
        self.assertEqual(invoked, [])
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0][1]["status"], "blocked_stale_stage1")
        self.assertGreater(diags[0][1]["session_age_days"], 7)

    def test_recent_scheduled_session_fetch_allowed(self):
        result, invoked, _ = run_original_function("2026-10-09")
        self.assertEqual(result, "FETCH_INVOKED_TEST_MARKER")
        self.assertEqual(len(invoked), 1)

    def test_manual_replay_old_session_allowed(self):
        result, invoked, _ = run_original_function("2026-08-21", event_name="workflow_dispatch")
        self.assertEqual(result, "FETCH_INVOKED_TEST_MARKER")
        self.assertEqual(len(invoked), 1)

    def test_future_session_blocked(self):
        result, invoked, _ = run_original_function("2026-10-20")
        self.assertIn("smoothness blocked", result)
        self.assertEqual(invoked, [])

    def test_invalid_session_blocked(self):
        result, invoked, _ = run_original_function("invalid")
        self.assertIn("invalid Stage-1 session", result)
        self.assertEqual(invoked, [])


if __name__ == "__main__":
    main()
