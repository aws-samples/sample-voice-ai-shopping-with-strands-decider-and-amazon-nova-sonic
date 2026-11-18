"""Answers to a question about the open item, built from its product record (ADR-37).

The decider says which detail is asked about; this module turns that detail into a sentence
using only fields the record has, so what is spoken matches what the card shows. A detail
the record does not hold is said plainly, never filled in.
"""
from __future__ import annotations

from ui_events import format_cents

UNKNOWN = "I don't have that detail for this one."


def _join(items: list[str]) -> str:
    items = [i.rstrip('.').strip() for i in items if i and i.strip()]
    if len(items) <= 1:
        return items[0] if items else ''
    return ', '.join(items[:-1]) + ' and ' + items[-1]


def answer(detail: str | None, product: dict) -> str:
    """One short sentence about the product for the asked detail."""
    def has(field):
        value = product.get(field)
        return value not in (None, '', [])

    if detail == 'material':
        return f"It's made of {product['material']}." if has('material') else UNKNOWN
    if detail == 'color':
        return f"It's {product['color']}." if has('color') else UNKNOWN
    if detail == 'size':
        return f"It's size {product['size']}." if has('size') else "The listing doesn't give a size for this one."
    if detail == 'price':
        return f"It's {format_cents(product['priceCents'])}." if has('priceCents') else UNKNOWN
    if detail == 'brand':
        return f"It's by {product['brand']}." if has('brand') else UNKNOWN
    if detail == 'features':
        features = (product.get('features') or [])[:3]
        return f"It has {_join([f[0].lower() + f[1:] if f else f for f in features])}." if features else UNKNOWN
    if detail == 'rating':
        if not has('averageRating'):
            return UNKNOWN
        count = product.get('ratingNumber')
        return f"It's rated {float(product['averageRating']):.1f} out of 5" + (f" from {int(count):,} reviews." if count else '.')
    if detail == 'availability':
        if product.get('available') is None:
            return UNKNOWN
        return "Yes, it's in stock." if product['available'] else "It's out of stock right now."
    if detail == 'description':
        return product['description'] if has('description') else UNKNOWN
    return UNKNOWN
