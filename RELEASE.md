# obsidian-knowledge-ingest v0.2.0

Version: `0.2.0` (`v0.2.0`).

## Included

- Read-only-first local directory manifest scanner with hashes, duplicate
  detection, revision history, and staging output.
- A Feishu Minutes adapter that reuses an existing `lark-cli` login and imports
  one explicitly selected transcript into private staging with a versioned
  manifest and an unreviewed candidate.
- Versioned parser adapter with an optional lightweight MarkItDown and
  LiteParse package set.
- Connector-specific scopes, source trust metadata, local install, verification,
  and rollback instructions.

## Not Included

- Feishu Docs/Wiki or Baidu Netdisk OAuth connector implementations.
- Automatic AI classification, scheduled watching, approval UI, or canonical
  Obsidian writeback.
- A full hash-locked transitive dependency set. The lightweight parser
  requirements pin direct package versions; pip resolves their dependencies.

## Validation

```bash
scripts/verify.sh
```

The release was checked with the full local verification suite and a private
Feishu Minutes canary covering source hash, staging permissions, and repeated
import idempotency. The canary transcript remains outside the public repository
and outside the Obsidian vault.
