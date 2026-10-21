import pytest

from filters import Budget
from policy import (Answer, Thresholds, apply_commits, chips, no_match, plan_understand, rank_by_best_fit,
                    why_this_pick)
from state import Commit, SessionState

T = Thresholds()                 # the design's flat 0.8 / 0.5
CAL = Thresholds.from_env()      # with calibration.json


def choice(qid, top, p, others=None):
    probs = {top: p, **(others or {'other_option': round(1 - p, 4)})}
    return Answer(question_id=qid, choice=top, confidence=p, probabilities=probs)


def test_threshold_validation():
    with pytest.raises(ValueError):
        Thresholds(act=0.5, ask=0.8)
    with pytest.raises(ValueError):
        Thresholds(act=1.2, ask=0.5)


@pytest.mark.parametrize('p, band', [(None, 'ask'), (0.0, 'ask'), (0.49, 'ask'), (0.5, 'narrow'), (0.79, 'narrow'),
                                     (0.8, 'act'), (1.0, 'act')])
def test_bands_total_and_monotonic(p, band):
    """Every confidence maps to exactly one band, monotonic in the confidence."""
    assert T.band(p) == band


def test_gift_for_my_mom_commits_recipient_and_gender():
    state = SessionState(session_id='s')
    plan = plan_understand(state, {'intent': choice('intent', 'narrowing', 0.93),
                                   'recipient': choice('recipient', 'parent', 0.86),
                                   'gender': choice('gender', 'women', 0.91)}, None, T)
    assert plan.intent == 'narrowing' and set(plan.commits) == {'recipient', 'gender'} and plan.clarify is None
    apply_commits(state, plan, None)
    assert [c['value'] for c in chips(state)] == ['Parent', 'Women']
    assert chips(state)[0]['confidence'] == 0.86   # the decider's own value


def test_gift_for_my_parent_asks_mom_or_dad_once():
    state = SessionState(session_id='s')
    plan = plan_understand(state, {'intent': choice('intent', 'narrowing', 0.93), 'recipient': choice('recipient', 'parent', 0.9),
                                   'gender': choice('gender', 'not_mentioned', 0.8)}, None, T)
    assert plan.clarify == 'Is that your mom or your dad?' and 'gender' not in plan.commits
    later = SessionState(session_id='s', recipient=Commit('parent', 0.9))
    again = plan_understand(later, {'intent': choice('intent', 'narrowing', 0.93),
                                    'category': choice('category', 'not_mentioned', 0.9)}, None, T)
    assert again.clarify is None   # not asked again on every later sentence


def test_mother_implies_women():
    state = SessionState(session_id='s')
    plan = plan_understand(state, {'intent': choice('intent', 'narrowing', 0.9), 'recipient': choice('recipient', 'mother', 0.7)}, None, CAL)
    assert plan.commits['gender'].value == 'women' and plan.commits['gender'].source == 'recipient' and plan.clarify is None


def test_calibrated_thresholds_are_per_question():
    assert CAL.band(0.6, 'recipient') == 'act' and CAL.band(0.5, 'category') == 'narrow'
    assert CAL.band(0.25, 'pointed_item') == 'act' and CAL.band(0.05, 'pointed_item') == 'ask'


def test_one_clarifying_question_in_priority_order():
    """Several ask-band answers -> one question, recipient before category."""
    state = SessionState(session_id='s')
    plan = plan_understand(state, {'intent': choice('intent', 'narrowing', 0.9),
                                   'recipient': choice('recipient', 'self', 0.3, {'friend': 0.25, 'parent': 0.45}),
                                   'category': choice('category', 'Tops', 0.31, {'Dresses': 0.27, 'Outerwear': 0.42})}, None, T)
    assert plan.clarify.startswith('Is this for')   # the recipient question, not the category one


def test_narrow_band_is_context_not_commit():
    state = SessionState(session_id='s')
    plan = plan_understand(state, {'intent': choice('intent', 'narrowing', 0.9),
                                   'category': choice('category', 'Dresses', 0.62)}, None, T)
    assert plan.commits == {} and plan.context == {'category': ('Dresses', 0.62)}


@pytest.mark.parametrize('intent, offer', [('place_order', 'place_order'), ('go_back', 'go_back'), ('start_over', 'start_over')])
def test_irreversible_intents_below_act_become_an_offer(intent, offer):
    """These intents run only at act; below it the turn records the action and asks about it."""
    plan = plan_understand(SessionState(session_id='s'), {'intent': choice('intent', intent, 0.6)}, None, T)
    assert (plan.intent, plan.offer, plan.confirmation) == ('ask_offer', offer, True) and plan.commits == {}


@pytest.mark.parametrize('intent', ['place_order', 'go_back', 'start_over', 'confirm', 'close_item', 'other'])
def test_an_intent_below_the_ask_threshold_narrows(intent):
    """Labeled set, 2026-10-19: all 8 offers made below the ask threshold were wrong."""
    state = SessionState(session_id='s', open_product={'asin': 'B000000001', 'title': 'Bag', 'priceCents': 100})
    plan = plan_understand(state, {'intent': choice('intent', intent, 0.2),
                                   'recipient': choice('recipient', 'mother', 0.95)}, None, T)
    assert plan.intent == 'narrowing' and plan.offer is None and plan.commits['recipient'].value == 'mother'


def test_unsure_yes_confirms_the_open_product_or_the_pending_offer():
    open_state = SessionState(session_id='s', open_product={'asin': 'B000000001', 'title': 'Bag', 'priceCents': 100})
    assert plan_understand(open_state, {'intent': choice('intent', 'confirm', 0.6)}, None, T).offer == 'add_to_cart:B000000001'
    offered = SessionState(session_id='s', pending_offer='widen:gender')
    assert plan_understand(offered, {'intent': choice('intent', 'confirm', 0.6)}, None, T).offer == 'widen:gender'
    nothing = plan_understand(SessionState(session_id='s'), {'intent': choice('intent', 'confirm', 0.6)}, None, T)
    assert nothing.intent == 'narrowing' and nothing.offer is None   # nothing to say yes to


def test_narrowing_is_the_safe_default_at_any_confidence():
    """Design decision 2026-10-18: a narrow-band narrowing intent still commits slots on their own confidence."""
    plan = plan_understand(SessionState(session_id='s'), {'intent': choice('intent', 'narrowing', 0.45),
                                                          'recipient': choice('recipient', 'mother', 0.95)}, None, T)
    assert plan.intent == 'narrowing' and plan.commits['recipient'].value == 'mother' and plan.clarify is None


def test_narrow_band_pointing_still_points():
    plan = plan_understand(SessionState(session_id='s'), {'intent': choice('intent', 'pointing', 0.74)}, None, T)
    assert plan.intent == 'pointing'


def test_stated_budget_commits_at_100_percent():
    state = SessionState(session_id='s')
    parsed = Budget(max_cents=8000)
    plan = plan_understand(state, {'intent': choice('intent', 'narrowing', 0.9)}, parsed, T)
    apply_commits(state, plan, parsed)
    assert state.budget == parsed and chips(state) == [{'slot': 'budget', 'label': 'Budget', 'value': 'Under $80', 'confidence': 1.0}]


def test_missing_answers_produce_no_commits():
    plan = plan_understand(SessionState(session_id='s'), {}, None, T)
    assert plan.commits == {} and plan.intent == 'narrowing'


def test_no_match_from_none_of_these():
    """ADR-21 threshold (0.35), re-derived on the synthetic catalog's labeled set."""
    assert no_match(Answer('best_fit', 'none_of_these', 0.4, {'none_of_these': 0.387, 'A': 0.2}), T)
    assert not no_match(Answer('best_fit', 'A', 0.1, {'none_of_these': 0.316, 'A': 0.1}), T)


def test_rank_by_best_fit_keeps_unscored_in_retrieval_order():
    candidates = [{'asin': x} for x in 'ABCD']
    ranked = rank_by_best_fit(candidates, Answer('best_fit', 'C', 0.2, {'C': 0.4, 'A': 0.3, 'none_of_these': 0.05}))
    assert [p['asin'] for p in ranked] == ['C', 'A', 'B', 'D']


def test_why_this_pick():
    state = SessionState(session_id='s', recipient=Commit('parent', 0.86), gender=Commit('women', 0.91),
                         category=Commit('Handbags', 0.91), budget=Budget(max_cents=10000),
                         budget_commit=Commit('Under $100', 1.0, 'stated'))
    text = why_this_pick(state, {'priceCents': 7999}, rank=0, candidates=212)
    assert text == 'Parent (86%) · Women (91%) · Handbags (91%) · $79.99, under your $100 budget · top pick of 212 candidates'
    assert why_this_pick(state, {'priceCents': 7999}, rank=None, candidates=0, pointed_probability=0.93).endswith('(93%)')



def test_without_calibration_json_no_match_falls_back_to_the_measured_value(tmp_path):
    from policy import Thresholds
    assert Thresholds.from_env(str(tmp_path / 'missing.json')).no_match == 0.35
