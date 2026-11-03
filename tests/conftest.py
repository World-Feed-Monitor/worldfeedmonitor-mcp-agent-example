from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def tool_use(name: str, arguments: dict[str, Any], id: str = "toolu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id, name=name, input=arguments)


def message(stop_reason: str, *content: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=list(content))


class FakeClaude:
    """Answers with scripted messages, in order, and records every request."""

    def __init__(self, *responses: SimpleNamespace):
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **request: Any) -> SimpleNamespace:
        # A snapshot: the loop keeps appending to the same list after the call.
        self.requests.append({**request, "messages": list(request["messages"])})
        return self.responses.pop(0)
