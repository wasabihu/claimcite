# Contributing · 参与贡献

[English](#english) · [中文](#中文)

## English

1. **Pick something.** Roadmap items in the README and issues labelled `good first issue` are ready to start.
   Comment on the issue so two people don't do the same work.
2. **Set up.**
   ```bash
   git clone https://github.com/wasabihu/claimcite && cd claimcite
   uv sync
   uv run pytest && uv run ruff check src tests
   ```
3. **Tests run offline.** Every network response used in tests is a file in `tests/fixtures/`, recorded from
   the real service. When you add a registry, record real responses (including "not found") and route them in
   `tests/conftest.py`.
4. **Bug fixes start with a failing test** that reproduces the bug, then the fix.
5. **Keep false alarms rare.** A checker that cries wolf gets ignored. If a check is uncertain, report a
   warning or "not checked" rather than an error.
6. **Be polite to free services.** Batch requests, respect rate limits, send the claimcite User-Agent.

**AI-assisted contributions are welcome.** Say so in the pull request, and make sure you have read and run
what you submit. You are responsible for it, not the tool.
AI agents: read [AGENTS.md](AGENTS.md) first.

## 中文

1. **选一项来做。** README 里的路线图和带 `good first issue` 标签的 issue 都可以直接开始做。先在 issue 下留言说你在做，免得两个人做重复了。
2. **搭环境。**
   ```bash
   git clone https://github.com/wasabihu/claimcite && cd claimcite
   uv sync
   uv run pytest && uv run ruff check src tests
   ```
3. **测试不联网。** 测试用到的每个网络响应都是 `tests/fixtures/` 里的文件，录自真实服务。新增数据源时，把真实响应（包括"查不到"的情况）录下来，并在 `tests/conftest.py` 里配好路由。
4. **修 bug 先写测试。** 先写一个能复现 bug、会失败的测试，再修。
5. **少误报。** 一个老是误报的检查工具，最后没人会理会。不确定的情况报"警告"或"未核查"，不要报"错误"。
6. **善待免费服务。** 尽量批量请求，遵守访问频率限制，请求时带上 claimcite 的 User-Agent。

**欢迎 AI 辅助完成的贡献。** 在 PR 里说明，并确保提交的内容你自己读过、跑过。对内容负责的是你，不是工具。AI 代理请先读 [AGENTS.md](AGENTS.md)。
