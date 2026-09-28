"""Small, dependency-free parser adapter contract for local source files."""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
import hashlib
import importlib
from importlib import metadata as importlib_metadata
import importlib.util
import argparse
import json
import mimetypes
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping, Optional, Protocol, Sequence


SCHEMA_VERSION = 1
TEXT_SUFFIXES = {
    ".c", ".cc", ".cpp", ".csv", ".go", ".h", ".html", ".htm", ".java",
    ".js", ".json", ".md", ".markdown", ".org", ".py", ".rst", ".rs",
    ".sh", ".sql", ".toml", ".ts", ".tsv", ".txt", ".xml", ".yaml", ".yml",
}

OPTIONAL_BACKENDS = {
    "macos-textutil": {
        "role": "legacy_doc_parser",
        "package": "textutil",
        "notes": "macOS built-in textutil fallback for legacy .doc files.",
    },
    "markitdown": {
        "role": "document_parser",
        "package": "markitdown",
        "notes": "General document-to-Markdown parser; load only when explicitly selected.",
    },
    "docling": {
        "role": "layout_aware_parser",
        "package": "docling",
        "notes": "Optional high-fidelity parser for layout, tables, page structure, and OCR.",
    },
    "liteparse": {
        "role": "pdf_parser",
        "package": "liteparse",
        "notes": "Optional lightweight PDF parser; preserve page references in metadata.",
    },
    "ocrmypdf": {
        "role": "pdf_preprocessor",
        "package": "ocrmypdf",
        "notes": "Produces a searchable PDF for a subsequent parser; it does not emit text itself.",
    },
}
PARSER_BACKENDS = {"macos-textutil", "markitdown", "docling", "liteparse"}
DOCUMENT_SUFFIXES = {
    ".doc", ".docx", ".epub", ".eml", ".html", ".htm", ".msg", ".odt",
    ".odp", ".ods", ".pdf", ".ppt", ".pptx", ".rtf", ".xls", ".xlsx",
}


@dataclass(frozen=True)
class ParsedContent:
    """Backend output. The public parse() function adds source and parser identity."""

    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    outline: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    assets: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


class ParserAdapter(Protocol):
    name: str
    version: str

    def parse_content(self, path: Path) -> ParsedContent:
        """Return extracted content; do not mutate the source file."""


class TextFixtureParser:
    """Pure Python UTF-8 parser for text and Markdown fixtures."""

    name = "text-fixture"
    version = "1"

    def parse_content(self, path: Path) -> ParsedContent:
        raw = path.read_bytes()
        if b"\x00" in raw:
            raise ValueError("binary content is not supported by the text fixture parser")
        text = raw.decode("utf-8-sig")
        outline = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                if title:
                    outline.append({
                        "kind": "heading",
                        "level": len(stripped) - len(stripped.lstrip("#")),
                        "title": title,
                        "line": line_number,
                    })
        return ParsedContent(
            text=text,
            metadata={"encoding": "utf-8", "line_count": len(text.splitlines())},
            outline=outline,
        )


def _markdown_outline(text: str) -> list[dict[str, Any]]:
    outline = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                outline.append({
                    "kind": "heading",
                    "level": len(stripped) - len(stripped.lstrip("#")),
                    "title": title,
                    "line": line_number,
                })
    return outline


def _package_version(package: str) -> str:
    try:
        return importlib_metadata.version(package)
    except importlib_metadata.PackageNotFoundError:
        return "unknown"


class MarkItDownAdapter:
    name = "markitdown"

    def __init__(self) -> None:
        module = importlib.import_module("markitdown")
        self._converter = module.MarkItDown(enable_plugins=False)
        self.version = _package_version("markitdown")

    def parse_content(self, path: Path) -> ParsedContent:
        result = self._converter.convert_local(str(path))
        text = result.text_content
        return ParsedContent(
            text=text,
            metadata={"title": getattr(result, "title", None)},
            outline=_markdown_outline(text),
        )


class MacOSTextutilAdapter:
    """Use the macOS system converter for legacy binary Word documents."""

    name = "macos-textutil"
    version = "system"

    def __init__(self) -> None:
        executable = shutil.which("textutil")
        if not executable:
            raise ModuleNotFoundError("textutil")
        self._executable = executable

    def parse_content(self, path: Path) -> ParsedContent:
        completed = subprocess.run(
            [self._executable, "-convert", "txt", "-stdout", str(path)],
            check=True,
            capture_output=True,
        )
        text = completed.stdout.decode("utf-8", errors="replace")
        return ParsedContent(
            text=text,
            metadata={"encoding": "utf-8", "line_count": len(text.splitlines()), "converter": self._executable},
            outline=_markdown_outline(text),
        )


class DoclingAdapter:
    name = "docling"

    def __init__(self) -> None:
        module = importlib.import_module("docling.document_converter")
        self._converter = module.DocumentConverter()
        self.version = _package_version("docling")

    def parse_content(self, path: Path) -> ParsedContent:
        result = self._converter.convert(str(path))
        text = result.document.export_to_markdown()
        return ParsedContent(
            text=text,
            metadata={"page_count": len(getattr(result.document, "pages", {}))},
            outline=_markdown_outline(text),
        )


class LiteParseAdapter:
    name = "liteparse"

    def __init__(self) -> None:
        module = importlib.import_module("liteparse")
        self._parser = module.LiteParse(output_format="markdown", quiet=True)
        self.version = _package_version("liteparse")

    def parse_content(self, path: Path) -> ParsedContent:
        result = self._parser.parse(str(path))
        pages = list(getattr(result, "pages", []) or [])
        page_refs = [
            {"page": getattr(page, "page_num", index)}
            for index, page in enumerate(pages, start=1)
        ]
        return ParsedContent(
            text=result.text,
            metadata={
                "page_count": getattr(result, "total_pages", len(pages)),
                "source_page_refs": page_refs,
            },
            outline=_markdown_outline(result.text),
        )


_ADAPTERS: dict[str, ParserAdapter] = {"text-fixture": TextFixtureParser()}


def register_adapter(name: str, adapter: ParserAdapter) -> None:
    """Override an optional backend, useful for host-specific workers and tests."""
    if name not in PARSER_BACKENDS:
        raise ValueError(f"not a declared optional backend: {name}")
    if not callable(getattr(adapter, "parse_content", None)):
        raise TypeError("adapter must provide parse_content(path)")
    _ADAPTERS[name] = adapter


def backend_catalog() -> dict[str, dict[str, Any]]:
    """Return declared backend roles without importing optional packages."""
    catalog = {
        "text-fixture": {
            "role": "text_parser",
            "package": None,
            "optional": False,
            "configured": True,
        }
    }
    for name, details in OPTIONAL_BACKENDS.items():
        catalog[name] = {
            **details,
            "optional": True,
            "registered": name in _ADAPTERS,
            "installed": importlib.util.find_spec(details["package"]) is not None,
        }
    return catalog


def _source_metadata(path: Path) -> dict[str, Any]:
    source: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "size_bytes": None,
        "modified_at": None,
        "sha256": None,
    }
    if not path.is_file():
        return source
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    source.update({
        "size_bytes": stat.st_size,
        "modified_at": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc)
        .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sha256": digest.hexdigest(),
    })
    return source


def _result(
    path: Path,
    *,
    status: str,
    parser_name: str,
    parser_version: str | None = None,
    content: ParsedContent | None = None,
    errors: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    content = content or ParsedContent(text="")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "source": _source_metadata(path),
        "parser": {"name": parser_name, "version": parser_version},
        "text": content.text if status == "ok" else None,
        "metadata": dict(content.metadata),
        "outline": [dict(item) for item in content.outline],
        "assets": [dict(item) for item in content.assets],
        "errors": [dict(item) for item in errors],
    }


def parse(path: str | Path, backend: str = "auto") -> dict[str, Any]:
    """Parse one local file into the versioned common result schema.

    ``auto`` selects the built-in parser for known text extensions. Optional
    engines are only selected explicitly and only loaded after registration.
    """
    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        return _result(
            source_path,
            status="error",
            parser_name=backend,
            errors=[{"code": "source_not_found", "message": "source is not a regular file"}],
        )

    if backend == "auto":
        suffix = source_path.suffix.lower()
        selected = (
            "text-fixture" if suffix in TEXT_SUFFIXES
            else "macos-textutil" if suffix == ".doc" and shutil.which("textutil")
            else "markitdown" if suffix in DOCUMENT_SUFFIXES
            else "auto"
        )
    else:
        selected = backend
    if selected == "auto":
        return _result(
            source_path,
            status="unsupported",
            parser_name="auto",
            errors=[{"code": "no_parser_for_media_type", "message": "no parser is configured for this file type"}],
        )
    if selected == "ocrmypdf":
        return _result(
            source_path,
            status="unsupported",
            parser_name=selected,
            errors=[{
                "code": "preprocessor_only",
                "message": "OCRmyPDF creates a searchable PDF; pass that output to a document parser",
            }],
        )
    if selected not in {"text-fixture", *OPTIONAL_BACKENDS}:
        raise ValueError(f"unknown parser backend: {selected}")
    adapter = _ADAPTERS.get(selected)
    if adapter is None:
        adapter_class = {
            "macos-textutil": MacOSTextutilAdapter,
            "markitdown": MarkItDownAdapter,
            "docling": DoclingAdapter,
            "liteparse": LiteParseAdapter,
        }[selected]
        try:
            adapter = adapter_class()
            _ADAPTERS[selected] = adapter
        except ModuleNotFoundError as exc:
            return _result(
                source_path,
                status="unavailable",
                parser_name=selected,
                errors=[{
                    "code": "backend_dependency_missing",
                    "message": f"optional dependency for {selected} is unavailable: {exc.name or exc}",
                }],
            )
        except ImportError as exc:
            return _result(
                source_path,
                status="unavailable",
                parser_name=selected,
                errors=[{"code": "backend_import_error", "message": str(exc)}],
            )
    try:
        content = adapter.parse_content(source_path)
    except UnicodeDecodeError as exc:
        return _result(
            source_path,
            status="error",
            parser_name=adapter.name,
            parser_version=adapter.version,
            errors=[{"code": "decode_error", "message": str(exc)}],
        )
    except Exception as exc:
        return _result(
            source_path,
            status="error",
            parser_name=adapter.name,
            parser_version=adapter.version,
            errors=[{"code": "parser_error", "message": str(exc)}],
        )
    return _result(
        source_path,
        status="ok",
        parser_name=adapter.name,
        parser_version=adapter.version,
        content=content,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse a local source into the common ingest schema.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    parse_parser = subparsers.add_parser("parse", help="parse one local file")
    parse_parser.add_argument("path")
    parse_parser.add_argument("--backend", choices=["auto", "text-fixture", "macos-textutil", "markitdown", "docling", "liteparse", "ocrmypdf"], default="auto")
    subparsers.add_parser("backends", help="show optional parser availability without loading them")
    args = parser.parse_args()
    if args.command == "backends":
        result = backend_catalog()
        exit_code = 0
    else:
        result = parse(args.path, backend=args.backend)
        exit_code = 0 if result["status"] == "ok" else 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
