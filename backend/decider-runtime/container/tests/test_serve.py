"""Back-pressure in the SageMaker wrapper: a bounded queue, a bounded wait, 503 beyond either.
Runs without a GPU or the model (a fake decider app)."""

import asyncio
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load(monkeypatch, queue, wait):
    monkeypatch.setenv('DECIDER_MAX_QUEUE', str(queue))
    monkeypatch.setenv('DECIDER_MAX_WAIT_S', str(wait))
    import serve
    return importlib.reload(serve)


def fake_decider(seconds):
    async def app(scope, receive, send):
        await asyncio.sleep(seconds)
        await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'application/json')]})
        await send({'type': 'http.response.body', 'body': b'{"answers": {}}', 'more_body': False})
    return app


async def invoke(routes):
    sent = []

    async def send(message):
        sent.append(message)

    await routes({'type': 'http', 'path': '/invocations'}, None, send)
    return sent[0]['status']


def test_calls_beyond_the_queue_are_answered_busy_at_once(monkeypatch):
    serve = load(monkeypatch, queue=2, wait=5)

    async def go():
        routes = serve.SageMakerRoutes(fake_decider(0.2), 'test-gpu')
        return await asyncio.gather(*(invoke(routes) for _ in range(5)))
    statuses = asyncio.run(go())
    # One runs, two wait, the other two are turned away.
    assert sorted(statuses) == [200, 200, 200, 503, 503]


def test_a_call_that_would_wait_too_long_is_answered_busy(monkeypatch):
    serve = load(monkeypatch, queue=10, wait=0.1)

    async def go():
        routes = serve.SageMakerRoutes(fake_decider(0.3), 'test-gpu')
        return await asyncio.gather(invoke(routes), invoke(routes))
    assert sorted(asyncio.run(go())) == [200, 503]


def test_the_lock_is_released_after_each_call(monkeypatch):
    serve = load(monkeypatch, queue=1, wait=1)

    async def go():
        routes = serve.SageMakerRoutes(fake_decider(0.01), 'test-gpu')
        return [await invoke(routes) for _ in range(3)]
    assert asyncio.run(go()) == [200, 200, 200]
