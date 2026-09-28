# Connector Matrix

| 输入 | 直接复用 | 适配层职责 | 当前限制 |
|---|---|---|---|
| 本机文件夹 | 本仓库扫描器；watchdog 可作为后续变化事件源 | allowlist、增量 manifest、哈希、去重、版本、候选 staging | watcher 尚未集成；默认只读 metadata，解析器另配 |
| 飞书妙记逐字稿 | 官方 `lark-cli`（用户已登录） | 单个显式 `minute_token` → transcript → parser → versioned Source Manifest 与待审候选 | 需要 `minutes:minutes.artifacts:read`；不搜索/翻页/申请权限；不代表云文档/Wiki 权限 |
| 飞书云文档/Wiki | `larksuite/lark-openapi-mcp` 或官方 Python SDK | 只读 fetch、分页、external_id、权限和内容 locator 转换为 SourceRecord | 通用文档正文 connector 尚未集成，仍需管理员开启 scope |
| 百度网盘 | `baidu-netdisk/mcp`（MIT，SSE） + 官方 PCS/XPan download | 进程 allowlist 只调用列目录；本地下载适配器对明确文件做流式下载、哈希校验、解析和候选 staging | 上游个人 OAuth 是限时体验应用；`netdisk` scope 包含读写能力，所以 token 本身不是只读凭证；远端 MCP 主分支尚未提供 `file_download`，REST 下载受应用授权目录限制 |
| PDF/DOCX/PPTX/XLSX/图片/音频 | Docling；LiteParse；MarkItDown fallback | parser、parser_version、outline、assets、失败原因 | 高保真解析可能占用 CPU/GPU；解析结果仍是候选 |
| 扫描 PDF | OCRmyPDF（独立可选 worker）→ Docling | 记录 OCR 输出哈希、页码、语言和失败原因 | MPL-2.0、Tesseract/Ghostscript 依赖，不进入默认 Skill 安装 |

## Write boundary

Connector output first lands in a local manifest and staging directory. It must
not write a canonical Obsidian project card or approved knowledge asset. A host
may promote a candidate only after source references, evidence status, privacy
scope, and human/explicit evidence review are present.

## Recommended order

1. Start with one local directory and `--emit-candidates`.
2. Use an existing read-only Feishu Minutes scope to import one explicit token.
3. Add a read-only Feishu Docs/Wiki scope and one known document node.
4. Add a read-only Baidu scope and one known file/folder; allowlist `file_list`/`file_doc_list`，then verify whether the returned text is complete.
5. For metadata-only results, download one explicitly authorized file with `baidu_netdisk_download.py`, verify SHA-256/size, and parse it into an unreviewed candidate.
6. Add Docling or LiteParse for a small file fixture set.
7. Only then add watchdog scheduling, semantic retrieval, or automatic routing.

The Feishu Minutes adapter and the optional Baidu adapters are the live remote
paths in this repository. Directory listings, abstract fields, and platform
segments must not be reported as full source-content retrieval. A downloaded
file becomes a source candidate only after local hash and parser evidence are
recorded; it still needs human/evidence review before promotion.
