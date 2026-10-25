"""Per-sentence flow.

One pipeline task per final user transcript; a newer final transcript cancels
the older task. Catalog search starts early on stable partial transcripts and is
reused when the final query matches. Every outcome handed to Sonic is built in
code from the same answers that drive the UI, so spoken and displayed facts
agree.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

import activity
import decider_client
import filters
import metrics
import policy
import questions
import ui_events
from decider_client import DeciderError, DeciderUnavailable
from rate_limit import TokenBucket
from state import SessionState

logger = logging.getLogger(__name__)

SHELF_SIZE = 12
CANDIDATES = 49
RETRIEVAL_LIMIT = 200
# Shopper transcripts, the decider's answers and the replies are personal data: kept out of the
# turn log unless switched on for debugging.
LOG_TRANSCRIPTS = os.environ.get('LOG_TRANSCRIPTS', '').lower() == 'true'
# Per connection: at most this many decider calls in flight (two turns' worth, so a shopper who
# corrects themselves never waits), and new turns (typed or spoken) at 1 a second with bursts
# of 5. A cancelled turn's calls keep their slots until the decider answers them, so a flood of
# sentences cannot pile up work on the decider.
DECIDER_SLOTS = 10
TURN_RATE = (1.0, 5)


def _cart_serialized(method):
    """One cart or order write at a time per session: a tapped remove and a turn's add could
    otherwise interleave, and the later response would overwrite the cart shown on screen."""
    async def wrapper(self, *args, **kwargs):
        self._commit_point()   # before waiting for the lock: a confirmed write is never dropped
        async with self._cart_lock:
            return await method(self, *args, **kwargs)
    wrapper.__name__, wrapper.__doc__ = method.__name__, method.__doc__
    return wrapper


def request_key(session_id: str) -> str:
    """An idempotency key for one confirmed cart or order action. Random, not the turn number: the
    turn counter restarts from the last saved state after a reconnect, and a reused key would make
    a new add or order a silent no-op reported as done."""
    return f'{session_id}:{uuid.uuid4().hex}'
SPECULATIVE_DEBOUNCE_S = 0.25
# How long get_turn_outcome waits for the turn before Sonic says "One moment.". The
# screen never waits on this. It covers the slowest turn (a no-match: search, best_fit
# and up to four widening searches), because a held confirmation is never spoken.
OUTCOME_HOLD_S = 6.0
INITIAL_SHELF_QUERY = 'popular gifts'
# 'department' must stay: the code's own hard-filter check (filters.HardFilters.allows) reads it.
CATALOG_FIELDS = ('asin', 'title', 'brand', 'department', 'category', 'gender', 'color', 'priceCents', 'rating',
                  'ratingCount', 'available', 'imageUrl')


class ToolError(Exception):
    """A Gateway tool failed or returned an unexpected payload."""


class Tools(Protocol):
    async def search_catalog(self, arguments: dict) -> dict: ...
    async def get_product_detail(self, arguments: dict) -> dict: ...
    async def add_to_cart(self, arguments: dict) -> dict: ...
    async def remove_from_cart(self, arguments: dict) -> dict: ...
    async def place_order(self, arguments: dict) -> dict: ...


@dataclass
class Deps:
    tools: Tools
    send: Callable[[dict], Awaitable[None]]
    thresholds: policy.Thresholds = field(default_factory=policy.Thresholds.from_env)
    catalog: questions.Catalog = field(default_factory=questions.Catalog)   # what the catalog offers (CATALOG#META)
    ask_decider: Callable[[str, dict], Awaitable[decider_client.DeciderResult]] = decider_client.ask
    on_commit: Callable[[SessionState, dict], Awaitable[None]] | None = None    # memory writes
    on_liked: Callable[[SessionState, dict], Awaitable[None]] | None = None
    on_turn_end: Callable[[SessionState, dict], Awaitable[None]] | None = None  # state snapshot + turn log


def normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r"[^\w\s$']", ' ', text.lower())).strip()


def cart_summary(cart: dict) -> str:
    count = cart.get('itemCount', 0)
    return 'The cart is empty.' if not count else f"{count} item{'s' if count != 1 else ''}, {ui_events.format_cents(cart.get('totalCents', 0))} total."


class TurnPipeline:
    def __init__(self, state: SessionState, deps: Deps):
        self.state = state
        self.deps = deps
        self._turn_task: asyncio.Task | None = None
        self._cart_lock = asyncio.Lock()
        # The turn that has started a cart or order write. A newer sentence waits for it
        # instead of cancelling it: on 2026-10-21 a "yes" that had already added the jeans
        # was cancelled before it closed the product, and the next sentence added them again.
        self._committing: asyncio.Task | None = None
        self._decider_slots = asyncio.Semaphore(DECIDER_SLOTS)
        # Idempotency key of an add that failed or timed out, by ASIN: the add may have committed,
        # so the shopper's retry reuses it and cannot add a second unit by accident.
        self._retry_keys: dict[str, str] = {}
        self._turns = TokenBucket(*TURN_RATE)
        self._round_answers: dict = {}
        self._background: set[asyncio.Task] = set()   # fire-and-forget work, kept referenced
        self._retrieval_filters: dict = {}
        self._outcome: asyncio.Future | None = None
        self._spec_timer: asyncio.Task | None = None
        self._spec: tuple[str, asyncio.Task] | None = None   # (key, search task)
        self._results: list[dict] = []     # every result of the latest search, in shelf order
        self._t0 = time.perf_counter()
        self.voice_replies = False
        self.speak_current = False   # whether Sonic's reply to the latest turn is played

    # ------------------------------------------------------------- entry points
    async def initial_shelf(self) -> None:
        """a shelf before the first sentence."""
        try:
            result = await self.deps.tools.search_catalog({'query': INITIAL_SHELF_QUERY, 'limit': SHELF_SIZE, 'availableOnly': True})
            self.state.shelf = [self._slim(h) for h in result.get('hits', [])][:SHELF_SIZE]
            await self.deps.send(ui_events.shelf(0, self.state.shelf, False, result.get('totalCatalog', self.deps.catalog.total),
                                                 result.get('totalMatched', 0), len(self.state.shelf)))
        except Exception:  # noqa: BLE001 -- an empty first screen is not fatal
            logger.warning('⚠️ initial shelf failed', exc_info=True)
        await self.deps.send(ui_events.chips(0, policy.chips(self.state), self.state.remembered))
        await self.deps.send(ui_events.cart(self.state.cart))

    def on_partial(self, text: str) -> None:
        """Partial user transcript (ADR-24): debounce, then start a speculative search.
        Never commits chips, calls the decider or changes the shelf."""
        if self._spec_timer:
            self._spec_timer.cancel()
        self._spec_timer = asyncio.create_task(self._speculate_after(text))

    def on_final(self, text: str) -> asyncio.Future:
        """Final user transcript: cancel the running turn and start a new one. A turn
        that has started a cart or order write is not cancelled; the new turn starts
        after it, on the state it leaves."""
        if self._spec_timer:
            self._spec_timer.cancel()
        if not self._turns.take():
            # Over the turn limit: this sentence is the newest outcome, so Sonic does not
            # repeat the previous turn's reply for it.
            logger.warning('⚠️ sentence dropped: more than %s turns a second', TURN_RATE[0])
            self._outcome = asyncio.get_running_loop().create_future()
            self._outcome.set_result({'directive': 'hold', 'say': 'One moment.', 'speak': False,
                                      'cart': cart_summary(self.state.cart)})
            self._spawn(self.deps.send(ui_events.status('degraded', 'That was too fast. Please say it again.')))
            return self._outcome
        previous = self._turn_task
        # Any turn still writing, not only the latest one: a turn waiting behind it is
        # replaced, and the new turn waits for the write as well.
        committing = self._committing
        after = committing if committing and not committing.done() else None
        if previous and not previous.done() and previous is not after:
            previous.cancel()
        loop = asyncio.get_running_loop()
        self._outcome = loop.create_future()
        outcome = self._outcome
        self._turn_task = asyncio.create_task(self._run_turn(text, outcome, after))
        return outcome

    def close(self) -> None:
        """The socket closed: stop work nobody will see. A turn that is writing to the cart
        finishes, and state saves already started are left to complete."""
        for task in (self._spec_timer, self._spec[1] if self._spec else None):
            if task and not task.done():
                task.cancel()
        turn = self._turn_task
        if turn and not turn.done() and self._committing is not turn:
            turn.cancel()

    async def turn_outcome(self, hold_s: float = OUTCOME_HOLD_S) -> dict:
        """What Sonic may say about the newest turn (get_turn_outcome tool)."""
        if self._outcome is None:
            return {'directive': 'other', 'say': '', 'cart': cart_summary(self.state.cart)}
        outcome = self._outcome
        try:
            return await asyncio.wait_for(asyncio.shield(outcome), timeout=hold_s)
        except asyncio.TimeoutError:
            return {'directive': 'hold', 'say': 'One moment.', 'speak': False, 'cart': cart_summary(self.state.cart)}
        except asyncio.CancelledError:
            if outcome.cancelled() and self._outcome is not outcome:
                return await self.turn_outcome(hold_s)   # a newer sentence replaced this turn
            raise

    # ---------------------------------------------------------------- retrieval
    def _search_args(self, text: str) -> dict:
        state = self.state
        recent = [t for t in state.transcripts[-2:] if normalize(t) != normalize(text)]
        labels = [c['value'] for c in policy.chips(state) if c['slot'] != 'budget']
        query = ' '.join([text] + recent + labels)[:500]
        hard = state.hard_filters()
        parsed = filters.parse_budget(text)
        if parsed:
            # The amount in this sentence replaces the budget; every other chip stays. Rebuilding
            # the filters by hand dropped the department, so each sentence with a price ran a
            # second search (the filter check in _narrowing caught it), ~0.4 s each (2026-10-20).
            hard = dataclasses.replace(hard, budget=parsed)
        return {'query': query, 'limit': RETRIEVAL_LIMIT, **hard.search_arguments()}

    @staticmethod
    def _filters_of(args: dict) -> dict:
        return {k: v for k, v in args.items() if k not in ('query', 'limit')}

    def _key(self, text: str) -> str:
        args = self._search_args(text)
        return normalize(text) + '|' + repr(sorted((k, v) for k, v in args.items() if k != 'query'))

    async def _speculate_after(self, text: str) -> None:
        await asyncio.sleep(SPECULATIVE_DEBOUNCE_S)
        key = self._key(text)
        if self._spec and self._spec[0] == key:
            return
        if self._spec:
            self._spec[1].cancel()
        self._spec = (key, asyncio.create_task(self._timed_search(self._search_args(text), early=True)))

    async def _timed_search(self, args: dict, early: bool = False) -> tuple[dict, float]:
        started = time.perf_counter()
        result = await self.deps.tools.search_catalog(args)
        ms = (time.perf_counter() - started) * 1000
        filters_text = ', '.join(f'{k}={v}' for k, v in args.items() if k not in ('query', 'limit', 'availableOnly')) or 'none'
        await self._activity('search', 'Started searching early, while you were still speaking' if early else
                             'Searched the catalog: Titan Text Embeddings V2 + OpenSearch Serverless (private, through the VPC)',
                             f"\"{str(args.get('query', ''))[:70]}\" · filters: {filters_text} · "
                             f"{result.get('totalMatched', len(result.get('hits', [])))} matches", ms,
                             turn=self.state.turn_seq + 1 if early else None)
        if result.get('error'):   # a tool error is a failed search, not "no match"
            raise RuntimeError(f"search_catalog error: {result['error']}")
        return result, ms

    async def _activity(self, service: str, title: str, detail: str = '', ms: float | None = None,
                        turn: int | None = None) -> None:
        """One plain-language line for the debug overlay; never fails the turn."""
        try:
            await self.deps.send(activity.event(self.state.turn_seq if turn is None else turn, service, title, detail, ms))
        except Exception:  # noqa: BLE001
            logger.debug('activity event not sent', exc_info=True)

    async def _ask(self, purpose: str, state_text: str, qs: dict) -> decider_client.DeciderResult:
        """A Strands Decider call, reported to the debug overlay with its answers."""
        try:
            result = await self._decider_call(state_text, qs)
        except Exception:
            await self._activity('decider', f'Strands Decider 2B did not answer: {activity.PURPOSES.get(purpose, purpose)}')
            raise
        await self._activity('decider', activity.decider_title(purpose, len(qs)),
                             activity.answers_detail(result.answers, qs), result.client_ms)
        return result

    async def _decider_call(self, state_text: str, qs: dict) -> decider_client.DeciderResult:
        """One decider call in one of the session's slots. Cancelled while waiting for a slot, the
        call is never sent; cancelled after, it is left to finish and frees its slot when it does
        (InvokeEndpoint cannot be recalled), so the next turn waits instead of adding load."""
        await self._decider_slots.acquire()
        call = asyncio.ensure_future(self.deps.ask_decider(state_text, qs))

        def done(task: asyncio.Future) -> None:
            self._decider_slots.release()
            if not task.cancelled():
                task.exception()   # retrieved, so an abandoned call's error is not reported as unhandled
        call.add_done_callback(done)
        return await asyncio.shield(call)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _retrieval_for(self, text: str) -> tuple[asyncio.Task, bool]:
        key = self._key(text)
        if self._spec and self._spec[0] == key and not self._spec[1].cancelled():
            task, self._spec = self._spec[1], None
            return task, True
        if self._spec:
            self._spec[1].cancel()
            self._spec = None
        return asyncio.create_task(self._timed_search(self._search_args(text))), False

    @staticmethod
    def _slim(hit: dict) -> dict:
        return {k: hit.get(k) for k in CATALOG_FIELDS}

    # --------------------------------------------------------------------- turn
    def _commit_point(self) -> None:
        """Called just before a cart or order write: from here the turn runs to its end. A tap
        in the cart panel is not a turn and never stands in for one."""
        if asyncio.current_task() is self._turn_task:
            self._committing = self._turn_task

    async def _run_turn(self, text: str, outcome: asyncio.Future, after: asyncio.Task | None = None) -> None:
        try:
            if after:
                await asyncio.wait({after})
            result = await self._turn(text)
        except asyncio.CancelledError:
            if not outcome.done():
                outcome.cancel()
            raise
        except Exception:  # noqa: BLE001 -- one bad turn must not end the session
            logger.error('❌ turn failed', exc_info=True)
            result = {'directive': 'ask', 'say': 'Sorry, something went wrong. Could you say that again?',
                      'cart': cart_summary(self.state.cart)}
            await self.deps.send(ui_events.status('error', 'Something went wrong with that request.'))
        if not outcome.done():
            outcome.set_result(result)

    async def _turn(self, text: str) -> dict:
        state, deps = self.state, self.deps
        self._t0 = time.perf_counter()
        ms = self._elapsed_ms
        state.turn_seq += 1
        turn = state.turn_seq
        await deps.send(ui_events.status('thinking', 'Thinking…'))
        timings: dict[str, Any] = {}

        parsed_budget = filters.parse_budget(text)
        # Search starts now (or was started on the partial transcript); it is
        # cancelled below if the intent turns out not to need it.
        retrieval, speculative = self._retrieval_for(text)
        self._retrieval_filters = self._filters_of(self._search_args(text))
        ids = questions.select_understand(state, parsed_budget)
        understand_q = questions.understand_questions(state, ids, deps.catalog)
        intent_q = {'intent': understand_q.pop('intent')}
        intent_text = questions.intent_state_text(state, text)
        offer_text = questions.offer_reply_text(state, text) if state.pending_offer else None
        recheck_ids = questions.recheck_ids(state, parsed_budget)
        recheck_q = questions.understand_questions(state, [i for i in recheck_ids if i != 'recipient'], deps.catalog)
        recipient_q = questions.understand_questions(state, ['recipient'], deps.catalog) if 'recipient' in recheck_ids else {}
        recheck_text = questions.recheck_text(state, text)
        recipient_text = questions.recipient_recheck_text(state, text)
        recheck: dict[str, policy.Answer] = {}
        state_text = questions.state_text(state, text)
        state.add_transcript(text)

        answers: dict[str, policy.Answer] = {}
        decider_down = None
        try:
            # In parallel: intent with minimal context, open slots with the full state, the
            # committed slots again with only the latest sentence (change of mind), and the
            # reply to a pending yes/no question with only that question.
            calls = [self._ask('intent', intent_text, intent_q)] + ([self._ask('details', state_text, understand_q)] if understand_q else []) \
                + ([self._ask('reply', offer_text, questions.offer_reply_question())] if offer_text else [])
            checks = ([self._ask('recheck', recheck_text, recheck_q)] if recheck_q else []) \
                + ([self._ask('recheck_person', recipient_text, recipient_q)] if recipient_q else [])
            results = await asyncio.gather(*calls, *checks)
            recheck = {k: v for r in results[len(calls):] for k, v in r.answers.items()}
            answers = {k: v for r in results[:len(calls)] for k, v in r.answers.items()}
            client_ms = max(r.client_ms for r in results)
            timings['understandMs'] = round(client_ms)
            metrics.latency('DeciderLatencyMs', client_ms, Round='understand')
        except DeciderUnavailable as exc:
            decider_down = 'unavailable'
            logger.warning('⚠️ decider unavailable: %s', exc)
            metrics.emit('DeciderUnavailable', 1)
            await deps.send(ui_events.status('degraded', 'Busy, keeping your current results.'))
        except DeciderError:
            decider_down = 'error'
            logger.error('❌ decider error', exc_info=True)
            metrics.emit('DeciderErrors', 1)

        # Change of mind: a committed slot whose re-check answer differs (recheck_ids).
        changes = policy.changed_slots(state, recheck, deps.thresholds) if not decider_down else {}
        if decider_down:
            plan = policy.UnderstandPlan(intent='narrowing', intent_band='ask',
                                         clarify='Could you say that another way?')
            if parsed_budget:
                plan.commits['budget'] = policy.Commit(parsed_budget.label(), 1.0, 'stated')
        else:
            # Planned on a copy with the changed slots cleared, so a new recipient brings
            # its own gender rather than meeting the old recipient's.
            planned = state
            if changes:
                planned = SessionState.from_dict(state.to_dict())
                for qid in changes:
                    policy.reopen(planned, 'budget' if qid == 'budget_band' else qid)
                if 'recipient' in changes:
                    policy.reset_for_new_person(planned)
            plan = policy.plan_understand(planned, {**answers, **changes}, parsed_budget, deps.thresholds)
        unsure = policy.unsure_change(state, recheck, deps.thresholds) if not changes and not decider_down else None
        if unsure and plan.intent == 'narrowing':
            # The decider leans toward a different value than a chip but is not sure:
            # ask about that one change, and keep the shelf until the shopper answers.
            plan = policy.UnderstandPlan(intent='ask_offer', intent_band=plan.intent_band, confirmation=True,
                                         offer=f'switch:{unsure[0]}:{unsure[1]}')
        if plan.intent == 'pointing' and plan.intent_band == 'act':
            # A confident pointing sentence chooses a card on the current screen; it does not
            # narrow. New chips from the open-slot questions are dropped: they are read with the
            # shelf in the context and can pick up its contents ("number three" committed Tops,
            # and the hard filter then refused No. 3, a dress; live session, 2026-10-23). A change
            # of mind, re-checked with the latest sentence alone, still applies.
            kept = {'budget' if q == 'budget_band' else q for q in changes}
            plan.commits = {k: v for k, v in plan.commits.items()
                            if k in kept or (k == 'gender' and v.source == 'recipient' and 'recipient' in kept)}
        if plan.intent != 'confirm':
            state.pending_offer = None   # an offer answers only the very next sentence
        if plan.intent == 'narrowing':
            state.push_history()        # go back returns to what was on screen before this sentence
        before = {s: state.committed(s) for s in ('recipient', 'gender', 'department', 'category', 'budget')}
        new_person = 'recipient' in changes and 'recipient' in plan.commits
        for qid in changes:
            slot = 'budget' if qid == 'budget_band' else qid
            if slot in plan.commits:
                policy.reopen(state, slot)   # a new department also clears its category
        if new_person:
            # The details answers of this round were read with the previous person's
            # sentences; only the recipient (and its gender) and a stated amount carry
            # over. _fresh_details asks this sentence again for the rest.
            policy.reset_for_new_person(state)
            state.transcripts = state.transcripts[-1:]   # the earlier sentences were about the previous person
            plan.commits = {k: v for k, v in plan.commits.items()
                            if k == 'recipient' or (k == 'gender' and v.source == 'recipient') or (k == 'budget' and v.source == 'stated')}
            plan.context = {}
        policy.apply_commits(state, plan, parsed_budget)
        if new_person:
            plan.commits.update(await self._fresh_details(text, answers.get('intent'), parsed_budget))
        if 'department' in plan.commits and state.category is None and not decider_down:
            await self._category_round(text, timings)
        newly = {s for s in before if state.committed(s) is not None and state.committed(s) != before[s]}

        await deps.send(ui_events.chips(turn, policy.chips(state), state.remembered))
        timings['firstUiMs'] = ms()
        changed_text = [f"{q}: {before['budget' if q == 'budget_band' else q].value if before['budget' if q == 'budget_band' else q] else '-'}"
                        f" → {a.choice}" for q, a in changes.items()]
        await self._activity('app', ('You changed your mind: ' + '; '.join(changed_text)) if changed_text else
                             'Chips updated' if newly else 'Chips unchanged',
                             ' · '.join(f"{c['label']}: {c['value']}" for c in policy.chips(state)) or 'no chips yet')
        metrics.latency('FirstUiLatencyMs', timings['firstUiMs'])
        if newly and deps.on_commit:
            self._spawn(deps.on_commit(state, {s: state.committed(s) for s in newly}))
            await self._activity('memory', 'Saved to AgentCore Memory for your next visit',
                                 ', '.join(f'{s}: {state.committed(s).value}' for s in newly))
        if plan.liked and state.open_product and deps.on_liked:
            self._spawn(deps.on_liked(state, state.open_product))

        if plan.intent == 'show_more' and (newly or changes):
            plan.intent = 'narrowing'   # "perfumes for men" changes a chip: a new search, not more of the old one
        # The policy has already turned unsure intents into 'narrowing', 'ask_offer' or 'decline'.
        handler = {
            'narrowing': self._narrowing, 'pointing': self._pointing, 'confirm': self._confirm,
            'place_order': self._place_order, 'go_back': self._go_back, 'start_over': self._start_over,
            'ask_offer': self._ask_offer, 'decline': self._decline,
            'close_item': self._close_item, 'move_screen': self._move_screen, 'show_more': self._show_more,
            'remove_from_cart': self._remove_from_cart,
        }.get(plan.intent)
        if retrieval is not None and plan.intent != 'narrowing':
            retrieval.cancel()
        self._round_answers = {}   # best_fit / pointed_item answers, for the debug overlay
        outcome = await handler(turn, plan, retrieval, timings, decider_down) if handler else \
            {'directive': 'other', 'say': ''}
        if retrieval is not None and not retrieval.cancelled():
            timings['retrievalSpeculative'] = speculative

        outcome.setdefault('cart', cart_summary(state.cart))
        outcome['chips'] = [f"{c['label']}: {c['value']}" + (f" ({round(c['confidence'] * 100)}%)" if c['slot'] != 'budget' else '')
                            for c in policy.chips(state)]
        # A clarifying question belongs to a narrowing turn and never replaces a
        # yes/no question, a no-match offer or an error.
        if plan.clarify and plan.intent == 'narrowing' and not outcome.get('confirmation') \
                and outcome['directive'] not in ('no_match', 'error'):
            outcome['directive'], outcome['say'] = 'ask', plan.clarify
            metrics.emit('ClarifyingQuestions', 1)
            await deps.send(ui_events.status('clarifying', plan.clarify))
        # Visual-first (ADR-23): speak when spoken replies are on, and always for a
        # yes/no confirmation (design decision 2026-10-18).
        outcome['speak'] = self.voice_replies or bool(outcome.get('confirmation'))
        if outcome.get('say'):
            await deps.send(ui_events.reply(turn, outcome['say']))
        if state.pending_offer:
            # Every pending offer is a yes/no question on screen too (Yes / No buttons).
            await deps.send(ui_events.question(turn, f'Should I {questions.offer_text(state)}?'))
        timings['totalMs'] = ms()
        await self._activity('app', activity.DIRECTIVES.get(outcome['directive'], outcome['directive']),
                             outcome.get('say', ''), timings['totalMs'])

        await deps.send(ui_events.debug(turn, text, ui_events.debug_questions({**answers, **{f'{k} (re-check)': v for k, v in recheck.items()}, **self._round_answers}), outcome.pop('_counts', {}),
                                        timings, f"intent {plan.intent} ({plan.intent_band})"))
        if outcome['directive'] not in ('ask', 'no_match'):
            await deps.send(ui_events.status('listening', 'Listening'))
        logger.info('📋 turn %s', {'turn': turn, 'intent': plan.intent, 'band': plan.intent_band,
                                   'directive': outcome['directive'], 'timings': timings,
                                   **({'transcript': text[:200], 'say': outcome.get('say', '')[:120],
                                       'answers': {q['id']: [(t['option'], t['p']) for t in q['top']]
                                                   for q in ui_events.debug_questions({**answers, **{f'{k} (re-check)': v for k, v in recheck.items()}, **self._round_answers})}}
                                      if LOG_TRANSCRIPTS else {})})
        if deps.on_turn_end:
            self._spawn(deps.on_turn_end(state, {'turn': turn, 'transcript': text, 'outcome': outcome,
                                                         'timings': timings}))
        return outcome

    async def _fresh_details(self, text: str, intent: policy.Answer | None, parsed_budget) -> dict:
        """After a new recipient reset the other chips: what this sentence says about them
        ("a dress for my mom" still sets the department), asked with the latest sentence
        and the new chips only. Returns the commits it made."""
        state, deps = self.state, self.deps
        ids = [i for i in questions.select_understand(state, parsed_budget) if i not in ('intent', 'liked_signal')]
        qs = questions.understand_questions(state, ids, deps.catalog)
        if not qs:
            return {}
        try:
            result = await self._ask('fresh', questions.recheck_text(state, text), qs)
        except (DeciderUnavailable, DeciderError):
            logger.warning('⚠️ fresh details round failed', exc_info=True)
            return {}
        fresh = policy.plan_understand(state, {**result.answers, **({'intent': intent} if intent else {})},
                                       parsed_budget, deps.thresholds)
        commits = {k: v for k, v in fresh.commits.items() if k != 'recipient'}
        policy.apply_commits(state, policy.UnderstandPlan(intent='narrowing', intent_band='act', commits=commits,
                                                          context=fresh.context), parsed_budget)
        return commits

    async def _category_round(self, text: str, timings: dict) -> None:
        """The department committed in this sentence: ask which of its categories (five
        options, not fifty). Commits on its own threshold; narrow-band answers only rank."""
        state, deps = self.state, self.deps
        try:
            result = await self._ask('category', questions.state_text(state, text),
                                     questions.understand_questions(state, ['category'], deps.catalog))
        except (DeciderUnavailable, DeciderError):
            logger.warning('⚠️ category round failed', exc_info=True)
            return
        timings['categoryMs'] = round(result.client_ms)
        answer = result.answers.get('category')
        if not answer or answer.choice in (None, questions.NOT_MENTIONED):
            return
        self._round_answers['category'] = answer
        band = deps.thresholds.band(answer.confidence, 'category')
        if band == 'act':
            state.category = policy.Commit(value=answer.choice, confidence=answer.confidence or 0.0)
        elif band == 'narrow':
            state.context['category'] = (answer.choice, answer.confidence or 0.0)

    def _elapsed_ms(self) -> int:
        return round((time.perf_counter() - self._t0) * 1000)

    # -------------------------------------------------------------- intents
    async def _narrowing(self, turn, plan, retrieval, timings, decider_down) -> dict:
        state, deps = self.state, self.deps
        try:
            result, retrieval_ms = await retrieval
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.error('❌ search_catalog failed', exc_info=True)
            return {'directive': 'ask', 'say': "I couldn't search just now. Could you repeat that?"}
        timings['retrievalMs'] = round(retrieval_ms)
        metrics.latency('RetrievalLatencyMs', retrieval_ms)
        hits = [self._slim(h) for h in result.get('hits', [])]
        hard = state.hard_filters()
        kept = filters.apply(hits, hard)
        if self._filters_of({**self._search_args(state.transcripts[-1]), **hard.search_arguments()}) \
                != self._retrieval_filters:
            # The filters changed after the search started (a commit, or a change of mind
            # that re-derived the gender): search again with the current filters.
            try:
                again, more_ms = await self._timed_search({**self._search_args(state.transcripts[-1]), **hard.search_arguments()})
                timings['retrievalMs'] += round(more_ms)
                kept = filters.apply([self._slim(h) for h in again.get('hits', [])], hard)
                result = again
            except Exception:  # noqa: BLE001
                logger.warning('⚠️ follow-up search failed', exc_info=True)
        catalog = result.get('totalCatalog', deps.catalog.total)
        counts = {'catalog': catalog, 'retrieved': len(hits), 'afterFilters': len(kept)}
        if not kept:
            metrics.emit('NoMatchTurns', 1)
            await deps.send(ui_events.status('no_match', f'Nothing matches {self._constraints()}.'))
            state.shelf = []   # the host's shelf always matches the screen
            await deps.send(ui_events.shelf(turn, [], True, catalog, 0, 0))
            return await self._no_match_outcome(f"Nothing in the catalog matches {self._constraints()}.", 0, counts)

        candidates = kept[:CANDIDATES]
        ranked = candidates
        if not decider_down:
            started = time.perf_counter()
            try:
                chosen = await self._ask('best_fit', questions.state_text(state, state.transcripts[-1], remembered=True),
                                                questions.best_fit_question(candidates))
                timings['chooseMs'] = round((time.perf_counter() - started) * 1000)
                metrics.latency('DeciderLatencyMs', chosen.client_ms, Round='choose')
                self._round_answers.update(chosen.answers)
                best_fit = chosen.answers.get('best_fit')
                if best_fit and policy.no_match(best_fit, deps.thresholds):
                    # A no-match never hides the shelf: the closest items stay, marked tentative.
                    metrics.emit('NoMatchTurns', 1)
                    state.shelf = policy.rank_by_best_fit(candidates, best_fit)[:SHELF_SIZE]
                    self._results = []
                    state.open_product = None
                    await deps.send(ui_events.status('no_match', 'No exact match. These are the closest.'))
                    await deps.send(ui_events.shelf(turn, state.shelf, True, catalog, len(kept), len(candidates)))
                    return await self._no_match_outcome(
                        f"Nothing is exactly what you asked for, so these are the closest {self._constraints()}.",
                        len(kept), {**counts, 'candidates': len(candidates)})
                if best_fit:
                    ranked = policy.rank_by_best_fit(candidates, best_fit)
            except (DeciderUnavailable, DeciderError):
                logger.warning('⚠️ choose round failed; keeping retrieval order', exc_info=True)
        state.shelf = ranked[:SHELF_SIZE]
        self._results = ranked + kept[len(candidates):]
        state.open_product = None
        await deps.send(ui_events.shelf(turn, state.shelf, policy.is_tentative(state), catalog, len(kept), len(candidates)))
        timings['shelfMs'] = self._elapsed_ms()
        metrics.latency('ShelfLatencyMs', timings['shelfMs'])
        top = state.shelf[0]
        return {'directive': 'narrow' if policy.is_tentative(state) else 'act',
                'say': f"Showing {len(state.shelf)} of {len(kept)} matches for {self._constraints()}. Top pick: {top['title'][:80]}, "
                       f"{ui_events.format_cents(top['priceCents'])}.",
                '_counts': {**counts, 'candidates': len(candidates)}}

    async def _pointing(self, turn, plan, retrieval, timings, decider_down) -> dict:
        state, deps = self.state, self.deps
        if not state.shelf:
            return {'directive': 'ask', 'say': 'There is nothing on screen yet. What are you looking for?'}
        started = time.perf_counter()
        try:
            chosen = await self._ask('pointing', questions.pointing_text(state, state.transcripts[-1]),
                                            questions.pointed_item_question(state.shelf, state.columns))
        except (DeciderUnavailable, DeciderError):
            logger.warning('⚠️ pointing round failed', exc_info=True)
            return {'directive': 'ask', 'say': 'Which number do you mean? You can also tap it.'}
        timings['chooseMs'] = round((time.perf_counter() - started) * 1000)
        self._round_answers.update(chosen.answers)
        # One policy: confident -> open it; unsure -> confirm it by its on-screen number
        # (the shopper decides, spoken even with voice replies off); nothing -> ask.
        answer = chosen.answers.get('pointed_item')
        number = next((i + 1 for i, p in enumerate(state.shelf) if answer and p['asin'] == answer.choice), None)
        band = deps.thresholds.band(answer.confidence if answer else None, 'pointed_item') if number else 'ask'
        if band == 'act':
            return await self._open(turn, answer.choice, pointed_probability=answer.confidence)
        if band == 'narrow':
            state.pending_offer = f'open:{answer.choice}'
            return {'directive': 'ask', 'confirmation': True,
                    'say': f"Do you mean {questions.short_name(state.shelf[number - 1], number)}?"}
        return {'directive': 'ask', 'say': 'Which number do you mean? You can also tap it.'}

    async def _open(self, turn: int, asin: str, pointed_probability: float | None = None) -> dict:
        state, deps = self.state, self.deps
        rank = next((i for i, p in enumerate(state.shelf) if p['asin'] == asin), None)
        try:
            started = time.perf_counter()
            detail = (await deps.tools.get_product_detail({'asin': asin})).get('product')
            await self._activity('dynamodb', 'Read the product details from DynamoDB (VoiceShop-Products)',
                                 (detail or {}).get('title', asin)[:80], (time.perf_counter() - started) * 1000)
        except Exception:  # noqa: BLE001
            logger.error('❌ get_product_detail failed', exc_info=True)
            detail = None
        if not detail or not state.hard_filters().allows(detail):
            # The hard-filter invariant also covers ui.product_open.
            return {'directive': 'ask', 'say': "I can't open that one. Which other item would you like?"}
        state.open_product = self._slim(detail)
        line = next((i for i in state.cart.get('items', []) if i['asin'] == asin), None)
        state.pending_offer = f"add_more:{asin}" if line else f"add_to_cart:{asin}"
        question = f"It's already in your cart ({line.get('quantity', 1)}). Should I add another one?" if line \
            else 'Should I add it to your cart?'
        why = policy.why_this_pick(state, detail, rank, len(state.shelf), pointed_probability)
        await deps.send(ui_events.product_open(turn, detail, why, pointed_probability))
        return {'directive': 'act', 'confirmation': True,
                'say': f"Opened {questions.short_name(detail, rank + 1) if rank is not None else detail['title'][:80]}, "
                       f"{ui_events.format_cents(detail['priceCents'])}. {question}",
                'product': {'title': detail['title'], 'brand': detail.get('brand'), 'price': ui_events.format_cents(detail['priceCents']),
                            'color': detail.get('color'), 'material': detail.get('material')}}

    async def remove_tapped(self, asin: str) -> None:
        """ui.cart_remove from the browser's cart panel (the line must be in the cart)."""
        if any(i['asin'] == asin for i in self.state.cart.get('items', [])):
            await self._remove(self.state.turn_seq, asin)

    async def open_tapped(self, asin: str) -> None:
        """ui.product_tap from the browser (must be on the current shelf)."""
        if any(p['asin'] == asin for p in self.state.shelf):
            await self._open(self.state.turn_seq, asin)

    async def _confirm(self, turn, plan, retrieval, timings, decider_down) -> dict:
        state = self.state
        offer, state.pending_offer = state.pending_offer, None
        if offer == 'place_order':
            return await self._do_place_order(turn)
        if offer and offer.startswith('open:'):
            return await self._open(turn, offer.split(':', 1)[1])
        if offer == 'go_back':
            return await self._go_back(turn, plan, retrieval, timings, decider_down)
        if offer == 'start_over':
            return await self._start_over(turn, plan, retrieval, timings, decider_down)
        if offer == 'remove:all':
            outcome = {'directive': 'act', 'say': 'The cart is already empty.'}
            for line in list(state.cart.get('items', [])):
                outcome = await self._remove(turn, line['asin'])
                if outcome['directive'] == 'error':
                    return outcome
            return {**outcome, 'say': 'Emptied the cart.'} if outcome['directive'] == 'act' and not state.cart.get('itemCount') else outcome
        if offer and offer.startswith('remove:'):
            return await self._remove(turn, offer.split(':', 1)[1])
        if offer and offer.startswith('remove_one:'):
            asin = offer.split(':', 1)[1]
            line = next((i for i in state.cart.get('items', []) if i['asin'] == asin), None)
            if not line:
                return {'directive': 'act', 'say': f'That is no longer in the cart. {cart_summary(state.cart)}'}
            return await self._remove(turn, asin, keep=line['quantity'] - 1)
        if offer and offer.startswith('switch:'):
            _, qid, value = offer.split(':', 2)
            state.push_history()
            policy.reopen(state, qid)
            if qid == 'recipient':
                policy.reset_for_new_person(state)
            setattr(state, qid, policy.Commit(value=value, confidence=1.0, source='confirmed'))
            implied = questions.implied_gender(value) if qid == 'recipient' else None
            if implied and state.gender is None:
                state.gender = policy.Commit(value=implied, confidence=1.0, source='recipient')
            text = next((t for t in reversed(state.transcripts[:-1]) if t), state.transcripts[-1])
            if qid == 'department':
                await self._category_round(text, timings)   # within the new department
            await self.deps.send(ui_events.chips(turn, policy.chips(state), state.remembered))
            args = {**{k: v for k, v in self._search_args(text).items() if k in ('query', 'limit')},
                    **state.hard_filters().search_arguments()}
            self._retrieval_filters = self._filters_of(args)
            return await self._narrowing(turn, plan, asyncio.create_task(self._timed_search(args)), timings, decider_down)
        if offer and offer.startswith('widen:'):
            state.push_history()
            policy.reopen(state, offer.split(':', 1)[1])
            await self.deps.send(ui_events.chips(turn, policy.chips(state), state.remembered))
            # The query comes from the earlier sentences; the filters are exactly the current
            # chips (an amount in an old sentence must not bring a dropped budget back).
            text = next((t for t in reversed(state.transcripts[:-1]) if t), state.transcripts[-1])
            query = {k: v for k, v in self._search_args(text).items() if k in ('query', 'limit')}
            args = {**query, **state.hard_filters().search_arguments()}
            self._retrieval_filters = self._filters_of(args)
            return await self._narrowing(turn, plan, asyncio.create_task(self._timed_search(args)), timings, decider_down)
        asin = offer.split(':', 1)[1] if offer and offer.startswith(('add_to_cart:', 'add_more:')) else \
            (state.open_product or {}).get('asin')
        if not asin:
            return {'directive': 'ask', 'say': 'Which one should I add?'}
        line = next((i for i in state.cart.get('items', []) if i['asin'] == asin), None)
        if line and offer != f'add_more:{asin}':
            # Already in the cart: another one only on an explicit yes.
            state.pending_offer = f'add_more:{asin}'
            return {'directive': 'ask', 'confirmation': True,
                    'say': f"{line.get('title', 'That item')[:80]} is already in your cart ({line.get('quantity', 1)}). "
                           'Should I add another one?'}
        return await self._add(turn, asin)

    @_cart_serialized
    async def _add(self, turn: int, asin: str) -> dict:
        state, deps = self.state, self.deps
        key = self._retry_keys.pop(asin, None) or request_key(state.session_id)
        try:
            started = time.perf_counter()
            result = await deps.tools.add_to_cart({'sessionId': state.session_id, 'asin': asin, 'quantity': 1,
                                                   'idempotencyKey': key})
            cart = result.get('cart') or {}
            await self._activity('dynamodb', 'Added to the cart: one DynamoDB transaction on VoiceShop-Cart',
                                 f"1 × {next((p['title'] for p in state.shelf if p['asin'] == asin), asin)[:60]} · cart now "
                                 f"{cart.get('itemCount', '?')} item(s), {ui_events.format_cents(cart.get('totalCents', 0))} · "
                                 'a retry with the same request key changes nothing', (time.perf_counter() - started) * 1000)
        except Exception:  # noqa: BLE001
            logger.error('❌ add_to_cart failed', exc_info=True)
            self._retry_keys[asin] = key
            return {'directive': 'error', 'say': "I couldn't add that to the cart."}
        if 'cart' not in result:
            return {'directive': 'error', 'say': "I couldn't add that to the cart."}
        state.cart = result['cart']
        await deps.send(ui_events.cart(state.cart))
        if state.open_product and state.open_product.get('asin') == asin:
            state.open_product = None   # added: back to the shelf
            await deps.send(ui_events.product_close(turn))
        return {'directive': 'act', 'say': f"Added to the cart. {cart_summary(state.cart)}"}

    async def _place_order(self, turn, plan, retrieval, timings, decider_down) -> dict:
        return await self._do_place_order(turn)

    @_cart_serialized
    async def _do_place_order(self, turn: int) -> dict:
        state, deps = self.state, self.deps
        if not state.cart.get('itemCount'):
            return {'directive': 'act', 'say': 'The cart is empty, so there is nothing to order yet.'}
        request = {'sessionId': state.session_id, 'idempotencyKey': request_key(state.session_id)}
        result = None
        for attempt in range(2):   # the same key: a retry returns the order instead of placing a second one
            try:
                started = time.perf_counter()
                result = await deps.tools.place_order(request)
                order = result.get('order') or {}
                await self._activity('dynamodb', 'Placed the order: one DynamoDB transaction writes VoiceShop-Orders and empties VoiceShop-Cart',
                                     f"order {order.get('orderId', '?')} · {order.get('itemCount', '?')} item(s), "
                                     f"{ui_events.format_cents(order.get('totalCents', 0))}" if order else str(result.get('error', '')),
                                     (time.perf_counter() - started) * 1000)
                break
            except Exception:  # noqa: BLE001
                logger.error('❌ place_order failed (attempt %d)', attempt + 1, exc_info=True)
        if result is None:
            return {'directive': 'error', 'say': "I couldn't confirm the order. If the cart is now empty, it was placed."}
        if result.get('error') == 'EmptyCart':
            state.cart = {'items': [], 'itemCount': 0, 'totalCents': 0}
            await deps.send(ui_events.cart(state.cart))
            return {'directive': 'act', 'say': 'The cart is empty, so there is nothing to order yet.'}
        order = result.get('order')
        if not order:
            logger.error('❌ place_order returned %s', result.get('error'))
            return {'directive': 'error', 'say': "I couldn't place the order. Please try again."}
        state.cart = {'items': [], 'itemCount': 0, 'totalCents': 0}
        state.pending_offer = None
        await deps.send(ui_events.order_placed(turn, order))
        await deps.send(ui_events.cart(state.cart))
        return {'directive': 'act', 'say': f"Order placed: {order['itemCount']} item{'s' if order['itemCount'] != 1 else ''}, "
                                           f"{ui_events.format_cents(order['totalCents'])}. No payment was taken."}

    async def _ask_offer(self, turn, plan, retrieval, timings, decider_down) -> dict:
        """An unsure irreversible request: record it and ask one spoken yes/no question.
        Nothing on screen changes until the shopper says yes."""
        if plan.offer == 'place_order' and not (self.state.cart or {}).get('itemCount'):
            return {'directive': 'act', 'say': 'The cart is empty, so there is nothing to order yet.'}
        self.state.pending_offer = plan.offer
        return {'directive': 'ask', 'confirmation': True, 'say': f'Should I {questions.offer_text(self.state)}?'}

    async def _remove_from_cart(self, turn, plan, retrieval, timings, decider_down) -> dict:
        """Which cart line the sentence names: removed when clear, otherwise asked about."""
        state = self.state
        items = state.cart.get('items', [])
        if not items:
            return {'directive': 'act', 'say': 'The cart is empty, so there is nothing to remove.'}
        sure = plan.intent_band == 'act'
        several = any(i.get('quantity', 1) > 1 for i in items)
        if len(items) == 1 and sure and not several:
            return await self._remove(turn, items[0]['asin'])
        qs = {**questions.cart_item_question(state.cart), **(questions.remove_amount_question() if several else {})}
        try:
            result = await self._ask('cart_item', questions.cart_text(state, state.transcripts[-1]), qs)
            answer, amount = result.answers.get('cart_item'), result.answers.get('remove_amount')
        except (DeciderUnavailable, DeciderError):
            answer = amount = None
        if len(items) == 1 and answer and answer.choice != 'none':
            answer = policy.Answer('cart_item', items[0]['asin'], 1.0)   # the only line
        band = self.deps.thresholds.band(answer.confidence if answer else None, 'cart_item')
        if answer and answer.choice == 'all':
            # Emptying the cart removes several lines: always asked first.
            state.pending_offer = 'remove:all'
            return {'directive': 'ask', 'confirmation': True, 'say': 'Should I empty the cart?'}
        if not answer or answer.choice in (None, 'none') or band == 'ask':
            return {'directive': 'ask', 'say': 'Which item should I remove? You can also tap the × next to it in the cart.'}
        line = next((i for i in items if i['asin'] == answer.choice), {})
        if line.get('quantity', 1) > 1 and not (amount and amount.choice == 'all'
                                                and self.deps.thresholds.band(amount.confidence, 'remove_amount') == 'act'):
            # Several of this line: one comes out unless the shopper clearly means all of them.
            if band == 'act' and sure:
                return await self._remove(turn, answer.choice, keep=line['quantity'] - 1)
            state.pending_offer = f"remove_one:{answer.choice}"
            return {'directive': 'ask', 'confirmation': True, 'say': f'Should I {questions.offer_text(state)}?'}
        if band == 'act' and sure:
            return await self._remove(turn, answer.choice)
        state.pending_offer = f'remove:{answer.choice}'   # unsure which line, or whether to remove: ask first
        return {'directive': 'ask', 'confirmation': True, 'say': f'Should I {questions.offer_text(state)}?'}

    @_cart_serialized
    async def _remove(self, turn: int, asin: str, keep: int = 0) -> dict:
        """Removes the line, or with keep lowers it to that many."""
        state, deps = self.state, self.deps
        title = next((i['title'] for i in state.cart.get('items', []) if i['asin'] == asin), asin)
        try:
            started = time.perf_counter()
            request = {'sessionId': state.session_id, 'asin': asin, **({'keepQuantity': keep} if keep else {})}
            result = await deps.tools.remove_from_cart(request)
            await self._activity('dynamodb', 'Removed from the cart: DynamoDB VoiceShop-Cart',
                                 f'{title[:80]}' + (f' · {keep} left' if keep else ''), (time.perf_counter() - started) * 1000)
        except Exception:  # noqa: BLE001
            logger.error('❌ remove_from_cart failed', exc_info=True)
            return {'directive': 'error', 'say': "I couldn't remove that from the cart."}
        if 'cart' not in result:
            return {'directive': 'error', 'say': "I couldn't remove that from the cart."}
        state.cart = result['cart']
        await deps.send(ui_events.cart(state.cart))
        if keep:
            return {'directive': 'act', 'say': f'Removed one {title[:80]}; {keep} left. {cart_summary(state.cart)}'}
        return {'directive': 'act', 'say': f'Removed {title[:80]}. {cart_summary(state.cart)}'}

    async def _show_more(self, turn, plan, retrieval, timings, decider_down) -> dict:
        """The next twelve of the latest search's results, after the ones on screen."""
        state = self.state
        on_screen = [p['asin'] for p in state.shelf]
        if [p['asin'] for p in self._results[:len(on_screen)]] != on_screen:
            # The shelf is not from the latest search (for example after "go back" or a
            # no-match turn): search it again. The turn's own search was cancelled for a
            # show_more intent, so this is a new one, for the sentence before "show me more".
            text = next((t for t in reversed(state.transcripts[:-1]) if t), state.transcripts[-1])
            args = self._search_args(text)
            self._retrieval_filters = self._filters_of(args)
            return await self._narrowing(turn, plan, asyncio.create_task(self._timed_search(args)), timings, decider_down)
        total = len(self._results)
        if len(on_screen) >= total:
            return {'directive': 'act', 'say': f'That is all {total} match{"es" if total != 1 else ""}.'}
        if len(on_screen) >= questions.MAX_OPTIONS - 1:
            return {'directive': 'act', 'say': 'That is as many as fit on one page. Add a detail to narrow them down.'}
        state.push_history()   # "go back" returns to the shorter shelf
        end = min(len(on_screen) + SHELF_SIZE, total, questions.MAX_OPTIONS - 1)
        state.shelf = self._results[:end]
        await self.deps.send(ui_events.shelf(turn, state.shelf, policy.is_tentative(state), self.deps.catalog.total,
                                             total, len(state.shelf)))
        await self.deps.send(ui_events.scroll(turn, 'down'))
        return {'directive': 'act', 'say': f'Showing {len(state.shelf)} of {total} matches. '
                                           f'Items {len(on_screen) + 1} to {len(state.shelf)} are new.'}

    async def _close_item(self, turn, plan, retrieval, timings, decider_down) -> dict:
        self.state.open_product = None
        await self.deps.send(ui_events.product_close(turn))
        return {'directive': 'act', 'say': 'Closed.'}

    async def _move_screen(self, turn, plan, retrieval, timings, decider_down) -> dict:
        """Scrolling changes nothing but the view, so the direction's top choice is used
        at any confidence; down if the decider cannot answer."""
        direction = 'down'
        try:
            result = await self._ask('scroll', f'Shopper said: "{self.state.transcripts[-1]}"', questions.scroll_question())
            answer = result.answers.get('scroll')
            direction = answer.choice if answer and answer.choice in ('down', 'up', 'top') else direction
            timings['scrollMs'] = round(result.client_ms)
        except (DeciderUnavailable, DeciderError):
            logger.warning('⚠️ scroll question failed', exc_info=True)
        await self.deps.send(ui_events.scroll(turn, direction))
        return {'directive': 'act', 'say': ''}

    async def _decline(self, turn, plan, retrieval, timings, decider_down) -> dict:
        """A no to the pending yes/no question: the offer is dropped (above) and nothing changes."""
        return {'directive': 'act', 'say': 'Okay, leaving it as it is.'}

    async def _go_back(self, turn, plan, retrieval, timings, decider_down) -> dict:
        if not self.state.go_back():
            return {'directive': 'act', 'say': 'There is nothing to go back to.'}
        await self._push_view(turn)
        return {'directive': 'act', 'say': 'Back to the previous results.'}

    async def _start_over(self, turn, plan, retrieval, timings, decider_down) -> dict:
        try:
            result = await self.deps.tools.search_catalog({'query': INITIAL_SHELF_QUERY, 'limit': SHELF_SIZE,
                                                           'availableOnly': True})
            initial = [self._slim(h) for h in result.get('hits', [])][:SHELF_SIZE]
        except Exception:  # noqa: BLE001
            initial = []
        self.state.start_over(initial)
        await self._push_view(turn)
        return {'directive': 'act', 'say': 'Starting over. What are you looking for?'}

    async def _push_view(self, turn: int) -> None:
        state = self.state
        await self.deps.send(ui_events.chips(turn, policy.chips(state), state.remembered))
        await self.deps.send(ui_events.shelf(turn, state.shelf, policy.is_tentative(state), self.deps.catalog.total,
                                             len(state.shelf), len(state.shelf)))

    async def _no_match_outcome(self, said: str, matches: int, counts: dict) -> dict:
        """A no-match ends with one yes/no offer to widen the search (spoken even with
        voice replies off). It drops the filter that adds the most matches, measured
        by searching once without each committed filter."""
        offer = await self._widen_offer(matches)
        if not offer:
            return {'directive': 'no_match', 'say': f'{said} What should I change?', '_counts': counts}
        slot, question, more = offer
        self.state.pending_offer = f'widen:{slot}'
        return {'directive': 'no_match', 'confirmation': True, 'say': f'{said} {question} That shows {more} matches.',
                '_counts': counts}

    async def _widen_offer(self, matches: int) -> tuple[str, str, str] | None:
        state = self.state
        hard = state.hard_filters()
        slots = [s for s in ('category', 'department', 'gender', 'budget') if getattr(hard, s) is not None]
        if not slots:
            return None
        text = next((t for t in reversed(state.transcripts) if t), '')
        base = {k: v for k, v in self._search_args(text).items() if k in ('query', 'limit')}

        async def count_without(slot: str) -> int:
            wider = dataclasses.replace(hard, **{slot: None})
            try:
                result, _ = await self._timed_search({**base, **wider.search_arguments()})
            except Exception:  # noqa: BLE001
                logger.warning('⚠️ widening search failed for %s', slot, exc_info=True)
                return 0
            return len(filters.apply([self._slim(h) for h in result.get('hits', [])], wider))

        counts = await asyncio.gather(*(count_without(s) for s in slots))
        best, count = max(zip(slots, counts), key=lambda sc: sc[1])
        if count <= matches:
            return None
        questions_by_slot = {
            'category': f"Should I look beyond {hard.category.lower()}?" if hard.category else '',
            'department': f"Should I look beyond {hard.department.lower()}?" if hard.department else '',
            'gender': f"Should I include all styles, not only {policy.LABELS.get(hard.gender, hard.gender)}?",
            'budget': f"Should I go beyond {hard.budget.label().lower()}?" if hard.budget else '',
        }
        return best, questions_by_slot[best], f"{count}{'+' if count >= RETRIEVAL_LIMIT else ''}"

    def _constraints(self) -> str:
        state = self.state
        parts = []
        if state.gender:
            parts.append(policy.LABELS.get(state.gender.value, state.gender.value))
        parts.append(state.category.value.lower() if state.category else 'items')
        if state.budget:
            parts.append(state.budget.label().lower())
        return ' '.join(parts)
