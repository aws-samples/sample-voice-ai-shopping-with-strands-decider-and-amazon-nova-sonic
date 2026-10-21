"""AgentCore Memory.

Read once at session start (1.5 s timeout) for "Remembered" hints, which are
context only and never committed as chips. Writes are async after commits and
likes. Any failure disables memory for the session and the session continues.
"""

from __future__ import annotations

import asyncio
import json
import datetime
import logging
import os

import boto3

from state import Commit, SessionState

logger = logging.getLogger(__name__)

READ_TIMEOUT_S = 1.5
TOP_K = 5

_client = None


def _agentcore():
    global _client
    if _client is None:
        _client = boto3.client('bedrock-agentcore', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
    return _client


class SessionMemory:
    def __init__(self, actor_id: str, session_id: str, memory_id: str | None = None):
        self.memory_id = memory_id or os.environ.get('MEMORY_ID')
        self.actor_id = actor_id
        self.session_id = session_id
        self.enabled = bool(self.memory_id)

    def _disable(self, what: str) -> None:
        logger.warning('⚠️ memory %s failed; continuing without memory', what, exc_info=True)
        self.enabled = False

    async def remembered(self) -> list[str]:
        """Short preference sentences, e.g. "Prefers women's styles; budget under $80"."""
        if not self.enabled:
            return []
        try:
            response = await asyncio.wait_for(asyncio.to_thread(
                _agentcore().retrieve_memory_records, memoryId=self.memory_id,
                namespace=f'/voiceshop/{self.actor_id}/preferences',
                searchCriteria={'searchQuery': 'shopping preferences', 'topK': TOP_K}), timeout=READ_TIMEOUT_S)
        except Exception:  # noqa: BLE001
            self._disable('read')
            return []
        records = response.get('memoryRecordSummaries', [])
        texts = [text for text in (_preference(r.get('content', {}).get('text', '')) for r in records) if text]
        return list(dict.fromkeys(texts))[:TOP_K]   # the strategy can store the same preference twice

    async def _event(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            await asyncio.to_thread(
                _agentcore().create_event, memoryId=self.memory_id, actorId=self.actor_id, sessionId=self.session_id,
                eventTimestamp=datetime.datetime.now(datetime.timezone.utc),
                payload=[{'conversational': {'content': {'text': text}, 'role': 'USER'}}])
        except Exception:  # noqa: BLE001
            self._disable('write')

    async def on_commit(self, state: SessionState, commits: dict[str, Commit]) -> None:
        phrases = {'recipient': 'Shopping for {}.', 'gender': 'Prefers {} styles.', 'category': 'Interested in {}.',
                   'budget': 'Budget {}.'}
        text = ' '.join(phrases[s].format(c.value) for s, c in commits.items() if s in phrases)
        if text:
            await self._event(text)

    async def on_liked(self, state: SessionState, product: dict) -> None:
        await self._event(f"Liked: {product.get('title', '')[:100]} ({product.get('category', '')}, {product.get('color', '')}).")


def _preference(text: str) -> str:
    """The user preference strategy stores JSON ({"context", "preference", "categories"});
    keep only the preference sentence."""
    try:
        value = json.loads(text)
    except ValueError:
        return text.strip()[:120]
    return str(value.get('preference', '')).strip()[:120] if isinstance(value, dict) else ''

