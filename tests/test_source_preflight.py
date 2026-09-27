import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import source_preflight


class SourcePreflightTests(unittest.TestCase):
    def test_report_redacts_token_and_records_scope_states(self):
        def fake_run(command, **kwargs):
            scope = command[command.index("--scope") + 1]
            ok = scope == source_preflight.SCOPES["minutes"]
            payload = {"ok": ok, "missing": None if ok else [scope]}
            return type("Completed", (), {"stdout": json.dumps(payload), "returncode": 0})()

        with mock.patch.object(source_preflight.importlib.util, "find_spec", return_value=object()), mock.patch.object(
            source_preflight.subprocess, "run", side_effect=fake_run
        ), mock.patch.dict(os.environ, {"BAIDU_NETDISK_ACCESS_TOKEN": "secret-token"}):
            report = source_preflight.build_report("lark-cli")

        self.assertEqual(report["baidu"]["access_token"], "configured")
        self.assertNotIn("secret-token", json.dumps(report))
        self.assertEqual(report["feishu"]["scopes"]["minutes"]["status"], "granted")
        self.assertEqual(report["feishu"]["scopes"]["docs"]["status"], "missing")

    def test_required_remote_gate_fails_closed(self):
        report = {
            "python": {"status": "ok"},
            "baidu": {"mcp_client": "installed", "access_token": "missing"},
            "feishu": {"scopes": {"docs": {"status": "missing"}, "docx": {"status": "granted"}}},
        }
        self.assertTrue(not source_preflight.required_gates_ready(report, True, False))
        self.assertTrue(not source_preflight.required_gates_ready(report, False, True))
        self.assertTrue(source_preflight.required_gates_ready(report, False, False))

if __name__ == "__main__":
    unittest.main()
