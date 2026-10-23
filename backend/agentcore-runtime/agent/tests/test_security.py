"""Security fixes from the 2026-10-23 AppSec review: session ownership, per-connection rate
limits, token audience, idempotency keys, serialized cart writes, strict audio decoding."""

import asyncio
import base64
import json

import pytest

import jwt_auth
import shop_agent
import turn_pipeline
from state import SessionState

from tests.test_turn_pipeline import CATALOG, FakeTools, MOM, make, run


def test_a_stored_session_opens_only_for_its_owner():
    stored = SessionState(session_id='s-' + 'x' * 40, actor_id='owner-sub')
    assert shop_agent.owned_state(stored, 'owner-sub') is stored
    assert shop_agent.owned_state(stored, 'someone-else') is None
    assert shop_agent.owned_state(None, 'owner-sub') is None


def test_token_bucket_allows_a_burst_then_the_rate():
    now = [0.0]
    bucket = shop_agent.TokenBucket(rate=1.0, burst=5, clock=lambda: now[0])
    assert all(bucket.take() for _ in range(5)) and not bucket.take()
    now[0] += 1.0
    assert bucket.take() and not bucket.take()


def test_normal_speech_fits_the_audio_limit():
    """About 16 frames a second for a minute stays within the audio bucket."""
    now = [0.0]
    rate, burst = shop_agent.RATE_LIMITS['bidi_audio_input']
    bucket = shop_agent.TokenBucket(rate, burst, clock=lambda: now[0])
    taken = 0
    for _ in range(16 * 60):
        now[0] += 1 / 16
        taken += bucket.take()
    assert taken == 16 * 60


def test_rate_limits_are_per_message_kind():
    buckets = {}
    assert shop_agent.limiter_for(buckets, 'ui.product_tap') is shop_agent.limiter_for(buckets, 'ui.layout')
    assert shop_agent.limiter_for(buckets, 'bidi_text_input') is not shop_agent.limiter_for(buckets, 'ui.layout')


def _token(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return f'header.{body}.signature'


def test_tokens_from_another_pool_or_client_are_rejected(monkeypatch):
    monkeypatch.setenv('USER_POOL_ID', 'us-east-1_Pool')
    monkeypatch.setenv('USER_POOL_CLIENT_ID', 'client-1')
    monkeypatch.setenv('AWS_REGION', 'us-east-1')
    good = {'iss': 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Pool', 'client_id': 'client-1', 'token_use': 'access'}
    jwt_auth.check_audience(jwt_auth._claims(_token(good)))
    for bad in ({**good, 'iss': 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Other'},
                {**good, 'client_id': 'client-2'}, {**good, 'token_use': 'id'}):
        with pytest.raises(jwt_auth.AuthError):
            jwt_auth.check_audience(jwt_auth._claims(_token(bad)))
    with pytest.raises(jwt_auth.AuthError):
        jwt_auth._claims('not-a-jwt')


def test_idempotency_keys_are_unique_and_fit_the_tools():
    session = 'session-' + 'x' * 92   # the longest session id the tools accept (100)
    keys = {turn_pipeline.request_key(session) for _ in range(100)}
    assert len(keys) == 100 and all(len(k) <= 140 for k in keys)


class RacyCart(FakeTools):
    """Like DynamoDB under a race: the add reads the cart as it was when the call started, but its
    response arrives later, after a remove has already changed the stored cart."""

    async def add_to_cart(self, args):
        self.calls.append(('add', args))
        snapshot = [dict(i) for i in self.cart['items']]
        await asyncio.sleep(0.2)
        product = next(p for p in CATALOG if p['asin'] == args['asin'])
        line = {'asin': args['asin'], 'title': product['title'], 'quantity': 1, 'priceCents': product['priceCents']}
        self._set([dict(i) for i in self.cart['items']] + [line])    # the store applies both writes
        stale = snapshot + [line]
        return {'cart': {'items': stale, 'itemCount': len(stale), 'totalCents': 0}}


def test_a_tapped_remove_waits_for_a_running_add():
    """The tap and the turn's add each set the cart shown on screen from their response; without
    one write at a time, the add's stale response would bring back the removed line."""
    async def go():
        p, tools, _, sent = make(MOM, tools=RacyCart())
        await p.on_final('a gift for my mom')
        line = {'asin': CATALOG[0]['asin'], 'title': CATALOG[0]['title'], 'quantity': 1, 'priceCents': CATALOG[0]['priceCents']}
        tools.cart = {'items': [line], 'itemCount': 1, 'totalCents': line['priceCents']}
        p.state.cart = dict(tools.cart)
        add = asyncio.create_task(p._add(p.state.turn_seq, CATALOG[1]['asin']))
        await asyncio.sleep(0.05)   # the add is in flight
        await p.remove_tapped(CATALOG[0]['asin'])
        await add
        return p, tools
    p, tools = run(go())
    assert [i['asin'] for i in p.state.cart['items']] == [i['asin'] for i in tools.cart['items']] == [CATALOG[1]['asin']]


def test_a_malformed_audio_frame_is_dropped(monkeypatch):
    from sonic_bridge import SonicBridge
    sent = []

    class Agent:
        async def send(self, event):
            sent.append(event)

    bridge = SonicBridge.__new__(SonicBridge)
    bridge.agent, bridge._pending, bridge._last_mic_frame = Agent(), [], 0.0
    asyncio.run(bridge.audio('not base64!!'))
    asyncio.run(bridge.audio(base64.b64encode(b'\x00\x01' * 8).decode()))
    assert len(sent) == 1
