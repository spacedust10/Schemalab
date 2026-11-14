"""Canvas CSV cells never reach a spreadsheet as formulas."""

import pytest

from schemalab.canvas import defuse


@pytest.mark.parametrize(
    "cell",
    ["=1+2", " =1+2", "\t=1", "​=HYPERLINK(1)", "﻿+1", "-2+3", "@SUM(1)", "|cmd", "%x"],
)
def test_cells_a_spreadsheet_could_run_are_prefixed(cell: str) -> None:
    assert defuse(cell) == "'" + cell


@pytest.mark.parametrize(
    "cell", ["Ada", "ada@umich.edu", "Lovelace, Ada", "CIS 545 001", "U1001", ""]
)
def test_ordinary_cells_are_left_alone(cell: str) -> None:
    assert defuse(cell) == cell
