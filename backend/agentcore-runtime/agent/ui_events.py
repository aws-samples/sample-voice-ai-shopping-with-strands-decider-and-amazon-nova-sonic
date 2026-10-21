"""Typed builders for server-to-browser messages.
Image URLs are presigned by the tool Lambdas and never logged (only asins are)."""

from __future__ import annotations

SHELF_FIELDS = ('asin', 'title', 'brand', 'priceCents', 'rating', 'imageUrl')


def chips(turn_id: int, chip_list: list[dict], remembered: list[str]) -> dict:
    return {'type': 'ui.chips', 'turnId': turn_id, 'chips': chip_list, 'remembered': remembered}


def shelf(turn_id: int, items: list[dict], tentative: bool, catalog: int, after_filters: int, candidates: int) -> dict:
    return {'type': 'ui.shelf', 'turnId': turn_id, 'tentative': tentative,
            'items': [{k: item.get(k) for k in SHELF_FIELDS} for item in items],
            'counts': {'catalog': catalog, 'afterFilters': after_filters, 'candidates': candidates}}


# The AgentCore Runtime WebSocket allows 64 KB per frame; stay under it with room to spare.
MAX_FRAME_BYTES = 60_000
SHELF_CHUNK = 12


def frames(message: dict) -> list[dict]:
    """One message as WebSocket frames: a shelf over twelve items (after "show more")
    goes as ui.shelf with the first twelve, then ui.shelf_append with twelve each.
    Every item carries a presigned image URL, so 36 items already passed 64 KB."""
    if message.get('type') != 'ui.shelf' or len(message.get('items', [])) <= SHELF_CHUNK:
        return [message]
    items = message['items']
    first = {**message, 'items': items[:SHELF_CHUNK]}
    rest = [{'type': 'ui.shelf_append', 'turnId': message['turnId'], 'items': items[i:i + SHELF_CHUNK]}
            for i in range(SHELF_CHUNK, len(items), SHELF_CHUNK)]
    return [first, *rest]


def reply(turn_id: int, text: str) -> dict:
    """The app's own reply for the turn. With spoken replies off, the chat shows this
    text: Sonic then answers in two or three words ("Take a look."), which said
    nothing when the screen did not change ("the cart is empty")."""
    return {'type': 'ui.reply', 'turnId': turn_id, 'text': text}


def question(turn_id: int, text: str) -> dict:
    """A yes/no question is pending: the page shows Yes / No buttons and, when mic
    permission is already granted, opens the mic so the answer can be spoken."""
    return {'type': 'ui.question', 'turnId': turn_id, 'text': text}


def product_open(turn_id: int, product: dict, why: str, confidence: float | None = None) -> dict:
    message = {'type': 'ui.product_open', 'turnId': turn_id, 'product': product, 'why': why}
    if confidence is not None:
        message['confidence'] = confidence
    return message


def cart(cart_state: dict) -> dict:
    return {'type': 'ui.cart', 'items': cart_state.get('items', []), 'itemCount': cart_state.get('itemCount', 0),
            'totalCents': cart_state.get('totalCents', 0)}


def order_placed(turn_id: int, order: dict) -> dict:
    return {'type': 'ui.order_placed', 'turnId': turn_id, 'orderId': order['orderId'], 'itemCount': order['itemCount'],
            'totalCents': order['totalCents'], 'placedAt': order['placedAt']}


def status(kind: str, message: str) -> dict:
    assert kind in ('listening', 'thinking', 'clarifying', 'no_match', 'continuation', 'degraded', 'error'), kind
    return {'type': 'ui.status', 'kind': kind, 'message': message}


def product_close(turn_id: int) -> dict:
    """The product panel closes (added to the cart, or the shopper asked to close it)."""
    return {'type': 'ui.product_close', 'turnId': turn_id}


def debug(turn_id: int, transcript: str, questions: list[dict], counts: dict, timings: dict, notes: str = '') -> dict:
    return {'type': 'ui.debug', 'turnId': turn_id, 'transcript': transcript, 'questions': questions,
            'counts': counts, 'timings': timings, 'backendNotes': notes}


def debug_questions(answers: dict) -> list[dict]:
    """Top-3 options per answered question, for the overlay, with plain-language names."""
    from activity import INTENT_NAMES, QUESTION_NAMES   # activity imports questions, which is fine here
    out = []
    for qid, answer in answers.items():
        base = qid.replace(' (re-check)', '')
        label = QUESTION_NAMES.get(base, base) + (' (checking for a change of mind)' if base != qid else '')
        if answer.noul is not None:
            out.append({'id': qid, 'label': label, 'type': 'noul', 'top': [{'option': 'yes', 'p': round(answer.noul, 3)}]})
        else:
            names = INTENT_NAMES if base == 'intent' else {}
            out.append({'id': qid, 'label': label, 'type': 'choice',
                        'top': [{'option': o, 'name': names.get(o, 'not said' if o == 'not_mentioned' else o), 'p': round(p, 3)}
                                for o, p in answer.top(3)]})
    return out


def format_cents(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def scroll(turn_id: int, direction: str) -> dict:
    """Move the page: 'down', 'up' or 'top'."""
    return {'type': 'ui.scroll', 'turnId': turn_id, 'direction': direction}
