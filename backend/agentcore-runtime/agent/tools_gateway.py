"""AgentCore Gateway tools over MCP with SigV4.

Tools are discovered once per session and resolved by suffix, because Gateway
names them "<target>___<tool>"; no prefixed name is hardcoded. Each Lambda's
JSON result arrives as MCP text content.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os

from mcp import ClientSession
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

from turn_pipeline import ToolError

logger = logging.getLogger(__name__)

TOOL_NAMES = ('search_catalog', 'get_product_detail', 'add_to_cart', 'remove_from_cart', 'place_order')
CALL_TIMEOUT_S = {'search_catalog': 2.5, 'get_product_detail': 2.0, 'add_to_cart': 3.0, 'remove_from_cart': 3.0, 'place_order': 4.0}


class GatewayTools:
    def __init__(self, gateway_url: str | None = None, region: str | None = None):
        self.gateway_url = gateway_url or os.environ['AGENTCORE_GATEWAY_URL']
        self.region = region or os.environ.get('AWS_REGION', 'us-east-1')
        self._stack = contextlib.AsyncExitStack()
        self._session: ClientSession | None = None
        self._names: dict[str, str] = {}

    async def __aenter__(self) -> 'GatewayTools':
        read, write, _ = await self._stack.enter_async_context(
            aws_iam_streamablehttp_client(endpoint=self.gateway_url, aws_service='bedrock-agentcore', aws_region=self.region))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        listed = (await self._session.list_tools()).tools
        self._names = {t.name.split('___')[-1]: t.name for t in listed}
        missing = set(TOOL_NAMES) - set(self._names)
        if missing:
            raise ToolError(f'Gateway is missing tools: {sorted(missing)}')
        logger.info('✅ Gateway tools: %s', sorted(self._names))
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    async def call(self, tool: str, arguments: dict) -> dict:
        if self._session is None:
            raise ToolError('Gateway session not open')
        try:
            result = await asyncio.wait_for(self._session.call_tool(self._names[tool], arguments),
                                            timeout=CALL_TIMEOUT_S.get(tool, 3.0))
        except asyncio.TimeoutError as exc:
            raise ToolError(f'{tool} timed out') from exc
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
