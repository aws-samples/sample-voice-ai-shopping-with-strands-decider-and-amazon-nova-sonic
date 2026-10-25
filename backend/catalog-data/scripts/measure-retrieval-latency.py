#!/usr/bin/env python3
"""Measures search_catalog retrieval latency over the deployed catalog.

Invokes the search_catalog Lambda directly (AgentCore Gateway, is not
deployed yet, so Gateway overhead is not included) and reports, per scenario,
p50/p95 of:
  - client: InvokeFunction round trip as seen by this script;
  - embed / search / presign / total: the handler's own timingsMs (Titan V2 query
    embedding, filtered kNN + counts on OpenSearch Serverless, thumbnail presigning);
  - overhead: client minus handler total (Lambda invoke + network to this client).
The first call is reported separately, with the Lambda Init Duration if it was a
cold start.

Usage:
    python3 measure-retrieval-latency.py [--calls 50] [--region us-east-1]
"""

import argparse
import base64
import json
import re
import statistics
import time

import boto3
from botocore.config import Config

FUNCTION_EXPORT = 'VoiceShop-LambdaStack-SearchCatalogFunctionArn'

# Shopper-style queries, rotated so the embedding cache-free path is exercised.
QUERIES = [
    'a gift for my mom, something elegant',
    'black leather crossbody bag',
    'silver necklace with a heart pendant',
    'warm winter hat for my dad',
    'summer dress with flowers',
    'sporty sunglasses for running',
    'a watch for my husband under a hundred dollars',
    'cute earrings for a little girl',
    'cotton t-shirt for men',
    'something purple for my sister',
]

SCENARIOS = {
    # Design default on every turn: limit=200, only availability filtered.
    'unfiltered_limit200': {'limit': 200},
    # Committed category, gender (+unisex) and budget, as later turns send.
    'filtered_limit200': {'limit': 200, 'maxPriceCents': 10000,
                          'categories': ['Jewelry', 'Handbags', 'Watches'], 'genders': ['women', 'unisex']},
    # Smaller page, to show how much of the time is thumbnail presigning.
    'unfiltered_limit50': {'limit': 50},
}


def percentiles(values):
    ordered = sorted(values)
    return {'p50': round(statistics.median(ordered), 1),
            'p95': round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 1)}


def invoke(client, function_name, payload):
    started = time.perf_counter()
    response = client.invoke(FunctionName=function_name, Payload=json.dumps(payload).encode(), LogType='Tail')
    client_ms = (time.perf_counter() - started) * 1000
    body = json.loads(response['Payload'].read())
    if response.get('FunctionError') or 'error' in body:
        raise RuntimeError(f'search_catalog failed: {body}')
    log_tail = base64.b64decode(response.get('LogResult', '')).decode(errors='replace')
    init = re.search(r'Init Duration: ([\d.]+) ms', log_tail)
    return client_ms, body, float(init.group(1)) if init else None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--calls', type=int, default=50)
    parser.add_argument('--region', default='us-east-1')
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    pages = session.client('cloudformation').get_paginator('list_exports').paginate()
    function_name = next(e['Value'] for page in pages for e in page['Exports'] if e['Name'] == FUNCTION_EXPORT)
    client = session.client('lambda', config=Config(read_timeout=30, retries={'total_max_attempts': 1}))

    client_ms, body, init_ms = invoke(client, function_name, {'query': QUERIES[0], **SCENARIOS['unfiltered_limit200']})
    results = {
        'region': args.region,
        'date': time.strftime('%Y-%m-%d'),
        'totalCatalog': body['totalCatalog'],
        'callsPerScenario': args.calls,
        'firstCall': {'clientMs': round(client_ms, 1), 'handlerMs': body['timingsMs'],
                      'lambdaInitDurationMs': init_ms, 'coldStart': init_ms is not None},
        'scenarios': {},
    }

    for name, params in SCENARIOS.items():
        series = {k: [] for k in ('client', 'embed', 'search', 'presign', 'total', 'overhead')}
        hits = []
        for i in range(args.calls):
            client_ms, body, _ = invoke(client, function_name, {'query': QUERIES[i % len(QUERIES)], **params})
            timings = body['timingsMs']
            series['client'].append(client_ms)
            for key in ('embed', 'search', 'presign', 'total'):
                series[key].append(timings[key])
            series['overhead'].append(client_ms - timings['total'])
            hits.append(len(body['hits']))
        results['scenarios'][name] = {
            'params': params,
            'hitsMedian': statistics.median(hits),
            'totalMatchedLast': body['totalMatched'],
            **{key: percentiles(values) for key, values in series.items()},
        }
        print(f'{name}: client p50 {results["scenarios"][name]["client"]["p50"]} ms, '
              f'handler p50 {results["scenarios"][name]["total"]["p50"]} ms')

    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
