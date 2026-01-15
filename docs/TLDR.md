### Repository Description (The One-Liner)

> A context-efficient CLI and Claude Code plugin for LangSmith that replaces heavy MCP servers with lightweight, on-demand skills.

---

### Project Overview (The Paragraph)

**`langsmith-cli`** is a dual-purpose tool designed to be the definitive interface for LangSmith. It serves as a polished, "Simon Willison-style" terminal utility for human developers and a zero-overhead plugin for Claude Code. By offloading logic to a lazy-loaded CLI, it eliminates the massive context cost of standard MCP servers while providing full access to projects, traces, and datasets in a machine-readable format.

### Key Objectives

* **Context Efficiency:** Replace 5,000+ tokens of MCP schema with a <200 token skill definition.
* **Dual UX:** Beautiful `rich` tables for humans; strict, sparse JSON for agents.
* **Feature Parity:** Full support for LangSmith primitives (Projects, Runs, Datasets) without the Docker overhead.


---

## 5. 🚀 Execution Strategy: Compatibility & Superiority

**Objective:** The agent must strictly follow a "Embrace, Extend, Extinguish" strategy regarding the legacy MCP server. We do not just want a different tool; we want the *obvious* choice.

### Phase A: Achieve Compatibility (The Foundation)

*Goal: Ensure the CLI does everything the MCP server currently does, so users lose nothing by switching.*

1. **The "Audit & Clone" Protocol:**
* **Action:** Systematically read the `services/tools/` directory of the [`langchain-ai/langsmith-mcp-server`](https://www.google.com/search?q=%5Bhttps://github.com/langchain-ai/langsmith-mcp-server%5D(https://github.com/langchain-ai/langsmith-mcp-server)) repository.
* **Requirement:** For every exposed tool (e.g., `list_datasets`, `read_run`), create a corresponding CLI command (`datasets list`, `runs get`).
* **Argument Parity:** If the MCP tool `search_traces` accepts a `filter` string, our CLI command `runs search` must accept a `--filter` flag that works identically.


2. **Validation:**
* Create a "test suite" of 5 common MCP queries.
* Verify that `langsmith-cli` returns the same data (or better structured data) for all 5 scenarios.



### Phase B: Achieve Superiority (The Evolution)

*Goal: Surpass the MCP server by leveraging the flexibility of a CLI and the specific needs of LLM Agents.*

1. **Context Superiority (The "Lazy" Architecture):**
* **Problem:** The MCP server forces 100% of its schema into the context window at startup.
* **Solution:** The CLI is invisible until called. We will implement granular `SKILL.md` definitions that only expose high-level intent, saving ~95% of context tokens compared to the MCP.


2. **UX Superiority (The "Simon Willison" Standard):**
* **Human UX:** Implement `rich` library formatting. When a human types `langsmith projects list`, they see a color-coded table, not raw JSON.
* **Agent UX:** Implement a global `--json` flag that strips all formatting and returns dense, machine-parsable JSON to minimize token waste.
* **Developer Experience:** Add "convenience flags" that the MCP lacks, such as `--last-error` (fetch the most recent failed run) or `--open` (open the trace in the browser).


3. **Performance Superiority:**
* Direct SDK usage removes the HTTP/JSON-RPC serialization overhead, resulting in faster execution for high-volume fetches.



### Phase C: The "Plugin Marketplace" Delivery

*Goal: The repository is not just a script; it is a distributable product.*

* **Repo as a Plugin Marketplace:** The repository root will be structured as a **Claude Plugin Marketplace** containing a single plugin.
* It must contain the `.claude-plugin/marketplace.json` manifest (for installation).
* It must contain the `.claude-plugin/plugin.json` manifest (for the plugin itself).
* It must house the `skills/` directory containing the "Bridge Skills" (the markdown files that teach Claude how to use the CLI).


* **Artifacts:**
1. **The Core CLI:** A robust Python package (`pip install .`).
2. **The Skill Definitions:** Optimized Markdown prompts.
3. **The Installer:** A seamless setup process (`claude plugin install ...`) that handles dependencies automatically.
