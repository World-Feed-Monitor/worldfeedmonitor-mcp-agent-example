# World Feed Monitor · MCP research agent example

A command-line **news research agent**. Ask it a question in plain words. Claude searches the world's news through the [World Feed Monitor](https://worldfeedmonitor.com) MCP server, then writes a short report in which every claim cites the article it came from.

```text
$ wfm-research "What are the latest AI developments in Japan?"

Japanese coverage this week centres on two model releases. Example Labs published a
Japanese-language model aimed at government use [A1], and a second lab announced
an open-weights release on 2 November [A3]. Funding coverage is thin: one report puts
the sector's third-quarter investment at a record [A4].

What the coverage does not show
No article in the results reports regulatory changes or company earnings.

Sources
  [A1] …headline as published…
       Example Shimbun, Japan · 2026-11-03 09:12 Asia/Tokyo
       https://…the original article…
  …

3 WFM tool call(s), 61 article(s) read.
```

*(Illustrative output. Answers come from whatever WFM returns at the time you ask.)*

You don't build or maintain any news ingestion. The agent discovers WFM's tools over the Model Context Protocol, and the model decides which ones to call and with what filters.

## What it demonstrates

- **Connecting to a remote MCP server** over Streamable HTTP with an `Authorization` header, using the official [`mcp`](https://pypi.org/project/mcp/) Python SDK.
- **Tool discovery.** The tools and their JSON schemas come from the server at run time. Nothing about them is hard-coded.
- **Tool invocation by an LLM.** Claude, through the official [`anthropic`](https://pypi.org/project/anthropic/) SDK, picks the tool, the countries, the language of the keywords and the dates.
- **Answers you can check:**
  - each claim cites a specific article as `[A3]`;
  - links are filled in from what WFM actually returned, never typed by the model;
  - a citation with no article behind it is flagged;
  - gaps in the coverage are stated, not filled in.
- **Careful with your quota:** a cap on tool calls, and clear handling of every failure (missing configuration, rejected credentials, inactive account, spent allowance, no results).

## Requirements

- Python 3.10 or later
- A World Feed Monitor account. **Free is enough**: every plan includes MCP access, and no card is needed.
- An Anthropic API key (paid usage, see [Costs](#costs))

## Install

```bash
git clone https://github.com/World-Feed-Monitor/worldfeedmonitor-mcp-agent-example.git
cd worldfeedmonitor-mcp-agent-example
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Configure

### 1. A free World Feed Monitor account

Sign up at [worldfeedmonitor.com/register](https://worldfeedmonitor.com/register) with an email address, GitHub or Google, then **confirm your email address**: the API and the MCP server answer only once it is confirmed.

### 2. MCP credentials

The MCP server uses the **same credentials as the REST API**. In the dashboard, open **API Credentials** and create one. You get:

- an **API key**;
- an **API secret**, shown **once**. Copy it straight away.

### 3. An Anthropic API key

Create one at [console.anthropic.com](https://console.anthropic.com/).

### 4. `.env`

```bash
cp .env.example .env
```

```bash
WFM_API_KEY=your-api-key
WFM_API_SECRET=your-api-secret
ANTHROPIC_API_KEY=your-anthropic-api-key
```

| Variable | Required | Default |
| --- | --- | --- |
| `WFM_API_KEY`, `WFM_API_SECRET` | yes | |
| `ANTHROPIC_API_KEY` | yes* | |
| `WFM_MCP_URL` | no | `https://api.worldfeedmonitor.com/mcp` |
| `ANTHROPIC_MODEL` | no | `claude-opus-5-5` (Claude Opus 5.5) |

\* Or any other credential source the Anthropic SDK supports.

`.env` is ignored by git. Values already set in your shell take precedence.

## Run

```bash
wfm-research "What are the latest AI developments in Japan?"
wfm-research "Compare recent renewable energy coverage in Germany and Brazil."
wfm-research "What are African publishers reporting about energy infrastructure?"
wfm-research "Summarize semiconductor news from Taiwan over the last week."
```

| Option | Effect |
| --- | --- |
| `--max-tool-calls N` | The most WFM tool calls to spend on one question (default 6) |
| `--verbose` | Show each tool call and its arguments as it is made. Credentials are never printed. |

Exit codes: `0` answered, `1` failed, `2` configuration, `3` credentials or account, `4` rate limit.

## How it works

```text
question ──► Claude ──tool_use──► wfm_agent ──tools/call──► api.worldfeedmonitor.com/mcp
               ▲                      │                              │
               └──── tool_result ◄────┴───── articles (A1, A2, …) ◄──┘
```

1. **Connect.** `WfmMcp` opens a Streamable HTTP session with `Authorization: Basic base64(api_key:api_secret)` on every request, the handshake included. WFM uses the classic `initialize` handshake, so the client is set to `mode="legacy"`.
2. **Discover.** `tools/list` returns WFM's four tools with their JSON schemas. They're passed to Claude unchanged, and discovering them costs nothing.
3. **Decide.** Claude reads the question and calls, for example, `search_news(country=["JP"], keywords="人工知能", from_date="2026-10-27")`.
4. **Invoke.** `tools/call` runs the search. Each call counts as one request against your plan. Every returned article gets a stable reference (`A1`, `A2`, …), which is what Claude sees, together with the title, publisher, date and summary.
5. **Answer.** Claude cites references after each claim. The program resolves them to the articles WFM returned and prints their real URLs.

The code is in three small files:

| File | Role |
| --- | --- |
| [`wfm_agent/mcp_client.py`](wfm_agent/mcp_client.py) | The MCP connection. Knows nothing about the LLM. |
| [`wfm_agent/agent.py`](wfm_agent/agent.py) | The Claude tool-use loop. The only provider-specific file. |
| [`wfm_agent/cli.py`](wfm_agent/cli.py) | Arguments, error messages, the printed report |

To use another LLM provider, write a function like `research()` in `agent.py` against that provider's SDK. `WfmMcp` stays as it is.

### The tools

| Tool | Arguments | Returns |
| --- | --- | --- |
| `search_news` | `keywords`, `title`, `country[]`, `language[]`, `topic[]`, `original_category[]`, `continent[]`, `continent_region[]`, `publisher[]`, `publisher_type[]`, `sector[]`, `has_image`, `from_date`, `to_date`, `sort`, `page` | 25 articles a page; the last day unless dates are given |
| `get_latest_news` | `country[]`, `language[]`, `topic[]`, `publisher[]`, `page` | The newest articles from the last day |
| `get_publishers` | `country`, `page`, `per_page` | The publishers WFM reads |
| `get_coverage` | `include[]` | Valid country, language and topic codes |

Unknown arguments are refused, and the model is told why, so it can correct itself.

## Usage limits

- **WFM:** each tool call counts as one request, exactly like a REST call. Connecting and listing tools are free. The Free plan allows 30 requests a minute, 1,000 a day and 10,000 a month. A typical question uses 2 to 6. When an allowance is used up, the agent stops and says when to try again.
- **Tool-call cap:** `--max-tool-calls` limits what one question can spend.

## Costs

World Feed Monitor's Free plan costs nothing. **Anthropic usage is billed to your Anthropic account.** Each search result adds a few thousand input tokens, and the conversation is resent on every turn. With Claude Opus 5.5 ($4 per million input tokens, $20 per million output) a question typically costs a few tens of cents. Prompt caching is on, so repeated context is billed at the cached rate. To spend less, use fewer tool calls or set `ANTHROPIC_MODEL` to a cheaper model.

## Use WFM's MCP server from your own client

Any MCP client that can send a request header can connect directly. With Claude Code:

```bash
claude mcp add --transport http worldfeedmonitor \
  https://api.worldfeedmonitor.com/mcp \
  --header "Authorization: Basic $(printf 'YOUR_API_KEY:YOUR_API_SECRET' | base64 | tr -d '\n')"
```

World Feed Monitor also lists Cursor, VS Code, and Claude Desktop through `mcp-remote`: use the same URL and header in each client's own configuration format. Browser-based connectors that sign in with OAuth (claude.ai, ChatGPT) are not supported yet.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

The tests run the real MCP client session against a stand-in for the WFM server (an `httpx2` mock transport) and use a scripted Claude, so they need no credentials and no network.

## Links

- [MCP server overview](https://worldfeedmonitor.com/#mcp)
- [API Playground](https://worldfeedmonitor.com/playground): try searches without an account
- [REST API reference](https://worldfeedmonitor.com/docs)
- [Next.js REST example](https://github.com/World-Feed-Monitor/worldfeedmonitor-nextjs-example)

## License

[MIT](LICENSE)
