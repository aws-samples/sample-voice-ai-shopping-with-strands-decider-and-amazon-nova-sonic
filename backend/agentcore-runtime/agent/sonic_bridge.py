"""Nova 2.5 Sonic bridge (ADR-15, ADR-23, ADR-24).

Owns the Strands BidiAgent + BedrockNovaSonicModel for one shopping session:

- Input router: browser mic frames go to Sonic; typed text goes to the turn
  pipeline directly and to Sonic so it can answer.
- Output tap: user transcript deltas feed speculative retrieval (ADR-24), the
  final user transcript starts a turn, and events are forwarded to the browser in
  the frontend's message names.
- Audio gate (ADR-23, design decision 2026-10-18): Sonic's audio reaches the
  browser only when spoken replies are on, or for a yes/no confirmation turn
  ("Should I add it to your cart?"). Transcripts are always forwarded as text.
- Continuation: Strands restarts the Sonic connection itself
  before the 8-minute limit (restart_after_s 420, at a turn boundary, with the
  same system prompt, tools and history, mic input held during the swap). The
  bridge reports each restart as ui.status continuation plus a metric.

Sonic gets two tools only: get_turn_outcome (what it may say, built in code) and
get_product_detail (read-only). It never searches, adds to cart or orders.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from typing import Awaitable, Callable

from strands import tool
from strands.bidi import BidiAgent
from strands.bidi.models import BedrockNovaSonicModel

import activity
import metrics
import ui_events
from turn_pipeline import TurnPipeline, normalize

logger = logging.getLogger(__name__)

TEXT_ECHO_WINDOW_S = 5.0
# Sonic detects the end of a turn from the live audio stream, so typed text gets
# no reply unless audio keeps flowing (measured 2026-10-18). After typed input the
# bridge streams silence for a few seconds, unless the mic is already streaming.
SILENCE_AFTER_TEXT_S = 3.0
SILENCE_FRAME = bytes(2048)          # 64 ms of 16 kHz PCM16
FRAME_S = 0.064
# Sonic closes a stream that gets no audio for 55 s (the shopper is reading, or the
# mic is off). The bridge then reopens it on the next mic frame or typed sentence,
# holding up to ~3 s of mic audio while it reopens (~1 s).
IDLE_TIMEOUT_TEXT = 'Timed out waiting for audio bytes'
MAX_PENDING_FRAMES = 50

SYSTEM_PROMPT = """You are the voice of VoiceShop, an online store the shopper browses by talking.
The screen already shows everything: the products, the shopper's preferences as chips, the cart.
Code and a decision model decide what happens; you only say it.

Rules:
- Before every reply, call get_turn_outcome and base your reply only on its result.
- Say the outcome's "say" text in natural words, in one short sentence.
- If "speak" is false, the shopper is reading the screen: reply with at most three words, "Here you go." when the directive is act or narrow, and "Take a look." for anything else.
- If the directive is "hold", say a two-word filler such as "One moment." and nothing else.
- Never state a price, total, count, percentage or product detail that is not in the outcome or in a get_product_detail result.
- Use get_product_detail only to answer a question about the open product, such as what it is made of.
- Never offer to search, add to the cart or place an order yourself; the store does that when the shopper asks."""


class SonicBridge:
    def __init__(self, pipeline: TurnPipeline, send: Callable[[dict], Awaitable[None]], tools,
                 voice: str = 'tiffany', model_id: str | None = None, region: str | None = None,
                 remembered: list[str] | None = None):
        self.pipeline = pipeline
        self.send = send
        self.tools = tools
        self.voice = voice
        self.model_id = model_id or os.environ.get('SONIC_MODEL_ID', 'amazon.nova-2-5-sonic')
        self.region = region or os.environ.get('AWS_REGION', 'us-east-1')
        self.remembered = remembered or []
        self.agent: BidiAgent | None = None
        self._receiver: asyncio.Task | None = None
        self._partials: dict[str, str] = {}
        self._last_text_input: tuple[str, float] | None = None
        self._audio_allowed = False
        self._last_mic_frame = 0.0
        self._silence: asyncio.Task | None = None
        self._opening: asyncio.Task | None = None
        self._pending: list[bytes] = []
        self._background: set[asyncio.Task] = set()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # ------------------------------------------------------------------ tools
    def _tools(self):
        pipeline, gateway = self.pipeline, self.tools

        @tool
        async def get_turn_outcome() -> dict:
            """Get what to say about the shopper's latest sentence: the directive (act, narrow,
            ask, no_match, hold, error), the "say" text, chips, cart summary, and whether to speak."""
            await self.send(activity.event(pipeline.state.turn_seq, 'sonic', 'Nova Sonic asked the app what to say (tool: get_turn_outcome)'))
            outcome = await pipeline.turn_outcome()
            self._audio_allowed = bool(outcome.get('speak')) or pipeline.voice_replies
            return outcome

        @tool
        async def get_product_detail(asin: str) -> dict:
            """Look up details (material, color, size, features) of the product that is open or on screen.

            Args:
                asin: The product id, 10 letters and digits.
            """
            state = pipeline.state
            allowed = {p['asin'] for p in state.shelf} | ({state.open_product['asin']} if state.open_product else set())
            if asin not in allowed:
                return {'error': 'Only products on screen can be looked up.'}
            try:
                started = time.monotonic()
                product = (await gateway.get_product_detail({'asin': asin})).get('product', {})
                await self.send(activity.event(pipeline.state.turn_seq, 'dynamodb',
                                               'Nova Sonic looked up product details (tool: get_product_detail, DynamoDB)',
                                               product.get('title', asin)[:80], (time.monotonic() - started) * 1000))
            except Exception:  # noqa: BLE001 -- Sonic says it could not look that up
                logger.warning('⚠️ get_product_detail failed for %s', asin, exc_info=True)
                return {'error': 'The product details are unavailable right now.'}
            keep = ('title', 'brand', 'color', 'material', 'size', 'category', 'features', 'averageRating', 'ratingNumber')
            detail = {k: product.get(k) for k in keep if product.get(k) is not None}
            if 'priceCents' in product:
                detail['price'] = ui_events.format_cents(product['priceCents'])
            return detail

        return [get_turn_outcome, get_product_detail]

    # -------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        prompt = SYSTEM_PROMPT
        if self.remembered:
            prompt += '\nRemembered from earlier visits (hints only, never facts): ' + '; '.join(self.remembered)
        model = BedrockNovaSonicModel(
            region=self.region, model_id=self.model_id, voice=self.voice,
            audio={'input': {'sample_rate': 16000}, 'output': {'sample_rate': 24000}},
            connection={'restart_after_s': int(os.environ.get('SONIC_ROLLOVER_SOFT_S', '420'))},
            # Low temperature keeps Sonic on the outcome text (as in the travel concierge sample).
            params={'inferenceConfiguration': {'temperature': 0.2, 'topP': 0.9, 'maxTokens': 1024}},
        )
        agent = BidiAgent(model=model, tools=self._tools(), system_prompt=prompt)
        try:
            await asyncio.wait_for(agent.start(), timeout=10)
        except BaseException:
            try:
                await agent.stop()
            except Exception:  # noqa: BLE001
                pass
            raise
        # Published only once it is running: a failed (re)open leaves no dead stream behind.
        self.agent = agent
        self._receiver = asyncio.create_task(self._receive(agent))
        logger.info('✅ Sonic stream open (%s, voice %s)', self.model_id, self.voice)

    async def stop(self) -> None:
        if self._silence:
            self._silence.cancel()
        if self._receiver:
            self._receiver.cancel()
        if self.agent:
            try:
                await self.agent.stop()
            except Exception:  # noqa: BLE001
                logger.warning('⚠️ Sonic stop failed', exc_info=True)

    # ------------------------------------------------------------------ input
    async def _ensure_open(self) -> None:
        if self.agent:
            return
        if self._opening is None or self._opening.done():
            self._opening = asyncio.create_task(self.start())
        await asyncio.shield(self._opening)

    async def _reopen_and_flush(self) -> None:
        try:
            await self._ensure_open()
        except Exception:  # noqa: BLE001
            logger.error('❌ Sonic stream could not reopen', exc_info=True)
            self._pending.clear()
            await self.send(ui_events.status('error', 'Voice is unavailable right now. You can still type.'))
            return
        # New frames keep queuing behind these until the queue is empty, so order holds.
        while self._pending and self.agent:
            frame = self._pending.pop(0)
            await self.agent.send({'audio_delta': {'format': 'pcm', 'source': {'bytes': frame}}})

    async def audio(self, pcm_base64: str) -> None:
        self._last_mic_frame = time.monotonic()
        frame = base64.b64decode(pcm_base64)
        if self.agent and not self._pending:
            await self.agent.send({'audio_delta': {'format': 'pcm', 'source': {'bytes': frame}}})
            return
        # Reopening after an idle close: hold the frames, send them once it is open.
        self._pending = (self._pending + [frame])[-MAX_PENDING_FRAMES:]
        if not self.agent and (self._opening is None or self._opening.done()):
            self._spawn(self._reopen_and_flush())

    async def _pump_silence(self) -> None:
        deadline = time.monotonic() + SILENCE_AFTER_TEXT_S
        while time.monotonic() < deadline:
            if time.monotonic() - self._last_mic_frame < 0.25:
                return   # the mic is streaming; it carries the turn end
            agent = self.agent
            if agent is None:
                return   # the stream closed (idle timeout); it reopens on the next input
            await agent.send({'audio_delta': {'format': 'pcm', 'source': {'bytes': SILENCE_FRAME}}})
            await asyncio.sleep(FRAME_S)

    async def text(self, text: str) -> None:
        """Typed input: the pipeline runs on it directly; Sonic gets it to reply."""
        self._last_text_input = (normalize(text), time.monotonic())
        self._audio_allowed = False
        await self.send({'type': 'bidi_transcript_stream', 'role': 'user', 'text': text, 'is_final': True})
        await self.send(activity.event(self.pipeline.state.turn_seq + 1, 'app', 'You typed', f'"{text}"'))
        self.pipeline.on_final(text)
        try:
            await self._ensure_open()
        except Exception:  # noqa: BLE001 -- the turn already ran; only the voice reply is lost
            logger.error('❌ Sonic stream could not reopen', exc_info=True)
        if self.agent:
            await self.agent.send(text)
            if self._silence and not self._silence.done():
                self._silence.cancel()
            self._silence = asyncio.create_task(self._pump_silence())

    # ----------------------------------------------------------------- output
    async def _receive(self, agent: BidiAgent) -> None:
        try:
            async for event in agent.receive():
                await self._on_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if self.agent is agent:
                self.agent = None   # reopened on the next input
            try:
                await agent.stop()
            except Exception:  # noqa: BLE001
                pass
            if IDLE_TIMEOUT_TEXT in str(exc):
                logger.info('ℹ️ Sonic stream closed after 55 s without audio; reopens on the next input')
                metrics.emit('SonicIdleCloses', 1)
                return
            logger.error('❌ Sonic stream ended unexpectedly', exc_info=True)
            await self.send(ui_events.status('error', 'Voice connection lost. Tap the mic to continue.'))

    async def _on_event(self, event: dict) -> None:
        kind = event.get('type')
        role = event.get('role')
        if kind == 'bidi_transcript_delta' and role == 'user':
            content_id = event.get('content_id', '')
            text = self._partials.get(content_id, '') + event.get('delta', '')
            self._partials[content_id] = text
            await self.send({'type': 'bidi_transcript_stream', 'role': 'user', 'text': text, 'is_final': False})
            self.pipeline.on_partial(text)
        elif kind == 'bidi_transcript_block' and role == 'user':
            self._partials.pop(event.get('content_id', ''), None)
            text = event.get('transcript', '').strip()
            if not text or self._is_echo_of_typed_text(text):
                return
            self._audio_allowed = False   # a new turn: closed until its outcome says otherwise
            await self.send({'type': 'bidi_transcript_stream', 'role': 'user', 'text': text, 'is_final': True})
            await self.send(activity.event(self.pipeline.state.turn_seq + 1, 'sonic', 'Nova Sonic heard you (speech to text)', f'"{text}"'))
            self.pipeline.on_final(text)
        elif kind == 'bidi_transcript_delta' and role == 'assistant':
            await self.send({'type': 'bidi_transcript_stream', 'role': 'assistant', 'text': event.get('delta', ''), 'is_final': False})
        elif kind == 'bidi_transcript_block' and role == 'assistant':
            await self.send({'type': 'bidi_transcript_stream', 'role': 'assistant', 'text': event.get('transcript', ''), 'is_final': True})
            spoken = self._audio_allowed or self.pipeline.voice_replies
            await self.send(activity.event(self.pipeline.state.turn_seq, 'sonic',
                                           'Nova Sonic replied out loud' if spoken else 'Nova Sonic wrote the reply (voice is off)',
                                           f"\"{event.get('transcript', '')[:160]}\""))
        elif kind == 'bidi_audio_delta':
            if self._audio_allowed or self.pipeline.voice_replies:
                await self.send({'type': 'bidi_audio_stream', 'audio': event.get('audio', '')})
        elif kind == 'bidi_barge_in':
            await self.send({'type': 'bidi_interruption'})
        elif kind == 'bidi_connection_restart':
            logger.info('ℹ️ Sonic connection restarted (%s)', event.get('reason', 'scheduled'))
            metrics.emit('SonicContinuations', 1)
            await self.send(ui_events.status('continuation', 'Voice connection refreshed.'))

    def _is_echo_of_typed_text(self, text: str) -> bool:
        if not self._last_text_input:
            return False
        typed, at = self._last_text_input
        return normalize(text) == typed and time.monotonic() - at < TEXT_ECHO_WINDOW_S
