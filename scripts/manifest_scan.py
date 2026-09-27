#!/usr/bin/env python3
"""Read-only local source scanner for the Obsidian knowledge ingest adapter."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import tempfile
from typing import Dict, Iterator, Optional, Set, Tuple


DEFAULT_EXCLUDES = {".git", ".venv", "node_modules", "__pycache__", ".cache"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def classify_hint(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".rst", ".org", ".html", ".htm", ".pdf", ".doc", ".docx", ".odt"}:
        return "document"
    if suffix in {".csv", ".tsv", ".xls", ".xlsx", ".ods", ".json"}:
        return "data"
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".heic"}:
        return "image"
    if suffix in {".mp3", ".m4a", ".wav", ".mp4", ".mov", ".webm"}:
        return "audio_or_video"
    if suffix in {".py", ".js", ".ts", ".go", ".rs", ".java", ".cpp", ".h", ".sh", ".yaml", ".yml", ".toml"}:
        return "code_or_config"
    return "other"


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"manifest_version": 1, "generated_at": None, "entries": [], "history": []}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("manifest_version") != 1 or not isinstance(data.get("entries"), list):
        raise ValueError(f"unsupported manifest: {path}")
    data.setdefault("history", [])
    return data


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def iter_files(root: Path, excludes: Set[str]) -> Iterator[Tuple[Path, Path]]:
    root = root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in excludes for part in relative.parts):
            continue
        yield path, relative


def make_entry(root: Path, label: str, path: Path, relative: Path, prior: Optional[Dict]) -> dict:
    stat = path.stat()
    source_id = f"local:{label}:{relative.as_posix()}"
    digest = sha256_file(path)
    previous_digest = prior.get("content_sha256") if prior else None
    version = int(prior.get("version", 0)) + 1 if prior and previous_digest != digest else int(prior.get("version", 1)) if prior else 1
    changed = bool(prior and previous_digest != digest)
    prior_revision = prior.get("revision_id") if prior else None
    if prior and not prior_revision:
        prior_revision = f"{source_id}@v{prior.get('version', 1)}"
    revision_id = f"{source_id}@v{version}"
    return {
        "source_id": source_id,
        "revision_id": revision_id,
        "provider": "local",
        "locator": str(path),
        "relative_path": relative.as_posix(),
        "title": path.name,
        "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "size_bytes": stat.st_size,
        "modified_at": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "content_sha256": digest,
        "permissions": "local_read",
        "retrieval_status": "metadata_only",
        "version": version,
        "supersedes": prior_revision if changed else prior.get("supersedes") if prior else None,
        "duplicate_of": None,
        "classification_status": "pending",
        "classification_hint": classify_hint(path),
    }


def candidate_markdown(entry: dict) -> str:
    source_ref = entry["source_id"]
    title = entry["title"].replace("\"", "'")
    return f'''---
title: "{title}"
type: knowledge_candidate
knowledge_type: candidate
source_refs:
  - "{source_ref}"
source_revision: "{entry["revision_id"]}"
provenance: "connector_metadata"
review_status: "unreviewed"
evidence_status: "partial"
privacy: "{entry["permissions"]}"
retrieval_status: "{entry["retrieval_status"]}"
content_sha256: "{entry["content_sha256"]}"
classification_hint: "{entry["classification_hint"]}"
---

# {title}

采集来源：`{entry["locator"]}`

当前只记录文件身份和元数据，尚未读取正文或形成知识结论。请在权限确认后选择解析器，并在复核后进入知识资产层。
'''


def candidate_filename(entry: dict) -> str:
    """Keep same-content files and successive revisions as separate candidates."""
    key = f"{entry['source_id']}@{entry['revision_id']}".encode("utf-8")
    return f"{hashlib.sha256(key).hexdigest()[:16]}.md"


def scan(args: argparse.Namespace) -> dict:
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"root is not a directory: {root}")
    manifest_path = Path(args.manifest).expanduser()
    previous = load_manifest(manifest_path)
    previous_by_id = {entry["source_id"]: entry for entry in previous["entries"]}
    entries = []
    history = list(previous.get("history", []))
    seen_hashes: Dict[str, str] = {}
    counts = {"new": 0, "modified": 0, "unchanged": 0, "duplicate": 0}
    for path, relative in iter_files(root, set(args.exclude) | DEFAULT_EXCLUDES):
        source_id = f"local:{args.label}:{relative.as_posix()}"
        prior = previous_by_id.get(source_id)
        entry = make_entry(root, args.label, path, relative, prior)
        if prior and prior.get("content_sha256") == entry["content_sha256"]:
            counts["unchanged"] += 1
        elif prior:
            counts["modified"] += 1
        else:
            counts["new"] += 1
        if entry["content_sha256"] in seen_hashes:
            entry["duplicate_of"] = seen_hashes[entry["content_sha256"]]
            counts["duplicate"] += 1
        else:
            seen_hashes[entry["content_sha256"]] = entry["source_id"]
        entries.append(entry)
        if prior and prior.get("content_sha256") != entry["content_sha256"]:
            prior_snapshot = dict(prior)
            prior_snapshot["record_status"] = "superseded"
            history.append(prior_snapshot)
    current_ids = {entry["source_id"] for entry in entries}
    deleted = [entry for entry in previous["entries"] if entry["source_id"] not in current_ids]
    counts["deleted"] = len(deleted)
    data = {
        "manifest_version": 1,
        "generated_at": utc_now(),
        "root_label": args.label,
        "entries": entries,
        "history": history,
        "deleted": deleted,
    }
    write_json_atomic(manifest_path, data)
    if args.output_dir and args.emit_candidates:
        candidate_dir = Path(args.output_dir).expanduser() / "candidates"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            output = candidate_dir / candidate_filename(entry)
            output.write_text(candidate_markdown(entry), encoding="utf-8")
    return {"manifest": str(manifest_path), "root": str(root), "entries": len(entries), "counts": counts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan_parser = sub.add_parser("scan", help="scan one local directory without modifying source files")
    scan_parser.add_argument("--root", required=True)
    scan_parser.add_argument("--label", required=True)
    scan_parser.add_argument("--manifest", required=True)
    scan_parser.add_argument("--output-dir")
    scan_parser.add_argument("--emit-candidates", action="store_true")
    scan_parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()
    result = scan(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
