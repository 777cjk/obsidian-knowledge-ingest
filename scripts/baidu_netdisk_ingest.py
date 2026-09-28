#!/usr/bin/env python3
"""Import platform-generated Baidu Netdisk text into private review staging."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import datetime as dt
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Mapping
from urllib.parse import quote, urlencode, urlsplit

from baidu_credentials import CredentialError, get_access_token

try:
    import fcntl
except ImportError:  # pragma: no cover - target deployments are macOS/Linux
    fcntl = None


BAIDU_SSE_ENDPOINT = "https://mcp-pan.baidu.com/sse"
READ_ONLY_TOOLS = frozenset({"file_list", "file_doc_list"})
MAX_PAGE_LIMIT = 50
MAX_FILE_LIMIT = 1000
FSID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


class IngestError(Exception):
    """A sanitized failure safe to show without echoing server data or tokens."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_private_dir(path: Path) -> None:
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


@asynccontextmanager
async def _connect():
    try:
        token = get_access_token()
    except CredentialError as exc:
        raise IngestError(str(exc)) from None
    try:
        endpoint = urlsplit(BAIDU_SSE_ENDPOINT)
        server_url = endpoint._replace(query=urlencode({"access_token": token})).geturl()
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except ImportError:
        raise IngestError("optional MCP client is missing; install with --with baidu-mcp") from None

    try:
        async with AsyncExitStack() as stack:
            streams = await stack.enter_async_context(sse_client(server_url))
            session = await stack.enter_async_context(ClientSession(*streams))
            await session.initialize()
            yield session
    except IngestError:
        raise
    except Exception:
        raise IngestError("Baidu MCP connection failed; check OAuth, service, and network") from None


def _required_tool(tools: dict[str, Any], tool_name: str) -> Any:
    if tool_name not in READ_ONLY_TOOLS:
        raise IngestError("requested Baidu tool is not in the read-only allowlist")
    tool = tools.get(tool_name)
    if tool is None:
        raise IngestError("Baidu MCP does not expose the selected read-only tool")
    schema = getattr(tool, "inputSchema", None)
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = schema.get("required", []) if isinstance(schema, dict) else []
    if not isinstance(properties, dict) or "dir" not in properties:
        raise IngestError("Baidu MCP tool schema does not support a directory-scoped read")
    if any(key not in {"dir", "page"} for key in required):
        raise IngestError("Baidu MCP tool requires unsupported arguments")
    return tool


def _payload_from_tool_result(result: Any) -> Any:
    if isinstance(result, Mapping):
        is_error = result.get("isError", result.get("is_error", False))
        structured = result.get("structuredContent", result.get("structured_content"))
        content = result.get("content", [])
    else:
        is_error = getattr(result, "isError", False)
        structured = getattr(result, "structuredContent", None)
        content = getattr(result, "content", [])
    if is_error:
        raise IngestError("Baidu MCP read tool returned an error")
    if isinstance(structured, (dict, list)):
        return structured
    if isinstance(content, list):
        for block in content:
            text_value = block.get("text") if isinstance(block, Mapping) else getattr(block, "text", None)
            if not isinstance(text_value, str):
                continue
            try:
                decoded = json.loads(text_value)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, (dict, list)):
                return decoded
    if isinstance(result, (dict, list)):
        return result
    raise IngestError("Baidu MCP returned an unsupported response format")


def _records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload
    if isinstance(payload, dict):
        for key in ("list", "files", "items", "records"):
            value = payload.get(key)
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                return value
        for key in ("data", "result", "payload"):
            if key in payload:
                return _records_from_payload(payload[key])
        if "fsid" in payload or "path" in payload:
            return [payload]
    raise IngestError("Baidu MCP response does not contain a supported file list")


async def fetch_records(remote_path: str, tool_name: str, max_pages: int, max_files: int) -> tuple[list[dict[str, Any]], bool]:
    if tool_name not in READ_ONLY_TOOLS:
        raise IngestError("requested Baidu tool is not in the read-only allowlist")
    path_arg = quote(remote_path, safe="")
    collected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    previous_page_signature: tuple[str, ...] | None = None
    reached_limit = False

    async with _connect() as session:
        try:
            response = await session.list_tools()
        except Exception:
            raise IngestError("Baidu MCP tool discovery failed") from None
        catalog = getattr(response, "tools", None)
        if not isinstance(catalog, list):
            raise IngestError("Baidu MCP returned an unsupported tool catalog")
        tools = {getattr(tool, "name", ""): tool for tool in catalog}
        tool = _required_tool(tools, tool_name)
        properties = tool.inputSchema["properties"]
        page_limit = max_pages if "page" in properties else 1

        for page in range(1, page_limit + 1):
            arguments: dict[str, Any] = {"dir": path_arg}
            if "page" in properties:
                arguments["page"] = page
            try:
                result = await session.call_tool(tool_name, arguments)
            except Exception:
                raise IngestError("Baidu MCP read request failed") from None
            records = _records_from_payload(_payload_from_tool_result(result))
            if not records:
                break
            page_signature = tuple(
                str(record.get("fsid", record.get("path", ""))) for record in records
            )
            if page_signature == previous_page_signature:
                reached_limit = True
                break
            previous_page_signature = page_signature
            for record in records:
                if record.get("isdir") in (1, True, "1") or record.get("is_dir") is True:
                    continue
                identity = str(record.get("fsid", record.get("path", "")))
                if not identity or identity in seen_ids:
                    continue
                seen_ids.add(identity)
                collected.append(record)
                if len(collected) >= max_files:
                    reached_limit = True
                    break
            if reached_limit:
                break
        else:
            reached_limit = "page" in properties
    return collected, reached_limit


def _text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n\n".join(part for part in (_text_value(item) for item in value) if part)
    if isinstance(value, dict):
        for key in ("text", "content", "value", "segment_content", "transcript", "segments"):
            if key in value:
                return _text_value(value[key])
    return ""


def _record_identity(record: dict[str, Any]) -> tuple[str, str]:
    fsid = record.get("fsid")
    if isinstance(fsid, (str, int)) and FSID_RE.fullmatch(str(fsid)):
        return f"baidu_netdisk:{fsid}", str(fsid)
    remote_path = record.get("path")
    if not isinstance(remote_path, str) or not remote_path.strip():
        raise IngestError("Baidu file record has no stable identifier or path")
    source_id = "baidu_netdisk:path:" + hashlib.sha256(remote_path.encode("utf-8")).hexdigest()[:24]
    return source_id, source_id.rsplit(":", 1)[-1]


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


def _load_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise IngestError("manifest must not be a symbolic link")
    if not path.exists():
        return {"manifest_version": 1, "generated_at": None, "entries": [], "history": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise IngestError("manifest is unreadable or invalid") from None
    if not isinstance(data, dict) or data.get("manifest_version") != 1 or not isinstance(data.get("entries"), list):
        raise IngestError("unsupported manifest schema")
    if not isinstance(data.get("history", []), list):
        raise IngestError("unsupported manifest schema")
    data.setdefault("history", [])
    return data


def _write_manifest(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _candidate_name(source_id: str, revision_id: str) -> str:
    digest = hashlib.sha256(f"{source_id}\0{revision_id}".encode("utf-8")).hexdigest()[:24]
    return digest + ".md"


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _find_prior(manifest: dict[str, Any], source_id: str) -> dict[str, Any] | None:
    matches = [
        entry for entry in manifest["entries"]
        if isinstance(entry, dict) and entry.get("source_id") == source_id and entry.get("provider") == "baidu_netdisk"
    ]
    if len(matches) > 1:
        raise IngestError("manifest has duplicate Baidu source entries")
    return matches[0] if matches else None


def _candidate_markdown(entry: dict[str, Any], segments: str, abstract: str) -> str:
    title = str(entry["title"]).replace("\r", " ").replace("\n", " ").strip() or "Baidu Netdisk source"
    body = [
        "---",
        f"title: {_yaml_string(title)}",
        "type: knowledge_candidate",
        "knowledge_type: candidate",
        "source_refs:",
        f"  - {_yaml_string(entry['source_id'])}",
        f"source_revision: {_yaml_string(entry['revision_id'])}",
        f"source_locator: {_yaml_string(entry['locator'])}",
        "source_content_trust: untrusted_data",
        "review_status: unreviewed",
        "classification_status: pending",
        f"evidence_status: {_yaml_string(entry['retrieval_status'])}",
        "privacy: user_read",
        f"retrieval_status: {_yaml_string(entry['retrieval_status'])}",
        f"content_completeness: {_yaml_string(entry['content_completeness'])}",
        f"content_sha256: {_yaml_string(entry['content_sha256'] or '')}",
        "---",
        "",
        f"# {title}",
        "",
        "Baidu Netdisk returned platform-generated text. Full-file completeness was not verified.",
        "",
    ]
    if segments:
        body.extend(["## Platform text segments", "", segments, ""])
    if abstract:
        body.extend(["## Platform abstract", "", abstract, ""])
    return "\n".join(body)


def _staging_lock(staging: Path) -> int:
    if fcntl is None or os.name != "posix":
        raise IngestError("cross-process staging locks require macOS or Linux")
    lock_path = staging / ".knowledge-ingest.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(lock_path, flags, 0o600)
    except OSError:
        raise IngestError("could not open the staging lock") from None


def _ingest_records(records: list[dict[str, Any]], staging: Path, remote_path: str, limited: bool) -> dict[str, Any]:
    ensure_private_dir(staging)
    source_dir = staging / ".source"
    baidu_source_dir = source_dir / "baidu-netdisk"
    candidates_dir = staging / "candidates"
    ensure_private_dir(source_dir)
    ensure_private_dir(baidu_source_dir)
    ensure_private_dir(candidates_dir)
    manifest_path = staging / "manifest.json"
    manifest = _load_manifest(manifest_path)
    counts = {"imported": 0, "updated": 0, "unchanged": 0, "metadata_only": 0, "candidates": 0}
    imported_ids: set[str] = set()
    new_files: list[Path] = []

    for record in records:
        source_id, safe_key = _record_identity(record)
        if source_id in imported_ids:
            continue
        imported_ids.add(source_id)
        remote_locator = record.get("path") if isinstance(record.get("path"), str) else str(record.get("fsid", ""))
        filename = record.get("filename") if isinstance(record.get("filename"), str) else Path(remote_locator).name
        title = filename or Path(remote_locator).name or source_id
        segments = _text_value(record.get("content"))
        abstract = _text_value(record.get("abstract"))
        if segments:
            retrieval_status = "platform_segments"
            completeness = "unverified_platform_segments"
        elif abstract:
            retrieval_status = "abstract_only"
            completeness = "abstract_only"
        else:
            retrieval_status = "metadata_only"
            completeness = "metadata_only"

        clean_metadata = {
            "fsid": str(record.get("fsid", "")),
            "path": remote_locator,
            "filename": filename,
            "md5": str(record.get("md5", "")),
            "category": record.get("category"),
            "size": record.get("size"),
            "mtime": record.get("mtime"),
            "content_payload": record.get("content"),
            "abstract_payload": record.get("abstract"),
            "extracted_segments": segments,
            "extracted_abstract": abstract,
        }
        snapshot_text = json.dumps(clean_metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        snapshot_digest = hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest()
        captured_text = "\n\n".join(part for part in (segments, abstract) if part)
        content_digest = hashlib.sha256(captured_text.encode("utf-8")).hexdigest() if captured_text else None
        prior = _find_prior(manifest, source_id)
        if prior and prior.get("source_snapshot_sha256") == snapshot_digest:
            counts["unchanged"] += 1
            if prior.get("retrieval_status") == "metadata_only":
                counts["metadata_only"] += 1
            continue

        version = int(prior.get("version", 1)) + 1 if prior else 1
        revision_id = f"{source_id}@v{version}"
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        snapshot_path = baidu_source_dir / f"{safe_key}-v{version}.json"
        has_candidate = bool(segments or abstract)
        entry: dict[str, Any] = {
            "source_id": source_id,
            "revision_id": revision_id,
            "provider": "baidu_netdisk",
            "locator": remote_locator,
            "title": title,
            "media_type": media_type,
            "size_bytes": record.get("size") if isinstance(record.get("size"), int) else None,
            "source_md5": str(record.get("md5", "")) or None,
            "content_sha256": content_digest,
            "source_snapshot_sha256": snapshot_digest,
            "snapshot_file": snapshot_path.relative_to(staging).as_posix(),
            "permissions": "user_read",
            "retrieval_status": retrieval_status,
            "content_completeness": completeness,
            "content_completeness_verified": False,
            "retrieved_at": utc_now(),
            "version": version,
            "supersedes": prior.get("revision_id") if prior else None,
            "duplicate_of": None,
            "classification_status": "pending",
            "source_content_trust": "untrusted_data",
        }
        if content_digest:
            duplicate = next(
                (existing for existing in manifest["entries"]
                 if isinstance(existing, dict) and existing.get("content_sha256") == content_digest),
                None,
            )
            if duplicate:
                entry["duplicate_of"] = duplicate.get("source_id")
        candidate_path = candidates_dir / _candidate_name(source_id, revision_id) if has_candidate else None
        try:
            _atomic_write(snapshot_path, snapshot_text + "\n")
            new_files.append(snapshot_path)
            if candidate_path:
                _atomic_write(candidate_path, _candidate_markdown(entry, segments, abstract))
                new_files.append(candidate_path)
        except Exception:
            for path in new_files:
                path.unlink(missing_ok=True)
            raise
        if prior:
            previous = dict(prior)
            previous["record_status"] = "superseded"
            manifest["history"].append(previous)
            manifest["entries"] = [
                item for item in manifest["entries"]
                if not (isinstance(item, dict) and item.get("provider") == "baidu_netdisk" and item.get("source_id") == source_id)
            ]
            counts["updated"] += 1
        else:
            counts["imported"] += 1
        manifest["entries"].append(entry)
        if has_candidate:
            counts["candidates"] += 1
        else:
            counts["metadata_only"] += 1

    manifest["manifest_version"] = 1
    manifest["generated_at"] = utc_now()
    manifest.setdefault("root_label", "multi_source_ingest")
    try:
        _write_manifest(manifest_path, manifest)
    except Exception:
        for path in new_files:
            path.unlink(missing_ok=True)
        raise IngestError("could not write the source manifest") from None
    return {
        "status": "ok",
        "provider": "baidu_netdisk",
        "counts": counts,
        "pages_or_results_limited": limited,
        "remote_path_hash": hashlib.sha256(remote_path.encode("utf-8")).hexdigest()[:12],
        "manifest": "manifest.json",
    }


def ingest(records: list[dict[str, Any]], staging_dir: str | Path, remote_path: str, limited: bool = False) -> dict[str, Any]:
    if not isinstance(remote_path, str) or not remote_path.startswith("/") or "\x00" in remote_path:
        raise IngestError("Baidu path must be one explicit absolute netdisk directory")
    requested_stage = Path(staging_dir).expanduser().absolute()
    ensure_private_dir(requested_stage)
    staging = requested_stage.resolve()
    descriptor = _staging_lock(staging)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise IngestError("staging lock must be a user-owned 0600 file")
        if fcntl is None:
            raise IngestError("cross-process staging locks require macOS or Linux")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _ingest_records(records, staging, remote_path, limited)
    except IngestError:
        raise
    except OSError:
        raise IngestError("local staging operation failed") from None
    finally:
        os.close(descriptor)


async def _run_live(args: argparse.Namespace) -> dict[str, Any]:
    if not 1 <= args.max_pages <= MAX_PAGE_LIMIT:
        raise IngestError(f"max-pages must be between 1 and {MAX_PAGE_LIMIT}")
    if not 1 <= args.max_files <= MAX_FILE_LIMIT:
        raise IngestError(f"max-files must be between 1 and {MAX_FILE_LIMIT}")
    records, limited = await fetch_records(args.path, args.tool, args.max_pages, args.max_files)
    return ingest(records, args.staging_dir, args.path, limited)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, help="one explicit absolute Baidu Netdisk directory")
    parser.add_argument("--staging-dir", required=True)
    parser.add_argument("--tool", choices=sorted(READ_ONLY_TOOLS), default="file_list")
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--max-files", type=int, default=200)
    args = parser.parse_args()
    try:
        result = asyncio.run(_run_live(args))
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
