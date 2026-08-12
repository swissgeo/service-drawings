"""Unit tests for the MaxBodySizeMiddleware."""

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import pytest

from app.middlewares.body_size import MaxBodySizeMiddleware

Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


async def _noop_receive() -> MutableMapping[str, Any]:
    return {}


async def _noop_send(_message: MutableMapping[str, Any]) -> None:
    return None


@pytest.mark.asyncio
async def test_malformed_content_length_proceeds() -> None:
    """A non-numeric Content-Length should be ignored and the request proceed."""
    calls: list[dict] = []

    async def app(scope, _receive: Receive, _send: Send) -> None:
        calls.append(scope)

    middleware = MaxBodySizeMiddleware(app, max_size=100)
    scope = {
        "type": "http",
        "headers": [(b"content-length", b"not-a-number")],
    }

    await middleware(scope, _noop_receive, _noop_send)

    assert calls == [scope]


@pytest.mark.asyncio
async def test_non_http_scope_passes_through() -> None:
    """Non-HTTP scopes (e.g. websocket/lifespan) pass through untouched."""
    calls: list[dict] = []

    async def app(scope, _receive: Receive, _send: Send) -> None:
        calls.append(scope)

    middleware = MaxBodySizeMiddleware(app, max_size=100)
    scope = {"type": "websocket"}

    await middleware(scope, _noop_receive, _noop_send)

    assert calls == [scope]
