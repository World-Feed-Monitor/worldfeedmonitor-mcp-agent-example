"""The research loop, with a scripted Claude and the stand-in WFM server."""

from __future__ import annotations

import datetime as dt

import pytest
from conftest import FakeClaude, message, text, tool_use
from fake_wfm import KEY, SECRET, FakeWfm

from wfm_agent.agent import FALLBACK_BETA, AgentError, research
from wfm_agent.mcp_client import WfmMcp, WfmRateLimitError

pytestmark = pytest.mark.anyio


def connect(fake: FakeWfm) -> WfmMcp:
    return WfmMcp("https://api.example.test/mcp", KEY, SECRET, transport=fake.transport())


async def test_claude_discovers_the_tools_calls_one_and_cites_what_it_returned() -> None:
    claude = FakeClaude(
        message("tool_use", tool_use("search_news", {"country": ["JP"], "keywords": "AI"})),
        message("end_turn", text("Two labs released models this week [A1][A2]. Funding rose [A2].")),
    )
    fake = FakeWfm()
    async with connect(fake) as wfm:
        report = await research(
            "Latest AI news in Japan?", wfm, claude, "claude-opus-5-5", today=dt.date(2026, 11, 3)
        )

    first = claude.requests[0]
    assert [t["name"] for t in first["tools"]] == [
        "search_news",
        "get_latest_news",
        "get_sources",
        "get_coverage",
    ]
    assert first["tools"][0]["input_schema"]["additionalProperties"] is False
    assert first["fallbacks"] == "default" and first["betas"] == [FALLBACK_BETA]
    assert first["cache_control"] == {"type": "ephemeral"}
    assert "Today is 2026-11-03" in first["system"]

    assert fake.calls == [("search_news", {"country": ["JP"], "keywords": "AI"})]
    tool_result = claude.requests[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "toolu_1" and '"ref": "A1"' in tool_result["content"]

    assert [a.ref for a in report.cited] == ["A1", "A2"]  # first-cited order, no repeats
    assert report.cited[0].url == fake.articles[0]["url"]
    assert report.unknown_refs == []


async def test_a_citation_with_no_returned_article_is_flagged_not_linked() -> None:
    claude = FakeClaude(
        message("tool_use", tool_use("search_news", {})),
        message("end_turn", text("Real [A1]. Invented [A9].")),
    )
    async with connect(FakeWfm()) as wfm:
        report = await research("q", wfm, claude, "m")

    assert [a.ref for a in report.cited] == ["A1"]
    assert report.unknown_refs == ["A9"]
    assert "[A9: not a returned article]" in report.text


async def test_the_prompt_asks_for_per_claim_refs_and_for_gaps_to_be_stated() -> None:
    claude = FakeClaude(message("end_turn", text("Nothing relevant.")))
    async with connect(FakeWfm()) as wfm:
        await research("q", wfm, claude, "m")

    system = claude.requests[0]["system"]
    assert "right after each claim" in system
    assert "What the coverage does not show" in system
    assert "never from your own knowledge" in system


async def test_no_results_is_reported_as_no_results() -> None:
    claude = FakeClaude(
        message("tool_use", tool_use("search_news", {"keywords": "nothing"})),
        message("end_turn", text("World Feed Monitor returned nothing relevant.")),
    )
    async with connect(FakeWfm(articles=[])) as wfm:
        report = await research("q", wfm, claude, "m")

    assert '"note": "No articles matched' in claude.requests[1]["messages"][-1]["content"][0]["content"]
    assert report.articles_seen == 0 and report.cited == []


async def test_the_tool_call_budget_protects_the_plan() -> None:
    claude = FakeClaude(
        message("tool_use", tool_use("search_news", {}, "t1"), tool_use("search_news", {"page": 2}, "t2")),
        message("end_turn", text("Done [A1].")),
    )
    fake = FakeWfm()
    async with connect(fake) as wfm:
        await research("q", wfm, claude, "m", max_tool_calls=1)

    assert len(fake.calls) == 1
    results = claude.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]  # every tool_use answered, in one message
    assert results[1]["is_error"] and "budget" in results[1]["content"]


async def test_a_spent_allowance_ends_the_research() -> None:
    claude = FakeClaude(message("tool_use", tool_use("search_news", {})))
    async with connect(FakeWfm(rate_limited=True)) as wfm:
        with pytest.raises(WfmRateLimitError):
            await research("q", wfm, claude, "m")


async def test_a_declined_request_is_an_error_not_an_answer() -> None:
    claude = FakeClaude(message("refusal"))
    async with connect(FakeWfm()) as wfm:
        with pytest.raises(AgentError, match="declined"):
            await research("q", wfm, claude, "m")
