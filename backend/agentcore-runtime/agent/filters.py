"""Budget parsing and the hard filters.

Owns the hard-filter invariant: no product that breaks a committed constraint is
ever shown or opened. The OpenSearch filter in search_catalog only narrows; this
module is applied last, before any ui.shelf or ui.product_open. The model never
does this arithmetic: amounts are parsed here, in code.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_UNITS = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
    'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16,
    'seventeen': 17, 'eighteen': 18, 'nineteen': 19,
}
_TENS = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90}
_NUMBER_WORD = r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|" \
               r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|" \
               r"thousand|and|a)"
# A spoken or written amount: "$79.99", "80", "eighty", "a hundred and fifty", "two hundred", and
# digits mixed with scale words, as speech-to-text often writes them: "200 thousand",
# "2 hundred and fifty", "2 thousand 500", "1.5k". A bare "k" counts only as money ("$5k",
# "5k dollars"): "a 4k monitor" or "12k gold" are not prices.
_SMALL_WORD = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|" \
              r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
_SCALE = rf"(?:[\s-]+(?:hundred|thousand)\b(?:[\s-]+(?:and[\s-]+)?(?:\d+|{_SMALL_WORD})\b)*)*(?:\s?k\b)?"
_AMOUNT = rf"(\$?\s?\d[\d,]*(?:\.\d{{1,2}})?{_SCALE}|(?:\b{_NUMBER_WORD}\b[\s-]?)+)"
_CURRENCY = r"(?:\s?(?:dollars?|bucks|usd))?"

_MAX_WORDS = r"under|below|less than|up to|no more than|not more than|at most|maximum of|max|within|cheaper than|tops"
_MIN_WORDS = r"over|above|more than|at least|minimum of|starting at"


def words_to_number(text: str) -> float | None:
    """'eighty' -> 80, 'a hundred and fifty' -> 150, 'two thousand' -> 2000, '2 thousand 500' -> 2500.
    None if not a number."""
    words = [w for w in re.split(r'[\s-]+', text.strip().lower()) if w and w != 'and']
    if not words or all(w == 'a' for w in words):
        return None
    total, current = 0, 0
    for word in words:
        if re.fullmatch(r'\d[\d,]*(?:\.\d+)?', word):
            current += float(word.replace(',', ''))
        elif word == 'a':
            current = max(current, 1)
        elif word in _UNITS:
            current += _UNITS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == 'hundred':
            current = max(current, 1) * 100
        elif word == 'thousand':
            total += max(current, 1) * 1000
            current = 0
        else:
            return None
    return total + current


# The search tool rejects prices above $100,000 (MAX_PRICE_CENTS in the Lambda validation); a
# larger spoken amount is no real limit, so it is capped there instead of failing every search.
MAX_PRICE_CENTS = 10_000_000


def _to_cents(amount: str, money: bool = True) -> int | None:
    amount = amount.strip()
    if re.match(r'^\$?\s?\d', amount):
        kilo = re.search(r'\s?k$', amount)
        if kilo and not money:
            return None   # "4k" or "12k" without a currency is not an amount
        number = amount[:kilo.start()] if kilo else amount
        value = words_to_number(number.replace('$', ' ').replace('-', ' '))
        if value is None:
            return None
        value = float(value) * (1000 if kilo else 1)
    else:
        number = words_to_number(amount)
        if number is None:
            return None
        value = float(number)
    if not (value > 0 and math.isfinite(value)):
        return None
    return min(round(value * 100), MAX_PRICE_CENTS)


@dataclass(frozen=True)
class Budget:
    min_cents: int | None = None
    max_cents: int | None = None

    def label(self) -> str:
        fmt = lambda c: f"${c / 100:,.0f}" if c % 100 == 0 else f"${c / 100:,.2f}"  # noqa: E731
        if self.min_cents is not None and self.max_cents is not None:
            return f"{fmt(self.min_cents)}–{fmt(self.max_cents)}"
        if self.max_cents is not None:
            return f"Under {fmt(self.max_cents)}"
        return f"Over {fmt(self.min_cents)}"  # type: ignore[arg-type]

    def allows(self, price_cents: int) -> bool:
        if self.max_cents is not None and price_cents > self.max_cents:
            return False
        if self.min_cents is not None and price_cents < self.min_cents:
            return False
        return True


def parse_budget(transcript: str) -> Budget | None:
    """Explicit amounts only: 'under 100 dollars', 'less than $50', 'between 20 and 40',
    '100 bucks', 'over two hundred'. Vague phrases ('nothing too pricey') return None;
    those go to the budget_band question instead."""
    text = transcript.lower().replace('’', "'")
    money = lambda m: bool(re.search(r"\$|dollars?|bucks|usd", m.group(0)))  # noqa: E731
    between = re.search(rf"\bbetween {_AMOUNT}{_CURRENCY} and {_AMOUNT}{_CURRENCY}", text)
    if between:
        low, high = _to_cents(between.group(1), money(between)), _to_cents(between.group(2), money(between))
        if low and high:
            return Budget(min_cents=min(low, high), max_cents=max(low, high))
    upper = re.search(rf"\b(?:{_MAX_WORDS})\s{_AMOUNT}{_CURRENCY}", text)
    if upper and (cents := _to_cents(upper.group(1), money(upper))):
        return Budget(max_cents=cents)
    lower = re.search(rf"\b(?:{_MIN_WORDS})\s{_AMOUNT}{_CURRENCY}", text)
    if lower and (cents := _to_cents(lower.group(1), money(lower))):
        return Budget(min_cents=cents)
    # A bare amount with a currency word or sign: "100 bucks", "$80", "eighty dollars" -> an upper bound.
    bare = re.search(rf"(\$\s?\d[\d,]*(?:\.\d{{1,2}})?)|\b({_AMOUNT})\s?(?:dollars?|bucks)\b", text)
    if bare:
        cents = _to_cents(bare.group(1) or bare.group(2))
        if cents:
            return Budget(max_cents=cents)
    return None


@dataclass(frozen=True)
class HardFilters:
    """Committed constraints only (confidence >= act, or a stated budget)."""
    budget: Budget | None = None
    department: str | None = None
    category: str | None = None
    gender: str | None = None
    available_only: bool = True

    def allows(self, product: dict) -> bool:
        if self.available_only and product.get('available') is False:
            return False
        if self.budget and not self.budget.allows(int(product['priceCents'])):
            return False
        if self.department and product.get('department') != self.department:
            return False
        if self.category and product.get('category') != self.category:
            return False
        # A committed gender v passes products whose gender is v or unisex.
        if self.gender and product.get('gender') not in (self.gender, 'unisex'):
            return False
        return True

    def search_arguments(self) -> dict:
        """The same constraints as search_catalog filter arguments (an optimization, not the guard)."""
        args: dict = {'availableOnly': self.available_only}
        if self.budget and self.budget.max_cents is not None:
            args['maxPriceCents'] = self.budget.max_cents
        if self.budget and self.budget.min_cents is not None:
            args['minPriceCents'] = self.budget.min_cents
        if self.department:
            args['departments'] = [self.department]
        if self.category:
            args['categories'] = [self.category]
        if self.gender:
            args['genders'] = [self.gender, 'unisex'] if self.gender != 'unisex' else ['unisex']
        return args


def apply(products: list[dict], filters: HardFilters) -> list[dict]:
    """Keeps input order (the ranking) and drops anything that breaks a hard filter."""
    return [p for p in products if filters.allows(p)]
