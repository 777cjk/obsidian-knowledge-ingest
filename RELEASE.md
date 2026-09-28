# obsidian-knowledge-ingest v0.4.0

Version: `0.4.0` (`v0.4.0`).

## Included

- Read-only-first local directory manifest scanner with hashes, duplicate
  detection, revision history, and staging output.
- A Feishu Minutes adapter that reuses an existing `lark-cli` login and imports
  one explicitly selected transcript into private staging with a versioned
  manifest and an unreviewed candidate.
- Versioned parser adapter with an optional lightweight MarkItDown (DOCX extra),
  macOS `textutil` fallback for legacy `.doc`, and LiteParse package set.
- A Baidu Netdisk SSE adapter that only allows the upstream read tools
  `file_list` and `file_doc_list`, writes platform response snapshots, and
  distinguishes segmented text, abstract-only, and metadata-only results.
- A local Baidu official-download adapter that streams an explicitly selected
  file into private staging, records SHA-256/size/parser evidence, and emits an
  unreviewed candidate when parsing succeeds.
- A shared private staging lock and a Feishu download isolation step that
  validates a fresh relative-output directory before moving transcripts.
- A redacted `source_preflight.py` command that separates local dependency
  readiness, connector authorization gates, and the later real content canary.
- Connector-specific scopes, source trust metadata, local install, verification,
  and rollback instructions.

## Not Included

- Feishu Docs/Wiki OAuth integration or a long-lived Baidu credential store.
- Automatic recursive download of all cloud files; every download remains an
  explicit directory/file operation and is subject to the provider's authorized
  path gate.
- Automatic AI classification, scheduled watching, approval UI, or canonical
  Obsidian writeback.
- A full hash-locked transitive dependency set. The lightweight parser
  requirements pin direct package versions; pip resolves their dependencies.

## Validation

```bash
scripts/verify.sh
```

The release was checked with the full local verification suite (41 tests),
Python 3.11/3.14 CI, and the pinned parser job, including
offline Baidu MCP fixtures, shared-lock behavior, source hashes, staging
permissions, and repeated import idempotency. The private Feishu canary
transcript remains outside the public repository and outside the Obsidian vault.
