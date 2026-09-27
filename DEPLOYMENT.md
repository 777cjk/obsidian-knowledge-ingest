# Local Deployment

This repository is a local, read-only-first adapter. The supported deployment
shape is a repository checkout plus a repository-local Python virtualenv. It
does not need an account, API key, daemon, database, or network service for
the local scanner and text parser.

## First install

From the repository root:

```bash
scripts/install.sh
```

The installer requires Python 3.9 or newer, creates or reuses `.venv`, and
runs `scripts/verify.sh`. The default path uses only the Python standard
library. It does not read credentials and does not inspect or modify a source
directory or an Obsidian vault.

Optional parser packages are explicit and isolated:

```bash
scripts/install.sh --with parser-lite
# equivalent direct pip install:
python -m pip install -r requirements-parser-lite.txt
# install one lightweight parser only:
scripts/install.sh --with markitdown
scripts/install.sh --with liteparse
scripts/install.sh --with docling
scripts/install.sh --with ocrmypdf
```

The default scanner supports Python 3.9 and newer. MarkItDown and LiteParse
require Python 3.10 or newer; the installer checks the selected virtualenv
before installing either package.

The lightweight parser set pins MarkItDown 0.1.8 and LiteParse 2.14.7 in
`requirements-parser-lite.txt` and its included files. Docling and OCRmyPDF
may require large downloads or operating-system tools; they are opt-in and
intentionally not version-locked here. Use `--offline` only when the required
package wheels are already available in the local pip cache.

## Verification

```bash
scripts/verify.sh
```

Verification compiles the adapters, runs the unit suite, scans a temporary
fixture directory, parses one Markdown fixture, and checks duplicate handling
and the common parser schema. The temporary directory is removed on exit.

## Upgrade

Keep the existing checkout and rerun the installer after updating the source:

```bash
scripts/install.sh --with parser-lite
```

The virtualenv is reused. The installer does not upgrade pip or silently add
optional packages. The lightweight parser set uses the repository's exact
direct-dependency pins; its transitive packages are resolved by pip at install
time and are not a full hash-locked environment.

## Rollback

The scanner writes manifests atomically, but source and staging data remain
outside the repository by design. Before a code upgrade, record the current
commit and keep the existing checkout or a separate copy:

```bash
git rev-parse HEAD
git diff --check
```

To roll back the local Python environment without deleting it, move the
repository-local virtualenv aside and recreate it from the previous checkout:

```bash
mv .venv ".venv.rollback.$(date +%Y%m%d%H%M%S)"
scripts/install.sh
```

Restore a previous `manifest.json` or staging directory from the host's own
backup. Do not regenerate a manifest against a changed source tree merely to
undo a code release; the manifest is evidence and should be preserved.

## Platform connectors

Feishu and Baidu Netdisk connectors are optional upstream integrations. Add
their OAuth and scope configuration only in the host adapter. This repository
does not store tokens, cookies, or connector configuration, and a successful
local install does not prove that a remote connector can read file content.
