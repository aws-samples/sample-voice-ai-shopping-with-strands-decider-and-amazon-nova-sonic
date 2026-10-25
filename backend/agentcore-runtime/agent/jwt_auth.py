"""First-message authentication (adapted from the template's jwt_auth.py).

The first WebSocket message must be {"type": "auth", "access_token": ...}. The
token is verified by Cognito itself (GetUser checks signature, expiry and
revocation). On failure the caller closes the socket with 4401 and never opens
a Sonic stream. Token contents are never logged.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

AUTH_CLOSE_CODE = 4401
MAX_TOKEN_BYTES = 8 * 1024

_cognito = None


def _client():
    global _cognito
    if _cognito is None:
        _cognito = boto3.client('cognito-idp', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
    return _cognito


class AuthError(Exception):
    pass


def _claims(token: str) -> dict:
    """The token's payload, unverified: called only after Cognito has accepted the token."""
    try:
        payload = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except (IndexError, ValueError) as exc:
        raise AuthError('malformed access token') from exc


def check_audience(claims: dict) -> None:
    """GetUser accepts an access token from any user pool in the Region; only this sample's
    user pool and app client may open a session (USER_POOL_ID and USER_POOL_CLIENT_ID, set by
    the runtime stack). Without them configured, every token is rejected."""
    pool, client = os.environ.get('USER_POOL_ID'), os.environ.get('USER_POOL_CLIENT_ID')
    if not (pool and client):
        raise AuthError('USER_POOL_ID and USER_POOL_CLIENT_ID are not configured')
    region = os.environ.get('AWS_REGION', 'us-east-1')
    if claims.get('iss') != f'https://cognito-idp.{region}.amazonaws.com/{pool}':
        raise AuthError('token from another user pool')
    if claims.get('client_id') != client:
        raise AuthError('token for another app client')
    if claims.get('token_use', 'access') != 'access':
        raise AuthError('not an access token')


async def verify(message: dict) -> dict:
    """Returns {'sub', 'username', 'name', 'email'} for a valid auth message, else raises AuthError."""
    if not isinstance(message, dict) or message.get('type') != 'auth':
        raise AuthError('first message must be auth')
    token = message.get('access_token')
    if not isinstance(token, str) or not token or len(token.encode()) > MAX_TOKEN_BYTES:
        raise AuthError('missing or oversized access token')
    try:
        response = await asyncio.to_thread(_client().get_user, AccessToken=token)
    except ClientError as exc:
        code = exc.response.get('Error', {}).get('Code', 'Unknown')
        logger.warning('⚠️ token rejected by Cognito: %s', code)
        raise AuthError(code) from exc
    try:
        check_audience(_claims(token))
    except AuthError as exc:
        logger.warning('⚠️ token rejected: %s', exc)
        raise
    attributes = {a['Name']: a['Value'] for a in response.get('UserAttributes', [])}
    return {'sub': attributes.get('sub', response.get('Username')), 'username': response.get('Username'),
            'name': attributes.get('name'), 'email': attributes.get('email')}
