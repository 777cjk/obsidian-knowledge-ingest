#!/usr/bin/env python3
"""Import one explicitly selected Feishu Minutes transcript into local staging."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import shutil
from typing import Any
from urllib.parse import urlparse, urlunparse

try:
    import fcntl
except ImportError:  # pragma: no cover - target deployments are macOS/Linux
    fcntl = None

import parser_adapter


READ_SCOPE = "minutes:minutes.artifacts:read"
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")


class IngestError(Exception):
    """A sanitized failure safe to show without echoing CLI output or credentials."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_private_dir(path: Path) -> None:
    """Create a private directory or fail closed on an existing unsafe one."""
    if path.is_symlink():
        raise IngestError("staging directories must not be symbolic links")
    try:
        path.lstat()
    except FileNotFoundError:
        missing: list[Path] = []
        current = path
        while True:
            try:
                current.lstat()
                break
            except FileNotFoundError:
                missing.append(current)
                if current.parent == current:
                    raise IngestError("could not create staging directory")
                current = current.parent
        for item in reversed(missing):
            try:
                item.mkdir(mode=0o700)
            except FileExistsError:
                pass
            _validate_private_dir(item)
        return
    _validate_private_dir(path)


def _validate_private_dir(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise IngestError("staging directory is unavailable") from None
    if not stat.S_ISDIR(info.st_mode):
        raise IngestError("staging path is not a real directory")
    if info.st_uid != os.geteuid():
        raise IngestError("staging directory is not owned by the current user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise IngestError("staging directory permissions must be 0700")


@contextmanager
def _staging_lock(staging: Path):
    """Serialize imports using an advisory lock supported by macOS and Linux."""
    if fcntl is None or os.name != "posix":
        raise IngestError("cross-process staging locks require macOS or Linux")
    lock_path = staging / ".feishu-minutes-ingest.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError:
        raise IngestError("could not open the staging lock") from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise IngestError("staging lock must be a user-owned 0600 file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError:
            raise IngestError("could not acquire the staging lock") from None
        yield
    except IngestError:
        raise
    except OSError:
        raise IngestError("could not inspect the staging lock") from None
    finally:
        os.close(descriptor)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _load_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise IngestError("manifest must not be a symbolic link")
    if not path.exists():
        return {"manifest_version": 1, "generated_at": None, "entries": [], "history": []}
    try:
        with path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError):
        raise IngestError("manifest is unreadable or invalid") from None
    if (
        not isinstance(manifest, dict)
        or manifest.get("manifest_version") != 1
        or not isinstance(manifest.get("entries"), list)
        or not isinstance(manifest.get("history", []), list)
    ):
        raise IngestError("unsupported manifest schema")
    manifest.setdefault("history", [])
    return manifest


def _run_cli(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    options: dict[str, Any] = {
        "cwd": str(cwd),
        "capture_output": True,
        "text": True,
        "check": False,
    }
    if os.name == "posix":
        options["umask"] = 0o077
    try:
        return subprocess.run(command, **options)
    except OSError:
        raise IngestError("could not start lark-cli") from None


def _json_success(result: subprocess.CompletedProcess[str], failure: str) -> dict[str, Any]:
    if result.returncode != 0:
        raise IngestError(failure)
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        raise IngestError("lark-cli returned invalid JSON") from None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise IngestError(failure)
    return payload


def _minute_record(payload: dict[str, Any], minute_token: str) -> dict[str, Any]:
    data = payload.get("data", payload)
    minutes = data.get("minutes") if isinstance(data, dict) else None
    if not isinstance(minutes, list) or len(minutes) != 1 or not isinstance(minutes[0], dict):
        raise IngestError("lark-cli did not return exactly one minute")
    record = minutes[0]
    if record.get("minute_token") != minute_token:
        raise IngestError("lark-cli returned a different minute")
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(artifacts.get("transcript_file"), str):
        raise IngestError("lark-cli did not return a transcript file")
    return record


def _trusted_url(record: dict[str, Any], minute_token: str) -> tuple[str, str]:
    for key in ("url", "minute_url", "share_url", "web_url"):
        value = record.get(key)
        if not isinstance(value, str):
            continue
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme == "https"
            and (host == "feishu.cn" or host.endswith(".feishu.cn") or host == "larksuite.com" or host.endswith(".larksuite.com"))
            and parsed.username is None
            and parsed.password is None
        ):
            sanitized = parsed._replace(query="", fragment="")
            return urlunparse(sanitized), "cli_response"
    return f"https://www.feishu.cn/minutes/{minute_token}", "constructed_locator"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _checked_transcript_path(raw_path: str, staging: Path, source_dir: Path) -> Path:
    reported = Path(raw_path)
    candidate = reported if reported.is_absolute() else staging / reported
    if candidate.is_symlink():
        raise IngestError("transcript path must not be a symbolic link")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise IngestError("transcript file is missing") from None
    if not _inside(resolved, source_dir) or not resolved.is_file():
        raise IngestError("transcript file is outside the staging source directory")
    try:
        resolved.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        raise IngestError("could not protect transcript file permissions") from None
    return resolved


def _candidate_name(source_id: str, revision_id: str) -> str:
    key = f"{source_id}\0{revision_id}".encode("utf-8")
    return hashlib.sha256(key).hexdigest()[:20] + ".md"


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _candidate_markdown(entry: dict[str, Any], parsed_text: str) -> str:
    title = str(entry["title"]).replace("\r", " ").replace("\n", " ").strip() or "Feishu Minutes transcript"
    body = parsed_text.rstrip()
    return (
        "---\n"
        f"title: {_yaml_string(title)}\n"
        "type: knowledge_candidate\n"
        "knowledge_type: candidate\n"
        "source_refs:\n"
        f"  - {_yaml_string(entry['source_id'])}\n"
        f"source_revision: {_yaml_string(entry['revision_id'])}\n"
        f"url: {_yaml_string(entry['source_url'])}\n"
        f"url_provenance: {_yaml_string(entry['url_provenance'])}\n"
        "source_content_trust: untrusted_data\n"
        "review_status: unreviewed\n"
        "classification_status: pending\n"
        "evidence_status: source_transcript\n"
        "privacy: user_read\n"
        "retrieval_status: content_retrieved\n"
        f"content_sha256: {_yaml_string(entry['content_sha256'])}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"Source: <{entry['source_url']}>\n\n"
        f"<!-- source url provenance: {entry['url_provenance']} -->\n\n"
        "## Transcript\n\n"
        f"{body}\n"
    )


def _ensure_candidate(path: Path, content: str, preserve_existing: bool = False) -> bool:
    if path.is_symlink():
        raise IngestError("candidate path must not be a symbolic link")
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise IngestError("existing candidate is unreadable") from None
        if existing != content:
            if preserve_existing:
                return False
            raise IngestError("candidate for this revision already exists and was preserved")
        return False
    _atomic_write(path, content)
    return True


def _find_prior(manifest: dict[str, Any], source_id: str) -> dict[str, Any] | None:
    matches = [
        entry for entry in manifest["entries"]
        if isinstance(entry, dict) and entry.get("source_id") == source_id and entry.get("provider") == "feishu_minutes"
    ]
    if len(matches) > 1:
        raise IngestError("manifest has duplicate Feishu source entries")
    return matches[0] if matches else None


def _archive_prior_transcript(staging: Path, source_dir: Path, prior: dict[str, Any]) -> tuple[Path, Path, str] | None:
    relative = prior.get("transcript_file")
    expected_hash = prior.get("content_sha256")
    if not isinstance(relative, str) or not isinstance(expected_hash, str):
        return None
    original = (staging / relative).resolve()
    if not _inside(original, source_dir) or not original.is_file():
        return None
    actual_hash = sha256_file(original)
    if actual_hash != expected_hash:
        raise IngestError("previous transcript changed outside the importer; refusing to replace it")
    descriptor, backup_name = tempfile.mkstemp(prefix=".refresh-", dir=str(source_dir))
    os.close(descriptor)
    backup = Path(backup_name)
    backup.unlink()
    try:
        os.replace(original, backup)
    except OSError:
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            pass
        raise IngestError("could not preserve the previous transcript") from None
    return original, backup, actual_hash


def _preserve_failed_file(path: Path, source_dir: Path) -> None:
    if not (path.exists() or path.is_symlink()):
        return
    failed_dir = source_dir / "failed-refresh"
    ensure_private_dir(failed_dir)
    descriptor, failed_name = tempfile.mkstemp(prefix="transcript-", dir=str(failed_dir))
    os.close(descriptor)
    failed = Path(failed_name)
    failed.unlink()
    os.replace(path, failed)


def _restore_backup(backup: tuple[Path, Path, str] | None, source_dir: Path) -> None:
    if backup is None:
        return
    original, saved, _ = backup
    if not saved.exists():
        return
    try:
        _preserve_failed_file(original, source_dir)
        original.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.replace(saved, original)
    except OSError:
        recovery_dir = source_dir / "recovery"
        try:
            ensure_private_dir(recovery_dir)
            recovery = recovery_dir / f"{saved.name}.txt"
            os.replace(saved, recovery)
        except OSError:
            raise IngestError("could not restore or recover the previous transcript") from None
        raise IngestError("previous transcript preserved in staging recovery") from None


def _finish_backup(
    staging: Path,
    source_dir: Path,
    backup: tuple[Path, Path, str] | None,
    current_path: Path,
    current_hash: str,
    prior: dict[str, Any] | None,
) -> str | None:
    """Restore an unchanged source, or retain the prior bytes under .source/history."""
    if backup is None:
        return None
    original, saved, old_hash = backup
    try:
        if current_hash == old_hash:
            if current_path == original:
                current_path.unlink()
            _restore_backup(backup, source_dir)
            return None

        source_key = hashlib.sha256(str(prior.get("source_id", "")).encode("utf-8")).hexdigest()[:20]
        history_dir = source_dir / "history" / source_key
        ensure_private_dir(history_dir)
        version = int(prior.get("version", 1))
        destination = history_dir / f"v{version}-{old_hash}.txt"
        if destination.exists():
            if sha256_file(destination) != old_hash:
                raise IngestError("source history path exists with different content")
            destination = history_dir / f"v{version}-{old_hash}-{saved.name.removeprefix('.refresh-')}.txt"
        os.replace(saved, destination)
        return destination.relative_to(staging).as_posix()
    except Exception:
        _restore_backup(backup, source_dir)
        raise


def _rollback_archive(
    staging: Path,
    source_dir: Path,
    backup: tuple[Path, Path, str] | None,
    archived_path: str | None,
    current_path: Path,
) -> None:
    if backup is None:
        return
    original, saved, _ = backup
    archived = staging / archived_path if archived_path else saved
    if archived.exists():
        _preserve_failed_file(current_path, source_dir)
        original.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.replace(archived, original)
    elif saved.exists():
        _restore_backup(backup, source_dir)


def _ingest_locked(minute_token: str, staging: Path, lark_cli: str) -> dict[str, Any]:
    source_dir = staging / ".source"
    candidates_dir = staging / "candidates"
    ensure_private_dir(source_dir)
    ensure_private_dir(candidates_dir)
    manifest_path = staging / "manifest.json"
    manifest = _load_manifest(manifest_path)
    source_id = f"feishu_minutes:{minute_token}"
    prior = _find_prior(manifest, source_id)

    auth_command = [lark_cli, "auth", "check", "--scope", READ_SCOPE, "--json"]
    auth_payload = _json_success(_run_cli(auth_command, staging), "Feishu minutes read scope check failed")
    del auth_payload

    backup = _archive_prior_transcript(staging, source_dir, prior) if prior else None
    download_dir = Path(tempfile.mkdtemp(prefix=".minutes-fetch-", dir=str(source_dir)))
    minutes_command = [
        lark_cli,
        "minutes",
        "+detail",
        "--as",
        "user",
        "--minute-tokens",
        minute_token,
        "--transcript",
        "--output-dir",
        ".",
        "--json",
    ]
    try:
        payload = _json_success(_run_cli(minutes_command, download_dir), "Feishu transcript retrieval failed")
        record = _minute_record(payload, minute_token)
        downloaded_path = _checked_transcript_path(
            record["artifacts"]["transcript_file"], download_dir, download_dir
        )
        relative_download = downloaded_path.relative_to(download_dir)
        target_path = source_dir / relative_download
        ensure_private_dir(target_path.parent)
        if target_path.exists() or target_path.is_symlink():
            raise IngestError("transcript destination already exists")
        os.replace(downloaded_path, target_path)
        transcript_path = target_path
        transcript_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        parsed = parser_adapter.parse(transcript_path, backend="auto")
        if parsed.get("status") != "ok" or not isinstance(parsed.get("text"), str):
            raise IngestError("transcript could not be parsed")
        if not parsed["text"].strip():
            raise IngestError("transcript contains no readable text")
        digest = sha256_file(transcript_path)
        parser_digest = parsed.get("source", {}).get("sha256")
        if parser_digest is not None and parser_digest != digest:
            raise IngestError("transcript changed while it was being parsed")
    except Exception:
        _restore_backup(backup, source_dir)
        shutil.rmtree(download_dir, ignore_errors=True)
        raise

    shutil.rmtree(download_dir, ignore_errors=True)

    if prior and prior.get("content_sha256") == digest:
        _finish_backup(staging, source_dir, backup, transcript_path, digest, prior)
        candidate_path = candidates_dir / _candidate_name(source_id, str(prior["revision_id"]))
        _ensure_candidate(candidate_path, _candidate_markdown(prior, parsed["text"]), preserve_existing=True)
        return {
            "status": "unchanged",
            "provider": "feishu_minutes",
            "version": int(prior.get("version", 1)),
            "content_sha256": digest,
            "manifest": "manifest.json",
            "candidate": candidate_path.relative_to(staging).as_posix(),
        }

    url, url_provenance = _trusted_url(record, minute_token)
    version = int(prior.get("version", 1)) + 1 if prior else 1
    revision_id = f"{source_id}@v{version}"
    title = record.get("title") if isinstance(record.get("title"), str) else "Feishu Minutes transcript"
    relative_transcript = transcript_path.relative_to(staging).as_posix()
    entry: dict[str, Any] = {
        "source_id": source_id,
        "revision_id": revision_id,
        "provider": "feishu_minutes",
        "locator": url,
        "source_url": url,
        "url_provenance": url_provenance,
        "title": title,
        "media_type": "text/plain",
        "size_bytes": transcript_path.stat().st_size,
        "transcript_file": relative_transcript,
        "content_sha256": digest,
        "permissions": "user_read",
        "retrieval_status": "content_retrieved",
        "retrieved_at": utc_now(),
        "version": version,
        "supersedes": prior.get("revision_id") if prior else None,
        "duplicate_of": None,
        "classification_status": "pending",
        "classification_hint": "meeting_transcript",
        "parser": parsed.get("parser", {}),
    }
    candidate_path = candidates_dir / _candidate_name(source_id, revision_id)
    candidate_text = _candidate_markdown(entry, parsed["text"])
    try:
        candidate_created = _ensure_candidate(candidate_path, candidate_text)
    except Exception:
        _restore_backup(backup, source_dir)
        raise
    try:
        archived_path = _finish_backup(staging, source_dir, backup, transcript_path, digest, prior)
    except Exception:
        if candidate_created:
            candidate_path.unlink(missing_ok=True)
        raise

    if prior:
        prior_snapshot = dict(prior)
        prior_snapshot["record_status"] = "superseded"
        if archived_path:
            prior_snapshot["transcript_file"] = archived_path
        manifest["history"].append(prior_snapshot)
        manifest["entries"] = [
            existing for existing in manifest["entries"]
            if not (isinstance(existing, dict) and existing.get("source_id") == source_id and existing.get("provider") == "feishu_minutes")
        ]
    manifest["entries"].append(entry)
    manifest["manifest_version"] = 1
    manifest["generated_at"] = utc_now()
    manifest["root_label"] = manifest.get("root_label", "feishu_minutes")
    try:
        _write_json_atomic(manifest_path, manifest)
    except Exception:
        if candidate_created:
            candidate_path.unlink(missing_ok=True)
        _rollback_archive(staging, source_dir, backup, archived_path, transcript_path)
        raise IngestError("could not write the source manifest") from None

    return {
        "status": "imported" if prior is None else "updated",
        "provider": "feishu_minutes",
        "version": version,
        "content_sha256": digest,
        "manifest": "manifest.json",
        "candidate": candidate_path.relative_to(staging).as_posix(),
    }


def ingest(minute_token: str, staging_dir: str | Path, lark_cli: str = "lark-cli") -> dict[str, Any]:
    if not TOKEN_RE.fullmatch(minute_token):
        raise IngestError("minute token must identify exactly one Feishu minute")

    requested_stage = Path(staging_dir).expanduser().absolute()
    ensure_private_dir(requested_stage)
    staging = requested_stage.resolve()
    with _staging_lock(staging):
        return _ingest_locked(minute_token, staging, lark_cli)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minute-token", required=True)
    parser.add_argument("--staging-dir", required=True)
    parser.add_argument("--lark-cli", default="lark-cli")
    args = parser.parse_args()
    try:
        result = ingest(args.minute_token, args.staging_dir, args.lark_cli)
    except IngestError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except OSError:
        print(json.dumps({"status": "error", "error": "local staging operation failed"}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
