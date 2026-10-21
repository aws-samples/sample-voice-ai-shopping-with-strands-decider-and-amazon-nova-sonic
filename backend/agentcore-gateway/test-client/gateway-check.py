#!/usr/bin/env python3
"""Gateway test client.

Connects to the AgentCore Gateway over MCP with SigV4 (service bedrock-agentcore),
lists the tools, resolves them by suffix (Gateway names them "<target>___<tool>",
as the session host will), calls each with valid and invalid input, and
measures the latency Gateway adds to search_catalog: client time minus the
Lambda handler's own timingsMs.total.

Usage: python3 gateway-check.py [--gateway-url URL] [--region us-east-1] [--calls 30]
Needs: mcp, mcp-proxy-for-aws, boto3.
"""

import argparse
import asyncio
import json
import statistics
import time
import uuid

import boto3
from mcp import ClientSession
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

QUERIES = ['a gift for my mom, something elegant', 'black leather crossbody bag', 'silver necklace with a heart pendant',
           'warm winter hat for my dad', 'summer dress with flowers', 'sporty sunglasses for running']


def gateway_url_from_exports(region: str) -> str:
    pages = boto3.client('cloudformation', region_name=region).get_paginator('list_exports').paginate()
    return next(e['Value'] for p in pages for e in p['Exports'] if e['Name'] == 'VoiceShop-AgentCoreGatewayStack-GatewayUrl')


def payload(result) -> dict:
    """Tool results arrive as MCP text content holding the Lambda's JSON."""
    text = ''.join(getattr(c, 'text', '') for c in result.content)
    try:
        return json.loads(text)
    except ValueError:
        return {'raw': text, 'isError': result.isError}


async def run(args) -> None:
    url = args.gateway_url or gateway_url_from_exports(args.region)
    async with aws_iam_streamablehttp_client(endpoint=url, aws_service='bedrock-agentcore', aws_region=args.region) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name.split('___')[-1]: t.name for t in (await session.list_tools()).tools}
            print('tools:', json.dumps(tools))
            assert set(tools) >= {'search_catalog', 'get_product_detail', 'add_to_cart', 'place_order'}, 'missing tools'

            call = lambda name, arguments: session.call_tool(tools[name], arguments)  # noqa: E731
            checks = []

            def check(label, ok, detail=''):
                checks.append(ok)
                print(f"{'✅' if ok else '❌'} {label} {detail}")

            # search_catalog
            r = payload(await call('search_catalog', {'query': 'leather bag for my mom', 'maxPriceCents': 8000, 'genders': ['women', 'unisex'], 'limit': 12}))
            hits = r.get('hits', [])
            check('search_catalog valid', bool(hits) and all(h['priceCents'] <= 8000 for h in hits), f"{len(hits)} hits, {r.get('totalMatched')} matched")
            r = payload(await call('search_catalog', {'query': 'bag', 'categories': ['Spaceships']}))
            check('search_catalog invalid category -> ValidationError', r.get('error') == 'ValidationError', r.get('message', ''))

            # get_product_detail
            asin = hits[0]['asin'] if hits else 'B000000000'
            r = payload(await call('get_product_detail', {'asin': asin}))
            check('get_product_detail valid', r.get('product', {}).get('asin') == asin, r.get('product', {}).get('title', '')[:50])
            r = payload(await call('get_product_detail', {'asin': 'not-an-asin'}))
            check('get_product_detail invalid asin -> ValidationError', r.get('error') == 'ValidationError', r.get('message', ''))

            # add_to_cart (idempotent) and place_order, on a throwaway session id
            session_id = f'gateway-check-{uuid.uuid4()}'
            key = f'add-{uuid.uuid4().hex[:12]}'
            first = payload(await call('add_to_cart', {'sessionId': session_id, 'asin': asin, 'quantity': 2, 'idempotencyKey': key}))
            again = payload(await call('add_to_cart', {'sessionId': session_id, 'asin': asin, 'quantity': 2, 'idempotencyKey': key}))
            check('add_to_cart valid', first.get('cart', {}).get('itemCount') == 2, json.dumps(first.get('cart', {}))[:120])
            check('add_to_cart retry is idempotent', again.get('cart', {}).get('itemCount') == 2)
            r = payload(await call('add_to_cart', {'sessionId': session_id, 'asin': asin, 'quantity': 99, 'idempotencyKey': 'x'}))
            check('add_to_cart quantity 99 -> ValidationError', r.get('error') == 'ValidationError', r.get('message', ''))
            order_key = f'order-{uuid.uuid4().hex[:12]}'
            order = payload(await call('place_order', {'sessionId': session_id, 'idempotencyKey': order_key}))
            check('place_order valid', order.get('order', {}).get('itemCount') == 2, json.dumps(order.get('order', {}))[:120])
            retry = payload(await call('place_order', {'sessionId': session_id, 'idempotencyKey': order_key}))
            check('place_order retry returns the same order', retry.get('order', {}).get('orderId') == order.get('order', {}).get('orderId'))
            empty = payload(await call('place_order', {'sessionId': session_id, 'idempotencyKey': f'order-{uuid.uuid4().hex[:12]}'}))
            check('place_order on the emptied cart -> EmptyCart', empty.get('error') == 'EmptyCart')

            # Gateway overhead on search_catalog (warm)
            client, handler = [], []
            for i in range(args.calls):
                started = time.perf_counter()
                r = payload(await call('search_catalog', {'query': QUERIES[i % len(QUERIES)], 'limit': 200}))
                client.append((time.perf_counter() - started) * 1000)
                handler.append(r['timingsMs']['total'])
            overhead = sorted(c - h for c, h in zip(client, handler))
            p95 = lambda v: sorted(v)[int(len(v) * 0.95) - 1]  # noqa: E731
            print(f"\nsearch_catalog via Gateway (limit=200, n={args.calls}): client p50 {statistics.median(client):.0f} / p95 {p95(client):.0f} ms; "
                  f"handler p50 {statistics.median(handler):.0f} ms; Gateway + invoke overhead p50 {statistics.median(overhead):.0f} / p95 {p95(overhead):.0f} ms")
            print(f"\n{sum(checks)}/{len(checks)} checks passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gateway-url')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--calls', type=int, default=30)
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
