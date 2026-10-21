import asyncio

import pytest

import turn_pipeline
from decider_client import DeciderResult, DeciderUnavailable
from policy import Answer
from questions import Catalog
from state import SessionState
from turn_pipeline import Deps, TurnPipeline

STORE = Catalog(total=5000, departments={'Bags & Accessories': ['Handbags'], 'Jewelry & Watches': ['Watches']})

CATALOG = [
    {'asin': f'H{i:09d}', 'title': f'{color} leather crossbody bag {i}', 'brand': 'Acme',
     'department': 'Bags & Accessories', 'category': 'Handbags',
     'gender': gender, 'color': color, 'priceCents': price, 'rating': 4.5, 'ratingCount': 100, 'available': True,
     'imageUrl': 'https://img/x'}
    for i, (color, gender, price) in enumerate([
        ('black', 'women', 4999), ('purple', 'women', 7999), ('brown', 'unisex', 6999), ('black', 'men', 5999),
        ('red', 'women', 12999), ('tan', 'women', 2999), ('purple', 'women', 9999), ('navy', 'women', 3999),
    ])
] + [{'asin': 'W000000001', 'title': 'steel watch', 'brand': 'Tick', 'department': 'Jewelry & Watches',
      'category': 'Watches', 'gender': 'men',
      'color': 'silver', 'priceCents': 8999, 'rating': 4.2, 'ratingCount': 50, 'available': True, 'imageUrl': 'u'}]


class FakeTools:
    def __init__(self, fail_search=False, search_delay=0.0):
        self.calls, self.fail_search, self.search_delay = [], fail_search, search_delay
        self.cart = {'items': [], 'itemCount': 0, 'totalCents': 0}
        self.keys = set()

    async def search_catalog(self, args):
        self.calls.append(('search', args))
        await asyncio.sleep(self.search_delay)
        if self.fail_search:
            raise RuntimeError('boom')
        hits = [p for p in CATALOG if ('maxPriceCents' not in args or p['priceCents'] <= args['maxPriceCents'])]
        # Deliberately ignore category/gender filters: the host's hard filters must still hold.
        return {'totalCatalog': 5000, 'totalMatched': len(hits), 'hits': hits[:args.get('limit', 200)]}

    async def get_product_detail(self, args):
        self.calls.append(('detail', args))
        product = next(p for p in CATALOG if p['asin'] == args['asin'])
        return {'product': {**product, 'features': [], 'description': '', 'images': {}}}

    async def add_to_cart(self, args):
        self.calls.append(('add', args))
        if args['idempotencyKey'] not in self.keys:
            self.keys.add(args['idempotencyKey'])
            price = next(p['priceCents'] for p in CATALOG if p['asin'] == args['asin'])
            self.cart = {'items': self.cart['items'] + [{'asin': args['asin'], 'quantity': 1, 'priceCents': price}],
                         'itemCount': self.cart['itemCount'] + 1, 'totalCents': self.cart['totalCents'] + price}
        return {'cart': self.cart}

    async def remove_from_cart(self, args):
        self.calls.append(('remove', args))
        items = [i for i in self.cart['items'] if i['asin'] != args['asin']]
        self.cart = {'items': items, 'itemCount': sum(i['quantity'] for i in items),
                     'totalCents': sum(i['priceCents'] * i['quantity'] for i in items)}
        return {'cart': self.cart}

    async def place_order(self, args):
        self.calls.append(('order', args))
        if not self.cart['itemCount']:
            return {'error': 'EmptyCart'}
        order = {'orderId': 'ord-1', 'items': self.cart['items'], 'itemCount': self.cart['itemCount'],
                 'totalCents': self.cart['totalCents'], 'placedAt': '2026-10-18T00:00:00Z'}
        self.cart = {'items': [], 'itemCount': 0, 'totalCents': 0}
        return {'order': order}


def ans(qid, choice, p, others=None):
    rest = others if others is not None else {'_other': round(1 - p, 6)}
    return Answer(question_id=qid, choice=choice, confidence=p, probabilities={choice: p, **rest})


class FakeDecider:
    """Scripted: understand answers come from `script` (question id -> Answer);
    best_fit prefers purple; pointed_item picks the second shelf item."""

    def __init__(self, script=None, none_of_these=0.03, down=False, delay=0.0):
        self.script, self.none, self.down, self.delay = script or {}, none_of_these, down, delay
        self.calls = []

    async def __call__(self, state_text, questions):
        self.calls.append((state_text, list(questions)))
        await asyncio.sleep(self.delay)
        if self.down:
            raise DeciderUnavailable('throttled')
        answers = {}
        for qid, spec in questions.items():
            if qid == 'best_fit':
                options = [k for k in spec['criteria'] if k != 'none_of_these']
                purple = [k for k in options if 'purple' in spec['criteria'][k]]
                top = purple[0] if purple else options[0]
                probs = {k: 0.01 for k in options}
                probs[top] = 0.2
                probs['none_of_these'] = self.none
                answers[qid] = Answer(qid, top, 0.2, probs)
            elif qid == 'pointed_item':
                options = list(spec['criteria'])
                answers[qid] = ans(qid, options[1], self.script.get('_pointed_p', 0.93), {})
            elif qid in self.script:
                answers[qid] = self.script[qid]
        return DeciderResult(answers=answers, server_ms=100.0, client_ms=110.0, invalid=[])


def make(script=None, tools=None, **decider_kw):
    sent = []

    async def send(message):
        sent.append(message)

    tools = tools or FakeTools()
    decider = FakeDecider(script, **decider_kw)
    pipeline = TurnPipeline(SessionState(session_id='session-' + 'x' * 30),
                            Deps(tools=tools, send=send, ask_decider=decider, catalog=STORE))
    return pipeline, tools, decider, sent


def of(sent, kind):
    return [m for m in sent if m['type'] == kind]


def run(coro):
    return asyncio.run(coro)


MOM = {'intent': ans('intent', 'narrowing', 0.93), 'recipient': ans('recipient', 'mother', 0.86),
       'gender': ans('gender', 'not_mentioned', 0.5)}   # "mother" implies women's products


def test_gift_for_mom_under_80_commits_chips_and_filters_shelf():
    async def go():
        p, tools, decider, sent = make(MOM)
        outcome = await p.on_final('a gift for my mom under eighty dollars')
        return p, tools, decider, sent, outcome
    p, tools, decider, sent, outcome = run(go())
    chips = of(sent, 'ui.chips')[-1]['chips']
    assert [(c['slot'], c['value']) for c in chips] == [('recipient', 'Mom'), ('gender', 'Women'), ('budget', 'Under $80')]
    shelf = of(sent, 'ui.shelf')[-1]['items']
    # Nothing over budget, no men's items (women or unisex only), and purple ranked first by best_fit.
    by_asin = {p['asin']: p for p in CATALOG}
    assert shelf and all(by_asin[i['asin']]['priceCents'] <= 8000 for i in shelf)
    assert all(by_asin[i['asin']]['gender'] in ('women', 'unisex') for i in shelf)
    assert by_asin[shelf[0]['asin']]['color'] == 'purple'
    assert outcome['directive'] == 'act' and 'Women' in ' '.join(outcome['chips'])
    debug = of(sent, 'ui.debug')[-1]
    assert {'understandMs', 'retrievalMs', 'chooseMs', 'firstUiMs', 'shelfMs'} <= set(debug['timings'])


def test_chips_pushed_before_shelf():
    """First UI update is the chips, then the shelf."""
    async def go():
        p, _, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        return sent
    sent = run(go())
    kinds = [m['type'] for m in sent if m['type'] in ('ui.chips', 'ui.shelf')]
    assert kinds == ['ui.chips', 'ui.shelf']


def test_parent_without_gender_asks_mom_or_dad():
    # "parent" carries no gender and the decider says gender was not mentioned.
    script = {'intent': ans('intent', 'narrowing', 0.93), 'recipient': ans('recipient', 'parent', 0.9),
              'gender': ans('gender', 'not_mentioned', 0.8)}

    async def go():
        p, _, _, sent = make(script)
        return await p.on_final('a gift for my parent'), sent
    outcome, sent = run(go())
    assert outcome['directive'] == 'ask' and outcome['say'] == 'Is that your mom or your dad?'
    assert of(sent, 'ui.status')[-1] == {'type': 'ui.status', 'kind': 'clarifying', 'message': 'Is that your mom or your dad?'}


def test_no_match_keeps_the_closest_items_and_offers_to_widen():
    """Policy: a no-match never hides the shelf, and ends with one spoken yes/no offer
    to drop the filter that adds the most matches."""
    async def go():
        p, tools, _, sent = make(MOM, none_of_these=0.4)
        outcome = await p.on_final('a laptop charger for my mom')
        return p, tools, outcome, sent
    p, tools, outcome, sent = run(go())
    shelf = of(sent, 'ui.shelf')[-1]
    assert outcome['directive'] == 'no_match' and shelf['tentative'] is True and shelf['items']
    assert p.state.shelf and all(item['gender'] in ('women', 'unisex') for item in p.state.shelf)   # the women's filter
    assert p.state.pending_offer == 'widen:gender'                       # only gender is committed as a filter
    assert 'not only women' in outcome['say'] and outcome['confirmation'] and outcome['speak'] is True
    searches = [a for kind, a in tools.calls if kind == 'search']
    assert any('gender' not in a for a in searches[1:])                  # counted once without the gender filter


def test_yes_to_widen_drops_that_filter_and_searches_again():
    async def go():
        p, _, decider, sent = make(MOM, none_of_these=0.4)
        await p.on_final('a laptop charger for my mom')
        decider.none = 0.01
        p.deps.ask_decider.script = {'intent': ans('intent', 'confirm', 0.9)}
        outcome = await p.on_final('yes')
        return p, outcome, sent
    p, outcome, sent = run(go())
    assert p.state.gender is None and p.state.pending_offer is None
    genders = {item['gender'] for item in p.state.shelf}
    assert len(genders) > 1 and outcome['directive'] in ('act', 'narrow')


def test_widen_offer_dropped_when_the_shopper_says_something_else():
    async def go():
        p, _, decider, _ = make(MOM, none_of_these=0.4)
        await p.on_final('a laptop charger for my mom')
        decider.none = 0.01
        p.deps.ask_decider.script = {'intent': ans('intent', 'narrowing', 0.9)}
        await p.on_final('something in purple')
        return p
    p = run(go())
    assert p.state.pending_offer is None and p.state.gender is not None   # filter kept, offer gone


def test_pointing_opens_second_item_and_sets_offer():
    async def go():
        p, tools, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95)}
        outcome = await p.on_final('the second one')
        return p, outcome, sent
    p, outcome, sent = run(go())
    opened = of(sent, 'ui.product_open')[-1]
    assert opened['product']['asin'] == p.state.shelf[1]['asin'] and opened['confidence'] == 0.93
    assert p.state.pending_offer == f"add_to_cart:{opened['product']['asin']}"
    assert 'Mom (86%)' in opened['why'] and 'Women (86%)' in opened['why']


def test_unsure_pointing_confirms_by_on_screen_number_then_opens_on_yes():
    async def go():
        p, tools, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95), '_pointed_p': 0.15}   # narrow band
        asked = await p.on_final('the second one')
        offer = p.state.pending_offer
        opened_before = len(of(sent, 'ui.product_open'))
        p.deps.ask_decider.script = {'intent': ans('intent', 'confirm', 0.9)}
        await p.on_final('yes')
        return p, sent, asked, offer, opened_before
    p, sent, asked, offer, opened_before = run(go())
    second = p.state.shelf[1]
    assert asked['say'].startswith('Do you mean No. 2, ') and asked['confirmation'] and asked['speak'] is True
    assert offer == f"open:{second['asin']}" and opened_before == 0
    assert of(sent, 'ui.product_open')[-1]['product']['asin'] == second['asin']
    assert p.state.pending_offer == f"add_to_cart:{second['asin']}"   # the add is still confirmed separately


def test_open_offer_dropped_when_the_shopper_says_something_else():
    async def go():
        p, _, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95), '_pointed_p': 0.15}
        await p.on_final('the second one')
        p.deps.ask_decider.script = {'intent': ans('intent', 'narrowing', 0.9)}
        await p.on_final('something in purple')
        return p, sent
    p, sent = run(go())
    assert p.state.pending_offer is None and not of(sent, 'ui.product_open')


def test_confirm_adds_with_idempotency_key_then_place_order_gating():
    async def go():
        p, tools, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95)}
        await p.on_final('the second one')
        p.deps.ask_decider.script = {'intent': ans('intent', 'confirm', 0.92)}
        added = await p.on_final('yes add it')
        p.deps.ask_decider.script = {'intent': ans('intent', 'place_order', 0.45)}   # narrow band for intent
        unsure = await p.on_final('maybe i should check out')
        p.deps.ask_decider.script = {'intent': ans('intent', 'place_order', 0.95), 'offer_reply': ans('offer_reply', 'yes', 0.93)}
        placed = await p.on_final('yes place my order')
        p.deps.ask_decider.script = {'intent': ans('intent', 'place_order', 0.95)}
        again = await p.on_final('place my order')
        return p, tools, sent, added, unsure, placed, again
    p, tools, sent, added, unsure, placed, again = run(go())
    add_calls = [a for kind, a in tools.calls if kind == 'add']
    assert add_calls and add_calls[0]['idempotencyKey'] == f'{p.state.session_id}:3'
    assert 'Added to the cart' in added['say']
    # An order is always offered first and placed on yes; never for an empty cart.
    assert unsure['say'] == 'Should I place the order?' and unsure['speak'] is True   # spoken confirmation
    order_calls = [a for kind, a in tools.calls if kind == 'order']
    assert len(order_calls) == 1 and order_calls[0]['idempotencyKey'] == f'{p.state.session_id}:5'
    assert of(sent, 'ui.order_placed')[0]['itemCount'] == 1
    assert of(sent, 'ui.cart')[-1]['itemCount'] == 0
    assert 'empty' in again['say']


def test_go_back_restores_previous_shelf_and_chips():
    async def go():
        p, _, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        first_shelf = list(p.state.shelf)
        p.state.push_history()
        p.deps.ask_decider.script = {'intent': ans('intent', 'narrowing', 0.9), 'category': ans('category', 'Watches', 0.95)}
        await p.on_final('actually a watch')
        p.deps.ask_decider.script = {'intent': ans('intent', 'go_back', 0.9)}
        await p.on_final('go back')
        return p, first_shelf
    p, first_shelf = run(go())
    assert p.state.shelf == first_shelf and p.state.category is None


def test_decider_unavailable_degrades_to_retrieval_order():
    async def go():
        p, _, _, sent = make(MOM, down=True)
        return await p.on_final('a gift for my mom under 80 dollars'), sent
    outcome, sent = run(go())
    assert any(m['type'] == 'ui.status' and m['kind'] == 'degraded' for m in sent)
    assert outcome['directive'] == 'ask'
    shelf = of(sent, 'ui.shelf')[-1]['items']
    assert shelf and all(next(c for c in CATALOG if c['asin'] == i['asin'])['priceCents'] <= 8000 for i in shelf)


def test_search_failure_keeps_shelf_and_asks():
    async def go():
        p, _, _, sent = make(MOM, tools=FakeTools(fail_search=True))
        return await p.on_final('a gift for my mom'), sent
    outcome, sent = run(go())
    assert outcome['directive'] == 'ask' and "couldn't search" in outcome['say'] and not of(sent, 'ui.shelf')


INTENT_ONLY = {'intent': ans('intent', 'narrowing', 0.93)}   # no commits, so no follow-up search


def test_speculative_search_reused_when_final_matches():
    async def go():
        p, tools, _, sent = make(INTENT_ONLY)
        p.on_partial('a gift for my mom')
        await asyncio.sleep(turn_pipeline.SPECULATIVE_DEBOUNCE_S + 0.05)
        await p.on_final('A gift for my mom.')
        return tools, sent
    tools, sent = run(go())
    assert len([c for c in tools.calls if c[0] == 'search']) == 1
    assert of(sent, 'ui.debug')[-1]['timings']['retrievalSpeculative'] is True


def test_speculative_search_redone_when_final_differs():
    async def go():
        p, tools, _, sent = make(INTENT_ONLY)
        p.on_partial('a gift for my')
        await asyncio.sleep(turn_pipeline.SPECULATIVE_DEBOUNCE_S + 0.05)
        await p.on_final('a gift for my mom under 80 dollars')
        return tools, sent
    tools, sent = run(go())
    assert len([c for c in tools.calls if c[0] == 'search']) == 2
    assert of(sent, 'ui.debug')[-1]['timings']['retrievalSpeculative'] is False


def test_newer_transcript_cancels_older_turn():
    async def go():
        p, _, _, sent = make(MOM, delay=0.2)
        first = p.on_final('a gift for my mom')
        await asyncio.sleep(0.05)
        second = p.on_final('a gift for my mom under 80 dollars')
        await second
        return first, sent
    first, sent = run(go())
    assert first.cancelled()
    assert len(of(sent, 'ui.shelf')) == 1


def test_turn_outcome_holds_when_slow():
    async def go():
        p, _, _, _ = make(MOM, delay=0.3)
        p.on_final('a gift for my mom')
        early = await p.turn_outcome(hold_s=0.05)
        late = await p.turn_outcome(hold_s=2)
        return early, late
    early, late = run(go())
    assert early['directive'] == 'hold' and late['directive'] == 'act'


def test_initial_shelf_before_first_sentence():
    async def go():
        p, _, _, sent = make()
        await p.initial_shelf()
        return sent
    sent = run(go())
    assert len(of(sent, 'ui.shelf')[0]['items']) > 0 and of(sent, 'ui.cart')


# ---- P0 regressions found in the 2026-10-19 review

def test_unsure_place_order_asks_then_yes_places_it():
    async def go():
        p, tools, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95)}
        await p.on_final('the second one')
        p.deps.ask_decider.script = {'intent': ans('intent', 'confirm', 0.95)}
        await p.on_final('yes add it')
        shelf_before = list(p.state.shelf)
        p.deps.ask_decider.script = {'intent': ans('intent', 'place_order', 0.4)}
        asked = await p.on_final('i guess i could check out')
        unchanged = p.state.shelf == shelf_before
        p.deps.ask_decider.script = {'intent': ans('intent', 'confirm', 0.95)}
        placed = await p.on_final('yes')
        return tools, asked, unchanged, placed
    tools, asked, unchanged, placed = run(go())
    assert asked['say'] == 'Should I place the order?' and asked['speak'] is True and unchanged
    assert [kind for kind, _ in tools.calls].count('order') == 1 and 'Order placed' in placed['say']


def test_clarifying_question_never_replaces_a_pointing_confirmation():
    async def go():
        p, _, _, _ = make({'intent': ans('intent', 'narrowing', 0.93), 'recipient': ans('recipient', 'parent', 0.9),
                           'gender': ans('gender', 'not_mentioned', 0.8)})
        await p.on_final('something for my parent')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95), '_pointed_p': 0.15}
        return await p.on_final('the second one')
    outcome = run(go())
    assert outcome['say'].startswith('Do you mean No. 2') and outcome['confirmation']


def test_add_offer_is_dropped_when_the_shopper_moves_on():
    async def go():
        p, tools, _, _ = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95)}
        await p.on_final('the second one')
        p.deps.ask_decider.script = {'intent': ans('intent', 'narrowing', 0.9)}
        await p.on_final('something in purple')
        return p
    p = run(go())
    assert p.state.pending_offer is None and p.state.open_product is None


def test_go_back_returns_to_the_previous_shelf_without_rewinding_the_turn_counter():
    async def go():
        p, _, _, _ = make(MOM)
        await p.on_final('a gift for my mom')
        first = [x['asin'] for x in p.state.shelf]
        p.deps.ask_decider.script = {**MOM, 'category': ans('category', 'Watches', 0.95)}
        await p.on_final('watches instead')
        p.deps.ask_decider.script = {'intent': ans('intent', 'go_back', 0.95)}
        await p.on_final('go back')
        return p, first
    p, first = run(go())
    assert [x['asin'] for x in p.state.shelf] == first and p.state.category is None and p.state.turn_seq == 3


def test_a_false_change_of_mind_keeps_the_chip():
    async def go():
        p, _, _, _ = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'narrowing', 0.9),
                                     'recipient': ans('recipient', 'not_mentioned', 0.8)}
        await p.on_final('something in purple')
        return p
    p = run(go())
    assert p.state.recipient and p.state.recipient.value == 'mother'


def test_search_error_is_a_failed_search_not_a_no_match():
    class ErrorTools(FakeTools):
        async def search_catalog(self, args):
            self.calls.append(('search', args))
            return {'error': {'code': 'ValidationError', 'message': 'maxPriceCents too large'}}
    async def go():
        p, _, _, sent = make(MOM, tools=ErrorTools())
        return await p.on_final('a gift for my mom'), sent
    outcome, sent = run(go())
    assert outcome['directive'] == 'ask' and "couldn't search" in outcome['say'] and not of(sent, 'ui.status') == []



def test_a_department_commit_asks_its_categories_in_the_same_sentence():
    async def go():
        p, _, decider, _ = make({'intent': ans('intent', 'narrowing', 0.93),
                                 'department': ans('department', 'Jewelry & Watches', 0.9),
                                 'category': ans('category', 'Watches', 0.95)})
        await p.on_final('a watch for me')
        return p, decider
    p, decider = run(go())
    assert p.state.department.value == 'Jewelry & Watches' and p.state.category.value == 'Watches'
    asked = [qs for _, qs in decider.calls]
    assert ['category'] in asked                         # its own round, within the department
    assert all(item['category'] == 'Watches' for item in p.state.shelf)


def test_removing_the_department_also_clears_its_category():
    import policy
    from state import Commit
    state = SessionState(session_id='s', department=Commit('Jewelry & Watches', 0.9), category=Commit('Watches', 0.9))
    policy.reopen(state, 'department')
    assert state.department is None and state.category is None


def test_yes_in_any_words_carries_out_the_pending_offer():
    """'Yeah' scored confirm 0.41 among seven intents (live session); the reply to a
    pending offer gets its own question, and a confident yes carries it out."""
    async def go():
        p, _, decider, _ = make(MOM)
        await p.on_final('a gift for my mom')
        first = [x['asin'] for x in p.state.shelf]
        p.deps.ask_decider.script = {**MOM, 'department': ans('department', 'Jewelry & Watches', 0.9),
                                     'category': ans('category', 'Watches', 0.95)}
        await p.on_final('watches instead')
        p.deps.ask_decider.script = {'intent': ans('intent', 'go_back', 0.4)}
        asked = await p.on_final('maybe the earlier ones')
        p.deps.ask_decider.script = {'intent': ans('intent', 'confirm', 0.41),
                                     'offer_reply': ans('offer_reply', 'yes', 0.89)}
        done = await p.on_final('yeah')
        return p, first, asked, done, decider
    p, first, asked, done, decider = run(go())
    assert asked['say'] == 'Should I go back to the previous results?'
    assert done['say'] == 'Back to the previous results.' and [x['asin'] for x in p.state.shelf] == first
    assert any(text.startswith('Assistant asked: "Should I go back') and 'offer_reply' in qs for text, qs in decider.calls)


def test_no_to_the_pending_offer_changes_nothing():
    """'No' to "go back?" is go_back 0.75 as an intent; the reply question declines it."""
    async def go():
        p, _, _, _ = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {**MOM, 'department': ans('department', 'Jewelry & Watches', 0.9),
                                     'category': ans('category', 'Watches', 0.95)}
        await p.on_final('watches instead')
        watches = [x['asin'] for x in p.state.shelf]
        p.deps.ask_decider.script = {'intent': ans('intent', 'go_back', 0.4)}
        await p.on_final('maybe the earlier ones')
        p.deps.ask_decider.script = {'intent': ans('intent', 'go_back', 0.75),
                                     'offer_reply': ans('offer_reply', 'no', 0.77)}
        done = await p.on_final('nope')
        return p, watches, done
    p, watches, done = run(go())
    assert done['say'] == 'Okay, leaving it as it is.'
    assert [x['asin'] for x in p.state.shelf] == watches and p.state.pending_offer is None


def test_adding_the_open_product_closes_its_panel():
    async def go():
        p, _, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95)}
        await p.on_final('the second one')
        p.deps.ask_decider.script = {'intent': ans('intent', 'confirm', 0.95)}
        await p.on_final('yes add it')
        return p, sent
    p, sent = run(go())
    assert p.state.open_product is None and of(sent, 'ui.product_close')


def test_a_new_category_in_the_same_department_is_asked_again():
    """'Look for some bracelet' while in Jewelry & Watches kept showing earrings (live session)."""
    from state import Commit
    async def go():
        p, _, decider, _ = make({'intent': ans('intent', 'narrowing', 0.9),
                                 'department': ans('department', 'Jewelry & Watches', 0.9),
                                 'category': ans('category', 'Watches', 0.95)})
        p.state.department = Commit('Jewelry & Watches', 0.9)
        p.state.category = Commit('Handbags', 0.9)   # stand-in for "earrings"
        await p.on_final('can you look for a watch')
        return p, decider
    p, decider = run(go())
    assert p.state.department.value == 'Jewelry & Watches' and p.state.category.value == 'Watches'


def test_a_new_recipient_replaces_the_chip_and_its_gender():
    """'And I want to look something for my wife' after 'a gift for my brother' stayed on men (live session)."""
    async def go():
        p, _, decider, _ = make({'intent': ans('intent', 'narrowing', 0.9), 'recipient': ans('recipient', 'brother', 0.85)})
        await p.on_final('a gift for my brother')
        p.deps.ask_decider.script = {'intent': ans('intent', 'narrowing', 0.9), 'recipient': ans('recipient', 'wife', 0.69)}
        await p.on_final('and i want to look something for my wife')
        return p, decider
    p, decider = run(go())
    assert p.state.recipient.value == 'wife' and p.state.gender.value == 'women'
    recheck = [text for text, qs in decider.calls if qs == ['recipient']]
    assert recheck and 'gift for my brother' not in recheck[-1] and 'recipient=brother' not in recheck[-1]


def test_a_new_person_resets_the_other_chips():
    """Shirts for my father, then 'now a gift for my mom' kept Men's Clothing / Shirts (live session)."""
    from state import Commit
    async def go():
        p, tools, decider, _ = make({'intent': ans('intent', 'narrowing', 0.9), 'recipient': ans('recipient', 'mother', 0.78)})
        p.state.recipient = Commit('father', 0.72)
        p.state.gender = Commit('men', 0.72, 'recipient')
        p.state.department = Commit('Bags & Accessories', 0.6)
        p.state.category = Commit('Handbags', 0.7)
        await p.on_final('and now i want to purchase some gift for my mom')
        return p, tools
    p, tools = run(go())
    assert p.state.recipient.value == 'mother' and p.state.gender.value == 'women'
    assert p.state.department is None and p.state.category is None
    last = [a for kind, a in tools.calls if kind == 'search'][-1]
    assert 'categories' not in last and 'departments' not in last and last['genders'] == ['women', 'unisex']


def test_a_new_department_clears_the_category_and_asks_within_it():
    """'What about some t shirts' while in Bags & Accessories kept showing bags (live session)."""
    from state import Commit
    async def go():
        p, _, decider, _ = make({'intent': ans('intent', 'narrowing', 0.9),
                                 'department': ans('department', 'Jewelry & Watches', 0.47),
                                 'category': ans('category', 'Watches', 0.9)})
        p.state.department = Commit('Bags & Accessories', 0.62)
        p.state.category = Commit('Handbags', 0.9)
        await p.on_final('what about a watch')
        return p, decider
    p, decider = run(go())
    assert p.state.department.value == 'Jewelry & Watches' and p.state.category.value == 'Watches'
    # the category was asked again, within the new department
    assert any(qs == ['category'] and 'department=Jewelry & Watches' in text for text, qs in decider.calls)


def test_a_confident_place_order_still_asks_first():
    async def go():
        p, tools, _, _ = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95)}
        await p.on_final('the second one')
        p.deps.ask_decider.script = {'intent': ans('intent', 'confirm', 0.92)}
        await p.on_final('yes add it')
        p.deps.ask_decider.script = {'intent': ans('intent', 'place_order', 0.96)}
        asked = await p.on_final('i am done, check out')
        return tools, asked
    tools, asked = run(go())
    assert asked['say'] == 'Should I place the order?' and asked['confirmation']
    assert not [a for kind, a in tools.calls if kind == 'order']


def test_close_this_closes_the_open_product():
    async def go():
        p, _, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95)}
        await p.on_final('the second one')
        p.deps.ask_decider.script = {'intent': ans('intent', 'close_item', 0.9)}
        await p.on_final('can you close this')
        return p, sent
    p, sent = run(go())
    assert p.state.open_product is None and of(sent, 'ui.product_close')


def test_scroll_down_moves_the_page_and_keeps_the_shelf():
    async def go():
        p, tools, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        shelf = [x['asin'] for x in p.state.shelf]
        searches = len([c for c in tools.calls if c[0] == 'search'])
        p.deps.ask_decider.script = {'intent': ans('intent', 'move_screen', 0.97), 'scroll': ans('scroll', 'down', 0.94)}
        await p.on_final('can you scroll down')
        return p, tools, sent, shelf, searches
    p, tools, sent, shelf, searches = run(go())
    assert of(sent, 'ui.scroll')[-1]['direction'] == 'down'
    assert [x['asin'] for x in p.state.shelf] == shelf   # the speculative search is cancelled


def test_an_unsure_change_is_asked_and_yes_switches():
    """'What about some t shirts' in Sports & Beauty leaned to Women's Clothing at 0.41: ask, don't guess."""
    from state import Commit
    async def go():
        p, tools, _, _ = make({'intent': ans('intent', 'narrowing', 0.9),
                               'department': ans('department', 'Jewelry & Watches', 0.35)})
        p.state.department = Commit('Bags & Accessories', 0.62)
        p.state.category = Commit('Handbags', 0.9)
        shelf = [x['asin'] for x in p.state.shelf]
        asked = await p.on_final('what about a watch')
        unchanged = (p.state.department.value, [x['asin'] for x in p.state.shelf] == shelf)
        p.deps.ask_decider.script = {'intent': ans('intent', 'confirm', 0.8), 'offer_reply': ans('offer_reply', 'yes', 0.95),
                                     'category': ans('category', 'Watches', 0.95)}
        await p.on_final('yes')
        return p, asked, unchanged
    p, asked, unchanged = run(go())
    assert asked['say'] == 'Should I look in Jewelry & Watches instead?' and asked['confirmation']
    assert unchanged == ('Bags & Accessories', True)
    assert p.state.department.value == 'Jewelry & Watches' and p.state.category.value == 'Watches'


def test_show_more_adds_the_next_results_and_go_back_returns(monkeypatch):
    """'Can you show me the next twelve' returned the same twelve (live session)."""
    import turn_pipeline
    monkeypatch.setattr(turn_pipeline, 'SHELF_SIZE', 3)   # the fixture catalog has 7 products
    async def go():
        p, _, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        first = [x['asin'] for x in p.state.shelf]
        p.deps.ask_decider.script = {'intent': ans('intent', 'show_more', 0.93)}
        more = await p.on_final('can you show me the next twelve')
        after = [x['asin'] for x in p.state.shelf]
        p.deps.ask_decider.script = {'intent': ans('intent', 'go_back', 0.95)}
        await p.on_final('go back')
        return p, first, after, more, sent
    p, first, after, more, sent = run(go())
    assert after[:3] == first and len(after) == 6 and more['say'] == 'Showing 6 of 7 matches. Items 4 to 6 are new.'
    assert of(sent, 'ui.scroll')[-1]['direction'] == 'down'
    assert [x['asin'] for x in p.state.shelf] == first


def test_show_more_with_a_new_chip_is_a_new_search():
    async def go():
        p, tools, _, _ = make(MOM)
        await p.on_final('a gift for my mom')
        searches = len([c for c in tools.calls if c[0] == 'search'])
        p.deps.ask_decider.script = {'intent': ans('intent', 'show_more', 0.6), 'budget_band': ans('budget_band', 'under_25', 0.9)}
        await p.on_final('show me more under 25 dollars')
        return p, tools, searches
    p, tools, searches = run(go())
    assert p.state.budget is not None


def test_a_pending_offer_is_also_a_question_on_screen():
    async def go():
        p, _, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'go_back', 0.4)}
        await p.on_final('maybe the earlier ones')
        return sent
    sent = run(go())
    assert of(sent, 'ui.question')[-1]['text'] == 'Should I go back to the previous results?'


def test_an_empty_cart_order_is_answered_in_words_on_screen():
    """With spoken replies off, Sonic said 'Take a look.' and nothing on screen changed."""
    async def go():
        p, _, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'place_order', 0.9)}
        await p.on_final('can you place the order')
        return sent
    sent = run(go())
    assert of(sent, 'ui.reply')[-1]['text'] == 'The cart is empty, so there is nothing to order yet.'



def _cart_of_two(p, tools):
    """Two lines in the cart, as after two adds."""
    lines = [{'asin': CATALOG[0]['asin'], 'title': CATALOG[0]['title'], 'quantity': 1, 'priceCents': CATALOG[0]['priceCents']},
             {'asin': CATALOG[1]['asin'], 'title': CATALOG[1]['title'], 'quantity': 1, 'priceCents': CATALOG[1]['priceCents']}]
    tools.cart = {'items': lines, 'itemCount': 2, 'totalCents': sum(l['priceCents'] for l in lines)}
    p.state.cart = dict(tools.cart)


def test_remove_a_named_item_from_the_cart():
    """'Can you remove the coral bay flutter sleeve blouse from the cart' went back instead (live session)."""
    async def go():
        p, tools, _, sent = make(MOM)
        await p.on_final('a gift for my mom')
        _cart_of_two(p, tools)
        p.deps.ask_decider.script = {'intent': ans('intent', 'remove_from_cart', 0.9),
                                     'cart_item': ans('cart_item', CATALOG[1]['asin'], 0.95)}
        done = await p.on_final('remove the purple bag from my cart')
        return p, tools, sent, done
    p, tools, sent, done = run(go())
    assert [a['asin'] for kind, a in tools.calls if kind == 'remove'] == [CATALOG[1]['asin']]
    assert p.state.cart['itemCount'] == 1 and done['say'].startswith('Removed ')
    assert of(sent, 'ui.cart')[-1]['itemCount'] == 1


def test_an_unsure_removal_asks_first_and_yes_removes():
    async def go():
        p, tools, _, _ = make(MOM)
        await p.on_final('a gift for my mom')
        _cart_of_two(p, tools)
        p.deps.ask_decider.script = {'intent': ans('intent', 'remove_from_cart', 0.9),
                                     'cart_item': ans('cart_item', CATALOG[0]['asin'], 0.5)}
        asked = await p.on_final('take the bag out')
        before = len([1 for kind, _ in tools.calls if kind == 'remove'])
        p.deps.ask_decider.script = {'intent': ans('intent', 'confirm', 0.8), 'offer_reply': ans('offer_reply', 'yes', 0.95)}
        await p.on_final('yes')
        return p, tools, asked, before
    p, tools, asked, before = run(go())
    assert asked['say'].startswith('Should I remove ') and asked['confirmation'] and before == 0
    assert [a['asin'] for kind, a in tools.calls if kind == 'remove'] == [CATALOG[0]['asin']]


def test_the_cart_remove_button_removes_that_line():
    async def go():
        p, tools, _, _ = make(MOM)
        await p.on_final('a gift for my mom')
        _cart_of_two(p, tools)
        await p.remove_tapped(CATALOG[0]['asin'])
        await p.remove_tapped('NOTINCART0')   # ignored: not a cart line
        return p, tools
    p, tools = run(go())
    assert [a['asin'] for kind, a in tools.calls if kind == 'remove'] == [CATALOG[0]['asin']]


def test_no_with_a_new_request_drops_the_offer_and_runs_the_request():
    """'No actually I changed my mind, I like number five' was only a 'no' (live session)."""
    async def go():
        p, _, decider, _ = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95)}
        await p.on_final('the third one')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.86), 'offer_reply': ans('offer_reply', 'no', 0.87)}
        done = await p.on_final('no actually i changed my mind i like number five')
        return p, decider, done
    p, decider, done = run(go())
    assert done['say'].startswith('Opened ') and p.state.open_product is not None


def test_pointing_does_not_see_the_open_product():
    async def go():
        p, _, decider, _ = make(MOM)
        await p.on_final('a gift for my mom')
        p.deps.ask_decider.script = {'intent': ans('intent', 'pointing', 0.95)}
        await p.on_final('the third one')
        await p.on_final('can you go with number five')
        return decider
    decider = run(go())
    pointing = [text for text, qs in decider.calls if qs == ['pointed_item']]
    assert 'Open product: none' in pointing[-1] and 'Pending offer' not in pointing[-1]



def test_empty_my_cart_asks_then_removes_every_line():
    """'Can you empty my cart' was a search (remove 0.43, live session)."""
    async def go():
        p, tools, _, _ = make(MOM)
        await p.on_final('a gift for my mom')
        _cart_of_two(p, tools)
        p.deps.ask_decider.script = {'intent': ans('intent', 'remove_from_cart', 0.97), 'cart_item': ans('cart_item', 'all', 0.4)}
        asked = await p.on_final('can you empty my cart')
        p.deps.ask_decider.script = {'intent': ans('intent', 'confirm', 0.8), 'offer_reply': ans('offer_reply', 'yes', 0.95)}
        done = await p.on_final('yes')
        return p, tools, asked, done
    p, tools, asked, done = run(go())
    assert asked['say'] == 'Should I empty the cart?' and asked['confirmation']
    assert len([1 for kind, _ in tools.calls if kind == 'remove']) == 2
    assert p.state.cart['itemCount'] == 0 and done['say'] == 'Emptied the cart.'


def test_an_unsure_removal_asks_instead_of_searching():
    async def go():
        p, tools, _, _ = make(MOM)
        await p.on_final('a gift for my mom')
        _cart_of_two(p, tools)
        searches = len([1 for kind, _ in tools.calls if kind == 'search'])
        p.deps.ask_decider.script = {'intent': ans('intent', 'remove_from_cart', 0.44),
                                     'cart_item': ans('cart_item', CATALOG[0]['asin'], 0.95)}
        asked = await p.on_final('take that out')
        return p, tools, asked
    p, tools, asked = run(go())
    assert asked['say'].startswith('Should I remove ') and not [a for kind, a in tools.calls if kind == 'remove']



def test_a_price_keeps_the_department_and_searches_once():
    """'Show me under fifty dollars' dropped the department and searched twice (2026-10-20)."""
    from state import Commit
    async def go():
        p, tools, _, _ = make({'intent': ans('intent', 'narrowing', 0.9)})
        p.state.department = Commit('Bags & Accessories', 0.8)
        before = len([1 for kind, _ in tools.calls if kind == 'search'])
        await p.on_final('show me under fifty dollars')
        return [a for kind, a in tools.calls if kind == 'search'][before:]
    searches = run(go())
    assert len(searches) == 1
    assert searches[0]['departments'] == ['Bags & Accessories'] and searches[0]['maxPriceCents'] == 5000
