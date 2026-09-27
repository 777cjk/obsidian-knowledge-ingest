# Parser Adapter Schema

`scripts/parser_adapter.py` exposes `parse(path, backend="auto")`. It reads a
local source and returns a versioned dictionary. It does not upload content,
change the source file, or write into an Obsidian vault.

```json
{
  "schema_version": 1,
  "status": "ok",
  "source": {
    "path": "/absolute/path/note.md",
    "name": "note.md",
    "media_type": "text/markdown",
    "size_bytes": 42,
    "modified_at": "2026-09-27T00:00:00Z",
    "sha256": "..."
  },
  "parser": {"name": "text-fixture", "version": "1"},
  "text": "# Note",
  "metadata": {"encoding": "utf-8", "line_count": 1},
  "outline": [{"kind": "heading", "level": 1, "title": "Note", "line": 1}],
  "assets": [],
  "errors": []
}
```

Statuses are `ok`, `unsupported`, `unavailable`, and `error`. The source record
and result keys are preserved for all statuses; `text` is null when parsing did
not succeed. `parse(path)` defaults to the pure-Python UTF-8 fixture parser for
known text formats. PDF and other binary inputs need an explicitly configured
backend.

## Optional boundaries

- MarkItDown, Docling, and LiteParse are lazy built-in adapters. Their Python
  packages are imported only when selected. Hosts may override an adapter with
  `register_adapter(name, adapter)`.
- OCRmyPDF is a preprocessing worker. Its searchable PDF output must then be
  passed to Docling, MarkItDown, or another document parser.
- Importing this module does not import or install any optional package. A
  missing optional package returns `status="unavailable"` with
  `backend_dependency_missing`.
- Adapter output should retain page/section references in `outline` or
  `metadata`, and describe extracted files or images in `assets`.

## CLI

```bash
python scripts/parser_adapter.py backends
python scripts/parser_adapter.py parse ./document.pdf --backend auto
python scripts/parser_adapter.py parse ./document.pdf --backend docling
```

`auto` uses the built-in UTF-8 parser for known text formats and MarkItDown for
common document formats. Select Docling explicitly when layout fidelity is
more important than startup cost. LiteParse currently supports PDF and other
formats documented by its upstream package. HTML is treated as text in `auto`
mode; use `--backend markitdown` when you want conversion through MarkItDown.
