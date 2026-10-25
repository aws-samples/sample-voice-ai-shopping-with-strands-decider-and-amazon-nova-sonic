import pytest

from filters import Budget, HardFilters, apply, parse_budget, words_to_number


@pytest.mark.parametrize('text, budget', [
    ('a leather bag for my mom under 100 dollars', Budget(max_cents=10000)),
    ('something less than $50', Budget(max_cents=5000)),
    ('between 20 and 40', Budget(min_cents=2000, max_cents=4000)),
    ('between forty and twenty dollars', Budget(min_cents=2000, max_cents=4000)),
    ('maybe 100 bucks', Budget(max_cents=10000)),
    ("i'm looking for a leather bag for my mom, under eighty dollars.", Budget(max_cents=8000)),
    ('nothing over a hundred and fifty', Budget(min_cents=15000)),  # "over" is a lower bound by its words
    ('up to $79.99', Budget(max_cents=7999)),
    ('at least two hundred dollars', Budget(min_cents=20000)),
    ('no more than 1,200 dollars', Budget(max_cents=120000)),
    ('a watch for $80', Budget(max_cents=8000)),
])
def test_parse_budget(text, budget):
    assert parse_budget(text) == budget


@pytest.mark.parametrize('text', ['nothing too pricey', 'a gift for my mom', 'the second one', 'a pair of shoes', ''])
def test_no_explicit_amount(text):
    assert parse_budget(text) is None


@pytest.mark.parametrize('words, number', [('eighty', 80), ('a hundred', 100), ('a hundred and fifty', 150),
                                            ('two hundred fifty', 250), ('twenty-five', 25), ('one thousand', 1000),
                                            ('shoes', None), ('a', None)])
def test_words_to_number(words, number):
    assert words_to_number(words) == number


def test_budget_label():
    assert Budget(max_cents=8000).label() == 'Under $80'
    assert Budget(min_cents=2000, max_cents=4000).label() == '$20–$40'
    assert Budget(max_cents=7999).label() == 'Under $79.99'


PRODUCTS = [
    {'asin': 'A', 'priceCents': 5000, 'category': 'Handbags', 'gender': 'women', 'available': True},
    {'asin': 'B', 'priceCents': 9000, 'category': 'Handbags', 'gender': 'women', 'available': True},
    {'asin': 'C', 'priceCents': 4000, 'category': 'Handbags', 'gender': 'unisex', 'available': True},
    {'asin': 'D', 'priceCents': 3000, 'category': 'Handbags', 'gender': 'men', 'available': True},
    {'asin': 'E', 'priceCents': 3000, 'category': 'Watches', 'gender': 'women', 'available': True},
    {'asin': 'F', 'priceCents': 3000, 'category': 'Handbags', 'gender': 'women', 'available': False},
]


def test_hard_filters_soundness_and_order():
    """Everything kept satisfies every committed filter; order is preserved."""
    f = HardFilters(budget=Budget(max_cents=8000), category='Handbags', gender='women')
    kept = apply(PRODUCTS, f)
    assert [p['asin'] for p in kept] == ['A', 'C']
    assert all(f.allows(p) for p in kept)


def test_gender_filter_includes_unisex_only():
    assert [p['asin'] for p in apply(PRODUCTS, HardFilters(gender='men'))] == ['C', 'D']


def test_unavailable_dropped_by_default():
    assert 'F' not in [p['asin'] for p in apply(PRODUCTS, HardFilters())]


def test_search_arguments_mirror_filters():
    args = HardFilters(budget=Budget(min_cents=1000, max_cents=8000), category='Watches', gender='men').search_arguments()
    assert args == {'availableOnly': True, 'maxPriceCents': 8000, 'minPriceCents': 1000,
                    'categories': ['Watches'], 'genders': ['men', 'unisex']}



def test_the_department_filter_rejects_other_and_missing_departments():
    from filters import HardFilters
    hard = HardFilters(department='Bags & Accessories')
    assert hard.allows({'priceCents': 1000, 'department': 'Bags & Accessories'})
    assert not hard.allows({'priceCents': 1000, 'department': 'Shoes'})
    assert not hard.allows({'priceCents': 1000})   # a product without a department cannot pass a department chip


def test_the_shelf_keeps_the_department_field():
    from turn_pipeline import CATALOG_FIELDS
    assert 'department' in CATALOG_FIELDS


def test_digits_with_a_scale_word():
    from filters import parse_budget
    assert parse_budget('under $1.5k').max_cents == 150000
    assert parse_budget('under $2 hundred').max_cents == 20000
    assert parse_budget('under 5 thousand dollars').max_cents == 500000
    assert parse_budget('show me 5 kids toys') is None   # "k" only as its own word


def test_mixed_digit_and_word_amounts_and_k_only_as_money():
    from filters import parse_budget, MAX_PRICE_CENTS
    assert parse_budget('under 2 hundred and fifty').max_cents == 25000
    assert parse_budget('under 2 thousand 500').max_cents == 250000
    assert parse_budget('under 5 hundred thousand dollars').max_cents == MAX_PRICE_CENTS
    assert parse_budget('a monitor that goes up to 4k') is None
    assert parse_budget('under 12k gold') is None
    assert parse_budget('under $5k').max_cents == 500000
    assert parse_budget('between 20 and 40 dollars').min_cents == 2000
