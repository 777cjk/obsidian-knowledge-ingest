# obsidian-knowledge-ingest v0.1.0

Version: `0.1.0` (`v0.1.0`).

## Included

- Read-only-first local directory manifest scanner with hashes, duplicate
  detection, revision history, and staging output.
- Versioned parser adapter with an optional lightweight MarkItDown and
  LiteParse package set.
- CI coverage for Python 3.11 and 3.14, plus a Python 3.11 parser canary.
- Explicit local install, verify, and rollback instructions.

## Not Included

- Feishu or Baidu Netdisk OAuth connector implementations.
- Automatic AI classification, scheduled watching, or canonical Obsidian
  writeback.
- A full hash-locked transitive dependency set. The lightweight parser
  requirements pin direct package versions; pip resolves their dependencies.

## Validation

```bash
scripts/verify.sh
scripts/install.sh --with parser-lite
```

The CI parser job checks both installed package versions and parser-adapter
registration before running the verification suite.
