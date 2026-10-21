"""VoiceShop session host: AgentCore Runtime WebSocket entrypoint.

GET /ping and /health for the runtime, and /ws for one shopping session. The
first message must be `auth`: an invalid token closes the socket with 4401 and
no Sonic stream is opened. Browser messages are validated against the WebSocket
contract; after 20 invalid ones the socket is closed with 1008.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid

import boto3
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import jwt_auth
import policy
import positions
import state_store
import ui_events
from memory import SessionMemory
from sonic_bridge import SonicBridge
from state import SessionState
from tools_gateway import GatewayTools
from questions import Catalog
from turn_pipeline import Deps, TurnPipeline

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger('shop_agent')

MAX_INVALID_MESSAGES = 20
MAX_AUDIO_B64 = 64 * 1024
ASIN = re.compile(r'^[A-Z0-9]{10}$')
SESSION_ID = re.compile(r'^[A-Za-z0-9-]{36,100}$')   # the cart tools' pattern
SLOTS = {'recipient', 'gender', 'department', 'category', 'budget'}
SESSION_HEADER = 'x-amzn-bedrock-agentcore-runtime-session-id'

app = FastAPI()

# Checked at startup: /ping fails if the thresholds are invalid (design invariant).
try:
    THRESHOLDS = policy.Thresholds.from_env()
    STARTUP_ERROR = None
except ValueError as exc:
    THRESHOLDS, STARTUP_ERROR = None, str(exc)
    logger.error('❌ invalid thresholds: %s', exc)

_ddb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))


@app.get('/ping')
async def ping():
    if STARTUP_ERROR:
        return JSONResponse({'status': 'Unhealthy', 'error': STARTUP_ERROR}, status_code=500)
    return {'status': 'Healthy'}


@app.get('/health')
async def health():
    return await ping()


async def catalog_meta() -> Catalog:
    """Departments, categories and their descriptions from CATALOG#META (written by
    the catalog loader); the decider's options come from here."""
    try:
        item = (await asyncio.to_thread(_ddb.Table(os.environ.get('PRODUCTS_TABLE_NAME', 'VoiceShop-Products')).get_item,
                                        Key={'PK': 'CATALOG#META', 'SK': 'META'})).get('Item') or {}
    except Exception:  # noqa: BLE001
        logger.warning('⚠️ CATALOG#META read failed; department and category questions are off', exc_info=True)
        item = {}
    catalog = Catalog(total=int(item.get('itemCount') or 0))
    if item.get('genders'):
        catalog.genders = list(item['genders'])
    catalog.departments = {d: list(c) for d, c in (item.get('departments') or {}).items()}
    catalog.department_descriptions = dict(item.get('departmentDescriptions') or {})
    catalog.category_descriptions = dict(item.get('categoryDescriptions') or {})
    return catalog


def valid(message: dict, state: SessionState) -> bool:
    kind = message.get('type') if isinstance(message, dict) else None
    if kind == 'bidi_audio_input':
        return isinstance(message.get('audio'), str) and len(message['audio']) <= MAX_AUDIO_B64
    if kind == 'bidi_text_input':
        return isinstance(message.get('text'), str) and 0 < len(message['text']) <= 500
    if kind == 'ui.layout':
        return isinstance(message.get('columns'), int) and 2 <= message['columns'] <= 6
    if kind == 'ui.chip_remove':
        return message.get('slot') in SLOTS
    if kind == 'ui.product_tap':
        asin = message.get('asin')
        return isinstance(asin, str) and bool(ASIN.match(asin)) and any(p['asin'] == asin for p in state.shelf)
    if kind == 'ui.close_product':
        return True
    if kind == 'ui.cart_remove':
        asin = message.get('asin')
        return isinstance(asin, str) and bool(ASIN.match(asin)) and any(i['asin'] == asin for i in state.cart.get('items', []))
    if kind == 'ui.voice_replies':
        return isinstance(message.get('enabled'), bool)
    return False


@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    send_lock = asyncio.Lock()

    async def send(message: dict) -> None:
        async with send_lock:
            for part in ui_events.frames(message):
                text = json.dumps(part, separators=(',', ':'))
                if len(text.encode()) > ui_events.MAX_FRAME_BYTES:
                    # The AgentCore WebSocket closes the connection on a frame over 64 KB
                    # (close code 1009); drop the one message instead of the session.
                    logger.error('❌ %s message of %d bytes not sent (limit %d)', part.get('type'), len(text.encode()),
                                 ui_events.MAX_FRAME_BYTES)
                    continue
                await websocket.send_text(text)

    try:
        user = await jwt_auth.verify(json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=15)))
    except (jwt_auth.AuthError, ValueError, asyncio.TimeoutError, WebSocketDisconnect):
        logger.warning('⚠️ authentication failed; closing 4401')
        try:
            await websocket.send_json({'type': 'error', 'code': 'auth', 'message': 'Authentication failed.'})
            await websocket.close(code=jwt_auth.AUTH_CLOSE_CODE)
        except Exception:  # noqa: BLE001
            pass
        return

    session_id = websocket.headers.get(SESSION_HEADER) or websocket.query_params.get('session_id') or ''
    if not SESSION_ID.match(session_id):   # the cart tools require 36-100 characters
        session_id = str(uuid.uuid4())
    logger.info('🚀 session %s for actor %s', session_id[:8], user['sub'][:8])
    state = await state_store.load(session_id) or SessionState(session_id=session_id, actor_id=user['sub'])
    restored = bool(state.turn_seq)
    memory = SessionMemory(actor_id=user['sub'], session_id=session_id)
    remembered, meta = await asyncio.gather(memory.remembered(), catalog_meta())
    state.remembered = remembered

    bridge: SonicBridge | None = None
    try:
        async with GatewayTools() as tools:
            async def on_turn_end(s, turn):
                await state_store.save(s, turn)

            pipeline = TurnPipeline(state, Deps(tools=tools, send=send, thresholds=THRESHOLDS, catalog=meta,
                                                on_commit=memory.on_commit, on_liked=memory.on_liked,
                                                on_turn_end=on_turn_end))
            if restored:
                await send(ui_events.chips(state.turn_seq, policy.chips(state), state.remembered))
                await send(ui_events.shelf(state.turn_seq, state.shelf, policy.is_tentative(state), meta.total,
                                           len(state.shelf), len(state.shelf)))
                await send(ui_events.cart(state.cart))
            else:
                await pipeline.initial_shelf()

            bridge = SonicBridge(pipeline, send, tools, voice=websocket.query_params.get('voice_id', 'tiffany'),
                                 remembered=remembered)
            try:
                await bridge.start()
                await send(ui_events.status('listening', 'Tell me what you are shopping for.'))
            except Exception:  # noqa: BLE001 -- typed input still works without voice
                logger.error('❌ Sonic stream could not open', exc_info=True)
                bridge = None
                await send(ui_events.status('error', 'Voice is unavailable right now. You can still type.'))

            invalid = 0
            while True:
                try:
                    message = json.loads(await websocket.receive_text())
                except ValueError:
                    message = None
                if not valid(message, state):
                    invalid += 1
                    logger.warning('⚠️ invalid browser message (%s of %s)', invalid, MAX_INVALID_MESSAGES)
                    if invalid >= MAX_INVALID_MESSAGES:
                        await websocket.close(code=1008)
                        return
                    continue
                kind = message['type']
                if kind == 'bidi_audio_input' and bridge:
                    await bridge.audio(message['audio'])
                elif kind == 'bidi_text_input':
                    if bridge:
                        await bridge.text(message['text'])
                    else:
                        await send({'type': 'bidi_transcript_stream', 'role': 'user', 'text': message['text'], 'is_final': True})
                        pipeline.on_final(message['text'])
                elif kind == 'ui.layout':
                    state.columns = positions.clamp_columns(message['columns'])
                elif kind == 'ui.chip_remove':
                    state.push_history()
                    policy.reopen(state, message['slot'])
                    await send(ui_events.chips(state.turn_seq, policy.chips(state), state.remembered))
                elif kind == 'ui.product_tap':
                    await pipeline.open_tapped(message['asin'])
                elif kind == 'ui.cart_remove':
                    await pipeline.remove_tapped(message['asin'])
                elif kind == 'ui.close_product':
                    state.open_product = None
                    state.pending_offer = None
                elif kind == 'ui.voice_replies':
                    pipeline.voice_replies = message['enabled']
    except WebSocketDisconnect:
        logger.info('ℹ️ session %s disconnected', session_id[:8])
    except Exception:  # noqa: BLE001
        logger.error('❌ session failed', exc_info=True)
        try:
            await send(ui_events.status('error', 'Something went wrong. Please reconnect.'))
        except Exception:  # noqa: BLE001
            pass
    finally:
        if bridge:
            await bridge.stop()
        await state_store.save(state)


if __name__ == '__main__':
    # AgentCore Runtime reaches the container on port 8080 from outside it; the microVM is
    # reachable only through AgentCore with SigV4, so binding all interfaces is required.
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8080')), log_level='info')  # nosec B104
