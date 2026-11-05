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
| DOI / PMID 是否存在 | doi.org 句柄接口（覆盖所有 DOI 注册机构）、PubMed |
| 文中写的标题和编号对应的文献是否一致 | 先查 PubMed，再查 Crossref，模糊匹配 |
| 年份是否一致（允许差一年：网络首发和印刷出版常差一年） | 同上 |
| 引号（`"…"`、`“…”`、`「…」`）里的原文是否出现在摘要中 | PubMed / Crossref 摘要 |
| 研究类型：荟萃分析、随机对照试验、观察性研究、动物研究等 | PubMed 的文献类型和 MeSH 主题词 |
| 网页链接能否打开、是否包含引号里的原文 | 抓取页面（遵守 `robots.txt`）、剥离 HTML、规范化空白 |

支持 Markdown 和纯文本，能识别常见的 `作者 (年份). 标题. 期刊：「引文」. <https://doi.org/…>` 格式，也能识别 `doi:…`、裸 DOI、`PMID: …` 和 PubMed 链接。其他网页链接会抓取核查（遵守 `robots.txt`）：页面能否打开、引号里的原文是否出现在正文中。死链或引文找不到只报**警告**，绝不报错误——网页会变，有些站点也会拦机器人；页面读不了时保持「未核查」。

**暂时做不到：** 判断文献是否*支持*文中的说法。这需要语言模型和一套标注好的测试集，见下方路线图。claimcite 只确认出处存在、并且和文中的描述对得上，不构成医疗建议。

## 安装和使用

需要 Python 3.10 及以上，不需要任何 API key。

```bash
uv tool install git+https://github.com/wasabihu/claimcite   # 或 pipx install git+...
claimcite check 文章.md --lang zh     # 中文报告
claimcite check 文章.md --format json # 给脚本和 CI 用
```

只要有一条引用出错，退出码就是 1，可以直接放进 CI 当检查关卡（用 `--fail-on warning|never` 调整严格程度）。设置环境变量 `NCBI_API_KEY` 可以提高 PubMed 的访问频率上限。

### 查询缓存

查询成功的记录会在磁盘上缓存 **30 天**（默认 `~/.cache/claimcite/v1/`，或 `$XDG_CACHE_HOME/claimcite/v1/`），改一行再跑不会把所有请求重发一遍。查询失败不缓存。单次运行想绕过缓存，加 `--no-cache`。

## 路线图

每一项都可以单独完成。挑一项，开个 issue 说一声你在做，就可以开始。

- [x] **网页链接**：检查网页还能打开，并且确实包含引号里的原文
- [ ] **更多数据源**：OpenAlex、Semantic Scholar、arXiv、知网/万方元数据、DataCite
- [ ] **撤稿**：标出已撤稿的论文（Crossref / Retraction Watch 数据）
- [ ] **全文**：在开放获取的全文里找引文（PubMed Central、Europe PMC），不只看摘要
- [ ] **支持度判断**：文献是否支持这句话？做成可插拔的模型判断，优先支持本地模型
- [ ] **基准测试集**：公开一套标注好的真实引用和编造引用，用来衡量每项检查的准确度
- [ ] **更多引用格式**：编号引用 `[1]`、脚注 `[^1]`、BibTeX，以及 Word / PDF 输入
- [ ] **集成**：GitHub Action、pre-commit 钩子、浏览器插件
- [x] **缓存**：多次运行之间缓存结果（磁盘缓存 30 天，`--no-cache` 可绕过）
- [ ] 批量查询进一步提速

## 参与贡献

欢迎任何规模的贡献，也欢迎 AI 辅助完成的贡献，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

MIT
