---
name: claimcite
description: Verify citations, DOIs, PMIDs, arXiv IDs, quotes, retractions, and unlinked bibliography entries in Markdown or plain-text drafts with zero external dependencies. Use before delivering articles, health/science writing, research notes, or AI-generated drafts containing references.
---

# claimcite — Citation Verification Skill

Use `claimcite` to verify that every citation in a draft actually exists, matches the title and year written next to it, has not been retracted, and supports any quoted text.

## When to run it

- Before finalizing an AI-drafted article, literature summary, psychology/medical explainer, or technical blog post that cites papers, DOIs, PMIDs, arXiv preprints, or web links.
- Whenever a document ends with a reference list (`## References`, `参考文献`, `Sources`) whose entries have no DOIs (AI-hallucinated references most often appear here).

## How to run it

### Option 1 — CLI (no API key required)

```bash
# Check one or more files (English or Chinese Markdown report)
claimcite check draft.md
claimcite check draft.md --lang zh

# Fast mode: check DOIs, PMIDs, arXiv IDs, and reference lists (skip ordinary web pages)
claimcite check draft.md --no-web

# Machine-readable JSON (good for automated pipelines)
claimcite check draft.md --format json
```

Exit codes:
- `0`: No failing findings under `--fail-on` (default `error`).
- `1`: One or more findings matched `--fail-on`.
- `2`: Input file could not be read or no documents matched.

### Option 2 — MCP server (`claimcite mcp`)

Add `claimcite` as a stdio MCP server in Claude Code, Codex, Cursor, or Antigravity:

```json
{
  "mcpServers": {
    "claimcite": {
      "command": "claimcite",
      "args": ["mcp"]
    }
  }
}
```

Or without global installation using `uvx`:

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

Exposed read-only MCP tools:
- `check_citations(text, format="md", lang="en", web=True, search=True, no_cache=False)`
- `check_files(paths, format="md", lang="en", web=True, search=True, no_cache=False)`

## How to interpret findings and fix the draft

- **Error (`exists: false`, `title_match: "mismatch"`, `retracted: true`)**: High-confidence problem. Replace or remove the nonexistent identifier, fix a swapped DOI/arXiv ID attached to the wrong paper, or remove/replace a retracted paper (unless intentionally citing its retraction notice).
- **Warning (`not_found`, `year_match: false`, `quote_missing`, `web.state: "dead"`)**: Needs human check. An unlinked reference may use an alternate title spelling, a quote may appear in the full text rather than the abstract, or a website may block bots.
- **Not checked (`cjk`, `book`, `search_off`, `web.state: "blocked"`)**: Expected when citing Chinese-language journals (CNKI/Wanfang have no open API), books, or pages disallowed by `robots.txt`. Do **not** treat Chinese paraphrases of English papers as missing quotes (`found_in_abstract: null` is expected).
- **Important limit**: Unquoted paraphrases are not checked by `claimcite`; only direct quotes in `"..."`, `“...”`, or `「...」` are matched against abstracts or pages.
