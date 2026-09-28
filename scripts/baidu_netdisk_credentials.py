#!/usr/bin/env python3
"""Store the Baidu token through the native macOS Keychain password prompt."""

from __future__ import annotations

import getpass
import shutil
import subprocess
import sys

from baidu_credentials import KEYCHAIN_SERVICE


def main() -> int:
    if sys.platform != "darwin":
        print("macOS Keychain storage is available only on macOS", file=sys.stderr)
        return 2
    security = shutil.which("security")
    if not security:
        print("macOS security command is unavailable", file=sys.stderr)
        return 2
    print("Enter the Baidu OAuth access token in the macOS Keychain prompt; it will not be echoed here.")
    result = subprocess.run(
        [
            security, "add-generic-password", "-U", "-a", getpass.getuser(),
            "-s", KEYCHAIN_SERVICE, "-l",
            "Obsidian Knowledge Ingest - Baidu Netdisk", "-w",
        ],
        check=False,
    )
    if result.returncode != 0:
        print("Keychain storage did not complete", file=sys.stderr)
        return 2
    print("Baidu token stored in macOS Keychain; value omitted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
