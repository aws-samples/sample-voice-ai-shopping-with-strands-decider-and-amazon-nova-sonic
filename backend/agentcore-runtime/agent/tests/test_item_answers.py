"""item_answers.answer: one sentence from the product record, nothing invented (ADR-37)."""
from decimal import Decimal

import pytest

from item_answers import UNKNOWN, answer

WALLET = {'title': 'Beige Zip-Around Wallet', 'brand': 'Cascadia Leatherworks', 'color': 'beige', 'material': 'leather',
          'priceCents': 6799, 'size': None, 'features': ['Zip-around closure', 'Twelve card slots', 'Coin pocket', 'Gift box'],
          'description': 'A roomy wallet for everyday use.', 'averageRating': Decimal('4.3'), 'ratingNumber': 1204,
          'available': True}


@pytest.mark.parametrize('detail, expected', [
    ('material', "It's made of leather."),
    ('color', "It's beige."),
    ('price', "It's $67.99."),
    ('brand', "It's by Cascadia Leatherworks."),
    ('features', "It has zip-around closure, twelve card slots and coin pocket."),   # first three only
    ('rating', "It's rated 4.3 out of 5 from 1,204 reviews."),
    ('availability', "Yes, it's in stock."),
    ('description', 'A roomy wallet for everyday use.'),
])
def test_each_detail_comes_from_its_field(detail, expected):
    assert answer(detail, WALLET) == expected


def test_a_missing_field_is_said_plainly_not_filled_in():
    assert answer('size', WALLET) == "The listing doesn't give a size for this one."
    assert answer('material', {**WALLET, 'material': ''}) == UNKNOWN
    assert answer('features', {**WALLET, 'features': []}) == UNKNOWN


def test_an_unknown_or_missing_detail_id_is_unknown():
    assert answer('other', WALLET) == UNKNOWN
    assert answer(None, WALLET) == UNKNOWN


def test_out_of_stock():
    assert answer('availability', {**WALLET, 'available': False}) == "It's out of stock right now."
