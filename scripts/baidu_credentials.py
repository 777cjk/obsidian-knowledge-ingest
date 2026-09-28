"""Resolve a Baidu Netdisk access token from the environment or macOS Keychain."""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys


KEYCHAIN_SERVICE = "obsidian-knowledge-ingest.baidu-netdisk"


class CredentialError(Exception):
    pass


def _security() -> str:
    executable = shutil.which("security")
    if sys.platform != "darwin" or not executable:
        raise CredentialError("Baidu token is missing; configure BAIDU_NETDISK_ACCESS_TOKEN")
    return executable


def get_access_token() -> str:
    token = os.environ.get("BAIDU_NETDISK_ACCESS_TOKEN", "").strip()
    if token:
        return token
    try:
        result = subprocess.run(
            [
                _security(), "find-generic-password", "-a", getpass.getuser(),
                "-s", KEYCHAIN_SERVICE, "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise CredentialError("Baidu token is unavailable in macOS Keychain") from None
    token = result.stdout.strip() if result.returncode == 0 else ""
    if not token:
        raise CredentialError("Baidu token is missing; store it in macOS Keychain or set BAIDU_NETDISK_ACCESS_TOKEN")
    return token


def is_configured() -> bool:
    if os.environ.get("BAIDU_NETDISK_ACCESS_TOKEN", "").strip():
        return True
    try:
        result = subprocess.run(
            [
                _security(), "find-generic-password", "-a", getpass.getuser(),
                "-s", KEYCHAIN_SERVICE,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return result.returncode == 0
    except (CredentialError, OSError, subprocess.SubprocessError):
        return False
