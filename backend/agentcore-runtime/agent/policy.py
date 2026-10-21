"""Threshold policy.

act (>= ACT_THRESHOLD) commits, narrow (between) is ranking context only, ask
(< ASK_THRESHOLD) produces at most one clarifying question per sentence, in
priority order recipient, gender, department, category, budget_band.
The bands apply to slot questions only, never to best_fit: no_match comes from
its none_of_these option (ADR-21). Confidences shown and spoken are the
decider's own values, read from the same Answer objects.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from filters import Budget
from questions import BUDGET_BANDS, NONE_OF_THESE, NOT_MENTIONED, implied_gender
from state import Commit, SessionState

CLARIFY_PRIORITY = ['recipient', 'gender', 'department', 'category', 'budget_band']
LABELS = {
    'self': 'you', 'parent': 'your parent', 'partner': 'your partner', 'child': 'your child', 'friend': 'a friend',
    'coworker': 'a coworker', 'other': 'someone else', 'mother': 'your mom', 'father': 'your dad', 'wife': 'your wife',
    'husband': 'your husband', 'girlfriend': 'your girlfriend', 'boyfriend': 'your boyfriend', 'daughter': 'your daughter',
    'son': 'your son', 'sister': 'your sister', 'brother': 'your brother', 'grandmother': 'your grandma',
    'grandfather': 'your grandpa', 'aunt': 'your aunt', 'uncle': 'your uncle',
    'women': "women's", 'men': "men's", 'unisex': 'unisex', 'girls': 'girls', 'boys': 'boys',
}
CHIP_LABELS = {'self': 'Myself', 'parent': 'Parent', 'partner': 'Partner', 'child': 'Child', 'friend': 'Friend',
               'coworker': 'Coworker', 'other': 'Someone else', 'mother': 'Mom', 'father': 'Dad', 'wife': 'Wife',
               'husband': 'Husband', 'girlfriend': 'Girlfriend', 'boyfriend': 'Boyfriend', 'daughter': 'Daughter',
               'son': 'Son', 'sister': 'Sister', 'brother': 'Brother', 'grandmother': 'Grandma', 'grandfather': 'Grandpa',
               'aunt': 'Aunt', 'uncle': 'Uncle', 'women': 'Women', 'men': 'Men', 'unisex': 'Unisex',
               'girls': 'Girls', 'boys': 'Boys'}


CALIBRATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calibration.json')


DEFAULT_NO_MATCH = 0.35   # calibration.json noMatch (synthetic catalog, 2026-10-19); the fallback without it


@dataclass(frozen=True)
class Thresholds:
    """Act / ask thresholds. Global defaults from ACT_THRESHOLD / ASK_THRESHOLD, with
    per-question values from calibration.json (domain adaptation, "How we tuned it")."""
    act: float = 0.8
    ask: float = 0.5
    no_match: float = DEFAULT_NO_MATCH
    per_question: dict = field(default_factory=dict)   # question id -> (act, ask)

    def __post_init__(self):
        for name, (act, ask) in {'default': (self.act, self.ask), **self.per_question}.items():
            if not (0 < ask < act <= 1):
                raise ValueError(f'{name}: thresholds must satisfy 0 < ask < act <= 1 (ask={ask}, act={act})')
        if not (0 < self.no_match < 1):
            raise ValueError(f'NO_MATCH_THRESHOLD must be in (0, 1), got {self.no_match}')

    @classmethod
    def from_env(cls, calibration_file: str = CALIBRATION_FILE) -> 'Thresholds':
        per_question = {}
        no_match = str(DEFAULT_NO_MATCH)
        if os.path.exists(calibration_file):
            with open(calibration_file, encoding='utf-8') as f:
                calibration = json.load(f)
            for qid, values in calibration.get('questions', {}).items():
                per_question[qid] = (float(values['act']), float(values['ask']))
            no_match = str(calibration.get('noMatch', {}).get('threshold', no_match))
        return cls(act=float(os.environ.get('ACT_THRESHOLD', '0.8')), ask=float(os.environ.get('ASK_THRESHOLD', '0.5')),
                   no_match=float(os.environ.get('NO_MATCH_THRESHOLD', no_match)), per_question=per_question)

    def for_question(self, question_id: str | None) -> tuple[float, float]:
        return self.per_question.get(question_id or '', (self.act, self.ask))

    def band(self, confidence: float | None, question_id: str | None = None) -> str:
        """Total and monotonic per question: every confidence maps to exactly one band."""
        act, ask = self.for_question(question_id)
        if confidence is None or confidence < ask:
            return 'ask'
        return 'act' if confidence >= act else 'narrow'


@dataclass(frozen=True)
class Answer:
    """One validated decider answer (choice or noul)."""
    question_id: str
    choice: str | None
    confidence: float | None
    probabilities: dict[str, float] = field(default_factory=dict)
    noul: float | None = None

    def top(self, n: int = 2) -> list[tuple[str, float]]:
        return sorted(self.probabilities.items(), key=lambda kv: -kv[1])[:n]


@dataclass
class UnderstandPlan:
    intent: str
    intent_band: str
    commits: dict[str, Commit] = field(default_factory=dict)
    context: dict[str, tuple[str, float]] = field(default_factory=dict)
    clarify: str | None = None
    liked: bool = False
    # A yes/no confirmation question (spoken even when spoken replies are off, ADR-23).
    confirmation: bool = False
    # The action an 'ask_offer' turn offers ('place_order', 'go_back', 'start_over',
    # 'add_to_cart:<asin>', 'open:<asin>', 'widen:<slot>', 'switch:<slot>:<value>',
    # 'remove:<asin>', 'remove_one:<asin>', 'add_more:<asin>', 'remove:all'); "yes" next turn carries it out.
    offer: str | None = None


def _ask_text(slot: str, answer: Answer, state: SessionState, recipient: str | None = None) -> str:
    (a, _), (b, _) = (answer.top(2) + [('', 0.0), ('', 0.0)])[:2]
    a, b = (a if a != NOT_MENTIONED else ''), (b if b != NOT_MENTIONED else '')
    if slot == 'recipient':
        if a and b:
            return f'Is this for {LABELS.get(a, a)} or {LABELS.get(b, b)}?'
        return 'Is this for you, or is it a gift?'
    if slot == 'gender':
        recipient = recipient or (state.recipient.value if state.recipient else None)
        if recipient == 'parent':
            return 'Is that your mom or your dad?'
        if recipient == 'child':
            return 'Your daughter or your son?'
        if recipient == 'partner':
            return "Should I look at women's or men's styles?"
        return "Women's or men's?"
    if slot in ('department', 'category'):
        if a and b:
            return f'Are you thinking of {a.lower()} or {b.lower()}?'
        return 'What kind of item are you looking for?'
    if slot == 'budget_band':
        return 'Roughly what budget do you have in mind?'
    return 'Which one do you mean?'


def plan_understand(state: SessionState, answers: dict[str, Answer], parsed_budget: Budget | None,
                    thresholds: Thresholds) -> UnderstandPlan:
    """Commit / narrow / ask for one understand round. A missing or invalid answer
    counts as below the ask threshold."""
    intent_answer = answers.get('intent')
    intent = intent_answer.choice if intent_answer and intent_answer.choice else 'narrowing'
    intent_band = thresholds.band(intent_answer.confidence if intent_answer else None, 'intent')
    # A pending yes/no question is answered by its own question (offer_reply): a
    # confident yes carries out the offer and a confident no drops it, whatever the
    # intent question says ("no" to "go back?" is go_back 0.75 there).
    reply = answers.get('offer_reply')
    if state.pending_offer and reply and reply.choice in ('yes', 'no') \
            and thresholds.band(reply.confidence, 'offer_reply') == 'act':
        same = {'go_back': 'go_back', 'place_order': 'place_order', 'start_over': 'start_over'}.get(state.pending_offer, 'confirm')
        if reply.choice == 'no' and (intent_band != 'act' or intent in (same, 'confirm', 'other')):
            return UnderstandPlan(intent='decline', intent_band='act')
        if reply.choice == 'no':
            # "No, actually I like number five": the offer is dropped and the new request runs
            # (live session, 2026-10-20). A no whose request is the offered action itself
            # ("no" to "go back?" read as go_back) still only declines.
            reply = None
    if reply and reply.choice == 'yes' and state.pending_offer \
            and thresholds.band(reply.confidence, 'offer_reply') == 'act':
        intent, intent_band = 'confirm', 'act'
    plan = UnderstandPlan(intent=intent, intent_band=intent_band)
    asks: dict[str, str] = {}

    # Narrowing is the safe default (design decision 2026-10-18): when the top
    # intent is narrowing, at any confidence, narrow and let each slot commit on
    # its own confidence. Pointing below act still runs the pointing round, which
    # has its own act check. confirm / place_order / go_back / start_over never
    # run below act: the turn becomes 'ask_offer', which records the action and
    # asks one spoken yes/no question about it; the shelf is left as it is.
    demote = False
    if intent == 'place_order' and intent_band == 'act':
        # An order cannot be undone: it is always offered first, at any confidence
        # (design decision 2026-10-19); "yes" places it.
        intent_band = plan.intent_band = 'narrow'
    if intent_band == 'ask' and intent not in ('narrowing', 'pointing'):
        # Below the ask threshold the top intent is not a request to act on: on the
        # labeled set every such offer was wrong ("a lawn mower" was start_over 0.27,
        # "Should I start over?"), so the sentence narrows like any other.
        intent = plan.intent = 'narrowing'
    if intent_band != 'act' and intent not in ('narrowing', 'pointing'):
        demote = True
        offer = {'place_order': 'place_order', 'go_back': 'go_back', 'start_over': 'start_over'}.get(intent)
        if intent == 'confirm':
            offer = state.pending_offer or (f"add_to_cart:{state.open_product['asin']}" if state.open_product else None)
        plan.intent = 'ask_offer' if offer else 'narrowing'
        plan.offer = offer
        plan.confirmation = bool(offer)
    elif intent_band != 'act' and intent == 'pointing':
        plan.intent = 'pointing'
    if demote and intent == 'remove_from_cart' and intent_band == 'narrow':
        # An unsure removal still runs, but its handler only asks ("Should I remove ...?").
        plan.intent, plan.offer, plan.confirmation = 'remove_from_cart', None, False

    if parsed_budget is not None:
        plan.commits['budget'] = Commit(value=parsed_budget.label(), confidence=1.0, source='stated')

    for slot in ('recipient', 'gender', 'department', 'category', 'budget_band'):
        answer = answers.get(slot)
        if answer is None or (slot == 'budget_band' and parsed_budget is not None):
            continue   # a parsed explicit amount always wins over a budget band
        name = 'budget' if slot == 'budget_band' else slot
        band = thresholds.band(answer.confidence, slot)
        if answer.choice in (None, NOT_MENTIONED):
            # "not mentioned" is not uncertainty: nothing to commit, nothing to ask.
            continue
        if band == 'act' and not demote:
            plan.commits[name] = Commit(value=answer.choice, confidence=answer.confidence or 0.0,
                                        source='band' if slot == 'budget_band' else 'decider')
        elif band in ('act', 'narrow'):
            plan.context[name] = (answer.choice, answer.confidence or 0.0)
        else:
            # A recipient committed earlier in this same sentence shapes the gender question.
            committed_recipient = plan.commits['recipient'].value if 'recipient' in plan.commits else None
            asks[slot] = _ask_text(slot, answer, state, committed_recipient)

    # The recipient's own option implies a gender ("mother" -> women): commit it with the
    # recipient's confidence unless the gender was stated or is already committed.
    recipient_commit = plan.commits.get('recipient')
    implied = implied_gender(recipient_commit.value) if recipient_commit else None
    if implied and 'gender' not in plan.commits and state.gender is None:
        plan.commits['gender'] = Commit(value=implied, confidence=recipient_commit.confidence, source='recipient')
        plan.context.pop('gender', None)
    if 'gender' in plan.commits:
        asks.pop('gender', None)
    # UX rule: a parent, partner or child without a gender makes the gender the
    # obvious next question ("Is that your mom or your dad?"), even when the
    # decider (correctly) says the sentence did not mention one. Asked once, in
    # the turn that sets the recipient, not on every later turn.
    recipient_now = plan.commits['recipient'].value if 'recipient' in plan.commits else None
    if recipient_now in ('parent', 'partner', 'child') and 'gender' not in plan.commits and state.gender is None \
            and 'gender' not in plan.context:
        asks.setdefault('gender', _ask_text('gender', answers.get('gender') or Answer('gender', None, None), state, recipient_now))
    liked = answers.get('liked_signal')
    plan.liked = bool(liked and liked.noul is not None and liked.noul >= thresholds.for_question('liked_signal')[0])

    # One clarifying question per sentence.
    for slot in CLARIFY_PRIORITY:
        if slot in asks:
            plan.clarify = asks[slot]
            break
    return plan


def reopen(state: SessionState, slot: str) -> None:
    """Clears a committed slot the shopper changed; a gender that came from the
    recipient goes with it."""
    if slot == 'budget':
        state.budget, state.budget_commit = None, None
        return
    setattr(state, slot, None)
    if slot == 'recipient' and state.gender and state.gender.source == 'recipient':
        state.gender = None
    if slot == 'department':
        state.category = None   # a category belongs to its department


PERSON_SLOTS = ('gender', 'department', 'category', 'budget')


def reset_for_new_person(state: SessionState) -> None:
    """A new recipient is a new shopping goal (design decision 2026-10-20): the gender,
    department, category and budget chosen for the previous person are cleared, so a
    father's "Men's Clothing / Shirts" never filters the search for a mother."""
    for slot in PERSON_SLOTS:
        reopen(state, slot)
    state.context = {}


def changed_slots(state: SessionState, answers: dict[str, Answer], thresholds: Thresholds) -> dict[str, Answer]:
    """The re-checked committed slots (recheck_ids) whose confident answer differs
    from the chip. A changed department drops the category answer: that category
    belongs to the old department, and the category round asks again."""
    changes = {}
    for qid, answer in answers.items():
        slot = 'budget' if qid == 'budget_band' else qid
        commit = state.committed(slot)
        if commit is None or answer.choice in (None, NOT_MENTIONED) or thresholds.band(answer.confidence, qid) != 'act':
            continue
        value = BUDGET_BANDS[answer.choice].label() if qid == 'budget_band' and answer.choice in BUDGET_BANDS else answer.choice
        if value != commit.value:
            changes[qid] = answer
    if 'recipient' in changes:
        return {'recipient': changes['recipient']}   # the other chips are reset, not changed
    if 'department' in changes:
        changes.pop('category', None)
    return changes


def unsure_change(state: SessionState, answers: dict[str, Answer], thresholds: Thresholds) -> tuple[str, str] | None:
    """The first re-checked slot (in priority order) whose answer differs from the chip
    in the narrow band: the decider leans toward a change but is not sure. The turn
    offers it as one yes/no question instead of searching with the old chip
    ("what about some t shirts" in Sports & Beauty: Women's Clothing 0.41)."""
    for qid in ('recipient', 'gender', 'department', 'category', 'budget_band'):
        answer = answers.get(qid)
        commit = state.committed('budget' if qid == 'budget_band' else qid)
        if not answer or commit is None or answer.choice in (None, NOT_MENTIONED) or qid == 'budget_band':
            continue
        if answer.choice != commit.value and thresholds.band(answer.confidence, qid) == 'narrow':
            return qid, answer.choice
    return None


def apply_commits(state: SessionState, plan: UnderstandPlan, parsed_budget: Budget | None) -> None:
    for slot, commit in plan.commits.items():
        if slot == 'budget':
            state.budget = parsed_budget if commit.source == 'stated' else BUDGET_BANDS.get(commit.value)
            state.budget_commit = Commit(value=state.budget.label() if state.budget else commit.value,
                                         confidence=commit.confidence, source=commit.source)
        else:
            setattr(state, slot, commit)
    state.context = plan.context


def no_match(best_fit: Answer | None, thresholds: Thresholds) -> bool:
    """ADR-21: no_match when the none_of_these option reaches NO_MATCH_THRESHOLD."""
    return bool(best_fit and best_fit.probabilities.get(NONE_OF_THESE, 0.0) >= thresholds.no_match)


def rank_by_best_fit(candidates: list[dict], best_fit: Answer) -> list[dict]:
    """Candidates in best_fit probability order, none_of_these removed; candidates the
    model did not score keep their retrieval order after the scored ones."""
    probs = best_fit.probabilities
    indexed = list(enumerate(candidates))
    indexed.sort(key=lambda ip: (-probs.get(ip[1]['asin'], -1.0), ip[0]))
    return [p for _, p in indexed]


def chips(state: SessionState) -> list[dict]:
    """Chip payload (ui.chips). Confidence is the decider's own value; budget is stated (1.0)."""
    out = []
    for slot, label in (('recipient', 'For'), ('gender', 'Gender'), ('department', 'Department'), ('category', 'Category')):
        commit = state.committed(slot)
        if commit:
            out.append({'slot': slot, 'label': label, 'value': CHIP_LABELS.get(commit.value, commit.value),
                        'confidence': round(commit.confidence, 4)})
    if state.budget_commit:
        out.append({'slot': 'budget', 'label': 'Budget', 'value': state.budget_commit.value,
                    'confidence': round(state.budget_commit.confidence, 4)})
    return out


def is_tentative(state: SessionState) -> bool:
    """The shelf is tentative while a narrow-band slot is shaping it."""
    return bool(state.context)


def why_this_pick(state: SessionState, product: dict, rank: int | None, candidates: int,
                  pointed_probability: float | None = None) -> str:
    """Built in code from committed chips, the price against the budget and the rank
    (ADR-21: no best_fit probability). Never generated."""
    parts = []
    for chip in chips(state):
        if chip['slot'] == 'budget':
            continue
        parts.append(f"{chip['value']} ({round(chip['confidence'] * 100)}%)")
    price = f"${product['priceCents'] / 100:,.2f}"
    if state.budget and state.budget.max_cents is not None:
        parts.append(f"{price}, under your ${state.budget.max_cents / 100:,.0f} budget")
    else:
        parts.append(price)
    if pointed_probability is not None:
        parts.append(f'the item you pointed at ({round(pointed_probability * 100)}%)')
    elif rank is not None:
        parts.append(f'top pick of {candidates} candidates' if rank == 0 else f'#{rank + 1} of {candidates} candidates')
    return ' · '.join(parts)
