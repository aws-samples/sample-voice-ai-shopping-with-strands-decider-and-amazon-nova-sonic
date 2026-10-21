"""AgentCore Gateway tools over MCP with SigV4.

Tools are discovered once per session and resolved by suffix, because Gateway
names them "<target>___<tool>"; no prefixed name is hardcoded. Each Lambda's
JSON result arrives as MCP text content.

The MCP connection can close under a live session: on 2026-10-21 it closed after
about 90 seconds, with nothing logged, and every later call raised
ClosedResourceError until the shopper left. A call that finds it closed reopens it
once and retries. Every tool is safe to retry: the reads change nothing, add_to_cart
and place_order carry idempotency keys, and remove_from_cart sets an end state.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os

import anyio
from mcp import ClientSession
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

from turn_pipeline import ToolError

logger = logging.getLogger(__name__)

TOOL_NAMES = ('search_catalog', 'get_product_detail', 'add_to_cart', 'remove_from_cart', 'place_order')
CALL_TIMEOUT_S = {'search_catalog': 2.5, 'get_product_detail': 2.0, 'add_to_cart': 3.0, 'remove_from_cart': 3.0, 'place_order': 4.0}


class GatewayTools:
    def __init__(self, gateway_url: str | None = None, region: str | None = None):
        self.gateway_url = gateway_url or os.environ['AGENTCORE_GATEWAY_URL']
        self.region = region or os.environ.get('AWS_REGION', 'us-east-1')
        self._session: ClientSession | None = None
        self._names: dict[str, str] = {}
        self._owner: tuple[asyncio.Task, asyncio.Event] | None = None
        self._reconnecting = asyncio.Lock()

    async def __aenter__(self) -> 'GatewayTools':
        await self._open()
        logger.info('✅ Gateway tools: %s', sorted(self._names))
        return self

    async def __aexit__(self, *exc) -> None:
        await self._close()

    async def _own(self, ready: asyncio.Future, stop: asyncio.Event) -> None:
        """Holds one MCP connection. The MCP client's task groups must be entered and
        exited in one task, so this task opens it, waits, and closes it; when the
        connection dies, its reason surfaces here and is logged."""
        try:
            async with contextlib.AsyncExitStack() as stack:
                read, write, _ = await stack.enter_async_context(
                    aws_iam_streamablehttp_client(endpoint=self.gateway_url, aws_service='bedrock-agentcore',
                                                  aws_region=self.region))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listed = (await session.list_tools()).tools
                names = {t.name.split('___')[-1]: t.name for t in listed}
                missing = set(TOOL_NAMES) - set(names)
                if missing:
                    raise ToolError(f'Gateway is missing tools: {sorted(missing)}')
                ready.set_result((session, names))
                await stop.wait()
        except BaseException as exc:  # noqa: BLE001
            if not ready.done():
                ready.set_exception(exc if isinstance(exc, Exception) else ToolError(f'Gateway connection failed: {exc!r}'))
            elif not stop.is_set():
                logger.warning('⚠️ Gateway connection ended', exc_info=True)
            if isinstance(exc, asyncio.CancelledError) and not stop.is_set():
                raise

    async def _open(self) -> None:
        ready, stop = asyncio.get_running_loop().create_future(), asyncio.Event()
        task = asyncio.create_task(self._own(ready, stop))
        try:
            self._session, self._names = await ready
        except BaseException:
            stop.set()
            raise
        self._owner = (task, stop)

    async def _close(self) -> None:
        owner, self._owner, self._session = self._owner, None, None
        if owner:
            task, stop = owner
            stop.set()
            with contextlib.suppress(BaseException):
                await task

    async def _reconnect(self, broken: ClientSession) -> None:
        """Replace a closed connection; concurrent callers reconnect once between them."""
        async with self._reconnecting:
            if self._session is not broken:
                return   # another call already reconnected
            await self._close()
            await self._open()
            logger.info('🔄 Gateway connection reopened')

    async def call(self, tool: str, arguments: dict) -> dict:
        if self._session is None:
            raise ToolError('Gateway session not open')
        for attempt in (1, 2):
            session = self._session
            try:
                result = await asyncio.wait_for(session.call_tool(self._names[tool], arguments),
                                                timeout=CALL_TIMEOUT_S.get(tool, 3.0))
                break
            except asyncio.TimeoutError as exc:
                raise ToolError(f'{tool} timed out') from exc
            except (anyio.ClosedResourceError, anyio.BrokenResourceError, McpError) as exc:
                closed = not isinstance(exc, McpError) or exc.error.code == CONNECTION_CLOSED
                if not closed or attempt == 2:
                    raise
                logger.warning('⚠️ Gateway connection closed during %s; reconnecting', tool)
                await self._reconnect(session)
        text = ''.join(getattr(c, 'text', '') for c in result.content)
        if result.isError:
            raise ToolError(f'{tool} failed: {text[:200]}')
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ToolError(f'{tool} returned non-JSON') from exc

    async def search_catalog(self, arguments: dict) -> dict:
        return await self.call('search_catalog', arguments)

    async def get_product_detail(self, arguments: dict) -> dict:
        return await self.call('get_product_detail', arguments)

    async def add_to_cart(self, arguments: dict) -> dict:
        return await self.call('add_to_cart', arguments)

    async def remove_from_cart(self, arguments: dict) -> dict:
        return await self.call('remove_from_cart', arguments)

    async def place_order(self, arguments: dict) -> dict:
        return await self.call('place_order', arguments)
