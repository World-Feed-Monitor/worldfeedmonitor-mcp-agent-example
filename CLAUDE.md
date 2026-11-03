# CLAUDE.md

A small Python CLI: a news research agent on the World Feed Monitor (WFM) MCP server, with Claude as the LLM.
Public, open-source example code. Keep it compact and easy to read.

## Verified MCP contract

Taken from the WFM server code (laravel/mcp 0.4.1), 2026-11-03. Don't invent tools, arguments or fields; if the
server changes, update this section first.

- Endpoint: `https://api.worldfeedmonitor.com/mcp`, Streamable HTTP, JSON responses. GET returns 405 (no SSE stream).
  Protocol versions accepted: 2025-11-25, 2025-06-18, 2025-03-26. Use the `initialize` handshake (`mode="legacy"`).
- Auth: `Authorization: Basic base64(api_key:api_secret)` on every message, `initialize` included. Same credentials
  as the REST API. Every plan includes MCP, Free too.
- HTTP refusals: 401 (bad credentials, with `WWW-Authenticate`), 403 (inactive account or email not confirmed;
  also a disallowed `Origin` header), 413 / 415 (oversized or non-JSON body). The MCP SDK reports these all as one
  generic `MCPError(-32603)`; `WfmMcp` recovers the status with an httpx2 response hook.
- Metering: only `tools/call` counts, one request per call. `initialize` and `tools/list` are free.
- Tools (all reject unknown arguments; list filters take at most 20 values):
  - `search_news`: keywords, title (≤255), macro_category[], publisher_category[], country[] (ISO-2/3),
    language[] (ISO 639-1), continent[], continent_region[], source[], source_type[], sector[], has_image,
    from_date, to_date (YYYY-MM-DD), sort (relevance|desc|asc), page (1–1000, 25 per page). Last day by default.
  - `get_latest_news`: macro_category[], country[], language[], source[], page. Newest first, last day.
  - `get_sources`: country (2–3 letters), page, per_page (≤100, default 50).
  - `get_coverage`: include[] of countries | languages | macro_categories.
- Search results: `structuredContent` = `{meta, articles[]}`; article = id, title, url, published_at
  (publisher's local time), published_timezone, author, summary (≤500 chars), image, source{key,name,url},
  country{iso_2,iso_3,name}, language{code,name}, topics[{key,name}].
- Tool-level failures are results with `isError: true` and a text message: validation errors, and a spent allowance,
  which also carries `structuredContent` `{window: minute|day|month, limit, retry_after}`.

## Layout and conventions

- `wfm_agent/mcp_client.py`: MCP only, provider-agnostic. `agent.py`: the only Anthropic-specific file.
  `cli.py`: arguments, errors, rendering. Plain functions and dataclasses; no new layers.
- The model cites `[A<n>]` refs, never URLs. URLs are printed from `WfmMcp.articles`, exactly as returned.
- Claude: `claude-opus-5-5` by default, `client.beta.messages.create` with `fallbacks="default"` +
  beta `server-side-fallback-2026-07-01`, `output_config.effort`, automatic `cache_control`. Handle
  `stop_reason` `refusal`, `pause_turn`, `max_tokens`. Append `response.content` whole: the history only grows.
  No forced `tool_choice` (400 on this model).
- Never log or print credentials or the Authorization header. Keep `__repr__` overrides that hide them.
- Tests: `pytest`, `tests/fake_wfm.py` (httpx2 MockTransport speaking WFM's JSON-RPC), `FakeClaude` in
  `conftest.py`. No test may need network or credentials. Run `pytest` and `ruff check . && ruff format --check .`.
- Out of scope: other LLM providers, a web UI, databases, RAG, a proxy that shares one WFM key with others.
- Commits: no AI attribution trailers or footers.
