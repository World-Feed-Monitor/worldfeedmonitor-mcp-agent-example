"""The research loop on Claude. The only file that knows about the LLM provider.

Claude sees the WFM tools exactly as the server describes them, decides which to call,
and writes an answer that cites each claim by an article reference ("[A3]"). To use
another provider, write a function like `research` against its SDK; WfmMcp stays as is.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import anthropic

from .mcp_client import Article, WfmMcp

MAX_TOKENS = 16_000
# Opus 5.5 defaults to "medium". Raise it for deeper research, at a higher token cost.
EFFORT = "medium"
# If a safety classifier declines a request, the API re-runs it on a fallback model it chooses.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """You are a news research assistant. You answer only from articles returned by the \
World Feed Monitor tools, never from your own knowledge.

How to work:
- Search before answering. Use get_coverage when you are unsure of a country, language or topic code.
- Without dates, searches cover the last day. For "this week" or "recently", pass from_date and to_date. \
Today is {today}.
- Search in the language the local press writes in when that helps (e.g. Japanese keywords for Japan).
- You have a budget of {budget} tool calls. Each one counts against the user's plan, so do not repeat \
searches that already answered.

How to answer:
- Every article in a tool result has a ref such as A3. Put the ref in square brackets right after each \
claim it supports, e.g. "Rates were held at 0.5% [A3][A7]." Every factual sentence needs at least one ref.
- Never write URLs; they are added from the refs.
- If the articles do not answer part of the question, say so plainly in a final section headed \
"What the coverage does not show". Do not fill gaps with assumptions or general knowledge.
- If no article is relevant, say that World Feed Monitor returned nothing relevant, and stop.
- Mention the publisher and date of key articles, and keep the answer under 400 words."""


class AgentError(Exception):
    """The model could not produce an answer (declined, or stopped short)."""


@dataclass
class Report:
    text: str
    cited: list[Article] = field(default_factory=list)
    unknown_refs: list[str] = field(default_factory=list)
    articles_seen: int = 0
    tool_calls: int = 0


async def research(
    question: str,
    wfm: WfmMcp,
    client: anthropic.AsyncAnthropic,
    model: str,
    *,
    max_tool_calls: int = 6,
    on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
    today: dt.date | None = None,
) -> Report:
    tools = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in await wfm.list_tools()
    ]
    system = SYSTEM.format(today=(today or dt.date.today()).isoformat(), budget=max_tool_calls)
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]

    for _ in range(max_tool_calls + 4):  # a few extra turns for writing the answer
        response = await client.beta.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": EFFORT},
            # Each turn resends the articles read so far; caching makes the repeats cheap.
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

        if response.stop_reason == "refusal":
            raise AgentError("Claude declined to research this question.")

        # Appended whole, thinking blocks included: the history is only ever added to.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason != "tool_use":
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "max_tokens":
                text += "\n\n(The answer was cut off at the length limit.)"
            if not text:
                raise AgentError(f"Claude stopped without an answer ({response.stop_reason}).")
            return _report(text, wfm)

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if wfm.calls >= max_tool_calls:
                results.append(
                    _tool_result(block.id, "Tool call budget reached. Answer with what you have.", True)
                )
                continue
            if on_tool_call:
                on_tool_call(block.name, block.input)
            # A rate limit raises WfmRateLimitError: further calls would only fail the same way.
            outcome = await wfm.call_tool(block.name, block.input)
            results.append(_tool_result(block.id, outcome.text, outcome.is_error))

        # Every tool_result in one user message.
        messages.append({"role": "user", "content": results})

    raise AgentError("The research did not finish within its turn limit.")


def _tool_result(tool_use_id: str, text: str, is_error: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
    if is_error:
        result["is_error"] = True
    return result


REF = re.compile(r"\[(A\d+)\]")


def _report(text: str, wfm: WfmMcp) -> Report:
    """Resolve the refs the answer cites to the articles WFM actually returned."""
    cited: list[Article] = []
    unknown: list[str] = []
    for ref in dict.fromkeys(REF.findall(text)):  # first-cited order, no repeats
        if ref in wfm.articles:
            cited.append(wfm.articles[ref])
        else:
            unknown.append(ref)

    # A ref nothing returned is a citation with nothing behind it: mark it in the text.
    for ref in unknown:
        text = text.replace(f"[{ref}]", f"[{ref}: not a returned article]")

    return Report(
        text=text, cited=cited, unknown_refs=unknown, articles_seen=len(wfm.articles), tool_calls=wfm.calls
    )
