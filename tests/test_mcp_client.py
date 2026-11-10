"""The MCP layer against a stand-in for the WFM server, through the real MCP client session."""

from __future__ import annotations

import json

import pytest
from fake_wfm import AUTH, KEY, SECRET, FakeWfm, article

from wfm_agent.mcp_client import (
    WfmAuthError,
    WfmForbiddenError,
    WfmMcp,
    WfmRateLimitError,
    basic_auth,
)

pytestmark = pytest.mark.anyio
URL = "https://api.example.test/mcp"


def connect(fake: FakeWfm, key: str = KEY, secret: str = SECRET) -> WfmMcp:
    return WfmMcp(URL, key, secret, transport=fake.transport())


def test_basic_auth_is_key_and_secret_base64_encoded() -> None:
    assert basic_auth(KEY, SECRET) == AUTH


async def test_every_request_carries_the_basic_header() -> None:
    fake = FakeWfm()
    async with connect(fake) as wfm:
        await wfm.list_tools()
        await wfm.call_tool("search_news", {"country": ["JP"]})

    posts = [r for r in fake.requests if r.method == "POST"]
    methods = [json.loads(r.content)["method"] for r in posts]
    assert methods[:2] == ["initialize", "notifications/initialized"]  # the legacy handshake, no probe
    assert all(r.headers["Authorization"] == AUTH for r in fake.requests)


async def test_tools_are_discovered_with_their_schemas_and_cost_nothing() -> None:
    fake = FakeWfm()
    async with connect(fake) as wfm:
        tools = await wfm.list_tools()

    assert [t.name for t in tools] == ["search_news", "get_latest_news", "get_publishers", "get_coverage"]
    assert tools[0].input_schema["additionalProperties"] is False
    assert "country" in tools[0].input_schema["properties"]
    assert fake.calls == []  # only tools/call is billed


async def test_a_tool_call_returns_articles_by_reference() -> None:
    fake = FakeWfm()
    async with connect(fake) as wfm:
        result = await wfm.call_tool("search_news", {"country": ["JP"], "keywords": "AI"})

    assert fake.calls == [("search_news", {"country": ["JP"], "keywords": "AI"})]
    assert wfm.calls == 1
    payload = json.loads(result.text)
    assert [a["ref"] for a in payload["articles"]] == ["A1", "A2"]
    assert payload["articles"][0]["publisher"] == "Example Shimbun"
    assert "url" not in payload["articles"][0]  # the model cites refs; links come from the record


async def test_source_urls_are_kept_exactly_as_the_server_sent_them() -> None:
    odd = "https://news.example.jp/記事/42?utm_source=x&a=1#top"
    fake = FakeWfm(articles=[article(1, url=odd)])
    async with connect(fake) as wfm:
        await wfm.call_tool("search_news", {})

    assert wfm.articles["A1"].url == odd


async def test_the_same_article_keeps_its_reference_across_calls() -> None:
    fake = FakeWfm(articles=[article(1), article(2)])
    async with connect(fake) as wfm:
        await wfm.call_tool("search_news", {"country": ["JP"]})
        fake.articles = [article(2), article(3)]
        second = json.loads((await wfm.call_tool("get_latest_news", {})).text)

    assert [a["ref"] for a in second["articles"]] == ["A2", "A3"]
    assert list(wfm.articles) == ["A1", "A2", "A3"]


async def test_empty_results_are_returned_with_a_note() -> None:
    fake = FakeWfm(articles=[])
    async with connect(fake) as wfm:
        payload = json.loads((await wfm.call_tool("search_news", {"keywords": "nothing"})).text)

    assert payload["articles"] == []
    assert "No articles matched" in payload["note"]
    assert wfm.articles == {}


async def test_a_refused_argument_goes_back_to_the_model_as_an_error() -> None:
    async with connect(FakeWfm()) as wfm:
        result = await wfm.call_tool("search_news", {"bogus": True})

    assert result.is_error
    assert "Unknown argument: bogus" in result.text


async def test_a_spent_allowance_stops_with_when_to_retry() -> None:
    async with connect(FakeWfm(rate_limited=True)) as wfm:
        with pytest.raises(WfmRateLimitError) as raised:
            await wfm.call_tool("search_news", {})

    assert raised.value.retry_after == 24
    assert raised.value.window == "minute"


async def test_wrong_credentials_say_what_to_check() -> None:
    with pytest.raises(WfmAuthError, match="WFM_API_KEY and WFM_API_SECRET"):
        async with connect(FakeWfm(), secret="wrong"):
            pass


async def test_an_account_that_cannot_use_the_api_gets_the_servers_reason() -> None:
    with pytest.raises(WfmForbiddenError, match="Account is not active"):
        async with connect(FakeWfm(status=403)):
            pass


def test_the_secret_is_not_in_the_connections_repr() -> None:
    assert SECRET not in repr(connect(FakeWfm()))
