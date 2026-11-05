# Instructions for AI agents · AI 代理须知

[English](#english) · [中文](#中文)

These rules apply to any AI coding agent working on claimcite. Human contributors: see [CONTRIBUTING.md](CONTRIBUTING.md), which these rules extend.

## English

### Think from first principles

Start from what is true, not from habit or what other projects do.

- **Start from the goal.** claimcite exists so a reader can trust that each citation is real, points to the paper the text names, and contains the quoted words. Judge every change by whether it makes that verdict more correct, or easier to get.
- **Get facts by running things.** Read the code, run it, and look at what the real service returns before you decide. Do not guess a field's meaning from its name. For example, Crossref's `update-to` sits on the retraction notice, not on the retracted paper. Only the real record shows that.
- **Question the starting point.** Issues, labels, earlier reviews and this file can be wrong or out of date. Check that the problem is real and still unsolved before you fix it.
- **Build the simplest fix that the facts require.** Do not add layers, options or dependencies the problem does not need.
- **Show your reasoning.** In the PR, say which facts you relied on (a recorded response, a failing test, a measurement) and how they lead to the change.

### Check before you commit

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

Both must pass. Tests run offline in well under a second.

### Where things live

| File | Job |
|---|---|
| `src/claimcite/extract.py` | Find citations (DOI, PMID, URL) and the title, year and quotes written next to them |
| `src/claimcite/sources.py` | Talk to doi.org, PubMed and Crossref; parse their responses into `Record` |
| `src/claimcite/check.py` | Compare what the text says with the `Record`; decide ok / warning / error / unchecked |
| `src/claimcite/report.py` | Markdown (English and Chinese) and JSON output |
| `src/claimcite/net.py` | HTTP with retries and per-host rate limits |
| `src/claimcite/cache.py` | On-disk cache of successful responses |
| `src/claimcite/web.py` | Fetch web pages (respecting robots.txt) and look for quoted text |
| `tests/fixtures/` | Real responses recorded from the services |

### Rules

1. **Bug fixes start with a failing test.** Write the test, watch it fail, then fix.
2. **Fixtures are recorded, not invented.** Save real responses from the service, including "not found". In `tests/conftest.py`, route only the exact identifiers a test uses. A broad pattern such as a whole DOI prefix makes unrelated tests pass by accident.
3. **Test the path you changed.** If a paper is found in PubMed first, a test through `check_reference` never reaches the Crossref parser. Test the parser directly as well.
4. **Assertions must be able to fail.** `assert a or b` that is true in the broken case proves nothing.
5. **False alarms are worse than misses.** Report `error` only when the registry answer is definite. If you are unsure, report `warning`. If the lookup itself failed, report `unchecked` and fill in `Finding.error`. Never report "does not exist" because a request failed.
6. **No secrets on disk or in history.** API keys, tokens and email addresses must not appear in the cache, logs, fixtures, test URLs or commits. The cache key strips `api_key`. Keep it that way for any new credential.
7. **Optional parts must not crash the run.** If the cache, or a single source, cannot be used, print one line to stderr and continue.
8. **Be polite to free services.** Batch requests, go through `net.http_get` for its rate limiting, and send the claimcite User-Agent. Do not put anyone's contact details in requests.
9. **Standard library only at runtime**, Python 3.10+. Discuss a new runtime dependency in an issue first. Dev tools go in the `dev` group.
10. **Both languages.** Every user-facing message in `report.py` needs English and Chinese. README changes go into both `README.md` and `README.zh-CN.md`.
11. **No traces of your own environment.** Do not leave comments, workarounds or paths that only make sense on the machine you ran on.
12. **One issue per pull request.** Comment on the issue before you start. Say in the PR that it is AI-assisted.
13. **Commit identity.** Do not commit with a personal email address that should stay private. Use a GitHub noreply address.
14. **Run a new check on a real document before calling it done.** Read every warning it gives and decide whether the document or the tool is wrong. Offline tests passed for the first web check, but a run on a real 34-chapter guide found 7 false warnings out of 15 in one chapter (curly vs straight quotes, extra spaces), plus a false warning on every PDF with a quote. Note the run time, too.

## 中文

### 第一性原理

从事实出发推导，不照搬习惯，也不照搬别的项目的做法。

- **先问目标。** claimcite 的目标是让读者能相信每条引用：论文真实存在，就是文中说的那篇，引号里的话确实出自它。每个改动都看它是否让这个判断更准确，或更容易得到。
- **事实靠运行得到。** 动手前先读代码、跑代码，看真实服务返回什么。不要凭字段名猜意思。比如 Crossref 的 `update-to` 在撤稿通知上，不在被撤的论文上，只有看真实记录才知道。
- **质疑出发点。** issue、标签、之前的 review，包括这份文件，都可能是错的或过时的。修之前先确认问题真实存在、还没人解决。
- **按事实需要做最简单的方案。** 问题不需要的抽象层、选项和依赖都不要加。
- **说清推理。** 在 PR 里写明你依据的事实（录下来的响应、失败的测试、实测数据），以及它们怎么推出这个改动。

### 提交前检查

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

两项都必须通过。测试不联网，一秒内跑完。

### 代码分工

| 文件 | 负责 |
|---|---|
| `src/claimcite/extract.py` | 找出引用（DOI、PMID、网址），以及旁边写的标题、年份和引文 |
| `src/claimcite/sources.py` | 访问 doi.org、PubMed、Crossref，把返回结果解析成 `Record` |
| `src/claimcite/check.py` | 比对文中写的和 `Record`，判定通过、警告、错误或未核查 |
| `src/claimcite/report.py` | Markdown（中英文）和 JSON 输出 |
| `src/claimcite/net.py` | 带重试和按站点限速的网络请求 |
| `src/claimcite/cache.py` | 把查询成功的结果缓存到磁盘 |
| `src/claimcite/web.py` | 抓取网页（遵守 robots.txt），查找引文 |
| `tests/fixtures/` | 从真实服务录下来的响应 |

### 规则

1. **修 bug 先写测试。** 先写测试，确认它失败，再修。
2. **测试数据要录，不要编。** 保存服务的真实响应，包括"查不到"的情况。在 `tests/conftest.py` 里只为测试用到的具体编号配路由。范围太宽的规则（比如整个 DOI 前缀）会让无关的测试碰巧通过。
3. **测到你改的那条路径。** 如果论文先在 PubMed 查到，经过 `check_reference` 的测试就走不到 Crossref 的解析代码，要对解析函数单独写测试。
4. **断言必须有可能失败。** 像 `assert a or b` 这样在代码出错时也成立的断言，什么也证明不了。
5. **误报比漏报更糟。** 只有数据源给出确定答案时才报"错误"。不确定就报"警告"。查询本身失败时报"未核查"，并填写 `Finding.error`。不能因为请求失败就报"编号不存在"。
6. **密钥不落盘，也不进历史。** API key、token、邮箱不能出现在缓存、日志、测试数据、测试网址或提交里。缓存键会去掉 `api_key`，以后新增任何凭据也要这样处理。
7. **可选部分不能让整个程序崩溃。** 缓存或某个数据源用不了时，往 stderr 打一行提示，然后继续。
8. **善待免费服务。** 批量请求，通过 `net.http_get` 发请求以遵守限速，并带上 claimcite 的 User-Agent。请求里不要放任何人的联系方式。
9. **运行时只用标准库**，Python 3.10 及以上。要加运行时依赖，先开 issue 讨论。开发工具放进 `dev` 组。
10. **中英双语。** `report.py` 里每条面向用户的提示都要有中文和英文。改 README 要同时改 `README.md` 和 `README.zh-CN.md`。
11. **不留下你自己运行环境的痕迹。** 只在你那台机器上才说得通的注释、绕路写法和路径，都不要提交。
12. **一个 PR 只做一个 issue。** 开工前先在 issue 下留言。在 PR 里说明是 AI 辅助完成的。
13. **提交身份。** 不要用需要保密的个人邮箱提交，用 GitHub 的 noreply 邮箱。
14. **新的检查先在真实文档上跑一遍再算完成。** 逐条看它报的警告，判断是文档错了还是工具错了。网页链接检查第一版的离线测试全部通过，但在一本 34 章的真实指南上一跑，一章的 15 条警告里有 7 条是误报（直引号和弯引号、多出的空格），带引文的 PDF 也每条都误报。顺便记下运行时间。
