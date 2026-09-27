# Connector Matrix

| 输入 | 直接复用 | 适配层职责 | 当前限制 |
|---|---|---|---|
| 本机文件夹 | watchdog + 本仓库扫描器 | allowlist、增量 manifest、哈希、去重、版本、候选 staging | 默认只读 metadata；解析器另配 |
| 飞书文档/Wiki | `larksuite/lark-openapi-mcp` 或官方 Python SDK | 只读 fetch、分页、external_id、权限和内容 locator 转换为 SourceRecord | 官方 MCP README 标注 Beta；部分文件上传/下载和直接编辑不支持 |
| 百度网盘 | `baidu-netdisk/mcp` | 只读 list/meta/content/abstract 统一为 SourceRecord；记录 OAuth 和 scope | 个人用户体验和企业开发者门槛会变化；不能假设所有二进制都能下载 |
| PDF/DOCX/PPTX/XLSX/图片/音频 | Docling；LiteParse；MarkItDown fallback | parser、parser_version、outline、assets、失败原因 | 高保真解析可能占用 CPU/GPU；解析结果仍是候选 |
| 扫描 PDF | OCRmyPDF（独立可选 worker）→ Docling | 记录 OCR 输出哈希、页码、语言和失败原因 | MPL-2.0、Tesseract/Ghostscript 依赖，不进入默认 Skill 安装 |

## Write boundary

Connector output first lands in a local manifest and staging directory. It must
not write a canonical Obsidian project card or approved knowledge asset. A host
may promote a candidate only after source references, evidence status, privacy
scope, and human/explicit evidence review are present.

## Recommended order

1. Start with one local directory and `--emit-candidates`.
2. Add a read-only Feishu scope and one known document/Wiki node.
3. Add a read-only Baidu scope and one known folder.
4. Add Docling or LiteParse for a small file fixture set.
5. Only then add watchdog scheduling, semantic retrieval, or automatic routing.

The current adapter intentionally stops before parser execution and remote
OAuth calls. That boundary keeps the local canary reproducible and prevents a
directory listing, summary field, or connector health check from being
reported as full source-content retrieval.
