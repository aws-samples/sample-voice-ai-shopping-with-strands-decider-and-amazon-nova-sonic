import pytest

from filters import Budget
from questions import (Catalog, MAX_OPTIONS, NONE_OF_THESE, best_fit_question,
                       pointed_item_question, recheck_ids, recheck_text, select_understand, state_text,
                       understand_questions)
from state import Commit, SessionState


def product(i, **kw):
    return {'asin': f'B{i:09d}', 'title': f'Item {i}', 'brand': 'Acme', 'color': 'black', 'priceCents': 1000 + i, **kw}


@pytest.mark.parametrize('setup, parsed, expected', [
    ({}, None, ['intent', 'recipient', 'gender', 'department', 'budget_band']),        # nothing committed yet
    ({}, Budget(max_cents=8000), ['intent', 'recipient', 'gender', 'department']),    # parser found the amount
    ({'recipient': Commit('mother', 0.9)}, None, ['intent', 'gender', 'department', 'budget_band']),
    ({'recipient': Commit('mother', 0.9), 'department': Commit('Bags & Accessories', 0.9)}, None,
     ['intent', 'gender', 'category', 'budget_band']),               # category within the department
    ({'recipient': Commit('mother', 0.9), 'gender': Commit('women', 0.9), 'department': Commit('Bags & Accessories', 0.9),
      'category': Commit('Handbags', 0.9), 'budget_commit': Commit('Under $80', 1.0, 'stated')}, None, ['intent']),
    ({'open_product': product(1), 'recipient': Commit('mother', 0.9), 'gender': Commit('women', 0.9),
      'department': Commit('Bags & Accessories', 0.9), 'category': Commit('Handbags', 0.9)}, Budget(max_cents=8000),
     ['intent', 'liked_signal']),
])
def test_selection_rule(setup, parsed, expected):
    """Every open slot is asked each round, so a category is heard while the recipient
    is still open; intent first. Committed slots are re-checked separately."""
    state = SessionState(session_id='s', **setup)
    ids = select_understand(state, parsed)
    assert ids == expected and ids[0] == 'intent'


def test_committed_slots_are_rechecked_with_only_the_latest_sentence():
    state = SessionState(session_id='s', recipient=Commit('brother', 0.85), gender=Commit('men', 0.85, 'recipient'),
                         department=Commit('Sports & Beauty', 0.75), budget_commit=Commit('Under $50', 1.0, 'stated'),
                         transcripts=['a gift for my brother', 'he likes perfumes'])
    assert recheck_ids(state, None) == ['recipient', 'gender', 'department']   # a stated amount is parsed, not re-asked
    text = recheck_text(state, 'something for my wife')
    assert 'something for my wife' in text and 'a gift for my brother' not in text and 'recipient=brother' in text


def test_remembered_stays_out_of_the_understand_round():
    state = SessionState(session_id='s', remembered=['Prefers gifts for mother'])
    assert 'mother' not in state_text(state, 'a gift for my dad')
    assert 'Prefers gifts for mother' in state_text(state, 'a gift for my dad', remembered=True)


def test_pointing_offered_only_with_a_shelf():
    empty = understand_questions(SessionState(session_id='s'), ['intent'], Catalog())
    shown = understand_questions(SessionState(session_id='s', shelf=[product(1)]), ['intent'], Catalog())
    assert 'pointing' not in empty['intent']['criteria']
    assert 'pointing' in shown['intent']['criteria']


def test_best_fit_cap_and_none_option():
    """At most 49 candidates plus none_of_these."""
    q = best_fit_question([product(i) for i in range(49)])['best_fit']
    assert len(q['criteria']) == MAX_OPTIONS and NONE_OF_THESE in q['criteria']
    with pytest.raises(ValueError):
        best_fit_question([product(i) for i in range(50)])


def test_pointed_item_labels_use_positions():
    q = pointed_item_question([product(i) for i in range(8)], columns=4)['pointed_item']
    assert q['criteria']['B000000000'].startswith('Item 1, the first item: row 1, far left')
    assert q['criteria']['B000000003'].startswith('Item 4, the fourth item: row 1, far right')
    assert q['criteria']['B000000005'].startswith('Item 6, the sixth item: row 2, second from the left')
    assert q['criteria']['B000000007'].startswith('Item 8, the eighth item, the last item')
    assert 'none' in q['criteria']


def test_state_text():
    state = SessionState(session_id='s', recipient=Commit('parent', 0.86), budget=Budget(max_cents=10000),
                         budget_commit=Commit('Under $100', 1.0, 'stated'), remembered=['likes purple'],
                         shelf=[product(1)], transcripts=['a gift for my mom', 'something to wear'])
    text = state_text(state, 'something to wear')
    assert 'Latest shopper sentence: "something to wear"' in text
    assert 'Earlier: "a gift for my mom"' in text
    assert 'recipient=parent (0.86)' in text and 'budget under $100 (stated)' in text
    assert 'likes purple' not in text and 'On screen (4 columns): 1 items' in text


def test_state_roundtrip_and_history():
    state = SessionState(session_id='s', recipient=Commit('parent', 0.9), budget=Budget(max_cents=8000),
                         budget_commit=Commit('Under $80', 1.0, 'stated'), shelf=[product(1)],
                         cart={'items': [{'asin': 'X'}], 'itemCount': 1, 'totalCents': 500})
    assert SessionState.from_dict(state.to_dict()) == state
    state.push_history()
    state.category = Commit('Handbags', 0.9)
    state.shelf = [product(2)]
    state.cart = {'items': [], 'itemCount': 0, 'totalCents': 0}   # order placed meanwhile
    assert state.go_back()
    assert state.category is None and state.shelf == [product(1)]
    assert state.cart['itemCount'] == 0     # go back never changes the cart
    assert not state.go_back()


STORE = Catalog(total=9, departments={'Bags & Accessories': ['Handbags', 'Wallets'], 'Jewelry & Watches': ['Watches']},
                department_descriptions={'Bags & Accessories': 'Handbags, Wallets'},
                category_descriptions={'Handbags': 'e.g. tote bag, crossbody bag'})


def test_category_is_asked_only_within_a_department():
    from state import Commit
    assert 'category' not in select_understand(SessionState(session_id='s'), None)
    inside = SessionState(session_id='s', department=Commit('Bags & Accessories', 0.9))
    assert 'category' in select_understand(inside, None)
    spec = understand_questions(inside, ['category'], STORE)['category']['criteria']
    assert set(spec) == {'Handbags', 'Wallets', 'not_mentioned'} and spec['Handbags'] == 'e.g. tote bag, crossbody bag'


def test_department_options_come_from_the_catalog():
    spec = understand_questions(SessionState(session_id='s'), ['department'], STORE)['department']['criteria']
    assert set(spec) == {'Bags & Accessories', 'Jewelry & Watches', 'not_mentioned'}
    assert spec['Jewelry & Watches'] == 'Watches'   # no description: the department's categories
    assert understand_questions(SessionState(session_id='s'), ['department'], Catalog()) == {}   # no catalog: not asked
