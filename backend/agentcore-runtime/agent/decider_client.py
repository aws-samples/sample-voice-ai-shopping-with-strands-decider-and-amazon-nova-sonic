"""Strands Decider client (ADR-22).

The only module that knows the systemone wire format. Calls the SageMaker
endpoint with InvokeEndpoint through asyncio.to_thread, using one module-level
client with short timeouts and no SDK retries. Rejects more than 50 options per
question before sending, validates every answer, and treats an invalid answer
as missing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ReadTimeoutError, ConnectTimeoutError

from policy import Answer
from questions import MAX_OPTIONS

logger = logging.getLogger(__name__)

MODEL = 'strands-decider-latest'
PROBABILITY_SUM_TOLERANCE = 0.02

_client = None


def _sagemaker_runtime():
    global _client
    if _client is None:
        _client = boto3.client(
            'sagemaker-runtime',
            region_name=os.environ.get('AWS_REGION', 'us-east-1'),
            config=Config(connect_timeout=1, read_timeout=3, retries={'max_attempts': 1, 'mode': 'standard'}),
        )
    return _client


class DeciderUnavailable(Exception):
    """Throttled, 5xx or timed out: load above capacity or scaling out. The turn degrades."""


class DeciderError(Exception):
    """Any other failure (bad request, unexpected response)."""


@dataclass
class DeciderResult:
    answers: dict[str, Answer]
    server_ms: float | None
    client_ms: float
    invalid: list[str]


def validate(question_id: str, spec: dict, raw: dict | None) -> Answer | None:
    """Returns a valid Answer, or None (treated as missing). Rules:
    choice: every requested criterion present in probabilities, choice is one of
    them, probabilities in [0, 1] summing to 1 ± 0.02; noul in [0, 1]."""
    if not isinstance(raw, dict):
        return None
    if spec['type'] == 'noul':
        value = raw.get('noul')
        if isinstance(value, (int, float)) and 0.0 <= value <= 1.0:
            return Answer(question_id=question_id, choice=None, confidence=float(value), noul=float(value))
        return None
    probabilities = raw.get('probabilities')
    choice = raw.get('choice')
    if not isinstance(probabilities, dict) or set(spec['criteria']) - set(probabilities):
        return None
    if choice not in spec['criteria']:
        return None
    values = [probabilities[k] for k in spec['criteria']]
    if not all(isinstance(v, (int, float)) and 0.0 <= v <= 1.0 for v in values):
        return None
    if abs(sum(values) - 1.0) > PROBABILITY_SUM_TOLERANCE:
        return None
    confidence = raw.get('confidence')
    return Answer(question_id=question_id, choice=choice,
                  confidence=float(confidence) if isinstance(confidence, (int, float)) else float(probabilities[choice]),
                  probabilities={k: float(probabilities[k]) for k in spec['criteria']})


def build_request(state_text: str, questions: dict) -> dict:
    for question_id, spec in questions.items():
        if spec['type'] == 'choice' and not (2 <= len(spec['criteria']) <= MAX_OPTIONS):
            raise ValueError(f'{question_id} has {len(spec["criteria"])} options; allowed 2-{MAX_OPTIONS}')
    return {'state': state_text, 'model': MODEL, 'questions': questions}


def parse_response(questions: dict, body: dict) -> tuple[dict[str, Answer], list[str]]:
    raw_answers = body.get('answers') or {}
    answers, invalid = {}, []
    for question_id, spec in questions.items():
        answer = validate(question_id, spec, raw_answers.get(question_id))
        if answer is None:
            invalid.append(question_id)
        else:
            answers[question_id] = answer
    return answers, invalid


async def ask(state_text: str, questions: dict, endpoint_name: str | None = None, client=None) -> DeciderResult:
    """One decider round. Raises DeciderUnavailable or DeciderError. A container error
    (ModelError or 5xx) is retried once: the questions are read-only, and a fresh decider
    endpoint failed some concurrent calls while its kernels were still being tuned
    (2026-10-21). Timeouts are not retried, so a slow decider never doubles the wait."""
    request = build_request(state_text, questions)
    endpoint = endpoint_name or os.environ['DECIDER_ENDPOINT_NAME']
    runtime = client or _sagemaker_runtime()

    def call():
        return runtime.invoke_endpoint(EndpointName=endpoint, ContentType='application/json',
                                       Accept='application/json', Body=json.dumps(request).encode())

    def call_with_one_retry():
        try:
            return call()
        except ClientError as exc:
            code = exc.response.get('Error', {}).get('Code', '')
            status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode', 0)
            if code != 'ModelError' and status < 500:
                raise
            if exc.response.get('OriginalStatusCode') == 503:
                raise   # the decider's queue is full: a retry would only add to it
            logger.warning('⚠️ decider %s (%s); retrying once', code, status)
            return call()

    started = time.perf_counter()
    try:
        response = await asyncio.to_thread(call_with_one_retry)
        body = json.loads(response['Body'].read())
    except (ReadTimeoutError, ConnectTimeoutError) as exc:
        raise DeciderUnavailable(f'timeout: {exc}') from exc
    except ClientError as exc:
        code = exc.response.get('Error', {}).get('Code', '')
        status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode', 0)
        if code in ('ThrottlingException', 'ServiceUnavailable', 'ModelNotReadyException') or status >= 500 \
                or code == 'ModelError':
            raise DeciderUnavailable(f'{code} ({status})') from exc
        raise DeciderError(f'{code} ({status}): {exc}') from exc
    except (BotoCoreError, ValueError) as exc:
        raise DeciderError(str(exc)) from exc
    client_ms = (time.perf_counter() - started) * 1000
    answers, invalid = parse_response(questions, body)
    if invalid:
        logger.warning('⚠️ decider returned invalid or missing answers for %s', invalid)
    return DeciderResult(answers=answers, server_ms=body.get('latency_ms'), client_ms=client_ms, invalid=invalid)
