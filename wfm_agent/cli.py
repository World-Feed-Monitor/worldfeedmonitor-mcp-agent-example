"""wfm-research "What are the latest AI developments in Japan?" """

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

import anthropic

from .agent import AgentError, Report, research
from .config import Config, ConfigError, load_config, load_dotenv
from .mcp_client import WfmAuthError, WfmError, WfmForbiddenError, WfmMcp, WfmRateLimitError

EXIT_CONFIG, EXIT_AUTH, EXIT_RATE_LIMIT, EXIT_FAILED = 2, 3, 4, 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wfm-research",
        description="Research a question in the world's news, through the World Feed Monitor MCP server.",
    )
    parser.add_argument("question", nargs="+", help="what to research, in plain words")
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=6,
        help="most WFM tool calls to spend; each counts against your plan (default: 6)",
    )
    parser.add_argument("--verbose", action="store_true", help="show each tool call as it is made")
    args = parser.parse_args(argv)

    load_dotenv()
    try:
        config = load_config()
    except ConfigError as error:
        return fail(str(error), EXIT_CONFIG)

    def show_call(name: str, arguments: dict[str, Any]) -> None:
        if args.verbose:
            print(f"→ {name} {json.dumps(arguments, ensure_ascii=False)}", file=sys.stderr)

    try:
        report = asyncio.run(run(" ".join(args.question), config, args.max_tool_calls, show_call))
    except WfmRateLimitError as error:
        wait = f" Try again in {error.retry_after} seconds." if error.retry_after else ""
        return fail(f"Your World Feed Monitor plan allowance is used up: {error}{wait}", EXIT_RATE_LIMIT)
    except (WfmAuthError, WfmForbiddenError) as error:
        return fail(str(error), EXIT_AUTH)
    except WfmError as error:
        return fail(str(error), EXIT_FAILED)
    except anthropic.AuthenticationError:
        return fail("Anthropic did not accept ANTHROPIC_API_KEY. Check it in .env.", EXIT_AUTH)
    except anthropic.NotFoundError:
        return fail(
            f"Anthropic does not know the model {config.model!r}. Check ANTHROPIC_MODEL.", EXIT_CONFIG
        )
    except anthropic.RateLimitError:
        return fail("Anthropic's rate limit was reached. Try again in a minute.", EXIT_RATE_LIMIT)
    except anthropic.APIStatusError as error:
        return fail(f"The Anthropic API returned {error.status_code}: {error.message}", EXIT_FAILED)
    except anthropic.APIConnectionError:
        return fail("Could not reach the Anthropic API.", EXIT_FAILED)
    except anthropic.AnthropicError as error:  # e.g. no Anthropic credentials found at all
        return fail(f"Anthropic: {error}", EXIT_CONFIG)
    except AgentError as error:
        return fail(str(error), EXIT_FAILED)

    print(render(report))
    return 0


async def run(
    question: str, config: Config, max_tool_calls: int, on_tool_call: Callable[[str, dict[str, Any]], None]
) -> Report:
    async with WfmMcp(config.mcp_url, config.wfm_api_key, config.wfm_api_secret) as wfm:
        return await research(
            question,
            wfm,
            anthropic.AsyncAnthropic(),
            config.model,
            max_tool_calls=max_tool_calls,
            on_tool_call=on_tool_call,
        )


def render(report: Report) -> str:
    lines = [report.text, ""]

    if report.cited:
        lines.append("Sources")
        for article in report.cited:
            when = article.published_at[:16] if article.published_at else "undated"
            tz = f" {article.published_timezone}" if article.published_timezone else ""
            where = ", ".join(p for p in (article.publisher, article.country) if p)
            lines.append(f"  [{article.ref}] {article.title}")
            lines.append(f"       {where} · {when}{tz}")
            lines.append(f"       {article.url}")
    elif report.articles_seen == 0:
        lines.append("World Feed Monitor returned no articles for this question.")
    else:
        lines.append("Warning: the answer cites none of the articles World Feed Monitor returned.")

    if report.unknown_refs:
        lines.append("")
        unknown = ", ".join(report.unknown_refs)
        lines.append(f"Warning: cited references with no returned article behind them: {unknown}")

    lines.append("")
    lines.append(f"{report.tool_calls} WFM tool call(s), {report.articles_seen} article(s) read.")
    return "\n".join(lines)


def fail(message: str, code: int) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
