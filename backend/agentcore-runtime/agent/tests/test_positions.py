import pytest

from positions import position


@pytest.mark.parametrize('columns', [2, 3, 4, 5, 6])
def test_rows_and_columns(columns):
    """Row numbers start at 1, columns at 0; a new row starts after the last column."""
    assert position(0, columns) == (1, 0)
    assert position(columns - 1, columns) == (1, columns - 1)
    assert position(columns, columns) == (2, 0)


def test_columns_are_clamped():
    assert position(2, 1) == (2, 0)   # treated as 2 columns
    assert position(6, 9) == (2, 0)   # treated as 6 columns
