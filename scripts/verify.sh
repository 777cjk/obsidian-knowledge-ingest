#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-}
if [ -z "$PYTHON_BIN" ] && [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi
PYTHON_BIN=${PYTHON_BIN:-python3}

cd "$ROOT_DIR"
"$PYTHON_BIN" - "$ROOT_DIR" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
if sys.version_info < (3, 9):
    raise SystemExit("Python >= 3.9 is required")
for required in (root / "scripts/manifest_scan.py", root / "scripts/parser_adapter.py"):
    if not required.is_file():
        raise SystemExit(f"missing required file: {required}")
print(f"using Python {sys.version.split()[0]}")
PY

"$PYTHON_BIN" -m py_compile \
    scripts/manifest_scan.py \
    scripts/parser_adapter.py \
    scripts/feishu_minutes_ingest.py \
    scripts/baidu_netdisk_ingest.py \
    scripts/source_preflight.py
"$PYTHON_BIN" -m unittest discover -s tests -v
"$PYTHON_BIN" scripts/source_preflight.py --json >/dev/null

SMOKE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/obsidian-knowledge-ingest.XXXXXX")
cleanup() {
    rm -rf "$SMOKE_DIR"
}
trap cleanup EXIT INT TERM

mkdir -p "$SMOKE_DIR/source/sub" "$SMOKE_DIR/stage"
printf '# Smoke\n\nsource\n' > "$SMOKE_DIR/source/a.md"
cp "$SMOKE_DIR/source/a.md" "$SMOKE_DIR/source/sub/copy.md"

"$PYTHON_BIN" scripts/manifest_scan.py scan \
    --root "$SMOKE_DIR/source" \
    --label verify \
    --manifest "$SMOKE_DIR/stage/manifest.json" \
    --output-dir "$SMOKE_DIR/stage" \
    --emit-candidates > "$SMOKE_DIR/scan.json"

"$PYTHON_BIN" scripts/parser_adapter.py parse "$SMOKE_DIR/source/a.md" > "$SMOKE_DIR/parse.json"

"$PYTHON_BIN" - "$SMOKE_DIR" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
scan = json.loads((root / "scan.json").read_text(encoding="utf-8"))
parsed = json.loads((root / "parse.json").read_text(encoding="utf-8"))
manifest = json.loads((root / "stage/manifest.json").read_text(encoding="utf-8"))
candidates = list((root / "stage/candidates").glob("*.md"))
assert scan["counts"]["new"] == 2, scan
assert scan["counts"]["duplicate"] == 1, scan
assert len(manifest["entries"]) == 2, manifest
assert len(candidates) == 2, candidates
assert parsed["status"] == "ok", parsed
assert parsed["source"]["sha256"], parsed
print("verification smoke passed")
PY
