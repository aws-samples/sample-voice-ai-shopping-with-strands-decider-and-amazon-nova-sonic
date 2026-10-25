"""Security fixes from the 2026-10-23 AppSec review: session ownership, per-connection rate
limits, token audience, idempotency keys, serialized cart writes, strict audio decoding; and from
the re-run: turn and decider limits that also cover speech, session claims, frame sizes."""

import asyncio
import base64
import json

import pytest

import jwt_auth
import shop_agent
import turn_pipeline
from state import SessionState

from tests.test_turn_pipeline import CATALOG, FakeTools, MOM, ans, make, run


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


def test_a_host_without_the_pool_settings_rejects_tokens(monkeypatch):
    monkeypatch.delenv('USER_POOL_ID', raising=False)
    monkeypatch.delenv('USER_POOL_CLIENT_ID', raising=False)
    with pytest.raises(jwt_auth.AuthError):
        jwt_auth.check_audience({'token_use': 'access'})


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
        add = asyncio.create_task(p._add(p.state.turn_seq, CATALOG[1]['asin'], f"add_to_cart:{CATALOG[1]['asin']}"))
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


def test_a_cart_tap_does_not_mark_a_turn_as_committing():
    async def go():
        p, tools, _, _ = make(MOM)
        await p.on_final('a gift for my mom')
        line = {'asin': CATALOG[0]['asin'], 'title': CATALOG[0]['title'], 'quantity': 1, 'priceCents': CATALOG[0]['priceCents']}
        tools.cart = {'items': [line], 'itemCount': 1, 'totalCents': line['priceCents']}
        p.state.cart = dict(tools.cart)
        await p.remove_tapped(CATALOG[0]['asin'])
        return p
    p = run(go())
    assert p._committing is None


class FakeSessions:
    """The VoiceShop-Sessions table with DynamoDB's conditional put."""

    def __init__(self, fail_reads=False):
        self.items, self.fail_reads = {}, fail_reads

    def get_item(self, Key):
        if self.fail_reads:
            raise RuntimeError('DynamoDB unavailable')
        item = self.items.get((Key['PK'], Key['SK']))
        return {'Item': item} if item else {}

    def put_item(self, Item, ConditionExpression=None, ExpressionAttributeValues=None):
        from botocore.exceptions import ClientError
        current = self.items.get((Item['PK'], Item['SK']))
        if ConditionExpression and current and current.get('actorId') != ExpressionAttributeValues[':me']:
            raise ClientError({'Error': {'Code': 'ConditionalCheckFailedException'}}, 'PutItem')
        self.items[(Item['PK'], Item['SK'])] = Item


def test_a_session_id_is_saved_only_by_its_owner(monkeypatch):
    import state_store
    table = FakeSessions()
    monkeypatch.setattr(state_store, '_sessions', lambda: table)
    sid = 's-' + 'x' * 40
    assert asyncio.run(state_store.save(SessionState(session_id=sid, actor_id='owner')))
    assert asyncio.run(state_store.save(SessionState(session_id=sid, actor_id='owner')))
    assert not asyncio.run(state_store.save(SessionState(session_id=sid, actor_id='attacker')))
    assert table.items[(f'SESSION#{sid}', 'STATE')]['actorId'] == 'owner'


def test_a_failed_state_read_is_not_a_new_session(monkeypatch):
    import state_store
    monkeypatch.setattr(state_store, '_sessions', lambda: FakeSessions(fail_reads=True))
    with pytest.raises(state_store.StateUnavailable):
        asyncio.run(state_store.load('s-' + 'x' * 40))


def test_an_oversized_audio_frame_is_invalid():
    state = SessionState(session_id='s-' + 'x' * 40)
    assert shop_agent.valid({'type': 'bidi_audio_input', 'audio': 'A' * 2732}, state)        # one 64 ms frame
    assert not shop_agent.valid({'type': 'bidi_audio_input', 'audio': 'A' * (shop_agent.MAX_AUDIO_B64 + 1)}, state)


def test_a_sentence_after_a_waiting_turn_also_waits_for_the_cart_write():
    """A "yes" is adding; the next sentence waits for it; a third sentence replaces the waiting
    one and must wait for the add too, not run alongside it."""
    async def go():
        p, tools, _, _ = make(MOM, tools=FakeTools(add_delay=0.2))
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95)}
        await p.on_final('the second one')
        p.deps.ask_decider.script = {'intent': ans('intent', 'confirm', 0.95)}
        first = p.on_final('yes')
        await asyncio.sleep(0.05)   # the add is in flight
        p.on_final('and also')
        started_after_add = []
        turn = p._turn

        async def recording_turn(text):
            started_after_add.append(first.done())
            return await turn(text)
        p._turn = recording_turn
        await p.on_final('show me more like this')
        return started_after_add
    assert run(go()) == [True]


def test_huge_spoken_amounts_are_capped_not_fatal():
    import filters
    assert filters.parse_budget('under $' + '9' * 400) is None   # infinite: no budget, no crash
    assert filters.parse_budget('under $' + '9' * 30).max_cents == filters.MAX_PRICE_CENTS
    assert filters.parse_budget('under 80 dollars').max_cents == 8000


class SlowAddTools(FakeTools):
    """The add commits in the store but its response never arrives in time."""

    def __init__(self):
        super().__init__()
        self.timeouts = 1

    async def add_to_cart(self, args):
        result = await super().add_to_cart(args)
        if self.timeouts:
            self.timeouts -= 1
            raise turn_pipeline.ToolError('add_to_cart timed out')
        return result


def test_closing_the_socket_stops_a_turn_that_is_not_writing():
    async def go():
        p, *_ = make(MOM, tools=FakeTools(search_delay=0.5))
        p.on_final('a gift for my mom')
        await asyncio.sleep(0.05)
        turn = p._turn_task
        await p.shutdown()
        await asyncio.sleep(0)
        return turn
    turn = run(go())
    assert turn.cancelled()


def test_a_stopped_bridge_never_publishes_a_late_stream(monkeypatch):
    import sonic_bridge

    class FakeAgent:
        stopped = False
        def __init__(self, **_): pass
        async def start(self): await asyncio.sleep(0)
        async def stop(self): FakeAgent.stopped = True
        async def receive(self):
            await asyncio.Event().wait()
            yield {}

    monkeypatch.setattr(sonic_bridge, 'BidiAgent', FakeAgent)
    monkeypatch.setattr(sonic_bridge, 'BedrockNovaSonicModel', lambda **_: None)

    async def go():
        bridge = sonic_bridge.SonicBridge.__new__(sonic_bridge.SonicBridge)
        bridge.__dict__.update(remembered=[], region='us-east-1', model_id='m', voice='v', agent=None, _receiver=None,
                               _silence=None, _opening=None, _background=set(), _stopped=False, pipeline=None)
        bridge._tools = lambda: []
        await bridge.stop()
        await bridge.start()
        return bridge
    bridge = run(go())
    assert bridge.agent is None and FakeAgent.stopped


def test_a_shopper_over_the_per_minute_limit_gets_a_hold_on_every_connection():
    """The limit is per shopper (actor), so a second connection does not reset it."""
    counts = {}

    async def allowed(actor_id):
        counts[actor_id] = counts.get(actor_id, 0) + 1
        return counts[actor_id] <= 2

    async def go():
        outcomes = []
        for _ in range(2):   # two connections of the same shopper
            p, tools, _, sent = make(MOM)
            p.state.actor_id = 'shopper-1'
            p.deps.user_turn_allowed = allowed
            outcomes.append(await p.on_final('a gift for my mom'))
            outcomes.append(await p.on_final('a gift for my mom'))
        return outcomes
    outcomes = run(go())
    assert [o['directive'] for o in outcomes[2:]] == ['hold', 'hold']
    assert outcomes[0]['directive'] != 'hold'


def test_the_per_shopper_counter_fails_open(monkeypatch):
    import state_store

    class Broken:
        def update_item(self, **_):
            raise RuntimeError('DynamoDB unavailable')
    monkeypatch.setattr(state_store, '_sessions', lambda: Broken())
    assert asyncio.run(state_store.user_turn_allowed('shopper-1')) is True


def test_the_per_shopper_counter_counts_in_one_minute_window(monkeypatch):
    import state_store
    seen = []

    class Table:
        def update_item(self, Key, **kwargs):
            seen.append(Key)
            return {'Attributes': {'turns': len(seen)}}
    monkeypatch.setattr(state_store, '_sessions', lambda: Table())
    monkeypatch.setattr(state_store, 'USER_TURNS_PER_MINUTE', 2)
    results = [asyncio.run(state_store.user_turn_allowed('shopper-1')) for _ in range(3)]
    assert results == [True, True, False]
    assert seen[0]['PK'] == 'USER#shopper-1' and seen[0]['SK'].startswith('TURNS#')


def test_shutdown_lets_a_cart_write_finish():
    async def go():
        p, tools, _, _ = make(MOM, tools=FakeTools(add_delay=0.3))
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95)}
        await p.on_final('the second one')
        p.deps.ask_decider.script = {'intent': ans('intent', 'confirm', 0.95)}
        p.on_final('yes')
        await asyncio.sleep(0.05)              # the add is in flight
        await p.shutdown()
        return p, tools
    p, tools = run(go())
    assert tools.cart['itemCount'] == 1 and p.state.cart['itemCount'] == 1




def test_an_unconfirmed_add_is_offered_again_with_the_same_key():
    """The add may have committed: the store asks again, and the yes retries with the same key,
    so the item is added once. Any other offer gets a new key."""
    async def go():
        p, tools, _, _ = make(MOM, tools=SlowAddTools())
        await p.on_final('a gift for my mom')
        x = CATALOG[0]['asin']
        first = await p._add(p.state.turn_seq, x, f'add_to_cart:{x}')
        offer = p.state.pending_offer
        p.state.pending_offer = None   # the confirm path pops the offer before carrying it out
        second = await p._add(p.state.turn_seq, x, offer)
        third = await p._add(p.state.turn_seq, CATALOG[1]['asin'], f"add_to_cart:{CATALOG[1]['asin']}")
        return first, offer, second, third, tools
    first, offer, second, third, tools = run(go())
    keys = [a['idempotencyKey'] for kind, a in tools.calls if kind == 'add']
    assert first['confirmation'] and offer == f"add_to_cart:{CATALOG[0]['asin']}"
    assert second['directive'] == 'act' and keys[0] == keys[1] and keys[2] != keys[0]
    assert tools.cart['items'][0]['quantity'] == 1
