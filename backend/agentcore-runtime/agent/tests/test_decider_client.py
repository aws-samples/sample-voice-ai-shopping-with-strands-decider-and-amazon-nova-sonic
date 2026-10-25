import asyncio
import io
import json
import os

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

import decider_client
from questions import Catalog, understand_questions
from state import SessionState

QUESTIONS = {'intent': {'type': 'choice', 'instructions': 'x', 'criteria': {'narrowing': None, 'other': None}},
             'liked_signal': {'type': 'noul', 'instructions': 'y'}}


class FakeRuntime:
    def __init__(self, body=None, error=None):
        self.body, self.error, self.requests = body, error, []

    def invoke_endpoint(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return {'Body': io.BytesIO(json.dumps(self.body).encode())}


def run(coro):
    return asyncio.run(coro)


def test_valid_answers_parsed():
    fake = FakeRuntime({'answers': {'intent': {'type': 'choice', 'choice': 'narrowing', 'probabilities': {'narrowing': 0.9, 'other': 0.1}, 'confidence': 0.87},
                                    'liked_signal': {'type': 'noul', 'noul': 0.2}}, 'latency_ms': 120.0})
    result = run(decider_client.ask('state', QUESTIONS, endpoint_name='e', client=fake))
    assert result.answers['intent'].confidence == 0.87 and result.answers['liked_signal'].noul == 0.2
    assert result.invalid == [] and result.server_ms == 120.0
    assert fake.requests[0]['ContentType'] == 'application/json'


@pytest.mark.parametrize('bad', [
    {'type': 'choice', 'choice': 'nope', 'probabilities': {'narrowing': 0.9, 'other': 0.1}},       # choice not offered
    {'type': 'choice', 'choice': 'narrowing', 'probabilities': {'narrowing': 0.9}},                # missing option
    {'type': 'choice', 'choice': 'narrowing', 'probabilities': {'narrowing': 0.9, 'other': 0.3}},  # sum 1.2
    None,
])
def test_invalid_answers_treated_as_missing(bad):
    fake = FakeRuntime({'answers': {'intent': bad, 'liked_signal': {'type': 'noul', 'noul': 0.5}}})
    result = run(decider_client.ask('state', QUESTIONS, endpoint_name='e', client=fake))
    assert 'intent' not in result.answers and result.invalid == ['intent']


def test_more_than_50_options_rejected_before_sending():
    fake = FakeRuntime({})
    big = {'best_fit': {'type': 'choice', 'instructions': 'x', 'criteria': {str(i): None for i in range(51)}}}
    with pytest.raises(ValueError):
        run(decider_client.ask('state', big, endpoint_name='e', client=fake))
    assert fake.requests == []


def test_throttling_and_timeouts_are_unavailable():
    throttled = ClientError({'Error': {'Code': 'ThrottlingException'}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'InvokeEndpoint')
    with pytest.raises(decider_client.DeciderUnavailable):
        run(decider_client.ask('s', QUESTIONS, endpoint_name='e', client=FakeRuntime(error=throttled)))
    with pytest.raises(decider_client.DeciderUnavailable):
        run(decider_client.ask('s', QUESTIONS, endpoint_name='e', client=FakeRuntime(error=ReadTimeoutError(endpoint_url='x'))))
    bad = ClientError({'Error': {'Code': 'ValidationError'}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'InvokeEndpoint')
    with pytest.raises(decider_client.DeciderError):
        run(decider_client.ask('s', QUESTIONS, endpoint_name='e', client=FakeRuntime(error=bad)))


@pytest.mark.skipif(not os.environ.get('VOICESHOP_LIVE_DECIDER'), reason='set VOICESHOP_LIVE_DECIDER=1 to call the deployed endpoint')
def test_live_endpoint_understand_round():
    state = SessionState(session_id='s')
    questions = understand_questions(state, ['intent', 'recipient', 'gender'], Catalog())
    result = run(decider_client.ask('Latest shopper sentence: "a gift for my mom"\nCommitted: none', questions,
                                    endpoint_name='voiceshop-decider'))
    assert result.invalid == []
    # "mother" carries the gender (questions.RECIPIENT_OPTIONS); the gender question itself may say not mentioned.
    assert result.answers['recipient'].choice == 'mother'
    assert result.answers['gender'].choice in ('women', 'not_mentioned')


def test_a_container_error_is_retried_once():
    from botocore.exceptions import ClientError
    calls = []

    class FlakyRuntime:
        def invoke_endpoint(self, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise ClientError({'Error': {'Code': 'ModelError', 'Message': 'triton'},
                                   'ResponseMetadata': {'HTTPStatusCode': 424}}, 'InvokeEndpoint')
            body = {'answers': {'q': {'type': 'noul', 'noul': 0.9}}}
            import io
            return {'Body': io.BytesIO(json.dumps(body).encode())}

    result = run(decider_client.ask('s', {'q': {'type': 'noul', 'instructions': 'x'}}, endpoint_name='e', client=FlakyRuntime()))
    assert len(calls) == 2 and result.answers['q'].noul == 0.9


def test_a_busy_decider_is_not_retried():
    """The container answers 503 when its queue is full; SageMaker passes it on as a ModelError."""
    from botocore.exceptions import ClientError
    calls = []

    class BusyRuntime:
        def invoke_endpoint(self, **kwargs):
            calls.append(1)
            raise ClientError({'Error': {'Code': 'ModelError', 'Message': 'busy'}, 'OriginalStatusCode': 503,
                               'ResponseMetadata': {'HTTPStatusCode': 424}}, 'InvokeEndpoint')

    with pytest.raises(decider_client.DeciderUnavailable):
        run(decider_client.ask('s', {'q': {'type': 'noul', 'instructions': 'x'}}, endpoint_name='e', client=BusyRuntime()))
    assert len(calls) == 1


def test_the_decider_client_makes_one_attempt_only(monkeypatch):
    """botocore's max_attempts counts retries; a hidden retry after a 3 s timeout would queue the
    same call again behind a busy decider."""
    monkeypatch.setattr(decider_client, '_client', None)
    assert decider_client._sagemaker_runtime().meta.config.retries['total_max_attempts'] == 1
