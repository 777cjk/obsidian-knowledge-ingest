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

The lightweight parser set pins MarkItDown 0.1.8 (with its `docx` extra) and LiteParse 2.14.7 in
`requirements-parser-lite.txt` and its included files. Docling and OCRmyPDF
may require large downloads or operating-system tools; they are opt-in and
intentionally not version-locked here. Use `--offline` only when the required
package wheels are already available in the local pip cache.

## Verification

```bash
scripts/verify.sh
```

Before a remote canary, run the connector preflight. It only checks the local
Python/MCP dependency, whether the Baidu token is present in the invoking
process, and the three Feishu scopes. It does not fetch a file or print a
credential:

```bash
python3 scripts/source_preflight.py --json
python3 scripts/source_preflight.py --require-baidu
python3 scripts/source_preflight.py --require-feishu-docs
```

The default command reports missing external gates without failing. The
`--require-*` forms return exit code 2 when the selected gate is not ready.
`local_ready` means only that the local Python check passed. `ready` means the
selected required gate passed. Both are preflight results only; a real canary must still inspect the
manifest and content completeness fields.

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
`scripts/install.sh --with baidu-mcp`, then either store the token through the
macOS Keychain prompt (`python3 scripts/baidu_netdisk_credentials.py`) or set
`BAIDU_NETDISK_ACCESS_TOKEN` for one process. Run
`scripts/baidu_netdisk_ingest.py` with one explicit absolute remote directory.
The adapter only permits `file_list` and
`file_doc_list`, applies page/file bounds, writes response snapshots and
unreviewed candidates under private staging, and shares the
`.knowledge-ingest.lock` with the Feishu adapter. It never calls upload,
delete, move, rename, copy, make-directory, or share tools.

The upstream README currently labels its personal OAuth app as a limited-time
trial, and the consent page describes `netdisk` as allowing folder creation
and read/write access. This adapter invokes only read tools, but the token's
granted authority is broader than this process's allowlist. Keep it in the
user's local environment or Keychain; do not deploy this trial app as a
multi-user service or describe its token as read-only. The personal OAuth
flow returns an access token without an automatic refresh path here; when it
expires, authorize again and replace the Keychain item using the same prompt.

Baidu's documented `content` is platform-generated segmented text and may be
empty; `abstract` may also be empty. The adapter records
`platform_segments`, `abstract_only`, or `metadata_only` and always marks
`content_completeness_verified: false`. A successful local fixture or tool
discovery is not a real authorization or full-file retrieval canary. After
OAuth, run one known directory, inspect the manifest and candidate, and
confirm whether returned segments cover the complete source before promoting
anything into the knowledge asset layer.

When the MCP response contains only metadata, the local download adapter can
retrieve one explicitly selected file through Baidu's official PCS/XPan
download endpoint and then run the shared parser contract:

```bash
scripts/install.sh --with baidu-mcp --with parser-lite
BAIDU_NETDISK_ACCESS_TOKEN='<token>' \
  .venv/bin/python scripts/baidu_netdisk_download.py \
  --path '/apps/<authorized-app>/<file.docx>' \
  --staging-dir '/private/path/baidu-download-staging'
```

The adapter streams into a user-owned `0700` staging directory, writes files
with mode `0600`, records SHA-256 and parser output, and emits an unreviewed
candidate only when parsing succeeds. It does not use desktop cookies, call
MCP write tools, or modify the Obsidian vault. Baidu's current authorization
model may restrict REST downloads to the application's authorized directory;
an `authorized_paths` or application-directory mismatch is a real external
gate, not a reason to treat a metadata listing as complete content.

Feishu Docs/Wiki and Baidu Netdisk still require separate host OAuth/scope
integrations. This repository does not store tokens, cookies, or connector
configuration, and a successful local install does not prove those remote
sources can return complete file content.
