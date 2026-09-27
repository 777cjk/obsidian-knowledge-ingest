#!/usr/bin/env python3
"""Check local connector prerequisites without fetching source content."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from typing import Any
import subprocess
import sys


SCOPES = {
    "minutes": "minutes:minutes.artifacts:read",
    "docs": "search:docs:read",
    "docx": "docx:document:readonly",
}


def _scope_check(cli: str, scope: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [cli, "auth", "check", "--scope", scope, "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return {"status": "unavailable"}
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}

    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError):
        return {"status": "error"}
    if payload.get("ok") is True:
        return {"status": "granted"}
    if isinstance(payload.get("missing"), list) and scope in payload["missing"]:
        return {"status": "missing"}
    return {"status": "error"}


def build_report(cli: str) -> dict[str, Any]:
    scopes = {name: _scope_check(cli, scope) for name, scope in SCOPES.items()}
    return {
        "python": {
            "status": "ok" if sys.version_info >= (3, 9) else "unsupported",
            "version": ".".join(str(part) for part in sys.version_info[:3]),
        },
        "baidu": {
            "mcp_client": "installed" if importlib.util.find_spec("mcp") is not None else "missing",
            "access_token": "configured" if os.environ.get("BAIDU_NETDISK_ACCESS_TOKEN") else "missing",
        },
        "feishu": {"cli": cli, "scopes": scopes},
    }


def required_gates_ready(report: dict[str, Any], require_baidu: bool, require_docs: bool) -> bool:
    if report["python"]["status"] != "ok":
        return False
    if require_baidu and (
        report["baidu"]["mcp_client"] != "installed"
        or report["baidu"]["access_token"] != "configured"
    ):
        return False
    if require_docs and any(
        report["feishu"]["scopes"][name]["status"] != "granted"
        for name in ("docs", "docx")
    ):
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lark-cli", default="lark-cli")
    parser.add_argument("--require-baidu", action="store_true")
    parser.add_argument("--require-feishu-docs", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    report = build_report(args.lark_cli)
    required = args.require_baidu or args.require_feishu_docs
    report["status"] = (
        "ready" if required and required_gates_ready(report, args.require_baidu, args.require_feishu_docs)
        else "blocked" if required
        else "local_ready" if report["python"]["status"] == "ok"
        else "blocked"
    )
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"status: {report['status']}")
        print(f"python: {report['python']['version']} ({report['python']['status']})")
        print(f"baidu: mcp={report['baidu']['mcp_client']}, token={report['baidu']['access_token']}")
        for name, result in report["feishu"]["scopes"].items():
            print(f"feishu {name}: {result['status']}")
    return 0 if report["status"] in {"local_ready", "ready"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
