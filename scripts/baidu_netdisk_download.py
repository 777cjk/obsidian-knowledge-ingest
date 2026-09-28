#!/usr/bin/env python3
"""Download explicitly selected Baidu Netdisk files and stage parsed candidates."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Iterable, Mapping
from urllib.parse import quote
from urllib.request import Request, urlopen

from baidu_credentials import CredentialError, get_access_token
from baidu_netdisk_ingest import IngestError, fetch_records, ensure_private_dir
from parser_adapter import parse


DOWNLOAD_ENDPOINTS = (
    "https://d.pcs.baidu.com/rest/2.0/pcs/file?method=download&access_token={token}&path={path}",
    "https://pan.baidu.com/rest/2.0/xpan/file?method=download&access_token={token}&path={path}",
)
DEFAULT_MAX_BYTES = 200 * 1024 * 1024
HEX_MD5_RE = re.compile(r"[0-9a-fA-F]{32}\Z")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_filename(name: str, fallback: str) -> str:
    candidate = Path(name).name.replace("\x00", "").strip()
    if not candidate or candidate in {".", ".."}:
        candidate = fallback
    return candidate[:180]


def _atomic_copy_stream(response: Any, destination: Path, max_bytes: int) -> tuple[int, str, str | None]:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=str(destination.parent))
    digest = hashlib.sha256()
    size = 0
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise IngestError("download exceeds max-bytes limit")
                digest.update(chunk)
                handle.write(chunk)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    content_type = None
    headers = getattr(response, "headers", None)
    if headers is not None:
        content_type = headers.get("Content-Type")
    return size, digest.hexdigest(), content_type


def download_file(remote_path: str, destination: Path, access_token: str, max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    """Download one file through Baidu's documented PCS/XPan download endpoint."""
    if not remote_path.startswith("/") or "\x00" in remote_path:
        raise IngestError("Baidu file path must be one explicit absolute path")
    if not access_token:
        raise IngestError("BAIDU_NETDISK_ACCESS_TOKEN is not configured")
    last_error: Exception | None = None
    for endpoint in DOWNLOAD_ENDPOINTS:
        url = endpoint.format(token=quote(access_token, safe=""), path=quote(remote_path, safe=""))
        try:
            request = Request(url, headers={"User-Agent": "pan.baidu.com"})
            with urlopen(request, timeout=60) as response:
                size, sha256, content_type = _atomic_copy_stream(response, destination, max_bytes)
            return {
                "status": "downloaded",
                "remote_path": remote_path,
                "local_path": str(destination),
                "size_bytes": size,
                "sha256": sha256,
                "content_type": content_type,
            }
        except IngestError:
            raise
        except Exception as exc:
            last_error = exc
    if isinstance(last_error, IngestError):
        raise last_error
    raise IngestError("Baidu file download failed; check OAuth, path, and network") from None


def _source_id(record: Mapping[str, Any]) -> str:
    fsid = record.get("fsid")
    if isinstance(fsid, (int, str)) and str(fsid):
        return f"baidu_netdisk:{fsid}"
    path = record.get("path")
    if isinstance(path, str) and path.startswith("/"):
        return "baidu_netdisk:path:" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]
    raise IngestError("Baidu record has no stable source identifier")


def _is_temporary_office_file(filename: str) -> bool:
    return filename.startswith("~$") or (filename.startswith(".~lock.") and filename.endswith("#"))


def _candidate_markdown(record: Mapping[str, Any], download: Mapping[str, Any], parsed: Mapping[str, Any]) -> str:
    title = str(record.get("filename") or Path(str(record.get("path", ""))).name or "Baidu source")
    source_id = _source_id(record)
    text = parsed.get("text") if isinstance(parsed.get("text"), str) else ""
    frontmatter = {
        "title": title,
        "type": "knowledge_candidate",
        "knowledge_type": "candidate",
        "source_refs": [source_id],
        "source_locator": record.get("path", ""),
        "source_content_trust": "untrusted_data",
        "review_status": "unreviewed",
        "evidence_status": "verified_download_unreviewed_content",
        "privacy": "user_read",
        "retrieval_status": "downloaded_and_parsed",
        "content_completeness": "full_download_sha256_verified",
        "content_sha256": download["sha256"],
        "parser": parsed.get("parser", {}),
        "captured_at": utc_now(),
    }
    lines = ["---", *[f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items()], "---", "", f"# {title}", ""]
    lines.extend([text, ""])
    lines.extend(["## Provenance", "", f"- source: `{record.get('path', '')}`", f"- sha256: `{download['sha256']}`", f"- parser: `{parsed.get('parser', {}).get('name', 'unknown')}`", ""])
    return "\n".join(lines)


def _stage_one(record: Mapping[str, Any], stage: Path, access_token: str, backend: str, max_bytes: int) -> dict[str, Any]:
    source_id = _source_id(record)
    remote_path = str(record.get("path", ""))
    filename = _safe_filename(str(record.get("filename") or Path(remote_path).name), source_id.rsplit(":", 1)[-1])
    if _is_temporary_office_file(filename):
        return {
            "source_id": source_id,
            "remote_path": remote_path,
            "filename": filename,
            "status": "skipped",
            "skip_reason": "temporary_office_lock_file",
            "download": None,
            "parser": {},
            "parse_status": "skipped",
            "parsed_snapshot": None,
            "candidate": None,
        }
    raw_dir = stage / "raw"
    parsed_dir = stage / "parsed"
    candidate_dir = stage / "candidates"
    for directory in (raw_dir, parsed_dir, candidate_dir):
        ensure_private_dir(directory)
    raw_path = raw_dir / f"{source_id.rsplit(':', 1)[-1]}-{filename}"
    download = download_file(remote_path, raw_path, access_token, max_bytes=max_bytes)
    remote_md5 = str(record.get("md5") or "")
    if HEX_MD5_RE.fullmatch(remote_md5):
        import hashlib as _hashlib
        local_md5 = _hashlib.md5(raw_path.read_bytes()).hexdigest()
        if local_md5.lower() != remote_md5.lower():
            raise IngestError("downloaded file MD5 does not match Baidu metadata")
    parsed = parse(raw_path, backend=backend)
    parsed_path = parsed_dir / f"{source_id.rsplit(':', 1)[-1]}.json"
    parsed_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(parsed_path, 0o600)
    candidate_path = None
    if parsed.get("status") == "ok" and parsed.get("text"):
        candidate_path = candidate_dir / f"{source_id.rsplit(':', 1)[-1]}.md"
        candidate_path.write_text(_candidate_markdown(record, download, parsed), encoding="utf-8")
        os.chmod(candidate_path, 0o600)
    return {
        "source_id": source_id,
        "remote_path": remote_path,
        "filename": filename,
        "download": download,
        "parser": parsed.get("parser", {}),
        "parse_status": parsed.get("status"),
        "parsed_snapshot": str(parsed_path),
        "candidate": str(candidate_path) if candidate_path else None,
    }


def run(records: Iterable[Mapping[str, Any]], stage_dir: str | Path, access_token: str, backend: str, max_bytes: int) -> dict[str, Any]:
    stage = Path(stage_dir).expanduser().absolute()
    ensure_private_dir(stage)
    results = [_stage_one(record, stage, access_token, backend, max_bytes) for record in records]
    manifest = {
        "manifest_version": 1,
        "generated_at": utc_now(),
        "provider": "baidu_netdisk",
        "retrieval": "official_pcs_download",
        "entries": results,
    }
    manifest_path = stage / "download-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    return {
        "status": "ok",
        "downloaded": sum((item.get("download") or {}).get("status") == "downloaded" for item in results),
        "skipped": sum(item.get("status") == "skipped" for item in results),
        "parsed": sum(item["parse_status"] == "ok" for item in results),
        "candidates": sum(bool(item["candidate"]) for item in results),
        "manifest": str(manifest_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--path", help="one explicit absolute Baidu file path")
    group.add_argument("--dir", help="one explicit absolute Baidu directory; list via file_doc_list")
    parser.add_argument("--staging-dir", required=True)
    parser.add_argument("--backend", default="auto", choices=["auto", "macos-textutil", "markitdown", "docling", "liteparse", "text-fixture"])
    parser.add_argument("--max-files", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args()
    try:
        token = get_access_token()
        if args.path:
            records = [{"path": args.path, "filename": Path(args.path).name}]
        else:
            records, _ = __import__("asyncio").run(fetch_records(args.dir, "file_doc_list", args.max_pages, args.max_files))
        result = run(records, args.staging_dir, token, args.backend, args.max_bytes)
    except (CredentialError, IngestError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
