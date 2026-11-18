# claimcite 引用核查

[English](README.md) · **中文**

逐条检查文章里的引用：文献是否真的存在，编号是否真的对应文中写的那篇论文，引号里号称原文的句子是否真的出自这篇论文。

越来越多的 AI 文章、健康建议甚至论文，引用的是根本不存在的文献，或者把一篇真论文的 DOI 安到另一篇论文头上。几十条引用，读者没法一条条去查。claimcite 一条命令就能查完。

```console
$ claimcite check examples/sample.md --lang zh
# 引用核查

通过 1 · 警告 2 · 错误 2 · 未核查 0

- **错误** · 第 7 行 · `doi:10.1016/s0140-6736(10)61388-8`
  - 标题与该编号对应的文献不符。登记的标题：Worldwide burden of disease from exposure to
    second-hand smoke: a retrospective analysis of data from 192 countries.
  - 年份不一致：文中 2021，登记 2011
- **错误** · 第 9 行 · `doi:10.1001/jama.2022.99999`
  - 编号不存在
- **警告** · 第 11 行 · `pmid:33237064`
  - 研究类型（PubMed 标注）：系统综述 / 荟萃分析
  - 引文在摘要中没有找到（可能在全文里）：「calcium and vitamin D supplementation cured
    osteoporosis in all women」
...
```

## 目前能查什么

| 检查 | 方法 |
|---|---|
| DOI / PMID / arXiv 编号是否存在 | doi.org 句柄接口（覆盖所有 DOI 注册机构）、PubMed、arXiv Atom 接口（含 `10.48550/arXiv.*`） |
| 论文是否已被撤稿 | PubMed `Retracted Publication` / `RetractionIn`、Crossref `updated-by` 与 `relation.is-retracted-by` |
| 文中写的标题和编号对应的文献是否一致 | 先查 PubMed，再查 Crossref 或 arXiv，模糊匹配（自动剥离出版商 `RETRACTED:` 前缀） |
| 年份是否一致（允许差一年：网络首发和印刷出版常差一年） | 同上（arXiv 同时核对首版与更新年份） |
| 引号（`"…"`、`“…”`、`「…」`）里的原文是否出现在摘要中 | PubMed / Crossref / arXiv 摘要；未命中时按需回退核查开放获取全文（PubMed Central、Europe PMC） |
| 研究类型：荟萃分析、随机对照试验、观察性研究、动物研究等 | PubMed 的文献类型和 MeSH 主题词 |
| 网页链接能否打开、是否包含引号里的原文 | 抓取页面（遵守 `robots.txt`）、剥离 HTML、规范化空白 |
| 没有 DOI / PMID 的参考文献是否存在 | 用标题先搜 Crossref，再搜 PubMed；找到后和其他论文一样核对 |

支持 Markdown 和纯文本（自动合并软换行段落与跨行缩进参考文献），能识别常见的 `作者 (年份). 标题. 期刊：「引文」. <https://doi.org/…>` 格式、标识符后同句内的引文，也能识别 `doi:…`、裸 DOI、`PMID: …`、`arXiv:…` 及对应链接。同时支持编号引用与作者-年份引用：正文引号后跟着 `[1]`、`[^1]`、`[1, 2]`、`[1-3]`，或无歧义的 `(Author, Year)` / `Author (Year)`，都会拿去和文末参考文献里对应的那一条核对。

编造的引用大多没有 DOI，比如 `Chen, L. (2021). 一个像模像样的标题. Journal of Something, 14(2), 88–97.`。参考文献标题（`## References`、`参考文献`、`Sources` 等）下面的这类条目，以及任何位置的 `[1] …`、`[^1]: …` 条目，会用标题先搜 Crossref、再搜 PubMed。能识别 APA、Harvard、MLA、Vancouver 和 GB/T 7714 格式。搜到的论文和带 DOI 的一样核对；两边都搜不到的报**警告**而不是错误：可能不存在，也可能是标题写法不同。书（大多不在这两个库里）和中文文献保持「未核查」。搜索每秒一次（Crossref 的限制），所以 30 条这样的参考文献第一次要半分钟左右。加 `--no-search` 可以关掉。代码块和行内代码会跳过。中文引文只和中文文本比对：出处的摘要或网页是英文时，注明「无法逐字核对」，不报「没找到」。其他网页链接会抓取核查（遵守 `robots.txt`）：页面能否打开、引号里的原文是否出现在正文中。死链或引文找不到只报**警告**，绝不报错误——网页会变，有些站点也会拦机器人；页面读不了时保持「未核查」。

**暂时做不到：** 检查转述。只有加了引号的文字才会和出处逐字比对；没加引号、只是换句话转述出处的句子，不会被提取，也不会被检查。同样，也不能判断文献是否*支持*文中的说法。这需要语言模型和一套标注好的测试集，见下方路线图。claimcite 只确认出处存在、并且和文中的描述对得上，不构成医疗建议。

## 安装和使用

需要 Python 3.10 及以上，不需要任何 API key。

```bash
pipx install claimcite                   # 或 uv tool install claimcite
claimcite check 文章.md --lang zh     # 中文报告
claimcite check 文章.md --format json # 给脚本和 CI 用
```

只要有一条引用出错，退出码就是 1，可以直接放进 CI 当检查关卡（用 `--fail-on warning|never` 调整严格程度）。设置环境变量 `NCBI_API_KEY` 可以提高 PubMed 的访问频率上限。

### 查询缓存

查询成功的记录会在磁盘上缓存 **30 天**（默认 `~/.cache/claimcite/v1/`，或 `$XDG_CACHE_HOME/claimcite/v1/`），改一行再跑不会把所有请求重发一遍。查询失败不缓存。单次运行想绕过缓存，加 `--no-cache`。

抓取网页时每个网站每秒最多请求一次，所以有几百个链接的文档，第一次运行要几分钟。只想核查 DOI 和 PMID，加 `--no-web`。PDF 里的引文暂时不核对。

### 在 CI 里用：GitHub Action

```yaml
# .github/workflows/citations.yml
on: [push, pull_request]
jobs:
  claimcite:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: wasabihu/claimcite@v0.4.1
        with:
          files: "docs/**/*.md"  # 默认：所有 Markdown 文件
          fail-on: error         # 也可以是 warning、never
          lang: zh               # 中文报告
          web: "true"            # 写 "false" 只查 DOI 和 PMID
          search: "true"         # 写 "false" 不按标题搜索没有 DOI 的参考文献
```

报告也会显示在这次运行的摘要（job summary）里。支持 Linux 和 macOS 运行器。

### 每次提交前查：pre-commit

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/wasabihu/claimcite
    rev: v0.4.1
    hooks:
      - id: claimcite
        # args: [--no-web]   # 更快：不抓普通网页链接
```

改动过的 Markdown 文件都会查一遍，有引用报错误时提交会被拦下。命令行也可以一次查多个文件：`claimcite check docs/*.md`，或者整个目录：`claimcite check docs/`。

### MCP 服务与 Agent Skill

`claimcite` 内置零第三方依赖的 stdio [MCP](https://modelcontextprotocol.io/) 服务端（`claimcite mcp`），可在 Claude Code、Codex、Cursor、Antigravity 中直接作为只读引文核查工具调用（暴露 `check_citations` 和 `check_files`）：

```json
{
  "mcpServers": {
    "claimcite": {
      "command": "uvx",
      "args": ["claimcite", "mcp"]
    }
  }
}
```

配套的 Agent 技能说明见 [`skills/claimcite/SKILL.md`](skills/claimcite/SKILL.md)。

### 离线评测基准

运行基于真实录制 API 响应的 22 条离线基准集（无需联网）：

```bash
uv run python benchmarks/run.py
```

详情与类别覆盖见 [`benchmarks/README.md`](benchmarks/README.md)。

## 路线图

每一项都可以单独完成。挑一项，开个 issue 说一声你在做，就可以开始。

- [x] **网页链接**：检查网页还能打开，并且确实包含引号里的原文
- [x] **没有编号的参考文献**：按标题查找（Crossref、PubMed）
- [x] **撤稿**：基于 Crossref（`updated-by` / `relation`）和 PubMed（`Retracted Publication`）标出已撤稿论文
- [ ] **更多数据源**：OpenAlex、Semantic Scholar、知网/万方元数据（已支持 `arXiv` 与 DataCite `10.48550/arXiv.*`）
- [x] **全文**：在开放获取的全文里找引文（PubMed Central、Europe PMC），不只看摘要
- [ ] **支持度判断**：文献是否支持这句话？做成可插拔的模型判断，优先支持本地模型
- [x] **基准测试集**：内置 [`benchmarks/`](benchmarks/README.md) 22 条真实录制离线评测集（欢迎补充更多公开语料）
- [x] **编号引用**：`[1]`、`[1-3]` 和脚注 `[^1]` 对应到文末参考文献
- [ ] **更多输入格式**：BibTeX，以及 Word / PDF 输入
- [x] **集成**：GitHub Action、pre-commit 钩子、stdio MCP 服务（`claimcite mcp`）、Agent Skill
- [ ] 浏览器插件
- [x] **缓存**：多次运行之间缓存结果（磁盘缓存 30 天，`--no-cache` 可绕过）
- [ ] 批量查询进一步提速

## 姊妹项目

如果你正在 Python 中构建极简、可审计的 AI Agent 运行时，欢迎查看我们的姊妹项目：**[cairnkit](https://github.com/wasabihu/cairnkit)**（其中也提供了集成 `claimcite` 做引用核验的工具示例）。

## 参与贡献

欢迎任何规模的贡献，也欢迎 AI 辅助完成的贡献，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

MIT
