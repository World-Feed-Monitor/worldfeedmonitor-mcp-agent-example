"""A stand-in for the World Feed Monitor MCP endpoint, as an httpx2 mock transport.

It answers the way the real server does (laravel/mcp, Streamable HTTP, JSON responses):
Basic auth on every message, 401 with WWW-Authenticate when it is wrong, 405 for the GET
stream, the four tools with their real argument names, and tool results carrying
structuredContent. Only tools/call is "billed", which `calls` records.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

import httpx2

KEY, SECRET = "test-key", "test-secret"
AUTH = "Basic " + base64.b64encode(f"{KEY}:{SECRET}".encode()).decode()

TOOLS = [
    {
        "name": "search_news",
        "title": "Search news",
        "description": "Search news articles from publishers worldwide. Returns 25 articles per page.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "keywords": {"type": "string", "maxLength": 255},
                "country": {"type": "array", "items": {"type": "string"}},
                "language": {"type": "array", "items": {"type": "string"}},
                "topic": {"type": "array", "items": {"type": "string"}},
                "from_date": {"type": "string", "format": "date"},
                "to_date": {"type": "string", "format": "date"},
                "sort": {"type": "string", "enum": ["relevance", "desc", "asc"]},
                "page": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
        },
    },
    {
        "name": "get_latest_news",
        "title": "Latest news",
        "description": "The newest news articles from the last day.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
    {
        "name": "get_publishers",
        "title": "News publishers",
        "description": "List the news publishers World Feed Monitor reads.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
    {
        "name": "get_coverage",
        "title": "Coverage",
        "description": "The countries, languages and topics World Feed Monitor covers.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
]


def article(n: int, **overrides: Any) -> dict[str, Any]:
    return {
        "id": f"uuid-{n}",
        "title": f"Article {n}",
        "url": f"https://news.example.jp/articles/{n}?ref=feed&lang=ja",
        "published_at": "2026-11-03 09:12:00",
        "published_timezone": "Asia/Tokyo",
        "author": None,
        "summary": f"Summary of article {n}.",
        "image": None,
        "publisher": {
            "key": "example-shimbun-jp",
            "name": "Example Shimbun",
            "url": "https://news.example.jp",
        },
        "country": {"iso_2": "JP", "iso_3": "JPN", "name": "Japan"},
        "language": {"code": "ja", "name": "Japanese"},
        "topics": [{"key": "technology", "name": "Technology"}],
        **overrides,
    }


@dataclass
class FakeWfm:
    articles: list[dict[str, Any]] = field(default_factory=lambda: [article(1), article(2)])
    status: int | None = None  # force an HTTP status on every POST, e.g. 401 or 403
    rate_limited: bool = False
    requests: list[httpx2.Request] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)

        if request.method == "GET":
            return httpx2.Response(405)
        if request.method == "DELETE":
            return httpx2.Response(200)
        if self.status == 403:
            return httpx2.Response(403, json={"message": "Account is not active", "status": "inactive"})
        if self.status == 401 or request.headers.get("Authorization") != AUTH:
            return httpx2.Response(
                401,
                json={"message": "Invalid credentials"},
                headers={"WWW-Authenticate": 'Basic realm="World Feed Monitor", charset="UTF-8"'},
            )

        message = json.loads(request.content)
        if "id" not in message:  # a notification
            return httpx2.Response(202)

        return httpx2.Response(
            200,
            json={"jsonrpc": "2.0", "id": message["id"], "result": self.result(message)},
            headers={"Mcp-Session-Id": "session-1"},
        )

    def result(self, message: dict[str, Any]) -> dict[str, Any]:
        method, params = message["method"], message.get("params", {})

        if method == "initialize":
            return {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "World Feed Monitor", "version": "1.0.0"},
                "instructions": "Every article carries its publisher and its URL.",
            }
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            self.calls.append((params["name"], params.get("arguments", {})))
            if self.rate_limited:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "You have used your plan allowance of 30 requests per minute. "
                            "It resets in 24 seconds.",
                        }
                    ],
                    "structuredContent": {"window": "minute", "limit": 30, "retry_after": 24},
                    "isError": True,
                }
            if "bogus" in params.get("arguments", {}):
                return {
                    "content": [
                        {"type": "text", "text": "Unknown argument: bogus. Accepted: keywords, country."}
                    ],
                    "isError": True,
                }
            payload = {
                "meta": {
                    "total_items": len(self.articles),
                    "total_pages": 1,
                    "current_page": 1,
                    "has_next": False,
                },
                "articles": self.articles,
            }
            return {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "structuredContent": payload,
                "isError": False,
            }

        return {}
