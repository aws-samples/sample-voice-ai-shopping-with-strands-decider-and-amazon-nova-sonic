import asyncio
import json
from types import SimpleNamespace

import anyio
import pytest

import tools_gateway
from tools_gateway import GatewayTools


class FakeSession:
    def __init__(self, fail_with=None):
        self.fail_with, self.calls = fail_with, []

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        if self.fail_with:
            raise self.fail_with
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({'ok': name}))], isError=False)


def gateway(*sessions):
    """A GatewayTools whose connections are the given sessions, opened in order."""
    gw = GatewayTools('https://gateway.example/mcp', 'us-east-1')
    queue = list(sessions)

    async def open_():
        gw._session, gw._names = queue.pop(0), {'search_catalog': 't___search_catalog'}
    async def close():
        gw._session = None
    gw._open, gw._close = open_, close
    return gw


def test_a_closed_connection_is_reopened_and_the_call_retried():
    """On 2026-10-21 every call after the connection closed raised ClosedResourceError."""
    async def go():
        broken, fresh = FakeSession(anyio.ClosedResourceError()), FakeSession()
        gw = gateway(broken, fresh)
        await gw._open()
        return await gw.call('search_catalog', {}), broken, fresh
    result, broken, fresh = asyncio.run(go())
    assert result == {'ok': 't___search_catalog'} and broken.calls and fresh.calls


def test_it_retries_once_only():
    async def go():
        gw = gateway(FakeSession(anyio.ClosedResourceError()), FakeSession(anyio.ClosedResourceError()))
        await gw._open()
        await gw.call('search_catalog', {})
    with pytest.raises(anyio.ClosedResourceError):
        asyncio.run(go())


def test_concurrent_calls_reconnect_once():
    async def go():
        broken, fresh = FakeSession(anyio.ClosedResourceError()), FakeSession()
        gw = gateway(broken, fresh)   # a second reconnect would find the queue empty
        await gw._open()
        return await asyncio.gather(gw.call('search_catalog', {}), gw.call('search_catalog', {}))
    assert asyncio.run(go()) == [{'ok': 't___search_catalog'}] * 2


def test_other_errors_are_not_retried():
    async def go():
        session = FakeSession(ValueError('bad input'))
        gw = gateway(session)
        await gw._open()
        try:
            await gw.call('search_catalog', {})
        finally:
            assert len(session.calls) == 1
    with pytest.raises(ValueError):
        asyncio.run(go())
