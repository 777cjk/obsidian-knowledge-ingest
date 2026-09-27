# Source Manifest Schema

Each entry is an observed source record, not a knowledge claim.

```yaml
manifest_version: 1
generated_at: "2026-09-27T00:00:00Z"
entries:
  - source_id: "local:personal-files:notes/example.pdf"
    revision_id: "local:personal-files:notes/example.pdf@v1"
    provider: local
    locator: "/path/to/source/notes/example.pdf"
    relative_path: "notes/example.pdf"
    title: "example.pdf"
    media_type: "application/pdf"
    size_bytes: 1234
    modified_at: "2026-09-27T00:00:00Z"
    content_sha256: "..."
    permissions: "local_read"
    retrieval_status: "metadata_only"
    version: 1
    supersedes: null
    duplicate_of: null
    classification_status: "pending"
history: []
```

`content_sha256` is used for exact duplicate detection. A changed file gets a
new `revision_id`, points to the previous revision with `supersedes`, and moves
the previous metadata snapshot into `history`. `source_id` remains stable for
the logical source path. The scanner never overwrites the original source file.
