# Obsidian Knowledge Ingest

一个可替换的、只读优先的采集适配层，为
`obsidian-ai-project-management` 提供 Source Manifest 和候选素材。

目标流程是把不同输入统一成；当前仓库已实现本机扫描与解析到候选这段：

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
| 本机显式目录的只读扫描、哈希、重复/版本记录和 staging manifest | 飞书与百度网盘 OAuth 连接器；本仓库只约定官方连接器的接入边界 |
| Markdown/纯文本解析，以及显式安装的 MarkItDown、LiteParse、Docling 后端入口 | OCRmyPDF 预处理执行、自动 AI 分类、定时 watcher、审批界面和自动写入 Obsidian canonical 笔记 |
| 带来源标识的候选文件与解析结果 schema | 真实云端内容读取、权限 scope 验证和端到端知识应用验收 |

因此当前版本是本机采集与解析适配器，不会扫描整台电脑、登录云盘、自动判断语义类别或直接改写 Obsidian 知识库。云连接器和 AI 分类必须由宿主显式配置，并先用单个只读 canary 验证。

扫描生成的 manifest 和候选会记录源文件名、locator（本机扫描时可能是绝对路径）、时间戳及 SHA-256。产物默认只写到用户指定的 staging 目录且不会自动上传；分享或提交前应检查并按需脱敏这些元数据。

## 上游组件采用边界

已实现适配入口：

- 通用文档解析：`microsoft/markitdown`（MIT），可选安装并有 HTML canary。
- 轻量 PDF 解析：`run-llama/liteparse`（Apache-2.0），可选安装并有 PDF/page-reference canary。
- 高保真解析：`docling-project/docling`（MIT），适配入口已实现，重量级依赖仍需显式安装；当前 CI 不安装或验证它。

后续可复用、但当前未集成：

- 本机文件变化事件：`gorakhargosh/watchdog`（Apache-2.0）。
- 飞书只读连接：官方 `larksuite/lark-openapi-mcp`（MIT）或 `larksuite/oapi-sdk-python`（MIT）。
- 百度网盘只读连接：官方 `baidu-netdisk/mcp`（MIT）。
- 扫描 PDF OCR 预处理：`ocrmypdf/OCRmyPDF`（MPL-2.0）；当前适配器只声明它是预处理步骤，不会执行 OCR。

本仓库实现 manifest、去重、版本链、权限状态、解析 schema、候选边界和回滚友好的 staging。上述连接器与 watcher 尚未打包；OAuth、scope、平台 API 和定时运行仍需后续宿主集成。

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

## 连接器边界

详见 [references/connectors.md](references/connectors.md)。飞书和百度网盘都需要用户 OAuth/应用 scope；“能列目录或返回摘要”不等于已经成功取得所有文件正文。平台授权、隐私策略和失败队列必须保留在 manifest 中。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

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
