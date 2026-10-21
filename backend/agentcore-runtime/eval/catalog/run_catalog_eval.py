#!/usr/bin/env python3
"""Labeled evaluation on the synthetic catalog, through the real turn pipeline.

Each scenario in scenarios.jsonl (see build_scenarios.py) runs as one session: the
production TurnPipeline, the deployed decider endpoint, the deployed Gateway for
search and product detail, and CATALOG#META for the question options. add_to_cart
and place_order are answered locally, so the evaluation writes nothing.

Every decider answer of every turn is recorded with its full probabilities. score()
then reports, per question and candidate act threshold, the commit precision and
recall, on the tuning and held-out splits separately, and for no-match (ADR-21) the
none_of_these probability of requests that do and do not fit the catalog.

Usage (credentials for the deployed account):
    python3 run_catalog_eval.py run   [--out results.json] [--concurrency 4]
    python3 run_catalog_eval.py score [--results results.json] [--target 0.95]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / 'agent'))

import boto3  # noqa: E402

import decider_client  # noqa: E402
import policy  # noqa: E402
import questions  # noqa: E402
from state import SessionState  # noqa: E402
from tools_gateway import GatewayTools  # noqa: E402
from turn_pipeline import Deps, TurnPipeline  # noqa: E402

REGION = os.environ.get('AWS_REGION', 'us-east-1')
THRESHOLDS = [round(0.05 * i, 2) for i in range(1, 20)]   # 0.05 .. 0.95
SLOT_QUESTIONS = ('intent', 'recipient', 'gender', 'department', 'category', 'budget_band', 'offer_reply')


def catalog_meta() -> questions.Catalog:
    """The same read as the session host (shop_agent.catalog_meta)."""
    table = boto3.resource('dynamodb', region_name=REGION).Table(os.environ.get('PRODUCTS_TABLE_NAME', 'VoiceShop-Products'))
    item = table.get_item(Key={'PK': 'CATALOG#META', 'SK': 'META'}).get('Item') or {}
    catalog = questions.Catalog(total=int(item.get('itemCount') or 0))
    if item.get('genders'):
        catalog.genders = list(item['genders'])
    catalog.departments = {d: list(c) for d, c in (item.get('departments') or {}).items()}
    catalog.department_descriptions = dict(item.get('departmentDescriptions') or {})
    catalog.category_descriptions = dict(item.get('categoryDescriptions') or {})
    return catalog


def gateway_url() -> str:
    outputs = boto3.client('cloudformation', region_name=REGION).describe_stacks(
        StackName='VoiceShop-AgentCoreGatewayStack')['Stacks'][0]['Outputs']
    return next(o['OutputValue'] for o in outputs if o['OutputKey'] == 'GatewayUrl')


class ReadOnlyTools:
    """Search and detail go to the Gateway; cart, removal and order are answered here."""

    def __init__(self, gateway: GatewayTools):
        self.gateway = gateway
        self.cart = {'items': [], 'itemCount': 0, 'totalCents': 0}

    async def search_catalog(self, arguments: dict) -> dict:
        return await self.gateway.search_catalog(arguments)

    async def get_product_detail(self, arguments: dict) -> dict:
        return await self.gateway.get_product_detail(arguments)

    async def add_to_cart(self, arguments: dict) -> dict:
        self.cart = {'items': self.cart['items'] + [{'asin': arguments['asin'], 'quantity': 1, 'priceCents': 0}],
                     'itemCount': self.cart['itemCount'] + 1, 'totalCents': 0}
        return {'cart': self.cart}

    async def remove_from_cart(self, arguments: dict) -> dict:
        items = [i for i in self.cart['items'] if i['asin'] != arguments['asin']]
        self.cart = {'items': items, 'itemCount': sum(i['quantity'] for i in items), 'totalCents': 0}
        return {'cart': self.cart}

    async def place_order(self, arguments: dict) -> dict:
        return {'error': 'EvaluationRun'}


def answer_record(answer: policy.Answer) -> dict:
    return {'choice': answer.choice, 'confidence': answer.confidence, 'noul': answer.noul,
            'probabilities': answer.probabilities}


async def run_scenario(scenario: dict, gateway: GatewayTools, catalog: questions.Catalog, endpoint: str) -> dict:
    turns_out: list[dict] = []
    current: list[dict] = []

    async def ask(state_text: str, qs: dict):
        result = await decider_client.ask(state_text, qs, endpoint_name=endpoint)
        current.append({'questions': list(qs), 'answers': {k: answer_record(v) for k, v in result.answers.items()},
                        'invalid': result.invalid})
        return result

    async def send(message: dict) -> None:
        return None

    pipeline = TurnPipeline(SessionState(session_id=f"eval-{scenario['id']}-" + 'x' * 24),
                            Deps(tools=ReadOnlyTools(gateway), send=send, catalog=catalog, ask_decider=ask,
                                 thresholds=policy.Thresholds.from_env()))
    await pipeline.initial_shelf()
    for turn in scenario['turns']:
        setup = turn.get('setup') or {}
        if setup.get('open') and len(pipeline.state.shelf) >= setup['open']:
            await pipeline.open_tapped(pipeline.state.shelf[setup['open'] - 1]['asin'])
        if setup.get('offer'):
            offer = setup['offer']
            if offer == 'add_open':
                offer = f"add_to_cart:{pipeline.state.open_product['asin']}" if pipeline.state.open_product else None
            pipeline.state.pending_offer = offer
        current = []
        try:
            outcome = await pipeline.on_final(turn['say'])
            error = None
        except Exception as exc:  # noqa: BLE001
            outcome, error = {}, repr(exc)
        turns_out.append({'say': turn['say'], 'labels': turn['labels'], 'setup': setup, 'calls': current,
                          'directive': outcome.get('directive'), 'reply': outcome.get('say'), 'error': error,
                          'state': {s: (c.value if c else None) for s in ('recipient', 'gender', 'department', 'category')
                                    for c in [pipeline.state.committed(s)]}})
    return {'id': scenario['id'], 'source': scenario['source'], 'split': scenario['split'], 'turns': turns_out}


async def run_all(args) -> None:
    scenarios = [json.loads(line) for line in (HERE / 'scenarios.jsonl').read_text().splitlines() if line.strip()]
    catalog = catalog_meta()
    semaphore = asyncio.Semaphore(args.concurrency)
    async with GatewayTools(gateway_url(), REGION) as gateway:
        async def one(s):
            async with semaphore:
                result = await run_scenario(s, gateway, catalog, args.endpoint)
                print(f"  {s['id']} {len(s['turns'])} turns", flush=True)
                return result
        results = await asyncio.gather(*(one(s) for s in scenarios))
    Path(args.out).write_text(json.dumps({'endpoint': args.endpoint, 'catalogItems': catalog.total,
                                          'scenarios': results}, indent=1))
    print(f'wrote {args.out}')


# ------------------------------------------------------------------ scoring

def occurrences(results: dict, split: str | None):
    """(question, label, answer, source) for every labeled question answered on a turn."""
    for s in results['scenarios']:
        if split and s['split'] != split:
            continue
        for t in s['turns']:
            for call in t['calls']:
                for qid, answer in call['answers'].items():
                    if qid in t['labels'] and qid in SLOT_QUESTIONS:
                        label = t['labels'][qid]
                        yield qid, questions.NOT_MENTIONED if label == '-' else label, answer, s['source'], t['say']


def match_points(results: dict, split: str | None):
    """(fits the catalog, none_of_these probability or None when best_fit was not asked, sentence)."""
    for s in results['scenarios']:
        if split and s['split'] != split:
            continue
        for t in s['turns']:
            if 'match' not in t['labels']:
                continue
            fits = [c['answers']['best_fit'] for c in t['calls'] if 'best_fit' in c['answers']]
            p = fits[-1]['probabilities'].get(questions.NONE_OF_THESE, 0.0) if fits else None
            yield t['labels']['match'], p, t['say'], t['directive']


def table(points: list, qid: str) -> list[dict]:
    rows = []
    stated = [p for p in points if p[1] != questions.NOT_MENTIONED]
    for th in THRESHOLDS:
        commits = [p for p in points if p[2]['choice'] not in (None, questions.NOT_MENTIONED)
                   and (p[2]['confidence'] or 0) >= th
                   and not (qid == 'offer_reply' and p[2]['choice'] == 'new_request')]
        right = [p for p in commits if p[2]['choice'] == p[1]]
        rows.append({'act': th, 'commits': len(commits), 'right': len(right),
                     'precision': round(len(right) / len(commits), 3) if commits else None,
                     'recall': round(len(right) / len(stated), 3) if stated else None})
    return rows


def score(args) -> None:
    results = json.loads(Path(args.results).read_text())
    report = {}
    for split in ('tuning', 'heldout', None):
        name = split or 'all'
        points = list(occurrences(results, split))
        per_q = {}
        for qid in SLOT_QUESTIONS:
            qp = [p for p in points if p[0] == qid]
            if not qp:
                continue
            rows = table(qp, qid)
            best = next((r for r in rows if r['precision'] is not None and r['precision'] >= args.target
                         and all(x['precision'] is None or x['precision'] >= args.target for x in rows if x['act'] >= r['act'])), None)
            per_q[qid] = {'n': len(qp), 'top1': round(sum(p[2]['choice'] == p[1] for p in qp) / len(qp), 3),
                          'lowestActAtTarget': best, 'rows': rows,
                          'errors': [{'say': p[4], 'label': p[1], 'got': p[2]['choice'], 'p': round(p[2]['confidence'] or 0, 3)}
                                     for p in qp if p[2]['choice'] != p[1]]}
        mp = list(match_points(results, split))
        nm_rows = []
        for th in THRESHOLDS[:12]:
            fits = [m for m in mp if m[0] and m[1] is not None]
            out = [m for m in mp if not m[0] and m[1] is not None]
            nm_rows.append({'noMatchAt': th, 'falseNoMatch': sum(m[1] >= th for m in fits), 'fitsAsked': len(fits),
                            'trueNoMatch': sum(m[1] >= th for m in out), 'outOfCatalogAsked': len(out)})
        report[name] = {'questions': per_q, 'noMatch': nm_rows,
                        'noMatchPoints': [{'fits': m[0], 'p': None if m[1] is None else round(m[1], 3), 'say': m[2],
                                           'directive': m[3]} for m in mp]}
    out = Path(args.results).with_name(Path(args.results).stem + '-score.json')
    out.write_text(json.dumps(report, indent=1))
    for name in ('tuning', 'heldout'):
        print(f'\n=== {name}')
        for qid, r in report[name]['questions'].items():
            b = r['lowestActAtTarget']
            print(f"  {qid:12} n={r['n']:3} top1={r['top1']:.2f}  lowest act at >= {args.target}: "
                  + (f"{b['act']:.2f} (precision {b['precision']}, recall {b['recall']}, {b['commits']} commits)" if b else 'none'))
    print(f'\nwrote {out}')


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['run', 'score'])
    parser.add_argument('--endpoint', default=os.environ.get('DECIDER_ENDPOINT_NAME', 'voiceshop-decider'))
    parser.add_argument('--out', default=str(HERE / 'results.json'))
    parser.add_argument('--results', default=str(HERE / 'results.json'))
    parser.add_argument('--concurrency', type=int, default=4)
    parser.add_argument('--target', type=float, default=0.95)
    args = parser.parse_args()
    if args.command == 'run':
        asyncio.run(run_all(args))
    else:
        score(args)


if __name__ == '__main__':
    main()
