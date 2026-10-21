#!/usr/bin/env python3
"""Text-mode test client for the session host. Signs in with Cognito, connects either to a local host
(--url ws://127.0.0.1:8080/ws) or to the deployed AgentCore runtime (--runtime-arn,
SigV4-presigned like the browser), plays scripted sentences as bidi_text_input,
and prints what the shopper would see and hear for each turn. Audio chunks are
counted, not played: with spoken replies off, only confirmation turns should
carry audio (ADR-23).

Usage:
    python3 text_client.py --credentials tester.json --url ws://127.0.0.1:8080/ws
    python3 text_client.py --credentials tester.json --runtime-arn arn:aws:bedrock-agentcore:...
    [--voice-replies] [--say "a gift for my mom" --say "the second one" ...]
"""

import argparse
import asyncio
import json
import time
import urllib.parse
import uuid

import boto3
import websockets
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest

SHOW_DEBUG = False
DEFAULT_SCRIPT = [
    'a gift for my mom under eighty dollars', 'something in purple', 'the second one',
    'yes, add it to my cart', 'place my order',
]


def access_token(creds: dict, region: str) -> str:
    idp = boto3.client('cognito-idp', region_name=region)
    result = idp.initiate_auth(ClientId=creds['client'], AuthFlow='USER_PASSWORD_AUTH',
                               AuthParameters={'USERNAME': creds['username'], 'PASSWORD': creds['password']})
    return result['AuthenticationResult']['AccessToken']


def presigned_url(runtime_arn: str, region: str, session_id: str) -> str:
    host = f'bedrock-agentcore.{region}.amazonaws.com'
    query = urllib.parse.urlencode({'qualifier': 'DEFAULT', 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': session_id})
    url = f'https://{host}/runtimes/{urllib.parse.quote(runtime_arn, safe="")}/ws?{query}'
    request = AWSRequest(method='GET', url=url, headers={'host': host})
    SigV4QueryAuth(boto3.Session().get_credentials().get_frozen_credentials(), 'bedrock-agentcore', region, expires=300).add_auth(request)
    return request.url.replace('https://', 'wss://')


async def run(args) -> None:
    creds = json.load(open(args.credentials))
    token = access_token(creds, args.region)
    session_id = f'text-client-{uuid.uuid4()}'
    url = args.url or presigned_url(args.runtime_arn, args.region, session_id)
    headers = {'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': session_id} if args.url else None
    async with websockets.connect(url, additional_headers=headers, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({'type': 'auth', 'access_token': token}))
        await ws.send(json.dumps({'type': 'ui.layout', 'columns': 4}))
        await ws.send(json.dumps({'type': 'ui.voice_replies', 'enabled': args.voice_replies}))
        await drain(ws, until=lambda m: m.get('type') == 'ui.status' and m.get('kind') in ('listening', 'error'), timeout=30, label='connect')
        for sentence in (args.say or DEFAULT_SCRIPT):
            if sentence.startswith('@wait '):   # e.g. --say "@wait 60": stay idle, as a reading shopper would
                print(f'\n(idle {sentence.split()[1]} s)')
                await drain(ws, until=lambda m: False, timeout=float(sentence.split()[1]), label='idle')
                continue
            started = time.perf_counter()
            await ws.send(json.dumps({'type': 'bidi_text_input', 'text': sentence}))
            print(f'\n> {sentence}')
            await drain(ws, until=lambda m: m.get('type') == 'ui.debug', timeout=20, label='turn', started=started,
                        settle=args.settle)


async def drain(ws, until, timeout, label, started=None, settle=0.0):
    audio = 0
    assistant = ''
    deadline = time.monotonic() + timeout
    done_at = None
    while time.monotonic() < deadline and (done_at is None or time.monotonic() < done_at):
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        m = json.loads(raw)
        kind = m.get('type')
        if kind == 'bidi_audio_stream':
            audio += 1
        elif kind == 'bidi_transcript_stream' and m.get('role') == 'assistant':
            assistant = m['text'] if m.get('is_final') else assistant + m.get('text', '')
        elif kind == 'ui.chips':
            print('  chips:', [f"{c['label']}: {c['value']}" + (f" {round(c['confidence'] * 100)}%" if c['slot'] != 'budget' else '') for c in m['chips']])
        elif kind == 'ui.shelf':
            print(f"  shelf: {len(m['items'])} items, e.g. {[i['title'][:30] for i in m['items'][:2]]} (tentative={m['tentative']})")
        elif kind == 'ui.product_open':
            print(f"  opened: {m['product']['title'][:60]} | why: {m['why']}")
        elif kind in ('ui.cart', 'ui.order_placed'):
            print(f"  {kind}: {m.get('itemCount')} items, {m.get('totalCents')} cents")
        elif kind == 'ui.status' and m.get('kind') not in ('thinking', 'listening'):
            print(f"  status {m['kind']}: {m['message']}")
        elif kind == 'ui.debug':
            print(f"  timings: {m['timings']}")
            if SHOW_DEBUG:
                for q in m.get('questions', []):
                    print(f"    {q['id']}: " + ', '.join(f"{t['option']} {t['p']}" for t in q['top']))
        elif kind == 'error':
            print('  ERROR', m)
        if done_at is None and until(m):
            done_at = time.monotonic() + settle   # keep listening for Sonic's reply and audio
    if assistant:
        print(f"  sonic: {assistant.strip()[:160]!r}")
    print(f'  audio chunks: {audio}' + (f' ({label})' if label != 'turn' else ''))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--credentials', required=True, help='JSON with pool, client, username, password (never commit it)')
    parser.add_argument('--url')
    parser.add_argument('--runtime-arn')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--voice-replies', action='store_true')
    parser.add_argument('--settle', type=float, default=4.0, help='seconds to keep listening after each turn for Sonic')
    parser.add_argument('--say', action='append')
    parser.add_argument('--debug', action='store_true', help='print the top decider answers per question')
    args = parser.parse_args()
    global SHOW_DEBUG
    SHOW_DEBUG = args.debug
    if not (args.url or args.runtime_arn):
        parser.error('one of --url or --runtime-arn is required')
    asyncio.run(run(args))


if __name__ == '__main__':
    main()
