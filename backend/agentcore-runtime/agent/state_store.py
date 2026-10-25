"""Session snapshots in VoiceShop-Sessions: the STATE
item is restored on WebSocket reconnect, and one TURN#<seq> item per turn records
what happened. Writes are async and never block a turn; items expire after 24 h."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

from state import SessionState

logger = logging.getLogger(__name__)

TTL_S = 24 * 3600
# Sentences per shopper per minute, across all of their connections (each connection also
# has its own 1-a-second limit). Normal speech is well under this.
USER_TURNS_PER_MINUTE = int(os.environ.get('USER_TURNS_PER_MINUTE', '60'))
_table = None


class StateUnavailable(Exception):
    """The saved state could not be read, so its owner is unknown."""


def _sessions():
    global _table
    if _table is None:
        _table = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1')) \
            .Table(os.environ.get('SESSIONS_TABLE_NAME', 'VoiceShop-Sessions'))
    return _table


def _ddb(value):
    """JSON round trip with floats as Decimal, which DynamoDB requires."""
    return json.loads(json.dumps(value), parse_float=Decimal)


async def load(session_id: str) -> SessionState | None:
    """The saved state, or None if there is none. Raises StateUnavailable if the read fails:
    the caller must not treat that as a new session, because the id may belong to someone else."""
    try:
        response = await asyncio.to_thread(_sessions().get_item, Key={'PK': f'SESSION#{session_id}', 'SK': 'STATE'})
    except Exception as exc:  # noqa: BLE001
        logger.warning('⚠️ state load failed', exc_info=True)
        raise StateUnavailable() from exc
    item = response.get('Item')
    if not item:
        return None
    data = json.loads(json.dumps(item['state'], default=float))
    logger.info('💾 restored session state (turn %s)', data.get('turn_seq'))
    return SessionState.from_dict(data)


async def user_turn_allowed(actor_id: str) -> bool:
    """Counts one sentence for this shopper in the current minute (one atomic counter in the
    Sessions table, expiring after two minutes). Fails open: if DynamoDB cannot answer, the
    per-connection limits still apply and the shopper is not blocked."""
    minute = int(time.time()) // 60
    try:
        response = await asyncio.to_thread(
            _sessions().update_item, Key={'PK': f'USER#{actor_id}', 'SK': f'TURNS#{minute}'},
            UpdateExpression='ADD turns :one SET expiresAt = :exp',
            ExpressionAttributeValues={':one': 1, ':exp': (minute + 2) * 60}, ReturnValues='UPDATED_NEW')
    except Exception:  # noqa: BLE001
        logger.warning('⚠️ per-shopper turn count failed; allowing the turn', exc_info=True)
        return True
    return int(response['Attributes']['turns']) <= USER_TURNS_PER_MINUTE


async def save(state: SessionState, turn: dict | None = None) -> bool:
    """Writes the snapshot only if the session is new or already this shopper's; returns False
    if it was not written (another shopper's session, or an error)."""
    expires = int(time.time()) + TTL_S
    snapshot = state.to_dict()
    snapshot['history'] = snapshot['history'][-3:]   # keep the item well under 400 KB
    try:
        await asyncio.to_thread(_sessions().put_item, Item={
            'PK': f'SESSION#{state.session_id}', 'SK': 'STATE', 'state': _ddb(snapshot), 'actorId': state.actor_id,
            'updatedAt': int(time.time()), 'expiresAt': expires},
            ConditionExpression='attribute_not_exists(PK) OR actorId = :me',
            ExpressionAttributeValues={':me': state.actor_id})
        if turn:
            await asyncio.to_thread(_sessions().put_item, Item={
                'PK': f'SESSION#{state.session_id}', 'SK': f"TURN#{turn['turn']:06d}", 'turn': _ddb(turn),
                'expiresAt': expires})
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            logger.warning('⚠️ session %s belongs to another shopper; snapshot not saved', state.session_id[:8])
            return False
        logger.warning('⚠️ state snapshot failed; next turn retries', exc_info=True)
        return False
    except Exception:  # noqa: BLE001
        logger.warning('⚠️ state snapshot failed; next turn retries', exc_info=True)
        return False
    return True
