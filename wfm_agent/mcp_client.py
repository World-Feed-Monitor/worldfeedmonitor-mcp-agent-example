"""The World Feed Monitor MCP connection. Knows nothing about the LLM.

Connects over Streamable HTTP with HTTP Basic auth, lists the tools, calls them,
and keeps every article a tool returned under a short reference ("A1", "A2", …)
so an answer can cite the exact article behind each claim.
"""

from __future__ import annotations

import base64
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

USER_AGENT = "worldfeedmonitor-mcp-agent-example/0.1"


class WfmError(Exception):
    """Something the user has to act on, with a message that says what."""


class WfmAuthError(WfmError):
    """401: the API key or secret was not accepted."""


class WfmForbiddenError(WfmError):
    """403: the account cannot use the API yet (inactive, or email not confirmed)."""


class WfmRateLimitError(WfmError):
    """A plan allowance is used up. Further calls would fail the same way until it resets."""

    def __init__(self, message: str, retry_after: int | None, window: str | None):
        super().__init__(message)
        self.retry_after = retry_after
        self.window = window


@dataclass
class Tool:
    """A tool as the server describes it, in no particular LLM's format."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolResult:
    """What goes back to the LLM: text, and whether the call failed."""

    text: str
    is_error: bool = False


@dataclass
class Article:
    ref: str
    title: str
    url: str
    publisher: str | None
    country: str | None
    published_at: str | None
    published_timezone: str | None


def basic_auth(key: str, secret: str) -> str:
    """HTTP Basic: the API key is the username and the API secret the password."""
    return "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode()


@dataclass
class WfmMcp:
    """An open connection to the WFM MCP server. Use as `async with WfmMcp(...) as wfm:`."""

    url: str
    api_key: str
    api_secret: str
    transport: httpx2.AsyncBaseTransport | None = None  # tests pass a mock here
    articles: dict[str, Article] = field(default_factory=dict)
    calls: int = 0
    _by_url: dict[str, str] = field(default_factory=dict, repr=False)
    _last_refusal: tuple[int, str] | None = field(default=None, repr=False)
    _client: Client | None = field(default=None, repr=False)
    _stack: AsyncExitStack | None = field(default=None, repr=False)

    def __repr__(self) -> str:  # the dataclass default would print the secret
        return f"WfmMcp(url={self.url!r})"

    async def __aenter__(self) -> WfmMcp:
        self._stack = AsyncExitStack()
        http = httpx2.AsyncClient(
            headers={"Authorization": basic_auth(self.api_key, self.api_secret), "User-Agent": USER_AGENT},
            timeout=httpx2.Timeout(30.0, read=120.0),
            transport=self.transport,
            event_hooks={"response": [self._remember_refusal]},
        )
        await self._stack.enter_async_context(http)
        # mode="legacy": WFM speaks the initialize handshake (protocol 2025-11-25 and earlier),
        # so the client skips probing for a newer protocol first.
        client = Client(streamable_http_client(self.url, http_client=http), mode="legacy")
        try:
            self._client = await self._stack.enter_async_context(client)
        except BaseException as error:
            await self._stack.aclose()
            raise self._explain(error) from None
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack:
            await self._stack.aclose()

    async def list_tools(self) -> list[Tool]:
        """Free: discovering tools does not count against the plan."""
        try:
            listing = await self._connected().list_tools()
        except BaseException as error:
            raise self._explain(error) from None
        return [Tool(t.name, t.description or "", dict(t.input_schema)) for t in listing.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """One tool call, which counts as one request against the plan."""
        self.calls += 1
        try:
            result = await self._connected().call_tool(name, arguments)
        except BaseException as error:
            raise self._explain(error) from None

        structured = result.structured_content or {}
        text = "".join(block.text for block in result.content if getattr(block, "type", None) == "text")

        if result.is_error:
            if "retry_after" in structured:
                raise WfmRateLimitError(text, structured.get("retry_after"), structured.get("window"))
            # A refused argument or filter value: the LLM can read it and try again.
            return ToolResult(text or "The tool call failed.", is_error=True)

        if "articles" in structured:
            structured = {**structured, "articles": [self._keep(a) for a in structured["articles"]]}
            if not structured["articles"]:
                structured["note"] = "No articles matched. Say so rather than answering from elsewhere."

        return ToolResult(json.dumps(structured, ensure_ascii=False) if structured else text)

    def _keep(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Give an article a reference, once per URL, and return what the LLM sees of it."""
        url = raw.get("url") or ""
        ref = self._by_url.get(url)
        if ref is None:
            ref = f"A{len(self.articles) + 1}"
            self._by_url[url] = ref
            self.articles[ref] = Article(
                ref=ref,
                title=raw.get("title") or url,
                url=url,  # kept exactly as the server sent it
                publisher=(raw.get("publisher") or {}).get("name"),
                country=(raw.get("country") or {}).get("name"),
                published_at=raw.get("published_at"),
                published_timezone=raw.get("published_timezone"),
            )
        # The URL is left out of what the model reads: it cites the ref, and links
        # are filled in from here, so none can be misquoted or invented.
        return {
            "ref": ref,
            "title": raw.get("title"),
            "publisher": (raw.get("publisher") or {}).get("name"),
            "country": (raw.get("country") or {}).get("name"),
            "language": (raw.get("language") or {}).get("name"),
            "published_at": raw.get("published_at"),
            "published_timezone": raw.get("published_timezone"),
            "topics": [t.get("name") for t in raw.get("topics") or []],
            "summary": raw.get("summary"),
        }

    def _connected(self) -> Client:
        if self._client is None:
            raise RuntimeError("Use WfmMcp as an async context manager.")
        return self._client

    async def _remember_refusal(self, response: httpx2.Response) -> None:
        # The MCP SDK reports any HTTP failure as one generic error, so the status
        # and the server's own message are noted here, where they are still known.
        if response.status_code in (401, 403):
            await response.aread()
            try:
                message = response.json().get("message", "")
            except ValueError:
                message = ""
            self._last_refusal = (response.status_code, message)

    def _explain(self, error: BaseException) -> BaseException:
        if self._last_refusal:
            status, message = self._last_refusal
            if status == 401:
                return WfmAuthError(
                    "World Feed Monitor did not accept WFM_API_KEY and WFM_API_SECRET. Check them in .env; "
                    "a lost secret cannot be shown again, so create a new credential in the dashboard."
                )
            return WfmForbiddenError(
                f"World Feed Monitor refused the connection: {message or 'access denied'}. "
                "Confirm your email address from the dashboard, or check that your account is active."
            )
        if _contains(error, (httpx2.TransportError,)):
            return WfmError(f"Could not reach the World Feed Monitor MCP server at {self.url}.")
        if _contains(error, (MCPError,)):
            return WfmError("The World Feed Monitor MCP server returned an error. Try again in a moment.")
        return error


def _contains(error: BaseException, kinds: tuple[type[BaseException], ...]) -> bool:
    """True if `error`, or anything in a (nested) exception group, is one of `kinds`."""
    if isinstance(error, kinds):
        return True
    return any(_contains(inner, kinds) for inner in getattr(error, "exceptions", ()))
