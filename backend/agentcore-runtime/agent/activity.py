"""Backend activity in plain words, for the debug overlay (ui.activity).

Each step of a turn (Nova Sonic hearing or speaking, a Strands Decider call, a
catalog search, a DynamoDB write, a memory save, the decision the code made)
becomes one line a viewer can follow without knowing the question ids.
"""

from __future__ import annotations

import time

from questions import NONE_OF_THESE, NOT_MENTIONED

QUESTION_NAMES = {
    'intent': 'What do you want to do?',
    'recipient': 'Who is it for?',
    'gender': "Women's, men's or kids' products?",
    'department': 'Which part of the store?',
    'category': 'What kind of item?',
    'budget_band': 'What budget?',
    'liked_signal': 'Do you like the open item?',
    'offer_reply': 'Yes or no to the question?',
    'best_fit': 'Which products fit best?',
    'pointed_item': 'Which card do you mean?',
    'scroll': 'Which way to scroll?',
    'cart_item': 'Which cart item?',
    'remove_amount': 'Remove one or all of them?',
}

INTENT_NAMES = {
    'narrowing': 'look for products', 'pointing': 'pick an item on screen', 'confirm': 'say yes / add to cart',
    'place_order': 'place the order', 'remove_from_cart': 'remove from the cart', 'go_back': 'go back', 'start_over': 'start over',
    'close_item': 'close the open item', 'move_screen': 'scroll the page', 'show_more': 'see more results',
    'other': 'something else',
}

PURPOSES = {
    'intent': 'What does this sentence ask for?',
    'details': 'Who it is for, what kind of item, budget',
    'recheck': 'Did you change your mind about a chip?',
    'recheck_person': 'Is it still for the same person?',
    'fresh': 'New person: what this sentence says about the item',
    'reply': 'Your answer to the yes/no question',
    'category': 'Which kind of item, within the department',
    'best_fit': 'Ranking the products the search found',
    'pointing': 'Which card on screen you mean',
    'scroll': 'Which way to scroll',
    'cart_item': 'Which item to take out of the cart',
}


DIRECTIVES = {
    'act': 'Decision: confident, so the app acted',
    'narrow': 'Decision: showing results; some details are still unsure',
    'ask': 'Decision: not sure, so the app asks one question',
    'no_match': 'Decision: nothing matched exactly; showing the closest items',
    'error': 'Something failed; the app says so',
    'other': 'Decision: nothing to do for this sentence',
}


def event(turn_id: int, service: str, title: str, detail: str = '', ms: float | None = None) -> dict:
    return {'type': 'ui.activity', 'turnId': turn_id, 'at': int(time.time() * 1000), 'service': service,
            'title': title, 'detail': detail, 'ms': None if ms is None else round(ms)}


def _option(qid: str, option: str, criteria: dict) -> str:
    if option in (NOT_MENTIONED, None):
        return 'not said'
    if option == NONE_OF_THESE:
        return 'none of these fit'
    if qid == 'intent':
        return INTENT_NAMES.get(option, option)
    if qid in ('best_fit', 'pointed_item'):
        label = str(criteria.get(option, option))
        return label.split(' | ')[1 if qid == 'pointed_item' and ' | ' in label else 0][:60]
    return option.replace('_', ' ')


def answers_detail(answers: dict, questions: dict) -> str:
    """One line per answer: "Who is it for? → wife (69%)"."""
    lines = []
    for qid, answer in answers.items():
        name = QUESTION_NAMES.get(qid, qid)
        criteria = (questions.get(qid) or {}).get('criteria', {})
        if answer.noul is not None:
            lines.append(f'{name} → yes {round(answer.noul * 100)}%')
            continue
        top = answer.top(2)
        if not top:
            continue
        first = f'{_option(qid, top[0][0], criteria)} ({round(top[0][1] * 100)}%)'
        runner = f', then {_option(qid, top[1][0], criteria)} ({round(top[1][1] * 100)}%)' if len(top) > 1 else ''
        lines.append(f'{name} → {first}{runner}')
    return '\n'.join(lines)


def decider_title(purpose: str, count: int) -> str:
    return f"Strands Decider 2B answered {count} question{'s' if count != 1 else ''}: {PURPOSES.get(purpose, purpose)}"
