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

The optional Feishu Minutes adapter calls the installed `lark-cli` and reads
one explicitly supplied `minute_token` per invocation. The user must already be
logged in with `minutes:minutes.artifacts:read`. It creates a fresh private
download directory under staging, runs the CLI with relative `--output-dir .`,
then validates and moves the transcript into the selected staging directory.
The manifest and unreviewed candidate are written only after that containment
check.
It does not search, request access, store credentials, or write to the vault.
The verified runtime is the official `lark-cli` v1.0.96; its transcript
directory naming sanitizes title path characters and its `--output-dir`
validation rejects escaping paths. Before changing CLI versions, repeat a
single-token canary in disposable private staging and confirm the same output
contract.
The staging directory and its output subdirectories must be owned by the
current user with mode `0700`; files are written with mode `0600`. Imports to
the same staging directory are serialized on macOS/Linux.

The optional Baidu Netdisk adapter reuses the upstream `baidu-netdisk/mcp`
SSE contract. Install its pinned client dependency with
`scripts/install.sh --with baidu-mcp`, set `BAIDU_NETDISK_ACCESS_TOKEN` in the
invoking process, and run `scripts/baidu_netdisk_ingest.py` with one explicit
absolute remote directory. The adapter only permits `file_list` and
`file_doc_list`, applies page/file bounds, writes response snapshots and
unreviewed candidates under private staging, and shares the
`.knowledge-ingest.lock` with the Feishu adapter. It never calls upload,
delete, move, rename, copy, make-directory, or share tools.

Baidu's documented `content` is platform-generated segmented text and may be
empty; `abstract` may also be empty. The adapter records
`platform_segments`, `abstract_only`, or `metadata_only` and always marks
`content_completeness_verified: false`. A successful local fixture or tool
discovery is not a real authorization or full-file retrieval canary. After
OAuth, run one known directory, inspect the manifest and candidate, and
confirm whether returned segments cover the complete source before promoting
anything into the knowledge asset layer.

Feishu Docs/Wiki and Baidu Netdisk still require separate host OAuth/scope
integrations. This repository does not store tokens, cookies, or connector
configuration, and a successful local install does not prove those remote
sources can return complete file content.
