"""Typed questions and the decider state text.

Builds Strands Decider question specs from SessionState. Every question in one call
shares the same state text, so the decider reads the state once per call. No
question is ever numeric: amounts are parsed in code (filters.parse_budget).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from filters import Budget
from positions import position
from state import SessionState

MAX_OPTIONS = 50
NONE_OF_THESE = 'none_of_these'
NONE = 'none'
NOT_MENTIONED = 'not_mentioned'

INTENTS = ['narrowing', 'pointing', 'confirm', 'remove_from_cart', 'place_order', 'go_back', 'start_over', 'close_item',
           'move_screen', 'show_more', 'other']
# Option descriptions measured on the live decider (2026-10-18): with them the top
# intent was right for 11 of 12 test sentences; bare names left most below 0.5.
INTENT_DESCRIPTIONS = {
    'narrowing': "Asks for a kind of product or adds a preference: who it is for, style, category, color, material or "
                 "budget (e.g. 'a gift for my mom', 'something in purple', 'under 50 dollars')",
    'pointing': "Picks out one specific item that is already on screen, by its position or as 'that one', or corrects "
                "which one they meant (e.g. 'the second one', 'show me that bag', 'number five, not six')",
    'confirm': "Says yes to the assistant's offer, or asks to add the open item to the cart (e.g. 'yes', 'add it to my cart')",
    # "Can you remove the coral bay flutter sleeve blouse from the cart" was go_back 0.64
    # (live session, 2026-10-20); with this option removal sentences are 0.85-0.95, and
    # with "or everything" "can you empty my cart" went from 0.43 to 0.97.
    'remove_from_cart': "Wants to take an item, or everything, out of the cart "
                        "(e.g. 'remove the blouse from my cart', 'empty my cart')",
    'place_order': 'Wants to check out or place the order now',
    'go_back': 'Wants to undo the last change or see the previous results',
    'start_over': 'Wants to clear everything and start a new search',
    # Screen actions (live session, 2026-10-19): without them "can you close this" was
    # place_order 0.50 and "scroll down" a search; with them 0.90 and 0.94. "The items
    # stay the same" keeps "show me more of those" (purple ones) narrowing, 0.59.
    'close_item': "Wants to close the open item and see the results again, without choosing anything (e.g. 'close this', 'hide it')",
    'move_screen': "Wants to scroll the page up or down; the items stay the same (e.g. 'scroll down', 'scroll back up to the top')",
    # "Can you show me the next twelve" was a new search that returned the same twelve
    # (live session, 2026-10-20); with this option 7 of 7 such sentences are show_more
    # at 0.64-0.93. A sentence that also changes a chip searches instead (turn_pipeline).
    'show_more': "Wants more of the same results, beyond the ones shown, without asking for anything different "
                 "(e.g. 'show me the next twelve', 'show me more', 'can I see all of them')",
    'other': 'Anything else, such as a question about a product or small talk',
}
# Fine-grained recipients (domain adaptation, "How we tuned it"): the decider picks
# "mother" far more reliably than it infers women's products from "parent", so the
# chosen option carries the gender. Measured on the earlier catalog's labeled set, 2026-10-18:
# recipient top-1 0.93 -> 0.99 and gender 0.83 -> 1.00 against bare options.
RECIPIENT_OPTIONS = {
    'self': ('The shopper themself', None), 'mother': ('Their mother or mom', 'women'),
    'father': ('Their father or dad', 'men'), 'parent': ('A parent, not saying which', None),
    'wife': ('Their wife', 'women'), 'husband': ('Their husband', 'men'), 'girlfriend': ('Their girlfriend', 'women'),
    'boyfriend': ('Their boyfriend', 'men'), 'partner': ('A spouse or partner, not saying which', None),
    'daughter': ('Their daughter or a little girl', 'girls'), 'son': ('Their son or a little boy', 'boys'),
    'child': ('A child, not saying which', None), 'sister': ('Their sister', 'women'), 'brother': ('Their brother', 'men'),
    'grandmother': ('Their grandmother or grandma', 'women'), 'grandfather': ('Their grandfather or grandpa', 'men'),
    'aunt': ('Their aunt', 'women'), 'uncle': ('Their uncle', 'men'), 'friend': ('A friend', None),
    'coworker': ('A coworker, boss or someone at the office', None), 'other': ('Someone else', None),
}
GENDER_DESCRIPTIONS = {'women': "Women's products", 'men': "Men's products", 'unisex': 'Unisex products, for anyone',
                       'girls': "Girls' products", 'boys': "Boys' products"}
BUDGET_BAND_DESCRIPTIONS = {'under_25': 'Under $25, cheap', '25_to_50': '$25 to $50', '50_to_100': '$50 to $100',
                            '100_to_200': '$100 to $200', 'over_200': 'Over $200, luxury'}
NOT_MENTIONED_DESCRIPTIONS = {'recipient': 'The sentence does not say who it is for', 'gender': 'The sentence does not say',
                              'department': 'The sentence does not say what kind of product',
                              'category': 'The sentence does not name a kind of item', 'budget_band': 'No budget mentioned'}
_UNITS = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth']
_TEENS = ['tenth', 'eleventh', 'twelfth', 'thirteenth', 'fourteenth', 'fifteenth', 'sixteenth', 'seventeenth',
          'eighteenth', 'nineteenth']
_TENS = {20: ('twentieth', 'twenty'), 30: ('thirtieth', 'thirty'), 40: ('fortieth', 'forty')}
# Ordinal words for every card a shelf can hold ("show more" adds twelve at a time, up to 49).
ORDINALS = _UNITS + _TEENS + [
    _TENS[t][0] if u == 0 else f'{_TENS[t][1]}-{_UNITS[u - 1]}' for t in (20, 30, 40) for u in range(10)]


DEFAULT_GENDERS = ['women', 'men', 'unisex', 'girls', 'boys']


def implied_gender(recipient: str | None) -> str | None:
    return RECIPIENT_OPTIONS.get(recipient or '', (None, None))[1]


@dataclass
class Catalog:
    """What the catalog offers, read from CATALOG#META (backend/catalog-data): the
    departments with their categories, and a short description of each, written
    from the catalog's taxonomy. The questions take their options from here, so
    a different catalog needs no code change ("How we tuned it")."""
    total: int = 0
    genders: list[str] = field(default_factory=lambda: list(DEFAULT_GENDERS))
    departments: dict[str, list[str]] = field(default_factory=dict)
    department_descriptions: dict[str, str] = field(default_factory=dict)
    category_descriptions: dict[str, str] = field(default_factory=dict)

    def categories_of(self, department: str | None) -> list[str]:
        return list(self.departments.get(department or '', []))
BUDGET_BANDS = {
    'under_25': Budget(max_cents=2500),
    '25_to_50': Budget(min_cents=2500, max_cents=5000),
    '50_to_100': Budget(min_cents=5000, max_cents=10000),
    '100_to_200': Budget(min_cents=10000, max_cents=20000),
    'over_200': Budget(min_cents=20000),
}
# A category is asked only within a department: ten departments of five categories
# each, never one 50-way choice (more options, lower and less calibrated confidences).
SLOT_PRIORITY = ['recipient', 'gender', 'department', 'category', 'budget_band']

INSTRUCTIONS = {
    'intent': 'What does the shopper want to do with their latest sentence?',
    'recipient': 'Who is the shopper buying for?',
    'gender': 'Does the shopper say which gender the products are for?',
    'department': 'Which part of the store is the shopper shopping in?',
    'category': 'Which kind of item does the shopper want?',
    'budget_band': 'Which price range does the shopper describe, if any?',
    'liked_signal': 'Does the shopper express that they like the product that is open or that they pointed at?',
    'offer_reply': "How does the shopper's reply answer the assistant's question?",
    'best_fit': "Which product best matches what the shopper is looking for?",
    'pointed_item': 'Which on-screen item is the shopper pointing at?',
    'scroll': 'Which way does the shopper want the page to move?',
    'cart_item': 'Which item in the cart does the shopper mean?',
    'remove_amount': 'How many of that item does the shopper want taken out of the cart?',
}


def _described(question_id: str, options: dict[str, str]) -> dict:
    criteria = {**options, NOT_MENTIONED: NOT_MENTIONED_DESCRIPTIONS[question_id]}
    return {'type': 'choice', 'instructions': INSTRUCTIONS[question_id], 'criteria': criteria}


def select_understand(state: SessionState, parsed_budget: Budget | None) -> list[str]:
    """Selection rule (revised 2026-10-18, second time): intent, then every open slot
    in priority order (budget_band skipped when the parser found an amount), then
    liked_signal when a product is open. Committed slots are re-checked in their own
    call (recheck_ids).

    The earlier rule asked at most two slots per round, so while the recipient stayed
    open the category was never asked and "show me watches instead" was lost. All
    open slots in one call cost ~100 ms more (231 -> 335 ms median, 3 vs 5 questions)."""
    open_slots = []
    for slot in SLOT_PRIORITY:
        name = 'budget' if slot == 'budget_band' else slot
        if not state.is_open(name):
            continue
        if slot == 'budget_band' and parsed_budget is not None:
            continue
        if slot == 'category' and state.department is None:
            continue   # asked within a department (in the same turn once it commits)
        open_slots.append(slot)
    selected = ['intent'] + open_slots
    if state.open_product is not None:
        selected.append('liked_signal')
    return selected


def recheck_ids(state: SessionState, parsed_budget: Budget | None) -> list[str]:
    """Change of mind: every committed slot is asked again, with only the latest
    sentence (recheck_text). A confident different answer replaces the chip.
    A budget from a stated amount is not re-asked: a new amount is parsed in code."""
    ids = []
    for slot in SLOT_PRIORITY:
        name = 'budget' if slot == 'budget_band' else slot
        commit = state.committed(name)
        if commit is None:
            continue
        if slot == 'budget_band' and (parsed_budget is not None or commit.source != 'band'):
            continue
        ids.append(slot)
    return ids


def recipient_recheck_text(state: SessionState, transcript: str) -> str:
    """The recipient re-check: the latest sentence and every chip except the recipient's
    own. With "For: Father" in the context, "now i want to purchase some gift for my mom"
    scored father 0.37 (mother 0.31); without it, mother 0.78. On 20 sentences: 20 right,
    no false change (pronoun sentences such as "he likes blue" answer not mentioned);
    with the chip, 3 missed (live endpoint, 2026-10-20)."""
    hidden = SessionState.from_dict(state.to_dict())
    hidden.recipient = None
    return state_text(hidden, transcript, earlier=False)


def recheck_text(state: SessionState, transcript: str) -> str:
    """The latest sentence and the chips, without the earlier sentences. With them,
    "and i want to look something for my wife" after "a gift for my brother" stayed
    brother 0.59 (wife 0.13); alone it is wife 0.69. On 18 sentences, re-asking this way
    found 7 of 9 changes with no false change in 9; the slot_changed question and a
    follow-up with the earlier sentences found 2 of 9 (live endpoint, 2026-10-19)."""
    return state_text(state, transcript, earlier=False)


def understand_questions(state: SessionState, ids: list[str], catalog: Catalog, department: str | None = None) -> dict:
    """department: the department whose categories the category question offers
    (default: the committed one)."""
    # Only the actions the screen allows: pointing and scrolling need a shelf, closing an open item.
    intents = [i for i in INTENTS if (state.shelf or i not in ('pointing', 'move_screen', 'show_more'))
               and (state.open_product or i != 'close_item') and (state.cart.get('itemCount') or i != 'remove_from_cart')]
    department = department or (state.department.value if state.department else None)
    builders = {
        'intent': lambda: {'type': 'choice', 'instructions': INSTRUCTIONS['intent'],
                           'criteria': {i: INTENT_DESCRIPTIONS[i] for i in intents}},
        'recipient': lambda: _described('recipient', {k: v[0] for k, v in RECIPIENT_OPTIONS.items()}),
        'gender': lambda: _described('gender', {g: GENDER_DESCRIPTIONS.get(g, g) for g in catalog.genders}),
        'department': lambda: _described('department', {d: catalog.department_descriptions.get(d, ', '.join(c))
                                                        for d, c in catalog.departments.items()}),
        'category': lambda: _described('category', {c: catalog.category_descriptions.get(c, c)
                                                    for c in catalog.categories_of(department)}),
        'budget_band': lambda: _described('budget_band', BUDGET_BAND_DESCRIPTIONS),
        'liked_signal': lambda: {'type': 'noul', 'instructions': INSTRUCTIONS['liked_signal']},
    }
    return {qid: builders[qid]() for qid in ids if not (qid == 'category' and not catalog.categories_of(department))
            and not (qid == 'department' and not catalog.departments)}


def product_label(product: dict) -> str:
    return f"{product['title'][:110]} | {product.get('brand', '')} | {product.get('color', '')} | ${product['priceCents'] / 100:.2f}"


def best_fit_question(candidates: list[dict]) -> dict:
    """≤ 49 candidates plus none_of_these (ADR-21). Keys are asins."""
    if len(candidates) + 1 > MAX_OPTIONS:
        raise ValueError(f'best_fit would have {len(candidates) + 1} options (max {MAX_OPTIONS})')
    criteria = {p['asin']: product_label(p) for p in candidates}
    criteria[NONE_OF_THESE] = 'None of these products is what the shopper asked for'
    return {'best_fit': {'type': 'choice', 'instructions': INSTRUCTIONS['best_fit'], 'criteria': criteria}}


def pointed_item_question(shelf: list[dict], columns: int) -> dict:
    """Ordinal-rich labels ("Item 2, the second item: row 1, second from the left"):
    pointing top-1 0.70 -> 0.90 on the eval set against "row 1 center, item 2"."""
    items = shelf[:MAX_OPTIONS - 1]   # every card on screen, 12 more per "show more"
    criteria = {}
    for index, product in enumerate(items):
        row, col = position(index, columns)
        side = 'far left' if col == 0 else 'far right' if col == columns - 1 else f'{ORDINALS[col]} from the left'
        last = ', the last item' if index == len(items) - 1 else ''
        criteria[product['asin']] = (f"Item {index + 1}, the {ORDINALS[index]} item{last}: row {row}, {side} | "
                                     f"{product['title'][:90]} | {product.get('color', '')}")
    criteria[NONE] = 'None of the items on screen'
    return {'pointed_item': {'type': 'choice', 'instructions': INSTRUCTIONS['pointed_item'], 'criteria': criteria}}


def shelf_listing(state: SessionState) -> str:
    """The numbered on-screen listing, used only in the pointing round's state text
    (in the understand round it pulled category and intent toward what was on screen)."""
    rows = -(-len(state.shelf) // max(state.columns, 1))
    items = '; '.join(f"{i + 1}. {p['title'][:60]} ({p.get('color', '')})" for i, p in enumerate(state.shelf[:MAX_OPTIONS - 1]))
    return f'On screen, {state.columns} columns, {rows} rows, numbered left to right then top to bottom: {items}'


def _commit_text(slot: str, state: SessionState) -> str | None:
    commit = state.committed(slot)
    if commit is None:
        return None
    if slot == 'budget':
        return f"budget {state.budget.label().lower() if state.budget else commit.value} ({'stated' if commit.source == 'stated' else round(commit.confidence, 2)})"
    return f"{slot}={commit.value} ({round(commit.confidence, 2)})"


def offer_reply_question() -> dict:
    """Asked in its own call whenever an offer is pending: how the reply answers that
    one yes/no question."""
    return {'offer_reply': {'type': 'choice', 'instructions': INSTRUCTIONS['offer_reply'], 'criteria': {
        'yes': 'Agrees', 'no': 'Declines', 'new_request': 'Ignores the question and asks for something else'}}}


def offer_reply_text(state: SessionState, transcript: str) -> str:
    """Only the question and the reply. With the shop state, "go back to the previous
    results" read "no" as go_back 0.75 and "yeah" as yes 0.22; alone, every yes scored
    at least 0.84 and every no at least 0.73 (live endpoint, 2026-10-19)."""
    return f'Assistant asked: "Should I {offer_text(state)}?"\nShopper replied: "{transcript}"'


def cart_item_question(cart: dict) -> dict:
    """Which cart line a removal names; asked with only the sentence and the cart.
    Measured on the live endpoint (2026-10-20): the named item at 0.93-0.97."""
    criteria = {i['asin']: i['title'] for i in cart.get('items', [])}
    criteria['all'] = 'Every item in the cart'
    criteria['none'] = 'None of the items in the cart'
    return {'cart_item': {'type': 'choice', 'instructions': INSTRUCTIONS['cart_item'], 'criteria': criteria}}


def remove_amount_question() -> dict:
    """Asked with cart_item when a cart line holds more than one: "I just need one,
    remove one" lowered nothing and emptied the line (live session, 2026-10-21)."""
    criteria = {'one': 'Just one of them, keeping the rest', 'all': 'All of them, the whole line'}
    return {'remove_amount': {'type': 'choice', 'instructions': INSTRUCTIONS['remove_amount'], 'criteria': criteria}}


def cart_text(state: SessionState, transcript: str) -> str:
    return f'Latest shopper sentence: "{transcript}"\nIn the cart: ' + '; '.join(
        f"{i['title']} (quantity {i.get('quantity', 1)})" for i in state.cart.get('items', []))


def pointing_text(state: SessionState, transcript: str) -> str:
    """The pointing round sees the cards on screen, not the open product: with "Open
    product: No. 3" in the context, "can you go with number five" chose No. 3 (0.37 vs
    0.35); without it, No. 5 at 0.52 (live endpoint, 2026-10-20)."""
    plain = SessionState.from_dict(state.to_dict())
    plain.open_product, plain.pending_offer = None, None
    return state_text(plain, transcript, listing=True)


def scroll_question() -> dict:
    """Asked alone after a move_screen intent: "show me more" is down 0.95, "take me to
    the first ones" top 0.97 (live endpoint, 2026-10-19)."""
    return {'scroll': {'type': 'choice', 'instructions': INSTRUCTIONS['scroll'], 'criteria': {
        'down': 'Further down, to see more items', 'up': 'Back up a little', 'top': 'All the way back to the first items'}}}


def short_name(product: dict, number: int) -> str:
    """How a card is named in a confirmation: its on-screen number and a short title."""
    words = product['title'].split()
    title = ' '.join(words[:7]) + ('…' if len(words) > 7 else '')
    color = product.get('color')
    return f"No. {number}, {title}" + (f" ({color})" if color else '')


def offer_text(state: SessionState) -> str:
    """The pending offer in words: the state text's 'Pending offer' line and the 'Should I ...?' question."""
    offer = state.pending_offer or ''
    if offer == 'place_order':
        return 'place the order'
    if offer == 'go_back':
        return 'go back to the previous results'
    if offer == 'start_over':
        return 'start over'
    if offer.startswith('widen:'):
        return f"widen the search by dropping the {offer.split(':', 1)[1]} filter"
    if offer.startswith('switch:'):
        _, slot, value = offer.split(':', 2)
        if slot == 'recipient':
            return {'self': 'shop for yourself instead', 'other': 'shop for someone else instead'}.get(
                value, f'shop for your {value} instead')
        if slot == 'gender':
            return f"show {GENDER_DESCRIPTIONS.get(value, value).lower()} instead"
        if slot == 'department':
            return f'look in {value} instead'
        return f'show {value.lower()} instead'
    if offer == 'remove:all':
        return 'empty the cart'
    if offer.startswith(('remove_one:', 'add_more:')):
        kind, asin = offer.split(':', 1)
        title = next((i['title'] for i in state.cart.get('items', []) if i['asin'] == asin), 'that item')
        return f'remove one {title} from the cart' if kind == 'remove_one' else f'add another {title} to the cart'
    if offer.startswith('remove:'):
        asin = offer.split(':', 1)[1]
        title = next((i['title'] for i in state.cart.get('items', []) if i['asin'] == asin), 'that item')
        return f'remove {title} from the cart'
    if offer.startswith('open:'):
        asin = offer.split(':', 1)[1]
        number = next((i + 1 for i, p in enumerate(state.shelf) if p['asin'] == asin), None)
        return f'open item number {number}' if number else 'open an item'
    return 'add the open product to the cart'


def intent_state_text(state: SessionState, transcript: str) -> str:
    """The intent question's context, asked in its own call: the full state without
    the earlier sentences. After four narrowing turns they pulled "the third one" to
    narrowing 0.63; without them it is pointing, while bare product phrases keep the
    committed-slot context they need ("a wallet" narrowing 0.71, but pointing 0.24
    with only the sentence and the screen). Measured 2026-10-18."""
    return state_text(state, transcript, earlier=False)


def state_text(state: SessionState, transcript: str, listing: bool = False, remembered: bool = False,
               earlier: bool = True) -> str:
    """remembered=True only for best_fit: memory from earlier visits ("shopping for
    mother") pulled "a gift for my dad" from father 0.79 to 0.47 in the understand
    round, which asks what this sentence says (measured 2026-10-18)."""
    previous = [t for t in state.transcripts[-3:-1]] if state.transcripts and state.transcripts[-1] == transcript else state.transcripts[-2:]
    committed = [c for c in (_commit_text(s, state) for s in ('recipient', 'gender', 'department', 'category', 'budget')) if c]
    lines = [f'Latest shopper sentence: "{transcript}"']
    if earlier and previous:
        lines.append('Earlier: ' + '; '.join(f'"{t}"' for t in previous))
    lines.append('Committed: ' + ('; '.join(committed) if committed else 'none'))
    if state.context:
        lines.append('Leaning: ' + '; '.join(f'{k}={v} ({round(p, 2)})' for k, (v, p) in state.context.items()))
    if remembered and state.remembered:
        lines.append('Remembered from earlier visits (may not apply today): ' + '; '.join(state.remembered))
    if listing and state.shelf:
        lines.append(shelf_listing(state))
    else:
        lines.append(f'On screen ({state.columns} columns): ' + (f'{len(state.shelf)} items' if state.shelf else 'none'))
    lines.append('Open product: ' + (product_label(state.open_product) if state.open_product else 'none'))
    if state.pending_offer:
        lines.append('Pending offer: ' + offer_text(state))
    return '\n'.join(lines)
