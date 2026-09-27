#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=${VENV_DIR:-"$ROOT_DIR/.venv"}
WITH_PACKAGES=""
OFFLINE=0
INSTALL_PARSER_LITE=0
INSTALL_MARKITDOWN=0
INSTALL_LITEPARSE=0
INSTALL_BAIDU_MCP=0

usage() {
    cat <<'EOF'
Usage: scripts/install.sh [options]

Create or reuse a repository-local virtual environment, install explicitly
requested optional parser packages, and run the local verification suite.

Options:
  --python PATH       Python executable used to create the virtualenv
  --venv PATH         virtualenv location (default: .venv)
  --with NAME         optional set/package: parser-lite, markitdown, liteparse,
                      docling, ocrmypdf, baidu-mcp
  --offline           pass --no-index to pip for locally cached packages
  -h, --help          show this help

The default path is dependency-free and does not contact a package index.
No credentials, source files, manifests, or Obsidian vault files are read or
written by this installer.

The parser-lite set pins MarkItDown and LiteParse using the requirements files
in this repository. Docling and OCRmyPDF are heavyweight optional tools and
are installed from the package index without version pins.
MarkItDown and LiteParse require Python >= 3.10 in the selected virtualenv.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --python)
            [ "$#" -ge 2 ] || { echo "--python requires a path" >&2; exit 2; }
            PYTHON_BIN=$2
            shift 2
            ;;
        --venv)
            [ "$#" -ge 2 ] || { echo "--venv requires a path" >&2; exit 2; }
            VENV_DIR=$2
            shift 2
            ;;
        --with)
            [ "$#" -ge 2 ] || { echo "--with requires a package name" >&2; exit 2; }
            case "$2" in
                parser-lite)
                    INSTALL_PARSER_LITE=1
                    ;;
                markitdown) INSTALL_MARKITDOWN=1 ;;
                liteparse) INSTALL_LITEPARSE=1 ;;
                baidu-mcp) INSTALL_BAIDU_MCP=1 ;;
                docling|ocrmypdf) WITH_PACKAGES="$WITH_PACKAGES $2" ;;
                *) echo "unsupported optional package: $2" >&2; exit 2 ;;
            esac
            shift 2
            ;;
        --offline)
            OFFLINE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$VENV_DIR" in
    /*) : ;;
    *) VENV_DIR="$ROOT_DIR/$VENV_DIR" ;;
esac

"$PYTHON_BIN" - "$VENV_DIR" <<'PY'
import sys
from pathlib import Path

if sys.version_info < (3, 9):
    raise SystemExit("Python >= 3.9 is required")
venv = Path(sys.argv[1])
if not venv.exists():
    print(f"creating virtualenv: {venv}")
else:
    print(f"reusing virtualenv: {venv}")
PY

if [ ! -x "$VENV_DIR/bin/python" ]; then
    mkdir -p "$(dirname -- "$VENV_DIR")"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
if [ "$INSTALL_PARSER_LITE" -eq 1 ] || \
   [ "$INSTALL_MARKITDOWN" -eq 1 ] || \
   [ "$INSTALL_LITEPARSE" -eq 1 ]; then
    if ! "$VENV_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
        echo "MarkItDown and LiteParse require Python >= 3.10 in the selected virtualenv" >&2
        exit 2
    fi
fi

if [ "$INSTALL_BAIDU_MCP" -eq 1 ]; then
    if ! "$VENV_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
        echo "The Baidu MCP client requires Python >= 3.10 in the selected virtualenv" >&2
        exit 2
    fi
fi

if [ "$INSTALL_PARSER_LITE" -eq 1 ] || \
   [ "$INSTALL_MARKITDOWN" -eq 1 ] || \
   [ "$INSTALL_LITEPARSE" -eq 1 ] || \
   [ "$INSTALL_BAIDU_MCP" -eq 1 ] || \
   [ -n "$WITH_PACKAGES" ]; then
    set --
    if [ "$OFFLINE" -eq 1 ]; then
        set -- "$@" --no-index
    fi
    if [ "$INSTALL_PARSER_LITE" -eq 1 ]; then
        set -- "$@" -r "$ROOT_DIR/requirements-parser-lite.txt"
    else
        if [ "$INSTALL_MARKITDOWN" -eq 1 ]; then
            set -- "$@" -r "$ROOT_DIR/requirements-markitdown.txt"
        fi
        if [ "$INSTALL_LITEPARSE" -eq 1 ]; then
            set -- "$@" -r "$ROOT_DIR/requirements-liteparse.txt"
        fi
    fi
    if [ "$INSTALL_BAIDU_MCP" -eq 1 ]; then
        set -- "$@" -r "$ROOT_DIR/requirements-baidu-mcp.txt"
    fi
    if [ -n "$WITH_PACKAGES" ]; then
        # The values are restricted to the heavyweight optional package allowlist above.
        # shellcheck disable=SC2086
        set -- "$@" $WITH_PACKAGES
    fi
    "$VENV_PYTHON" -m pip install "$@"
fi

PYTHON_BIN="$VENV_PYTHON" "$ROOT_DIR/scripts/verify.sh"
