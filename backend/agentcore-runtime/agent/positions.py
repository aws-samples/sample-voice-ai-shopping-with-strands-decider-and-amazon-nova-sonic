"""Shelf positions: row and column of each card in the browser's grid, for the card labels
of the pointing question ("Item 2, the second item: row 1, second from the left").

The browser reports its column count (2-6) with ui.layout; index 0 is the top-left card.
"""

from __future__ import annotations

MIN_COLUMNS, MAX_COLUMNS = 2, 6


def clamp_columns(columns: int) -> int:
    return max(MIN_COLUMNS, min(MAX_COLUMNS, int(columns)))


def position(index: int, columns: int) -> tuple[int, int]:
    """(row starting at 1, column starting at 0)."""
    row, col = divmod(index, clamp_columns(columns))
    return row + 1, col

