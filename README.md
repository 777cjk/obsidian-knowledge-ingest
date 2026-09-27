# Obsidian Knowledge Ingest

一个可替换的、只读优先的采集适配层，为
`obsidian-ai-project-management` 提供 Source Manifest 和候选素材。

目标流程是把不同输入统一到同一个、可审计的来源记录与候选格式：

```text
本机目录 / 飞书 / 百度网盘
        -> source manifest
        -> hash、增量、重复、版本历史、权限状态
        -> 解析器（可选）
        -> AI 分类候选
        -> Obsidian 来源层
        -> 人工/证据复核
```

## 当前交付边界

| 已实现 | 仍需宿主或后续适配 |
|---|---|
| 本机显式目录扫描；明确 `minute_token` 的飞书妙记逐字稿只读导入；百度网盘目录只读 MCP 适配；哈希、版本记录和 staging 候选 | 飞书云文档/Wiki 与百度网盘 OAuth 配置，以及百度完整正文读取 |
| Markdown/纯文本解析，以及显式安装的 MarkItDown、LiteParse、Docling 后端入口 | OCRmyPDF 预处理执行、自动 AI 分类、定时 watcher、审批界面和自动写入 Obsidian canonical 笔记 |
| 带来源标识的候选文件与解析结果 schema；飞书妙记接入已做单条真实只读 canary | 百度真实云端内容读取；飞书云文档/Wiki scope 验证；端到端知识应用验收 |

因此当前版本覆盖本机显式目录、飞书妙记逐字稿和百度网盘显式目录的只读列表/平台文本片段，不会扫描整台电脑、自动登录云盘、自动判断语义类别或直接改写 Obsidian canonical 笔记。百度结果会明确区分 `platform_segments`、`abstract_only` 和 `metadata_only`，并将完整性标记为未验证；它不把平台摘要当作原文件全文。百度适配器仍需宿主配置 OAuth，再以单个已知目录 canary 验证实际返回内容。

扫描生成的 manifest 和候选会记录源文件名、locator（本机扫描时可能是绝对路径）、时间戳及 SHA-256。产物默认只写到用户指定的 staging 目录且不会自动上传；分享或提交前应检查并按需脱敏这些元数据。

## 上游组件采用边界

已实现适配入口：

- 通用文档解析：`microsoft/markitdown`（MIT），可选安装并有 HTML canary。
- 轻量 PDF 解析：`run-llama/liteparse`（Apache-2.0），可选安装并有 PDF/page-reference canary。
- 高保真解析：`docling-project/docling`（MIT），适配入口已实现，重量级依赖仍需显式安装；当前 CI 不安装或验证它。

后续可复用、但当前未集成：

- 本机文件变化事件：`gorakhargosh/watchdog`（Apache-2.0）。
- 飞书云文档/Wiki：后续可接官方 `larksuite/lark-openapi-mcp` 或 `larksuite/oapi-sdk-python`；通用文档正文读取当前未集成。
- 百度网盘：已复用 `baidu-netdisk/mcp`（MIT）的 SSE MCP 契约；`scripts/baidu_netdisk_ingest.py` 只允许 `file_list`/`file_doc_list`，不暴露上传、删除、移动、分享等写工具。上游返回的是可能为空的分段内容/摘要，完整正文仍需单文件 canary 证明。
- 扫描 PDF OCR 预处理：`ocrmypdf/OCRmyPDF`（MPL-2.0）；当前适配器只声明它是预处理步骤，不会执行 OCR。

本仓库实现 manifest、去重、版本链、权限状态、解析 schema、候选边界和回滚友好的 staging。飞书妙记连接器通过宿主已安装的 `lark-cli` 按 token 按需调用；百度适配器通过可选的 `mcp==1.6.0` SSE 客户端按显式目录调用。

## 本机目录扫描

扫描只写到 staging，不直接修改 Obsidian canonical 文件：

```bash
python3 scripts/manifest_scan.py scan \
  --root /path/to/source \
  --label personal-files \
  --manifest /path/to/staging/manifest.json \
  --output-dir /path/to/staging \
  --emit-candidates
```

输出包括：

- `manifest.json`：文件身份、大小、mtime、SHA-256、权限、当前版本和历史版本；
- `candidates/*.md`：带 `source_refs` 的待复核候选，不是长期知识资产；
- 命令行 JSON 摘要：`new / modified / unchanged / deleted / duplicate`。

每个当前条目都有稳定的 `source_id` 和版本化的 `revision_id`。文件修改会把上一版放入 `history`，并由 `supersedes` 指向上一版；同内容的不同路径也会生成不同候选文件，不会互相覆盖。

默认不读取文件正文、不上传文件、不写 Obsidian、不删除源文件。解析和 AI 提取应在权限确认后作为下一阶段 adapter 执行。

## 飞书妙记逐字稿导入

先用已登录的官方 `lark-cli` 按关键词找到本人可读取的妙记 token，再显式导入一条：

```bash
lark-cli minutes +search --as user --query "项目关键词" --owner-ids me --page-size 5 --json
python3 scripts/feishu_minutes_ingest.py \
  --minute-token <minute-token> \
  --staging-dir /path/to/private/staging
```

导入命令先检查 `minutes:minutes.artifacts:read`，再调用只读的 `minutes +detail --transcript`。它不会执行搜索、翻页、申请权限或调用飞书写入 API。原逐字稿、Source Manifest 和 `classification_status: pending` 的候选都写在 staging；摘要只返回标题、哈希、解析状态和候选路径，不打印逐字稿。候选必须经过人工/证据复核后才能进入知识资产层。

逐字稿含用户原始内容。staging 必须是当前用户拥有、权限为 `0700` 的私有目录；导入器会用 `0600` 保存文件，并在 macOS/Linux 对同一 staging 的导入加锁串行。来源 URL 会移除 query 和 fragment。分享或提交前仍需检查候选和 manifest 的来源 locator。当前飞书云文档/Wiki 读取仍未实现，妙记权限不代表拥有云文档权限。

## 百度网盘只读目录导入

百度适配器通过上游 `baidu-netdisk/mcp` 的 SSE 服务读取一个显式目录。它需要宿主先配置 `BAIDU_NETDISK_ACCESS_TOKEN`，默认只调用 `file_list`；文档目录可显式切换到 `file_doc_list`。每次导入限制页数和文件数，输出写入私有 staging，不写入 Obsidian 正式笔记：

~~~bash
scripts/install.sh --with baidu-mcp
BAIDU_NETDISK_ACCESS_TOKEN='从百度授权页取得的 token' \
  .venv/bin/python scripts/baidu_netdisk_ingest.py \
  --path '/AI' \
  --tool file_list \
  --staging-dir '/private/path/baidu-staging'
~~~

适配器只开放 `file_list` 和 `file_doc_list` 两个读工具。它保留百度 `fsid`/路径/远端 MD5、原始响应快照哈希、版本链和权限状态；含平台分段或摘要时生成待审候选，但 frontmatter 会写明 `content_completeness: unverified_platform_segments` 或 `abstract_only`。没有正文或摘要的文件只进入 manifest 的 `metadata_only` 条目，不生成知识候选。不要把目录列表、摘要或平台分段报告成已获取原文件全文。

## 连接器边界

详见 [references/connectors.md](references/connectors.md)。飞书妙记使用现有 user OAuth 与 artifacts scope；飞书云文档/Wiki、百度网盘仍需要各自的 OAuth/应用 scope。“能列目录或返回摘要”不等于已经成功取得所有文件正文。平台授权、隐私策略和失败队列必须保留在 manifest 中。

网页资料可由 [Obsidian Web Clipper](https://github.com/obsidianmd/obsidian-clipper)
经人工选择后剪藏为 Markdown；它是人工采集入口，不会扫描本机或自动分类。
Vault MCP 是另一种可选的读取接口，但其上游默认允许整库读写；接入前应启用
`OBSIDIAN_READ_ONLY=true` 并配置最小 `OBSIDIAN_READ_PATHS`。本 Skill 的 canonical
写回仍须经过宿主 checkpoint。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

先检查本机连接器依赖和授权状态（不读取远端文件、不打印 token）：

```bash
python3 scripts/source_preflight.py
python3 scripts/source_preflight.py --json
```

需要进入真实百度 canary 或飞书 Docs/Wiki canary 前，用对应 Gate 让缺失授权变成失败：

```bash
python3 scripts/source_preflight.py --require-baidu
python3 scripts/source_preflight.py --require-feishu-docs
```

默认输出的 `local_ready` 只表示本机 Python 检查通过；带 `--require-*` 输出 `ready` 才表示所选授权 Gate 已满足。两者都不等于正文读取成功。正文仍须按连接器命令做单目录/单文档 canary，并检查 manifest 的完整性字段。

要重复安装并运行本地 canary，使用 [DEPLOYMENT.md](DEPLOYMENT.md) 中的
`scripts/install.sh` 和 `scripts/verify.sh`。默认安装只使用 Python 标准库；
MarkItDown、LiteParse、Docling 和 OCRmyPDF 必须通过 `--with` 显式加入。
`--with parser-lite` 会按仓库的 pinned requirements 安装 MarkItDown 0.1.8
和 LiteParse 2.14.7；Docling 和 OCRmyPDF 仍是重量级 opt-in 依赖，当前不锁版本。

完成一次真实采集后，再将来源卡通过 `obsidian-ai-project-management` 的候选→复核→应用流程写入 Obsidian。

## 解析器选择

- 首选 Docling：需要版面、页码、表格和 OCR 保真度时使用；它是重量级可选依赖，不应成为 Skill 的强制安装依赖。
- 轻量优先 MarkItDown：纯文本、Office 和简单 PDF 先走它；失败或版面要求更高时再转 Docling。
- 扫描 PDF：先由 OCRmyPDF 生成可搜索副本，再交给 Docling；保留独立进程和 MPL-2.0 归属。
- LiteParse：适合低成本 PDF 抽取；输出仍需保留页码、原文哈希和 parser 版本。

解析器统一返回版本化 JSON，可用同一入口检查和运行：

```bash
python scripts/parser_adapter.py backends
python scripts/parser_adapter.py parse ./document.pdf --backend auto
python scripts/parser_adapter.py parse ./document.pdf --backend docling
```

安装轻量解析器到隔离环境：`scripts/install.sh --with parser-lite`。也可直接
复用 [requirements-parser-lite.txt](requirements-parser-lite.txt)。本项目的
`.venv` 已验证 MarkItDown 0.1.8 和 LiteParse 2.14.7；Docling 和 OCR worker
保持可选，避免让轻量采集路径承担重型模型与系统依赖。
